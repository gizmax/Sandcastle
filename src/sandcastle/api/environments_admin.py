"""Admin CRUD + work.stats SSE for Anthropic Managed Agents environments.

This module exposes a FastAPI router mounted at `/admin/environments` that
proxies Anthropic's `/v1/environments` API for self-hosted sandbox lifecycle
management plus a Server-Sent Events stream over the `work/stats` endpoint
that operators can consume for autoscaling decisions (see Anthropic docs
section Monitoring).

All Anthropic-bound requests forward the `managed-agents-2026-04-01` beta
header. Admin-only (uses `_require_admin` from routes.py). Tenant scoping
is enforced via the existing `get_tenant_id` helper, and per-tenant cache
keys keep `work/stats` results isolated.

The `work/stats` endpoint is cached in-process for 5 seconds so that
multiple pollers in front of the same environment do not hammer Anthropic.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from sandcastle.api.auth import get_tenant_id

logger = logging.getLogger(__name__)


# Beta header re-exported here so tests can import a single source of truth
# without pulling agent_webhooks. Value MUST stay in sync with
# `sandcastle.api.agent_webhooks.ANTHROPIC_BETA_HEADER`.
ANTHROPIC_BETA_HEADER = "managed-agents-2026-04-01"
ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ENVIRONMENTS_ENDPOINT = "/v1/environments"

# Allowed `type` values for environment creation. Self-hosted is the only
# kind supported by PR #226 typed config.
ALLOWED_ENV_TYPES: frozenset[str] = frozenset({"self_hosted"})

# In-process cache for work/stats (5 second TTL).
_STATS_CACHE_TTL_SECONDS = 5.0
_stats_cache: dict[tuple[str | None, str], tuple[float, dict[str, Any]]] = {}

# SSE poll interval.
_STREAM_POLL_INTERVAL_SECONDS = 2.0


router = APIRouter(prefix="/admin/environments", tags=["environments-admin"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_admin_local(req: Request) -> None:
    """Wrap routes._require_admin via lazy import to avoid an import cycle.

    routes.py imports many heavy modules at import-time; importing it from
    inside our module-level scope would create a circular dependency when
    routes.py later imports back from us (transitively via main.py).
    """
    from sandcastle.api.routes import _require_admin

    _require_admin(req)


def _anthropic_headers() -> dict[str, str]:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    return {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": ANTHROPIC_BETA_HEADER,
        "content-type": "application/json",
    }


def _http_client() -> httpx.AsyncClient:
    """Return an httpx.AsyncClient bound to Anthropic's base URL.

    Patchable in tests via `monkeypatch.setattr(
        environments_admin, "_http_client", lambda: fake_client)`.
    """
    return httpx.AsyncClient(base_url=ANTHROPIC_BASE_URL, timeout=30.0)


def _raise_anthropic(exc: httpx.HTTPStatusError) -> None:
    """Translate an Anthropic error response into a 502 with a hint."""
    status_code = exc.response.status_code
    try:
        body = exc.response.json()
    except Exception:  # noqa: BLE001
        body = {"raw": exc.response.text}
    hint = ""
    if status_code == 401:
        hint = (
            " Check that ANTHROPIC_API_KEY is configured and has Managed "
            "Agents beta access."
        )
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={
            "code": "ANTHROPIC_UPSTREAM_ERROR",
            "message": f"Anthropic returned {status_code}.{hint}",
            "upstream_status": status_code,
            "upstream_body": body,
        },
    )


async def _emit_audit(
    event_type: str,
    actor_id: str,
    payload: dict[str, Any],
    source_ip: str | None,
) -> None:
    """Best-effort audit log append. Never raises into the request path."""
    try:
        from sandcastle.engine.audit import append_audit_event
        from sandcastle.models.db import async_session

        async with async_session() as session:
            await append_audit_event(
                session=session,
                event_type=event_type,
                run_id=None,
                actor_id=actor_id,
                payload=payload,
                source_ip=source_ip,
            )
    except Exception:
        logger.warning(
            "Failed to emit audit event %s for environments admin",
            event_type,
            exc_info=True,
        )


def _tag_with_tenant(body: dict[str, Any], tenant_id: str | None) -> dict[str, Any]:
    """Attach tenant scope to Anthropic env metadata so list calls can filter."""
    out = dict(body)
    meta = dict(out.get("metadata") or {})
    if tenant_id is not None:
        meta["sandcastle_tenant_id"] = tenant_id
    out["metadata"] = meta
    return out


def _filter_by_tenant(
    records: list[dict[str, Any]], tenant_id: str | None
) -> list[dict[str, Any]]:
    """Return only records whose metadata.sandcastle_tenant_id matches tenant_id.

    Admin (tenant_id is None) sees everything. Tenant-scoped callers only see
    their own envs, plus untagged envs are hidden from tenants for safety.
    """
    if tenant_id is None:
        return records
    out: list[dict[str, Any]] = []
    for r in records:
        meta = (r or {}).get("metadata") or {}
        if meta.get("sandcastle_tenant_id") == tenant_id:
            out.append(r)
    return out


def _unwrap_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return list(payload)
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            return list(payload["data"])
        if isinstance(payload.get("environments"), list):
            return list(payload["environments"])
    return []


# ---------------------------------------------------------------------------
# CRUD routes
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_environment(req: Request) -> dict[str, Any]:
    """Create a new managed-agents environment via Anthropic.

    Body: {"name": str, "type": "self_hosted"}.
    """
    _require_admin_local(req)
    try:
        body = await req.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid json: {exc}") from exc

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")

    name = (body.get("name") or "").strip() if isinstance(body.get("name"), str) else ""
    env_type = body.get("type")

    if not name:
        raise HTTPException(status_code=400, detail="`name` is required")
    if env_type not in ALLOWED_ENV_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"`type` must be one of {sorted(ALLOWED_ENV_TYPES)}; "
                f"got {env_type!r}"
            ),
        )

    tenant_id = get_tenant_id(req)
    out_body = _tag_with_tenant({"name": name, "type": env_type}, tenant_id)

    client = _http_client()
    try:
        resp = await client.post(
            ENVIRONMENTS_ENDPOINT, json=out_body, headers=_anthropic_headers()
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _raise_anthropic(exc)
        data = resp.json()
    finally:
        await client.aclose()

    await _emit_audit(
        event_type="environment.created",
        actor_id=tenant_id or "admin",
        payload={"environment_id": data.get("id"), "name": name, "type": env_type},
        source_ip=req.client.host if req.client else None,
    )
    return data


@router.get("")
async def list_environments(req: Request) -> dict[str, Any]:
    """List environments visible to the caller (tenant-filtered)."""
    _require_admin_local(req)
    tenant_id = get_tenant_id(req)

    client = _http_client()
    try:
        resp = await client.get(ENVIRONMENTS_ENDPOINT, headers=_anthropic_headers())
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _raise_anthropic(exc)
        raw = resp.json()
    finally:
        await client.aclose()

    records = _filter_by_tenant(_unwrap_list(raw), tenant_id)
    return {"data": records}


@router.get("/{env_id}")
async def get_environment(env_id: str, req: Request) -> dict[str, Any]:
    """Return a single environment record."""
    _require_admin_local(req)
    tenant_id = get_tenant_id(req)

    client = _http_client()
    try:
        resp = await client.get(
            f"{ENVIRONMENTS_ENDPOINT}/{env_id}", headers=_anthropic_headers()
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _raise_anthropic(exc)
        record = resp.json()
    finally:
        await client.aclose()

    if tenant_id is not None:
        meta = (record or {}).get("metadata") or {}
        if meta.get("sandcastle_tenant_id") != tenant_id:
            raise HTTPException(status_code=404, detail="environment not found")
    return record


@router.delete("/{env_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_environment(env_id: str, req: Request) -> None:
    """Delete an environment via Anthropic."""
    _require_admin_local(req)
    tenant_id = get_tenant_id(req)

    client = _http_client()
    try:
        resp = await client.delete(
            f"{ENVIRONMENTS_ENDPOINT}/{env_id}", headers=_anthropic_headers()
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _raise_anthropic(exc)
    finally:
        await client.aclose()

    # Invalidate stats cache entries for this env, regardless of tenant key.
    for key in list(_stats_cache.keys()):
        if key[1] == env_id:
            _stats_cache.pop(key, None)

    await _emit_audit(
        event_type="environment.deleted",
        actor_id=tenant_id or "admin",
        payload={"environment_id": env_id},
        source_ip=req.client.host if req.client else None,
    )
    return None


# ---------------------------------------------------------------------------
# work.stats: cached one-shot + SSE
# ---------------------------------------------------------------------------


async def _fetch_work_stats(env_id: str) -> dict[str, Any]:
    """Call Anthropic work/stats endpoint, raising HTTPException on error."""
    client = _http_client()
    try:
        resp = await client.get(
            f"{ENVIRONMENTS_ENDPOINT}/{env_id}/work/stats",
            headers=_anthropic_headers(),
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _raise_anthropic(exc)
        return resp.json()
    finally:
        await client.aclose()


async def _get_cached_stats(tenant_id: str | None, env_id: str) -> dict[str, Any]:
    """5-second in-process cache around `_fetch_work_stats`."""
    key = (tenant_id, env_id)
    now = time.monotonic()
    cached = _stats_cache.get(key)
    if cached is not None:
        ts, data = cached
        if now - ts < _STATS_CACHE_TTL_SECONDS:
            return data
    data = await _fetch_work_stats(env_id)
    _stats_cache[key] = (now, data)
    return data


def _shape_event(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalise the Anthropic payload into the SSE event schema."""
    return {
        "depth": raw.get("depth", raw.get("queue_depth", 0)),
        "pending": raw.get("pending", raw.get("pending_count", 0)),
        "oldest_queued_at": raw.get("oldest_queued_at"),
        "workers_polling": raw.get(
            "workers_polling", raw.get("active_workers", 0)
        ),
        "ts": time.time(),
    }


