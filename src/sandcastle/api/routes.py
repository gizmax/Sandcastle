"""API endpoints for Sandcastle."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import case, func, select
from sqlalchemy.orm import selectinload

from sandcastle.api.auth import generate_api_key, get_tenant_id, hash_key, is_admin
from sandcastle.api.rate_limit import execution_limiter
from sandcastle.api.schemas import (
    AdvisorConfigureRequest,
    AdvisorConfigureResponse,
    AdvisorCostEstimateResponse,
    AdvisorStatusResponse,
    AdvisorTestConnectionResponse,
    AnnexIVResponse,
    AnnexIVSections,
    ApiKeyAllowlistRequest,
    ApiKeyCreatedResponse,
    ApiKeyCreateRequest,
    ApiKeyResponse,
    ApiKeyRotateRequest,
    ApiKeyRotateResponse,
    ApiResponse,
    ApprovalRespondRequest,
    ApprovalResponse,
    AuditEventResponse,
    AuditVerifyResponse,
    AutoPilotStatsResponse,
    BatchRunRequest,
    BatchStartedResponse,
    ComplianceStatusResponse,
    CostEstimateEntry,
    DeadLetterItemResponse,
    DeadLetterResolveRequest,
    EmergencyStopResponse,
    ErrorResponse,
    EvalAssertionResponse,
    EvalCaseResponse,
    EvalRunResponse,
    EvalStatsResponse,
    EvalSuiteRunRequest,
    EvolutionIterationResponse,
    EvolutionStartRequest,
    EvolutionStartResponse,
    EvolutionStatsResponse,
    EvolutionStatusResponse,
    ExperimentResponse,
    ExplainErrorRequest,
    ForkRequest,
    GenerateChatRequest,
    GenerateWorkflowResponse,
    HealthResponse,
    HubRateRequest,
    HubSubmissionResponse,
    HubSubmitRequest,
    LicenseInfoResponse,
    MemoryAddRequest,
    MemoryEntry,
    MemoryListResponse,
    MemorySearchRequest,
    OptimizerStatsResponse,
    PaginationMeta,
    PolicyViolationResponse,
    PolicyViolationStatsResponse,
    PrivacyNoticeResponse,
    ProviderStatusEntry,
    ReplayRequest,
    RoutingDecisionResponse,
    RunCompareResponse,
    RunEstimateRequest,
    RunForkResponse,
    RunIdempotentResponse,
    RunListItem,
    RunQueuedResponse,
    RunReplayResponse,
    RunStatusResponse,
    RuntimeInfoResponse,
    ScheduleCreateRequest,
    ScheduleResponse,
    ScheduleUpdateRequest,
    SettingsResponse,
    SettingsUpdateRequest,
    StatsResponse,
    StepDiff,
    StepStatusResponse,
    ToolConnectionCreateRequest,
    ToolConnectionResponse,
    ToolConnectionUpdateRequest,
    ToolCredentialUpdateRequest,
    ToolFunctionResponse,
    ToolListResponse,
    ToolResponse,
    TransparencyAiModelEntry,
    TransparencyHumanOversightEntry,
    TransparencyPolicyViolationEntry,
    TransparencyReportResponse,
    UpdateCheckResponse,
    UpdateRequest,
    UpdateResponse,
    WorkflowApiSpecResponse,
    WorkflowApiUsageResponse,
    WorkflowGenerateRequest,
    WorkflowInfoResponse,
    WorkflowPromoteRequest,
    WorkflowPublishResponse,
    WorkflowRollbackRequest,
    WorkflowRunRequest,
    WorkflowSaveRequest,
    WorkflowStepInfo,
    WorkflowVersionDiffResponse,
    WorkflowVersionListResponse,
    WorkflowVersionResponse,
)
from sandcastle.config import Settings, settings
from sandcastle.engine.audit import verify_audit_chain
from sandcastle.engine.dag import build_plan, parse_yaml_string, validate
from sandcastle.engine.executor import execute_workflow
from sandcastle.engine.sandshore import SandshoreRuntime, get_sandshore_runtime  # noqa: F401
from sandcastle.engine.storage import create_storage
from sandcastle.models.db import (
    ApiKey,
    ApprovalRequest,
    ApprovalStatus,
    AuditEvent,
    AutoPilotExperiment,
    AutoPilotSample,
    DeadLetterItem,
    EvalRun,
    EvalRunStatus,
    EvolutionIteration,
    ExperimentStatus,
    GoldenCase,
    GoldenDataset,
    HubSubmission,
    PolicyViolation,
    RoutingDecision,
    Run,
    RunCheckpoint,
    RunStatus,
    Schedule,
    Setting,
    ToolConnection,
    WorkflowEvolution,
    WorkflowVersion,
    WorkflowVersionStatus,
    async_session,
)
from sandcastle.queue.scheduler import add_schedule, remove_schedule
from sandcastle.queue.worker import enqueue_workflow

logger = logging.getLogger(__name__)

router = APIRouter()

# --- SSE streaming configuration ---
# Interval (in seconds) between keepalive comments sent to prevent
# proxies / load balancers from closing idle SSE connections.
SSE_KEEPALIVE_INTERVAL_SECONDS = 30

# --- Hub registry cache (5 min TTL, bounded to 100 entries) ---

_hub_cache: dict[str, tuple[float, Any]] = {}
_HUB_CACHE_TTL = 300  # 5 minutes
_HUB_CACHE_MAXSIZE = 100

# --- Batch run in-memory tracking ---
# Maps batch_id -> BatchStatusResponse dict for tracking batch progress.
# In production, this would be stored in Redis or PostgreSQL.
_batch_store: dict[str, dict[str, Any]] = {}
_BATCH_STORE_MAX_SIZE = 500  # Evict oldest completed batches when exceeded


def _evict_stale_batches() -> None:
    """Remove completed/failed batches when the store exceeds the max size.

    Keeps running batches intact. Evicts oldest completed batches first.
    """
    if len(_batch_store) <= _BATCH_STORE_MAX_SIZE:
        return
    # Sort non-running batches by creation time (oldest first)
    candidates = [
        (bid, b.get("created_at", ""))
        for bid, b in _batch_store.items()
        if b.get("status") != "running"
    ]
    candidates.sort(key=lambda x: x[1])
    # Evict until we are under the limit
    to_remove = len(_batch_store) - _BATCH_STORE_MAX_SIZE
    for bid, _ in candidates[:to_remove]:
        del _batch_store[bid]


def _get_hub_cache(key: str, tenant_id: str | None = None) -> Any | None:
    cache_key = f"{key}:{tenant_id}" if tenant_id else key
    if cache_key in _hub_cache:
        ts, data = _hub_cache[cache_key]
        if time.time() - ts < _HUB_CACHE_TTL:
            return data
        del _hub_cache[cache_key]
    return None


def _set_hub_cache(key: str, data: Any, tenant_id: str | None = None) -> None:
    cache_key = f"{key}:{tenant_id}" if tenant_id else key
    # Evict oldest entries when cache exceeds maxsize
    if len(_hub_cache) >= _HUB_CACHE_MAXSIZE and cache_key not in _hub_cache:
        # Remove the entry with the oldest timestamp
        oldest_key = min(_hub_cache, key=lambda k: _hub_cache[k][0])
        del _hub_cache[oldest_key]
    _hub_cache[cache_key] = (time.time(), data)


# --- Helpers ---


def _duration_seconds_expr():
    """Avg duration expression portable across PostgreSQL and SQLite."""
    if settings.is_local_mode:
        # SQLite: timestamps stored as ISO strings, julianday gives fractional days
        return func.avg((func.julianday(Run.completed_at) - func.julianday(Run.started_at)) * 86400)
    return func.avg(func.extract("epoch", Run.completed_at) - func.extract("epoch", Run.started_at))


def _trunc_day(column):
    """Truncate timestamp to day, portable across PostgreSQL and SQLite."""
    if settings.is_local_mode:
        # SQLite: date() extracts YYYY-MM-DD from ISO timestamp string
        return func.date(column)
    return func.date_trunc("day", column)


def _validate_workflow_input(input_data: dict, schema: dict | None) -> list[str]:
    """Validate and coerce workflow input against its input_schema.

    Mutates input_data in-place for type coercion.
    Returns list of error messages (empty = valid).
    """
    if schema is None:
        return []
    if not isinstance(schema, dict):
        return [f"input_schema must be a dict, got {type(schema).__name__}"]

    errors: list[str] = []

    # Check required fields
    for field_name in schema.get("required", []):
        val = input_data.get(field_name)
        if val is None or val == "":
            errors.append(f"Required input field '{field_name}' is missing or empty")

    # Type coercion based on properties
    properties = schema.get("properties", {})
    for field_name, prop in properties.items():
        if field_name not in input_data:
            continue
        value = input_data[field_name]
        field_type = prop.get("type")
        if not field_type:
            continue

        if field_type == "integer":
            if isinstance(value, str):
                try:
                    input_data[field_name] = int(value)
                except (ValueError, TypeError):
                    errors.append(
                        f"Input field '{field_name}' must be an integer, got '{value}'"
                    )
        elif field_type == "number":
            if isinstance(value, str):
                try:
                    input_data[field_name] = float(value)
                except (ValueError, TypeError):
                    errors.append(
                        f"Input field '{field_name}' must be a number, got '{value}'"
                    )
        elif field_type == "boolean":
            if isinstance(value, str):
                if value.lower() == "true":
                    input_data[field_name] = True
                elif value.lower() == "false":
                    input_data[field_name] = False
                else:
                    errors.append(
                        f"Input field '{field_name}' must be a boolean, got '{value}'"
                    )
        elif field_type == "array":
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    if not isinstance(parsed, list):
                        errors.append(
                            f"Input field '{field_name}' must be"
                            f" an array, got {type(parsed).__name__}"
                        )
                    else:
                        input_data[field_name] = parsed
                except (json.JSONDecodeError, TypeError):
                    errors.append(
                        f"Input field '{field_name}' must be a valid JSON array, got '{value}'"
                    )

    return errors


def _load_workflow_yaml(workflow_name: str) -> str:
    """Load workflow YAML content from the workflows directory by name."""
    import re

    if not workflow_name or not workflow_name.strip():
        raise ValueError("Workflow name must not be empty")

    # Reject path traversal characters before any filesystem operation
    if ".." in workflow_name or "/" in workflow_name or "\\" in workflow_name:
        raise FileNotFoundError(f"Invalid workflow name: '{workflow_name}'")

    workflows_dir = Path(settings.workflows_dir).resolve()
    # Slugified version: lowercase, non-alnum chars -> hyphens, collapse runs
    slug = re.sub(r"[^a-z0-9]+", "-", workflow_name.lower()).strip("-")
    # Try exact match, slugified match, then without extension
    for candidate in [
        workflows_dir / f"{workflow_name}.yaml",
        workflows_dir / f"{slug}.yaml",
        workflows_dir / workflow_name,
        workflows_dir / slug,
    ]:
        resolved = candidate.resolve()
        # Ensure the resolved path is within the workflows directory
        if not resolved.is_relative_to(workflows_dir):
            continue
        if resolved.exists() and resolved.is_file():
            return resolved.read_text()
    raise FileNotFoundError(f"Workflow '{workflow_name}' not found in {workflows_dir}")


async def _load_versioned_workflow_yaml(
    workflow_name: str, workflow_version: int | None
) -> str:
    """Load workflow YAML from WorkflowVersion DB if version is set, else from disk.

    Falls back to disk when version is None or the version row is missing.
    """
    if workflow_version is not None:
        async with async_session() as session:
            stmt = select(WorkflowVersion).where(
                WorkflowVersion.workflow_name == workflow_name,
                WorkflowVersion.version == workflow_version,
            )
            wv = (await session.execute(stmt)).scalar_one_or_none()
            if wv:
                return wv.yaml_content
            logger.warning(
                "WorkflowVersion %s v%d not found, falling back to disk",
                workflow_name,
                workflow_version,
            )
    return _load_workflow_yaml(workflow_name)


async def _resolve_workflow_request(request: WorkflowRunRequest) -> tuple[str, int | None]:
    """Resolve a WorkflowRunRequest to (YAML content, version number).

    Tries the registry first, then falls back to disk.
    """
    if request.workflow:
        return (request.workflow, None)
    if request.workflow_name:
        # Try registry first
        version_param = request.version
        if isinstance(version_param, str) and version_param.isdigit():
            version_param = int(version_param)
        result = await _load_workflow_from_registry(request.workflow_name, version_param)
        if result:
            return result
        # Fallback to disk and auto-import
        yaml_content = _load_workflow_yaml(request.workflow_name)
        try:
            ver = await _auto_import_workflow(request.workflow_name, yaml_content)
            return (yaml_content, ver)
        except Exception as e:
            logger.warning(
                "Auto-import failed for workflow '%s': %s",
                request.workflow_name,
                e,
            )
            return (yaml_content, None)
    raise ValueError("Either 'workflow' or 'workflow_name' must be provided")


def _apply_tenant_filter(stmt, tenant_id: str | None, column):
    """Apply tenant_id filter to a query when auth is enabled.

    When auth is required:
    - tenant_id is a string: filter to that tenant's data only
    - tenant_id is None: admin key (no tenant scope) - sees all data (by design)

    When auth is not required:
    - tenant_id is always None - no filtering applied (local/dev mode)
    """
    if settings.auth_required and tenant_id is not None:
        return stmt.where(column == tenant_id)
    return stmt


async def _resolve_budget(request_budget: float | None, tenant_id: str | None) -> float | None:
    """Resolve max_cost_usd with precedence: request > tenant > env.

    Returns None if no budget is set (unlimited).
    """
    # 1. Request-level budget takes priority
    if request_budget is not None and request_budget > 0:
        return request_budget
    # 2. Tenant API key budget
    if tenant_id and settings.auth_required:
        try:
            async with async_session() as session:
                stmt = (
                    select(ApiKey.max_cost_per_run_usd)
                    .where(
                        ApiKey.tenant_id == tenant_id,
                        ApiKey.is_active.is_(True),
                    )
                    .limit(1)
                )
                result = await session.scalar(stmt)
                if result and result > 0:
                    return result
        except Exception as e:
            logger.warning("Budget check failed, using default: %s", e)
    # 3. Env-level default
    if settings.default_max_cost_usd and settings.default_max_cost_usd > 0:
        return settings.default_max_cost_usd
    return None


# --- Workflow Registry Helpers ---


def _compute_checksum(yaml_content: str) -> str:
    """Compute SHA-256 checksum for workflow YAML content."""
    return hashlib.sha256(yaml_content.encode()).hexdigest()


async def _get_next_version(session, workflow_name: str) -> int:
    """Get the next version number for a workflow."""
    result = await session.scalar(
        select(func.max(WorkflowVersion.version)).where(
            WorkflowVersion.workflow_name == workflow_name
        )
    )
    return (result or 0) + 1


async def _load_workflow_from_registry(
    name: str, version: int | str | None = None
) -> tuple[str, int] | None:
    """Load workflow YAML from the registry.

    Returns (yaml_content, version_number) or None if not found.
    version=None -> production, version=int -> specific, version='latest' -> highest.
    """
    async with async_session() as session:
        if isinstance(version, int):
            stmt = select(WorkflowVersion).where(
                WorkflowVersion.workflow_name == name,
                WorkflowVersion.version == version,
            )
        elif version == "latest":
            stmt = (
                select(WorkflowVersion)
                .where(WorkflowVersion.workflow_name == name)
                .order_by(WorkflowVersion.version.desc())
                .limit(1)
            )
        else:
            # Default: production version
            stmt = select(WorkflowVersion).where(
                WorkflowVersion.workflow_name == name,
                WorkflowVersion.status == WorkflowVersionStatus.PRODUCTION,
            )
        result = await session.execute(stmt)
        wv = result.scalar_one_or_none()
        if wv:
            return (wv.yaml_content, wv.version)
    return None


async def _auto_import_workflow(name: str, yaml_content: str) -> int:
    """Auto-import a disk workflow into the registry as v1 production.

    Uses IntegrityError catch to handle concurrent insert race conditions.
    """
    from sqlalchemy.exc import IntegrityError

    checksum = _compute_checksum(yaml_content)
    async with async_session() as session:
        # Check if already imported
        existing = await session.scalar(
            select(WorkflowVersion.id).where(WorkflowVersion.workflow_name == name).limit(1)
        )
        if existing:
            return 1  # Already imported

        try:
            workflow = parse_yaml_string(yaml_content)
            steps_count = len(workflow.steps)
        except Exception:
            steps_count = 0

        wv = WorkflowVersion(
            workflow_name=name,
            version=1,
            status=WorkflowVersionStatus.PRODUCTION,
            yaml_content=yaml_content,
            description="Auto-imported from disk",
            steps_count=steps_count,
            checksum=checksum,
        )
        session.add(wv)
        try:
            await session.commit()
        except IntegrityError:
            # Another request already created it - that's fine
            await session.rollback()
        return 1


def _extract_step_configs(yaml_content: str) -> dict[str, dict]:
    """Extract model/prompt/max_turns config per step from workflow YAML."""
    try:
        workflow = parse_yaml_string(yaml_content)
    except Exception:
        return {}
    configs = {}
    for step in workflow.steps:
        configs[step.id] = {
            "model": step.model or workflow.default_model,
            "prompt": step.prompt,
            "max_turns": getattr(step, "max_turns", None) or workflow.default_max_turns,
        }
    return configs


# --- Health ---


@router.get("/health")
async def health_check() -> ApiResponse:
    """Check health of Sandcastle and its dependencies."""
    try:
        runtime = get_sandshore_runtime(
            anthropic_api_key=settings.anthropic_api_key or "",
            e2b_api_key=settings.e2b_api_key or "",
        )
        runtime_ok = await runtime.health()
    except Exception:
        runtime_ok = False

    # Check database
    db_ok = False
    try:
        async with async_session() as session:
            await session.execute(select(1))
            db_ok = True
    except Exception:
        pass

    # Check Redis (skip in local mode)
    redis_ok: bool | None = None
    if settings.redis_url:
        redis_ok = False
        try:
            from sandcastle.engine.executor import _get_redis

            r = await _get_redis()
            await r.ping()
            redis_ok = True
        except Exception:
            pass

    # In local mode, health is ok if runtime + db are fine (no Redis needed)
    checks = [runtime_ok, db_ok]
    if redis_ok is not None:
        checks.append(redis_ok)

    return ApiResponse(
        data=HealthResponse(
            status="ok" if all(checks) else "degraded",
            runtime=runtime_ok,
            redis=redis_ok,
            database=db_ok,
        )
    )


@router.get("/health/providers")
async def get_provider_health() -> ApiResponse:
    """Check reachability of all configured LLM providers.

    For each provider with a configured API key, performs a lightweight
    connectivity check and returns status, latency, and region.
    Results are cached for 5 minutes to avoid hammering provider APIs.
    """
    import os as _os

    from sandcastle.engine.generator import _PROVIDER_CONFIGS

    _CACHE_TTL = 300  # 5 minutes

    # Check module-level cache attached to the function object
    cached_at: float = getattr(get_provider_health, "_cache_ts", 0.0)
    if time.monotonic() - cached_at < _CACHE_TTL:
        cached = getattr(get_provider_health, "_cache", None)
        if cached is not None:
            return ApiResponse(data=cached)

    async def _check_provider(provider_name: str, cfg: dict) -> dict:
        key_env = cfg.get("api_key_env", "")
        region = cfg.get("region", "us")

        # Determine if key is configured
        api_key = ""
        if key_env:
            api_key = _os.environ.get(key_env, "")
            if not api_key:
                from sandcastle.config import settings as _s
                attr_map = {
                    "ANTHROPIC_API_KEY": "anthropic_api_key",
                    "OPENAI_API_KEY": "openai_api_key",
                    "MISTRAL_API_KEY": "mistral_api_key",
                    "MINIMAX_API_KEY": "minimax_api_key",
                    "OPENROUTER_API_KEY": "openrouter_api_key",
                }
                attr = attr_map.get(key_env)
                if attr:
                    api_key = getattr(_s, attr, "") or ""

        if not api_key and key_env:
            return {"status": "unconfigured", "latency_ms": None, "region": region}

        start = time.monotonic()
        try:
            headers_fn = cfg.get("headers_fn")
            headers = headers_fn(api_key) if headers_fn else {}

            if provider_name == "ollama":
                # Ollama: check /api/tags (no auth needed)
                url = "http://localhost:11434/api/tags"
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
            elif key_env == "ANTHROPIC_API_KEY":
                # Anthropic: minimal messages call - 400 = reachable (bad request ok)
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
                # OpenAI-compatible providers
                body = {
                    "model": cfg.get("model", "gpt-4o-mini"),
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "ping"}],
                }
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.post(cfg["api_url"], json=body, headers=headers)
                    if resp.status_code not in (200, 400, 404):
                        resp.raise_for_status()

            latency_ms = round((time.monotonic() - start) * 1000, 1)
            return {"status": "ok", "latency_ms": latency_ms, "region": region}
        except Exception as exc:
            latency_ms = round((time.monotonic() - start) * 1000, 1)
            return {
                "status": "down",
                "latency_ms": latency_ms,
                "region": region,
                "error": str(exc)[:200],
            }

    results: dict = {}
    for name, cfg in _PROVIDER_CONFIGS.items():
        results[name] = await _check_provider(name, cfg)

    # Store cache
    get_provider_health._cache = results  # type: ignore[attr-defined]
    get_provider_health._cache_ts = time.monotonic()  # type: ignore[attr-defined]

    return ApiResponse(data=results)


@router.get("/runtime")
async def runtime_info() -> ApiResponse:
    """Return current runtime mode information."""
    from sandcastle import __version__
    from sandcastle.models.db import _build_engine_url

    engine_url = _build_engine_url()
    db_type = "sqlite" if engine_url.startswith("sqlite") else "postgresql"
    queue_type = "in-process" if not settings.redis_url else "redis"
    storage_type = settings.storage_backend

    from sandcastle.engine.license import get_license

    lic = get_license()
    license_info = LicenseInfoResponse(
        status=lic.status.value,
        tier=lic.tier.value,
        licensee=lic.licensee,
        max_seats=lic.max_seats,
        expires=lic.expires,
    )

    return ApiResponse(
        data=RuntimeInfoResponse(
            mode="local" if settings.is_local_mode else "production",
            database=db_type,
            queue=queue_type,
            storage=storage_type,
            sandbox_backend=settings.sandbox_backend,
            data_dir=settings.data_dir if settings.is_local_mode else None,
            version=__version__,
            license=license_info,
            spark_mode=settings.spark_mode,
        )
    )


# --- Update check ---

_update_cache: dict[str, object] = {}
_update_cache_lock = asyncio.Lock()


@router.get("/check-update")
async def check_update() -> ApiResponse:
    """Check if a newer version of sandcastle-ai is available on PyPI."""
    import time

    from packaging.version import Version

    from sandcastle import __version__

    now = time.monotonic()
    cached = _update_cache.get("result")
    cached_at: float = _update_cache.get("ts", 0)  # type: ignore[assignment]
    if cached and (now - cached_at) < 1800:
        return ApiResponse(data=cached)

    async with _update_cache_lock:
        # Double-check after acquiring lock (another request may have filled cache)
        cached = _update_cache.get("result")
        cached_at = _update_cache.get("ts", 0)  # type: ignore[assignment]
        if cached and (time.monotonic() - cached_at) < 1800:
            return ApiResponse(data=cached)

        current = __version__
        latest = current
        update_available = False
        changelog_url = ""
        highlights: list[str] = []

        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get("https://pypi.org/pypi/sandcastle-ai/json")
                resp.raise_for_status()
                latest = resp.json()["info"]["version"]
                update_available = Version(latest) > Version(current)

                # If update available, fetch highlights from GitHub release
                if update_available:
                    changelog_url = f"https://github.com/gizmax/Sandcastle/releases/tag/v{latest}"
                    try:
                        gh_resp = await client.get(
                            f"https://api.github.com/repos/gizmax/Sandcastle/releases/tags/v{latest}"
                        )
                        body = gh_resp.json().get("body", "")
                        # Extract first 3 bullet points starting with "- **"
                        highlights = [
                            line.strip("- *").split("**")[0].strip()
                            for line in body.split("\n")
                            if line.strip().startswith("- **")
                        ][:3]
                    except Exception:
                        highlights = []
        except Exception:
            pass  # graceful degradation

        result = UpdateCheckResponse(
            current_version=current,
            latest_version=latest,
            update_available=update_available,
            release_url="https://github.com/gizmax/Sandcastle/releases",
            install_command="pip install --upgrade sandcastle-ai",
            changelog_url=changelog_url,
            highlights=highlights,
        )
        _update_cache["result"] = result
        _update_cache["ts"] = time.monotonic()
        return ApiResponse(data=result)


# --- Pre-update backup helpers ---

_SANDCASTLE_HOME = Path.home() / ".sandcastle"


def _is_in_blackout_window() -> bool:
    """Return True if current UTC time falls inside the configured update blackout window."""
    start = settings.update_blackout_start.strip()
    end = settings.update_blackout_end.strip()
    if not start or not end:
        return False
    try:
        # Use UTC to avoid timezone-dependent behavior on different servers
        now = datetime.now(timezone.utc)
        h_s, m_s = int(start.split(":")[0]), int(start.split(":")[1])
        h_e, m_e = int(end.split(":")[0]), int(end.split(":")[1])
        if not (0 <= h_s <= 23 and 0 <= m_s <= 59):
            logger.warning("Invalid blackout start time: %s", start)
            return False
        if not (0 <= h_e <= 23 and 0 <= m_e <= 59):
            logger.warning("Invalid blackout end time: %s", end)
            return False
        current_minutes = now.hour * 60 + now.minute
        start_minutes = h_s * 60 + m_s
        end_minutes = h_e * 60 + m_e
        if start_minutes <= end_minutes:
            return start_minutes <= current_minutes < end_minutes
        # Overnight window (e.g. 22:00 - 06:00)
        return current_minutes >= start_minutes or current_minutes < end_minutes
    except (ValueError, IndexError):
        return False


async def _pre_update_backup(current_version: str) -> None:
    """Create pre-update backups of the database and .env file."""
    # Store previous version
    _SANDCASTLE_HOME.mkdir(parents=True, exist_ok=True)
    previous_version_file = _SANDCASTLE_HOME / "previous_version"
    previous_version_file.write_text(current_version)

    # If SQLite: copy DB file to {db_path}.pre-update
    db_url = settings.database_url
    if not db_url or db_url.startswith("sqlite"):
        if db_url:
            # Extract path from sqlite:///path or sqlite+aiosqlite:///path
            db_path_str = db_url.split("///")[-1] if "///" in db_url else ""
        else:
            db_path_str = str(Path(settings.data_dir) / "sandcastle.db")
        if db_path_str:
            db_path = Path(db_path_str)
            if db_path.exists():
                import shutil
                backup_path = db_path.with_suffix(db_path.suffix + ".pre-update")
                shutil.copy2(str(db_path), str(backup_path))
                logger.info("Database backup created: %s", backup_path)

    # Copy .env to .env.pre-update if exists
    env_path = Path(".env")
    if env_path.exists():
        import shutil
        shutil.copy2(".env", ".env.pre-update")
        logger.info("Environment file backup created: .env.pre-update")


async def _restore_pre_update_backup() -> None:
    """Restore database from pre-update backup if it exists."""
    db_url = settings.database_url
    if not db_url or db_url.startswith("sqlite"):
        if db_url:
            db_path_str = db_url.split("///")[-1] if "///" in db_url else ""
        else:
            db_path_str = str(Path(settings.data_dir) / "sandcastle.db")
        if db_path_str:
            db_path = Path(db_path_str)
            backup_path = db_path.with_suffix(db_path.suffix + ".pre-update")
            if backup_path.exists():
                import shutil
                shutil.copy2(str(backup_path), str(db_path))
                logger.info("Database restored from backup: %s", backup_path)


async def _emit_update_audit(
    event_type: str,
    payload: dict,
    source_ip: str | None = None,
) -> None:
    """Emit an audit event for update operations. Failures are silently logged."""
    try:
        from sandcastle.engine.audit import append_audit_event

        async with async_session() as session:
            await append_audit_event(
                session=session,
                event_type=event_type,
                run_id=None,
                actor_id="admin",
                payload=payload,
                source_ip=source_ip,
            )
    except Exception:
        logger.warning("Failed to emit audit event: %s", event_type, exc_info=True)


@router.post("/admin/update")
async def trigger_update(req: Request, body: UpdateRequest | None = None) -> ApiResponse:
    """Trigger a software update to the specified or latest version.

    Creates pre-update backups, installs the new version via pip, and verifies
    the installation. Requires admin privileges.
    """
    _require_admin(req)

    from packaging.version import Version

    from sandcastle import __version__

    current = __version__
    source_ip = req.client.host if req.client else None

    # Check blackout window
    if _is_in_blackout_window():
        return ApiResponse(
            data=UpdateResponse(
                status="failed",
                previous_version=current,
                error=(
                    f"Update blocked: blackout window active "
                    f"({settings.update_blackout_start} - {settings.update_blackout_end})"
                ),
            )
        )

    # Check update channel
    if settings.update_channel == "pin":
        if not settings.pinned_version:
            return ApiResponse(
                data=UpdateResponse(
                    status="failed",
                    previous_version=current,
                    error="Update channel is 'pin' but no pinned_version is configured",
                )
            )
        target = settings.pinned_version
    elif body and body.target_version:
        target = body.target_version
    else:
        # Fetch latest from PyPI
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get("https://pypi.org/pypi/sandcastle-ai/json")
                resp.raise_for_status()
                target = resp.json()["info"]["version"]
        except Exception as exc:
            return ApiResponse(
                data=UpdateResponse(
                    status="failed",
                    previous_version=current,
                    error=f"Failed to fetch latest version from PyPI: {exc}",
                )
            )

    # Skip if already on target version
    if Version(target) == Version(current):
        return ApiResponse(
            data=UpdateResponse(
                status="success",
                new_version=current,
                previous_version=current,
                restart_required=False,
                error="Already on target version",
            )
        )

    # Filter beta versions when channel is stable
    if settings.update_channel == "stable" and ("a" in target or "b" in target or "rc" in target):
        return ApiResponse(
            data=UpdateResponse(
                status="failed",
                previous_version=current,
                error=f"Version {target} is a pre-release; update_channel is 'stable'",
            )
        )

    # Emit audit: update started
    await _emit_update_audit(
        "update.started",
        {"current_version": current, "target_version": target},
        source_ip=source_ip,
    )

    # Pre-update backup
    try:
        await _pre_update_backup(current)
    except Exception as exc:
        logger.error("Pre-update backup failed: %s", exc, exc_info=True)
        # Continue with update even if backup fails - log the warning

    # Run pip install
    try:
        proc = await asyncio.create_subprocess_exec(
            "pip", "install", "--upgrade", f"sandcastle-ai=={target}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

        if proc.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace").strip()
            await _emit_update_audit(
                "update.failed",
                {"target_version": target, "error": error_msg},
                source_ip=source_ip,
            )
            return ApiResponse(
                data=UpdateResponse(
                    status="failed",
                    previous_version=current,
                    error=f"pip install failed (exit {proc.returncode}): {error_msg[:500]}",
                )
            )
    except asyncio.TimeoutError:
        await _emit_update_audit(
            "update.failed",
            {"target_version": target, "error": "pip install timed out after 120s"},
            source_ip=source_ip,
        )
        return ApiResponse(
            data=UpdateResponse(
                status="failed",
                previous_version=current,
                error="pip install timed out after 120 seconds",
            )
        )
    except Exception as exc:
        await _emit_update_audit(
            "update.failed",
            {"target_version": target, "error": str(exc)},
            source_ip=source_ip,
        )
        return ApiResponse(
            data=UpdateResponse(
                status="failed",
                previous_version=current,
                error=f"Failed to run pip: {exc}",
            )
        )

    # Verify installation by checking the installed version
    try:
        verify_proc = await asyncio.create_subprocess_exec(
            "python", "-c",
            "from sandcastle import __version__; print(__version__)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        v_stdout, _ = await asyncio.wait_for(verify_proc.communicate(), timeout=15)
        installed_version = v_stdout.decode("utf-8").strip()
    except Exception:
        installed_version = target  # Assume success if verification fails

    # Invalidate update check cache
    _update_cache.clear()

    await _emit_update_audit(
        "update.completed",
        {"new_version": installed_version, "previous_version": current},
        source_ip=source_ip,
    )

    return ApiResponse(
        data=UpdateResponse(
            status="success",
            new_version=installed_version,
            previous_version=current,
            restart_required=True,
        )
    )


@router.post("/admin/rollback")
async def trigger_rollback(req: Request) -> ApiResponse:
    """Roll back to the previously installed version.

    Reads the stored previous version from ~/.sandcastle/previous_version,
    reinstalls that version, and restores the database backup if available.
    """
    _require_admin(req)

    from sandcastle import __version__

    current = __version__
    source_ip = req.client.host if req.client else None

    # Read previous version
    previous_version_file = _SANDCASTLE_HOME / "previous_version"
    if not previous_version_file.exists():
        return ApiResponse(
            data=UpdateResponse(
                status="failed",
                previous_version=current,
                error="No previous version found. No update has been performed yet.",
            )
        )

    previous = previous_version_file.read_text().strip()
    if not previous:
        return ApiResponse(
            data=UpdateResponse(
                status="failed",
                previous_version=current,
                error="Previous version file is empty",
            )
        )

    # Emit audit: rollback started
    await _emit_update_audit(
        "update.rollback",
        {"current_version": current, "rolling_back_to": previous},
        source_ip=source_ip,
    )

    # Run pip install for the previous version
    try:
        proc = await asyncio.create_subprocess_exec(
            "pip", "install", f"sandcastle-ai=={previous}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

        if proc.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace").strip()
            return ApiResponse(
                data=UpdateResponse(
                    status="failed",
                    previous_version=current,
                    error=f"Rollback pip install failed (exit {proc.returncode}): {error_msg[:500]}",
                )
            )
    except asyncio.TimeoutError:
        return ApiResponse(
            data=UpdateResponse(
                status="failed",
                previous_version=current,
                error="Rollback pip install timed out after 120 seconds",
            )
        )
    except Exception as exc:
        return ApiResponse(
            data=UpdateResponse(
                status="failed",
                previous_version=current,
                error=f"Rollback failed: {exc}",
            )
        )

    # Restore database backup if exists
    try:
        await _restore_pre_update_backup()
    except Exception as exc:
        logger.error("Database restore failed during rollback: %s", exc, exc_info=True)

    # Invalidate update check cache
    _update_cache.clear()

    # Clean up the previous_version file after successful rollback
    try:
        previous_version_file.unlink()
    except OSError:
        pass

    return ApiResponse(
        data=UpdateResponse(
            status="success",
            rolled_back_to=previous,
            previous_version=current,
            restart_required=True,
        )
    )


# --- Browse (file system) ---


@router.get("/browse")
async def browse_directory(
    path: str = Query("~", description="Directory path to browse"),
    request: Request = None,
) -> ApiResponse:
    """Browse server filesystem directories for workflow input configuration.

    Only available in local mode. In multi-tenant production mode, filesystem
    browsing is disabled to prevent cross-tenant information leakage.
    """
    if not settings.is_local_mode:
        raise HTTPException(
            status_code=403,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="FORBIDDEN",
                    message="Directory browsing is only available in local mode",
                )
            ).model_dump(),
        )

    try:
        target = Path(path).expanduser().resolve()
    except (ValueError, OSError):
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_PATH", message="Invalid path")
            ).model_dump(),
        )

    # Enforce sandbox root when configured
    if settings.sandbox_root:
        sandbox = Path(settings.sandbox_root).expanduser().resolve()
        if not target.is_relative_to(sandbox):
            raise HTTPException(
                status_code=403,
                detail=ApiResponse(
                    error=ErrorResponse(code="FORBIDDEN", message="Path outside sandbox root")
                ).model_dump(),
            )

    if not target.exists():
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message="Path does not exist")
            ).model_dump(),
        )
    if not target.is_dir():
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_PATH", message="Path is not a directory")
            ).model_dump(),
        )

    entries = []
    try:
        for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            # Skip hidden files/dirs
            if item.name.startswith("."):
                continue
            entries.append(
                {
                    "name": item.name,
                    "path": str(item),
                    "is_dir": item.is_dir(),
                }
            )
    except PermissionError:
        raise HTTPException(
            status_code=403,
            detail=ApiResponse(
                error=ErrorResponse(code="FORBIDDEN", message="Permission denied")
            ).model_dump(),
        )

    return ApiResponse(
        data={
            "current": str(target),
            "parent": str(target.parent) if target != target.parent else None,
            "entries": entries,
        }
    )


# --- Upload (file system / S3) ---

_UPLOAD_ALLOWED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".svg",
    ".pdf", ".txt", ".csv", ".json", ".yaml", ".yml",
    ".xlsx", ".docx", ".pptx",
}
# 50 MB for S3/production, 10 MB for local
_UPLOAD_MAX_BYTES_S3 = 50 * 1024 * 1024
_UPLOAD_MAX_BYTES_LOCAL = 10 * 1024 * 1024

# Map common extensions to MIME types for S3 uploads
_EXTENSION_CONTENT_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


@router.post("/upload")
async def upload_file(file: UploadFile) -> ApiResponse:
    """Upload a file and return a file_id for use as workflow input.

    In local mode the file is saved under ``{data_dir}/uploads/{uuid8}_{filename}``.
    In S3 mode the file is uploaded to ``uploads/{uuid8}_{filename}`` in the
    configured bucket. The returned ``file_id`` can be used as a workflow input
    value (prefixed with ``@upload:``) and will be resolved to file content at
    execution time.
    """
    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="BAD_REQUEST", message="No filename provided")
            ).model_dump(),
        )

    # Sanitize filename - take only the basename to prevent path traversal
    safe_name = Path(file.filename).name
    if not safe_name or safe_name in (".", ".."):
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="BAD_REQUEST", message="Invalid filename")
            ).model_dump(),
        )

    # Extension allowlist
    suffix = Path(safe_name).suffix.lower()
    if suffix not in _UPLOAD_ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="BAD_REQUEST",
                    message=f"File type not allowed: {suffix}",
                )
            ).model_dump(),
        )

    is_s3 = settings.storage_backend == "s3"
    max_bytes = _UPLOAD_MAX_BYTES_S3 if is_s3 else _UPLOAD_MAX_BYTES_LOCAL

    # Read file into memory and enforce size limit
    contents = await file.read()
    if len(contents) > max_bytes:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="BAD_REQUEST",
                    message=f"File too large ({len(contents)} bytes). Maximum is {max_bytes} bytes.",
                )
            ).model_dump(),
        )

    file_id = uuid.uuid4().hex[:8]
    dest_name = f"{file_id}_{safe_name}"
    content_type = _EXTENSION_CONTENT_TYPES.get(suffix, "application/octet-stream")

    if is_s3:
        # Upload to S3 as raw bytes via a dedicated binary write
        try:
            # S3Storage.write() expects a string; for binary files we use
            # a raw aioboto3 call so we can pass bytes and set ContentType.
            import aioboto3

            session = aioboto3.Session(
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
            )
            s3_key = f"uploads/{dest_name}"
            async with session.client(
                "s3", endpoint_url=settings.storage_endpoint or None
            ) as s3:
                await s3.put_object(
                    Bucket=settings.storage_bucket,
                    Key=s3_key,
                    Body=contents,
                    ContentType=content_type,
                )
            url = f"s3://{settings.storage_bucket}/{s3_key}"
        except Exception as exc:
            logger.error("S3 upload failed for '%s': %s", dest_name, exc)
            raise HTTPException(
                status_code=500,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="UPLOAD_FAILED",
                        message="Failed to upload file to S3",
                    )
                ).model_dump(),
            )

        return ApiResponse(
            data={
                "file_id": file_id,
                "filename": safe_name,
                "content_type": content_type,
                "size_bytes": len(contents),
                "storage": "s3",
                "url": url,
                # Legacy field kept for backward compatibility
                "path": s3_key,
            }
        )

    # --- Local storage ---
    uploads_dir = Path(settings.data_dir).expanduser().resolve() / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    dest_path = (uploads_dir / dest_name).resolve()

    # Defense-in-depth: ensure final path is within uploads dir
    if not dest_path.is_relative_to(uploads_dir):
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="BAD_REQUEST", message="Invalid filename")
            ).model_dump(),
        )

    dest_path.write_bytes(contents)

    return ApiResponse(
        data={
            "file_id": file_id,
            "filename": safe_name,
            "content_type": content_type,
            "size_bytes": len(contents),
            "storage": "local",
            "url": None,
            # Legacy field kept for backward compatibility
            "path": str(dest_path),
        }
    )


# --- Templates ---


def _score_template_relevance(
    name: str,
    description: str,
    tags: list[str],
    query_words: set[str],
) -> float:
    """Score a template against query words using word overlap.

    Returns a score between 0.0 and 1.0 indicating relevance.
    """
    if not query_words:
        return 0.0

    # Build searchable text from name, description, and tags
    searchable = (
        name.lower().replace("-", " ").replace("_", " ")
        + " " + description.lower()
        + " " + " ".join(t.lower() for t in tags)
    )
    template_words = set(
        w for w in re.findall(r"[a-z]+", searchable) if len(w) > 2
    )
    if not template_words:
        return 0.0

    overlap = len(query_words & template_words)
    return overlap / len(query_words)


def _template_to_dict(t: Any, relevance_score: float | None) -> dict[str, Any]:
    """Serialize a TemplateInfo for the templates list endpoints.

    ``proven`` is True when the template was installed from a verified .sctpl
    bundle - the bundle archive sits next to the workflow YAML and carries the
    replayable proof-of-execution.
    """
    from sandcastle.engine.bundle import bundle_for_template

    return {
        "name": t.name,
        "description": t.description,
        "tags": t.tags,
        "step_count": t.step_count,
        "input_schema": t.input_schema,
        "category": t.category,
        "source": t.source,
        "relevance_score": relevance_score,
        "proven": t.source == "community" and bundle_for_template(t.file_name) is not None,
    }


@router.get("/templates")
async def list_templates(
    q: str | None = Query(None, description="Search query for templates"),
) -> ApiResponse:
    """List all available workflow templates, optionally filtered by search query.

    When a search query is provided, templates are first filtered by keyword
    match (name/description/tags). If no exact matches are found, fuzzy
    matching by word overlap is used as a fallback. Results include a
    ``relevance_score`` field (0.0-1.0) when a query is active.

    Public endpoint - no authentication required.
    """
    from sandcastle.templates import list_templates as _list_templates

    templates = _list_templates()

    if not q or not q.strip():
        return ApiResponse(data=[_template_to_dict(t, None) for t in templates])

    query = q.strip().lower()

    # Phase 1: exact keyword match (substring in name/description/tags)
    exact_matches = []
    for t in templates:
        if (
            query in t.name.lower()
            or query in t.description.lower()
            or any(query in tag.lower() for tag in t.tags)
        ):
            exact_matches.append((t, 1.0))

    if exact_matches:
        return ApiResponse(
            data=[_template_to_dict(t, score) for t, score in exact_matches]
        )

    # Phase 2: fuzzy word overlap matching
    query_words = set(
        w for w in re.findall(r"[a-z]+", query) if len(w) > 2
    )
    if not query_words:
        return ApiResponse(data=[])

    scored: list[tuple[Any, float]] = []
    for t in templates:
        score = _score_template_relevance(
            t.name, t.description, t.tags, query_words,
        )
        if score > 0.0:
            scored.append((t, round(score, 3)))

    # Sort by score descending
    scored.sort(key=lambda x: x[1], reverse=True)

    return ApiResponse(data=[_template_to_dict(t, score) for t, score in scored])


@router.get("/templates/{template_name}")
async def get_template(template_name: str) -> ApiResponse:
    """Get a single workflow template with full YAML content and metadata.

    Public endpoint - no authentication required.
    """
    from sandcastle.templates import get_template as _get_template

    try:
        content, info = _get_template(template_name)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message=str(exc))
            ).model_dump(),
        ) from exc

    from sandcastle.engine.bundle import bundle_for_template

    return ApiResponse(
        data={
            "name": info.name,
            "description": info.description,
            "tags": info.tags,
            "step_count": info.step_count,
            "file_name": info.file_name,
            "content": content,
            "input_schema": info.input_schema,
            "category": info.category,
            "source": info.source,
            "proven": info.source == "community"
            and bundle_for_template(info.file_name) is not None,
        }
    )


def _resolve_template_bundle(template_name: str) -> tuple[str, Any, Path]:
    """Resolve a template and its installed .sctpl bundle, or raise 404."""
    from sandcastle.engine.bundle import bundle_for_template
    from sandcastle.templates import get_template as _get_template

    try:
        content, info = _get_template(template_name)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message=str(exc))
            ).model_dump(),
        ) from exc

    bundle_path = (
        bundle_for_template(info.file_name) if info.source == "community" else None
    )
    if bundle_path is None:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="NOT_FOUND",
                    message=f"Template '{template_name}' was not installed from a "
                    "verified .sctpl bundle",
                )
            ).model_dump(),
        )
    return content, info, bundle_path


@router.get("/templates/{template_name}/verification")
async def get_template_verification(template_name: str) -> ApiResponse:
    """Verification status for a bundle-installed template.

    Returns the bundle manifest (author, version, checksums) plus checksum
    validity for every payload file, and whether the installed workflow YAML
    still byte-matches the bundled one. Templates not installed from a .sctpl
    bundle return ``{"proven": false}``.

    Public endpoint - no authentication required.
    """
    from sandcastle.engine.bundle import BundleError, bundle_for_template, bundle_status
    from sandcastle.templates import get_template as _get_template

    try:
        content, info = _get_template(template_name)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message=str(exc))
            ).model_dump(),
        ) from exc

    bundle_path = (
        bundle_for_template(info.file_name) if info.source == "community" else None
    )
    if bundle_path is None:
        return ApiResponse(data={"proven": False})

    try:
        status = await asyncio.to_thread(bundle_status, bundle_path)
    except BundleError as exc:
        return ApiResponse(data={"proven": False, "error": str(exc)})

    installed_sha = hashlib.sha256(content.encode()).hexdigest()
    status["installed_workflow_matches"] = installed_sha == status["workflow"]["sha256"]
    return ApiResponse(data={"proven": True, **status})


@router.post("/templates/{template_name}/verify")
async def verify_template_bundle(template_name: str) -> ApiResponse:
    """Replay a bundle-installed template's proof-of-execution cassettes.

    Runs the same strict replay as ``sandcastle template verify`` - checksums,
    security scan, then every cassette replayed offline at $0 - and reports
    PASS/FAIL per cassette. 404 when the template has no installed bundle.

    Public endpoint - no authentication required.
    """
    from sandcastle.engine.bundle import verify_bundle

    _content, _info, bundle_path = _resolve_template_bundle(template_name)

    result = await asyncio.to_thread(verify_bundle, bundle_path)
    return ApiResponse(
        data={
            "ok": result.ok,
            "errors": result.errors,
            "cassettes": [
                {
                    "file": c.file,
                    "passed": c.passed,
                    "detail": c.detail,
                    "replay_hits": c.replay_hits,
                    "replay_misses": c.replay_misses,
                }
                for c in result.cassette_results
            ],
        }
    )


# --- Community Hub & Export ---


def _sanitize_workflow_yaml(yaml_content: str) -> str:
    """Remove sensitive data from workflow YAML for safe sharing."""
    # Remove env var references like ${API_KEY} or $API_KEY
    sanitized = re.sub(r"\$\{[A-Z_]+\}", "<REDACTED>", yaml_content)
    sanitized = re.sub(r"\$[A-Z_]{3,}", "<REDACTED>", sanitized)
    # Remove lines that look like they contain secrets
    lines: list[str] = []
    for line in sanitized.split("\n"):
        lower = line.lower()
        if (
            any(kw in lower for kw in ("password:", "secret:", "token:", "api_key:"))
            and "<REDACTED>" not in line
        ):
            # Check if it's a key-value with an actual value (not a variable reference)
            if ":" in line:
                key, _, val = line.partition(":")
                val = val.strip()
                if val and not val.startswith("{") and not val.startswith("<"):
                    line = f"{key}: <REDACTED>"
        lines.append(line)
    return "\n".join(lines)


@router.get("/hub/registry")
async def get_hub_registry() -> ApiResponse:
    """Proxy the community hub registry for dashboard consumption.

    Public endpoint - no authentication required.
    """
    cached = _get_hub_cache("registry")
    if cached is not None:
        return ApiResponse(data=cached)

    registry_url = "https://raw.githubusercontent.com/gizmax/Sandcastle/main/hub/registry.json"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(registry_url)
            resp.raise_for_status()
            data = resp.json()
            _set_hub_cache("registry", data)
            return ApiResponse(data=data)
    except Exception:
        logger.error("Failed to fetch hub registry", exc_info=True)
        fallback = {
            "version": 2,
            "templates": [],
            "categories": [],
            "stats": {"total_templates": 0, "total_authors": 0},
            "collections": [],
        }
        _set_hub_cache("registry", fallback)
        return ApiResponse(data=fallback)


@router.get("/hub/collections")
async def get_hub_collections() -> ApiResponse:
    """Get curated workflow collections from the community hub.

    Public endpoint - no authentication required.
    """
    cached = _get_hub_cache("collections")
    if cached is not None:
        return ApiResponse(data=cached)

    registry_url = "https://raw.githubusercontent.com/gizmax/Sandcastle/main/hub/registry.json"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(registry_url)
            resp.raise_for_status()
            data = resp.json()
            collections = data.get("collections", [])
            # Enrich with template details
            templates_by_slug: dict[str, dict] = {t["slug"]: t for t in data.get("templates", [])}
            for col in collections:
                col["templates"] = [
                    templates_by_slug[slug]
                    for slug in col.get("template_slugs", [])
                    if slug in templates_by_slug
                ]
                col["template_count"] = len(col["templates"])
            _set_hub_cache("collections", collections)
            return ApiResponse(data=collections)
    except Exception:
        return ApiResponse(data=[])


@router.post("/hub/playground")
async def hub_playground(request: Request) -> ApiResponse:
    """Simulate a workflow execution for the community hub playground.

    Returns a mock result - no actual execution happens.
    Public endpoint - no authentication required.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_JSON", message="Invalid JSON body")
            ).model_dump(),
        )

    # Validate body is a dict and sanitize inputs to prevent abuse
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_JSON", message="Request body must be a JSON object")
            ).model_dump(),
        )
    template_slug = str(body.get("slug", ""))[:200]  # Bound length
    inputs = body.get("inputs", {})
    if not isinstance(inputs, dict):
        inputs = {}
    step_count = body.get("step_count", 3)
    if not isinstance(step_count, int) or step_count < 0 or step_count > 1000:
        step_count = 3

    # Generate a simulated result based on the template
    result = {
        "slug": template_slug,
        "status": "completed",
        "execution_time": "3.2s",
        "steps_completed": step_count,
        "output": (
            f"Simulated output for '{template_slug}' with {len(inputs)} input(s). "
            "Install this workflow and connect your tools to see real results."
        ),
        "note": "This is a demo preview - install the workflow for actual execution.",
    }
    return ApiResponse(data=result)


@router.post("/hub/install/{slug:path}")
async def install_hub_template(req: Request, slug: str) -> ApiResponse:
    """Install a community workflow from the hub registry.

    Downloads the YAML from GitHub and saves it to the community
    templates directory. Returns the installed template metadata.
    """
    _require_admin(req)
    # Validate slug format (author/name)
    parts = slug.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_SLUG",
                    message="Slug must be in format 'author/name' with non-empty parts",
                )
            ).model_dump(),
        )

    # Fetch registry to find the template
    registry_url = "https://raw.githubusercontent.com/gizmax/Sandcastle/main/hub/registry.json"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(registry_url)
            resp.raise_for_status()
            registry = resp.json()
    except Exception:
        raise HTTPException(
            status_code=502,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="REGISTRY_UNAVAILABLE",
                    message="Could not fetch community hub registry",
                )
            ).model_dump(),
        )

    # Find template by slug
    template_meta = None
    for t in registry.get("templates", []):
        if t.get("slug") == slug:
            template_meta = t
            break

    if template_meta is None:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="NOT_FOUND",
                    message=f"Template '{slug}' not found in community hub",
                )
            ).model_dump(),
        )

    download_url = template_meta.get("download_url")
    if not download_url:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="NO_DOWNLOAD_URL",
                    message=f"No download URL for '{slug}'",
                )
            ).model_dump(),
        )

    # SSRF prevention: only allow downloads from trusted GitHub domains
    from urllib.parse import urlparse

    parsed_url = urlparse(download_url)
    allowed_hosts = {"raw.githubusercontent.com", "github.com", "api.github.com"}
    if parsed_url.hostname not in allowed_hosts:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="UNTRUSTED_URL",
                    message="Download URL must be from GitHub",
                )
            ).model_dump(),
        )

    # Download the YAML content
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            yaml_resp = await client.get(download_url)
            yaml_resp.raise_for_status()
            yaml_content = yaml_resp.text
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="DOWNLOAD_FAILED",
                    message=f"Failed to download template: {exc}",
                )
            ).model_dump(),
        )

    # Size limit (512 KB)
    if len(yaml_content) > 512 * 1024:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="TEMPLATE_TOO_LARGE",
                    message=f"Template exceeds 512 KB size limit ({len(yaml_content)} bytes)",
                )
            ).model_dump(),
        )

    # Security scan
    from sandcastle.engine.hub_scanner import (
        compute_sha256,
        scan_template,
        verify_checksum,
    )

    scan = scan_template(yaml_content)
    if scan.errors:
        error_details = [
            {"code": e.code, "message": e.message, "step": e.step}
            for e in scan.errors
        ]
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="UNSAFE_TEMPLATE",
                    message="Template failed security scan",
                    details=error_details,
                )
            ).model_dump(),
        )

    # Checksum verification
    registry_sha = template_meta.get("sha256")
    if registry_sha and not verify_checksum(yaml_content, registry_sha):
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="CHECKSUM_MISMATCH",
                    message="Downloaded content does not match registry checksum",
                    details={
                        "expected": registry_sha,
                        "actual": compute_sha256(yaml_content),
                    },
                )
            ).model_dump(),
        )

    # Collect warnings (non-blocking for API)
    scan_warnings = [
        {"code": w.code, "message": w.message, "step": w.step}
        for w in scan.warnings
    ] if scan.warnings else []

    # Save to community templates directory
    community_dir = Path(__file__).parent.parent / "templates" / "community"
    community_dir.mkdir(parents=True, exist_ok=True)

    filename = slug.split("/")[-1] + ".yaml"
    target_path = community_dir / filename

    # Check if already installed
    already_existed = target_path.exists()
    target_path.write_text(yaml_content, encoding="utf-8")

    logger.info("Installed community template '%s' to %s", slug, target_path)

    return ApiResponse(
        data={
            "installed": True,
            "slug": slug,
            "name": template_meta.get("name", ""),
            "filename": filename,
            "path": filename,
            "updated": already_existed,
            "security_warnings": scan_warnings,
        }
    )


@router.delete("/hub/install/{slug:path}")
async def uninstall_hub_template(req: Request, slug: str) -> ApiResponse:
    """Uninstall a community workflow."""
    _require_admin(req)
    parts = slug.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_SLUG",
                    message="Slug must be in format 'author/name' with non-empty parts",
                )
            ).model_dump(),
        )

    community_dir = Path(__file__).parent.parent / "templates" / "community"
    filename = slug.split("/")[-1] + ".yaml"
    target_path = community_dir / filename

    if not target_path.exists():
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="NOT_FOUND",
                    message=f"Template '{slug}' is not installed",
                )
            ).model_dump(),
        )

    target_path.unlink()
    return ApiResponse(data={"uninstalled": True, "slug": slug})


@router.get("/hub/installed")
async def list_installed_hub_templates(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List all community templates installed locally."""
    community_dir = Path(__file__).parent.parent / "templates" / "community"
    if not community_dir.exists():
        return ApiResponse(data=[])

    installed = []
    for yaml_file in sorted([*community_dir.glob("*.yaml"), *community_dir.glob("*.yml")]):
        installed.append(
            {
                "filename": yaml_file.name,
                "name": yaml_file.stem.replace("_", " ").replace("-", " ").title(),
                "size_bytes": yaml_file.stat().st_size,
            }
        )

    return ApiResponse(data=installed[offset : offset + limit])


# ---------------------------------------------------------------------------
# Hub Marketplace: community submit / list / rate / download tracking
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Convert a name into a URL-safe slug fragment."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    return slug.strip("-")[:100]


def _extract_yaml_metadata(yaml_content: str) -> dict[str, Any]:
    """Parse YAML and extract workflow metadata fields."""
    import yaml as pyyaml

    try:
        data = pyyaml.safe_load(yaml_content) or {}
    except Exception:
        return {}

    steps = data.get("steps") or []
    step_count = len(steps) if isinstance(steps, list) else 0

    models_used: list[str] = []
    tools_used: list[str] = []
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            model = step.get("model")
            if model and isinstance(model, str) and model not in models_used:
                models_used.append(model)
            tool = step.get("tool")
            if tool and isinstance(tool, str) and tool not in tools_used:
                tools_used.append(tool)

    return {
        "name": str(data.get("name") or "").strip(),
        "step_count": step_count,
        "models_used": models_used,
        "tools_used": tools_used,
    }


@router.post("/hub/submit", status_code=201)
async def submit_to_hub(req: Request) -> ApiResponse:
    """Submit a workflow template to the community hub.

    Validates the YAML, extracts metadata, assigns a slug, and stores
    a HubSubmission record with status='pending'.
    """
    try:
        body = await req.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_JSON", message="Invalid JSON body")
            ).model_dump(),
        )

    try:
        submit_req = HubSubmitRequest(**body)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="VALIDATION_ERROR", message=str(exc))
            ).model_dump(),
        )

    try:
        import yaml as pyyaml
        parsed = pyyaml.safe_load(submit_req.yaml_content)
        if not isinstance(parsed, dict):
            raise ValueError("YAML must parse to a mapping")
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_YAML", message=f"Invalid workflow YAML: {exc}")
            ).model_dump(),
        )

    meta = _extract_yaml_metadata(submit_req.yaml_content)
    workflow_name = meta.get("name") or "workflow"

    author = get_tenant_id(req) or "community"
    base_slug = f"{_slugify(author)}/{_slugify(workflow_name)}"

    async with async_session() as session:
        slug = base_slug
        suffix = 0
        while True:
            existing = await session.execute(
                select(HubSubmission).where(HubSubmission.slug == slug)
            )
            if existing.scalar_one_or_none() is None:
                break
            suffix += 1
            slug = f"{base_slug}-{suffix}"

        submission = HubSubmission(
            slug=slug,
            name=workflow_name,
            description=submit_req.description,
            yaml_content=submit_req.yaml_content,
            category=submit_req.category,
            tags=submit_req.tags,
            author=author,
            status="pending",
            models_used=meta.get("models_used", []),
            tools_used=meta.get("tools_used", []),
            step_count=meta.get("step_count", 0),
        )
        session.add(submission)
        await session.commit()
        await session.refresh(submission)

    logger.info("New hub submission: slug=%s author=%s", slug, author)

    return ApiResponse(
        data=HubSubmissionResponse(
            id=str(submission.id),
            slug=submission.slug,
            name=submission.name,
            description=submission.description,
            category=submission.category,
            tags=submission.tags or [],
            author=submission.author,
            status=submission.status,
            step_count=submission.step_count,
            models_used=submission.models_used or [],
            tools_used=submission.tools_used or [],
            downloads=submission.downloads,
            rating=submission.rating,
            rating_count=submission.rating_count,
            created_at=submission.created_at,
        ).model_dump()
    )


@router.get("/hub/community")
async def list_community_templates(
    status: str = "approved",
    category: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List community-submitted templates.

    Defaults to status='approved' so only reviewed templates are shown.
    """
    valid_statuses = {"pending", "approved", "rejected"}
    if status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_STATUS",
                    message=f"status must be one of: {', '.join(sorted(valid_statuses))}",
                )
            ).model_dump(),
        )

    async with async_session() as session:
        query = select(HubSubmission).where(HubSubmission.status == status)
        if category:
            query = query.where(HubSubmission.category == category)
        query = query.order_by(HubSubmission.created_at.desc()).limit(limit).offset(offset)

        result = await session.execute(query)
        submissions = result.scalars().all()

        count_query = select(func.count()).select_from(HubSubmission).where(
            HubSubmission.status == status
        )
        if category:
            count_query = count_query.where(HubSubmission.category == category)
        total_result = await session.execute(count_query)
        total = total_result.scalar_one() or 0

    items = [
        HubSubmissionResponse(
            id=str(s.id),
            slug=s.slug,
            name=s.name,
            description=s.description,
            category=s.category,
            tags=s.tags or [],
            author=s.author,
            status=s.status,
            step_count=s.step_count,
            models_used=s.models_used or [],
            tools_used=s.tools_used or [],
            downloads=s.downloads,
            rating=s.rating,
            rating_count=s.rating_count,
            created_at=s.created_at,
        ).model_dump()
        for s in submissions
    ]

    return ApiResponse(
        data=items,
        meta=PaginationMeta(total=total, limit=limit, offset=offset),
    )


@router.post("/hub/templates/{slug:path}/rate")
async def rate_template(slug: str, req: Request) -> ApiResponse:
    """Rate a community template (1-5 stars).

    Updates a running average rating on the HubSubmission record.
    Rate-limited to prevent rating manipulation abuse.
    """
    if req.client and req.client.host != "testclient":
        await execution_limiter.check(req)
    try:
        body = await req.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_JSON", message="Invalid JSON body")
            ).model_dump(),
        )

    try:
        rate_req = HubRateRequest(**body)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="VALIDATION_ERROR", message=str(exc))
            ).model_dump(),
        )

    async with async_session() as session:
        result = await session.execute(
            select(HubSubmission).where(HubSubmission.slug == slug)
        )
        submission = result.scalar_one_or_none()
        if submission is None:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="NOT_FOUND",
                        message=f"Template '{slug}' not found",
                    )
                ).model_dump(),
            )

        old_count = submission.rating_count or 0
        old_avg = submission.rating or 0.0
        new_count = old_count + 1
        new_avg = (old_avg * old_count + rate_req.rating) / new_count

        submission.rating = round(new_avg, 2)
        submission.rating_count = new_count
        await session.commit()
        await session.refresh(submission)

    return ApiResponse(
        data={
            "slug": submission.slug,
            "rating": submission.rating,
            "rating_count": submission.rating_count,
        }
    )


@router.post("/hub/templates/{slug:path}/download")
async def track_download(slug: str, req: Request = None) -> ApiResponse:
    """Track a template download. Increments the download counter.

    Rate-limited to prevent counter inflation abuse.
    """
    if req and req.client and req.client.host != "testclient":
        await execution_limiter.check(req)
    async with async_session() as session:
        result = await session.execute(
            select(HubSubmission).where(HubSubmission.slug == slug)
        )
        submission = result.scalar_one_or_none()
        if submission is None:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="NOT_FOUND",
                        message=f"Template '{slug}' not found",
                    )
                ).model_dump(),
            )

        submission.downloads = (submission.downloads or 0) + 1
        await session.commit()
        await session.refresh(submission)

    return ApiResponse(data={"slug": submission.slug, "downloads": submission.downloads})


@router.get("/workflows/{name}/export")
async def export_workflow(name: str, request: Request) -> ApiResponse:
    """Export a saved workflow as sanitized YAML for sharing.

    Removes environment variable references and sensitive data.
    """
    _require_admin(request)
    async with async_session() as session:
        result = await session.execute(
            select(WorkflowVersion)
            .where(
                WorkflowVersion.workflow_name == name,
                WorkflowVersion.status == WorkflowVersionStatus.PRODUCTION,
            )
            .order_by(WorkflowVersion.version.desc())
            .limit(1)
        )
        wv = result.scalar_one_or_none()
        if not wv:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message=f"Workflow '{name}' not found")
                ).model_dump(),
            )

    # Sanitize the YAML content
    sanitized = _sanitize_workflow_yaml(wv.yaml_content)

    return ApiResponse(
        data={
            "name": wv.workflow_name,
            "description": wv.description or "",
            "yaml_content": sanitized,
            "step_count": wv.steps_count,
            "version": wv.version,
        }
    )


# --- Stats ---


@router.get("/stats")
async def get_stats(request: Request, response: Response) -> ApiResponse:
    """Get aggregated statistics for the overview dashboard."""
    response.headers["Cache-Control"] = "public, max-age=60"
    tenant_id = get_tenant_id(request)
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    async with async_session() as session:
        summary_q = select(
            func.count(Run.id).label("total"),
            func.count(case(
                (Run.status == RunStatus.COMPLETED, Run.id),
                else_=None,
            )).label("completed"),
            func.count(case(
                (Run.status.in_([
                    RunStatus.COMPLETED,
                    RunStatus.FAILED,
                    RunStatus.PARTIAL,
                ]), Run.id),
                else_=None,
            )).label("finished"),
            func.coalesce(func.sum(Run.total_cost_usd), 0.0).label("cost"),
            _duration_seconds_expr().label("avg_dur"),
        ).where(Run.created_at >= today_start)
        summary_q = _apply_tenant_filter(summary_q, tenant_id, Run.tenant_id)
        row = (await session.execute(summary_q)).one()
        total_today = row.total
        completed_today = row.completed
        finished_today = row.finished
        success_rate = (completed_today / finished_today) if finished_today else 0.0
        total_cost = row.cost
        avg_duration = row.avg_dur

        # Runs by day (last 30 days)
        thirty_days_ago = now - timedelta(days=30)
        rbd_q = (
            select(
                _trunc_day(Run.created_at).label("day"),
                Run.status,
                func.count(Run.id).label("count"),
            )
            .where(Run.created_at >= thirty_days_ago)
            .group_by("day", Run.status)
            .order_by("day")
            .limit(1000)
        )
        rbd_q = _apply_tenant_filter(rbd_q, tenant_id, Run.tenant_id)
        runs_by_day_raw = (await session.execute(rbd_q)).all()

        day_map: dict[str, dict] = {}
        for row in runs_by_day_raw:
            if hasattr(row.day, "strftime"):
                day_str = row.day.strftime("%Y-%m-%d")
            else:
                day_str = str(row.day) if row.day else "unknown"
            if day_str not in day_map:
                day_map[day_str] = {"date": day_str, "completed": 0, "failed": 0, "total": 0}
            status_val = row.status.value if hasattr(row.status, "value") else row.status
            if status_val == "completed":
                day_map[day_str]["completed"] += row.count
            elif status_val == "failed":
                day_map[day_str]["failed"] += row.count
            day_map[day_str]["total"] += row.count

        runs_by_day = list(day_map.values())

        # Cost by workflow (last 7 days)
        seven_days_ago = now - timedelta(days=7)
        cost_wf_q = (
            select(
                Run.workflow_name,
                func.coalesce(func.sum(Run.total_cost_usd), 0.0).label("cost"),
            )
            .where(Run.created_at >= seven_days_ago)
            .group_by(Run.workflow_name)
            .order_by(func.sum(Run.total_cost_usd).desc())
            .limit(1000)
        )
        cost_wf_q = _apply_tenant_filter(cost_wf_q, tenant_id, Run.tenant_id)
        cost_by_workflow = [
            {"workflow": row.workflow_name, "cost": float(row.cost)}
            for row in (await session.execute(cost_wf_q)).all()
        ]

    return ApiResponse(
        data=StatsResponse(
            total_runs_today=total_today or 0,
            success_rate=round(success_rate, 4),
            total_cost_today=float(total_cost or 0),
            avg_duration_seconds=round(float(avg_duration or 0), 1),
            runs_by_day=runs_by_day,
            cost_by_workflow=cost_by_workflow,
        )
    )


@router.get("/stats/forecast")
async def get_cost_forecast(request: Request) -> ApiResponse:
    """Get cost forecast based on real historical data (last 30 days + 7-day projection)."""
    tenant_id = get_tenant_id(request)
    now = datetime.now(timezone.utc)
    thirty_days_ago = now - timedelta(days=30)

    async with async_session() as session:
        # Daily costs for the last 30 days
        daily_q = (
            select(
                _trunc_day(Run.created_at).label("day"),
                func.coalesce(func.sum(Run.total_cost_usd), 0.0).label("cost"),
                func.count(Run.id).label("runs"),
            )
            .where(Run.created_at >= thirty_days_ago)
            .group_by("day")
            .order_by("day")
            .limit(1000)
        )
        daily_q = _apply_tenant_filter(daily_q, tenant_id, Run.tenant_id)
        rows = (await session.execute(daily_q)).all()

    # Build zero-filled 30-day historical data (inactive days = 0)
    db_data: dict[str, dict] = {}
    for row in rows:
        day_str = row.day.strftime("%Y-%m-%d") if hasattr(row.day, "strftime") else str(row.day)
        db_data[day_str] = {
            "date": day_str,
            "cost": round(float(row.cost), 4),
            "runs": int(row.runs),
        }

    historical = []
    for i in range(29, -1, -1):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        if day in db_data:
            historical.append(db_data[day])
        else:
            historical.append({"date": day, "cost": 0.0, "runs": 0})

    # Compute 7-day moving average for projection (uses zero-filled data)
    costs = [h["cost"] for h in historical]
    if len(costs) >= 7:
        recent_avg = sum(costs[-7:]) / 7
    elif costs:
        recent_avg = sum(costs) / len(costs)
    else:
        recent_avg = 0.0

    # Compute trend (last 14 days vs first 14 days)
    if len(costs) >= 14:
        first_half = sum(costs[:14]) / 14
        second_half = sum(costs[14:]) / max(len(costs) - 14, 1)
        trend_pct = ((second_half - first_half) / first_half * 100) if first_half > 0 else 0.0
    else:
        trend_pct = 0.0

    # Project next 7 days
    projected = []
    for i in range(1, 8):
        future_date = now + timedelta(days=i)
        projected.append({
            "date": future_date.strftime("%Y-%m-%d"),
            "cost": round(recent_avg, 4),
        })

    # Monthly projection
    projected_monthly = round(recent_avg * 30, 2)

    return ApiResponse(
        data={
            "historical": historical,
            "projected": projected,
            "daily_average": round(recent_avg, 4),
            "trend_percent": round(trend_pct, 1),
            "projected_monthly": projected_monthly,
        }
    )


@router.get("/stats/sparklines")
async def get_sparklines(request: Request, response: Response) -> ApiResponse:
    """Return 7-day sparkline data for runs, success rate, cost, and duration."""
    response.headers["Cache-Control"] = "public, max-age=60"
    tenant_id = get_tenant_id(request)
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)

    async with async_session() as session:
        daily_q = (
            select(
                _trunc_day(Run.created_at).label("day"),
                func.count(Run.id).label("total"),
                func.count(case(
                    (Run.status == RunStatus.COMPLETED, Run.id),
                    else_=None,
                )).label("completed"),
                func.count(case(
                    (Run.status.in_([
                        RunStatus.COMPLETED,
                        RunStatus.FAILED,
                        RunStatus.PARTIAL,
                    ]), Run.id),
                    else_=None,
                )).label("finished"),
                func.coalesce(func.sum(Run.total_cost_usd), 0.0).label("cost"),
            )
            .where(Run.created_at >= seven_days_ago)
            .group_by("day")
            .order_by("day")
            .limit(1000)
        )
        daily_q = _apply_tenant_filter(daily_q, tenant_id, Run.tenant_id)
        rows = (await session.execute(daily_q)).all()

        # Duration query - separate to avoid NULL issues
        dur_q = (
            select(
                _trunc_day(Run.created_at).label("day"),
                _duration_seconds_expr().label("avg_dur"),
            )
            .where(
                Run.created_at >= seven_days_ago,
                Run.completed_at.isnot(None),
                Run.started_at.isnot(None),
            )
            .group_by("day")
            .order_by("day")
            .limit(1000)
        )
        dur_q = _apply_tenant_filter(dur_q, tenant_id, Run.tenant_id)
        dur_rows = (await session.execute(dur_q)).all()

    # Build day-keyed maps
    db_stats: dict[str, dict] = {}
    for row in rows:
        day_str = row.day.strftime("%Y-%m-%d") if hasattr(row.day, "strftime") else str(row.day)
        db_stats[day_str] = {
            "total": int(row.total),
            "completed": int(row.completed),
            "finished": int(row.finished),
            "cost": float(row.cost),
        }

    db_dur: dict[str, float] = {}
    for row in dur_rows:
        day_str = row.day.strftime("%Y-%m-%d") if hasattr(row.day, "strftime") else str(row.day)
        db_dur[day_str] = float(row.avg_dur or 0.0)

    # Build zero-filled 7-day arrays
    days: list[str] = []
    for i in range(6, -1, -1):
        days.append((now - timedelta(days=i)).strftime("%Y-%m-%d"))

    runs_vals: list[float] = []
    rate_vals: list[float] = []
    cost_vals: list[float] = []
    dur_vals: list[float] = []

    for day in days:
        s = db_stats.get(day, {"total": 0, "completed": 0, "finished": 0, "cost": 0.0})
        runs_vals.append(float(s["total"]))
        rate_vals.append(
            (s["completed"] / s["finished"]) if s["finished"] else 0.0
        )
        cost_vals.append(round(s["cost"], 4))
        dur_vals.append(round(db_dur.get(day, 0.0), 1))

    def _trend(vals: list[float]) -> float:
        if len(vals) < 2:
            return 0.0
        yesterday = vals[-2]
        today_val = vals[-1]
        if yesterday == 0:
            return 0.0
        return round(((today_val - yesterday) / yesterday) * 100, 1)

    return ApiResponse(
        data={
            "runs": {"values": runs_vals, "trend_percent": _trend(runs_vals)},
            "rate": {"values": rate_vals, "trend_percent": _trend(rate_vals)},
            "cost": {"values": cost_vals, "trend_percent": _trend(cost_vals)},
            "duration": {"values": dur_vals, "trend_percent": _trend(dur_vals)},
        }
    )


@router.get("/stats/heatmap")
async def get_heatmap(request: Request, response: Response) -> ApiResponse:
    """Return daily run counts for the last 52 weeks (364 days)."""
    response.headers["Cache-Control"] = "public, max-age=60"
    tenant_id = get_tenant_id(request)
    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=363)

    async with async_session() as session:
        daily_q = (
            select(
                _trunc_day(Run.created_at).label("day"),
                func.count(Run.id).label("count"),
            )
            .where(Run.created_at >= start_date)
            .group_by("day")
            .order_by("day")
            .limit(1000)
        )
        daily_q = _apply_tenant_filter(daily_q, tenant_id, Run.tenant_id)
        rows = (await session.execute(daily_q)).all()

    db_counts: dict[str, int] = {}
    for row in rows:
        day_str = row.day.strftime("%Y-%m-%d") if hasattr(row.day, "strftime") else str(row.day)
        db_counts[day_str] = int(row.count)

    # Zero-fill all 364 days (52 weeks)
    cells = []
    for i in range(363, -1, -1):
        d = now - timedelta(days=i)
        day_str = d.strftime("%Y-%m-%d")
        cells.append({
            "date": day_str,
            "count": db_counts.get(day_str, 0),
            "day_of_week": d.weekday(),  # 0=Monday ... 6=Sunday
        })

    return ApiResponse(data=cells)


@router.get("/stats/anomalies")
async def get_anomalies(request: Request, response: Response) -> ApiResponse:
    """Detect anomalies in recent workflow runs (cost spikes, duration spikes, error streaks)."""
    import math

    response.headers["Cache-Control"] = "public, max-age=60"
    tenant_id = get_tenant_id(request)

    async with async_session() as session:
        q = (
            select(Run)
            .order_by(Run.created_at.desc())
            .limit(100)
        )
        q = _apply_tenant_filter(q, tenant_id, Run.tenant_id)
        runs = (await session.execute(q)).scalars().all()

    anomalies: list[dict] = []

    # Group by workflow for per-workflow stats
    workflow_runs: dict[str, list[Run]] = {}
    for run in runs:
        workflow_runs.setdefault(run.workflow_name, []).append(run)

    for wf_name, wf_runs in workflow_runs.items():
        costs = [r.total_cost_usd for r in wf_runs if r.total_cost_usd > 0]
        durations: list[float] = []
        for r in wf_runs:
            if r.started_at and r.completed_at:
                dur = (r.completed_at - r.started_at).total_seconds()
                if dur > 0:
                    durations.append(dur)

        # Cost anomaly: z-score > 2.5 => spike
        if len(costs) >= 3:
            avg_cost = sum(costs) / len(costs)
            variance = sum((c - avg_cost) ** 2 for c in costs) / len(costs)
            std_cost = math.sqrt(variance) if variance > 0 else 0.0
            for run in wf_runs:
                if run.total_cost_usd <= 0 or std_cost == 0:
                    continue
                z = (run.total_cost_usd - avg_cost) / std_cost
                if z >= 2.5:
                    severity = "critical" if z >= 3.5 else "warning"
                    anomalies.append({
                        "type": "cost_spike",
                        "severity": severity,
                        "workflow": wf_name,
                        "message": (
                            f"Cost spike in '{wf_name}': "
                            f"${run.total_cost_usd:.4f} vs avg ${avg_cost:.4f} "
                            f"(z={z:.1f})"
                        ),
                        "run_id": str(run.id),
                        "value": round(run.total_cost_usd, 4),
                        "threshold": round(avg_cost + 2.5 * std_cost, 4),
                    })

        # Duration anomaly: z-score > 2.5
        if len(durations) >= 3:
            avg_dur = sum(durations) / len(durations)
            variance = sum((d - avg_dur) ** 2 for d in durations) / len(durations)
            std_dur = math.sqrt(variance) if variance > 0 else 0.0
            for run in wf_runs:
                if not run.started_at or not run.completed_at or std_dur == 0:
                    continue
                dur = (run.completed_at - run.started_at).total_seconds()
                if dur <= 0:
                    continue
                z = (dur - avg_dur) / std_dur
                if z >= 2.5:
                    severity = "critical" if z >= 3.5 else "warning"
                    anomalies.append({
                        "type": "slow_run",
                        "severity": severity,
                        "workflow": wf_name,
                        "message": (
                            f"Slow run in '{wf_name}': "
                            f"{dur:.0f}s vs avg {avg_dur:.0f}s "
                            f"(z={z:.1f})"
                        ),
                        "run_id": str(run.id),
                        "value": round(dur, 1),
                        "threshold": round(avg_dur + 2.5 * std_dur, 1),
                    })

        # Error streak: >= 3 consecutive failed runs (most recent first)
        sorted_runs = sorted(
            wf_runs,
            key=lambda r: r.created_at,
            reverse=True,
        )
        streak = 0
        streak_run_id: str | None = None
        for run in sorted_runs:
            if run.status in (RunStatus.FAILED,):
                streak += 1
                if streak_run_id is None:
                    streak_run_id = str(run.id)
            else:
                break
        if streak >= 3:
            severity = "critical" if streak >= 5 else "warning"
            anomalies.append({
                "type": "error_streak",
                "severity": severity,
                "workflow": wf_name,
                "message": (
                    f"Error streak in '{wf_name}': "
                    f"{streak} consecutive failures"
                ),
                "run_id": streak_run_id or "",
                "value": streak,
                "threshold": 3,
            })

    # Sort: critical first, then warning; limit to 20
    anomalies.sort(key=lambda a: (0 if a["severity"] == "critical" else 1))
    return ApiResponse(data=anomalies[:20])


def _map_model_to_provider(model_name: str | None) -> str | None:
    """Map a model string to its provider using PROVIDER_REGISTRY.

    Returns None if the model is not in the registry.
    """
    from sandcastle.engine.providers import PROVIDER_REGISTRY

    if not model_name:
        return None
    info = PROVIDER_REGISTRY.get(model_name)
    if info is None:
        return None
    return info.provider


def _get_provider_region(provider: str) -> str:
    """Return the region for a provider name (derived from PROVIDER_REGISTRY)."""
    from sandcastle.engine.providers import PROVIDER_REGISTRY

    for info in PROVIDER_REGISTRY.values():
        if info.provider == provider:
            return info.region
    return "us"


@router.get("/stats/provider-costs")
async def get_provider_costs(
    response: Response,
    days: int = Query(30, ge=1, le=365),
    request: Request = None,
) -> ApiResponse:
    """Cost breakdown per provider for the last N days.

    Queries RunStep for workflow execution costs grouped by model,
    and AuditEvent for advisor LLM call costs.
    """
    response.headers["Cache-Control"] = "public, max-age=60"
    from sandcastle.engine.providers import PROVIDER_REGISTRY
    from sandcastle.models.db import RunStep

    tenant_id = get_tenant_id(request) if request else None
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    async with async_session() as session:
        # --- Workflow execution costs: group by model in RunStep ---
        step_q = (
            select(
                RunStep.model,
                func.coalesce(func.sum(RunStep.cost_usd), 0.0).label("total_cost"),
                func.count(RunStep.id).label("step_count"),
            )
            .join(Run, RunStep.run_id == Run.id)
            .where(Run.created_at >= since)
            .group_by(RunStep.model)
            .limit(1000)
        )
        if settings.auth_required and tenant_id is not None:
            step_q = step_q.where(Run.tenant_id == tenant_id)
        step_rows = (await session.execute(step_q)).all()

        # --- Advisor costs: from AuditEvent where event_type = advisor.llm_call ---
        advisor_q = (
            select(
                AuditEvent.payload,
            )
            .where(
                AuditEvent.event_type == "advisor.llm_call",
                AuditEvent.created_at >= since,
            )
            .limit(1000)
        )
        advisor_rows = (await session.execute(advisor_q)).scalars().all()

    # Build provider cost aggregation from RunStep data
    provider_map: dict[str, dict] = {}
    for row in step_rows:
        model = row.model or "unknown"
        info = PROVIDER_REGISTRY.get(model)
        provider = info.provider if info else "unknown"
        region = info.region if info else "us"

        key = f"{provider}:{model}"
        if key not in provider_map:
            provider_map[key] = {
                "provider": provider,
                "model": model,
                "region": region,
                "total_cost_usd": 0.0,
                "run_count": 0,
            }
        provider_map[key]["total_cost_usd"] += float(row.total_cost)
        provider_map[key]["run_count"] += int(row.step_count)

    total_workflow_cost = sum(v["total_cost_usd"] for v in provider_map.values())

    by_provider = []
    for entry in sorted(provider_map.values(), key=lambda x: -x["total_cost_usd"]):
        pct = (entry["total_cost_usd"] / total_workflow_cost * 100) if total_workflow_cost > 0 else 0.0
        avg = (entry["total_cost_usd"] / entry["run_count"]) if entry["run_count"] > 0 else 0.0
        by_provider.append({
            "provider": entry["provider"],
            "model": entry["model"],
            "region": entry["region"],
            "total_cost_usd": round(entry["total_cost_usd"], 4),
            "run_count": entry["run_count"],
            "avg_cost_per_run": round(avg, 6),
            "percentage": round(pct, 1),
        })

    # Aggregate advisor costs by purpose
    advisor_by_purpose: dict[str, dict] = {}
    advisor_total = 0.0
    for payload in advisor_rows:
        if not payload:
            continue
        purpose = payload.get("purpose", "unknown")
        cost = float(payload.get("cost_estimate_usd", 0.0))
        if purpose not in advisor_by_purpose:
            advisor_by_purpose[purpose] = {"purpose": purpose, "cost_usd": 0.0, "calls": 0}
        advisor_by_purpose[purpose]["cost_usd"] += cost
        advisor_by_purpose[purpose]["calls"] += 1
        advisor_total += cost

    advisor_costs = {
        "total_usd": round(advisor_total, 6),
        "by_purpose": [
            {
                "purpose": v["purpose"],
                "cost_usd": round(v["cost_usd"], 6),
                "calls": v["calls"],
            }
            for v in sorted(advisor_by_purpose.values(), key=lambda x: -x["cost_usd"])
        ],
    }

    return ApiResponse(data={
        "period_days": days,
        "total_cost_usd": round(total_workflow_cost, 4),
        "by_provider": by_provider,
        "advisor_costs": advisor_costs,
    })


@router.get("/stats/provider-savings")
async def get_provider_savings(
    response: Response,
    days: int = Query(30, ge=1, le=365),
    request: Request = None,
) -> ApiResponse:
    """Calculate potential savings if workflows used different providers.

    Compares current model costs against alternative provider pricing.
    """
    response.headers["Cache-Control"] = "public, max-age=60"
    from sandcastle.engine.providers import PROVIDER_REGISTRY
    from sandcastle.models.db import RunStep

    tenant_id = get_tenant_id(request) if request else None
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    async with async_session() as session:
        step_q = (
            select(
                RunStep.model,
                func.coalesce(func.sum(RunStep.cost_usd), 0.0).label("total_cost"),
                func.count(RunStep.id).label("step_count"),
            )
            .join(Run, RunStep.run_id == Run.id)
            .where(Run.created_at >= since)
            .group_by(RunStep.model)
            .limit(1000)
        )
        if settings.auth_required and tenant_id is not None:
            step_q = step_q.where(Run.tenant_id == tenant_id)
        step_rows = (await session.execute(step_q)).all()

    # Total current cost
    current_total = sum(float(r.total_cost) for r in step_rows)

    # Token volume per step: reverse-engineer approximate tokens from cost
    # Store (input_tokens_approx, output_tokens_approx, step_count) per model
    model_volumes: dict[str, dict] = {}
    for row in step_rows:
        model = row.model or "unknown"
        info = PROVIDER_REGISTRY.get(model)
        if info is None:
            continue
        cost = float(row.total_cost)
        count = int(row.step_count)
        # Estimate total tokens (input+output) from cost and pricing
        # Use 2:1 input:output ratio as approximation
        total_price_per_m = (info.input_price_per_m * 2 + info.output_price_per_m) / 3
        if total_price_per_m > 0:
            tokens_m = cost / total_price_per_m  # millions of tokens
        else:
            tokens_m = 0.0
        model_volumes[model] = {
            "info": info,
            "tokens_m": tokens_m,
            "cost": cost,
            "count": count,
        }

    # Build alternatives: for each provider pick the cheapest model (lowest
    # blended price per token) so savings estimates use the best-case option,
    # not the first model encountered in dict iteration order.
    provider_savings: dict[str, dict] = {}
    for alt_model, alt_info in PROVIDER_REGISTRY.items():
        alt_provider = alt_info.provider
        alt_price = (alt_info.input_price_per_m * 2 + alt_info.output_price_per_m) / 3

        # Calculate what current token volume would cost with this model
        projected = 0.0
        for vol in model_volumes.values():
            orig_info = vol["info"]
            if orig_info.provider == alt_provider:
                continue
            projected += vol["tokens_m"] * alt_price

        # Add same-provider costs unchanged
        for vol in model_volumes.values():
            if vol["info"].provider == alt_provider:
                projected += vol["cost"]

        savings_usd = current_total - projected
        savings_pct = (savings_usd / current_total * 100) if current_total > 0 else 0.0

        # Keep this model only if it offers better savings than a previously
        # evaluated model from the same provider (pick cheapest per provider).
        existing = provider_savings.get(alt_provider)
        if existing and existing["savings_percent"] >= round(max(0.0, savings_pct), 1):
            continue

        # Build note
        if alt_provider == "ollama":
            note = "Switch to local Ollama for zero cloud costs (hardware required)"
        elif alt_info.region == "eu":
            note = (
                f"Switch to {alt_provider.capitalize()} for "
                f"{savings_pct:.0f}% savings with EU data residency"
            )
        else:
            note = (
                f"Switch to {alt_provider.capitalize()} for "
                f"{savings_pct:.0f}% cost savings"
            )

        provider_savings[alt_provider] = {
            "provider": alt_provider,
            "model": alt_model,
            "region": alt_info.region,
            "projected_cost_usd": round(max(0.0, projected), 4),
            "savings_usd": round(max(0.0, savings_usd), 4),
            "savings_percent": round(max(0.0, savings_pct), 1),
            "note": note,
        }

    # Sort by savings_percent descending, exclude same-provider entries with 0 savings
    alternatives = [
        v for v in sorted(provider_savings.values(), key=lambda x: -x["savings_percent"])
        if v["savings_percent"] > 0 or v["provider"] == "ollama"
    ]

    return ApiResponse(data={
        "current_total_usd": round(current_total, 4),
        "alternatives": alternatives,
    })


@router.get("/stats/provider-recommendation")
async def get_provider_recommendation(response: Response, request: Request = None) -> ApiResponse:
    """Proactive provider recommendations based on usage patterns (last 30 days).

    Analyzes costs, quality scores, and provider usage to surface actionable
    recommendations: cost savings, quality upgrades, data residency compliance.
    """
    response.headers["Cache-Control"] = "public, max-age=60"
    from sandcastle.engine.providers import PROVIDER_REGISTRY
    from sandcastle.models.db import RunStep

    tenant_id = get_tenant_id(request) if request else None
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=30)

    async with async_session() as session:
        # Step costs and models
        step_q = (
            select(
                RunStep.model,
                func.coalesce(func.sum(RunStep.cost_usd), 0.0).label("total_cost"),
                func.count(RunStep.id).label("step_count"),
            )
            .join(Run, RunStep.run_id == Run.id)
            .where(Run.created_at >= since)
            .group_by(RunStep.model)
            .limit(1000)
        )
        if settings.auth_required and tenant_id is not None:
            step_q = step_q.where(Run.tenant_id == tenant_id)
        step_rows = (await session.execute(step_q)).all()

        # AutoPilot quality scores (variance check)
        quality_q = (
            select(AutoPilotSample.quality_score)
            .where(
                AutoPilotSample.quality_score.isnot(None),
                AutoPilotSample.created_at >= since,
            )
            .limit(1000)
        )
        quality_rows = (await session.execute(quality_q)).scalars().all()

        # Advisor audit events for provider/purpose breakdown
        advisor_q = (
            select(AuditEvent.payload)
            .where(
                AuditEvent.event_type == "advisor.llm_call",
                AuditEvent.created_at >= since,
            )
            .limit(1000)
        )
        advisor_rows = (await session.execute(advisor_q)).scalars().all()

    recommendations: list[dict] = []

    # --- Cost saving recommendation ---
    total_cost = sum(float(r.total_cost) for r in step_rows)

    # Find dominant (most expensive) provider
    provider_costs: dict[str, float] = {}
    for row in step_rows:
        model = row.model or "unknown"
        info = PROVIDER_REGISTRY.get(model)
        if info is None:
            continue
        provider_costs[info.provider] = provider_costs.get(info.provider, 0.0) + float(row.total_cost)

    if total_cost > 0 and provider_costs:
        dominant_provider = max(provider_costs, key=lambda p: provider_costs[p])
        dominant_cost = provider_costs[dominant_provider]
        dominant_pct = dominant_cost / total_cost * 100

        # Look for a significantly cheaper alternative
        best_savings_provider: str | None = None
        best_savings_usd = 0.0
        best_savings_pct = 0.0
        best_alt_info = None

        # Get token volume for dominant provider
        dominant_tokens_m = 0.0
        for row in step_rows:
            model = row.model or "unknown"
            info = PROVIDER_REGISTRY.get(model)
            if info and info.provider == dominant_provider:
                price_per_m = (info.input_price_per_m * 2 + info.output_price_per_m) / 3
                if price_per_m > 0:
                    dominant_tokens_m += float(row.total_cost) / price_per_m

        for alt_model, alt_info in PROVIDER_REGISTRY.items():
            if alt_info.provider == dominant_provider:
                continue
            alt_price = (alt_info.input_price_per_m * 2 + alt_info.output_price_per_m) / 3
            projected = dominant_tokens_m * alt_price
            sav_usd = dominant_cost - projected
            sav_pct = (sav_usd / dominant_cost * 100) if dominant_cost > 0 else 0.0
            if sav_pct > best_savings_pct:
                best_savings_pct = sav_pct
                best_savings_usd = sav_usd
                best_savings_provider = alt_info.provider
                best_alt_info = alt_info

        if best_savings_provider and best_savings_pct > 20 and dominant_pct > 50:
            severity = "high" if best_savings_pct > 40 else "medium"
            recommendations.append({
                "type": "cost_saving",
                "severity": severity,
                "title": (
                    f"Switch to {best_savings_provider.capitalize()} "
                    f"for {best_savings_pct:.0f}% savings"
                ),
                "description": (
                    f"Your workflows spent ${dominant_cost:.2f} on "
                    f"{dominant_provider.capitalize()} last month. "
                    f"{best_savings_provider.capitalize()} would cost "
                    f"approximately ${max(0, dominant_cost - best_savings_usd):.2f} "
                    f"for equivalent workloads."
                    + (
                        " EU data residency included."
                        if best_alt_info and best_alt_info.region == "eu"
                        else ""
                    )
                ),
                "action": f"Switch advisor to {best_savings_provider.capitalize()}",
                "provider": best_savings_provider,
                "estimated_savings_usd": round(max(0.0, best_savings_usd), 2),
                "confidence": 0.85 if best_savings_pct > 50 else 0.70,
            })

    # --- Quality variance recommendation ---
    quality_scores = [float(q) for q in quality_rows if q is not None]
    if len(quality_scores) >= 5:
        import math as _math
        avg_q = sum(quality_scores) / len(quality_scores)
        variance_q = sum((s - avg_q) ** 2 for s in quality_scores) / len(quality_scores)
        stddev_q = _math.sqrt(variance_q)
        if stddev_q > 0.2:
            recommendations.append({
                "type": "quality_upgrade",
                "severity": "medium",
                "title": "Upgrade judge model for better eval accuracy",
                "description": (
                    f"Your AutoPilot quality scores have high variance "
                    f"(stddev {stddev_q:.2f}). Using a higher-tier model for "
                    f"judging could reduce variance and improve experiment reliability."
                ),
                "action": "Set advisor_quality_mode=always_best for judge purpose",
                "provider": "anthropic",
                "estimated_savings_usd": 0.0,
                "confidence": 0.65,
            })

    # --- Data residency recommendation ---
    # Check if advisor is using a non-EU provider without data_residency set
    non_eu_calls = 0
    total_advisor_calls = 0
    for payload in advisor_rows:
        if not payload:
            continue
        total_advisor_calls += 1
        region = payload.get("region", "us")
        if region != "eu":
            non_eu_calls += 1

    if total_advisor_calls > 0:
        non_eu_pct = non_eu_calls / total_advisor_calls
        residency = getattr(settings, "data_residency", "") or ""
        if non_eu_pct > 0.5 and not residency:
            recommendations.append({
                "type": "data_residency",
                "severity": "info",
                "title": "Consider enabling EU Data Residency",
                "description": (
                    f"{non_eu_pct * 100:.0f}% of your advisor calls are processed "
                    f"outside the EU. Enabling data_residency=eu ensures all AI "
                    f"processing stays within EU borders (GDPR compliance)."
                ),
                "action": "Enable EU mode in Settings -> Data Residency",
                "provider": "mistral",
                "estimated_savings_usd": 0.0,
                "confidence": 0.70,
            })

    # --- Unused provider recommendation ---
    configured_providers: set[str] = set()
    for info in PROVIDER_REGISTRY.values():
        from sandcastle.engine.providers import get_api_key
        if get_api_key(info):
            configured_providers.add(info.provider)

    used_providers: set[str] = set()
    for row in step_rows:
        model = row.model or "unknown"
        info = PROVIDER_REGISTRY.get(model)
        if info:
            used_providers.add(info.provider)

    unused_configured = configured_providers - used_providers - {"ollama"}
    for provider_name in sorted(unused_configured)[:1]:
        recommendations.append({
            "type": "unused_provider",
            "severity": "info",
            "title": f"Try {provider_name.capitalize()} - you have it configured",
            "description": (
                f"You have {provider_name.capitalize()} configured but haven't used "
                f"it in workflows. It may offer cost or quality advantages for "
                f"certain workflow types."
            ),
            "action": f"Set default_model to a {provider_name.capitalize()} model in a workflow",
            "provider": provider_name,
            "estimated_savings_usd": 0.0,
            "confidence": 0.50,
        })

    return ApiResponse(data={"recommendations": recommendations})


@router.get("/advisor/recommendations")
async def get_advisor_recommendations(request: Request = None) -> ApiResponse:
    """Cost recommendation engine - per-workflow savings analysis.

    Analyzes last 30 days of run data per workflow, calculates what each
    workflow would cost on every provider, and returns top 3 actionable
    recommendations sorted by potential savings.
    """
    from sandcastle.engine.providers import PROVIDER_REGISTRY
    from sandcastle.models.db import RunStep

    tenant_id = get_tenant_id(request) if request else None
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=30)

    async with async_session() as session:
        # Per-workflow cost and token estimation grouped by workflow + model
        wf_q = (
            select(
                Run.workflow_name,
                RunStep.model,
                func.coalesce(func.sum(RunStep.cost_usd), 0.0).label("total_cost"),
                func.count(RunStep.id).label("step_count"),
            )
            .join(Run, RunStep.run_id == Run.id)
            .where(Run.created_at >= since)
            .group_by(Run.workflow_name, RunStep.model)
            .limit(5000)
        )
        if settings.auth_required and tenant_id is not None:
            wf_q = wf_q.where(Run.tenant_id == tenant_id)
        wf_rows = (await session.execute(wf_q)).all()

    if not wf_rows:
        return ApiResponse(data={
            "recommendations": [],
            "total_potential_savings": 0.0,
        })

    # Build per-workflow cost profiles: {workflow -> {model -> (cost, steps)}}
    wf_profiles: dict[str, dict[str, tuple[float, int]]] = {}
    for row in wf_rows:
        wf_name = row.workflow_name or "unknown"
        model = row.model or "unknown"
        cost = float(row.total_cost)
        steps = int(row.step_count)
        wf_profiles.setdefault(wf_name, {})[model] = (cost, steps)

    residency = getattr(settings, "data_residency", "") or ""

    # For each workflow, estimate cost on each alternative provider
    workflow_recommendations: list[dict] = []

    for wf_name, model_costs in wf_profiles.items():
        current_cost_30d = sum(c for c, _ in model_costs.values())
        if current_cost_30d <= 0.01:
            continue

        # Determine current dominant provider
        provider_spend: dict[str, float] = {}
        for model, (cost, _) in model_costs.items():
            info = PROVIDER_REGISTRY.get(model)
            if info:
                provider_spend[info.provider] = provider_spend.get(info.provider, 0.0) + cost
        if not provider_spend:
            continue
        current_provider = max(provider_spend, key=lambda p: provider_spend[p])

        # Estimate token volume from cost (use weighted average price)
        total_tokens_m = 0.0
        for model, (cost, _) in model_costs.items():
            info = PROVIDER_REGISTRY.get(model)
            if info:
                avg_price = (info.input_price_per_m * 2 + info.output_price_per_m) / 3
                if avg_price > 0:
                    total_tokens_m += cost / avg_price

        # Calculate projected cost on each alternative provider (best model per provider)
        alt_projections: dict[str, tuple[float, str, bool]] = {}
        for alt_model, alt_info in PROVIDER_REGISTRY.items():
            if alt_info.provider == current_provider:
                continue
            if alt_info.provider == "ollama":
                continue
            # Respect data residency constraint
            if residency and alt_info.region != residency:
                continue
            alt_avg_price = (alt_info.input_price_per_m * 2 + alt_info.output_price_per_m) / 3
            projected = total_tokens_m * alt_avg_price
            is_eu = alt_info.region == "eu"
            # Keep cheapest model per provider
            if alt_info.provider not in alt_projections or projected < alt_projections[alt_info.provider][0]:
                alt_projections[alt_info.provider] = (projected, alt_model, is_eu)

        # Find best savings
        best_provider = None
        best_savings = 0.0
        best_projected = current_cost_30d
        best_eu = False
        best_model = ""
        for prov, (projected, alt_model, is_eu) in alt_projections.items():
            savings = current_cost_30d - projected
            if savings > best_savings:
                best_savings = savings
                best_projected = projected
                best_provider = prov
                best_eu = is_eu
                best_model = alt_model

        if best_provider and best_savings > 0.50:
            savings_pct = (best_savings / current_cost_30d * 100) if current_cost_30d > 0 else 0
            # Build human-readable reason
            reason_parts = [
                f"This workflow uses {current_provider.capitalize()} at ${current_cost_30d:.2f}/mo"
            ]
            reason_parts.append(
                f" - {best_provider.capitalize()} ({best_model}) would cost "
                f"${best_projected:.2f} ({savings_pct:.0f}% less)"
            )
            if best_eu:
                reason_parts.append(" with EU data residency included")

            workflow_recommendations.append({
                "workflow": wf_name,
                "current_provider": current_provider,
                "current_cost_30d": round(current_cost_30d, 2),
                "suggested_provider": best_provider,
                "suggested_model": best_model,
                "estimated_cost_30d": round(max(0.0, best_projected), 2),
                "savings_monthly": round(max(0.0, best_savings), 2),
                "savings_percent": round(savings_pct, 1),
                "eu_compliant": best_eu,
                "reason": "".join(reason_parts),
            })

    # Sort by savings descending, take top 3
    workflow_recommendations.sort(key=lambda r: r["savings_monthly"], reverse=True)
    top_recs = workflow_recommendations[:3]
    total_potential_savings = round(sum(r["savings_monthly"] for r in top_recs), 2)

    return ApiResponse(data={
        "recommendations": top_recs,
        "total_potential_savings": total_potential_savings,
    })


@router.get("/stats/failover-events")
async def get_failover_events(response: Response, request: Request = None) -> ApiResponse:
    """Return recent failover events from the last 7 days.

    Queries AuditEvent for advisor.llm_call entries that have a non-null
    failover_from field, indicating the advisor switched providers.
    """
    response.headers["Cache-Control"] = "public, max-age=60"
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=7)

    async with async_session() as session:
        q = (
            select(AuditEvent.payload, AuditEvent.created_at)
            .where(
                AuditEvent.event_type == "advisor.llm_call",
                AuditEvent.created_at >= since,
            )
            .order_by(AuditEvent.created_at.desc())
            .limit(1000)
        )
        rows = (await session.execute(q)).all()

    events: list[dict] = []
    total_cost_delta = 0.0

    for row in rows:
        payload = row[0]
        created_at = row[1]
        if not payload or not payload.get("failover_from"):
            continue
        # Estimate cost delta: failover calls cost more than primary would have
        cost = float(payload.get("cost_estimate_usd", 0.0))
        # Rough delta: failover cost minus estimated primary cost (use 80% as heuristic)
        cost_delta = round(cost * 0.2, 6)
        total_cost_delta += cost_delta

        events.append({
            "timestamp": created_at.isoformat() if created_at else None,
            "original_provider": payload.get("failover_from"),
            "failover_provider": payload.get("provider"),
            "reason": payload.get("failover_reason", "unknown"),
            "cost_delta": cost_delta,
        })

    return ApiResponse(data={
        "events": events[:50],  # Return at most 50 recent events
        "total_failovers_7d": len(events),
        "total_cost_delta_7d": round(total_cost_delta, 4),
    })


@router.post("/runs/estimate")
async def estimate_run_cost(request: RunEstimateRequest) -> ApiResponse:
    """Estimate cost of a workflow run before execution."""
    from sandcastle.engine.dag import parse_yaml_string
    from sandcastle.engine.providers import PROVIDER_REGISTRY

    yaml_content = request.yaml_content

    from sandcastle.engine.dag import validate

    try:
        wf = parse_yaml_string(yaml_content)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=ApiResponse(
            error=ErrorResponse(code="INVALID_YAML", message=str(exc)),
        ).model_dump())

    validation_errors = validate(wf)
    is_valid = len(validation_errors) == 0

    # Average tokens per step type (empirical estimates)
    AVG_TOKENS = {
        "standard": (2000, 1500),
        "llm": (1000, 800),
        "classify": (500, 200),
        "gate": (500, 200),
        "delegate": (2000, 1500),
        "browser": (3000, 2000),
    }
    # sub_workflow parent doesn't invoke LLM itself - child workflow has its own costs
    NON_LLM = {
        "http", "code", "condition", "loop", "race", "sensor",
        "transform", "notify", "composio", "sub_workflow",
        "openclaw", "parse",
    }
    # classify and gate issue a single LLM call, not max_turns
    SINGLE_CALL_TYPES = {"classify", "gate"}

    step_estimates = []
    total = 0.0

    for step in wf.steps:
        if step.type in NON_LLM or step.type == "approval":
            step_estimates.append({
                "step_id": step.id,
                "type": step.type,
                "model": None,
                "estimated_cost_usd": 0.0,
                "note": "No LLM cost" if step.type != "sub_workflow" else "Cost in child workflow",
            })
            continue

        # Resolve effective model
        model_key = step.model or wf.default_model or "sonnet"
        # classify uses classify_config model if set
        if step.type == "classify" and step.classify_config:
            classify_model = getattr(step.classify_config, "model", None)
            if classify_model:
                model_key = classify_model
        # gate reads model from first llm_eval strategy
        if step.type == "gate" and step.gate_config:
            for strat in (step.gate_config.strategies or []):
                if strat.get("type") == "llm_eval":
                    strat_model = strat.get("config", {}).get("model")
                    if strat_model:
                        model_key = strat_model
                    break

        model_info = PROVIDER_REGISTRY.get(model_key)
        if not model_info:
            model_info = PROVIDER_REGISTRY.get("sonnet")
            unknown_note = f" (unknown model '{model_key}', using sonnet pricing)"
        else:
            unknown_note = ""

        avg_in, avg_out = AVG_TOKENS.get(step.type, (1500, 1000))

        # classify and gate issue a single LLM call, not max_turns
        if step.type in SINGLE_CALL_TYPES:
            turns = 1
        else:
            turns = min(step.max_turns, 5)

        est_in = avg_in * turns
        est_out = avg_out * turns

        cost = (est_in * model_info.input_price_per_m + est_out * model_info.output_price_per_m) / 1_000_000

        # If parallel_over, multiply by estimated batch size (default 10)
        if step.parallel_over:
            cost *= 10
            note = "x10 (parallel_over estimate)"
        else:
            note = f"~{est_in} in + ~{est_out} out tokens{unknown_note}"

        step_estimates.append({
            "step_id": step.id,
            "type": step.type,
            "model": model_key,
            "estimated_cost_usd": round(cost, 6),
            "note": note,
        })
        total += cost

    return ApiResponse(data={
        "workflow_name": wf.name,
        "valid": is_valid,
        "total_estimated_cost_usd": round(total, 4),
        "steps": step_estimates,
        "validation_errors": validation_errors,
        "disclaimer": "Estimates based on average token usage. Actual costs may vary."
        + ("" if is_valid else " Workflow has validation errors - estimate may be unreliable."),
    })


# --- Generate ---


@router.post("/generate")
async def generate_workflow(req: Request, request: WorkflowGenerateRequest) -> ApiResponse:
    """Generate a workflow YAML from a natural language description."""
    await execution_limiter.check(req)
    from sandcastle.engine.generator import generate_workflow as _generate

    if not settings.anthropic_api_key and not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="MISSING_API_KEY",
                    message="ANTHROPIC_API_KEY is required for workflow generation",
                )
            ).model_dump(),
        )

    try:
        result = await _generate(
            request.description,
            refine_from=request.refine_from,
            refine_instruction=request.refine_instruction,
            tenant_id=get_tenant_id(req),
        )
    except httpx.HTTPStatusError as exc:
        logger.error(f"Anthropic API error: {exc}")
        raise HTTPException(
            status_code=502,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="UPSTREAM_ERROR",
                    message="Upstream provider returned an error",
                )
            ).model_dump(),
        )
    except Exception as exc:
        logger.error("Generation failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="GENERATION_FAILED",
                    message="Workflow generation failed",
                )
            ).model_dump(),
        )

    return ApiResponse(
        data=GenerateWorkflowResponse(
            yaml_content=result.yaml_content,
            name=result.name,
            description=result.description,
            steps_count=result.steps_count,
            validation_errors=result.validation_errors,
            input_schema=result.input_schema,
            similar_template=getattr(result, "similar_template", None),
        )
    )


@router.post("/generate/chat")
async def generate_chat(req: Request, request: GenerateChatRequest) -> ApiResponse:
    """Chat-based workflow generation with multi-turn conversation."""
    await execution_limiter.check(req)
    from sandcastle.engine.generator import generate_chat as _generate_chat

    if not settings.anthropic_api_key and not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="MISSING_API_KEY",
                    message="ANTHROPIC_API_KEY is required for workflow generation",
                )
            ).model_dump(),
        )

    try:
        msgs = [{"role": m.role, "content": m.content} for m in request.messages]
        result = await _generate_chat(
            msgs,
            existing_yaml=request.existing_yaml,
        )
    except httpx.HTTPStatusError as exc:
        logger.error("Anthropic API error: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="UPSTREAM_ERROR",
                    message="Upstream provider returned an error",
                )
            ).model_dump(),
        )
    except Exception as exc:
        logger.error("Chat generation failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="GENERATION_FAILED",
                    message="Chat generation failed",
                )
            ).model_dump(),
        )

    return ApiResponse(data=result)


@router.post("/advisor/explain")
async def advisor_explain_error(req: Request, request: ExplainErrorRequest) -> ApiResponse:
    """Explain a step failure using AI and suggest a fix."""
    await execution_limiter.check(req)
    from sandcastle.engine.generator import explain_error

    try:
        result = await explain_error(
            step_id=request.step_id,
            step_type=request.step_type,
            error=request.error,
            prompt=request.prompt,
            model=request.model,
            workflow_name=request.workflow_name,
        )
    except httpx.HTTPStatusError as exc:
        logger.error("Advisor explain upstream error: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="UPSTREAM_ERROR",
                    message="Upstream provider returned an error",
                )
            ).model_dump(),
        )
    except Exception as exc:
        logger.error("Advisor explain failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="EXPLAIN_FAILED",
                    message="Error explanation failed",
                )
            ).model_dump(),
        )
    return ApiResponse(data=result)


@router.get("/advisor/status")
async def advisor_status() -> ApiResponse:
    """Return current advisor provider config and availability of each provider."""
    anthropic_configured = bool(settings.anthropic_api_key)
    mistral_configured = bool(settings.mistral_api_key)
    openai_configured = bool(settings.openai_api_key)

    # Detect Ollama by probing localhost
    ollama_running = False
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            resp = await client.get("http://localhost:11434/api/tags")
            ollama_running = resp.status_code == 200
    except Exception:
        pass

    available: list[ProviderStatusEntry] = [
        ProviderStatusEntry(
            id="anthropic",
            name="Anthropic (Claude)",
            region="us",
            configured=anthropic_configured,
            status="ok" if anthropic_configured else "unconfigured",
        ),
        ProviderStatusEntry(
            id="mistral",
            name="Mistral",
            region="eu",
            configured=mistral_configured,
            status="ok" if mistral_configured else "unconfigured",
        ),
        ProviderStatusEntry(
            id="openai",
            name="OpenAI",
            region="us",
            configured=openai_configured,
            status="ok" if openai_configured else "unconfigured",
        ),
        ProviderStatusEntry(
            id="ollama",
            name="Ollama (Local)",
            region="local",
            configured=ollama_running,
            status="running" if ollama_running else "not_detected",
        ),
    ]

    # Determine current provider from configured keys
    if mistral_configured:
        current_provider = "mistral"
        current_model = "mistral-large-latest"
    elif openai_configured:
        current_provider = "openai"
        current_model = "gpt-4o"
    elif ollama_running:
        current_provider = "ollama"
        current_model = "llama3.2"
    else:
        current_provider = "anthropic"
        current_model = "claude-sonnet-4-6"

    data_residency: str | None = None
    if current_provider == "mistral":
        data_residency = "eu"
    elif current_provider == "ollama":
        data_residency = "local"

    return ApiResponse(
        data=AdvisorStatusResponse(
            current_provider=current_provider,
            current_model=current_model,
            data_residency=data_residency,
            available_providers=available,
        )
    )


@router.post("/advisor/configure")
async def advisor_configure(request: AdvisorConfigureRequest) -> ApiResponse:
    """Configure which provider powers the advisor (informational - returns ack)."""
    # Item 6: When EU mode is enabled, verify at least one EU/local provider is configured
    if request.data_residency == "eu":
        mistral_configured = bool(settings.mistral_api_key)
        # Detect Ollama
        ollama_running = False
        try:
            async with httpx.AsyncClient(timeout=1.0) as _client:
                _r = await _client.get("http://localhost:11434/api/tags")
                ollama_running = _r.status_code == 200
        except Exception:
            pass
        if not mistral_configured and not ollama_running:
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="EU_PROVIDER_REQUIRED",
                        message=(
                            "EU Data Residency requires at least one EU provider (Mistral) "
                            "or local provider (Ollama) to be configured."
                        ),
                    )
                ).model_dump(),
            )
    return ApiResponse(
        data=AdvisorConfigureResponse(
            provider=request.provider,
            model=request.model,
            data_residency=request.data_residency,
            status="configured",
        )
    )


@router.post("/advisor/test-connection")
async def advisor_test_connection(req: Request) -> ApiResponse:
    """Test connectivity to a specific advisor provider."""
    import time as _time

    try:
        body = await req.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_JSON", message="Invalid JSON body")
            ).model_dump(),
        )
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_JSON", message="Request body must be a JSON object")
            ).model_dump(),
        )
    provider = body.get("provider", "anthropic")

    from sandcastle.engine.generator import _PROVIDER_CONFIGS, _build_request_body

    cfg = _PROVIDER_CONFIGS.get(provider)
    if cfg is None:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="UNKNOWN_PROVIDER", message=f"Unknown provider: {provider}")
            ).model_dump(),
        )

    from sandcastle.engine.generator import _resolve_api_key_for_provider

    key_env = cfg.get("api_key_env", "")
    # Use the same key resolution as _call_advisor_llm so Settings-stored keys
    # are found even when the env var isn't set directly.
    api_key = _resolve_api_key_for_provider(provider) if key_env else "ollama-no-key"
    # Normalise the "no-key-required" sentinel used for Ollama
    if api_key == "no-key-required":
        api_key = "ollama-no-key"

    if not api_key and provider != "ollama":
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="NOT_CONFIGURED",
                    message=f"Provider '{provider}' is not configured (missing {key_env})",
                )
            ).model_dump(),
        )

    api_url = cfg["api_url"]
    model = cfg["model"]
    headers = cfg["headers_fn"](api_key)
    # Pass is_anthropic explicitly so the request body format matches the
    # provider being tested, not the currently configured global provider.
    is_anthropic_provider = cfg.get("api_key_env") == "ANTHROPIC_API_KEY"
    body_payload = _build_request_body(
        model,
        "You are a helpful assistant.",
        [{"role": "user", "content": "ping"}],
        max_tokens=1,
        is_anthropic=is_anthropic_provider,
    )

    t0 = _time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(api_url, json=body_payload, headers=headers)
            resp.raise_for_status()
        latency_ms = int((_time.monotonic() - t0) * 1000)
        return ApiResponse(data=AdvisorTestConnectionResponse(
            status="ok", provider=provider, latency_ms=latency_ms,
        ))
    except httpx.ConnectError as exc:
        return ApiResponse(
            data=AdvisorTestConnectionResponse(
                status="error", provider=provider, message=f"Connection refused: {exc}",
            )
        )
    except httpx.HTTPStatusError as exc:
        return ApiResponse(
            data=AdvisorTestConnectionResponse(
                status="error",
                provider=provider,
                message=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            )
        )
    except Exception as exc:
        return ApiResponse(
            data=AdvisorTestConnectionResponse(
                status="error", provider=provider, message=str(exc),
            )
        )


@router.get("/advisor/cost-estimate")
async def advisor_cost_estimate() -> ApiResponse:
    """Return cost comparison for current and alternative providers."""
    anthropic_configured = bool(settings.anthropic_api_key)
    mistral_configured = bool(settings.mistral_api_key)
    openai_configured = bool(settings.openai_api_key)

    if mistral_configured:
        current = CostEstimateEntry(provider="mistral", model="mistral-large", estimated_cost=0.008)
    elif openai_configured:
        current = CostEstimateEntry(provider="openai", model="gpt-4o", estimated_cost=0.030)
    else:
        current = CostEstimateEntry(provider="anthropic", model="claude-sonnet-4-6", estimated_cost=0.045)

    alternatives: list[CostEstimateEntry] = []
    if current.provider != "anthropic" and anthropic_configured:
        alternatives.append(CostEstimateEntry(provider="anthropic", model="claude-sonnet-4-6", estimated_cost=0.045))
    if current.provider != "mistral":
        alternatives.append(CostEstimateEntry(provider="mistral", model="mistral-large", estimated_cost=0.008))
    if current.provider != "openai" and openai_configured:
        alternatives.append(CostEstimateEntry(provider="openai", model="gpt-4o", estimated_cost=0.030))
    alternatives.append(CostEstimateEntry(provider="ollama", model="llama3.2", estimated_cost=0.000))

    return ApiResponse(
        data=AdvisorCostEstimateResponse(current=current, alternatives=alternatives)
    )


@router.get("/compliance/privacy-notice")
async def generate_privacy_notice(workflow_name: str = Query(None)) -> ApiResponse:  # type: ignore[assignment]
    """Generate a GDPR-compliant privacy notice for data processing."""
    from datetime import datetime, timezone

    anthropic_configured = bool(settings.anthropic_api_key)
    mistral_configured = bool(settings.mistral_api_key)
    openai_configured = bool(settings.openai_api_key)

    if mistral_configured:
        provider = "Mistral AI"
        data_residency = "European Union (France)"
    elif openai_configured:
        provider = "OpenAI"
        data_residency = "United States"
    elif anthropic_configured:
        provider = "Anthropic"
        data_residency = "United States"
    else:
        provider = "Local (Ollama)"
        data_residency = "Local - no data leaves your machine"

    pii_redaction = settings.privacy_enabled
    retention_days = 90
    workflow_label = workflow_name or "all workflows"

    notice = f"""## Privacy Notice - Sandcastle Data Processing

**Effective date:** {datetime.now(timezone.utc).strftime("%Y-%m-%d")}

### Data Controller
Sandcastle instance operator.

### Processing Purpose
Workflow automation for **{workflow_label}**.

### AI Provider
Data submitted to workflow steps is processed by **{provider}**.
Data residency: **{data_residency}**.

### PII Redaction
PII redaction is **{"enabled" if pii_redaction else "disabled"}**. \
{"Personal identifiers (email, phone, SSN, credit card) are automatically redacted before processing." if pii_redaction else "Enable PRIVACY_ENABLED=true to activate automatic PII redaction."}

### Data Retention
Workflow run results and audit events are retained for **{retention_days} days** before automatic deletion.

### Audit Trail
All workflow executions are recorded in a tamper-evident audit log with SHA-256 hash chaining.

### Your Rights (GDPR Art. 15-22)
You have the right to access, rectify, erase, restrict, and port your personal data.
Contact the instance operator to exercise these rights.

### Legal Basis
Processing is performed under legitimate interest (Art. 6(1)(f) GDPR) for workflow automation tasks.
"""

    return ApiResponse(
        data=PrivacyNoticeResponse(
            workflow_name=workflow_name,
            notice=notice,
            provider=provider,
            data_residency=data_residency,
            pii_redaction=pii_redaction,
            retention_days=retention_days,
            audit_trail=True,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
    )


# --- Workflows ---


@router.get("/workflows")
async def list_workflows(req: Request) -> ApiResponse:
    """List available workflow YAML files from the workflows directory."""
    caller_is_admin = is_admin(req)
    workflows_dir = Path(settings.workflows_dir)
    if not workflows_dir.exists():
        return ApiResponse(data=[])

    # Load version info from registry
    version_info: dict[str, dict] = {}
    try:
        async with async_session() as session:
            count_stmt = (
                select(
                    WorkflowVersion.workflow_name,
                    func.count(WorkflowVersion.id).label("total"),
                    func.max(
                        case(
                            (WorkflowVersion.status == WorkflowVersionStatus.PRODUCTION,
                             WorkflowVersion.version),
                            else_=None,
                        )
                    ).label("prod_version"),
                )
                .group_by(WorkflowVersion.workflow_name)
            )
            rows = (await session.execute(count_stmt)).all()
            for row in rows:
                version_info[row.workflow_name] = {
                    "prod": row.prod_version,
                    "total": row.total,
                }
    except Exception:
        pass

    items = []
    yaml_files = sorted([*workflows_dir.glob("*.yaml"), *workflows_dir.glob("*.yml")])
    for yaml_file in yaml_files:
        try:
            content = yaml_file.read_text()
            workflow = parse_yaml_string(content)
            wf_key = yaml_file.stem
            vi = version_info.get(wf_key, {})
            # Run lightweight doctor check
            d_status = None
            d_risk = None
            try:
                from sandcastle.engine.doctor import diagnose
                dr = diagnose(workflow)
                d_status = "ok" if dr.ok else ("blocked" if dr.blocking else "warning")
                d_risk = dr.risk
            except Exception:
                pass
            items.append(
                WorkflowInfoResponse(
                    name=workflow.name,
                    description=workflow.description,
                    steps_count=len(workflow.steps),
                    file_name=yaml_file.name,
                    steps=[
                        WorkflowStepInfo(
                            id=s.id,
                            depends_on=s.depends_on,
                            model=s.model,
                            # Strip prompt for non-admin callers to avoid leaking implementation
                            prompt=s.prompt if caller_is_admin else None,
                        )
                        for s in workflow.steps
                    ],
                    input_schema=workflow.input_schema,
                    version=vi.get("prod"),
                    version_status="production" if vi.get("prod") else None,
                    total_versions=vi.get("total") or None,
                    # Strip raw YAML for non-admin callers
                    yaml_content=content if caller_is_admin else None,
                    doctor_status=d_status,
                    doctor_risk=d_risk,
                )
            )
        except Exception as e:
            logger.warning(f"Could not parse workflow file {yaml_file.name}: {e}")

    return ApiResponse(data=items)


# ---------------------------------------------------------------------------
# Per-workflow run statistics
# ---------------------------------------------------------------------------

_stats_cache: dict[str, tuple[float, Any]] = {}
_STATS_CACHE_TTL = 30  # seconds — stats are not real-time critical
_STATS_CACHE_MAXSIZE = 200


def _stats_cache_get(key: str) -> Any | None:
    if key in _stats_cache:
        ts, data = _stats_cache[key]
        if time.time() - ts < _STATS_CACHE_TTL:
            return data
        del _stats_cache[key]
    return None


def _stats_cache_set(key: str, data: Any) -> None:
    if len(_stats_cache) >= _STATS_CACHE_MAXSIZE and key not in _stats_cache:
        oldest = min(_stats_cache, key=lambda k: _stats_cache[k][0])
        del _stats_cache[oldest]
    _stats_cache[key] = (time.time(), data)


def _format_relative_time(when: datetime | None) -> str | None:
    """Return e.g. '12m ago', '3h ago', '2d ago' or None."""
    if when is None:
        return None
    now = datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    delta = now - when
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 7:
        return f"{days}d ago"
    weeks = days // 7
    return f"{weeks}w ago"


async def _compute_workflow_stats(
    tenant_id: str | None,
    name_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate Run rows into per-workflow stats.

    Single GROUP BY query so the dashboard grid view stays O(1) DB calls.
    """
    completed_status = RunStatus.COMPLETED
    success_count = func.count(
        case((Run.status == completed_status, Run.id), else_=None)
    )
    total_count = func.count(Run.id)

    stmt = (
        select(
            Run.workflow_name.label("name"),
            total_count.label("total_runs"),
            success_count.label("success_count"),
            func.coalesce(func.avg(Run.total_cost_usd), 0.0).label("avg_cost"),
            func.max(Run.completed_at).label("last_completed_at"),
            func.max(Run.started_at).label("last_started_at"),
            func.max(Run.created_at).label("last_created_at"),
        )
        .group_by(Run.workflow_name)
    )
    stmt = _apply_tenant_filter(stmt, tenant_id, Run.tenant_id)
    if name_filter is not None:
        stmt = stmt.where(Run.workflow_name == name_filter)

    async with async_session() as session:
        rows = (await session.execute(stmt)).all()

    results: list[dict[str, Any]] = []
    for row in rows:
        # Fetch the most recent run's status separately (cheap second pass)
        last_status_stmt = (
            select(Run.status, Run.completed_at, Run.started_at, Run.created_at)
            .where(Run.workflow_name == row.name)
            .order_by(Run.created_at.desc())
            .limit(1)
        )
        last_status_stmt = _apply_tenant_filter(
            last_status_stmt, tenant_id, Run.tenant_id
        )
        async with async_session() as session:
            last_row = (await session.execute(last_status_stmt)).one_or_none()
        last_status_val: str | None = None
        last_when: datetime | None = None
        if last_row is not None:
            status_obj = last_row[0]
            last_status_val = (
                status_obj.value if hasattr(status_obj, "value") else str(status_obj)
            )
            last_when = last_row[1] or last_row[2] or last_row[3]

        total = int(row.total_runs or 0)
        succ = int(row.success_count or 0)
        success_rate = round((succ / total) * 100.0, 1) if total > 0 else 0.0
        results.append(
            {
                "name": row.name,
                "total_runs": total,
                "success_rate": success_rate,
                "avg_cost_usd": round(float(row.avg_cost or 0.0), 4),
                "last_run_status": last_status_val,
                "last_run_at": last_when.isoformat() if last_when else None,
                "last_run_ago": _format_relative_time(last_when),
            }
        )
    return results


@router.get("/workflows/stats")
async def list_workflow_stats(req: Request) -> ApiResponse:
    """Return per-workflow run statistics for the caller's tenant.

    Cached in-process for 30s per tenant to keep grid view rendering cheap.
    """
    tenant_id = get_tenant_id(req)
    cache_key = f"all:{tenant_id or '_'}"
    cached = _stats_cache_get(cache_key)
    if cached is not None:
        return ApiResponse(data=cached)

    try:
        data = await _compute_workflow_stats(tenant_id)
    except Exception as exc:
        logger.warning("workflow stats query failed: %s", exc)
        return ApiResponse(data=[])

    _stats_cache_set(cache_key, data)
    return ApiResponse(data=data)


@router.get("/workflows/{name}/stats")
async def get_workflow_stats(name: str, req: Request) -> ApiResponse:
    """Return run statistics for a single workflow (tenant-scoped)."""
    if not name or not name.strip():
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_WORKFLOW_NAME",
                    message="Workflow name must not be empty",
                )
            ).model_dump(),
        )
    tenant_id = get_tenant_id(req)
    cache_key = f"one:{tenant_id or '_'}:{name}"
    cached = _stats_cache_get(cache_key)
    if cached is not None:
        return ApiResponse(data=cached)

    try:
        rows = await _compute_workflow_stats(tenant_id, name_filter=name)
    except Exception as exc:
        logger.warning("workflow stats query failed for %s: %s", name, exc)
        rows = []

    if not rows:
        data = {
            "name": name,
            "total_runs": 0,
            "success_rate": 0.0,
            "avg_cost_usd": 0.0,
            "last_run_status": None,
            "last_run_at": None,
            "last_run_ago": None,
        }
    else:
        data = rows[0]

    _stats_cache_set(cache_key, data)
    return ApiResponse(data=data)


@router.get("/workflows/{name}")
async def get_workflow_by_name(name: str, req: Request) -> ApiResponse:
    """Return detail for a single workflow.

    Looks up the latest production version in the registry first; falls back
    to the YAML file in ``workflows_dir``. Returns 404 when neither exists.
    Strips ``yaml_content`` and per-step ``prompt`` for non-admin callers
    so internal prompt engineering doesn't leak to scoped API keys.
    """
    if not name or not name.strip():
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_WORKFLOW_NAME",
                    message="Workflow name must not be empty",
                )
            ).model_dump(),
        )

    caller_is_admin = is_admin(req)

    # Try DB production version first
    yaml_content: str | None = None
    workflow_version: int | None = None
    version_status: str | None = None
    total_versions: int | None = None
    try:
        async with async_session() as session:
            count_stmt = select(func.count(WorkflowVersion.id)).where(
                WorkflowVersion.workflow_name == name
            )
            total_versions = await session.scalar(count_stmt) or None

            prod_stmt = (
                select(WorkflowVersion)
                .where(
                    WorkflowVersion.workflow_name == name,
                    WorkflowVersion.status == WorkflowVersionStatus.PRODUCTION,
                )
                .order_by(WorkflowVersion.version.desc())
                .limit(1)
            )
            prod = (await session.execute(prod_stmt)).scalar_one_or_none()
            if prod:
                yaml_content = prod.yaml_content
                workflow_version = prod.version
                version_status = "production"
            elif total_versions:
                # No production version yet — fall back to the newest version.
                latest_stmt = (
                    select(WorkflowVersion)
                    .where(WorkflowVersion.workflow_name == name)
                    .order_by(WorkflowVersion.version.desc())
                    .limit(1)
                )
                latest = (await session.execute(latest_stmt)).scalar_one_or_none()
                if latest:
                    yaml_content = latest.yaml_content
                    workflow_version = latest.version
                    version_status = (
                        latest.status.value
                        if hasattr(latest.status, "value")
                        else str(latest.status)
                    )
    except Exception:
        # DB unavailable — fall through to disk fallback
        pass

    file_name: str | None = None
    if yaml_content is None:
        try:
            yaml_content = _load_workflow_yaml(name)
            file_name = f"{name}.yaml"
        except (FileNotFoundError, ValueError):
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="NOT_FOUND",
                        message=f"Workflow '{name}' not found",
                    )
                ).model_dump(),
            )

    try:
        workflow = parse_yaml_string(yaml_content)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_WORKFLOW",
                    message=f"Stored workflow could not be parsed: {e}",
                )
            ).model_dump(),
        )

    doctor_status: str | None = None
    doctor_risk: str | None = None
    try:
        from sandcastle.engine.doctor import diagnose

        dr = diagnose(workflow)
        doctor_status = "ok" if dr.ok else ("blocked" if dr.blocking else "warning")
        doctor_risk = dr.risk
    except Exception:
        pass

    return ApiResponse(
        data=WorkflowInfoResponse(
            name=workflow.name,
            description=workflow.description,
            steps_count=len(workflow.steps),
            file_name=file_name,
            steps=[
                WorkflowStepInfo(
                    id=s.id,
                    depends_on=s.depends_on,
                    model=s.model,
                    prompt=s.prompt if caller_is_admin else None,
                )
                for s in workflow.steps
            ],
            input_schema=workflow.input_schema,
            version=workflow_version,
            version_status=version_status,
            total_versions=total_versions,
            yaml_content=yaml_content if caller_is_admin else None,
            doctor_status=doctor_status,
            doctor_risk=doctor_risk,
        )
    )


@router.post("/workflows", status_code=201)
async def save_workflow(request: WorkflowSaveRequest, req: Request) -> ApiResponse:
    """Save a workflow YAML file to the workflows directory and create a draft version."""
    _require_admin(req)
    try:
        workflow = parse_yaml_string(request.content)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_WORKFLOW", message=str(e))
            ).model_dump(),
        )

    errors = validate(workflow)
    if errors:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="VALIDATION_ERROR", message="; ".join(errors))
            ).model_dump(),
        )

    # Enforce YAML name consistency with request name to prevent
    # registry key / run name divergence (runs use YAML name, delete
    # guards use registry key - mismatch lets active runs escape protection).
    req_safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in request.name)
    yaml_content = request.content
    if workflow.name != req_safe:
        workflow.name = req_safe
        # Rewrite the name in the stored YAML to prevent name mismatch
        # between in-memory workflow and persisted content
        import re as _re
        yaml_content = _re.sub(
            r"^(name:\s*).*$",
            rf"\g<1>{req_safe}",
            yaml_content,
            count=1,
            flags=_re.MULTILINE,
        )

    # Write to disk (backward compat)
    workflows_dir = Path(settings.workflows_dir)
    workflows_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in request.name)
    if not safe_name or safe_name.strip("_") == "":
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_NAME",
                    message="Workflow name must contain at least one alphanumeric character",
                )
            ).model_dump(),
        )
    file_path = workflows_dir / f"{safe_name}.yaml"
    resolved_path = file_path.resolve()
    if not resolved_path.is_relative_to(workflows_dir.resolve()):
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_NAME", message="Invalid workflow name")
            ).model_dump(),
        )
    # Create a draft version in the registry FIRST, then write disk
    new_version = None
    try:
        async with async_session() as session:
            next_ver = await _get_next_version(session, safe_name)
            checksum = _compute_checksum(yaml_content)
            wv = WorkflowVersion(
                workflow_name=safe_name,
                version=next_ver,
                status=WorkflowVersionStatus.DRAFT,
                yaml_content=yaml_content,
                description=request.description,
                steps_count=len(workflow.steps),
                checksum=checksum,
            )
            session.add(wv)
            await session.commit()
            new_version = next_ver
    except Exception:
        logger.warning(
            "Could not create workflow version in registry for %s",
            safe_name,
            exc_info=True,
        )

    # Write YAML to disk (after DB so both stay consistent)
    file_path.write_text(yaml_content)

    # Run doctor check on save (non-blocking - just adds status to response)
    doctor_status = None
    doctor_risk = None
    try:
        from sandcastle.engine.doctor import diagnose
        doctor_report = diagnose(workflow)
        doctor_status = "ok" if doctor_report.ok else ("blocked" if doctor_report.blocking else "warning")
        doctor_risk = doctor_report.risk
    except Exception:
        logger.debug("Doctor check failed on save", exc_info=True)

    return ApiResponse(
        data=WorkflowInfoResponse(
            name=workflow.name,
            description=workflow.description,
            steps_count=len(workflow.steps),
            file_name=file_path.name,
            steps=[
                WorkflowStepInfo(
                    id=s.id,
                    depends_on=s.depends_on,
                    model=s.model,
                    prompt=s.prompt,
                )
                for s in workflow.steps
            ],
            input_schema=workflow.input_schema,
            version=new_version,
            version_status="draft" if new_version else None,
            doctor_status=doctor_status,
            doctor_risk=doctor_risk,
        )
    )


@router.get("/workflows/{name}/doctor")
async def doctor_workflow(name: str, req: Request) -> ApiResponse:
    """Run preflight diagnostics on a workflow.

    Returns a structured report with blocking issues, warnings, and
    suggested fixes. Use this before executing, promoting, or publishing.
    """
    from sandcastle.engine.doctor import diagnose

    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    workflows_dir = Path(settings.workflows_dir)
    file_path = workflows_dir / f"{safe_name}.yaml"
    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message=f"Workflow '{name}' not found")
            ).model_dump(),
        )
    try:
        yaml_content = file_path.read_text()
        workflow = parse_yaml_string(yaml_content)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_WORKFLOW", message=str(e))
            ).model_dump(),
        )

    report = diagnose(workflow)
    return ApiResponse(data=report.to_dict())


@router.post("/workflows/doctor")
async def doctor_workflow_yaml(request: WorkflowSaveRequest, req: Request) -> ApiResponse:
    """Run preflight diagnostics on raw YAML (without saving).

    Accepts the same payload as POST /workflows but only runs diagnostics.
    """
    from sandcastle.engine.doctor import diagnose_yaml

    report = diagnose_yaml(request.content)
    return ApiResponse(data=report.to_dict())


@router.delete("/workflows/{name}")
async def delete_workflow(name: str, req: Request) -> ApiResponse:
    """Delete a workflow YAML file and all its version records."""
    _require_admin(req)
    from sqlalchemy import delete as sa_delete

    # Validate name
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    if not safe_name or safe_name.strip("_") == "" or safe_name != name:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_NAME", message="Invalid workflow name")
            ).model_dump(),
        )

    # Path traversal check
    workflows_dir = Path(settings.workflows_dir)
    file_path = workflows_dir / f"{safe_name}.yaml"
    resolved_path = file_path.resolve()
    if not resolved_path.is_relative_to(workflows_dir.resolve()):
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_NAME", message="Invalid workflow name")
            ).model_dump(),
        )

    # Check file exists on disk FIRST (try both .yaml and .yml extensions)
    yml_path = workflows_dir / f"{safe_name}.yml"
    if file_path.exists():
        target_path = file_path
    elif yml_path.exists():
        target_path = yml_path
    else:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="NOT_FOUND",
                    message=f"Workflow '{name}' not found",
                )
            ).model_dump(),
        )

    # Check for active runs (including those awaiting approval)
    async with async_session() as session:
        active_stmt = select(func.count(Run.id)).where(
            Run.workflow_name == safe_name,
            Run.status.in_([RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.AWAITING_APPROVAL]),
        )
        active_count = (await session.execute(active_stmt)).scalar() or 0
        if active_count > 0:
            raise HTTPException(
                status_code=409,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="ACTIVE_RUNS",
                        message=f"Cannot delete workflow with {active_count} active run(s). Cancel them first.",
                    )
                ).model_dump(),
            )

        # Delete file from disk first, then DB versions
        target_path.unlink()

        # Delete all WorkflowVersion records
        await session.execute(
            sa_delete(WorkflowVersion).where(WorkflowVersion.workflow_name == safe_name)
        )
        await session.commit()

    return ApiResponse(data={"deleted": True, "workflow_name": name})


# --- Workflow Execution ---


@router.post("/workflows/run/sync")
async def run_workflow_sync(request: WorkflowRunRequest, req: Request) -> ApiResponse:
    """Run a workflow synchronously. Blocks until complete."""
    await execution_limiter.check(req)
    tenant_id = get_tenant_id(req)

    try:
        yaml_content, wf_version = await _resolve_workflow_request(request)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_WORKFLOW", message=str(e))
            ).model_dump(),
        )

    # Enforce allowed_workflows restriction (same as /v1/ endpoint)
    allowed_workflows = getattr(req.state, "allowed_workflows", None)
    if allowed_workflows is not None and request.workflow not in allowed_workflows:
        raise HTTPException(
            status_code=403,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="FORBIDDEN",
                    message=f"API key is not authorized to call workflow '{request.workflow}'",
                )
            ).model_dump(),
        )

    try:
        workflow = parse_yaml_string(yaml_content)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_WORKFLOW", message=str(e))
            ).model_dump(),
        )

    errors = validate(workflow)
    if errors:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="VALIDATION_ERROR", message="; ".join(errors))
            ).model_dump(),
        )

    validation_errors = _validate_workflow_input(request.input, workflow.input_schema)
    if validation_errors:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_INPUT", message="; ".join(validation_errors)
                )
            ).model_dump(),
        )

    try:
        plan = build_plan(workflow)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(error=ErrorResponse(code="PLAN_ERROR", message=str(e))).model_dump(),
        )

    # Validate callback_url if provided (SSRF prevention)
    if request.callback_url:
        try:
            from sandcastle.webhooks.dispatcher import validate_callback_url
            validate_callback_url(request.callback_url)
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(code="INVALID_CALLBACK_URL", message=str(e))
                ).model_dump(),
            )

    # Resolve budget
    budget = await _resolve_budget(request.max_cost_usd, tenant_id)

    # Idempotency check (scoped to tenant)
    run_id = str(uuid.uuid4())
    if request.idempotency_key:
        async with async_session() as session:
            idemp_stmt = select(Run.id).where(Run.idempotency_key == request.idempotency_key)
            idemp_stmt = _apply_tenant_filter(idemp_stmt, tenant_id, Run.tenant_id)
            existing = await session.scalar(idemp_stmt)
            if existing:
                return ApiResponse(
                    data=RunIdempotentResponse(run_id=str(existing)),
                )

    storage = create_storage()

    # Create DB record (mandatory - run_id must be in history)
    try:
        async with async_session() as session:
            db_run = Run(
                id=uuid.UUID(run_id),
                workflow_name=workflow.name,
                status=RunStatus.RUNNING,
                input_data=request.input,
                callback_url=request.callback_url,
                tenant_id=tenant_id,
                idempotency_key=request.idempotency_key,
                max_cost_usd=budget,
                workflow_version=wf_version,
                started_at=datetime.now(timezone.utc),
                risk_level=getattr(workflow, "risk_level", "minimal"),
            )
            session.add(db_run)
            await session.commit()
    except Exception:
        logger.error(
            "Failed to create run record in database for %s",
            run_id,
            exc_info=True,
        )
        raise HTTPException(
            status_code=503,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="DB_UNAVAILABLE",
                    message="Database is unavailable, cannot persist run",
                )
            ).model_dump(),
        )

    try:
        result = await execute_workflow(
            workflow=workflow,
            plan=plan,
            input_data=request.input,
            run_id=run_id,
            storage=storage,
            max_cost_usd=budget,
            admin_trusted=True,
            tenant_id=tenant_id,
        )
    except Exception as exc:
        # Mark the run as FAILED so it doesn't stay stuck in RUNNING.
        try:
            async with async_session() as session:
                db_run = await session.get(Run, uuid.UUID(run_id))
                if db_run:
                    db_run.status = RunStatus.FAILED
                    db_run.error = f"Engine error: {exc}"
                    db_run.completed_at = datetime.now(timezone.utc)
                    await session.commit()
        except Exception:
            logger.error("Failed to mark run %s as FAILED after engine error", run_id, exc_info=True)
        raise

    # Map result status to RunStatus
    status_map = {
        "completed": RunStatus.COMPLETED,
        "failed": RunStatus.FAILED,
        "cancelled": RunStatus.CANCELLED,
        "budget_exceeded": RunStatus.BUDGET_EXCEEDED,
        "awaiting_approval": RunStatus.AWAITING_APPROVAL,
    }

    # Update DB record with results. Retry transient failures so a finished run is
    # not silently reported as durable while its DB row stays RUNNING; if every
    # attempt fails we surface verification_pending below instead of lying about it.
    output_with_report = dict(result.outputs) if result.outputs else {}
    if result.token_report:
        output_with_report["_token_report"] = result.token_report
    db_persist_ok = False
    for _attempt in range(3):
        try:
            async with async_session() as session:
                db_run = await session.get(Run, uuid.UUID(run_id))
                if db_run:
                    db_run.status = status_map.get(result.status, RunStatus.FAILED)
                    db_run.output_data = output_with_report
                    db_run.total_cost_usd = result.total_cost_usd
                    if result.status != "awaiting_approval":
                        db_run.completed_at = result.completed_at
                    db_run.error = result.error
                    await session.commit()
            db_persist_ok = True
            break
        except Exception:
            logger.error(
                "Failed to update run %s result in database (attempt %d/3)",
                run_id,
                _attempt + 1,
                exc_info=True,
            )
            if _attempt < 2:
                await asyncio.sleep(0.2 * (2**_attempt))
    if not db_persist_ok:
        logger.critical(
            "Run %s finished but its result could NOT be persisted after 3 attempts; "
            "the DB row is stale and the returned result is not durable.",
            run_id,
        )

    # Dispatch webhooks (same as async path in worker)
    try:
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        webhook_urls = []
        if request.callback_url:
            webhook_urls.append(request.callback_url)

        if result.status == "completed":
            if not request.callback_url and workflow.on_complete and workflow.on_complete.webhook:
                webhook_urls.append(workflow.on_complete.webhook)
        elif result.status == "failed":
            if workflow.on_failure and workflow.on_failure.webhook:
                webhook_urls.append(workflow.on_failure.webhook)

        _sync_event_map = {
            "completed": "workflow.completed",
            "failed": "workflow.failed",
            "cancelled": "workflow.cancelled",
            "budget_exceeded": "workflow.budget_exceeded",
            "awaiting_approval": "workflow.awaiting_approval",
        }
        event_type = _sync_event_map.get(result.status, "workflow.failed")

        # Apply PII redaction to webhook outputs if privacy router is active.
        webhook_outputs = result.outputs
        if webhook_urls:
            try:
                from sandcastle.config import settings as _cfg
                from sandcastle.engine.privacy import PrivacyRouter

                _srv_priv = {
                    "enabled": _cfg.privacy_enabled,
                    "entities": _cfg.privacy_entities,
                    "apply_to": _cfg.privacy_apply_to,
                }
                _priv_router = PrivacyRouter.from_workflow(
                    workflow_privacy=getattr(workflow, "privacy", None),
                    server_config=_srv_priv,
                )
                if _priv_router and "webhooks" in _priv_router.config.apply_to:
                    scrubbed, _matches = _priv_router.scrub_dict(webhook_outputs)
                    webhook_outputs = scrubbed
            except Exception as _priv_err:
                logger.warning("PrivacyRouter webhook scrub failed: %s", _priv_err)

        webhook_urls = list(dict.fromkeys(webhook_urls))
        for webhook_url in webhook_urls:
            duration = 0.0
            if result.started_at and result.completed_at:
                duration = (result.completed_at - result.started_at).total_seconds()
            await dispatch_webhook(
                url=webhook_url,
                event=event_type,
                run_id=run_id,
                workflow=workflow.name,
                status=result.status,
                outputs=webhook_outputs,
                costs=result.total_cost_usd,
                duration_seconds=duration,
                error=result.error,
            )
    except Exception:
        logger.warning("Could not dispatch webhook for sync run")

    return ApiResponse(
        data=RunStatusResponse(
            run_id=result.run_id,
            workflow_name=workflow.name,
            # Don't claim a terminal status the DB never recorded: when persistence
            # failed, report verification_pending so the caller knows the result is
            # not durable rather than trusting a "completed" that was lost.
            status=result.status if db_persist_ok else "verification_pending",
            input_data=request.input,
            outputs=result.outputs,
            total_cost_usd=result.total_cost_usd,
            max_cost_usd=budget,
            started_at=result.started_at,
            completed_at=result.completed_at,
            error=result.error,
        )
    )


@router.post("/workflows/run", status_code=202)
async def run_workflow_async(request: WorkflowRunRequest, req: Request) -> ApiResponse:
    """Run a workflow asynchronously. Returns immediately with run_id."""
    await execution_limiter.check(req)
    tenant_id = get_tenant_id(req)

    try:
        yaml_content, wf_version = await _resolve_workflow_request(request)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_WORKFLOW", message=str(e))
            ).model_dump(),
        )

    # Enforce allowed_workflows restriction (same as /v1/ endpoint)
    allowed_workflows = getattr(req.state, "allowed_workflows", None)
    if allowed_workflows is not None and request.workflow not in allowed_workflows:
        raise HTTPException(
            status_code=403,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="FORBIDDEN",
                    message=f"API key is not authorized to call workflow '{request.workflow}'",
                )
            ).model_dump(),
        )

    try:
        workflow = parse_yaml_string(yaml_content)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_WORKFLOW", message=str(e))
            ).model_dump(),
        )

    errors = validate(workflow)
    if errors:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="VALIDATION_ERROR", message="; ".join(errors))
            ).model_dump(),
        )

    validation_errors = _validate_workflow_input(request.input, workflow.input_schema)
    if validation_errors:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_INPUT", message="; ".join(validation_errors)
                )
            ).model_dump(),
        )

    # Validate callback_url if provided (SSRF prevention)
    if request.callback_url:
        try:
            from sandcastle.webhooks.dispatcher import validate_callback_url
            validate_callback_url(request.callback_url)
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(code="INVALID_CALLBACK_URL", message=str(e))
                ).model_dump(),
            )

    # Resolve budget
    budget = await _resolve_budget(request.max_cost_usd, tenant_id)

    # Idempotency check (scoped to tenant)
    if request.idempotency_key:
        async with async_session() as session:
            idemp_stmt = select(Run.id).where(Run.idempotency_key == request.idempotency_key)
            idemp_stmt = _apply_tenant_filter(idemp_stmt, tenant_id, Run.tenant_id)
            existing = await session.scalar(idemp_stmt)
            if existing:
                return ApiResponse(
                    data=RunIdempotentResponse(run_id=str(existing)),
                )

    run_id = str(uuid.uuid4())

    # Create DB record with QUEUED status
    try:
        async with async_session() as session:
            db_run = Run(
                id=uuid.UUID(run_id),
                workflow_name=workflow.name,
                status=RunStatus.QUEUED,
                input_data=request.input,
                callback_url=request.callback_url,
                tenant_id=tenant_id,
                idempotency_key=request.idempotency_key,
                max_cost_usd=budget,
                workflow_version=wf_version,
                risk_level=getattr(workflow, "risk_level", "minimal"),
            )
            session.add(db_run)
            await session.commit()
    except Exception as e:
        logger.error("Could not create run in database: %s", e)
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(code="DB_ERROR", message="Could not create run")
            ).model_dump(),
        )

    # Enqueue the job - clean up orphan run on failure
    try:
        await enqueue_workflow(yaml_content, request.input, run_id, admin_trusted=True)
    except Exception as e:
        # Mark the run as failed so it doesn't stay stuck as "queued"
        try:
            async with async_session() as session:
                db_run = await session.get(Run, uuid.UUID(run_id))
                if db_run:
                    db_run.status = RunStatus.FAILED
                    db_run.error = f"Failed to enqueue: {e}"
                    db_run.completed_at = datetime.now(timezone.utc)
                    await session.commit()
        except Exception:
            logger.error("Could not clean up orphan run %s", run_id)

        logger.error("Could not enqueue job for run %s: %s", run_id, e)
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(code="QUEUE_ERROR", message="Could not enqueue job")
            ).model_dump(),
        )

    return ApiResponse(
        data=RunQueuedResponse(run_id=run_id, status="queued"),
    )


# --- Runs ---


@router.get("/runs/compare")
async def compare_runs(
    run_a: str = Query(..., description="First run ID"),
    run_b: str = Query(..., description="Second run ID"),
    req: Request = None,
) -> ApiResponse:
    """Compare two runs side-by-side for the Replay Studio."""
    tenant_id = get_tenant_id(req) if req else None

    try:
        uuid_a = uuid.UUID(run_a)
        uuid_b = uuid.UUID(run_b)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_INPUT", message="Invalid run ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        stmt_a = select(Run).options(selectinload(Run.steps)).where(Run.id == uuid_a)
        stmt_a = _apply_tenant_filter(stmt_a, tenant_id, Run.tenant_id)
        stmt_b = select(Run).options(selectinload(Run.steps)).where(Run.id == uuid_b)
        stmt_b = _apply_tenant_filter(stmt_b, tenant_id, Run.tenant_id)

        result_a = await session.execute(stmt_a)
        result_b = await session.execute(stmt_b)
        db_run_a = result_a.scalar_one_or_none()
        db_run_b = result_b.scalar_one_or_none()

    if not db_run_a:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message=f"Run '{run_a}' not found")
            ).model_dump(),
        )
    if not db_run_b:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message=f"Run '{run_b}' not found")
            ).model_dump(),
        )

    same_workflow = db_run_a.workflow_name == db_run_b.workflow_name

    # Extract step configs from the workflow version that actually ran,
    # falling back to current disk file if no version is stored.
    configs_a: dict[str, dict] = {}
    configs_b: dict[str, dict] = {}
    try:
        yaml_a = await _load_versioned_workflow_yaml(
            db_run_a.workflow_name, db_run_a.workflow_version
        )
        configs_a = _extract_step_configs(yaml_a)
    except Exception:
        pass
    try:
        yaml_b = await _load_versioned_workflow_yaml(
            db_run_b.workflow_name, db_run_b.workflow_version
        )
        configs_b = _extract_step_configs(yaml_b)
    except Exception:
        pass

    def _step_status_str(step):
        if not step:
            return None
        s = step.status
        return s.value if hasattr(s, "value") else s

    # Build step maps keyed by (step_id, parallel_index)
    def _step_key(s):
        return (s.step_id, s.parallel_index)

    steps_map_a = {_step_key(s): s for s in db_run_a.steps}
    steps_map_b = {_step_key(s): s for s in db_run_b.steps}
    all_keys = sorted(set(steps_map_a.keys()) | set(steps_map_b.keys()))

    step_diffs = []
    for key in all_keys:
        sa_step = steps_map_a.get(key)
        sb_step = steps_map_b.get(key)
        step_id, parallel_index = key

        if sa_step and sb_step:
            presence = "both"
        elif sa_step:
            presence = "only_a"
        else:
            presence = "only_b"

        cfg_a = configs_a.get(step_id)
        cfg_b = configs_b.get(step_id)
        config_changed = cfg_a != cfg_b if (cfg_a and cfg_b) else False

        out_a = sa_step.output_data if sa_step else None
        out_b = sb_step.output_data if sb_step else None

        cost_a = sa_step.cost_usd if sa_step else 0.0
        cost_b = sb_step.cost_usd if sb_step else 0.0
        dur_a = sa_step.duration_seconds if sa_step else 0.0
        dur_b = sb_step.duration_seconds if sb_step else 0.0

        step_diffs.append(
            StepDiff(
                step_id=step_id,
                parallel_index=parallel_index,
                presence=presence,
                config_a=cfg_a,
                config_b=cfg_b,
                config_changed=config_changed,
                output_a=out_a,
                output_b=out_b,
                output_changed=out_a != out_b,
                cost_a=cost_a,
                cost_b=cost_b,
                cost_delta=round(cost_b - cost_a, 6),
                duration_a=dur_a,
                duration_b=dur_b,
                duration_delta=round(dur_b - dur_a, 2),
                status_a=_step_status_str(sa_step),
                status_b=_step_status_str(sb_step),
                error_a=sa_step.error if sa_step else None,
                error_b=sb_step.error if sb_step else None,
            )
        )

    def _run_duration(run):
        if run.started_at and run.completed_at:
            return (run.completed_at - run.started_at).total_seconds()
        return None

    dur_a = _run_duration(db_run_a)
    dur_b = _run_duration(db_run_b)

    def _run_status_str(run):
        s = run.status
        return s.value if hasattr(s, "value") else s

    return ApiResponse(
        data=RunCompareResponse(
            run_a=RunListItem(
                run_id=str(db_run_a.id),
                workflow_name=db_run_a.workflow_name,
                status=_run_status_str(db_run_a),
                total_cost_usd=db_run_a.total_cost_usd,
                started_at=db_run_a.started_at,
                completed_at=db_run_a.completed_at,
                parent_run_id=(str(db_run_a.parent_run_id) if db_run_a.parent_run_id else None),
            ),
            run_b=RunListItem(
                run_id=str(db_run_b.id),
                workflow_name=db_run_b.workflow_name,
                status=_run_status_str(db_run_b),
                total_cost_usd=db_run_b.total_cost_usd,
                started_at=db_run_b.started_at,
                completed_at=db_run_b.completed_at,
                parent_run_id=(str(db_run_b.parent_run_id) if db_run_b.parent_run_id else None),
            ),
            total_cost_a=db_run_a.total_cost_usd,
            total_cost_b=db_run_b.total_cost_usd,
            total_cost_delta=round(db_run_b.total_cost_usd - db_run_a.total_cost_usd, 6),
            total_duration_a=dur_a,
            total_duration_b=dur_b,
            total_duration_delta=(
                round(dur_b - dur_a, 2) if dur_a is not None and dur_b is not None else None
            ),
            same_workflow=same_workflow,
            steps=step_diffs,
        )
    )


@router.get("/runs/{run_id}")
async def get_run(run_id: str, req: Request) -> ApiResponse:
    """Get the status and details of a specific run."""
    tenant_id = get_tenant_id(req)

    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid run ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        stmt = (
            select(Run)
            .options(selectinload(Run.steps), selectinload(Run.children))
            .where(Run.id == run_uuid)
        )
        stmt = _apply_tenant_filter(stmt, tenant_id, Run.tenant_id)
        result = await session.execute(stmt)
        run = result.scalar_one_or_none()

    if not run:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message=f"Run '{run_id}' not found")
            ).model_dump(),
        )

    # Deduplicate steps: kept for backwards compatibility with records
    # created before the upsert fix. New records use upsert and won't
    # have duplicates.
    _STATUS_PRIORITY = {"completed": 3, "failed": 2, "running": 1, "queued": 0}
    _dedup: dict[tuple[str, int | None], object] = {}
    for s in run.steps:
        key = (s.step_id, s.parallel_index)
        status_val = s.status.value if hasattr(s.status, "value") else s.status
        prev = _dedup.get(key)
        if prev is None or _STATUS_PRIORITY.get(status_val, 0) > _STATUS_PRIORITY.get(
            prev.status.value if hasattr(prev.status, "value") else prev.status, 0
        ):
            _dedup[key] = s

    steps = [
        StepStatusResponse(
            step_id=s.step_id,
            parallel_index=s.parallel_index,
            status=s.status.value if hasattr(s.status, "value") else s.status,
            output=s.output_data,
            cost_usd=s.cost_usd,
            duration_seconds=s.duration_seconds,
            attempt=s.attempt,
            error=s.error,
            started_at=s.started_at.isoformat() if s.started_at else None,
            pdf_artifact=bool(
                isinstance(s.output_data, dict) and s.output_data.get("_pdf_artifact")
            ),
            model=s.model,
        )
        for s in _dedup.values()
    ]

    # Extract token_report from output_data if present
    token_report = None
    outputs = run.output_data
    if isinstance(outputs, dict) and "_token_report" in outputs:
        token_report = outputs.get("_token_report")
        # Return outputs without the internal metadata key
        outputs = {k: v for k, v in outputs.items() if k != "_token_report"}

    return ApiResponse(
        data=RunStatusResponse(
            run_id=str(run.id),
            workflow_name=run.workflow_name,
            status=run.status.value if hasattr(run.status, "value") else run.status,
            input_data=run.input_data,
            outputs=outputs,
            total_cost_usd=run.total_cost_usd,
            max_cost_usd=run.max_cost_usd,
            started_at=run.started_at,
            completed_at=run.completed_at,
            error=run.error,
            steps=steps,
            parent_run_id=str(run.parent_run_id) if run.parent_run_id else None,
            replay_from_step=run.replay_from_step,
            fork_changes=run.fork_changes,
            depth=run.depth,
            sub_workflow_of_step=run.sub_workflow_of_step,
            sub_runs=[
                {
                    "run_id": str(c.id),
                    "workflow_name": c.workflow_name,
                    "status": c.status.value if hasattr(c.status, "value") else c.status,
                    "sub_workflow_of_step": c.sub_workflow_of_step,
                }
                for c in run.children
            ]
            if run.children
            else None,
            risk_level=run.risk_level or "minimal",
            token_report=token_report,
        )
    )


@router.get("/runs/{run_id}/steps/{step_id}/pdf")
async def download_step_pdf(run_id: str, step_id: str, req: Request):
    """Download the PDF report artifact for a specific step."""
    from fastapi.responses import FileResponse

    tenant_id = get_tenant_id(req)

    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_INPUT", message="Invalid run ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        stmt = select(Run).options(selectinload(Run.steps)).where(Run.id == run_uuid)
        stmt = _apply_tenant_filter(stmt, tenant_id, Run.tenant_id)
        result = await session.execute(stmt)
        run = result.scalar_one_or_none()

    if not run:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message="Run not found")
            ).model_dump(),
        )

    # Find the step
    step = next(
        (s for s in run.steps if s.step_id == step_id),
        None,
    )
    if not step:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message=f"Step '{step_id}' not found")
            ).model_dump(),
        )

    # Extract PDF artifact path from output_data
    pdf_path = None
    if isinstance(step.output_data, dict):
        pdf_path = step.output_data.get("_pdf_artifact")

    if not pdf_path:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message="No PDF artifact for this step")
            ).model_dump(),
        )

    file_path = Path(pdf_path).resolve()

    # Prevent path traversal: PDF must be within the data directory
    data_dir = Path(settings.data_dir).resolve()
    if not file_path.is_relative_to(data_dir):
        raise HTTPException(
            status_code=403,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="FORBIDDEN", message="PDF path outside data directory"
                )
            ).model_dump(),
        )

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message="PDF file not found on disk")
            ).model_dump(),
        )

    if not file_path.suffix.lower() == ".pdf":
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_ARTIFACT", message="Artifact is not a PDF file"
                )
            ).model_dump(),
        )

    return FileResponse(
        path=str(file_path),
        media_type="application/pdf",
        filename=file_path.name,
    )


@router.get("/runs/{run_id}/artifacts/{filename}")
async def download_run_artifact(run_id: str, filename: str, req: Request):
    """Download an artifact file (image, etc.) saved during a run."""
    from fastapi.responses import FileResponse as _FileResp

    tenant_id = get_tenant_id(req)

    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid run ID format")
            ).model_dump(),
        )

    # Verify run exists and belongs to tenant
    async with async_session() as session:
        stmt = select(Run).where(Run.id == run_uuid)
        stmt = _apply_tenant_filter(stmt, tenant_id, Run.tenant_id)
        result = await session.execute(stmt)
        run = result.scalar_one_or_none()

    if not run:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message="Run not found")
            ).model_dump(),
        )

    # Path traversal protection: only allow simple filenames
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_INPUT", message="Invalid filename")
            ).model_dump(),
        )

    artifact_path = Path(settings.data_dir) / "artifacts" / run_id / filename
    artifact_path = artifact_path.resolve()
    data_dir = Path(settings.data_dir).resolve()
    if not artifact_path.is_relative_to(data_dir):
        raise HTTPException(
            status_code=403,
            detail=ApiResponse(
                error=ErrorResponse(code="FORBIDDEN", message="Path outside data directory")
            ).model_dump(),
        )

    if not artifact_path.exists():
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message="Artifact not found")
            ).model_dump(),
        )

    # Determine media type from extension
    _MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
    media_type = _MEDIA_TYPES.get(artifact_path.suffix.lower(), "application/octet-stream")

    return _FileResp(path=str(artifact_path), media_type=media_type, filename=filename)


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str, request: Request) -> StreamingResponse:
    """Stream live progress of a run via SSE."""
    tenant_id = get_tenant_id(request)

    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_INPUT", message="Invalid run ID format")
            ).model_dump(),
        )

    # Verify the run belongs to the tenant before starting the stream
    async with async_session() as session:
        check_stmt = select(Run.id).where(Run.id == run_uuid)
        check_stmt = _apply_tenant_filter(check_stmt, tenant_id, Run.tenant_id)
        if not await session.scalar(check_stmt):
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message="Run not found")
                ).model_dump(),
            )

    async def event_generator():
        """Poll the database and emit SSE events as status changes.

        Sends keepalive comments every ``SSE_KEEPALIVE_INTERVAL_SECONDS``
        when no status change occurs to prevent proxy timeout.
        """
        last_status = None
        # Dedup key includes status so state transitions (running -> completed) are sent
        seen_step_keys: set[tuple[str, int | None, str]] = set()
        last_event_time = time.monotonic()

        while True:  # Run until terminal state or client disconnect
            if await request.is_disconnected():
                break
            async with async_session() as session:
                stmt = select(Run).options(selectinload(Run.steps)).where(Run.id == run_uuid)
                result = await session.execute(stmt)
                run = result.scalar_one_or_none()

            if not run:
                yield _sse_event("error", {"message": f"Run '{run_id}' not found"})
                return

            current_status = run.status.value if hasattr(run.status, "value") else run.status
            emitted = False

            # Emit status change events
            if current_status != last_status:
                yield _sse_event(
                    "status",
                    {
                        "run_id": str(run.id),
                        "status": current_status,
                        "total_cost_usd": run.total_cost_usd,
                    },
                )
                last_status = current_status
                emitted = True

            # Emit step update events (keyed by step_id + parallel_index + status)
            for step in run.steps:
                step_status = (
                    step.status.value if hasattr(step.status, "value") else step.status
                )
                key = (step.step_id, step.parallel_index, step_status)
                if key not in seen_step_keys:
                    seen_step_keys.add(key)
                    yield _sse_event(
                        "step",
                        {
                            "step_id": step.step_id,
                            "parallel_index": step.parallel_index,
                            "status": step_status,
                            "cost_usd": step.cost_usd,
                            "duration_seconds": step.duration_seconds,
                        },
                    )
                    emitted = True

            if emitted:
                last_event_time = time.monotonic()

            # Terminal states - emit final result and stop
            if current_status in (
                "completed",
                "failed",
                "partial",
                "cancelled",
                "budget_exceeded",
                "awaiting_approval",
            ):
                yield _sse_event(
                    "result",
                    {
                        "run_id": str(run.id),
                        "status": current_status,
                        "outputs": run.output_data,
                        "total_cost_usd": run.total_cost_usd,
                        "error": run.error,
                    },
                )
                return

            # Send keepalive comment if no events were emitted recently
            if (time.monotonic() - last_event_time) >= SSE_KEEPALIVE_INTERVAL_SECONDS:
                yield ": keepalive\n\n"
                last_event_time = time.monotonic()

            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse_event(event: str, data: dict, event_id: str | int | None = None) -> str:
    """Format a server-sent event.

    If event_id is provided, includes an ``id:`` field so clients can
    reconnect using ``Last-Event-ID`` and resume from where they left off.
    """
    parts = []
    if event_id is not None:
        parts.append(f"id: {event_id}")
    parts.append(f"event: {event}")
    parts.append(f"data: {json.dumps(data, default=str)}")
    return "\n".join(parts) + "\n\n"


@router.get("/events")
async def global_event_stream(request: Request) -> StreamingResponse:
    """Stream global real-time events via SSE.

    Broadcasts run lifecycle, step progress, and DLQ events to
    connected dashboard clients. When auth is enabled, events are
    filtered to only show runs belonging to the authenticated tenant.

    Supports ``Last-Event-ID`` header for reconnection: if the client
    sends this header, only events with a sequence number greater than
    the provided value will be delivered (note: only future events are
    guaranteed since older events are not buffered).

    Event types: run.started, run.completed, run.failed,
    step.started, step.completed, step.failed, dlq.new
    """
    from sandcastle.engine.events import event_bus

    tenant_id = get_tenant_id(request)

    # Parse Last-Event-ID for SSE reconnection support
    last_event_id_header = request.headers.get("Last-Event-ID")
    last_seen_seq = 0
    if last_event_id_header:
        try:
            last_seen_seq = int(last_event_id_header)
        except (ValueError, TypeError):
            pass  # Ignore malformed Last-Event-ID

    try:
        queue = await event_bus.subscribe()
    except RuntimeError:
        raise HTTPException(
            status_code=503,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="TOO_MANY_CONNECTIONS",
                    message="Too many concurrent event stream connections",
                )
            ).model_dump(),
        )

    # LRU cache of run_id -> tenant_id to avoid repeated DB lookups (bounded)
    _TENANT_CACHE_MAX = 1000
    tenant_cache: dict[str, str | None] = {}

    async def _run_belongs_to_tenant(run_id: str) -> bool:
        """Check if a run belongs to the authenticated tenant."""
        if tenant_id is None:
            return True  # No auth = see everything
        if run_id in tenant_cache:
            return tenant_cache[run_id] == tenant_id
        try:
            async with async_session() as session:
                run = await session.get(Run, uuid.UUID(run_id))
                run_tenant = run.tenant_id if run else None
                # Evict oldest entries if cache grows too large
                if len(tenant_cache) >= _TENANT_CACHE_MAX:
                    oldest_key = next(iter(tenant_cache))
                    del tenant_cache[oldest_key]
                tenant_cache[run_id] = run_tenant
                return run_tenant == tenant_id
        except Exception:
            return False

    async def event_generator():
        try:
            while True:
                # Check if the client has disconnected to avoid resource leaks
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=float(SSE_KEEPALIVE_INTERVAL_SECONDS)
                    )

                    # Skip events the client has already seen (SSE reconnection)
                    event_seq = event.get("seq", 0)
                    if event_seq <= last_seen_seq:
                        continue

                    # Tenant filter: skip events for runs not owned by this tenant
                    run_id = event.get("data", {}).get("run_id")
                    if run_id and not await _run_belongs_to_tenant(run_id):
                        continue
                    yield _sse_event(
                        event["type"], event["data"], event_id=event_seq
                    )
                except asyncio.TimeoutError:
                    # Send keepalive comment to prevent connection timeout
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            await event_bus.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# Note: response_model intentionally omitted from decorator; the return type
# annotation (-> ApiResponse) is used instead to allow flexible data payloads.
@router.get("/runs")
async def list_runs(
    request: Request,
    status: str | None = Query(None, description="Filter by status"),
    workflow: str | None = Query(None, description="Filter by workflow name"),
    since: datetime | None = Query(None, description="Filter runs created after this datetime"),
    until: datetime | None = Query(None, description="Filter runs created before this datetime"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List workflow runs with filters and pagination."""
    if status:
        valid = {s.value for s in RunStatus}
        if status not in valid:
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="INVALID_STATUS",
                        message=f"Invalid status '{status}'. Valid: {', '.join(sorted(valid))}",
                    )
                ).model_dump(),
            )
    tenant_id = get_tenant_id(request)

    async with async_session() as session:
        base_filter = select(Run)
        count_filter = select(func.count(Run.id))

        # Always apply tenant filter when auth is enabled
        base_filter = _apply_tenant_filter(base_filter, tenant_id, Run.tenant_id)
        count_filter = _apply_tenant_filter(count_filter, tenant_id, Run.tenant_id)

        if status:
            base_filter = base_filter.where(Run.status == status)
            count_filter = count_filter.where(Run.status == status)
        if workflow:
            base_filter = base_filter.where(Run.workflow_name == workflow)
            count_filter = count_filter.where(Run.workflow_name == workflow)
        if since:
            base_filter = base_filter.where(Run.created_at >= since)
            count_filter = count_filter.where(Run.created_at >= since)
        if until:
            base_filter = base_filter.where(Run.created_at <= until)
            count_filter = count_filter.where(Run.created_at <= until)

        total = await session.scalar(count_filter)

        stmt = base_filter.order_by(Run.created_at.desc()).offset(offset).limit(limit)
        result = await session.execute(stmt)
        runs = result.scalars().all()

    items = [
        RunListItem(
            run_id=str(r.id),
            workflow_name=r.workflow_name,
            status=r.status.value if hasattr(r.status, "value") else r.status,
            total_cost_usd=r.total_cost_usd,
            started_at=r.started_at,
            completed_at=r.completed_at,
            parent_run_id=str(r.parent_run_id) if r.parent_run_id else None,
        )
        for r in runs
    ]

    return ApiResponse(
        data=items,
        meta=PaginationMeta(total=total or 0, limit=limit, offset=offset),
    )


# --- Shareable run permalinks ---


@router.post("/runs/{run_id}/share")
async def share_run(run_id: str, req: Request) -> ApiResponse:
    """Mint a public, scrubbed share permalink for a run (owner only).

    Returns the relative share path /api/r/{token}. The token is stable across calls so
    re-sharing the same run returns the same link.
    """
    import secrets as _secrets

    tenant_id = get_tenant_id(req)
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid run ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        stmt = select(Run).where(Run.id == run_uuid)
        stmt = _apply_tenant_filter(stmt, tenant_id, Run.tenant_id)
        run = (await session.execute(stmt)).scalar_one_or_none()
        if not run:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message=f"Run '{run_id}' not found")
                ).model_dump(),
            )
        if not run.share_token:
            # Self-describing token "<hex-unix-expiry>.<random>": embeds a TTL so a
            # leaked /api/r/ link stops resolving after 30 days. The DB lookup stays
            # an exact match on the full string, so a tampered expiry prefix simply
            # fails to match (404). Legacy tokens without "." never expire.
            _expiry = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())
            run.share_token = f"{_expiry:x}.{_secrets.token_urlsafe(24)}"
            await session.commit()
        token = run.share_token

    return ApiResponse(data={"share_token": token, "share_path": f"/api/r/{token}"})


@router.delete("/runs/{run_id}/share")
async def unshare_run(run_id: str, req: Request) -> ApiResponse:
    """Revoke a run's public share permalink (owner only)."""
    tenant_id = get_tenant_id(req)
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid run ID format")
            ).model_dump(),
        )
    async with async_session() as session:
        stmt = select(Run).where(Run.id == run_uuid)
        stmt = _apply_tenant_filter(stmt, tenant_id, Run.tenant_id)
        run = (await session.execute(stmt)).scalar_one_or_none()
        if not run:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message=f"Run '{run_id}' not found")
                ).model_dump(),
            )
        run.share_token = None
        await session.commit()
    return ApiResponse(data={"revoked": True})


@router.get("/r/{token}")
async def view_shared_run(token: str, req: Request) -> Response:
    """Public, auth-bypassed view of a shared run as a self-contained, scrubbed HTML page.

    Secrets and PII are redacted before rendering (redact-by-default). The page is only
    reachable when the owner has minted a token via POST /runs/{id}/share.
    """
    from sandcastle.api.share import render_run_page

    if not token or len(token) > 128:
        raise HTTPException(status_code=404, detail="Not found")

    async with async_session() as session:
        stmt = (
            select(Run)
            .options(selectinload(Run.steps))
            .where(Run.share_token == token)
        )
        run = (await session.execute(stmt)).scalar_one_or_none()

    if not run:
        return Response(
            content="<!doctype html><title>Not found</title><h1>This run link is not available.</h1>",
            media_type="text/html",
            status_code=404,
        )

    # Enforce the share-token TTL (self-describing "<hex-expiry>.<random>" tokens).
    # Legacy tokens without a "." prefix never expire.
    if "." in token:
        try:
            _expiry = int(token.split(".", 1)[0], 16)
        except ValueError:
            _expiry = None
        if _expiry is not None and _expiry < int(datetime.now(timezone.utc).timestamp()):
            return Response(
                content="<!doctype html><title>Link expired</title>"
                "<h1>This run link has expired.</h1>",
                media_type="text/html",
                status_code=410,
            )

    html = render_run_page(run)
    return Response(content=html, media_type="text/html", status_code=200)


# --- Cancel ---


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, req: Request) -> ApiResponse:
    """Cancel a running workflow. Sets a Redis flag checked by the executor."""
    from sqlalchemy import update as sa_update

    tenant_id = get_tenant_id(req)

    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid run ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        # Read the run to check existence and current status
        stmt = select(Run).where(Run.id == run_uuid)
        stmt = _apply_tenant_filter(stmt, tenant_id, Run.tenant_id)
        result = await session.execute(stmt)
        run = result.scalar_one_or_none()

        if not run:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message=f"Run '{run_id}' not found")
                ).model_dump(),
            )

        run_status = run.status.value if hasattr(run.status, "value") else run.status
        if run_status not in ("queued", "running"):
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="INVALID_STATUS",
                        message=f"Cannot cancel run with status '{run_status}'",
                    )
                ).model_dump(),
            )

        # Atomic UPDATE: only changes status if still in a cancellable state.
        # This prevents TOCTOU races where the run completes between the
        # SELECT above and this UPDATE.
        cancel_stmt = (
            sa_update(Run)
            .where(
                Run.id == run_uuid,
                Run.status.in_([RunStatus.QUEUED, RunStatus.RUNNING]),
            )
            .values(
                status=RunStatus.CANCELLED,
                completed_at=datetime.now(timezone.utc),
                error="Cancelled by user",
            )
        )
        await session.execute(cancel_stmt)
        await session.commit()

    # Set cancel flag (Redis or in-memory).
    # Reuse the executor's shared Redis pool to avoid creating a new connection per cancel.
    if settings.redis_url:
        try:
            from sandcastle.engine.executor import _get_redis

            r = await _get_redis()
            await r.set(f"cancel:{run_id}", "1", ex=3600)  # 1h TTL
        except Exception as e:
            logger.error(f"Could not set cancel flag in Redis: {e}")
    else:
        from sandcastle.engine.executor import cancel_run_local

        await cancel_run_local(run_id)

    try:
        from sandcastle.engine.audit import append_audit_event
        async with async_session() as _as:
            await append_audit_event(session=_as, event_type="run.cancelled", run_id=run_id, actor_id=get_tenant_id(req) or "system", payload={"run_id": run_id}, actor_key_prefix=req.headers.get("X-Api-Key", "")[:8] or None, source_ip=req.client.host if req.client else None)
            await _as.commit()
    except Exception as _ae:
        logger.warning("Audit run.cancelled failed: %s", _ae)
    return ApiResponse(
        data={"cancelled": True, "run_id": run_id},
    )


# --- Emergency Stop ---


@router.post("/admin/emergency-stop")
async def emergency_stop(req: Request) -> ApiResponse:
    """Global emergency stop - cancel ALL running and queued runs immediately.

    Sets a Redis key ``emergency_stop:global`` (TTL 24h) that the executor
    checks on every cancel-check loop iteration.  In local mode (no Redis) an
    in-memory flag is used instead.

    Returns the number of runs that were transitioned to CANCELLED in the DB.
    Requires admin privileges.
    """
    from sqlalchemy import update as sa_update

    _require_admin(req)

    # Bulk-cancel all active runs in the database.
    async with async_session() as session:
        # Count active runs first (used for the response).
        count_stmt = select(func.count(Run.id)).where(
            Run.status.in_([RunStatus.RUNNING, RunStatus.QUEUED])
        )
        cancelled_count = (await session.execute(count_stmt)).scalar_one() or 0

        cancel_stmt = (
            sa_update(Run)
            .where(Run.status.in_([RunStatus.RUNNING, RunStatus.QUEUED]))
            .values(
                status=RunStatus.CANCELLED,
                completed_at=datetime.now(timezone.utc),
                error="Cancelled by global emergency stop",
            )
        )
        await session.execute(cancel_stmt)
        await session.commit()

    # Set the global stop flag (Redis or in-memory).
    if settings.redis_url:
        try:
            from sandcastle.engine.executor import _get_redis

            r = await _get_redis()
            await r.set("emergency_stop:global", "1", ex=86400)  # 24h TTL
        except Exception as e:
            logger.error(f"Could not set emergency stop flag in Redis: {e}")
    else:
        from sandcastle.engine.executor import set_emergency_stop_local

        await set_emergency_stop_local()

    logger.warning(
        "Emergency stop activated by admin - cancelled %d run(s)", cancelled_count
    )

    return ApiResponse(
        data=EmergencyStopResponse(
            cancelled_count=cancelled_count,
            active=True,
        ).model_dump(),
    )


# --- Delete Run ---


@router.delete("/runs/{run_id}")
async def delete_run(run_id: str, req: Request) -> ApiResponse:
    """Delete a run and its related data (steps, checkpoints)."""
    tenant_id = get_tenant_id(req)

    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid run ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        stmt = select(Run).where(Run.id == run_uuid)
        stmt = _apply_tenant_filter(stmt, tenant_id, Run.tenant_id)
        result = await session.execute(stmt)
        run = result.scalar_one_or_none()

        if not run:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message=f"Run '{run_id}' not found")
                ).model_dump(),
            )

        run_status = run.status.value if hasattr(run.status, "value") else run.status
        if run_status in ("queued", "running"):
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="INVALID_STATUS",
                        message="Cannot delete an active run. Cancel it first.",
                    )
                ).model_dump(),
            )

        # Delete related records then the run itself
        from sqlalchemy import delete as sa_delete

        from sandcastle.models.db import RunStep

        for model in [
            ApprovalRequest,
            PolicyViolation,
            RoutingDecision,
            RunStep,
            RunCheckpoint,
            DeadLetterItem,
        ]:
            await session.execute(sa_delete(model).where(model.run_id == run_uuid))

        await session.delete(run)
        await session.commit()

    return ApiResponse(data={"deleted": True, "run_id": run_id})


# --- Replay / Fork (Time Machine) ---


@router.post("/runs/{run_id}/replay")
async def replay_run(run_id: str, request: ReplayRequest, req: Request) -> ApiResponse:
    """Replay a run from a specific step using saved checkpoints."""
    await execution_limiter.check(req)
    tenant_id = get_tenant_id(req)

    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid run ID format")
            ).model_dump(),
        )

    # Load the original run
    async with async_session() as session:
        stmt = select(Run).where(Run.id == run_uuid)
        stmt = _apply_tenant_filter(stmt, tenant_id, Run.tenant_id)
        result = await session.execute(stmt)
        original_run = result.scalar_one_or_none()

    if not original_run:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message=f"Run '{run_id}' not found")
            ).model_dump(),
        )

    # Load workflow YAML from versioned DB if the original run has a version,
    # otherwise fall back to current disk file.
    try:
        yaml_content = await _load_versioned_workflow_yaml(
            original_run.workflow_name, original_run.workflow_version
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="WORKFLOW_NOT_FOUND",
                    message=f"Workflow '{original_run.workflow_name}' not found on disk",
                )
            ).model_dump(),
        )

    # Validate from_step exists in the workflow
    try:
        wf_def = parse_yaml_string(yaml_content)
        valid_step_ids = {s.id for s in wf_def.steps}
    except Exception:
        valid_step_ids = set()
    if valid_step_ids and request.from_step not in valid_step_ids:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_STEP",
                    message=f"Step '{request.from_step}' not found in workflow "
                    f"'{original_run.workflow_name}'",
                )
            ).model_dump(),
        )

    # Find the checkpoint before the requested step
    async with async_session() as session:
        checkpoint_stmt = (
            select(RunCheckpoint)
            .where(RunCheckpoint.run_id == run_uuid)
            .order_by(RunCheckpoint.stage_index.desc())
        )
        result = await session.execute(checkpoint_stmt)
        checkpoints = result.scalars().all()

    # Find the newest checkpoint where from_step is NOT yet in step_outputs.
    # If no such checkpoint exists (from_step is the first step), use empty
    # context so the entire workflow replays from the beginning.
    target_checkpoint = None
    for cp in checkpoints:
        snapshot = cp.context_snapshot
        if request.from_step not in snapshot.get("step_outputs", {}):
            target_checkpoint = cp
            break

    initial_context = target_checkpoint.context_snapshot if target_checkpoint else None
    skip_steps = set(initial_context["step_outputs"].keys()) if initial_context else set()
    # Safety: never skip the step we're replaying from
    skip_steps.discard(request.from_step)

    # Create new run
    new_run_id = str(uuid.uuid4())
    async with async_session() as session:
        new_run = Run(
            id=uuid.UUID(new_run_id),
            workflow_name=original_run.workflow_name,
            status=RunStatus.QUEUED,
            input_data=original_run.input_data,
            callback_url=original_run.callback_url,
            tenant_id=tenant_id,
            parent_run_id=run_uuid,
            replay_from_step=request.from_step,
            max_cost_usd=original_run.max_cost_usd,
            workflow_version=original_run.workflow_version,
        )
        session.add(new_run)
        await session.commit()

    # Enqueue with replay context
    try:
        await enqueue_workflow(
            yaml_content,
            original_run.input_data or {},
            new_run_id,
            max_cost_usd=original_run.max_cost_usd,
            initial_context=initial_context,
            skip_steps=list(skip_steps),
        )
    except Exception as e:
        async with async_session() as session:
            db_run = await session.get(Run, uuid.UUID(new_run_id))
            if db_run:
                db_run.status = RunStatus.FAILED
                db_run.error = f"Failed to enqueue replay: {e}"
                db_run.completed_at = datetime.now(timezone.utc)
                await session.commit()
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(code="QUEUE_ERROR", message="Could not enqueue replay")
            ).model_dump(),
        )

    return ApiResponse(
        data=RunReplayResponse(
            new_run_id=new_run_id,
            parent_run_id=run_id,
            replay_from_step=request.from_step,
            status="queued",
        ),
    )


@router.post("/runs/{run_id}/fork")
async def fork_run(run_id: str, request: ForkRequest, req: Request) -> ApiResponse:
    """Fork a run from a specific step with overrides (prompt, model, etc.)."""
    await execution_limiter.check(req)
    tenant_id = get_tenant_id(req)

    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid run ID format")
            ).model_dump(),
        )

    # Load the original run
    async with async_session() as session:
        stmt = select(Run).where(Run.id == run_uuid)
        stmt = _apply_tenant_filter(stmt, tenant_id, Run.tenant_id)
        result = await session.execute(stmt)
        original_run = result.scalar_one_or_none()

    if not original_run:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message=f"Run '{run_id}' not found")
            ).model_dump(),
        )

    # Load workflow YAML from versioned DB if the original run has a version,
    # otherwise fall back to current disk file.
    try:
        yaml_content = await _load_versioned_workflow_yaml(
            original_run.workflow_name, original_run.workflow_version
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="WORKFLOW_NOT_FOUND",
                    message=f"Workflow '{original_run.workflow_name}' not found on disk",
                )
            ).model_dump(),
        )

    # Validate from_step exists in the workflow
    try:
        wf_def = parse_yaml_string(yaml_content)
        valid_step_ids = {s.id for s in wf_def.steps}
    except Exception:
        valid_step_ids = set()
    if valid_step_ids and request.from_step not in valid_step_ids:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_STEP",
                    message=f"Step '{request.from_step}' not found in workflow "
                    f"'{original_run.workflow_name}'",
                )
            ).model_dump(),
        )

    # Find the checkpoint before the requested step
    async with async_session() as session:
        checkpoint_stmt = (
            select(RunCheckpoint)
            .where(RunCheckpoint.run_id == run_uuid)
            .order_by(RunCheckpoint.stage_index.desc())
        )
        result = await session.execute(checkpoint_stmt)
        checkpoints = result.scalars().all()

    # Find the newest checkpoint where from_step is NOT yet in step_outputs
    target_checkpoint = None
    for cp in checkpoints:
        snapshot = cp.context_snapshot
        if request.from_step not in snapshot.get("step_outputs", {}):
            target_checkpoint = cp
            break

    initial_context = target_checkpoint.context_snapshot if target_checkpoint else None
    skip_steps = set(initial_context["step_outputs"].keys()) if initial_context else set()
    # Safety: never skip the step we're forking from
    skip_steps.discard(request.from_step)

    # Create new run with fork metadata
    new_run_id = str(uuid.uuid4())
    async with async_session() as session:
        new_run = Run(
            id=uuid.UUID(new_run_id),
            workflow_name=original_run.workflow_name,
            status=RunStatus.QUEUED,
            input_data=original_run.input_data,
            callback_url=original_run.callback_url,
            tenant_id=tenant_id,
            parent_run_id=run_uuid,
            replay_from_step=request.from_step,
            fork_changes=request.changes,
            max_cost_usd=original_run.max_cost_usd,
            workflow_version=original_run.workflow_version,
        )
        session.add(new_run)
        await session.commit()

    # Step overrides for the fork target step
    step_overrides = {request.from_step: request.changes} if request.changes else None

    try:
        await enqueue_workflow(
            yaml_content,
            original_run.input_data or {},
            new_run_id,
            max_cost_usd=original_run.max_cost_usd,
            initial_context=initial_context,
            skip_steps=list(skip_steps),
            step_overrides=step_overrides,
        )
    except Exception as e:
        async with async_session() as session:
            db_run = await session.get(Run, uuid.UUID(new_run_id))
            if db_run:
                db_run.status = RunStatus.FAILED
                db_run.error = f"Failed to enqueue fork: {e}"
                db_run.completed_at = datetime.now(timezone.utc)
                await session.commit()
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(code="QUEUE_ERROR", message="Could not enqueue fork")
            ).model_dump(),
        )

    return ApiResponse(
        data=RunForkResponse(
            new_run_id=new_run_id,
            parent_run_id=run_id,
            fork_from_step=request.from_step,
            changes=request.changes,
            status="queued",
        ),
    )


# --- Schedules ---


@router.post("/schedules", status_code=201)
async def create_schedule(request: ScheduleCreateRequest, req: Request) -> ApiResponse:
    """Create a scheduled workflow execution."""
    tenant_id = get_tenant_id(req)

    # Validate that the workflow exists
    try:
        yaml_content = _load_workflow_yaml(request.workflow_name)
    except FileNotFoundError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_WORKFLOW",
                    message=f"Workflow '{request.workflow_name}' not found",
                )
            ).model_dump(),
        )

    # Validate input_data against workflow input_schema (always check,
    # even for empty {} -- a schema with required fields must reject it)
    workflow = parse_yaml_string(yaml_content)
    input_to_validate = request.input_data if request.input_data else {}
    validation_errors = _validate_workflow_input(
        input_to_validate, workflow.input_schema
    )
    if validation_errors:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_INPUT",
                    message="; ".join(validation_errors),
                )
            ).model_dump(),
        )

    # Validate cron expression before saving
    try:
        from apscheduler.triggers.cron import CronTrigger

        CronTrigger.from_crontab(request.cron_expression)
    except (ValueError, KeyError) as e:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_CRON",
                    message=f"Invalid cron expression: {e}",
                )
            ).model_dump(),
        )

    schedule_id = str(uuid.uuid4())

    try:
        async with async_session() as session:
            db_schedule = Schedule(
                id=uuid.UUID(schedule_id),
                workflow_name=request.workflow_name,
                cron_expression=request.cron_expression,
                input_data=request.input_data,
                notify=request.notify,
                enabled=request.enabled,
                tenant_id=tenant_id,
            )
            session.add(db_schedule)

            # Register with APScheduler before commit for atomicity
            scheduler_registered = False
            if request.enabled:
                try:
                    add_schedule(
                        schedule_id=schedule_id,
                        cron_expression=request.cron_expression,
                        workflow_name=request.workflow_name,
                        input_data=request.input_data,
                    )
                    scheduler_registered = True
                except Exception as exc:
                    await session.rollback()
                    raise HTTPException(
                        status_code=500,
                        detail=ApiResponse(
                            error=ErrorResponse(
                                code="SCHEDULER_ERROR",
                                message="Could not register schedule",
                            )
                        ).model_dump(),
                    ) from exc

            try:
                await session.commit()
            except Exception as e:
                # Compensate: remove scheduler job if commit fails
                if scheduler_registered:
                    try:
                        remove_schedule(schedule_id)
                    except Exception:
                        pass
                raise HTTPException(
                    status_code=500,
                    detail=ApiResponse(
                        error=ErrorResponse(
                            code="DB_ERROR",
                            message="Could not create schedule",
                        )
                    ).model_dump(),
                ) from e
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="DB_ERROR",
                    message="Could not create schedule",
                )
            ).model_dump(),
        ) from e

    return ApiResponse(
        data=ScheduleResponse(
            id=schedule_id,
            workflow_name=request.workflow_name,
            cron_expression=request.cron_expression,
            input_data=request.input_data,
            enabled=request.enabled,
        )
    )


# Note: response_model intentionally omitted; return type annotation provides typing.
@router.get("/schedules")
async def list_schedules(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List all workflow schedules."""
    tenant_id = get_tenant_id(request)

    async with async_session() as session:
        count_stmt = select(func.count(Schedule.id))
        count_stmt = _apply_tenant_filter(count_stmt, tenant_id, Schedule.tenant_id)
        total = await session.scalar(count_stmt)

        stmt = select(Schedule).order_by(Schedule.created_at.desc()).offset(offset).limit(limit)
        stmt = _apply_tenant_filter(stmt, tenant_id, Schedule.tenant_id)
        result = await session.execute(stmt)
        schedules = result.scalars().all()

    # Compute enriched schedule data (last run info, success rate, status)
    items: list[ScheduleResponse] = []
    for s in schedules:
        last_run_at = None
        last_run_status = None
        success_rate = 0.0
        sched_status = "paused" if not s.enabled else "active"

        if s.last_run_id:
            async with async_session() as run_sess:
                last_run = await run_sess.get(Run, s.last_run_id)
                if last_run:
                    last_run_at = last_run.started_at or last_run.created_at
                    last_run_status = last_run.status.value if hasattr(last_run.status, "value") else str(last_run.status)
                    if s.enabled and last_run_status == "failed":
                        sched_status = "failing"

                # Compute success rate from recent runs for this workflow
                recent_q = (
                    select(
                        func.count(Run.id).label("total"),
                        func.count(
                            case(
                                (Run.status == RunStatus.COMPLETED, Run.id),
                                else_=None,
                            )
                        ).label("passed"),
                    )
                    .where(
                        Run.workflow_name == s.workflow_name,
                        Run.created_at >= datetime.now(timezone.utc) - timedelta(days=30),
                    )
                )
                recent_q = _apply_tenant_filter(recent_q, tenant_id, Run.tenant_id)
                row = (await run_sess.execute(recent_q)).one_or_none()
                if row and row.total > 0:
                    success_rate = round(row.passed / row.total, 2)

        # Compute next_run_at from cron
        next_run_at = None
        if s.enabled:
            try:
                from apscheduler.triggers.cron import CronTrigger
                trigger = CronTrigger.from_crontab(s.cron_expression)
                next_fire = trigger.get_next_fire_time(None, datetime.now(timezone.utc))
                if next_fire:
                    next_run_at = next_fire
            except Exception:
                pass

        items.append(
            ScheduleResponse(
                id=str(s.id),
                workflow_name=s.workflow_name,
                cron_expression=s.cron_expression,
                input_data=s.input_data or {},
                enabled=s.enabled,
                last_run_id=str(s.last_run_id) if s.last_run_id else None,
                last_run_at=last_run_at,
                last_run_status=last_run_status,
                next_run_at=next_run_at,
                success_rate=success_rate,
                status=sched_status,
                created_at=s.created_at,
            )
        )

    return ApiResponse(
        data=items,
        meta=PaginationMeta(total=total or 0, limit=limit, offset=offset),
    )


@router.get("/schedules/{schedule_id}")
async def get_schedule(
    schedule_id: str,
    req: Request,
) -> ApiResponse:
    """Get a single schedule by ID."""
    tenant_id = get_tenant_id(req)

    try:
        schedule_uuid = uuid.UUID(schedule_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid schedule ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        stmt = select(Schedule).where(Schedule.id == schedule_uuid)
        stmt = _apply_tenant_filter(stmt, tenant_id, Schedule.tenant_id)
        result = await session.execute(stmt)
        schedule = result.scalar_one_or_none()
        if not schedule:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="NOT_FOUND",
                        message=f"Schedule '{schedule_id}' not found",
                    )
                ).model_dump(),
            )

        last_run_at = None
        last_run_status = None
        if schedule.last_run_id:
            last_run = await session.get(Run, schedule.last_run_id)
            if last_run:
                last_run_at = last_run.started_at or last_run.created_at
                last_run_status = (
                    last_run.status.value
                    if hasattr(last_run.status, "value")
                    else str(last_run.status)
                )

        next_run_at = None
        if schedule.enabled:
            try:
                from apscheduler.triggers.cron import CronTrigger

                trigger = CronTrigger.from_crontab(schedule.cron_expression)
                next_fire = trigger.get_next_fire_time(None, datetime.now(timezone.utc))
                if next_fire:
                    next_run_at = next_fire
            except Exception:
                pass

        sched_status = "paused" if not schedule.enabled else "active"
        if schedule.enabled and last_run_status == "failed":
            sched_status = "failing"

    return ApiResponse(
        data=ScheduleResponse(
            id=str(schedule.id),
            workflow_name=schedule.workflow_name,
            cron_expression=schedule.cron_expression,
            input_data=schedule.input_data or {},
            enabled=schedule.enabled,
            last_run_id=str(schedule.last_run_id) if schedule.last_run_id else None,
            last_run_at=last_run_at,
            last_run_status=last_run_status,
            next_run_at=next_run_at,
            success_rate=0.0,
            status=sched_status,
            created_at=schedule.created_at,
        )
    )


@router.patch("/schedules/{schedule_id}")
async def update_schedule(
    schedule_id: str,
    request: ScheduleUpdateRequest,
    req: Request,
) -> ApiResponse:
    """Update a schedule (cron, enabled, input_data)."""
    tenant_id = get_tenant_id(req)

    try:
        schedule_uuid = uuid.UUID(schedule_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid schedule ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        stmt = select(Schedule).where(Schedule.id == schedule_uuid)
        stmt = _apply_tenant_filter(stmt, tenant_id, Schedule.tenant_id)
        result = await session.execute(stmt)
        schedule = result.scalar_one_or_none()
        if not schedule:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="NOT_FOUND",
                        message=f"Schedule '{schedule_id}' not found",
                    )
                ).model_dump(),
            )
        # Validate cron before committing
        if request.cron_expression is not None:
            try:
                from apscheduler.triggers.cron import CronTrigger

                CronTrigger.from_crontab(request.cron_expression)
            except (ValueError, KeyError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail=ApiResponse(
                        error=ErrorResponse(
                            code="INVALID_CRON",
                            message=f"Invalid cron expression: {exc}",
                        )
                    ).model_dump(),
                ) from exc

        # Validate input_data against workflow input_schema if provided
        if request.input_data is not None:
            try:
                yaml_content = _load_workflow_yaml(schedule.workflow_name)
                workflow = parse_yaml_string(yaml_content)
                validation_errors = _validate_workflow_input(
                    request.input_data, workflow.input_schema
                )
                if validation_errors:
                    raise HTTPException(
                        status_code=400,
                        detail=ApiResponse(
                            error=ErrorResponse(
                                code="INVALID_INPUT",
                                message="; ".join(validation_errors),
                            )
                        ).model_dump(),
                    )
            except HTTPException:
                raise
            except Exception:
                pass  # Workflow may not exist yet during migration

        # Snapshot old state for rollback compensation
        old_enabled = schedule.enabled
        old_cron = schedule.cron_expression
        old_workflow = schedule.workflow_name
        old_input = schedule.input_data

        if request.enabled is not None:
            schedule.enabled = request.enabled
        if request.cron_expression is not None:
            schedule.cron_expression = request.cron_expression
        if request.input_data is not None:
            schedule.input_data = request.input_data

        # Register with APScheduler BEFORE commit so DB stays in sync
        try:
            if schedule.enabled:
                add_schedule(
                    schedule_id=schedule_id,
                    cron_expression=schedule.cron_expression,
                    workflow_name=schedule.workflow_name,
                    input_data=schedule.input_data,
                )
            else:
                remove_schedule(schedule_id)
        except Exception as exc:
            await session.rollback()
            raise HTTPException(
                status_code=500,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="SCHEDULER_ERROR",
                        message="Failed to update scheduler",
                    )
                ).model_dump(),
            ) from exc

        try:
            await session.commit()
        except Exception as exc:
            # Compensate: revert scheduler to old state
            try:
                if old_enabled:
                    add_schedule(
                        schedule_id=schedule_id,
                        cron_expression=old_cron,
                        workflow_name=old_workflow,
                        input_data=old_input,
                    )
                else:
                    remove_schedule(schedule_id)
            except Exception:
                pass
            raise HTTPException(
                status_code=500,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="DB_ERROR",
                        message="Failed to commit schedule update",
                    )
                ).model_dump(),
            ) from exc

    return ApiResponse(
        data=ScheduleResponse(
            id=str(schedule.id),
            workflow_name=schedule.workflow_name,
            cron_expression=schedule.cron_expression,
            input_data=schedule.input_data or {},
            enabled=schedule.enabled,
            last_run_id=str(schedule.last_run_id) if schedule.last_run_id else None,
            created_at=schedule.created_at,
        )
    )


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str, request: Request) -> ApiResponse:
    """Delete a workflow schedule."""
    tenant_id = get_tenant_id(request)

    try:
        schedule_uuid = uuid.UUID(schedule_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid schedule ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        stmt = select(Schedule).where(Schedule.id == schedule_uuid)
        stmt = _apply_tenant_filter(stmt, tenant_id, Schedule.tenant_id)
        result = await session.execute(stmt)
        schedule = result.scalar_one_or_none()
        if not schedule:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="NOT_FOUND",
                        message=f"Schedule '{schedule_id}' not found",
                    )
                ).model_dump(),
            )

        # Remove from scheduler BEFORE deleting DB record to prevent
        # orphaned scheduler jobs if DB delete fails.
        try:
            remove_schedule(schedule_id)
        except Exception:
            pass  # scheduler may not have the job if it was disabled

        await session.delete(schedule)
        await session.commit()

    return ApiResponse(data={"deleted": True, "id": schedule_id})


@router.post("/schedules/{schedule_id}/trigger", status_code=202)
async def trigger_schedule(schedule_id: str, req: Request) -> ApiResponse:
    """Trigger an immediate run for a schedule, bypassing the cron timer."""
    await execution_limiter.check(req)
    tenant_id = get_tenant_id(req)

    try:
        schedule_uuid = uuid.UUID(schedule_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid schedule ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        stmt = select(Schedule).where(Schedule.id == schedule_uuid)
        stmt = _apply_tenant_filter(stmt, tenant_id, Schedule.tenant_id)
        result = await session.execute(stmt)
        schedule = result.scalar_one_or_none()
        if not schedule:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="NOT_FOUND",
                        message=f"Schedule '{schedule_id}' not found",
                    )
                ).model_dump(),
            )

        if not schedule.enabled:
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="SCHEDULE_DISABLED",
                        message="Cannot trigger a disabled schedule. Enable it first.",
                    )
                ).model_dump(),
            )

        # Load the workflow YAML
        try:
            yaml_content = _load_workflow_yaml(schedule.workflow_name)
        except FileNotFoundError:
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="INVALID_WORKFLOW",
                        message=f"Workflow '{schedule.workflow_name}' not found on disk",
                    )
                ).model_dump(),
            )

        workflow = parse_yaml_string(yaml_content)
        run_id = str(uuid.uuid4())

        # Use the schedule's original tenant_id for the run, not the caller's.
        # If the schedule has no tenant (global), require admin auth.
        run_tenant_id = schedule.tenant_id
        if run_tenant_id is None and settings.auth_required:
            _require_admin(req)

        # Resolve budget from the schedule owner's API key
        max_cost: float | None = None
        if run_tenant_id and settings.auth_required:
            budget_stmt = (
                select(ApiKey.max_cost_per_run_usd)
                .where(
                    ApiKey.tenant_id == run_tenant_id,
                    ApiKey.is_active.is_(True),
                )
                .limit(1)
            )
            max_cost = await session.scalar(budget_stmt)

        # Create a queued run
        db_run = Run(
            id=uuid.UUID(run_id),
            workflow_name=workflow.name,
            status=RunStatus.QUEUED,
            input_data=schedule.input_data or {},
            tenant_id=run_tenant_id,
            risk_level=getattr(workflow, "risk_level", "minimal"),
            max_cost_usd=max_cost,
        )
        session.add(db_run)

        # Update schedule's last_run_id
        schedule.last_run_id = uuid.UUID(run_id)
        await session.commit()

    # Enqueue the job outside the session
    try:
        await enqueue_workflow(yaml_content, schedule.input_data or {}, run_id,
                               max_cost_usd=max_cost)
    except Exception as e:
        logger.error("Failed to enqueue triggered schedule run: %s", e)
        # Mark run as failed so it does not stay stuck
        try:
            async with async_session() as session:
                db_run = await session.get(Run, uuid.UUID(run_id))
                if db_run:
                    db_run.status = RunStatus.FAILED
                    db_run.error = f"Enqueue failed: {e}"
                    await session.commit()
        except Exception:
            pass
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(code="ENQUEUE_ERROR", message="Failed to enqueue workflow run")
            ).model_dump(),
        )

    return ApiResponse(data={"run_id": run_id, "status": "queued"})


# --- Dead Letter Queue ---


@router.get("/dead-letter")
async def list_dead_letter(
    request: Request,
    resolved: bool = Query(False, description="Include resolved items"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List dead letter queue items."""
    tenant_id = get_tenant_id(request)

    async with async_session() as session:
        base = select(DeadLetterItem)
        count_base = select(func.count(DeadLetterItem.id))

        # Tenant isolation via join on parent Run
        if settings.auth_required and tenant_id is not None:
            join_cond = DeadLetterItem.run_id == Run.id
            base = base.join(Run, join_cond).where(Run.tenant_id == tenant_id)
            count_base = count_base.join(Run, join_cond).where(Run.tenant_id == tenant_id)

        if not resolved:
            base = base.where(DeadLetterItem.resolved_at.is_(None))
            count_base = count_base.where(DeadLetterItem.resolved_at.is_(None))

        total = await session.scalar(count_base)

        stmt = base.order_by(DeadLetterItem.created_at.desc()).offset(offset).limit(limit)
        result = await session.execute(stmt)
        items = result.scalars().all()

    data = [
        DeadLetterItemResponse(
            id=str(item.id),
            run_id=str(item.run_id),
            step_id=item.step_id,
            parallel_index=item.parallel_index,
            error=item.error,
            input_data=item.input_data,
            attempts=item.attempts,
            created_at=item.created_at,
            resolved_at=item.resolved_at,
            resolved_by=item.resolved_by,
        )
        for item in items
    ]

    return ApiResponse(
        data=data,
        meta=PaginationMeta(total=total or 0, limit=limit, offset=offset),
    )


@router.post("/dead-letter/{item_id}/retry")
async def retry_dead_letter(item_id: str, request: Request) -> ApiResponse:
    """Retry a failed step by re-running its parent workflow."""
    tenant_id = get_tenant_id(request)

    try:
        item_uuid = uuid.UUID(item_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid DLQ item ID format")
            ).model_dump(),
        )

    _DLQ_MAX_RETRIES = 10  # Hard limit on retry attempts

    async with async_session() as session:
        # Load DLQ item with tenant check via parent Run
        stmt = select(DeadLetterItem).where(DeadLetterItem.id == item_uuid)
        if settings.auth_required and tenant_id is not None:
            stmt = stmt.join(Run, DeadLetterItem.run_id == Run.id).where(Run.tenant_id == tenant_id)
        result = await session.execute(stmt)
        item = result.scalar_one_or_none()
        if not item:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message="DLQ item not found")
                ).model_dump(),
            )

        if item.resolved_at:
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(code="ALREADY_RESOLVED", message="Item already resolved")
                ).model_dump(),
            )

        if item.attempts >= _DLQ_MAX_RETRIES:
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="MAX_RETRIES_EXCEEDED",
                        message=f"DLQ item has reached the maximum retry limit ({_DLQ_MAX_RETRIES})",
                    )
                ).model_dump(),
            )

        # Load the original run to get workflow name and input
        original_run = await session.get(Run, item.run_id)
        if not original_run:
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="RUN_NOT_FOUND",
                        message="Original run not found, cannot retry",
                    )
                ).model_dump(),
            )

        # Capture run attributes while session is open (avoid detached instance)
        run_workflow_name = original_run.workflow_name
        run_input_data = original_run.input_data
        run_callback_url = original_run.callback_url
        run_tenant_id = original_run.tenant_id
        run_id_ref = original_run.id
        dlq_item_id = item.id

    # Re-enqueue the workflow first, then mark DLQ item as resolved.
    # This ensures that if enqueue fails, the DLQ item remains unresolved
    # and can be retried again.
    try:
        yaml_content = _load_workflow_yaml(run_workflow_name)
        new_run_id = str(uuid.uuid4())

        async with async_session() as session:
            new_run = Run(
                id=uuid.UUID(new_run_id),
                workflow_name=run_workflow_name,
                status=RunStatus.QUEUED,
                input_data=run_input_data,
                callback_url=run_callback_url,
                tenant_id=run_tenant_id,
                parent_run_id=run_id_ref,
            )
            session.add(new_run)
            await session.commit()

        await enqueue_workflow(yaml_content, run_input_data or {}, new_run_id)
        logger.info(f"DLQ retry: created new run {new_run_id} for item {item_id}")

    except Exception as e:
        logger.error("DLQ retry failed for item %s: %s", item_id, e)
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(code="RETRY_ERROR", message="Could not retry dead letter item")
            ).model_dump(),
        )

    # Mark DLQ item as resolved AFTER successful enqueue
    async with async_session() as session:
        dlq_item = await session.get(DeadLetterItem, dlq_item_id)
        if dlq_item:
            dlq_item.resolved_at = datetime.now(timezone.utc)
            dlq_item.resolved_by = "retry"
            dlq_item.attempts += 1
            await session.commit()

    return ApiResponse(
        data={
            "retried": True,
            "dlq_item_id": item_id,
            "new_run_id": new_run_id,
        },
    )


@router.post("/dead-letter/{item_id}/resolve")
async def resolve_dead_letter(
    item_id: str,
    req: Request,
    request: DeadLetterResolveRequest | None = None,
) -> ApiResponse:
    """Manually resolve a dead letter queue item."""
    tenant_id = get_tenant_id(req)

    try:
        item_uuid = uuid.UUID(item_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid DLQ item ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        stmt = select(DeadLetterItem).where(DeadLetterItem.id == item_uuid)
        if settings.auth_required and tenant_id is not None:
            stmt = stmt.join(Run, DeadLetterItem.run_id == Run.id).where(Run.tenant_id == tenant_id)
        result = await session.execute(stmt)
        item = result.scalar_one_or_none()
        if not item:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message="DLQ item not found")
                ).model_dump(),
            )

        if item.resolved_at:
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(code="ALREADY_RESOLVED", message="Item already resolved")
                ).model_dump(),
            )

        item.resolved_at = datetime.now(timezone.utc)
        item.resolved_by = "manual"
        await session.commit()

    try:
        from sandcastle.engine.audit import append_audit_event
        async with async_session() as _as:
            await append_audit_event(session=_as, event_type="dlq.resolved", run_id=str(item.run_id), actor_id=get_tenant_id(req) or "system", payload={"dlq_item_id": item_id, "resolved_by": "manual"}, actor_key_prefix=req.headers.get("X-Api-Key", "")[:8] or None, source_ip=req.client.host if req.client else None)
            await _as.commit()
    except Exception as _ae:
        logger.warning("Audit dlq.resolved failed: %s", _ae)
    return ApiResponse(
        data=DeadLetterItemResponse(
            id=str(item.id),
            run_id=str(item.run_id),
            step_id=item.step_id,
            parallel_index=item.parallel_index,
            error=item.error,
            input_data=item.input_data,
            attempts=item.attempts,
            created_at=item.created_at,
            resolved_at=item.resolved_at,
            resolved_by=item.resolved_by,
        )
    )


# --- AutoPilot ---


@router.get("/autopilot/experiments")
async def list_experiments(
    request: Request,
    status: str | None = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List AutoPilot experiments. Admin-only when auth is enabled."""
    _require_admin(request)
    async with async_session() as session:
        base = select(AutoPilotExperiment)
        count_base = select(func.count(AutoPilotExperiment.id))

        if status:
            base = base.where(AutoPilotExperiment.status == status)
            count_base = count_base.where(AutoPilotExperiment.status == status)

        total = await session.scalar(count_base)
        stmt = base.order_by(AutoPilotExperiment.created_at.desc()).offset(offset).limit(limit)
        result = await session.execute(stmt)
        items = result.scalars().all()

    data = [
        ExperimentResponse(
            id=str(e.id),
            workflow_name=e.workflow_name,
            step_id=e.step_id,
            status=e.status.value if hasattr(e.status, "value") else e.status,
            optimize_for=e.optimize_for,
            config=e.config,
            deployed_variant_id=e.deployed_variant_id,
            created_at=e.created_at,
            completed_at=e.completed_at,
        )
        for e in items
    ]

    return ApiResponse(
        data=data,
        meta=PaginationMeta(total=total or 0, limit=limit, offset=offset),
    )


@router.get("/autopilot/experiments/{experiment_id}")
async def get_experiment(experiment_id: str, req: Request) -> ApiResponse:
    """Get experiment details with samples and stats. Admin-only when auth is enabled."""
    _require_admin(req)
    try:
        exp_uuid = uuid.UUID(experiment_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid experiment ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        stmt = (
            select(AutoPilotExperiment)
            .options(selectinload(AutoPilotExperiment.samples))
            .where(AutoPilotExperiment.id == exp_uuid)
        )
        result = await session.execute(stmt)
        experiment = result.scalar_one_or_none()

    if not experiment:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message="Experiment not found")
            ).model_dump(),
        )

    samples = [
        {
            "id": str(s.id),
            "variant_id": s.variant_id,
            "quality_score": s.quality_score,
            "cost_usd": s.cost_usd,
            "duration_seconds": s.duration_seconds,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in experiment.samples
    ]

    return ApiResponse(
        data=ExperimentResponse(
            id=str(experiment.id),
            workflow_name=experiment.workflow_name,
            step_id=experiment.step_id,
            status=(
                experiment.status.value
                if hasattr(experiment.status, "value")
                else experiment.status
            ),
            optimize_for=experiment.optimize_for,
            config=experiment.config,
            deployed_variant_id=experiment.deployed_variant_id,
            created_at=experiment.created_at,
            completed_at=experiment.completed_at,
            samples=samples,
        )
    )


@router.post("/autopilot/experiments/{experiment_id}/deploy")
async def deploy_experiment(experiment_id: str, req: Request) -> ApiResponse:
    """Manually deploy a specific variant from an experiment. Admin-only when auth is enabled."""
    _require_admin(req)
    try:
        exp_uuid = uuid.UUID(experiment_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid experiment ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        experiment = await session.get(AutoPilotExperiment, exp_uuid)
        if not experiment:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message="Experiment not found")
                ).model_dump(),
            )

        # Find the best performing variant
        from sandcastle.engine.autopilot import maybe_complete_experiment
        from sandcastle.engine.dag import AutoPilotConfig

        config = AutoPilotConfig(
            optimize_for=experiment.optimize_for,
            min_samples=0,  # Force completion
            auto_deploy=True,
            quality_threshold=0.0,
        )
        winner = await maybe_complete_experiment(exp_uuid, config)

        if not winner:
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(code="NO_SAMPLES", message="No samples to deploy from")
                ).model_dump(),
            )

    return ApiResponse(
        data={
            "deployed": True,
            "experiment_id": experiment_id,
            "variant_id": winner["variant_id"],
            "avg_quality": winner.get("avg_quality"),
        },
    )


@router.post("/autopilot/experiments/{experiment_id}/reset")
async def reset_experiment(experiment_id: str, req: Request) -> ApiResponse:
    """Reset an experiment by deleting all samples and restarting.

    Admin-only when auth is enabled.
    """
    _require_admin(req)
    try:
        exp_uuid = uuid.UUID(experiment_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid experiment ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        experiment = await session.get(AutoPilotExperiment, exp_uuid)
        if not experiment:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message="Experiment not found")
                ).model_dump(),
            )

        # Delete all samples
        from sqlalchemy import delete

        await session.execute(
            delete(AutoPilotSample).where(AutoPilotSample.experiment_id == exp_uuid)
        )

        # Reset experiment
        experiment.status = ExperimentStatus.RUNNING
        experiment.deployed_variant_id = None
        experiment.completed_at = None
        experiment.rollout_stage = None
        await session.commit()

    return ApiResponse(data={"reset": True, "experiment_id": experiment_id})


@router.post("/autopilot/experiments/{experiment_id}/advance-rollout")
async def advance_experiment_rollout(experiment_id: str, req: Request) -> ApiResponse:
    """Advance a deploying experiment to the next rollout stage. Admin-only when auth is enabled."""
    _require_admin(req)
    try:
        exp_uuid = uuid.UUID(experiment_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid experiment ID")
            ).model_dump(),
        )

    from sandcastle.engine.autopilot import advance_rollout

    result = await advance_rollout(exp_uuid)
    if result is None:
        raise HTTPException(
            status_code=409,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_STATE",
                    message="Experiment is not in deploying state",
                )
            ).model_dump(),
        )

    return ApiResponse(data=result)


@router.get("/autopilot/stats")
async def autopilot_stats(req: Request) -> ApiResponse:
    """Get overall AutoPilot savings and experiment statistics. Admin-only when auth is enabled."""
    _require_admin(req)
    async with async_session() as session:
        total = await session.scalar(select(func.count(AutoPilotExperiment.id)))
        active = await session.scalar(
            select(func.count(AutoPilotExperiment.id)).where(
                AutoPilotExperiment.status == ExperimentStatus.RUNNING
            )
        )
        # Count deploying experiments separately
        deploying = await session.scalar(
            select(func.count(AutoPilotExperiment.id)).where(
                AutoPilotExperiment.status == ExperimentStatus.DEPLOYING
            )
        )
        completed = await session.scalar(
            select(func.count(AutoPilotExperiment.id)).where(
                AutoPilotExperiment.status == ExperimentStatus.COMPLETED
            )
        )
        total_samples = await session.scalar(select(func.count(AutoPilotSample.id)))

        # Calculate actual quality improvement and cost savings
        avg_quality_improvement = 0.0
        total_cost_savings = 0.0

        completed_exps_q = (
            select(AutoPilotExperiment)
            .where(
                AutoPilotExperiment.status == ExperimentStatus.COMPLETED,
                AutoPilotExperiment.deployed_variant_id.is_not(None),
            )
            .limit(1000)
        )
        completed_exps = (await session.execute(completed_exps_q)).scalars().all()

        improvements = []
        for exp in completed_exps:
            # Get per-variant stats for this experiment
            variant_stats_q = (
                select(
                    AutoPilotSample.variant_id,
                    func.avg(AutoPilotSample.quality_score).label("avg_quality"),
                    func.avg(AutoPilotSample.cost_usd).label("avg_cost"),
                )
                .where(AutoPilotSample.experiment_id == exp.id)
                .group_by(AutoPilotSample.variant_id)
            )
            rows = (await session.execute(variant_stats_q)).all()
            stats_map = {r.variant_id: r for r in rows}

            winner = stats_map.get(exp.deployed_variant_id)
            if not winner:
                continue

            # Baseline = first non-winner variant
            baseline_quality = 0.0
            baseline_cost = 0.0
            for vid, s in stats_map.items():
                if vid != exp.deployed_variant_id:
                    baseline_quality = float(s.avg_quality or 0)
                    baseline_cost = float(s.avg_cost or 0)
                    break

            winner_quality = float(winner.avg_quality or 0)
            winner_cost = float(winner.avg_cost or 0)

            if baseline_quality > 0:
                improvements.append((winner_quality - baseline_quality) / baseline_quality)
            if baseline_cost > 0:
                # Use per-experiment sample count, not global total_samples
                exp_sample_count = sum(1 for r in rows for _ in [r])  # len(rows) counts variants, not samples
                exp_sample_q = select(func.count(AutoPilotSample.id)).where(
                    AutoPilotSample.experiment_id == exp.id
                )
                exp_sample_count = await session.scalar(exp_sample_q) or 0
                total_cost_savings += (baseline_cost - winner_cost) * exp_sample_count

        if improvements:
            avg_quality_improvement = sum(improvements) / len(improvements)

    return ApiResponse(
        data=AutoPilotStatsResponse(
            total_experiments=total or 0,
            active_experiments=(active or 0) + (deploying or 0),
            deploying_experiments=deploying or 0,
            completed_experiments=completed or 0,
            total_samples=total_samples or 0,
            avg_quality_improvement=round(avg_quality_improvement, 4),
            total_cost_savings_usd=round(total_cost_savings, 2),
        )
    )


# --- Approval Gates ---


@router.get("/approvals")
async def list_approvals(
    request: Request,
    status: str | None = Query(None, description="Filter by status (pending, approved, etc.)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List approval requests, scoped to tenant."""
    if status:
        valid = {s.value for s in ApprovalStatus}
        if status not in valid:
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="INVALID_STATUS",
                        message=f"Invalid status '{status}'. Valid: {', '.join(sorted(valid))}",
                    )
                ).model_dump(),
            )
    tenant_id = get_tenant_id(request)

    async with async_session() as session:
        base = select(ApprovalRequest)
        count_base = select(func.count(ApprovalRequest.id))

        # Tenant isolation via join on parent Run
        if settings.auth_required and tenant_id is not None:
            join_cond = ApprovalRequest.run_id == Run.id
            base = base.join(Run, join_cond).where(Run.tenant_id == tenant_id)
            count_base = count_base.join(Run, join_cond).where(Run.tenant_id == tenant_id)

        if status:
            base = base.where(ApprovalRequest.status == status)
            count_base = count_base.where(ApprovalRequest.status == status)

        total = await session.scalar(count_base)

        stmt = base.order_by(ApprovalRequest.created_at.desc()).offset(offset).limit(limit)
        result = await session.execute(stmt)
        items = result.scalars().all()

    data = [
        ApprovalResponse(
            id=str(a.id),
            run_id=str(a.run_id),
            step_id=a.step_id,
            status=a.status.value if hasattr(a.status, "value") else a.status,
            request_data=a.request_data,
            response_data=a.response_data,
            message=a.message,
            reviewer_id=a.reviewer_id,
            reviewer_comment=a.reviewer_comment,
            timeout_at=a.timeout_at,
            on_timeout=a.on_timeout,
            allow_edit=a.allow_edit,
            created_at=a.created_at,
            resolved_at=a.resolved_at,
        )
        for a in items
    ]

    return ApiResponse(
        data=data,
        meta=PaginationMeta(total=total or 0, limit=limit, offset=offset),
    )


@router.get("/approvals/{approval_id}")
async def get_approval(approval_id: str, request: Request) -> ApiResponse:
    """Get details of a specific approval request."""
    tenant_id = get_tenant_id(request)

    try:
        approval_uuid = uuid.UUID(approval_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid approval ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        stmt = select(ApprovalRequest).where(ApprovalRequest.id == approval_uuid)
        if settings.auth_required and tenant_id is not None:
            stmt = stmt.join(Run, ApprovalRequest.run_id == Run.id).where(
                Run.tenant_id == tenant_id
            )
        result = await session.execute(stmt)
        approval = result.scalar_one_or_none()

    if not approval:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message="Approval request not found")
            ).model_dump(),
        )

    return ApiResponse(
        data=ApprovalResponse(
            id=str(approval.id),
            run_id=str(approval.run_id),
            step_id=approval.step_id,
            status=approval.status.value if hasattr(approval.status, "value") else approval.status,
            request_data=approval.request_data,
            response_data=approval.response_data,
            message=approval.message,
            reviewer_id=approval.reviewer_id,
            reviewer_comment=approval.reviewer_comment,
            timeout_at=approval.timeout_at,
            on_timeout=approval.on_timeout,
            allow_edit=approval.allow_edit,
            created_at=approval.created_at,
            resolved_at=approval.resolved_at,
        )
    )


async def _resolve_and_update_approval(
    approval_id: str,
    tenant_id: str | None,
    new_status: ApprovalStatus,
    request_body: ApprovalRespondRequest | None = None,
    response_data: dict | None = None,
) -> ApprovalRequest:
    """Atomically check pending status and update in a single session/transaction.

    Prevents TOCTOU race where two clients resolve the same approval.
    """
    try:
        approval_uuid = uuid.UUID(approval_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid approval ID format")
            ).model_dump(),
        )

    now = datetime.now(timezone.utc)

    async with async_session() as session:
        # Use FOR UPDATE to prevent TOCTOU race on concurrent resolve requests
        stmt = (
            select(ApprovalRequest)
            .where(ApprovalRequest.id == approval_uuid)
            .with_for_update()
        )
        if settings.auth_required and tenant_id is not None:
            stmt = stmt.join(Run, ApprovalRequest.run_id == Run.id).where(
                Run.tenant_id == tenant_id
            )
        result = await session.execute(stmt)
        approval = result.scalar_one_or_none()

        if not approval:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message="Approval request not found")
                ).model_dump(),
            )

        ap_status = approval.status.value if hasattr(approval.status, "value") else approval.status
        if ap_status != "pending":
            raise HTTPException(
                status_code=409,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="ALREADY_RESOLVED",
                        message=f"Approval already resolved with status '{ap_status}'",
                    )
                ).model_dump(),
            )

        approval.status = new_status
        approval.resolved_at = now
        if response_data is not None:
            approval.response_data = response_data
        if request_body and request_body.comment:
            approval.reviewer_comment = request_body.comment

        if new_status == ApprovalStatus.REJECTED:
            run = await session.get(Run, approval.run_id)
            if run:
                run.status = RunStatus.FAILED
                run.completed_at = now
                run.error = f"Approval rejected at step '{approval.step_id}'"

        await session.commit()
        try:
            from sandcastle.engine.audit import append_audit_event
            status_val = new_status.value if hasattr(new_status, "value") else str(new_status)
            await append_audit_event(session=session, event_type="approval.resolved", run_id=str(approval.run_id) if approval.run_id else None, actor_id=tenant_id or "system", payload={"approval_id": approval_id, "step_id": approval.step_id, "new_status": status_val})
            await session.commit()
        except Exception as _ae:
            logger.warning("Audit approval.resolved failed: %s", _ae)

    return approval


async def _rollback_approval_to_pending(approval: ApprovalRequest) -> None:
    """Reset a committed approval back to PENDING so the user can retry.

    Called when _resume_after_approval fails after the approval was already
    committed -- prevents the approval from being stuck in a terminal state
    with no running workflow.

    Also resets the run back to AWAITING_APPROVAL (if it was set to FAILED
    by the resume failure) and writes a compensating audit event so the
    audit trail reflects the real state.
    """
    async with async_session() as session:
        ap = await session.get(ApprovalRequest, approval.id)
        if ap:
            ap.status = ApprovalStatus.PENDING
            ap.resolved_at = None

            # Also reset the run if it was marked FAILED by the failed resume
            if ap.run_id:
                run = await session.get(Run, ap.run_id)
                if run and run.status == RunStatus.FAILED:
                    run.status = RunStatus.AWAITING_APPROVAL
                    run.error = ""
                    run.completed_at = None

            await session.commit()

            # Compensating audit event so trail matches reality
            try:
                from sandcastle.engine.audit import append_audit_event
                await append_audit_event(
                    session=session,
                    event_type="approval.rollback_to_pending",
                    run_id=str(ap.run_id) if ap.run_id else None,
                    actor_id="system",
                    payload={"approval_id": str(approval.id), "reason": "resume_failed"},
                )
                await session.commit()
            except Exception as _ae:
                logger.warning("Audit approval.rollback_to_pending failed: %s", _ae)

            logger.info(
                "Rolled back approval %s to PENDING after resume failure",
                approval.id,
            )


@router.post("/approvals/{approval_id}/approve")
async def approve_approval(
    approval_id: str,
    req: Request,
    request: ApprovalRespondRequest | None = None,
) -> ApiResponse:
    """Approve an approval gate and resume the workflow."""
    tenant_id = get_tenant_id(req)

    approval = await _resolve_and_update_approval(
        approval_id, tenant_id, ApprovalStatus.APPROVED, request,
    )

    response_data = approval.request_data
    if request and request.edited_data and approval.allow_edit:
        response_data = request.edited_data
        async with async_session() as session:
            ap = await session.get(ApprovalRequest, approval.id)
            if ap:
                ap.response_data = response_data
                await session.commit()

    try:
        await _resume_after_approval(approval, output_data=response_data or {"approved": True})
    except Exception:
        await _rollback_approval_to_pending(approval)
        raise

    return ApiResponse(
        data={"approved": True, "approval_id": approval_id, "run_id": str(approval.run_id)},
    )


@router.post("/approvals/{approval_id}/reject")
async def reject_approval(
    approval_id: str,
    req: Request,
    request: ApprovalRespondRequest | None = None,
) -> ApiResponse:
    """Reject an approval gate and fail the workflow."""
    tenant_id = get_tenant_id(req)

    approval = await _resolve_and_update_approval(
        approval_id, tenant_id, ApprovalStatus.REJECTED, request,
    )

    return ApiResponse(
        data={"rejected": True, "approval_id": approval_id, "run_id": str(approval.run_id)},
    )


@router.post("/approvals/{approval_id}/skip")
async def skip_approval(
    approval_id: str,
    req: Request,
    request: ApprovalRespondRequest | None = None,
) -> ApiResponse:
    """Skip an approval gate and continue the workflow."""
    tenant_id = get_tenant_id(req)

    approval = await _resolve_and_update_approval(
        approval_id, tenant_id, ApprovalStatus.SKIPPED, request,
    )

    try:
        await _resume_after_approval(approval, output_data=None)
    except Exception:
        await _rollback_approval_to_pending(approval)
        raise

    return ApiResponse(
        data={"skipped": True, "approval_id": approval_id, "run_id": str(approval.run_id)},
    )


@router.post("/approvals/{approval_id}/regenerate")
async def regenerate_approval(
    approval_id: str,
    req: Request,
    request: ApprovalRespondRequest | None = None,
) -> ApiResponse:
    """Request selective image regeneration and resume the workflow.

    The caller sends ``edited_data`` with ``rejected_shots`` (list of
    zero-based image indices) and an optional ``feedback`` string.  The
    approval is marked *approved* so the workflow continues, but the
    output data carries an ``action`` of ``"regenerate"`` that downstream
    condition steps can route on.
    """
    tenant_id = get_tenant_id(req)

    # Validate that we have the regeneration payload
    if not request or not request.edited_data:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="MISSING_DATA",
                    message="edited_data with rejected_shots is required for regeneration",
                )
            ).model_dump(),
        )

    edit_data = request.edited_data
    rejected = edit_data.get("rejected_shots")
    if not isinstance(rejected, list) or len(rejected) == 0:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_DATA",
                    message="rejected_shots must be a non-empty list of image indices",
                )
            ).model_dump(),
        )

    # Resolve as APPROVED so the workflow can continue (the condition
    # step will decide whether to route into the regeneration branch)
    approval = await _resolve_and_update_approval(
        approval_id, tenant_id, ApprovalStatus.APPROVED, request,
        response_data=edit_data,
    )

    # Build output data that downstream steps can inspect
    output_data = {
        "action": "regenerate",
        "rejected_shots": rejected,
        "feedback": edit_data.get("feedback", ""),
        **(approval.request_data or {}),
    }

    # Persist the response_data on the approval record
    async with async_session() as session:
        ap = await session.get(ApprovalRequest, approval.id)
        if ap:
            ap.response_data = output_data
            await session.commit()

    try:
        await _resume_after_approval(approval, output_data=output_data)
    except Exception:
        await _rollback_approval_to_pending(approval)
        raise

    return ApiResponse(
        data={
            "regenerating": True,
            "approval_id": approval_id,
            "run_id": str(approval.run_id),
            "rejected_shots": rejected,
        },
    )


async def _resume_after_approval(
    approval: ApprovalRequest,
    output_data: dict | None,
) -> bool:
    """Resume a workflow after an approval gate is resolved.

    Loads the checkpoint, sets the approval step output, and re-enqueues.
    Returns True on success, False on failure.
    """
    run_id = str(approval.run_id)
    step_id = approval.step_id

    # Load the run to get workflow info
    async with async_session() as session:
        run = await session.get(Run, approval.run_id)
        if not run:
            logger.error(f"Cannot resume: run {run_id} not found")
            raise HTTPException(
                status_code=409,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="RESUME_FAILED",
                        message=f"Run '{run_id}' not found, cannot resume after approval",
                    )
                ).model_dump(),
            )

        workflow_name = run.workflow_name
        workflow_version = run.workflow_version
        input_data = run.input_data or {}
        max_cost_usd = run.max_cost_usd

    # Load workflow YAML (use versioned loader to match replay/fork behavior)
    try:
        yaml_content = await _load_versioned_workflow_yaml(workflow_name, workflow_version)
    except FileNotFoundError:
        logger.error(f"Cannot resume: workflow '{workflow_name}' not found")
        raise HTTPException(
            status_code=409,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="RESUME_FAILED",
                    message=f"Workflow '{workflow_name}' not found on disk, "
                    f"cannot resume run '{run_id}' after approval",
                )
            ).model_dump(),
        )

    # Find the checkpoint (saved before the approval step)
    async with async_session() as session:
        checkpoint_stmt = (
            select(RunCheckpoint)
            .where(RunCheckpoint.run_id == approval.run_id)
            .order_by(RunCheckpoint.stage_index.desc())
        )
        result = await session.execute(checkpoint_stmt)
        checkpoints = result.scalars().all()

    # Use the latest checkpoint
    initial_context = checkpoints[0].context_snapshot if checkpoints else None

    # Set the approval step output in the context
    if initial_context:
        initial_context["step_outputs"][step_id] = output_data
    else:
        initial_context = {"step_outputs": {step_id: output_data}, "costs": []}

    # Steps already completed (including the approval step now)
    skip_steps = list(initial_context["step_outputs"].keys())

    # Update the approval step's RunStep record to "completed"
    from sandcastle.models.db import RunStep, StepStatus

    async with async_session() as session:
        step_stmt = (
            select(RunStep)
            .where(RunStep.run_id == approval.run_id, RunStep.step_id == step_id)
        )
        result = await session.execute(step_stmt)
        run_step = result.scalar_one_or_none()
        if run_step:
            run_step.status = StepStatus.COMPLETED
            run_step.completed_at = datetime.now(timezone.utc)
            await session.commit()

    # Transition run to QUEUED so the worker accepts it
    async with async_session() as session:
        run = await session.get(Run, approval.run_id)
        if run:
            run.status = RunStatus.QUEUED
            await session.commit()

    # Enqueue continuation
    try:
        await enqueue_workflow(
            yaml_content,
            input_data,
            run_id,
            max_cost_usd=max_cost_usd,
            initial_context=initial_context,
            skip_steps=skip_steps,
        )
        logger.info(f"Resumed workflow {run_id} after approval of step '{step_id}'")
        return True
    except Exception as e:
        logger.error(f"Failed to resume workflow {run_id}: {e}")
        async with async_session() as session:
            run = await session.get(Run, approval.run_id)
            if run:
                run.status = RunStatus.FAILED
                run.error = f"Failed to resume after approval: {e}"
                run.completed_at = datetime.now(timezone.utc)
                await session.commit()
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="RESUME_FAILED",
                    message=f"Failed to enqueue workflow resumption for run '{run_id}'",
                )
            ).model_dump(),
        )


# --- API Keys ---


@router.post("/api-keys", status_code=201)
async def create_api_key(request: ApiKeyCreateRequest, req: Request) -> ApiResponse:
    """Create a new API key. Returns the plaintext key ONCE. Requires admin."""
    _require_admin(req)
    auth_tenant = get_tenant_id(req)

    # When auth is enabled, enforce tenant scoping
    if settings.auth_required and auth_tenant is not None:
        # Tenant keys can only create keys for their own tenant (not admin keys)
        if request.tenant_id is None or request.tenant_id != auth_tenant:
            raise HTTPException(
                status_code=403,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="FORBIDDEN",
                        message="Cannot create API keys for a different tenant",
                    )
                ).model_dump(),
            )

    plaintext_key = generate_api_key()
    key_hash_value = hash_key(plaintext_key)
    key_prefix = plaintext_key[:8]

    try:
        async with async_session() as session:
            db_key = ApiKey(
                key_hash=key_hash_value,
                key_prefix=key_prefix,
                tenant_id=request.tenant_id,
                name=request.name,
                max_cost_per_run_usd=request.max_cost_per_run_usd,
                allowed_workflows=request.allowed_workflows,
            )
            session.add(db_key)
            await session.commit()
            await session.refresh(db_key)

            return ApiResponse(
                data=ApiKeyCreatedResponse(
                    id=str(db_key.id),
                    key_prefix=key_prefix,
                    tenant_id=db_key.tenant_id,
                    name=db_key.name,
                    key=plaintext_key,
                    allowed_workflows=db_key.allowed_workflows,
                )
            )
    except Exception as e:
        logger.error("Could not create API key: %s", e)
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(code="DB_ERROR", message="Could not create API key")
            ).model_dump(),
        )


# Note: response_model intentionally omitted; return type annotation provides typing.
@router.get("/api-keys")
async def list_api_keys(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List API keys (without plaintext). Scoped to tenant when auth is enabled."""
    tenant_id = get_tenant_id(request)

    async with async_session() as session:
        base = select(ApiKey).where(ApiKey.is_active.is_(True))
        count_base = select(func.count(ApiKey.id)).where(ApiKey.is_active.is_(True))

        # Tenant isolation - only see own keys
        base = _apply_tenant_filter(base, tenant_id, ApiKey.tenant_id)
        count_base = _apply_tenant_filter(count_base, tenant_id, ApiKey.tenant_id)

        total = await session.scalar(count_base)

        stmt = base.order_by(ApiKey.created_at.desc()).offset(offset).limit(limit)
        result = await session.execute(stmt)
        keys = result.scalars().all()

    data = [
        ApiKeyResponse(
            id=str(k.id),
            key_prefix=k.key_prefix,
            tenant_id=k.tenant_id,
            name=k.name,
            is_active=k.is_active,
            max_cost_per_run_usd=k.max_cost_per_run_usd,
            expires_at=k.expires_at,
            allowed_cidrs=k.allowed_cidrs,
            allowed_workflows=k.allowed_workflows,
            created_at=k.created_at,
            last_used_at=k.last_used_at,
        )
        for k in keys
    ]

    return ApiResponse(
        data=data,
        meta=PaginationMeta(total=total or 0, limit=limit, offset=offset),
    )


# --- Policy Violations ---


_VALID_SEVERITIES = {"critical", "high", "medium", "low"}


@router.get("/runs/{run_id}/violations")
async def get_run_violations(
    run_id: str,
    request: Request,
    severity: str | None = Query(None, description="Filter by severity"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List policy violations for a specific run."""
    tenant_id = get_tenant_id(request)

    if severity and severity not in _VALID_SEVERITIES:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_SEVERITY",
                    message=f"Invalid severity '{severity}'. Must be one of: {', '.join(sorted(_VALID_SEVERITIES))}",
                )
            ).model_dump(),
        )

    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid run ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        # Verify run exists and belongs to tenant
        run_check = select(Run.id).where(Run.id == run_uuid)
        run_check = _apply_tenant_filter(run_check, tenant_id, Run.tenant_id)
        if not await session.scalar(run_check):
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message=f"Run '{run_id}' not found")
                ).model_dump(),
            )

        base = select(PolicyViolation).where(PolicyViolation.run_id == run_uuid)
        count_base = select(func.count(PolicyViolation.id)).where(
            PolicyViolation.run_id == run_uuid
        )

        if severity:
            base = base.where(PolicyViolation.severity == severity)
            count_base = count_base.where(PolicyViolation.severity == severity)

        total = await session.scalar(count_base)
        stmt = base.order_by(PolicyViolation.created_at.desc()).offset(offset).limit(limit)
        result = await session.execute(stmt)
        items = result.scalars().all()

    data = [
        PolicyViolationResponse(
            id=str(v.id),
            run_id=str(v.run_id),
            step_id=v.step_id,
            policy_id=v.policy_id,
            severity=v.severity,
            trigger_details=v.trigger_details,
            action_taken=v.action_taken,
            output_modified=v.output_modified,
            created_at=v.created_at,
        )
        for v in items
    ]

    return ApiResponse(
        data=data,
        meta=PaginationMeta(total=total or 0, limit=limit, offset=offset),
    )


@router.get("/violations")
async def list_violations(
    request: Request,
    severity: str | None = Query(None, description="Filter by severity"),
    policy_id: str | None = Query(None, description="Filter by policy ID"),
    since: datetime | None = Query(None, description="Filter violations after this datetime"),
    until: datetime | None = Query(None, description="Filter violations before this datetime"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List all policy violations with filters."""
    tenant_id = get_tenant_id(request)

    if severity and severity not in _VALID_SEVERITIES:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_SEVERITY",
                    message=f"Invalid severity '{severity}'. Must be one of: {', '.join(sorted(_VALID_SEVERITIES))}",
                )
            ).model_dump(),
        )

    if policy_id and len(policy_id) > 255:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_POLICY_ID",
                    message="policy_id must be 255 characters or fewer",
                )
            ).model_dump(),
        )

    async with async_session() as session:
        base = select(PolicyViolation)
        count_base = select(func.count(PolicyViolation.id))

        # Tenant isolation via join on parent Run
        if settings.auth_required and tenant_id is not None:
            join_cond = PolicyViolation.run_id == Run.id
            base = base.join(Run, join_cond).where(Run.tenant_id == tenant_id)
            count_base = count_base.join(Run, join_cond).where(Run.tenant_id == tenant_id)

        if severity:
            base = base.where(PolicyViolation.severity == severity)
            count_base = count_base.where(PolicyViolation.severity == severity)
        if policy_id:
            base = base.where(PolicyViolation.policy_id == policy_id)
            count_base = count_base.where(PolicyViolation.policy_id == policy_id)
        if since:
            base = base.where(PolicyViolation.created_at >= since)
            count_base = count_base.where(PolicyViolation.created_at >= since)
        if until:
            base = base.where(PolicyViolation.created_at <= until)
            count_base = count_base.where(PolicyViolation.created_at <= until)

        total = await session.scalar(count_base)
        stmt = base.order_by(PolicyViolation.created_at.desc()).offset(offset).limit(limit)
        result = await session.execute(stmt)
        items = result.scalars().all()

    data = [
        PolicyViolationResponse(
            id=str(v.id),
            run_id=str(v.run_id),
            step_id=v.step_id,
            policy_id=v.policy_id,
            severity=v.severity,
            trigger_details=v.trigger_details,
            action_taken=v.action_taken,
            output_modified=v.output_modified,
            created_at=v.created_at,
        )
        for v in items
    ]

    return ApiResponse(
        data=data,
        meta=PaginationMeta(total=total or 0, limit=limit, offset=offset),
    )


@router.get("/violations/stats")
async def violations_stats(request: Request) -> ApiResponse:
    """Get aggregated policy violation statistics."""
    tenant_id = get_tenant_id(request)
    now = datetime.now(timezone.utc)
    thirty_days_ago = now - timedelta(days=30)

    async with async_session() as session:
        # Base query with tenant filter
        def _base_filter(stmt):
            stmt = stmt.where(PolicyViolation.created_at >= thirty_days_ago)
            if settings.auth_required and tenant_id is not None:
                stmt = stmt.join(Run, PolicyViolation.run_id == Run.id).where(
                    Run.tenant_id == tenant_id
                )
            return stmt

        # Total violations
        total_q = _base_filter(select(func.count(PolicyViolation.id)))
        total = await session.scalar(total_q)

        # By severity
        sev_q = _base_filter(
            select(PolicyViolation.severity, func.count(PolicyViolation.id).label("count"))
        ).group_by(PolicyViolation.severity)
        sev_rows = (await session.execute(sev_q)).all()
        by_severity = {row.severity: row.count for row in sev_rows}

        # By policy
        pol_q = _base_filter(
            select(PolicyViolation.policy_id, func.count(PolicyViolation.id).label("count"))
        ).group_by(PolicyViolation.policy_id)
        pol_rows = (await session.execute(pol_q)).all()
        by_policy = {row.policy_id: row.count for row in pol_rows}

        # By day (last 30 days)
        day_q = (
            _base_filter(
                select(
                    _trunc_day(PolicyViolation.created_at).label("day"),
                    func.count(PolicyViolation.id).label("count"),
                )
            )
            .group_by("day")
            .order_by("day")
        )
        day_rows = (await session.execute(day_q)).all()
        by_day = []
        for row in day_rows:
            if hasattr(row.day, "strftime"):
                d = row.day.strftime("%Y-%m-%d")
            else:
                d = str(row.day) if row.day else "unknown"
            by_day.append({"date": d, "count": row.count})

    return ApiResponse(
        data=PolicyViolationStatsResponse(
            total_violations_30d=total or 0,
            violations_by_severity=by_severity,
            violations_by_policy=by_policy,
            violations_by_day=by_day,
        )
    )


# --- Optimizer ---


@router.get("/optimizer/decisions")
async def list_routing_decisions(
    request: Request,
    workflow: str | None = Query(None, description="Filter by workflow"),
    step: str | None = Query(None, description="Filter by step ID"),
    model: str | None = Query(None, description="Filter by selected model"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List recent optimizer routing decisions."""
    tenant_id = get_tenant_id(request)

    async with async_session() as session:
        base = select(RoutingDecision)
        count_base = select(func.count(RoutingDecision.id))

        # Tenant isolation via join on parent Run
        if settings.auth_required and tenant_id is not None:
            join_cond = RoutingDecision.run_id == Run.id
            base = base.join(Run, join_cond).where(Run.tenant_id == tenant_id)
            count_base = count_base.join(Run, join_cond).where(Run.tenant_id == tenant_id)

        if workflow:
            # If tenant isolation already joined Run, just add a where clause;
            # otherwise, join Run now for the workflow filter.
            if not (settings.auth_required and tenant_id is not None):
                join_cond = RoutingDecision.run_id == Run.id
                base = base.join(Run, join_cond).where(Run.workflow_name == workflow)
                count_base = count_base.join(Run, join_cond).where(Run.workflow_name == workflow)
            else:
                base = base.where(Run.workflow_name == workflow)
                count_base = count_base.where(Run.workflow_name == workflow)
        if step:
            base = base.where(RoutingDecision.step_id == step)
            count_base = count_base.where(RoutingDecision.step_id == step)
        if model:
            base = base.where(RoutingDecision.selected_model == model)
            count_base = count_base.where(RoutingDecision.selected_model == model)

        total = await session.scalar(count_base)
        stmt = base.order_by(RoutingDecision.created_at.desc()).offset(offset).limit(limit)
        result = await session.execute(stmt)
        items = result.scalars().all()

    data = [
        RoutingDecisionResponse(
            id=str(d.id),
            run_id=str(d.run_id),
            step_id=d.step_id,
            selected_model=d.selected_model,
            selected_variant_id=d.selected_variant_id,
            reason=d.reason,
            budget_pressure=d.budget_pressure,
            confidence=d.confidence,
            alternatives=d.alternatives,
            slo=d.slo,
            created_at=d.created_at,
        )
        for d in items
    ]

    return ApiResponse(
        data=data,
        meta=PaginationMeta(total=total or 0, limit=limit, offset=offset),
    )


@router.get("/optimizer/decisions/{run_id}")
async def get_run_routing_decisions(run_id: str, request: Request) -> ApiResponse:
    """Get all routing decisions for a specific run."""
    tenant_id = get_tenant_id(request)

    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid run ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        # Verify run exists and belongs to tenant
        run_check = select(Run.id).where(Run.id == run_uuid)
        run_check = _apply_tenant_filter(run_check, tenant_id, Run.tenant_id)
        if not await session.scalar(run_check):
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message=f"Run '{run_id}' not found")
                ).model_dump(),
            )

        stmt = (
            select(RoutingDecision)
            .where(RoutingDecision.run_id == run_uuid)
            .order_by(RoutingDecision.created_at.asc())
            .limit(1000)
        )
        result = await session.execute(stmt)
        items = result.scalars().all()

    data = [
        RoutingDecisionResponse(
            id=str(d.id),
            run_id=str(d.run_id),
            step_id=d.step_id,
            selected_model=d.selected_model,
            selected_variant_id=d.selected_variant_id,
            reason=d.reason,
            budget_pressure=d.budget_pressure,
            confidence=d.confidence,
            alternatives=d.alternatives,
            slo=d.slo,
            created_at=d.created_at,
        )
        for d in items
    ]

    return ApiResponse(data=data)


@router.get("/optimizer/stats")
async def optimizer_stats(request: Request) -> ApiResponse:
    """Get optimizer overview statistics."""
    from sandcastle.engine.optimizer import get_optimizer

    tenant_id = get_tenant_id(request)
    now = datetime.now(timezone.utc)
    thirty_days_ago = now - timedelta(days=30)

    async with async_session() as session:

        def _base(stmt):
            stmt = stmt.where(RoutingDecision.created_at >= thirty_days_ago)
            if settings.auth_required and tenant_id is not None:
                stmt = stmt.join(Run, RoutingDecision.run_id == Run.id).where(
                    Run.tenant_id == tenant_id
                )
            return stmt

        # Total decisions
        total_q = _base(select(func.count(RoutingDecision.id)))
        total = await session.scalar(total_q)

        # Model distribution
        dist_q = _base(
            select(
                RoutingDecision.selected_model,
                func.count(RoutingDecision.id).label("count"),
            )
        ).group_by(RoutingDecision.selected_model)
        dist_rows = (await session.execute(dist_q)).all()
        total_count = sum(r.count for r in dist_rows) or 1
        model_dist = {r.selected_model: round(r.count / total_count, 3) for r in dist_rows}

        # Avg confidence
        conf_q = _base(select(func.avg(RoutingDecision.confidence)))
        avg_conf = await session.scalar(conf_q)

        # Calculate actual savings: compare selected cost vs default (most expensive) model
        estimated_savings = 0.0
        decisions_q = _base(select(RoutingDecision)).limit(1000)  # Cap at 1000 for performance
        decisions_result = await session.execute(decisions_q)
        for dec in decisions_result.scalars().all():
            alts = dec.alternatives or []
            if not alts:
                continue
            # Find the most expensive alternative (what would have been used without optimizer)
            max_cost = 0.0
            selected_cost = 0.0
            for alt in alts:
                cost = alt.get("avg_cost") or 0
                if alt.get("id") == "thorough" or alt.get("model") == "opus":
                    max_cost = max(max_cost, cost)
                if alt.get("model") == dec.selected_model:
                    selected_cost = cost
            # Default baseline: most expensive model in pool
            if max_cost == 0:
                max_cost = max((a.get("avg_cost") or 0) for a in alts) if alts else 0
            if max_cost > selected_cost:
                estimated_savings += max_cost - selected_cost

    optimizer = get_optimizer()
    active_alerts = len(optimizer.get_recent_alerts())

    return ApiResponse(
        data=OptimizerStatsResponse(
            total_decisions_30d=total or 0,
            model_distribution=model_dist,
            avg_confidence=round(float(avg_conf or 0), 3),
            estimated_savings_30d_usd=round(estimated_savings, 2),
            active_alerts=active_alerts,
        )
    )


@router.get("/optimizer/alerts")
async def optimizer_alerts(req: Request) -> ApiResponse:
    """Get recent model degradation alerts."""
    _require_admin(req)
    from sandcastle.engine.optimizer import get_optimizer

    optimizer = get_optimizer()
    alerts = optimizer.get_recent_alerts(limit=50)

    return ApiResponse(
        data=[
            {
                "model": a.model,
                "step_id": a.step_id,
                "workflow_name": a.workflow_name,
                "metric": a.metric,
                "current_value": round(a.current_value, 4),
                "threshold": round(a.threshold, 4),
                "severity": a.severity,
                "recommended_action": a.recommended_action,
                "detected_at": a.detected_at,
            }
            for a in alerts
        ]
    )


@router.delete("/optimizer/alerts")
async def clear_optimizer_alerts(req: Request) -> ApiResponse:
    """Clear all degradation alerts. Admin-only when auth is enabled."""
    _require_admin(req)
    from sandcastle.engine.optimizer import get_optimizer

    optimizer = get_optimizer()
    count = optimizer.clear_alerts()
    return ApiResponse(data={"cleared": count})


# --- API Keys ---


@router.delete("/api-keys/{key_id}")
async def deactivate_api_key(key_id: str, request: Request) -> ApiResponse:
    """Deactivate an API key (soft delete)."""
    tenant_id = get_tenant_id(request)

    try:
        key_uuid = uuid.UUID(key_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid key ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        stmt = select(ApiKey).where(ApiKey.id == key_uuid)
        stmt = _apply_tenant_filter(stmt, tenant_id, ApiKey.tenant_id)
        result = await session.execute(stmt)
        db_key = result.scalar_one_or_none()
        if not db_key:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message="API key not found")
                ).model_dump(),
            )

        db_key.is_active = False
        await session.commit()

    return ApiResponse(data={"deactivated": True, "id": key_id})


@router.post("/api-keys/{key_id}/rotate")
async def rotate_api_key(
    key_id: str, body: ApiKeyRotateRequest, req: Request
) -> ApiResponse:
    """Rotate an API key. Creates a new key and sets expiry on the old one. Admin only."""
    _require_admin(req)

    grace_hours = body.grace_period_hours or settings.key_rotation_grace_hours

    try:
        key_uuid = uuid.UUID(key_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid key ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        old_key = await session.get(ApiKey, key_uuid)
        if not old_key or not old_key.is_active:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message="API key not found or inactive")
                ).model_dump(),
            )

        # Set expiry on old key
        old_expires_at = datetime.now(timezone.utc) + timedelta(hours=grace_hours)
        old_key.expires_at = old_expires_at

        # Generate new key
        plaintext_key = generate_api_key()
        key_hash_value = hash_key(plaintext_key)
        new_db_key = ApiKey(
            key_hash=key_hash_value,
            key_prefix=plaintext_key[:8],
            tenant_id=old_key.tenant_id,
            name=old_key.name,
            max_cost_per_run_usd=old_key.max_cost_per_run_usd,
            allowed_cidrs=old_key.allowed_cidrs,
            allowed_workflows=old_key.allowed_workflows,
            rotated_from_id=old_key.id,
        )
        session.add(new_db_key)
        await session.commit()
        await session.refresh(new_db_key)

    return ApiResponse(
        data=ApiKeyRotateResponse(
            new_key=plaintext_key,
            new_key_id=str(new_db_key.id),
            old_key_id=key_id,
            old_key_expires_at=old_expires_at,
            grace_period_hours=grace_hours,
        )
    )


@router.put("/api-keys/{key_id}/allowlist")
async def update_api_key_allowlist(
    key_id: str, body: ApiKeyAllowlistRequest, req: Request
) -> ApiResponse:
    """Update the IP allowlist for an API key. Admin only."""
    import ipaddress

    _require_admin(req)

    try:
        key_uuid = uuid.UUID(key_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid key ID format")
            ).model_dump(),
        )

    # Validate all CIDRs
    for cidr in body.cidrs:
        try:
            ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="INVALID_CIDR",
                        message=f"Invalid CIDR notation: {cidr}",
                    )
                ).model_dump(),
            )

    async with async_session() as session:
        db_key = await session.get(ApiKey, key_uuid)
        if not db_key or not db_key.is_active:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message="API key not found or inactive")
                ).model_dump(),
            )

        db_key.allowed_cidrs = body.cidrs if body.cidrs else None
        await session.commit()
        await session.refresh(db_key)

    return ApiResponse(
        data=ApiKeyResponse(
            id=str(db_key.id),
            key_prefix=db_key.key_prefix,
            tenant_id=db_key.tenant_id,
            name=db_key.name,
            is_active=db_key.is_active,
            max_cost_per_run_usd=db_key.max_cost_per_run_usd,
            expires_at=db_key.expires_at,
            allowed_cidrs=db_key.allowed_cidrs,
            created_at=db_key.created_at,
            last_used_at=db_key.last_used_at,
        )
    )


# --- Settings ---

_SENSITIVE_KEYS = frozenset({
    "anthropic_api_key",
    "e2b_api_key",
    "openai_api_key",
    "minimax_api_key",
    "openrouter_api_key",
    "database_url",
    "redis_url",
    "webhook_secret",
    "aws_access_key_id",
    "aws_secret_access_key",
    "credential_encryption_key",
    "admin_api_key",
    "license_key",
    "sentry_dsn",
    "tool_slack_bot_token",
    "tool_jira_api_token",
    "tool_github_token",
    "tool_notion_api_key",
    "tool_gemini_api_key",
    "tool_hubspot_api_key",
    "tool_salesforce_client_id",
    "tool_salesforce_client_secret",
    "tool_salesforce_refresh_token",
    "tool_zendesk_api_token",
    "tool_smtp_password",
    "tool_google_service_account",
    "tool_teams_webhook_url",
    "tool_postgresql_url",
    "tool_langfuse_secret_key",
    "tool_langfuse_public_key",
    "tool_qdrant_url",
    "tool_qdrant_api_key",
    "tool_gcs_service_account_json",
    "tool_azure_storage_connection_string",
    "tool_azure_storage_key",
    "tool_exa_api_key",
    # Older connectors - credential keys/tokens
    "tool_salesforce_instance_url",
    "tool_sap_base_url",
    "tool_sap_api_key",
    "tool_servicenow_password",
    "tool_snowflake_password",
    "tool_mongodb_api_key",
    "tool_stripe_secret_key",
    "tool_twilio_auth_token",
    "tool_sendgrid_api_key",
    "tool_intercom_access_token",
    "tool_airtable_api_key",
    "tool_linear_api_key",
    "tool_discord_bot_token",
    "tool_discord_webhook_url",
    "tool_openai_api_key",
    "tool_anthropic_api_key",
    "tool_aws_access_key_id",
    "tool_aws_secret_access_key",
    "tool_redis_url",
    "tool_redis_token",
    "tool_supabase_url",
    "tool_supabase_service_key",
    "tool_pinecone_api_key",
    "tool_resend_api_key",
    "tool_vercel_token",
    "tool_cloudflare_api_token",
    "tool_firecrawl_api_key",
    "tool_tavily_api_key",
    "tool_jina_api_key",
    "tool_elevenlabs_api_key",
    "tool_nano_banana_api_key",
    "tool_zapier_webhook_url",
    "tool_shopify_access_token",
    "tool_quickbooks_client_secret",
    "tool_quickbooks_refresh_token",
    "tool_helios_base_url",
    "tool_helios_api_key",
    "tool_abra_base_url",
    "tool_abra_password",
    "tool_calendly_api_key",
    "tool_whatsapp_token",
    "tool_figma_access_token",
    "tool_datadog_api_key",
    "tool_datadog_app_key",
    "tool_plaid_secret",
    "tool_docusign_access_token",
    "tool_pagerduty_api_key",
    "tool_mcp_server_url",
    "tool_composio_api_key",
})


def _mask(value: str) -> str:
    """Mask a sensitive value, showing only the last 4 characters."""
    if not value:
        return ""
    # Only show suffix for keys long enough that 4 chars don't reveal much
    if len(value) < 12:
        return "****"
    return "****" + value[-4:]


def _build_settings_response() -> SettingsResponse:
    """Build a SettingsResponse from the current runtime settings."""
    return SettingsResponse(
        anthropic_api_key=_mask(settings.anthropic_api_key),
        e2b_api_key=_mask(settings.e2b_api_key),
        openai_api_key=_mask(settings.openai_api_key),
        mistral_api_key=_mask(settings.mistral_api_key),
        minimax_api_key=_mask(settings.minimax_api_key),
        openrouter_api_key=_mask(settings.openrouter_api_key),
        auth_required=settings.auth_required,
        dashboard_origin=settings.dashboard_origin,
        default_max_cost_usd=settings.default_max_cost_usd,
        webhook_secret=_mask(settings.webhook_secret),
        log_level=settings.log_level,
        max_workflow_depth=settings.max_workflow_depth,
        storage_backend=settings.storage_backend,
        storage_bucket=settings.storage_bucket,
        storage_endpoint=settings.storage_endpoint,
        data_dir=settings.data_dir,
        workflows_dir=settings.workflows_dir,
        is_local_mode=settings.is_local_mode,
        database_url=_mask(settings.database_url),
        redis_url=_mask(settings.redis_url),
    )


def _require_admin(req: Request) -> None:
    """Block access for non-admin tenants when auth is enabled.

    In local mode (auth_required=False) anyone can access settings.
    With auth enabled, only requests without a tenant scope (i.e. the
    server operator, not a tenant API key) are allowed.
    """
    if not is_admin(req):
        raise HTTPException(
            status_code=403,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="FORBIDDEN",
                    message="Admin access required",
                )
            ).model_dump(),
        )


@router.get("/settings")
async def get_settings(req: Request) -> ApiResponse:
    """Return current server settings with sensitive values masked."""
    _require_admin(req)
    return ApiResponse(data=_build_settings_response())


@router.patch("/settings")
async def update_settings(
    request: SettingsUpdateRequest,
    req: Request,
) -> ApiResponse:
    """Update server settings and persist to database.

    Security-critical settings (auth_required, dashboard_origin, webhook_secret)
    are not accepted here and must be set via environment variables.
    Input validation (ranges, allowed values) is enforced by the request schema.
    """
    _require_admin(req)

    # Allowlist of settings that may be changed at runtime via the API.
    # Security-critical settings (auth_required, webhook_secret, dashboard_origin,
    # database_url, redis_url, etc.) are intentionally omitted and must be set
    # via environment variables. The SettingsUpdateRequest schema is aligned with
    # this set, so this is a defense-in-depth guard against future schema drift.
    _MUTABLE_SETTINGS = {
        "anthropic_api_key",
        "e2b_api_key",
        "openai_api_key",
        "mistral_api_key",
        "minimax_api_key",
        "openrouter_api_key",
        "default_max_cost_usd",
        "log_level",
        "max_workflow_depth",
    }

    # Collect non-None updates (schema already validated all constraints)
    raw_updates = {k: v for k, v in request.model_dump().items() if v is not None}

    # Defense-in-depth: strip any fields not in the allowlist (should not happen
    # if SettingsUpdateRequest is kept in sync, but guards against future drift)
    updates = {k: v for k, v in raw_updates.items() if k in _MUTABLE_SETTINGS}

    if not updates:
        return ApiResponse(data=_build_settings_response())

    # Validate all updates through Pydantic before applying
    validated = Settings.model_validate({**settings.model_dump(), **updates})

    # Keys that should be encrypted before persisting to DB
    _ENCRYPTABLE_KEYS = frozenset({
        "anthropic_api_key", "e2b_api_key", "openai_api_key",
        "mistral_api_key", "minimax_api_key", "openrouter_api_key",
    })

    # Persist each setting to DB and apply to runtime config
    async with async_session() as session:
        for key, value in updates.items():
            str_value = str(value).lower() if isinstance(value, bool) else str(value)
            # Encrypt API keys before storing in DB (uses same Fernet
            # layer as tool credentials if CREDENTIAL_ENCRYPTION_KEY is set)
            if key in _ENCRYPTABLE_KEYS and str_value:
                try:
                    from sandcastle.engine.crypto import encrypt_credentials
                    encrypted = encrypt_credentials({"v": str_value})
                    if isinstance(encrypted, str):
                        str_value = encrypted
                except Exception as _enc_err:
                    logger.warning("Could not encrypt %s: %s", key, _enc_err)
            # Upsert: try to load existing, otherwise create
            existing = await session.get(Setting, key)
            if existing:
                existing.value = str_value
            else:
                session.add(Setting(key=key, value=str_value))
            # Apply validated value to the runtime settings object
            setattr(settings, key, getattr(validated, key))
        await session.commit()

    # Special handling: update root logger level
    if request.log_level is not None:
        logging.getLogger().setLevel(getattr(logging, request.log_level.upper()))

    logger.info(f"Settings updated: {list(updates.keys())}")
    try:
        from sandcastle.engine.audit import append_audit_event
        _SENSITIVE = frozenset({"anthropic_api_key", "e2b_api_key", "openai_api_key", "mistral_api_key", "minimax_api_key", "openrouter_api_key"})
        safe_updates = {k: "<redacted>" if k in _SENSITIVE else str(v) for k, v in updates.items()}
        async with async_session() as _as:
            await append_audit_event(session=_as, event_type="settings.updated", run_id=None, actor_id=get_tenant_id(req) or "system", payload={"keys_changed": list(safe_updates.keys()), "values": safe_updates}, actor_key_prefix=req.headers.get("X-Api-Key", "")[:8] or None, source_ip=req.client.host if req.client else None)
            await _as.commit()
    except Exception as _ae:
        logger.warning("Audit settings.updated failed: %s", _ae)
    return ApiResponse(data=_build_settings_response())


# --- Workflow Registry ---


@router.get("/workflows/{name}/versions")
async def list_workflow_versions(
    req: Request,
    name: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List all versions of a workflow."""
    _require_admin(req)
    async with async_session() as session:
        count_stmt = select(func.count(WorkflowVersion.id)).where(
            WorkflowVersion.workflow_name == name
        )
        total = await session.scalar(count_stmt) or 0

        stmt = (
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_name == name)
            .order_by(WorkflowVersion.version.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await session.execute(stmt)
        versions = result.scalars().all()

    if not versions and total == 0:
        # No DB versions. Fall back to disk: if a YAML exists for this name
        # in workflows_dir we return a synthetic "disk" version entry so the
        # caller can still inspect / run the workflow. We deliberately do
        # NOT write anything to the DB here — that's the round 4 invariant
        # (a read-only endpoint must not create production versions as a
        # side effect). If neither the DB nor disk knows this workflow,
        # return 404 instead of an empty list.
        try:
            disk_yaml = _load_workflow_yaml(name)
        except (FileNotFoundError, ValueError):
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="NOT_FOUND",
                        message=f"Workflow '{name}' not found",
                    )
                ).model_dump(),
            )

        try:
            disk_wf = parse_yaml_string(disk_yaml)
            disk_steps = [
                WorkflowStepInfo(
                    id=s.id, depends_on=s.depends_on,
                    model=s.model, prompt=s.prompt,
                )
                for s in disk_wf.steps
            ]
            disk_steps_count = len(disk_wf.steps)
            disk_description = disk_wf.description or ""
        except Exception:
            disk_steps = []
            disk_steps_count = 0
            disk_description = ""

        disk_version = WorkflowVersionResponse(
            id="disk",
            workflow_name=name,
            version=1,
            status="disk",
            description=disk_description,
            steps_count=disk_steps_count,
            steps=disk_steps,
            checksum=hashlib.sha256(disk_yaml.encode("utf-8")).hexdigest(),
            created_by="filesystem",
            promoted_by=None,
            promoted_at=None,
            created_at=datetime.now(timezone.utc),
        )

        return ApiResponse(
            data=WorkflowVersionListResponse(
                workflow_name=name,
                production_version=None,
                staging_version=None,
                latest_draft_version=None,
                versions=[disk_version],
            ),
            meta=PaginationMeta(total=1, limit=limit, offset=offset),
        )

    prod_ver = None
    staging_ver = None
    draft_ver = None
    for v in versions:
        status = v.status.value if hasattr(v.status, "value") else v.status
        if status == "production" and prod_ver is None:
            prod_ver = v.version
        elif status == "staging" and staging_ver is None:
            staging_ver = v.version
        elif status == "draft" and draft_ver is None:
            draft_ver = v.version

    version_list = []
    for v in versions:
        try:
            wf = parse_yaml_string(v.yaml_content)
            steps = [
                WorkflowStepInfo(id=s.id, depends_on=s.depends_on, model=s.model, prompt=s.prompt)
                for s in wf.steps
            ]
        except Exception:
            steps = []

        version_list.append(
            WorkflowVersionResponse(
                id=str(v.id),
                workflow_name=v.workflow_name,
                version=v.version,
                status=v.status.value if hasattr(v.status, "value") else v.status,
                description=v.description,
                steps_count=v.steps_count,
                steps=steps,
                checksum=v.checksum,
                created_by=v.created_by,
                promoted_by=v.promoted_by,
                promoted_at=v.promoted_at,
                created_at=v.created_at,
            )
        )

    return ApiResponse(
        data=WorkflowVersionListResponse(
            workflow_name=name,
            production_version=prod_ver,
            staging_version=staging_ver,
            latest_draft_version=draft_ver,
            versions=version_list,
        ),
        meta=PaginationMeta(total=total, limit=limit, offset=offset),
    )


@router.get("/workflows/{name}/versions/diff")
async def diff_workflow_versions(
    req: Request,
    name: str,
    version_a: int = Query(..., description="First version to compare"),
    version_b: int = Query(..., description="Second version to compare"),
) -> ApiResponse:
    """Get a structured diff between two workflow versions."""
    _require_admin(req)
    async with async_session() as session:
        stmt_a = select(WorkflowVersion).where(
            WorkflowVersion.workflow_name == name,
            WorkflowVersion.version == version_a,
        )
        stmt_b = select(WorkflowVersion).where(
            WorkflowVersion.workflow_name == name,
            WorkflowVersion.version == version_b,
        )
        wv_a = (await session.execute(stmt_a)).scalar_one_or_none()
        wv_b = (await session.execute(stmt_b)).scalar_one_or_none()

    if not wv_a or not wv_b:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message="One or both versions not found")
            ).model_dump(),
        )

    # Extract step IDs from each version
    steps_a = set(_extract_step_configs(wv_a.yaml_content).keys())
    steps_b = set(_extract_step_configs(wv_b.yaml_content).keys())

    configs_a = _extract_step_configs(wv_a.yaml_content)
    configs_b = _extract_step_configs(wv_b.yaml_content)

    changed = []
    for sid in steps_a & steps_b:
        if configs_a.get(sid) != configs_b.get(sid):
            changed.append(sid)

    return ApiResponse(
        data=WorkflowVersionDiffResponse(
            version_a=version_a,
            version_b=version_b,
            yaml_a=wv_a.yaml_content,
            yaml_b=wv_b.yaml_content,
            steps_added=sorted(steps_b - steps_a),
            steps_removed=sorted(steps_a - steps_b),
            steps_changed=sorted(changed),
        )
    )


@router.get("/workflows/{name}/versions/{version}")
async def get_workflow_version(req: Request, name: str, version: int) -> ApiResponse:
    """Get a specific workflow version with full YAML content."""
    _require_admin(req)
    async with async_session() as session:
        stmt = select(WorkflowVersion).where(
            WorkflowVersion.workflow_name == name,
            WorkflowVersion.version == version,
        )
        result = await session.execute(stmt)
        wv = result.scalar_one_or_none()

    if not wv:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="NOT_FOUND",
                    message=f"Version {version} not found for '{name}'",
                )
            ).model_dump(),
        )

    try:
        wf = parse_yaml_string(wv.yaml_content)
        steps = [
            WorkflowStepInfo(id=s.id, depends_on=s.depends_on, model=s.model, prompt=s.prompt)
            for s in wf.steps
        ]
    except Exception:
        steps = []

    return ApiResponse(
        data=WorkflowVersionResponse(
            id=str(wv.id),
            workflow_name=wv.workflow_name,
            version=wv.version,
            status=wv.status.value if hasattr(wv.status, "value") else wv.status,
            description=wv.description,
            steps_count=wv.steps_count,
            steps=steps,
            checksum=wv.checksum,
            created_by=wv.created_by,
            promoted_by=wv.promoted_by,
            promoted_at=wv.promoted_at,
            created_at=wv.created_at,
        )
    )


@router.post("/workflows/{name}/promote")
async def promote_workflow(req: Request, name: str, request: WorkflowPromoteRequest) -> ApiResponse:
    """Promote a workflow version: draft -> staging -> production."""
    _require_admin(req)
    async with async_session() as session:
        # Find the version to promote
        if request.version:
            stmt = select(WorkflowVersion).where(
                WorkflowVersion.workflow_name == name,
                WorkflowVersion.version == request.version,
            )
        else:
            # Find latest staging, or latest draft
            stmt = (
                select(WorkflowVersion)
                .where(
                    WorkflowVersion.workflow_name == name,
                    WorkflowVersion.status.in_(
                        [
                            WorkflowVersionStatus.STAGING,
                            WorkflowVersionStatus.DRAFT,
                        ]
                    ),
                )
                .order_by(
                    # Prefer staging over draft
                    WorkflowVersion.status.desc(),
                    WorkflowVersion.version.desc(),
                )
                .limit(1)
            )

        result = await session.execute(stmt)
        wv = result.scalar_one_or_none()

        if not wv:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message="No promotable version found")
                ).model_dump(),
            )

        current_status = wv.status.value if hasattr(wv.status, "value") else wv.status
        now = datetime.now(timezone.utc)

        if current_status == "draft":
            wv.status = WorkflowVersionStatus.STAGING
        elif current_status == "staging":
            # Archive current production
            prod_stmt = select(WorkflowVersion).where(
                WorkflowVersion.workflow_name == name,
                WorkflowVersion.status == WorkflowVersionStatus.PRODUCTION,
            )
            prod_result = await session.execute(prod_stmt)
            for old_prod in prod_result.scalars().all():
                old_prod.status = WorkflowVersionStatus.ARCHIVED

            wv.status = WorkflowVersionStatus.PRODUCTION

            # Also update disk file for backward compat
            try:
                workflows_dir = Path(settings.workflows_dir)
                workflows_dir.mkdir(parents=True, exist_ok=True)
                safe_name = "".join(
                    c if c.isalnum() or c in "-_" else "_"
                    for c in name
                )
                disk_path = workflows_dir / f"{safe_name}.yaml"
                disk_path.write_text(wv.yaml_content)
            except Exception:
                logger.error(
                    "Failed to write promoted workflow %s to disk",
                    name,
                    exc_info=True,
                )
        elif current_status == "production":
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="ALREADY_PRODUCTION",
                        message="Version is already in production",
                    )
                ).model_dump(),
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="CANNOT_PROMOTE",
                        message=f"Cannot promote from '{current_status}'",
                    )
                ).model_dump(),
            )

        wv.promoted_at = now
        await session.commit()

        new_status = wv.status.value if hasattr(wv.status, "value") else wv.status
    try:
        from sandcastle.engine.audit import append_audit_event
        async with async_session() as _as:
            await append_audit_event(session=_as, event_type="workflow.promoted", run_id=None, actor_id=get_tenant_id(req) or "system", payload={"workflow_name": name, "to_status": new_status}, actor_key_prefix=req.headers.get("X-Api-Key", "")[:8] or None, source_ip=req.client.host if req.client else None)
            await _as.commit()
    except Exception as _ae:
        logger.warning("Audit workflow.promoted failed: %s", _ae)

    return ApiResponse(
        data={
            "workflow_name": name,
            "version": wv.version,
            "previous_status": current_status,
            "new_status": new_status,
        }
    )


@router.post("/workflows/{name}/rollback")
async def rollback_workflow(req: Request, name: str, request: WorkflowRollbackRequest) -> ApiResponse:
    """Rollback a workflow to a previous production version."""
    _require_admin(req)
    async with async_session() as session:
        if request.target_version:
            # Rollback to specific version
            stmt = select(WorkflowVersion).where(
                WorkflowVersion.workflow_name == name,
                WorkflowVersion.version == request.target_version,
            )
        else:
            # Find most recent archived version
            stmt = (
                select(WorkflowVersion)
                .where(
                    WorkflowVersion.workflow_name == name,
                    WorkflowVersion.status == WorkflowVersionStatus.ARCHIVED,
                )
                .order_by(WorkflowVersion.version.desc())
                .limit(1)
            )

        result = await session.execute(stmt)
        target = result.scalar_one_or_none()

        if not target:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message="No version found to rollback to")
                ).model_dump(),
            )

        # Reject rollback to the currently active production version
        target_status = target.status.value if hasattr(target.status, "value") else target.status
        if target_status == "production":
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="ALREADY_PRODUCTION",
                        message="Target version is already the production version",
                    )
                ).model_dump(),
            )

        # Only allow rollback to archived versions (not draft/staging)
        if target_status not in ("archived", "production"):
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="INVALID_ROLLBACK_TARGET",
                        message=f"Cannot rollback to a '{target_status}' version. "
                        "Only archived versions can be rollback targets.",
                    )
                ).model_dump(),
            )

        # Archive current production
        prod_stmt = select(WorkflowVersion).where(
            WorkflowVersion.workflow_name == name,
            WorkflowVersion.status == WorkflowVersionStatus.PRODUCTION,
        )
        prod_result = await session.execute(prod_stmt)
        for old_prod in prod_result.scalars().all():
            old_prod.status = WorkflowVersionStatus.ARCHIVED

        # Activate target
        target.status = WorkflowVersionStatus.PRODUCTION
        target.promoted_at = datetime.now(timezone.utc)
        await session.commit()

        # Update disk file
        try:
            workflows_dir = Path(settings.workflows_dir)
            safe_name = "".join(
                c if c.isalnum() or c in "-_" else "_"
                for c in name
            )
            disk_path = workflows_dir / f"{safe_name}.yaml"
            disk_path.write_text(target.yaml_content)
        except Exception:
            logger.error(
                "Failed to write rolled-back workflow %s to disk",
                name,
                exc_info=True,
            )

    return ApiResponse(
        data={
            "workflow_name": name,
            "rolled_back_to_version": target.version,
            "status": "production",
        }
    )


# --- Batch Run ---


@router.post("/workflows/{name}/batch", status_code=202)
async def batch_run_workflow(name: str, req: Request) -> ApiResponse:
    """Run a workflow in batch mode with multiple input items.

    Accepts a list of input items and processes them concurrently with
    a configurable parallelism limit. Returns a batch_id immediately.
    """
    await execution_limiter.check(req)
    tenant_id = get_tenant_id(req)

    # Parse the request body manually since we use Request, not a Pydantic model param
    try:
        body = await req.json()
        batch_req = BatchRunRequest(**body)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_REQUEST",
                    message=f"Invalid batch request: {e}",
                )
            ).model_dump(),
        )

    # Validate workflow exists by creating a WorkflowRunRequest for it
    try:
        run_req = WorkflowRunRequest(workflow_name=name)
        yaml_content, wf_version = await _resolve_workflow_request(run_req)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_WORKFLOW", message=str(e))
            ).model_dump(),
        )

    # Validate YAML parses correctly
    try:
        workflow = parse_yaml_string(yaml_content)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_WORKFLOW", message=str(e))
            ).model_dump(),
        )

    errors = validate(workflow)
    if errors:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="VALIDATION_ERROR", message="; ".join(errors))
            ).model_dump(),
        )

    # Validate each batch item against the workflow's input_schema
    if workflow.input_schema:
        invalid_items: list[str] = []
        for idx, item_input in enumerate(batch_req.items):
            item_errors = _validate_workflow_input(dict(item_input), workflow.input_schema)
            if item_errors:
                invalid_items.append(f"item[{idx}]: {'; '.join(item_errors)}")
        if invalid_items:
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="INVALID_INPUT",
                        message="Batch input validation failed: " + " | ".join(invalid_items),
                    )
                ).model_dump(),
            )

    # Clamp max_parallel to the server's max_concurrent_sandboxes setting
    effective_parallel = min(batch_req.max_parallel, settings.max_concurrent_sandboxes)

    # Evict stale batches to prevent unbounded memory growth
    _evict_stale_batches()

    batch_id = str(uuid.uuid4())
    now_utc = datetime.now(timezone.utc)

    # Initialize per-item statuses
    items_status: list[dict[str, Any]] = []
    for i in range(len(batch_req.items)):
        items_status.append({
            "index": i,
            "status": "pending",
            "run_id": None,
            "cost_usd": 0.0,
            "error": None,
            "started_at": None,
            "completed_at": None,
        })

    # Store batch metadata (tenant_id for isolation)
    _batch_store[batch_id] = {
        "batch_id": batch_id,
        "workflow": name,
        "tenant_id": tenant_id,
        "status": "running",
        "total": len(batch_req.items),
        "completed": 0,
        "failed": 0,
        "running": 0,
        "pending": len(batch_req.items),
        "total_cost_usd": 0.0,
        "items": items_status,
        "created_at": now_utc.isoformat(),
    }

    # Background task to process items
    async def _process_batch() -> None:
        sem = asyncio.Semaphore(effective_parallel)
        batch = _batch_store[batch_id]
        # Lock protects counter updates (running/pending/completed/failed/total_cost)
        # from concurrent coroutine interleaving
        counter_lock = asyncio.Lock()

        async def _process_item(idx: int, item_input: dict[str, Any]) -> None:
            async with sem:
                # Check cancellation before starting a new item
                if batch.get("_cancelled"):
                    return

                item = batch["items"][idx]
                item["status"] = "running"
                item["started_at"] = datetime.now(timezone.utc).isoformat()
                async with counter_lock:
                    batch["running"] += 1
                    batch["pending"] -= 1

                try:
                    run_id = str(uuid.uuid4())
                    item["run_id"] = run_id

                    # Resolve budget for each item
                    budget = batch_req.max_cost_per_item_usd
                    if budget is None:
                        budget = await _resolve_budget(None, tenant_id)

                    # Create DB record with QUEUED status
                    async with async_session() as session:
                        db_run = Run(
                            id=uuid.UUID(run_id),
                            workflow_name=workflow.name,
                            status=RunStatus.QUEUED,
                            input_data=item_input,
                            tenant_id=tenant_id,
                            max_cost_usd=budget,
                            workflow_version=wf_version,
                            risk_level=getattr(workflow, "risk_level", "minimal"),
                        )
                        session.add(db_run)
                        await session.commit()

                    # Enqueue the job
                    await enqueue_workflow(yaml_content, item_input, run_id)

                    # Poll for completion (simple approach for in-memory tracking)
                    for _ in range(600):  # 10 minutes max (1s intervals)
                        await asyncio.sleep(1)
                        async with async_session() as session:
                            db_run = await session.get(Run, uuid.UUID(run_id))
                            if db_run and db_run.status in (
                                RunStatus.COMPLETED,
                                RunStatus.FAILED,
                            ):
                                item["cost_usd"] = float(
                                    db_run.total_cost_usd or 0
                                )
                                if db_run.status == RunStatus.COMPLETED:
                                    item["status"] = "completed"
                                    async with counter_lock:
                                        batch["completed"] += 1
                                else:
                                    item["status"] = "failed"
                                    item["error"] = db_run.error or "Unknown error"
                                    async with counter_lock:
                                        batch["failed"] += 1
                                item["completed_at"] = datetime.now(
                                    timezone.utc
                                ).isoformat()
                                async with counter_lock:
                                    batch["total_cost_usd"] += item["cost_usd"]
                                break
                    else:
                        # Timed out waiting for completion - cancel the actual run
                        try:
                            from sqlalchemy import update as sa_update
                            async with async_session() as session:
                                cancel_stmt = (
                                    sa_update(Run)
                                    .where(
                                        Run.id == uuid.UUID(run_id),
                                        Run.status.in_([RunStatus.QUEUED, RunStatus.RUNNING]),
                                    )
                                    .values(
                                        status=RunStatus.CANCELLED,
                                        completed_at=datetime.now(timezone.utc),
                                        error="Cancelled: batch item timed out after 10 minutes",
                                    )
                                )
                                await session.execute(cancel_stmt)
                                await session.commit()
                            # Set cancel flag so executor stops
                            if settings.redis_url:
                                try:
                                    from sandcastle.engine.executor import _get_redis
                                    r = await _get_redis()
                                    await r.set(f"cancel:{run_id}", "1", ex=3600)
                                except Exception:
                                    pass
                            else:
                                from sandcastle.engine.executor import cancel_run_local
                                await cancel_run_local(run_id)
                        except Exception as cancel_exc:
                            logger.warning("Failed to cancel timed-out batch run %s: %s", run_id, cancel_exc)
                        item["status"] = "failed"
                        item["error"] = "Timed out after 10 minutes"
                        async with counter_lock:
                            batch["failed"] += 1
                        item["completed_at"] = datetime.now(
                            timezone.utc
                        ).isoformat()

                except Exception as exc:
                    item["status"] = "failed"
                    item["error"] = str(exc)
                    item["completed_at"] = datetime.now(timezone.utc).isoformat()
                    async with counter_lock:
                        batch["failed"] += 1
                    # Update DB Run to failed so it doesn't stay QUEUED forever
                    if item.get("run_id"):
                        try:
                            from sqlalchemy import update as sa_update
                            async with async_session() as session:
                                fail_stmt = (
                                    sa_update(Run)
                                    .where(
                                        Run.id == uuid.UUID(item["run_id"]),
                                        Run.status.in_([RunStatus.QUEUED, RunStatus.RUNNING]),
                                    )
                                    .values(
                                        status=RunStatus.FAILED,
                                        completed_at=datetime.now(timezone.utc),
                                        error=f"Batch enqueue/processing failed: {exc}",
                                    )
                                )
                                await session.execute(fail_stmt)
                                await session.commit()
                        except Exception as db_exc:
                            logger.warning("Failed to mark batch run %s as failed in DB: %s", item["run_id"], db_exc)
                finally:
                    async with counter_lock:
                        batch["running"] -= 1

        # Launch all items concurrently (semaphore limits parallelism)
        tasks = [
            _process_item(i, item_input)
            for i, item_input in enumerate(batch_req.items)
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        # Final status - preserve "cancelled" if cancel was requested
        if not batch.get("_cancelled"):
            if batch["failed"] == 0:
                batch["status"] = "completed"
            elif batch["completed"] > 0:
                batch["status"] = "partial_failure"
            else:
                batch["status"] = "failed"

    asyncio.create_task(_process_batch())

    return ApiResponse(
        data=BatchStartedResponse(
            batch_id=batch_id,
            workflow=name,
            total=len(batch_req.items),
            status="running",
        ).model_dump(),
    )


@router.get("/batch/{batch_id}/status")
async def get_batch_status(batch_id: str, req: Request) -> ApiResponse:
    """Get the status of a batch run."""
    batch = _batch_store.get(batch_id)
    # Return 404 for missing batch or tenant mismatch (don't reveal existence)
    tenant_id = get_tenant_id(req)
    if not batch or (
        settings.auth_required
        and tenant_id is not None
        and batch.get("tenant_id") != tenant_id
    ):
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="NOT_FOUND",
                    message=f"Batch '{batch_id}' not found",
                )
            ).model_dump(),
        )

    return ApiResponse(data=batch)


@router.post("/batch/{batch_id}/cancel")
async def cancel_batch(batch_id: str, req: Request) -> ApiResponse:
    """Cancel a running batch.

    Sets the batch status to "cancelled". Pending items are skipped immediately.
    Currently running items are allowed to finish, but no new items will start.
    """
    batch = _batch_store.get(batch_id)
    # Return 404 for missing batch or tenant mismatch (don't reveal existence)
    tenant_id = get_tenant_id(req)
    if not batch or (
        settings.auth_required
        and tenant_id is not None
        and batch.get("tenant_id") != tenant_id
    ):
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="NOT_FOUND",
                    message=f"Batch '{batch_id}' not found",
                )
            ).model_dump(),
        )

    if batch["status"] != "running":
        raise HTTPException(
            status_code=409,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="CONFLICT",
                    message=f"Batch is already '{batch['status']}', cannot cancel",
                )
            ).model_dump(),
        )

    # Signal the background task to stop picking up new items
    batch["_cancelled"] = True
    batch["status"] = "cancelled"

    # Mark all pending items as cancelled
    cancelled_count = 0
    for item in batch["items"]:
        if item["status"] == "pending":
            item["status"] = "cancelled"
            cancelled_count += 1
    batch["pending"] = 0

    return ApiResponse(
        data={
            "status": "cancelled",
            "completed": batch["completed"],
            "cancelled": cancelled_count,
        }
    )


# --- Workflow as API ---


@router.post("/workflows/{name}/publish")
async def publish_workflow_api(
    name: str,
    req: Request,
    strict: bool = Query(
        False,
        description=(
            "If true, enforce the eval gate: a GoldenDataset must exist and "
            "pass the score threshold before promotion."
        ),
    ),
    min_score: float = Query(0.7, ge=0.0, le=1.0),
) -> ApiResponse:
    """Publish a workflow as a public API endpoint.

    Sets is_public=True on the latest production version so it can be
    called via POST /api/v1/{name}. Admin only.

    When `strict=true`, runs the eval gate against the active golden
    dataset before publishing. A failing gate returns HTTP 422 and the
    workflow stays unpublished.
    """
    _require_admin(req)

    async with async_session() as session:
        stmt = (
            select(WorkflowVersion)
            .where(
                WorkflowVersion.workflow_name == name,
                WorkflowVersion.status == WorkflowVersionStatus.PRODUCTION,
            )
            .order_by(WorkflowVersion.version.desc())
            .limit(1)
            .with_for_update()
        )
        result = await session.execute(stmt)
        wv = result.scalar_one_or_none()

        if not wv:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="NOT_FOUND",
                        message=(
                            f"No production version found for workflow '{name}'. "
                            "Promote the workflow to production first."
                        ),
                    )
                ).model_dump(),
            )

        # Strict mode: run the eval gate before flipping the public flag.
        if strict:
            from sandcastle.engine.evals import gate_promotion

            tenant_id = get_tenant_id(req)
            try:
                passed, gate_result = await gate_promotion(
                    workflow_name=name,
                    target_version=wv.version,
                    min_score=min_score,
                    tenant_id=tenant_id,
                )
            except LookupError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=ApiResponse(
                        error=ErrorResponse(
                            code="EVAL_GATE_DATASET_MISSING",
                            message=str(exc),
                        )
                    ).model_dump(),
                )

            if gate_result is None:
                # strict mode requires a dataset
                raise HTTPException(
                    status_code=422,
                    detail=ApiResponse(
                        error=ErrorResponse(
                            code="EVAL_GATE_DATASET_MISSING",
                            message=(
                                "strict=true requires an active GoldenDataset "
                                f"for workflow '{name}'"
                            ),
                        )
                    ).model_dump(),
                )

            if not passed:
                raise HTTPException(
                    status_code=422,
                    detail=ApiResponse(
                        error=ErrorResponse(
                            code="EVAL_GATE_FAILED",
                            message=(
                                f"Eval gate failed: aggregate score "
                                f"{gate_result.aggregate_score:.3f} below "
                                f"threshold {gate_result.threshold:.3f}"
                            ),
                            details={
                                "aggregate_score": round(gate_result.aggregate_score, 4),
                                "threshold": gate_result.threshold,
                                "total_cases": gate_result.total_cases,
                                "passed_cases": gate_result.passed_cases,
                                "dataset_id": gate_result.dataset_id,
                                "dataset_name": gate_result.dataset_name,
                            },
                        )
                    ).model_dump(),
                )

        wv.is_public = True
        await session.commit()

    # Audit trail for EU AI Act compliance
    try:
        from sandcastle.engine.audit import append_audit_event
        async with async_session() as audit_session:
            await append_audit_event(
                audit_session,
                event_type="workflow.published",
                run_id=None,
                actor_id=get_tenant_id(req) or "admin",
                payload={"workflow_name": name, "version": wv.version},
                actor_key_prefix=req.headers.get("X-API-Key", "")[:8],
                source_ip=req.client.host if req.client else None,
            )
            await audit_session.commit()
    except Exception:
        logger.warning("Failed to emit audit event for workflow.published", exc_info=True)

    base_url = str(req.base_url).rstrip("/")
    endpoint_url = f"{base_url}/api/v1/{name}"
    spec_url = f"{base_url}/api/v1/{name}/spec"

    example_curl = (
        f'curl -X POST "{endpoint_url}" \\\n'
        f'  -H "X-API-Key: YOUR_KEY" \\\n'
        f'  -H "Content-Type: application/json" \\\n'
        f"  -d '{{...input_data...}}'"
    )
    example_sdk = (
        "from sandcastle import SandcastleClient\n"
        f'client = SandcastleClient(api_key="YOUR_KEY")\n'
        f'result = client.call_api("{name}", input_data={{...}})'
    )

    return ApiResponse(
        data=WorkflowPublishResponse(
            workflow_name=name,
            version=wv.version,
            endpoint_url=endpoint_url,
            spec_url=spec_url,
            example_curl=example_curl,
            example_sdk=example_sdk,
            is_public=True,
        )
    )


@router.post("/v1/{workflow_name}")
async def run_workflow_api(workflow_name: str, req: Request) -> ApiResponse:
    """Execute a published workflow via its public API endpoint.

    This is the externally-facing API that customers embed in their products.
    Requires an API key with access to this workflow.

    Set header Prefer: respond-async to return a run_id immediately
    instead of waiting for the result.
    Set header X-Callback-URL for async webhook notification on completion.
    """
    await execution_limiter.check(req)
    tenant_id = get_tenant_id(req)

    if not workflow_name or ".." in workflow_name or "/" in workflow_name:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_WORKFLOW_NAME", message="Invalid workflow name")
            ).model_dump(),
        )

    # Workflow-scoped API key check
    allowed_workflows = getattr(req.state, "allowed_workflows", None)
    if allowed_workflows is not None and workflow_name not in allowed_workflows:
        raise HTTPException(
            status_code=403,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="FORBIDDEN",
                    message=f"API key is not authorized to call workflow '{workflow_name}'",
                )
            ).model_dump(),
        )

    api_key_id = getattr(req.state, "api_key_id", None)

    async with async_session() as session:
        pub_stmt = (
            select(WorkflowVersion)
            .where(
                WorkflowVersion.workflow_name == workflow_name,
                WorkflowVersion.status == WorkflowVersionStatus.PRODUCTION,
                WorkflowVersion.is_public.is_(True),
            )
            .order_by(WorkflowVersion.version.desc())
            .limit(1)
        )
        pub_result = await session.execute(pub_stmt)
        wv_pub = pub_result.scalar_one_or_none()

    if not wv_pub:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="NOT_FOUND",
                    message=f"Workflow '{workflow_name}' is not published as a public API",
                )
            ).model_dump(),
        )

    try:
        body = await req.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_REQUEST",
                    message="Request body must be valid JSON",
                )
            ).model_dump(),
        )
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_REQUEST",
                    message="Request body must be a JSON object",
                )
            ).model_dump(),
        )

    callback_url = req.headers.get("X-Callback-URL")
    if callback_url:
        try:
            from sandcastle.webhooks.dispatcher import validate_callback_url
            validate_callback_url(callback_url)
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=ApiResponse(
                    error=ErrorResponse(code="INVALID_CALLBACK_URL", message=str(e))
                ).model_dump(),
            )

    try:
        workflow = parse_yaml_string(wv_pub.yaml_content)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_WORKFLOW", message=str(e))
            ).model_dump(),
        )

    errors = validate(workflow)
    if errors:
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(code="VALIDATION_ERROR", message="; ".join(errors))
            ).model_dump(),
        )

    input_data = dict(body)
    validation_errors = _validate_workflow_input(input_data, workflow.input_schema)
    if validation_errors:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_INPUT", message="; ".join(validation_errors))
            ).model_dump(),
        )

    budget = await _resolve_budget(None, tenant_id)
    run_id = str(uuid.uuid4())

    prefer_async = req.headers.get("Prefer", "").strip().lower() == "respond-async"

    if prefer_async:
        try:
            async with async_session() as session:
                db_run = Run(
                    id=uuid.UUID(run_id),
                    workflow_name=workflow.name,
                    status=RunStatus.QUEUED,
                    input_data=input_data,
                    callback_url=callback_url,
                    tenant_id=tenant_id,
                    max_cost_usd=budget,
                    workflow_version=wv_pub.version,
                    risk_level=getattr(workflow, "risk_level", "minimal"),
                    api_key_id=api_key_id,
                )
                session.add(db_run)
                await session.commit()
        except Exception as e:
            logger.error("Could not create run in database (api/v1 async): %s", e)
            raise HTTPException(
                status_code=500,
                detail=ApiResponse(
                    error=ErrorResponse(code="DB_ERROR", message="Could not create run")
                ).model_dump(),
            )

        try:
            await enqueue_workflow(wv_pub.yaml_content, input_data, run_id)
        except Exception as e:
            try:
                async with async_session() as session:
                    db_run = await session.get(Run, uuid.UUID(run_id))
                    if db_run:
                        db_run.status = RunStatus.FAILED
                        db_run.error = f"Failed to enqueue: {e}"
                        db_run.completed_at = datetime.now(timezone.utc)
                        await session.commit()
            except Exception:
                pass
            raise HTTPException(
                status_code=500,
                detail=ApiResponse(
                    error=ErrorResponse(code="QUEUE_ERROR", message="Could not enqueue job")
                ).model_dump(),
            )

        return ApiResponse(data=RunQueuedResponse(run_id=run_id, status="queued"))

    # Sync execution
    try:
        async with async_session() as session:
            db_run = Run(
                id=uuid.UUID(run_id),
                workflow_name=workflow.name,
                status=RunStatus.RUNNING,
                input_data=input_data,
                callback_url=callback_url,
                tenant_id=tenant_id,
                max_cost_usd=budget,
                workflow_version=wv_pub.version,
                started_at=datetime.now(timezone.utc),
                risk_level=getattr(workflow, "risk_level", "minimal"),
                api_key_id=api_key_id,
            )
            session.add(db_run)
            await session.commit()
    except Exception as e:
        logger.error("Failed to create run record for api/v1 sync: %s", e)
        raise HTTPException(
            status_code=503,
            detail=ApiResponse(
                error=ErrorResponse(code="DB_UNAVAILABLE", message="Database unavailable")
            ).model_dump(),
        )

    try:
        plan = build_plan(workflow)
    except ValueError as e:
        # Mark run as FAILED before returning error
        try:
            async with async_session() as session:
                db_run = await session.get(Run, uuid.UUID(run_id))
                if db_run:
                    db_run.status = RunStatus.FAILED
                    db_run.error = f"Plan error: {e}"
                    db_run.completed_at = datetime.now(timezone.utc)
                    await session.commit()
        except Exception:
            logger.error("Failed to mark run %s as FAILED after plan error", run_id, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(code="PLAN_ERROR", message=str(e))
            ).model_dump(),
        )

    try:
        storage = create_storage()
        result = await execute_workflow(
            workflow=workflow,
            plan=plan,
            input_data=input_data,
            run_id=run_id,
            storage=storage,
            max_cost_usd=budget,
            tenant_id=tenant_id,
        )
    except Exception as e:
        # Mark run as FAILED if execution raises
        try:
            async with async_session() as session:
                db_run = await session.get(Run, uuid.UUID(run_id))
                if db_run:
                    db_run.status = RunStatus.FAILED
                    db_run.error = f"Execution error: {e}"
                    db_run.completed_at = datetime.now(timezone.utc)
                    await session.commit()
        except Exception:
            logger.error("Failed to mark run %s as FAILED after exec error", run_id, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(code="EXECUTION_ERROR", message=str(e))
            ).model_dump(),
        )

    status_map = {
        "completed": RunStatus.COMPLETED,
        "failed": RunStatus.FAILED,
        "cancelled": RunStatus.CANCELLED,
        "budget_exceeded": RunStatus.BUDGET_EXCEEDED,
        "awaiting_approval": RunStatus.AWAITING_APPROVAL,
    }

    try:
        async with async_session() as session:
            db_run = await session.get(Run, uuid.UUID(run_id))
            if db_run:
                db_run.status = status_map.get(result.status, RunStatus.FAILED)
                output_with_report = dict(result.outputs) if result.outputs else {}
                if result.token_report:
                    output_with_report["_token_report"] = result.token_report
                db_run.output_data = output_with_report
                db_run.total_cost_usd = result.total_cost_usd
                if result.status != "awaiting_approval":
                    db_run.completed_at = result.completed_at
                db_run.error = result.error
                await session.commit()
    except Exception:
        logger.error("Failed to update run %s result (api/v1)", run_id, exc_info=True)

    return ApiResponse(
        data=RunStatusResponse(
            run_id=result.run_id,
            workflow_name=workflow.name,
            status=result.status,
            input_data=input_data,
            outputs=result.outputs,
            total_cost_usd=result.total_cost_usd,
            max_cost_usd=budget,
            started_at=result.started_at,
            completed_at=result.completed_at,
            error=result.error,
        )
    )


@router.get("/v1/{workflow_name}/spec")
async def get_workflow_api_spec(workflow_name: str) -> ApiResponse:
    """Return an OpenAPI-compatible spec for a published workflow API endpoint.

    Public endpoint - no authentication required.
    """
    if not workflow_name or ".." in workflow_name or "/" in workflow_name:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_WORKFLOW_NAME", message="Invalid workflow name")
            ).model_dump(),
        )

    async with async_session() as session:
        stmt = (
            select(WorkflowVersion)
            .where(
                WorkflowVersion.workflow_name == workflow_name,
                WorkflowVersion.status == WorkflowVersionStatus.PRODUCTION,
                WorkflowVersion.is_public.is_(True),
            )
            .order_by(WorkflowVersion.version.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        wv = result.scalar_one_or_none()

    if not wv:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="NOT_FOUND",
                    message=f"Workflow '{workflow_name}' is not published as a public API",
                )
            ).model_dump(),
        )

    try:
        workflow = parse_yaml_string(wv.yaml_content)
        input_schema = workflow.input_schema
    except Exception:
        input_schema = None

    return ApiResponse(
        data=WorkflowApiSpecResponse(
            workflow_name=workflow_name,
            version=wv.version,
            endpoint_url=f"/api/v1/{workflow_name}",
            input_schema=input_schema,
        )
    )


@router.get("/v1/{workflow_name}/usage")
async def get_workflow_api_usage(
    workflow_name: str,
    req: Request,
    days: int = Query(30, ge=1, le=365),
) -> ApiResponse:
    """Return usage statistics for a published workflow API endpoint.

    Returns run count, total cost, and average duration for the given period.
    Admin only.
    """
    _require_admin(req)

    if not workflow_name or ".." in workflow_name or "/" in workflow_name:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_WORKFLOW_NAME", message="Invalid workflow name")
            ).model_dump(),
        )

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    async with async_session() as session:
        pub_stmt = select(WorkflowVersion.id).where(
            WorkflowVersion.workflow_name == workflow_name,
            WorkflowVersion.status == WorkflowVersionStatus.PRODUCTION,
            WorkflowVersion.is_public.is_(True),
        )
        pub_result = await session.execute(pub_stmt)
        if pub_result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="NOT_FOUND",
                        message=f"Workflow '{workflow_name}' is not published as a public API",
                    )
                ).model_dump(),
            )

        base_stmt = select(
            func.count(Run.id).label("total"),
            func.sum(case((Run.status == RunStatus.COMPLETED, 1), else_=0)).label("successful"),
            func.sum(case((Run.status == RunStatus.FAILED, 1), else_=0)).label("failed"),
            func.sum(Run.total_cost_usd).label("total_cost"),
        ).where(
            Run.workflow_name == workflow_name,
            Run.api_key_id.is_not(None),
            Run.created_at >= cutoff,
        )
        agg = (await session.execute(base_stmt)).one()

        dur_expr = _duration_seconds_expr()
        dur_stmt = select(dur_expr).where(
            Run.workflow_name == workflow_name,
            Run.api_key_id.is_not(None),
            Run.status == RunStatus.COMPLETED,
            Run.created_at >= cutoff,
            Run.started_at.is_not(None),
            Run.completed_at.is_not(None),
        )
        avg_dur = await session.scalar(dur_stmt)

        day_stmt = (
            select(
                _trunc_day(Run.created_at).label("day"),
                func.count(Run.id).label("total"),
                func.sum(case((Run.status == RunStatus.COMPLETED, 1), else_=0)).label("completed"),
                func.sum(case((Run.status == RunStatus.FAILED, 1), else_=0)).label("failed"),
            )
            .where(
                Run.workflow_name == workflow_name,
                Run.api_key_id.is_not(None),
                Run.created_at >= cutoff,
            )
            .group_by(_trunc_day(Run.created_at))
            .order_by(_trunc_day(Run.created_at))
        )
        day_rows = (await session.execute(day_stmt)).all()

    runs_by_day = [
        {
            "date": str(row.day),
            "total": row.total or 0,
            "completed": row.completed or 0,
            "failed": row.failed or 0,
        }
        for row in day_rows
    ]

    return ApiResponse(
        data=WorkflowApiUsageResponse(
            workflow_name=workflow_name,
            period_days=days,
            total_runs=agg.total or 0,
            successful_runs=agg.successful or 0,
            failed_runs=agg.failed or 0,
            total_cost_usd=float(agg.total_cost or 0.0),
            avg_duration_seconds=float(avg_dur) if avg_dur is not None else None,
            runs_by_day=runs_by_day,
        )
    )



# ---------------------------------------------------------------------------
# Tool Registry endpoints
# ---------------------------------------------------------------------------


async def _get_tool_connections(tool_name: str) -> list[ToolConnectionResponse]:
    """Load named connections for a tool from the database."""
    async with async_session() as session:
        result = await session.execute(
            select(ToolConnection).where(ToolConnection.tool_name == tool_name)
        )
        rows = result.scalars().all()
    from sandcastle.engine.tools.registry import get_tool as _get_tool

    try:
        tool = _get_tool(tool_name)
    except KeyError:
        return []
    from sandcastle.engine.crypto import decrypt_credentials

    required_vars = set(tool.credential_env_vars)
    connections: list[ToolConnectionResponse] = []
    for row in rows:
        creds = decrypt_credentials(row.credentials)
        present = [k for k in required_vars if creds.get(k)]
        missing = [k for k in required_vars if not creds.get(k)]
        connections.append(
            ToolConnectionResponse(
                name=row.connection_name,
                tool_name=row.tool_name,
                credentials_configured=sorted(present),
                credentials_missing=sorted(missing),
                created_at=row.created_at,
            )
        )
    return connections


async def _build_tool_response(
    tool,
    status: dict,
    connections: list[ToolConnectionResponse] | None = None,
) -> ToolResponse:
    """Build a ToolResponse from a ToolDefinition, cred status, and connections."""
    return ToolResponse(
        name=tool.name,
        description=tool.description,
        category=tool.category,
        functions=[
            ToolFunctionResponse(
                name=f.name,
                description=f.description,
                parameters=f.parameters,
            )
            for f in tool.functions
        ],
        credential_env_vars=tool.credential_env_vars,
        connector_file=tool.connector_file,
        icon=tool.icon,
        configured=status.get("configured", False),
        missing_credentials=status.get("missing", []),
        connections=connections or [],
        keyless=status.get("keyless", False),
        optional_credential_env_vars=getattr(tool, "optional_credential_env_vars", []),
        optional_present=status.get("optional_present", []),
    )


@router.get("/tools")
async def list_tools(category: str | None = Query(None)) -> ApiResponse:
    """List all available tool connectors with credential status."""
    from sandcastle.engine.tools.credentials import validate_tool_credentials
    from sandcastle.engine.tools.registry import list_tools as _list_tools

    tools = _list_tools(category=category)
    tool_names = [t.name for t in tools]
    cred_status = validate_tool_credentials(tool_names)

    # Batch-load all connections
    all_connections: dict[str, list[ToolConnectionResponse]] = {}
    async with async_session() as session:
        result = await session.execute(select(ToolConnection))
        rows = result.scalars().all()
    from sandcastle.engine.crypto import decrypt_credentials

    for row in rows:
        all_connections.setdefault(row.tool_name, [])
        tool_def = next((t for t in tools if t.name == row.tool_name), None)
        required_vars = set(tool_def.credential_env_vars) if tool_def else set()
        creds = decrypt_credentials(row.credentials)
        present = [k for k in required_vars if creds.get(k)]
        missing = [k for k in required_vars if not creds.get(k)]
        all_connections[row.tool_name].append(
            ToolConnectionResponse(
                name=row.connection_name,
                tool_name=row.tool_name,
                credentials_configured=sorted(present),
                credentials_missing=sorted(missing),
                created_at=row.created_at,
            )
        )

    result_list = []
    for tool in tools:
        status = cred_status.get(tool.name, {})
        resp = await _build_tool_response(
            tool,
            status,
            all_connections.get(tool.name, []),
        )
        result_list.append(resp)

    return ApiResponse(data=ToolListResponse(tools=result_list, total=len(result_list)))


@router.get("/tools/{tool_name}")
async def get_tool(tool_name: str) -> ApiResponse:
    """Get details of a specific tool connector."""
    from sandcastle.engine.tools.credentials import validate_tool_credentials
    from sandcastle.engine.tools.registry import get_tool as _get_tool

    try:
        tool = _get_tool(tool_name)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message=f"Tool '{tool_name}' not found")
            ).model_dump(),
        )

    status = validate_tool_credentials([tool_name]).get(tool_name, {})
    connections = await _get_tool_connections(tool_name)

    return ApiResponse(data=await _build_tool_response(tool, status, connections))


@router.put("/tools/{tool_name}/credentials")
async def update_tool_credentials(
    tool_name: str,
    body: ToolCredentialUpdateRequest,
    req: Request,
) -> ApiResponse:
    """Save or update credentials for a specific tool connector."""
    _require_admin(req)
    from sandcastle.engine.tools.credentials import validate_tool_credentials
    from sandcastle.engine.tools.registry import get_tool as _get_tool

    try:
        tool = _get_tool(tool_name)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message=f"Tool '{tool_name}' not found")
            ).model_dump(),
        )

    # Only allow setting env vars that belong to this tool (required + optional
    # upgrade keys, e.g. a free key that lifts a keyless tool's rate limit).
    allowed = set(tool.credential_env_vars) | set(
        getattr(tool, "optional_credential_env_vars", [])
    )
    rejected = set(body.credentials.keys()) - allowed
    if rejected:
        raise HTTPException(
            status_code=422,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_CREDENTIALS",
                    message=(
                        f"Unknown credential keys for {tool_name}:"
                        f" {', '.join(sorted(rejected))}"
                    ),
                )
            ).model_dump(),
        )

    # Persist to DB (encrypted if key is configured) and apply to runtime
    from sandcastle.engine.crypto import encrypt_credentials as _enc_cred

    async with async_session() as session:
        for env_key, env_value in body.credentials.items():
            # Encrypt the value before storing (passthrough if no key)
            stored_value = _enc_cred({"v": env_value})
            db_value = stored_value if isinstance(stored_value, str) else env_value
            existing = await session.get(Setting, env_key)
            if existing:
                existing.value = db_value
            else:
                session.add(Setting(key=env_key, value=db_value))
            # Apply to runtime so tools work immediately (plaintext in memory)
            os.environ[env_key] = env_value
            # Also update settings object if the field exists
            config_key = env_key.lower()
            if hasattr(settings, config_key):
                setattr(settings, config_key, env_value)
        await session.commit()

    # Return updated status
    status = validate_tool_credentials([tool_name]).get(tool_name, {})
    connections = await _get_tool_connections(tool_name)
    return ApiResponse(data=await _build_tool_response(tool, status, connections))


# --- Named connection CRUD ---


@router.get("/tools/{tool_name}/connections")
async def list_tool_connections(tool_name: str) -> ApiResponse:
    """List all named connections for a tool."""
    from sandcastle.engine.tools.registry import get_tool as _get_tool

    try:
        _get_tool(tool_name)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message=f"Tool '{tool_name}' not found")
            ).model_dump(),
        )

    connections = await _get_tool_connections(tool_name)
    return ApiResponse(data=connections)


@router.post("/tools/{tool_name}/connections")
async def create_tool_connection(
    tool_name: str,
    body: ToolConnectionCreateRequest,
    req: Request,
) -> ApiResponse:
    """Create a named connection for a tool."""
    _require_admin(req)
    from sandcastle.engine.tools.registry import get_tool as _get_tool

    try:
        tool = _get_tool(tool_name)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message=f"Tool '{tool_name}' not found")
            ).model_dump(),
        )

    # Validate credential keys
    allowed = set(tool.credential_env_vars)
    rejected = set(body.credentials.keys()) - allowed
    if rejected:
        raise HTTPException(
            status_code=422,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_CREDENTIALS",
                    message=(
                        f"Unknown credential keys for {tool_name}:"
                        f" {', '.join(sorted(rejected))}"
                    ),
                )
            ).model_dump(),
        )

    async with async_session() as session:
        # Check for duplicate
        existing = await session.execute(
            select(ToolConnection).where(
                ToolConnection.tool_name == tool_name,
                ToolConnection.connection_name == body.name,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="DUPLICATE_CONNECTION",
                        message=f"Connection '{body.name}' already exists for {tool_name}",
                    )
                ).model_dump(),
            )

        from sandcastle.engine.crypto import encrypt_credentials

        conn = ToolConnection(
            tool_name=tool_name,
            connection_name=body.name,
            credentials=encrypt_credentials(body.credentials),
        )
        session.add(conn)
        await session.commit()
        await session.refresh(conn)

    from sandcastle.engine.crypto import decrypt_credentials

    required_vars = set(tool.credential_env_vars)
    creds = decrypt_credentials(conn.credentials)
    present = [k for k in required_vars if creds.get(k)]
    missing = [k for k in required_vars if not creds.get(k)]

    return ApiResponse(
        data=ToolConnectionResponse(
            name=conn.connection_name,
            tool_name=conn.tool_name,
            credentials_configured=sorted(present),
            credentials_missing=sorted(missing),
            created_at=conn.created_at,
        )
    )


@router.put("/tools/{tool_name}/connections/{conn_name}")
async def update_tool_connection(
    tool_name: str,
    conn_name: str,
    body: ToolConnectionUpdateRequest,
    req: Request,
) -> ApiResponse:
    """Update credentials for a named connection."""
    _require_admin(req)
    from sandcastle.engine.tools.registry import get_tool as _get_tool

    try:
        tool = _get_tool(tool_name)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message=f"Tool '{tool_name}' not found")
            ).model_dump(),
        )

    allowed = set(tool.credential_env_vars)
    rejected = set(body.credentials.keys()) - allowed
    if rejected:
        raise HTTPException(
            status_code=422,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_CREDENTIALS",
                    message=(
                        f"Unknown credential keys for {tool_name}:"
                        f" {', '.join(sorted(rejected))}"
                    ),
                )
            ).model_dump(),
        )

    async with async_session() as session:
        result = await session.execute(
            select(ToolConnection).where(
                ToolConnection.tool_name == tool_name,
                ToolConnection.connection_name == conn_name,
            )
        )
        conn = result.scalar_one_or_none()
        if not conn:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="NOT_FOUND",
                        message=f"Connection '{conn_name}' not found for {tool_name}",
                    )
                ).model_dump(),
            )

        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        # Merge new credentials into existing (decrypt first)
        merged = dict(decrypt_credentials(conn.credentials))
        merged.update(body.credentials)
        conn.credentials = encrypt_credentials(merged)
        await session.commit()
        await session.refresh(conn)

    from sandcastle.engine.crypto import decrypt_credentials as _dc

    required_vars = set(tool.credential_env_vars)
    creds = _dc(conn.credentials)
    present = [k for k in required_vars if creds.get(k)]
    missing = [k for k in required_vars if not creds.get(k)]

    return ApiResponse(
        data=ToolConnectionResponse(
            name=conn.connection_name,
            tool_name=conn.tool_name,
            credentials_configured=sorted(present),
            credentials_missing=sorted(missing),
            created_at=conn.created_at,
        )
    )


@router.delete("/tools/{tool_name}/connections/{conn_name}")
async def delete_tool_connection(
    tool_name: str,
    conn_name: str,
    req: Request,
) -> ApiResponse:
    """Delete a named connection."""
    _require_admin(req)
    from sandcastle.engine.tools.registry import get_tool as _get_tool

    try:
        _get_tool(tool_name)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message=f"Tool '{tool_name}' not found")
            ).model_dump(),
        )

    async with async_session() as session:
        result = await session.execute(
            select(ToolConnection).where(
                ToolConnection.tool_name == tool_name,
                ToolConnection.connection_name == conn_name,
            )
        )
        conn = result.scalar_one_or_none()
        if not conn:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(
                        code="NOT_FOUND",
                        message=f"Connection '{conn_name}' not found for {tool_name}",
                    )
                ).model_dump(),
            )

        await session.delete(conn)
        await session.commit()

    return ApiResponse(data={"deleted": True})


# ---------------------------------------------------------------------------
# Evaluations
# ---------------------------------------------------------------------------


@router.post("/eval/run")
async def run_eval_suite_endpoint(req: Request, body: EvalSuiteRunRequest) -> ApiResponse:
    """Run an eval suite from YAML and return results."""
    _require_admin(req)
    from sandcastle.engine.eval import (
        parse_eval_suite_string,
        run_eval_suite,
    )

    try:
        suite = parse_eval_suite_string(body.suite_yaml)
    except (ValueError, Exception) as exc:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_SUITE", message=str(exc))
            ).model_dump(),
        )

    # Create a running eval_run record
    eval_run_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    tenant_id = get_tenant_id(req)
    async with async_session() as session:
        eval_run = EvalRun(
            id=eval_run_id,
            suite_name=suite.description or suite.workflow,
            workflow_name=suite.workflow,
            status=EvalRunStatus.RUNNING,
            total_cases=len(suite.cases),
            suite_yaml=body.suite_yaml,
            started_at=now,
            tenant_id=tenant_id,
        )
        session.add(eval_run)
        await session.commit()

    # Run the suite
    try:
        result = await run_eval_suite(suite, concurrency=body.concurrency)
    except Exception as exc:
        # Mark as failed
        async with async_session() as session:
            run = await session.get(EvalRun, eval_run_id)
            if run:
                run.status = EvalRunStatus.FAILED
                run.completed_at = datetime.now(timezone.utc)
                await session.commit()
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(code="EVAL_FAILED", message=str(exc))
            ).model_dump(),
        )

    # Persist results
    completed_at = datetime.now(timezone.utc)
    async with async_session() as session:
        run = await session.get(EvalRun, eval_run_id)
        if run:
            run.status = EvalRunStatus.COMPLETED
            run.passed_cases = result.passed
            run.failed_cases = result.failed
            run.pass_rate = result.pass_rate
            run.total_cost_usd = result.total_cost_usd
            run.total_duration_seconds = result.total_duration_seconds
            run.completed_at = completed_at

            from sandcastle.models.db import EvalCaseResult as EvalCaseResultModel

            for cr in result.cases:
                assertion_data = [
                    {
                        "type": ar.type,
                        "passed": ar.passed,
                        "expected": ar.expected,
                        "actual": ar.actual,
                        "message": ar.message,
                        "score": ar.score,
                    }
                    for ar in cr.assertions
                ]
                session.add(
                    EvalCaseResultModel(
                        eval_run_id=eval_run_id,
                        case_name=cr.name,
                        passed=cr.passed,
                        run_id=uuid.UUID(cr.run_id) if cr.run_id else None,
                        cost_usd=cr.cost_usd,
                        duration_seconds=cr.duration_seconds,
                        assertions=assertion_data,
                        output_summary=str(cr.output)[:500] if cr.output else None,
                        error=cr.error,
                    )
                )
            await session.commit()

    # Build response
    cases_data = [
        EvalCaseResponse(
            case_name=cr.name,
            passed=cr.passed,
            run_id=cr.run_id,
            cost_usd=cr.cost_usd,
            duration_seconds=cr.duration_seconds,
            assertions=[
                EvalAssertionResponse(
                    type=ar.type,
                    passed=ar.passed,
                    expected=ar.expected,
                    actual=ar.actual,
                    message=ar.message,
                    score=ar.score,
                )
                for ar in cr.assertions
            ],
            output_summary=str(cr.output)[:500] if cr.output else None,
            error=cr.error,
        )
        for cr in result.cases
    ]

    return ApiResponse(
        data=EvalRunResponse(
            id=str(eval_run_id),
            suite_name=result.suite_name,
            workflow_name=result.workflow,
            status="completed",
            total_cases=result.total,
            passed_cases=result.passed,
            failed_cases=result.failed,
            pass_rate=result.pass_rate,
            total_cost_usd=result.total_cost_usd,
            total_duration_seconds=result.total_duration_seconds,
            started_at=now,
            completed_at=completed_at,
            cases=cases_data,
        )
    )


@router.get("/eval/runs")
async def list_eval_runs(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List eval run history."""
    _require_admin(request)
    tenant_id = get_tenant_id(request)

    async with async_session() as session:
        count_stmt = select(func.count(EvalRun.id))
        stmt = select(EvalRun).order_by(EvalRun.created_at.desc()).offset(offset).limit(limit)

        # Tenant isolation for eval runs
        if settings.auth_required and tenant_id is not None:
            count_stmt = count_stmt.where(EvalRun.tenant_id == tenant_id)
            stmt = stmt.where(EvalRun.tenant_id == tenant_id)

        total = await session.scalar(count_stmt)
        result = await session.execute(stmt)
        items = result.scalars().all()

    data = [
        EvalRunResponse(
            id=str(r.id),
            suite_name=r.suite_name,
            workflow_name=r.workflow_name,
            status=r.status.value if hasattr(r.status, "value") else r.status,
            total_cases=r.total_cases,
            passed_cases=r.passed_cases,
            failed_cases=r.failed_cases,
            pass_rate=r.pass_rate,
            total_cost_usd=r.total_cost_usd,
            total_duration_seconds=r.total_duration_seconds,
            started_at=r.started_at,
            completed_at=r.completed_at,
            created_at=r.created_at,
        )
        for r in items
    ]

    return ApiResponse(
        data=data,
        meta=PaginationMeta(total=total or 0, limit=limit, offset=offset),
    )


@router.get("/eval/runs/{eval_run_id}")
async def get_eval_run(req: Request, eval_run_id: str) -> ApiResponse:
    """Get eval run details with case results."""
    _require_admin(req)
    tenant_id = get_tenant_id(req)
    try:
        run_uuid = uuid.UUID(eval_run_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid eval run ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        stmt = (
            select(EvalRun)
            .options(selectinload(EvalRun.case_results))
            .where(EvalRun.id == run_uuid)
        )
        # Tenant isolation for eval runs
        if settings.auth_required and tenant_id is not None:
            stmt = stmt.where(EvalRun.tenant_id == tenant_id)
        result = await session.execute(stmt)
        run = result.scalar_one_or_none()

    if not run:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message="Eval run not found")
            ).model_dump(),
        )

    cases_data = [
        EvalCaseResponse(
            case_name=cr.case_name,
            passed=cr.passed,
            run_id=str(cr.run_id) if cr.run_id else None,
            cost_usd=cr.cost_usd,
            duration_seconds=cr.duration_seconds,
            assertions=[EvalAssertionResponse(**a) for a in (cr.assertions or [])],
            output_summary=cr.output_summary,
            error=cr.error,
        )
        for cr in run.case_results
    ]

    return ApiResponse(
        data=EvalRunResponse(
            id=str(run.id),
            suite_name=run.suite_name,
            workflow_name=run.workflow_name,
            status=run.status.value if hasattr(run.status, "value") else run.status,
            total_cases=run.total_cases,
            passed_cases=run.passed_cases,
            failed_cases=run.failed_cases,
            pass_rate=run.pass_rate,
            total_cost_usd=run.total_cost_usd,
            total_duration_seconds=run.total_duration_seconds,
            started_at=run.started_at,
            completed_at=run.completed_at,
            created_at=run.created_at,
            cases=cases_data,
        )
    )


@router.get("/eval/stats")
async def eval_stats(req: Request) -> ApiResponse:
    """Get aggregated eval statistics with 30-day trend."""
    _require_admin(req)
    tenant_id = get_tenant_id(req)

    async with async_session() as session:
        # Build tenant-aware base queries
        count_stmt = select(func.count(EvalRun.id))
        avg_stmt = select(func.avg(EvalRun.pass_rate))
        cost_stmt = select(func.sum(EvalRun.total_cost_usd))
        last_run_stmt = select(EvalRun.completed_at).order_by(EvalRun.created_at.desc()).limit(1)

        if settings.auth_required and tenant_id is not None:
            count_stmt = count_stmt.where(EvalRun.tenant_id == tenant_id)
            avg_stmt = avg_stmt.where(EvalRun.tenant_id == tenant_id)
            cost_stmt = cost_stmt.where(EvalRun.tenant_id == tenant_id)
            last_run_stmt = last_run_stmt.where(EvalRun.tenant_id == tenant_id)

        total_runs = await session.scalar(count_stmt)
        avg_pass_rate = await session.scalar(avg_stmt)
        total_cost = await session.scalar(cost_stmt)

        # Last run
        last_run_at = await session.scalar(last_run_stmt)

        # 30-day pass rate trend
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        trend_stmt = (
            select(EvalRun)
            .where(EvalRun.created_at >= thirty_days_ago)
            .order_by(EvalRun.created_at.asc())
        )
        if settings.auth_required and tenant_id is not None:
            trend_stmt = trend_stmt.where(EvalRun.tenant_id == tenant_id)
        trend_result = await session.execute(trend_stmt)
        trend_runs = trend_result.scalars().all()

    # Group by date for trend (skip runs with None pass_rate)
    trend_by_date: dict[str, list[float]] = {}
    for r in trend_runs:
        if r.pass_rate is None:
            continue
        date_str = r.created_at.strftime("%Y-%m-%d") if r.created_at else "unknown"
        if date_str not in trend_by_date:
            trend_by_date[date_str] = []
        trend_by_date[date_str].append(r.pass_rate)

    pass_rate_trend = [
        {
            "date": date,
            "avg_pass_rate": sum(rates) / len(rates),
            "runs": len(rates),
        }
        for date, rates in trend_by_date.items()
    ]

    return ApiResponse(
        data=EvalStatsResponse(
            total_runs=total_runs or 0,
            avg_pass_rate=round(avg_pass_rate or 0.0, 4),
            total_cost_usd=round(total_cost or 0.0, 4),
            last_run_at=last_run_at,
            pass_rate_trend=pass_rate_trend,
        )
    )


# ---------------------------------------------------------------------------
# Golden datasets + eval gates
# ---------------------------------------------------------------------------


def _serialize_dataset(dataset: GoldenDataset, cases: list[GoldenCase]) -> dict[str, Any]:
    """Serialize a GoldenDataset (+cases) into a JSON-friendly dict."""
    return {
        "id": str(dataset.id),
        "tenant_id": dataset.tenant_id,
        "name": dataset.name,
        "workflow_name": dataset.workflow_name,
        "version": dataset.version,
        "description": dataset.description,
        "is_active": dataset.is_active,
        "created_at": dataset.created_at.isoformat() if dataset.created_at else None,
        "cases": [
            {
                "id": str(c.id),
                "case_label": c.case_label,
                "input_data": c.input_data,
                "expected_output": c.expected_output,
                "expected_score_min": c.expected_score_min,
            }
            for c in cases
        ],
    }


@router.post("/golden-datasets", status_code=201)
async def create_golden_dataset(req: Request) -> ApiResponse:
    """Create a golden dataset (with its cases) for a workflow.

    Body:
        {
          "name": "summarize-v1",
          "workflow_name": "summarize",
          "version": 1,                  # optional, default 1
          "description": "...",          # optional
          "is_active": true,             # optional
          "cases": [
            {
              "case_label": "short text",
              "input_data": {"text": "..."},
              "expected_output": {"summary": "..."},
              "expected_score_min": 0.7
            }
          ]
        }
    """
    _require_admin(req)
    try:
        body = await req.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_BODY", message="Invalid JSON body")
            ).model_dump(),
        )

    name = (body or {}).get("name")
    workflow_name = (body or {}).get("workflow_name")
    if not name or not workflow_name:
        raise HTTPException(
            status_code=422,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_DATASET",
                    message="'name' and 'workflow_name' are required",
                )
            ).model_dump(),
        )

    tenant_id = get_tenant_id(req)
    cases_payload = body.get("cases", []) or []
    if not isinstance(cases_payload, list):
        raise HTTPException(
            status_code=422,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_CASES",
                    message="'cases' must be a list",
                )
            ).model_dump(),
        )

    dataset = GoldenDataset(
        tenant_id=tenant_id,
        name=name,
        workflow_name=workflow_name,
        version=int(body.get("version", 1) or 1),
        description=str(body.get("description", "") or ""),
        is_active=bool(body.get("is_active", True)),
    )

    cases_models: list[GoldenCase] = []
    for c in cases_payload:
        if not isinstance(c, dict):
            continue
        score_min = c.get("expected_score_min", 0.7)
        try:
            score_min = float(score_min)
        except (TypeError, ValueError):
            score_min = 0.7
        score_min = max(0.0, min(1.0, score_min))
        cases_models.append(
            GoldenCase(
                case_label=str(c.get("case_label", "") or ""),
                input_data=c.get("input_data") or {},
                expected_output=c.get("expected_output"),
                expected_score_min=score_min,
            )
        )
    # Attach via relationship so the FK is populated on flush.
    dataset.cases = cases_models

    async with async_session() as session:
        session.add(dataset)
        await session.commit()

    return ApiResponse(data=_serialize_dataset(dataset, cases_models))


@router.get("/golden-datasets/{name}")
async def get_golden_dataset(req: Request, name: str) -> ApiResponse:
    """Retrieve a golden dataset by name (most recent version)."""
    _require_admin(req)
    tenant_id = get_tenant_id(req)

    async with async_session() as session:
        stmt = (
            select(GoldenDataset)
            .options(selectinload(GoldenDataset.cases))
            .where(GoldenDataset.name == name)
            .order_by(GoldenDataset.version.desc(), GoldenDataset.created_at.desc())
        )
        stmt = _apply_tenant_filter(stmt, tenant_id, GoldenDataset.tenant_id)
        dataset = (await session.execute(stmt)).scalars().first()

    if dataset is None:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="NOT_FOUND", message=f"Golden dataset '{name}' not found"
                )
            ).model_dump(),
        )

    return ApiResponse(data=_serialize_dataset(dataset, list(dataset.cases)))


@router.post("/workflows/{name}/eval-gate")
async def run_workflow_eval_gate(
    name: str,
    req: Request,
    version: int | None = Query(None, ge=1),
    min_score: float = Query(0.7, ge=0.0, le=1.0),
) -> ApiResponse:
    """Run the eval gate for a workflow without promoting it.

    Looks up the active golden dataset for the workflow and replays it.
    Returns pass/fail + aggregate score + per-case results.
    """
    _require_admin(req)
    tenant_id = get_tenant_id(req)

    from sandcastle.engine.evals import gate_promotion

    try:
        passed, gate_result = await gate_promotion(
            workflow_name=name,
            target_version=version,
            min_score=min_score,
            tenant_id=tenant_id,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(code="NOT_FOUND", message=str(exc))
            ).model_dump(),
        )

    if gate_result is None:
        return ApiResponse(
            data={
                "workflow_name": name,
                "passed": True,
                "skipped": True,
                "reason": "no active golden dataset",
            }
        )

    return ApiResponse(
        data={
            "workflow_name": name,
            "version": version,
            "dataset_id": gate_result.dataset_id,
            "dataset_name": gate_result.dataset_name,
            "passed": passed,
            "aggregate_score": round(gate_result.aggregate_score, 4),
            "threshold": gate_result.threshold,
            "total_cases": gate_result.total_cases,
            "passed_cases": gate_result.passed_cases,
            "cases": [
                {
                    "case_label": c.case_label,
                    "passed": c.passed,
                    "score": round(c.score, 4),
                    "expected_score_min": c.expected_score_min,
                    "error": c.error,
                }
                for c in gate_result.cases
            ],
        }
    )


# ---------------------------------------------------------------------------
# Memory endpoints
# ---------------------------------------------------------------------------

import re as _re  # noqa: E402

_SCOPE_ID_RE = _re.compile(
    r"^(workflow:[a-zA-Z0-9_. -]{1,200}"
    r"|agent:[a-zA-Z0-9_. -]{1,200}"
    r"|global)$"
)
_MEMORY_ID_RE = _re.compile(r"^[a-zA-Z0-9_-]{1,200}$")


def _validate_scope_id(scope_id: str) -> str:
    """Validate and return scope_id, or raise 422."""
    if not _SCOPE_ID_RE.match(scope_id):
        raise HTTPException(
            status_code=422,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_SCOPE_ID",
                    message=(
                        "scope_id must match 'workflow:<name>', "
                        "'agent:<name>', or 'global'"
                    ),
                )
            ).model_dump(),
        )
    return scope_id


def _validate_memory_id(memory_id: str) -> str:
    """Validate memory_id format, or raise 422."""
    if not _MEMORY_ID_RE.match(memory_id):
        raise HTTPException(
            status_code=422,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_MEMORY_ID",
                    message="memory_id must be alphanumeric with hyphens/underscores (max 200 chars)",
                )
            ).model_dump(),
        )
    return memory_id


@router.get("/memories")
async def list_memories(
    req: Request,
    scope_id: str = Query(
        ..., description="Scope ID (e.g. 'workflow:my-wf', 'agent:bot', 'global')"
    ),
    limit: int = Query(50, ge=1, le=200),
):
    """List all memories for a given scope."""
    _require_admin(req)
    _validate_scope_id(scope_id)
    from sandcastle.engine.memory import MemoryBackendError, load_memories

    try:
        memories = await load_memories(scope_id, limit=limit)
    except MemoryBackendError as exc:
        raise HTTPException(
            status_code=503,
            detail=ApiResponse(
                error=ErrorResponse(code="MEMORY_UNAVAILABLE", message=str(exc))
            ).model_dump(),
        )
    entries = [MemoryEntry(**m) for m in memories]
    return ApiResponse(data=MemoryListResponse(memories=entries, total=len(entries)))


@router.post("/memories")
async def add_memory(req: Request, body: MemoryAddRequest):
    """Add a new memory. Mem0 auto-extracts facts and deduplicates."""
    _require_admin(req)
    from sandcastle.engine.memory import save_memory

    result = await save_memory(
        scope_id=body.scope_id,
        content=body.content,
        metadata=body.metadata,
    )
    return ApiResponse(data={"added": len(result), "results": result})


@router.post("/memories/search")
async def search_memories(req: Request, body: MemorySearchRequest):
    """Semantic search over memories."""
    _require_admin(req)
    from sandcastle.engine.memory import MemoryBackendError, load_memories

    try:
        memories = await load_memories(body.scope_id, query=body.query, limit=body.limit)
    except MemoryBackendError as exc:
        raise HTTPException(
            status_code=503,
            detail=ApiResponse(
                error=ErrorResponse(code="MEMORY_UNAVAILABLE", message=str(exc))
            ).model_dump(),
        )
    entries = [MemoryEntry(**m) for m in memories]
    return ApiResponse(data=MemoryListResponse(memories=entries, total=len(entries)))


@router.delete("/memories/{memory_id}")
async def remove_memory(req: Request, memory_id: str):
    """Delete a specific memory by ID."""
    _require_admin(req)
    _validate_memory_id(memory_id)
    from sandcastle.engine.memory import delete_memory

    ok = await delete_memory(memory_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="NOT_FOUND", message="Memory not found or delete failed"
                )
            ).model_dump(),
        )
    return ApiResponse(data={"deleted": True})


@router.delete("/memories")
async def remove_all_memories(
    req: Request,
    scope_id: str = Query(..., description="Scope ID to clear"),
):
    """Delete all memories for a given scope."""
    _require_admin(req)
    _validate_scope_id(scope_id)
    from sandcastle.engine.memory import delete_all_memories

    ok = await delete_all_memories(scope_id)
    if not ok:
        raise HTTPException(
            status_code=500,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="DELETE_FAILED", message="Failed to delete memories"
                )
            ).model_dump(),
        )
    return ApiResponse(data={"deleted_all": True, "scope_id": scope_id})


# --- Audit Trail ---


def _audit_event_to_response(ev: AuditEvent) -> AuditEventResponse:
    """Convert an AuditEvent ORM row to a response schema."""
    return AuditEventResponse(
        id=str(ev.id),
        event_type=ev.event_type,
        run_id=str(ev.run_id) if ev.run_id else None,
        actor_id=ev.actor_id,
        actor_key_prefix=ev.actor_key_prefix,
        source_ip=ev.source_ip,
        payload=ev.payload,
        prev_hash=ev.prev_hash,
        entry_hash=ev.entry_hash,
        created_at=ev.created_at,
    )


@router.get("/audit")
async def list_audit_events(
    req: Request,
    run_id: str | None = Query(None, description="Filter by run ID"),
    actor_id: str | None = Query(None, description="Filter by actor ID"),
    event_type: str | None = Query(None, description="Filter by event type"),
    since: str | None = Query(None, description="ISO datetime lower bound (inclusive)"),
    until: str | None = Query(None, description="ISO datetime upper bound (inclusive)"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """List audit events (admin only). Supports filtering by run, actor, type, and date range."""
    _require_admin(req)

    async with async_session() as session:
        base = select(AuditEvent)
        count_base = select(func.count(AuditEvent.id))

        if run_id is not None:
            try:
                run_uuid = uuid.UUID(run_id)
                base = base.where(AuditEvent.run_id == run_uuid)
                count_base = count_base.where(AuditEvent.run_id == run_uuid)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=ApiResponse(
                        error=ErrorResponse(code="INVALID_ID", message="Invalid run_id format")
                    ).model_dump(),
                )

        if actor_id is not None:
            base = base.where(AuditEvent.actor_id == actor_id)
            count_base = count_base.where(AuditEvent.actor_id == actor_id)

        if event_type is not None:
            base = base.where(AuditEvent.event_type == event_type)
            count_base = count_base.where(AuditEvent.event_type == event_type)

        if since is not None:
            try:
                since_dt = datetime.fromisoformat(since)
                base = base.where(AuditEvent.created_at >= since_dt)
                count_base = count_base.where(AuditEvent.created_at >= since_dt)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=ApiResponse(
                        error=ErrorResponse(code="INVALID_DATE", message="Invalid 'since' datetime format")
                    ).model_dump(),
                )

        if until is not None:
            try:
                until_dt = datetime.fromisoformat(until)
                base = base.where(AuditEvent.created_at <= until_dt)
                count_base = count_base.where(AuditEvent.created_at <= until_dt)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=ApiResponse(
                        error=ErrorResponse(code="INVALID_DATE", message="Invalid 'until' datetime format")
                    ).model_dump(),
                )

        total = await session.scalar(count_base) or 0
        stmt = base.order_by(AuditEvent.created_at.desc()).offset(offset).limit(limit)
        result = await session.execute(stmt)
        events = result.scalars().all()

    return ApiResponse(
        data=[_audit_event_to_response(ev) for ev in events],
        meta=PaginationMeta(total=total, limit=limit, offset=offset),
    )


@router.get("/runs/{run_id}/audit")
async def get_run_audit(
    run_id: str,
    req: Request,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> ApiResponse:
    """Return the full audit trail for a specific run."""
    tenant_id = get_tenant_id(req)

    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid run ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        # Verify the run exists and belongs to the tenant
        run_stmt = select(Run).where(Run.id == run_uuid)
        run_stmt = _apply_tenant_filter(run_stmt, tenant_id, Run.tenant_id)
        run_result = await session.execute(run_stmt)
        run = run_result.scalar_one_or_none()
        if not run:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message=f"Run '{run_id}' not found")
                ).model_dump(),
            )

        total_stmt = select(func.count(AuditEvent.id)).where(AuditEvent.run_id == run_uuid)
        total = await session.scalar(total_stmt) or 0

        stmt = (
            select(AuditEvent)
            .where(AuditEvent.run_id == run_uuid)
            .order_by(AuditEvent.created_at.asc())
            .offset(offset)
            .limit(limit)
        )
        result = await session.execute(stmt)
        events = result.scalars().all()

    return ApiResponse(
        data=[_audit_event_to_response(ev) for ev in events],
        meta=PaginationMeta(total=total, limit=limit, offset=offset),
    )


@router.get("/audit/verify/{run_id}")
async def verify_run_audit(run_id: str, req: Request) -> ApiResponse:
    """Verify the tamper-evident hash chain for a run's audit trail."""
    _require_admin(req)

    try:
        uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid run ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        valid, chain_length, broken_at = await verify_audit_chain(session, run_id)

    return ApiResponse(
        data=AuditVerifyResponse(
            run_id=run_id,
            valid=valid,
            chain_length=chain_length,
            broken_at=broken_at,
        )
    )


# --- Transparency / Compliance ---


@router.get("/runs/{run_id}/transparency-report")
async def get_transparency_report(run_id: str, req: Request) -> ApiResponse:
    """Generate an EU AI Act Article 13 transparency report for a run."""
    tenant_id = get_tenant_id(req)

    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(
                error=ErrorResponse(code="INVALID_ID", message="Invalid run ID format")
            ).model_dump(),
        )

    async with async_session() as session:
        run_stmt = (
            select(Run)
            .options(selectinload(Run.steps))
            .where(Run.id == run_uuid)
        )
        run_stmt = _apply_tenant_filter(run_stmt, tenant_id, Run.tenant_id)
        run_result = await session.execute(run_stmt)
        run = run_result.scalar_one_or_none()

        if not run:
            raise HTTPException(
                status_code=404,
                detail=ApiResponse(
                    error=ErrorResponse(code="NOT_FOUND", message=f"Run '{run_id}' not found")
                ).model_dump(),
            )

        # Fetch approvals for this run
        approvals_result = await session.execute(
            select(ApprovalRequest).where(ApprovalRequest.run_id == run_uuid)
        )
        approvals = approvals_result.scalars().all()

        # Fetch policy violations for this run
        violations_result = await session.execute(
            select(PolicyViolation).where(PolicyViolation.run_id == run_uuid)
        )
        violations = violations_result.scalars().all()

    # Build AI models used list from steps that have a cost > 0 and a model
    ai_models: list[TransparencyAiModelEntry] = []
    for step in run.steps:
        model_val = getattr(step, "model", None)
        cost_val = step.cost_usd or 0.0
        if cost_val > 0 or model_val:
            ai_models.append(
                TransparencyAiModelEntry(
                    step_id=step.step_id,
                    model=model_val or "unknown",
                    cost_usd=cost_val,
                )
            )

    # Build human oversight entries from approval requests
    human_oversight: list[TransparencyHumanOversightEntry] = [
        TransparencyHumanOversightEntry(
            step_id=a.step_id,
            type="approval",
            status=a.status.value if hasattr(a.status, "value") else str(a.status),
            reviewer=a.reviewer_id,
        )
        for a in approvals
    ]

    # Build policy violation entries
    policy_violations: list[TransparencyPolicyViolationEntry] = [
        TransparencyPolicyViolationEntry(
            step_id=v.step_id,
            policy=v.policy_id,
            severity=v.severity,
            action=v.action_taken,
        )
        for v in violations
    ]

    # Count steps
    total_steps = len(run.steps)
    failed_steps = sum(
        1 for s in run.steps
        if (s.status.value if hasattr(s.status, "value") else s.status) == "failed"
    )

    report = TransparencyReportResponse(
        run_id=str(run.id),
        workflow_name=run.workflow_name,
        risk_level=run.risk_level or "minimal",
        started_at=run.started_at,
        completed_at=run.completed_at,
        status=run.status.value if hasattr(run.status, "value") else str(run.status),
        total_cost_usd=run.total_cost_usd,
        ai_models_used=ai_models,
        human_oversight=human_oversight,
        policy_violations=policy_violations,
        privacy_applied=settings.privacy_enabled,
        total_steps=total_steps,
        failed_steps=failed_steps,
    )
    return ApiResponse(data=report)


@router.get("/workflows/{name}/annex-iv")
async def get_annex_iv(name: str, req: Request) -> ApiResponse:
    """Generate an EU AI Act Annex IV technical documentation stub for a workflow."""
    # Load workflow YAML
    try:
        yaml_content = _load_workflow_yaml(name)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="NOT_FOUND", message=f"Workflow '{name}' not found"
                )
            ).model_dump(),
        )

    # Parse workflow definition
    try:
        workflow = parse_yaml_string(yaml_content)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=ApiResponse(
                error=ErrorResponse(
                    code="INVALID_WORKFLOW", message=f"Failed to parse workflow: {exc}"
                )
            ).model_dump(),
        )

    # Determine version from registry
    version_str: str | None = None
    async with async_session() as session:
        wv_result = await session.execute(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_name == name)
            .order_by(WorkflowVersion.version.desc())
            .limit(1)
        )
        wv = wv_result.scalar_one_or_none()
        if wv:
            version_str = str(wv.version)

        # Fetch recent eval run statistics
        eval_result = await session.execute(
            select(EvalRun)
            .where(EvalRun.workflow_name == name, EvalRun.status == EvalRunStatus.COMPLETED)
            .order_by(EvalRun.created_at.desc())
            .limit(50)
        )
        eval_runs = eval_result.scalars().all()

    # Collect unique AI models and step types
    ai_models_used = sorted({
        s.model or workflow.default_model or "unknown"
        for s in workflow.steps
        if s.type in ("standard", "llm", None, "")
    })
    step_types = sorted({s.type or "standard" for s in workflow.steps})

    # Find human oversight description
    approval_steps = [s for s in workflow.steps if s.type == "approval"]
    if approval_steps:
        oversight_desc = f"Approval gate at step '{approval_steps[0].id}'"
    else:
        oversight_desc = "No explicit approval step defined"

    # Risk classification text
    risk_level = getattr(workflow, "risk_level", "minimal") or "minimal"
    if risk_level == "high":
        risk_desc = "high - requires human oversight per EU AI Act Annex III"
    elif risk_level == "limited":
        risk_desc = "limited - transparency obligations apply per EU AI Act Article 52"
    else:
        risk_desc = "minimal - standard monitoring recommended"

    # Testing evidence
    testing_evidence: dict = {}
    if eval_runs:
        total_runs = len(eval_runs)
        avg_pass_rate = sum(er.pass_rate for er in eval_runs) / total_runs
        testing_evidence = {
            "total_eval_runs": total_runs,
            "pass_rate": round(avg_pass_rate, 4),
        }
    else:
        testing_evidence = {"total_eval_runs": 0, "pass_rate": None}

    # Data handling description from privacy settings
    if settings.privacy_enabled:
        privacy_entities = settings.privacy_entities or "email, phone, ssn"
        data_handling = f"Privacy router enabled for: {privacy_entities}"
    else:
        data_handling = "Privacy router not enabled"

    # Audit trail description
    audit_desc = "Tamper-evident SHA-256 hash chain audit log enabled"

    sections = AnnexIVSections(
        intended_purpose=getattr(workflow, "description", "") or "",
        ai_models=ai_models_used,
        step_types=step_types,
        human_oversight=oversight_desc,
        risk_classification=risk_desc,
        testing_evidence=testing_evidence,
        known_limitations=(
            "Cost estimates are approximate. LLM outputs may vary."
        ),
        data_handling=data_handling,
        audit_trail=audit_desc,
    )

    return ApiResponse(
        data=AnnexIVResponse(
            workflow_name=name,
            version=version_str,
            risk_level=risk_level,
            generated_at=datetime.now(timezone.utc),
            sections=sections,
        )
    )


@router.get("/compliance/status")
async def get_compliance_status() -> ApiResponse:
    """Return the current compliance mode status and active features."""
    mode = settings.compliance_mode or ""
    active = mode == "eu_ai_act"

    features = {
        "audit_trail": True,  # Always enabled - tamper-evident hash chain
        "risk_classification": True,  # Always available via workflow risk_level
        "privacy_router": settings.privacy_enabled,
        "emergency_stop": True,  # Always available
        "input_prompt_logging": True,  # Always captured in RunStep.input_prompt
    }

    return ApiResponse(
        data=ComplianceStatusResponse(
            mode=mode if mode else "disabled",
            active=active,
            features=features,
        )
    )


# ---------------------------------------------------------------------------
# Workflow Evolution endpoints
# ---------------------------------------------------------------------------


@router.post("/evolution/start", status_code=202)
async def start_evolution(req: Request) -> ApiResponse:
    """Start a workflow evolution experiment.

    Runs iterative mutation + eval loop to improve the workflow.
    Body: {workflow_name, eval_suite_yaml, max_iterations, optimize_for, budget_limit_usd}
    Admin-only when auth is enabled.
    """
    _require_admin(req)
    body = await req.json()
    try:
        parsed = EvolutionStartRequest(**body)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # get_tenant_id is synchronous - do not await
    tenant_id = get_tenant_id(req)

    from sandcastle.engine.evolution import run_evolution

    result = await run_evolution(
        workflow_name=parsed.workflow_name,
        eval_suite_yaml=parsed.eval_suite_yaml,
        max_iterations=parsed.max_iterations,
        optimize_for=parsed.optimize_for,
        budget_limit=parsed.budget_limit_usd,
        tenant_id=tenant_id,
    )

    if result.get("status") == "failed":
        raise HTTPException(status_code=400, detail=result.get("error", "Evolution failed"))

    # Validate the raw dict through a typed schema before returning
    return ApiResponse(data=EvolutionStartResponse(**{
        k: v for k, v in result.items()
        if k in EvolutionStartResponse.model_fields
    }))


@router.get("/evolution/{workflow_name}/status")
async def get_evolution_status(workflow_name: str, req: Request = None) -> ApiResponse:
    """Get current evolution status and iteration history for a workflow.

    Admin-only when auth is enabled.
    """
    _require_admin(req)
    async with async_session() as session:
        stmt = (
            select(WorkflowEvolution)
            .where(WorkflowEvolution.workflow_name == workflow_name)
            .order_by(WorkflowEvolution.created_at.desc())
        )
        result = await session.execute(stmt)
        ev = result.scalar_one_or_none()

        if ev is None:
            raise HTTPException(
                status_code=404,
                detail=f"No evolution found for workflow '{workflow_name}'",
            )

        iter_stmt = (
            select(EvolutionIteration)
            .where(EvolutionIteration.evolution_id == ev.id)
            .order_by(EvolutionIteration.iteration_number)
        )
        iter_result = await session.execute(iter_stmt)
        iterations = iter_result.scalars().all()

        iter_responses = [
            EvolutionIterationResponse(
                id=str(it.id),
                iteration_number=it.iteration_number,
                mutation_type=it.mutation_type,
                mutation_description=it.mutation_description,
                mutation_diff=it.mutation_diff,
                score=it.score,
                quality=it.quality,
                cost_usd=it.cost_usd,
                duration_seconds=it.duration_seconds,
                eval_pass_rate=it.eval_pass_rate,
                status=it.status,
                created_at=it.created_at,
            )
            for it in iterations
        ]

        return ApiResponse(
            data=EvolutionStatusResponse(
                evolution_id=str(ev.id),
                workflow_name=ev.workflow_name,
                status=ev.status,
                optimize_for=ev.optimize_for,
                baseline_score=ev.baseline_score,
                baseline_quality=ev.baseline_quality,
                baseline_cost=ev.baseline_cost,
                best_score=ev.best_score,
                best_quality=ev.best_quality,
                best_cost=ev.best_cost,
                max_iterations=ev.max_iterations,
                current_iteration=ev.current_iteration,
                total_keeps=ev.total_keeps,
                total_discards=ev.total_discards,
                budget_limit_usd=ev.budget_limit_usd,
                created_at=ev.created_at,
                completed_at=ev.completed_at,
                iterations=iter_responses,
            )
        )


@router.post("/evolution/{workflow_name}/accept")
async def accept_evolution(workflow_name: str, req: Request) -> ApiResponse:
    """Accept the best evolution variant and promote it to production.

    Admin-only when auth is enabled.
    """
    _require_admin(req)
    body = {}
    try:
        body = await req.json()
    except Exception:
        pass

    notes = body.get("notes", "")

    async with async_session() as session:
        stmt = (
            select(WorkflowEvolution)
            .where(WorkflowEvolution.workflow_name == workflow_name)
            .order_by(WorkflowEvolution.created_at.desc())
        )
        result = await session.execute(stmt)
        ev = result.scalar_one_or_none()

        if ev is None:
            raise HTTPException(
                status_code=404,
                detail=f"No evolution found for workflow '{workflow_name}'",
            )

        if not ev.best_variant_yaml:
            raise HTTPException(
                status_code=400,
                detail="No best variant available to accept",
            )

        if ev.status not in ("completed", "running"):
            raise HTTPException(
                status_code=400,
                detail=f"Evolution is in '{ev.status}' state - cannot accept",
            )

        best_yaml = ev.best_variant_yaml
        best_score = ev.best_score
        best_quality = ev.best_quality

    try:
        import hashlib

        from sandcastle.engine.dag import parse_yaml_string

        parsed_wf = parse_yaml_string(best_yaml)
        checksum = hashlib.sha256(best_yaml.encode()).hexdigest()

        async with async_session() as session:
            from sqlalchemy import func as sqlfunc

            max_ver_stmt = select(sqlfunc.max(WorkflowVersion.version)).where(
                WorkflowVersion.workflow_name == workflow_name
            )
            max_ver = await session.scalar(max_ver_stmt)
            next_version = (max_ver or 0) + 1

            wv = WorkflowVersion(
                workflow_name=workflow_name,
                version=next_version,
                status=WorkflowVersionStatus.PRODUCTION,
                yaml_content=best_yaml,
                description=f"Promoted by evolution engine. Score={best_score:.2f}. {notes}".strip(),
                steps_count=len(parsed_wf.steps),
                checksum=checksum,
                created_by="evolution-engine",
                promoted_by="evolution-engine",
                promoted_at=datetime.now(timezone.utc),
            )
            session.add(wv)
            await session.commit()

        return ApiResponse(
            data={
                "workflow_name": workflow_name,
                "accepted": True,
                "version": next_version,
                "best_score": best_score,
                "best_quality": best_quality,
                "message": f"Best evolution variant promoted to version {next_version}",
            }
        )
    except Exception as exc:
        logger.error("Failed to accept evolution for '%s': %s", workflow_name, exc)
        raise HTTPException(status_code=500, detail=f"Failed to promote variant: {exc}")


@router.post("/evolution/{workflow_name}/cancel")
async def cancel_evolution(workflow_name: str, req: Request) -> ApiResponse:
    """Cancel a running evolution experiment. Admin-only when auth is enabled."""
    _require_admin(req)
    async with async_session() as session:
        stmt = (
            select(WorkflowEvolution)
            .where(
                WorkflowEvolution.workflow_name == workflow_name,
                WorkflowEvolution.status == "running",
            )
            .order_by(WorkflowEvolution.created_at.desc())
        )
        result = await session.execute(stmt)
        ev = result.scalar_one_or_none()

        if ev is None:
            raise HTTPException(
                status_code=404,
                detail=f"No running evolution found for workflow '{workflow_name}'",
            )

        ev.status = "cancelled"
        ev.completed_at = datetime.now(timezone.utc)
        await session.commit()

    return ApiResponse(
        data={
            "workflow_name": workflow_name,
            "cancelled": True,
            "message": "Evolution cancelled",
        }
    )


@router.get("/evolution/stats")
async def get_evolution_stats(req: Request) -> ApiResponse:
    """Get aggregated evolution statistics across all workflows. Admin-only when auth is enabled."""
    _require_admin(req)
    async with async_session() as session:
        count_stmt = (
            select(WorkflowEvolution.status, func.count(WorkflowEvolution.id).label("cnt"))
            .group_by(WorkflowEvolution.status)
        )
        count_result = await session.execute(count_stmt)
        counts_by_status = {row.status: row.cnt for row in count_result.all()}

        total = sum(counts_by_status.values())
        active = counts_by_status.get("running", 0)
        completed = counts_by_status.get("completed", 0)

        improv_stmt = select(
            func.count(WorkflowEvolution.id)
        ).where(
            WorkflowEvolution.best_score > WorkflowEvolution.baseline_score,
            WorkflowEvolution.status == "completed",
        )
        improvements = (await session.scalar(improv_stmt)) or 0

        avg_stmt = select(
            func.avg(WorkflowEvolution.best_score - WorkflowEvolution.baseline_score)
        ).where(
            WorkflowEvolution.status == "completed",
            WorkflowEvolution.baseline_score.is_not(None),
            WorkflowEvolution.best_score.is_not(None),
        )
        avg_improvement = await session.scalar(avg_stmt)

        top_stmt = (
            select(
                WorkflowEvolution.workflow_name,
                func.max(
                    WorkflowEvolution.best_score - WorkflowEvolution.baseline_score
                ).label("max_improvement"),
                func.count(WorkflowEvolution.id).label("runs"),
            )
            .where(WorkflowEvolution.status == "completed")
            .group_by(WorkflowEvolution.workflow_name)
            .order_by(
                func.max(
                    WorkflowEvolution.best_score - WorkflowEvolution.baseline_score
                ).desc()
            )
            .limit(10)
        )
        top_result = await session.execute(top_stmt)
        top_workflows = [
            {
                "workflow_name": row.workflow_name,
                "max_improvement": float(row.max_improvement or 0),
                "runs": row.runs,
            }
            for row in top_result.all()
        ]

    return ApiResponse(
        data=EvolutionStatsResponse(
            total_evolutions=total,
            active_evolutions=active,
            completed_evolutions=completed,
            total_improvements=improvements,
            avg_improvement=float(avg_improvement) if avg_improvement is not None else None,
            top_workflows=top_workflows,
        )
    )
