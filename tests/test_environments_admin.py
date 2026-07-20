"""Tests for the /admin/environments CRUD + work.stats SSE router."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from sandcastle.api import environments_admin
from sandcastle.api.environments_admin import (
    ANTHROPIC_BETA_HEADER,
    ENVIRONMENTS_ENDPOINT,
    router,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Resp:
    """Minimal httpx.Response-like object for testing."""

    def __init__(
        self,
        status_code: int,
        json_body: Any | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else {}
        self.text = text or json.dumps(self._json_body)

    def json(self) -> Any:
        return self._json_body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://test")
            response = httpx.Response(
                status_code=self.status_code,
                text=self.text,
                request=request,
            )
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=request, response=response
            )


class FakeAsyncClient:
    """Captures Anthropic calls + headers; replies with scripted responses."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses: dict[tuple[str, str], _Resp] = {}
        self.closed = False

    def respond(self, method: str, path: str, response: _Resp) -> None:
        self.responses[(method.upper(), path)] = response

    async def _do(
        self, method: str, path: str, headers: dict[str, str], json_body: Any
    ) -> _Resp:
        self.calls.append(
            {
                "method": method.upper(),
                "path": path,
                "headers": dict(headers),
                "json": json_body,
            }
        )
        resp = self.responses.get((method.upper(), path))
        if resp is None:
            return _Resp(200, {})
        return resp

    async def get(self, path: str, headers: dict[str, str]) -> _Resp:
        return await self._do("GET", path, headers, None)

    async def post(
        self, path: str, json: Any, headers: dict[str, str]
    ) -> _Resp:
        return await self._do("POST", path, headers, json)

    async def delete(self, path: str, headers: dict[str, str]) -> _Resp:
        return await self._do("DELETE", path, headers, None)

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def fake_client(monkeypatch) -> FakeAsyncClient:
    fc = FakeAsyncClient()
    monkeypatch.setattr(environments_admin, "_http_client", lambda: fc)
    # Stable API key for header assertions.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    # Wipe the in-process stats cache.
    environments_admin._stats_cache.clear()
    return fc


@pytest.fixture
def app_factory():
    """Build a FastAPI app whose tenant_id can be controlled per request."""

    def _build(tenant_id: str | None = None) -> FastAPI:
        app = FastAPI()

        @app.middleware("http")
        async def _set_state(request: Request, call_next):
            request.state.tenant_id = tenant_id
            request.state._auth_checked = True
            return await call_next(request)

        app.include_router(router)
        return app

    return _build


@pytest.fixture
def admin_client(app_factory) -> TestClient:
    return TestClient(app_factory(tenant_id=None))


def _no_audit(monkeypatch) -> None:
    """Stub out audit emission so tests do not need the DB schema."""

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(environments_admin, "_emit_audit", _noop)


# ---------------------------------------------------------------------------
# CRUD + header assertions
# ---------------------------------------------------------------------------


def test_post_creates_environment_and_forwards_beta_header(
    fake_client, admin_client, monkeypatch
):
    _no_audit(monkeypatch)
    fake_client.respond(
        "POST",
        ENVIRONMENTS_ENDPOINT,
        _Resp(201, {"id": "env_123", "name": "prod", "type": "self_hosted"}),
    )

    resp = admin_client.post(
        "/admin/environments",
        json={"name": "prod", "type": "self_hosted"},
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["id"] == "env_123"

    sent = fake_client.calls[0]
    assert sent["method"] == "POST"
    assert sent["path"] == ENVIRONMENTS_ENDPOINT
    assert sent["headers"]["anthropic-beta"] == ANTHROPIC_BETA_HEADER
    assert sent["headers"]["x-api-key"] == "sk-test-key"
    # Admin caller has no tenant; metadata.sandcastle_tenant_id should not
    # leak a value (key absent or None).
    meta = sent["json"].get("metadata", {})
    assert meta.get("sandcastle_tenant_id") is None


def test_beta_header_value_is_managed_agents_2026_04_01(
    fake_client, admin_client, monkeypatch
):
    _no_audit(monkeypatch)
    fake_client.respond("GET", ENVIRONMENTS_ENDPOINT, _Resp(200, {"data": []}))
    admin_client.get("/admin/environments")
    assert fake_client.calls[0]["headers"]["anthropic-beta"] == (
        "managed-agents-2026-04-01"
    )


def test_get_returns_list(fake_client, admin_client):
    fake_client.respond(
        "GET",
        ENVIRONMENTS_ENDPOINT,
        _Resp(200, {"data": [{"id": "env_1"}, {"id": "env_2"}]}),
    )
    resp = admin_client.get("/admin/environments")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()["data"]] == ["env_1", "env_2"]