@router.get("/{env_id}/work/stats")
async def work_stats(env_id: str, req: Request) -> dict[str, Any]:
    """Return Anthropic work/stats, cached for 5 seconds per tenant+env."""
    _require_admin_local(req)
    tenant_id = get_tenant_id(req)
    return await _get_cached_stats(tenant_id, env_id)


@router.get("/{env_id}/work/stream")
async def work_stream(env_id: str, req: Request) -> StreamingResponse:
    """SSE feed that polls work/stats every 2 seconds and emits a normalised event."""
    _require_admin_local(req)
    tenant_id = get_tenant_id(req)

    async def event_generator():
        try:
            while True:
                if await req.is_disconnected():
                    return
                try:
                    raw = await _get_cached_stats(tenant_id, env_id)
                except HTTPException as exc:
                    payload = {
                        "error": True,
                        "status": exc.status_code,
                        "detail": exc.detail,
                        "ts": time.time(),
                    }
                    yield f"event: error\ndata: {json.dumps(payload)}\n\n"
                    return
                except Exception as exc:  # noqa: BLE001
                    payload = {
                        "error": True,
                        "detail": str(exc),
                        "ts": time.time(),
                    }
                    yield f"event: error\ndata: {json.dumps(payload)}\n\n"
                    return

                event = _shape_event(raw)
                yield f"event: work_stats\ndata: {json.dumps(event)}\n\n"
                await asyncio.sleep(_STREAM_POLL_INTERVAL_SECONDS)
        except asyncio.CancelledError:  # noqa: BLE001
            return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


__all__ = [
    "ALLOWED_ENV_TYPES",
    "ANTHROPIC_BETA_HEADER",
    "ENVIRONMENTS_ENDPOINT",
    "router",
]
