"""FastAPI application entrypoint for Sandcastle."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware

from sandcastle import __version__
from sandcastle.api.a2a import a2a_router
from sandcastle.api.agent_webhooks import router as agent_webhooks_router
from sandcastle.api.agui import agui_router
from sandcastle.api.auth import auth_middleware
from sandcastle.api.environments_admin import router as environments_admin_router
from sandcastle.api.routes import router
from sandcastle.api.security_headers import security_headers_middleware
from sandcastle.config import Settings, settings

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


async def _validate_providers() -> None:
    """Pre-flight check: verify configured LLM providers are reachable.

    Checks the advisor provider and each PROVIDER_REGISTRY entry that has a
    configured API key. Runs all health checks in parallel via asyncio.gather
    so startup is not blocked by slow providers. Logs warnings for unreachable
    providers but never blocks startup - this is informational only.
    """
    import asyncio
    import os
    import time

    import httpx

    from sandcastle.engine.generator import _PROVIDER_CONFIGS

    logger.info("Running provider pre-flight checks...")

    async def _check_provider(
        provider_name: str, cfg: dict
    ) -> tuple[str, bool]:
        """Check a single provider. Returns (provider_name, success)."""
        key_env = cfg.get("api_key_env", "")
        if not key_env:
            # Local keyless providers (ollama / omlx). Derive the health URL
            # from settings so omlx is probed on its own port instead of the
            # hardcoded Ollama port.
            if provider_name == "omlx":
                base = settings.omlx_base_url.rstrip("/")
                health_url = f"{base}/v1/models"  # OpenAI-compatible endpoint
                unreachable_msg = f"omlx server not running at {base}"
            else:
                base = settings.ollama_host.rstrip("/")
                health_url = f"{base}/api/tags"
                unreachable_msg = f"Ollama not running at {base}"
            start = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    resp = await client.get(health_url)
                    resp.raise_for_status()
                latency_ms = round((time.monotonic() - start) * 1000)
                logger.info("Provider %s: ok (%dms, region=local)", provider_name, latency_ms)
                return provider_name, True
            except Exception:
                logger.warning("Provider %s: not reachable (%s)", provider_name, unreachable_msg)
                return provider_name, False

        api_key = os.environ.get(key_env, "")
        if not api_key:
            # Try settings
            attr_map = {
                "ANTHROPIC_API_KEY": "anthropic_api_key",
                "OPENAI_API_KEY": "openai_api_key",
                "MISTRAL_API_KEY": "mistral_api_key",
                "MINIMAX_API_KEY": "minimax_api_key",
                "OPENROUTER_API_KEY": "openrouter_api_key",
            }
            attr = attr_map.get(key_env)
            if attr:
                api_key = getattr(settings, attr, "") or ""

        if not api_key:
            logger.debug("Provider %s: skipped (no API key configured)", provider_name)
            return provider_name, True  # Not a failure, just skipped

        start = time.monotonic()
        try:
            headers_fn = cfg.get("headers_fn")
            headers = headers_fn(api_key) if headers_fn else {}

            if key_env == "ANTHROPIC_API_KEY":
                body = {
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "ping"}],
                }
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.post(cfg["api_url"], json=body, headers=headers)
                    if resp.status_code not in (200, 400, 404):
                        resp.raise_for_status()
            else:
                body = {
                    "model": cfg.get("model", "gpt-4o-mini"),
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "ping"}],
                }
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.post(cfg["api_url"], json=body, headers=headers)
                    if resp.status_code not in (200, 400, 404):
                        resp.raise_for_status()

            latency_ms = round((time.monotonic() - start) * 1000)
            region = cfg.get("region", "us")
            logger.info(
                "Provider %s: ok (%dms, region=%s)", provider_name, latency_ms, region
            )
            return provider_name, True
        except Exception as exc:
            latency_ms = round((time.monotonic() - start) * 1000)
            logger.warning(
                "Provider %s: unreachable after %dms - %s",
                provider_name,
                latency_ms,
                str(exc)[:200],
            )
            return provider_name, False

    # Build list of check tasks, skipping providers without API keys
    # (the inner function handles the skip logic and logging)
    tasks = [
        _check_provider(name, cfg)
        for name, cfg in _PROVIDER_CONFIGS.items()
    ]

    if not tasks:
        logger.warning(
            "No LLM providers configured. Set ANTHROPIC_API_KEY or other provider keys."
        )
        return

    # Run all provider checks in parallel
    results = await asyncio.gather(*tasks, return_exceptions=True)

    checked = 0
    warnings = 0
    for result in results:
        if isinstance(result, Exception):
            # Should not happen since _check_provider catches all exceptions,
            # but handle defensively
            logger.warning("Provider check raised unexpected error: %s", result)
            warnings += 1
            checked += 1
        else:
            _name, success = result
            checked += 1
            if not success:
                warnings += 1

    if checked == 0:
        logger.warning(
            "No LLM providers configured. Set ANTHROPIC_API_KEY or other provider keys."
        )
    elif warnings:
        logger.warning(
            "Provider pre-flight: %d/%d provider(s) unreachable. "
            "Failover will activate automatically when needed.",
            warnings,
            checked,
        )
    else:
        logger.info("Provider pre-flight: all %d configured provider(s) reachable", checked)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle - startup and shutdown hooks."""
    # Initialize telemetry (opt-in, disabled by default)
    from sandcastle.engine.telemetry import init_sentry

    init_sentry()

    # Validate license key
    from sandcastle.engine.license import LicenseStatus, get_license

    lic = get_license()
    if lic.status == LicenseStatus.valid:
        logger.info(
            "License: %s tier, licensed to %s (expires %s)",
            lic.tier.value,
            lic.licensee,
            lic.expires or "never",
        )
    elif lic.status == LicenseStatus.missing:
        logger.warning(
            "Running in community mode - not licensed for production use. "
            "Set LICENSE_KEY in .env for production deployments."
        )
    elif lic.status == LicenseStatus.expired:
        logger.warning("License expired: %s", lic.detail)
    else:
        logger.warning("License invalid: %s", lic.detail)

    if settings.is_local_mode:
        logger.info(
            "Sandcastle starting in local mode (SQLite + filesystem + in-process queue)"
        )
        # Auto-create tables for SQLite (no Alembic needed)
        from sandcastle.models.db import init_db

        await init_db()
        logger.info("Local database initialized")
    else:
        logger.info(
            "Sandcastle starting in production mode (PostgreSQL + Redis + S3)"
        )

    # Load saved settings from DB
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from sandcastle.models.db import ApiKey, Setting, async_session

    async with async_session() as session:
        result = await session.execute(sa_select(Setting))
        saved = {s.key: s.value for s in result.scalars().all()}

        # Only restore settings that are safe to change at runtime.
        # Security-critical settings (auth, encryption, DB, Redis) must come
        # from environment variables and cannot be overridden from the DB.
        _RESTORABLE_SETTINGS = {
            "anthropic_api_key", "e2b_api_key", "openai_api_key",
            "mistral_api_key", "minimax_api_key", "openrouter_api_key",
            "default_max_cost_usd", "log_level", "max_workflow_depth",
        }
        # Keys in restorable settings that may be stored encrypted
        _ENCRYPTED_RESTORABLE = {
            "anthropic_api_key", "e2b_api_key", "openai_api_key",
            "mistral_api_key", "minimax_api_key", "openrouter_api_key",
        }
        for key, value in saved.items():
            if key not in _RESTORABLE_SETTINGS:
                if hasattr(settings, key):
                    logger.debug(
                        "Skipping non-restorable saved setting '%s'", key
                    )
                continue
            if hasattr(settings, key):
                # Decrypt encrypted credential values from DB
                if key in _ENCRYPTED_RESTORABLE and isinstance(value, str) and value.startswith("gAAAAA"):
                    try:
                        from sandcastle.engine.crypto import decrypt_credentials
                        decrypted = decrypt_credentials(value)
                        if isinstance(decrypted, dict) and "v" in decrypted:
                            value = decrypted["v"]
                    except Exception:
                        logger.warning("Could not decrypt saved setting '%s', skipping", key)
                        continue
                field_type = type(getattr(settings, key))
                try:
                    if field_type is bool:
                        coerced = value.lower() in ("true", "1", "yes")
                    elif field_type is int:
                        coerced = int(value)
                    elif field_type is float:
                        coerced = float(value)
                    else:
                        coerced = value
                    # Validate through Pydantic to enforce field validators
                    validated = Settings.model_validate(
                        {**settings.model_dump(), key: coerced}
                    )
                    setattr(settings, key, getattr(validated, key))
                except Exception as e:
                    # Never log the value - it may be an API key or secret
                    logger.warning(
                        f"Ignoring invalid saved setting {key}=<redacted>: {e}"
                    )

        if saved:
            logger.info(f"Loaded {len(saved)} saved settings from database")

        # Restore tool credentials (TOOL_* keys) into os.environ so connectors work
        tool_cred_count = 0
        for key, value in saved.items():
            if key.startswith("TOOL_") and value:
                # Decrypt if encrypted
                if isinstance(value, str) and value.startswith("gAAAAA"):
                    try:
                        from sandcastle.engine.crypto import decrypt_credentials
                        decrypted = decrypt_credentials(value)
                        if isinstance(decrypted, dict) and "v" in decrypted:
                            value = decrypted["v"]
                    except Exception:
                        logger.warning("Could not decrypt tool credential '%s', skipping", key)
                        continue
                os.environ[key] = value
                tool_cred_count += 1
        if tool_cred_count:
            logger.info(f"Restored {tool_cred_count} tool credential(s) from database")

    # Clean up stale runs (queued/running) left from previous crash/restart
    from datetime import datetime, timezone

    from sqlalchemy import update as sa_update

    from sandcastle.models.db import Run, RunStatus

    async with async_session() as session:
        # Count first, then update
        count_result = await session.execute(
            sa_select(func.count()).select_from(Run).where(
                Run.status.in_([RunStatus.QUEUED, RunStatus.RUNNING])
            )
        )
        orphan_count = count_result.scalar() or 0

        if orphan_count:
            await session.execute(
                sa_update(Run)
                .where(Run.status.in_([RunStatus.QUEUED, RunStatus.RUNNING]))
                .values(
                    status=RunStatus.FAILED,
                    error="Server restarted - run was orphaned",
                    completed_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            logger.info(f"Cleaned up {orphan_count} orphaned runs from previous session")

    # Start the cron scheduler (skip in multi-worker deployments)
    if settings.scheduler_enabled:
        from sandcastle.queue.scheduler import restore_schedules, start_scheduler

        await start_scheduler()
        await restore_schedules()
    else:
        logger.info("Scheduler disabled (SCHEDULER_ENABLED=false)")

    # Warn when authentication is disabled
    if not settings.auth_required:
        logger.warning(
            "Authentication is DISABLED. All API endpoints are publicly accessible. "
            "Set AUTH_REQUIRED=true for production deployments."
        )

    # Warn about default API key pepper when auth is enabled
    if settings.auth_required:
        import os as _os
        if not _os.getenv("API_KEY_PEPPER"):
            logger.warning(
                "AUTH_REQUIRED=true but API_KEY_PEPPER is not set. "
                "Using default pepper - set API_KEY_PEPPER env var for production."
            )

    # Warn about placeholder credentials
    _placeholders = {"minioadmin", "your-webhook-signing-secret", "sandcastle"}
    _cred_warnings = []
    if settings.webhook_secret in _placeholders:
        _cred_warnings.append("WEBHOOK_SECRET")
    if settings.aws_access_key_id in _placeholders:
        _cred_warnings.append("AWS_ACCESS_KEY_ID")
    if settings.aws_secret_access_key in _placeholders:
        _cred_warnings.append("AWS_SECRET_ACCESS_KEY")
    if _cred_warnings:
        logger.warning(
            "Placeholder credentials detected for: %s. "
            "Set secure values via environment variables for production.",
            ", ".join(_cred_warnings),
        )

    # Bootstrap admin API key from env var (if configured and not yet in DB)
    if settings.admin_api_key:
        from sandcastle.api.auth import hash_key

        admin_hash = hash_key(settings.admin_api_key)
        async with async_session() as session:
            existing = await session.execute(
                sa_select(ApiKey).where(ApiKey.key_hash == admin_hash)
            )
            if not existing.scalar_one_or_none():
                admin_key = ApiKey(
                    key_hash=admin_hash,
                    key_prefix=settings.admin_api_key[:8],
                    tenant_id=None,
                    name="admin (bootstrap)",
                    is_active=True,
                )
                session.add(admin_key)
                await session.commit()
                logger.info("Admin API key bootstrapped from ADMIN_API_KEY env var")

    # Pre-flight provider validation (best-effort, never blocks startup)
    await _validate_providers()

    yield

    # Shutdown
    from sandcastle.models.db import _engine_url, _is_in_memory_sqlite, engine

    if settings.scheduler_enabled:
        from sandcastle.queue.scheduler import stop_scheduler

        await stop_scheduler()
    # Disposing an in-memory SQLite engine (StaticPool holds a single shared
    # connection) destroys the entire database. That is harmless in production
    # (file SQLite / PostgreSQL), but in the test suite - where many tests
    # drive the app through its lifespan - it wipes the schema for every
    # subsequent test, surfacing as cascading "no such table" failures. The
    # in-memory connection is owned by the process and reclaimed on exit, so
    # skipping dispose for it is safe.
    if not _is_in_memory_sqlite(_engine_url):
        await engine.dispose()
    logger.info("Sandcastle shut down")


app = FastAPI(
    title="Sandcastle",
    description="Production-ready workflow orchestrator for AI agents",
    version=__version__,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# GZip compression (added first = innermost, compresses all responses >= 1KB)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Auth (added second = wraps gzip)
app.add_middleware(BaseHTTPMiddleware, dispatch=auth_middleware)

# Security headers (added third = wraps auth + gzip)
app.add_middleware(BaseHTTPMiddleware, dispatch=security_headers_middleware)

# CORS (added fourth = outermost middleware, wraps everything)
# Only allow the configured dashboard origin plus the two default Vite dev
# server ports (5173 primary, 5174 fallback). Previously ports 5173-5180 were
# allowed, which needlessly widened the CORS attack surface.
_cors_origins = [
    settings.dashboard_origin,
    "http://localhost:5173",
    "http://localhost:5174",
]
# Wildcard + credentials is invalid per CORS spec - filter it out.
# Also deduplicate (dashboard_origin may overlap with hardcoded Vite ports).
_cors_origins = list(dict.fromkeys(o for o in _cors_origins if o != "*"))
if settings.dashboard_origin == "*":
    logger.warning(
        "DASHBOARD_ORIGIN='*' is invalid with allow_credentials=True. "
        "Set it to your actual dashboard URL."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")

# A2A protocol routes (root level - /.well-known/agent.json and /a2a)
app.include_router(a2a_router)

# AG-UI protocol routes (/api/agui/stream/{run_id})
app.include_router(agui_router, prefix="/api/agui")

# Anthropic Managed Agents webhook receiver (root level - /agent-webhooks/anthropic)
app.include_router(agent_webhooks_router)
app.include_router(environments_admin_router)

# ---------------------------------------------------------------------------
# Dashboard static files (served from the same port)
# ---------------------------------------------------------------------------

# Look for pre-built dashboard in known locations
_DASHBOARD_CANDIDATES = [
    Path(__file__).parent.parent.parent / "dashboard" / "dist",  # repo dev
    Path(__file__).parent / "dashboard",                          # installed pkg
]
_dashboard_dir: Path | None = next(
    (p for p in _DASHBOARD_CANDIDATES if (p / "index.html").exists()), None
)

if _dashboard_dir:
    logger.info(f"Serving dashboard from {_dashboard_dir}")
    app.mount("/assets", StaticFiles(directory=_dashboard_dir / "assets"), name="dashboard-assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa_fallback(path: str):
        """Serve dashboard SPA - static files or fallback to index.html."""
        # Don't intercept /api paths - let FastAPI return 404 for unknown API routes
        if path.startswith("api/") or path == "api":
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Not found")
        # Don't intercept A2A protocol paths
        if path.startswith(".well-known/") or path == "a2a":
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Not found")
        file = (_dashboard_dir / path).resolve()
        if file.is_relative_to(_dashboard_dir) and file.exists() and file.is_file():
            return FileResponse(file)
        return FileResponse(_dashboard_dir / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "sandcastle.main:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
    )