def test_delete_returns_204(fake_client, admin_client, monkeypatch):
    _no_audit(monkeypatch)
    fake_client.respond("DELETE", f"{ENVIRONMENTS_ENDPOINT}/env_42", _Resp(204))
    resp = admin_client.delete("/admin/environments/env_42")
    assert resp.status_code == 204
    assert fake_client.calls[0]["method"] == "DELETE"


def test_empty_name_rejected(fake_client, admin_client, monkeypatch):
    _no_audit(monkeypatch)
    resp = admin_client.post(
        "/admin/environments", json={"name": "", "type": "self_hosted"}
    )
    assert resp.status_code == 400
    # No upstream call was made.
    assert fake_client.calls == []


def test_invalid_env_type_rejected(fake_client, admin_client, monkeypatch):
    _no_audit(monkeypatch)
    resp = admin_client.post(
        "/admin/environments", json={"name": "x", "type": "e2b_cloud"}
    )
    assert resp.status_code == 400
    assert fake_client.calls == []


# ---------------------------------------------------------------------------
# Auth + tenant isolation
# ---------------------------------------------------------------------------


def test_non_admin_caller_gets_403(fake_client, app_factory, monkeypatch):
    _no_audit(monkeypatch)
    # auth_required must be True for is_admin() to evaluate tenant scope.
    from sandcastle.config import settings

    monkeypatch.setattr(settings, "auth_required", True)
    tenant_app = app_factory(tenant_id="tenant_a")
    client = TestClient(tenant_app)

    resp = client.post(
        "/admin/environments", json={"name": "x", "type": "self_hosted"}
    )
    assert resp.status_code == 403


def test_real_auth_middleware_protects_admin_environments(fake_client, monkeypatch):
    """The root-mounted router must not bypass production API-key auth."""
    from sandcastle.api.auth import auth_middleware, hash_key
    from sandcastle.config import settings
    from sandcastle.models.db import ApiKey, async_session

    monkeypatch.setattr(settings, "auth_required", True)

    app = FastAPI()
    app.add_middleware(BaseHTTPMiddleware, dispatch=auth_middleware)
    app.include_router(router)
    client = TestClient(app)

    tenant_key = f"sc_env_tenant_{time.time_ns()}"

    async def _insert_tenant_key() -> None:
        async with async_session() as session:
            session.add(
                ApiKey(
                    key_hash=hash_key(tenant_key),
                    key_prefix=tenant_key[:8],
                    tenant_id="tenant_a",
                    name="environment tenant key",
                )
            )
            await session.commit()

    asyncio.run(_insert_tenant_key())

    assert client.get("/admin/environments").status_code == 401
    assert client.get(
        "/admin/environments", headers={"X-API-Key": tenant_key}
    ).status_code == 403
    assert fake_client.calls == []


def test_tenant_isolation_on_list(fake_client, app_factory, monkeypatch):
    """Tenant A's GET must not see tenant B's environments."""
    from sandcastle.config import settings

    # Switch off auth_required so a tenant-scoped caller is still allowed by
    # _require_admin (in local mode anyone is admin).
    monkeypatch.setattr(settings, "auth_required", False)
    fake_client.respond(
        "GET",
        ENVIRONMENTS_ENDPOINT,
        _Resp(
            200,
            {
                "data": [
                    {
                        "id": "a1",
                        "metadata": {"sandcastle_tenant_id": "tenant_a"},
                    },
                    {
                        "id": "b1",
                        "metadata": {"sandcastle_tenant_id": "tenant_b"},
                    },
                    {"id": "untagged"},
                ]
            },
        ),
    )
    client_a = TestClient(app_factory(tenant_id="tenant_a"))
    resp = client_a.get("/admin/environments")
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()["data"]]
    assert ids == ["a1"]


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def test_anthropic_401_surfaces_as_502_with_env_hint(
    fake_client, admin_client, monkeypatch
):
    _no_audit(monkeypatch)
    fake_client.respond(
        "GET", ENVIRONMENTS_ENDPOINT, _Resp(401, {"error": "unauthorized"})
    )
    resp = admin_client.get("/admin/environments")
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail["upstream_status"] == 401
    assert "ANTHROPIC_API_KEY" in detail["message"]


# ---------------------------------------------------------------------------
# work/stats: caching + SSE
# ---------------------------------------------------------------------------


def test_work_stats_cached_for_5s(fake_client, admin_client):
    fake_client.respond(
        "GET",
        f"{ENVIRONMENTS_ENDPOINT}/env_1/work/stats",
        _Resp(200, {"depth": 7, "pending": 3}),
    )

    r1 = admin_client.get("/admin/environments/env_1/work/stats")
    r2 = admin_client.get("/admin/environments/env_1/work/stats")

    assert r1.status_code == 200
    assert r2.status_code == 200
    # Both responses come from a single upstream call thanks to the cache.
    work_stat_calls = [
        c
        for c in fake_client.calls
        if c["path"].endswith("/work/stats")
    ]
    assert len(work_stat_calls) == 1


def test_work_stream_emits_normalised_sse_event(fake_client, monkeypatch):
    """Drive the SSE generator directly so the test does not hang on the
    open TestClient stream (the route loops until the client disconnects)."""
    fake_client.respond(
        "GET",
        f"{ENVIRONMENTS_ENDPOINT}/env_1/work/stats",
        _Resp(
            200,
            {
                "depth": 12,
                "pending": 5,
                "oldest_queued_at": "2026-05-19T10:00:00Z",
                "workers_polling": 2,
            },
        ),
    )

    # Build a fake request whose is_disconnected() flips True after the
    # first event so the generator returns cleanly.
    counter = {"calls": 0}

    class _State:
        tenant_id = None
        _auth_checked = True

    class _Req:
        client = None
        state = _State()

        async def is_disconnected(self):
            counter["calls"] += 1
            return counter["calls"] > 1

    # Make the poll interval near-zero to keep the test fast.
    monkeypatch.setattr(
        environments_admin, "_STREAM_POLL_INTERVAL_SECONDS", 0.0
    )

    async def _drive():
        resp = await environments_admin.work_stream("env_1", _Req())  # type: ignore[arg-type]
        assert resp.media_type == "text/event-stream"
        assert resp.headers["cache-control"] == "no-cache"
        chunks: list[str] = []
        async for piece in resp.body_iterator:
            chunks.append(
                piece if isinstance(piece, str) else piece.decode("utf-8")
            )
        return "".join(chunks)

    body = asyncio.run(_drive())
    first_event = body.split("\n\n", 1)[0]
    lines = first_event.splitlines()
    assert "event: work_stats" in lines
    data_line = next(line for line in lines if line.startswith("data: "))
    payload = json.loads(data_line[len("data: ") :])
    assert payload["depth"] == 12
    assert payload["pending"] == 5
    assert payload["oldest_queued_at"] == "2026-05-19T10:00:00Z"
    assert payload["workers_polling"] == 2
    assert "ts" in payload
