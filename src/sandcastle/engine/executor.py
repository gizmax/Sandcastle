"""Workflow executor - parallel execution with retries, cost tracking, budget, cancel."""

from __future__ import annotations

import asyncio
import collections
import copy
import csv
import dataclasses
import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from simpleeval import simple_eval

from sandcastle.engine.dag import (
    ExecutionPlan,
    StepDefinition,
    WorkflowDefinition,
)
from sandcastle.engine.events import event_bus
from sandcastle.engine.sandshore import (
    SandshoreError,
    SandshoreRuntime,
    get_sandshore_runtime,
)
from sandcastle.engine.storage import StorageBackend

logger = logging.getLogger(__name__)

# Pre-compiled regex patterns for template and storage reference resolution.
_TEMPLATE_RE = re.compile(r"\{((?:input|steps|env)\.[^}]+|run_id|date|memory|context)\}")
_STORAGE_RE = re.compile(r"\{storage\.([^}]+)\}")

# Default instructions prepended to every step prompt to keep agent output clean.
_STEP_SYSTEM_PREFIX = (
    "IMPORTANT: Return ONLY the requested data. "
    "No introductory text, no commentary, no filler phrases, no sign-offs. "
    "Do not include a Sources/References section. "
    "Do not include Notes or Remarks sections. "
    "Go straight to the answer.\n\n"
)


@dataclass
class StepResult:
    """Result from executing a single step."""

    step_id: str
    parallel_index: int | None = None
    output: Any = None
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    status: str = "completed"  # "completed" | "failed" | "skipped"
    error: str | None = None
    attempt: int = 1
    input_prompt: str | None = None  # Resolved prompt sent to the LLM


@dataclass
class RunContext:
    """Mutable context passed through the execution of a workflow run.

    Multiple concurrent tasks may read/write step_outputs, costs,
    branch_skip_steps, and branch_run_steps.  All mutations of these
    shared fields from _prepare_and_run_step must be guarded by the
    ``_lock`` asyncio.Lock to prevent data races.
    """

    run_id: str
    input: dict
    step_outputs: dict[str, Any] = field(default_factory=dict)
    step_results: dict[str, StepResult] = field(default_factory=dict)
    costs: list[float] = field(default_factory=list)
    status: str = "running"
    error: str | None = None
    max_cost_usd: float | None = None
    workflow_name: str = ""
    default_tools: list[str] = field(default_factory=list)
    memories: list[dict] = field(default_factory=list)
    _resolved_context: str = field(default="", repr=False)  # Dynamic context from context_query
    _memory_config: Any = field(default=None, repr=False)
    _memory_scope_id: str = field(default="", repr=False)
    branch_skip_steps: set[str] = field(default_factory=set)
    branch_run_steps: set[str] = field(default_factory=set)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    # Token optimization: cache-aware execution
    _file_read_cache: dict[str, str] = field(default_factory=dict, repr=False)
    _file_read_counts: dict[str, int] = field(default_factory=dict, repr=False)
    _seen_prompt_hashes: set[str] = field(default_factory=set, repr=False)
    _step_output_max_tokens: dict[str, int] = field(default_factory=dict, repr=False)
    admin_trusted: bool = field(default=False)
    # Tenant ID is propagated into the run so sub-workflow + delegate steps
    # can inherit it (round 9 multi-tenant memory isolation).
    tenant_id: str | None = field(default=None, repr=False)

    def with_item(self, item: Any, index: int) -> RunContext:
        """Create a child context for a parallel_over item.

        Each child gets its own costs list to avoid concurrent appends
        to a shared list. The parent aggregates child costs after join.
        Deep-copies step_outputs and step_results so that mutations in
        the child do not leak back into the parent context.
        """
        return RunContext(
            run_id=self.run_id,
            input={**self.input, "_item": item, "_index": index},
            step_outputs=copy.deepcopy(self.step_outputs),
            step_results=copy.deepcopy(self.step_results),
            costs=[],
            status=self.status,
            max_cost_usd=self.max_cost_usd,
            workflow_name=self.workflow_name,
            default_tools=self.default_tools,
            memories=list(self.memories),
            _resolved_context=self._resolved_context,
            _memory_config=self._memory_config,
            _memory_scope_id=self._memory_scope_id,
            branch_skip_steps=set(self.branch_skip_steps),
            branch_run_steps=set(self.branch_run_steps),
            _file_read_cache=dict(self._file_read_cache),
            _file_read_counts=dict(self._file_read_counts),
            _seen_prompt_hashes=set(self._seen_prompt_hashes),
            _step_output_max_tokens=dict(self._step_output_max_tokens),
        )

    @property
    def total_cost(self) -> float:
        return sum(self.costs)

    def snapshot(self) -> dict:
        """Create a serializable snapshot of the context for checkpointing."""
        return {
            "run_id": self.run_id,
            "input": self.input,
            "step_outputs": self.step_outputs,
            "costs": self.costs,
            "total_cost": self.total_cost,
        }


@dataclass
class WorkflowResult:
    """Final result of a workflow execution."""

    run_id: str
    outputs: dict[str, Any]
    total_cost_usd: float
    status: str
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    token_report: dict[str, Any] | None = None


_UNRESOLVED = object()


def resolve_variable(var_path: str, context: RunContext) -> Any:
    """Resolve a dotted variable path against the run context.

    Supports:
    - input.X -> from context.input
    - steps.STEP_ID.output -> from context.step_outputs
    - steps.STEP_ID.output.FIELD -> specific field
    - run_id -> current run UUID
    - date -> current ISO date

    Returns _UNRESOLVED sentinel if the variable path cannot be resolved
    (unknown path, missing step). Returns None if the variable resolved
    but its value is None (e.g. skipped step output).
    """
    if not var_path:
        return _UNRESOLVED

    parts = var_path.split(".")

    def _traverse(obj: Any, path_parts: list[str]) -> Any:
        for part in path_parts:
            if obj is None:
                return None
            if isinstance(obj, dict):
                obj = obj.get(part)
            elif isinstance(obj, list):
                try:
                    idx = int(part)
                except (ValueError, TypeError):
                    return _UNRESOLVED
                if idx < 0 or idx >= len(obj):
                    return _UNRESOLVED
                obj = obj[idx]
            else:
                return _UNRESOLVED
        return obj

    if parts[0] == "input":
        if len(parts) > 1 and parts[1] not in context.input:
            return _UNRESOLVED
        return _traverse(context.input, parts[1:])

    if parts[0] == "steps" and len(parts) >= 3:
        step_id = parts[1]
        if step_id not in context.step_outputs:
            return _UNRESOLVED
        step_data = context.step_outputs[step_id]
        if parts[2] == "output":
            if len(parts) == 3:
                # Apply output_max_tokens truncation if configured
                max_tok = context._step_output_max_tokens.get(step_id, 0)
                if max_tok > 0 and isinstance(step_data, str):
                    max_chars = max_tok * 4
                    if len(step_data) > max_chars:
                        step_data = step_data[:max_chars] + "\n\n[... truncated to fit context window ...]"
                return step_data
            if step_data is None:
                return None
            return _traverse(step_data, parts[3:])
        # Resolve step metadata attributes from step_results
        if parts[2] in ("status", "error", "cost"):
            sr = context.step_results.get(step_id)
            if sr is None:
                return _UNRESOLVED
            if parts[2] == "status":
                return sr.status
            if parts[2] == "error":
                return sr.error
            if parts[2] == "cost":
                return sr.cost_usd
            return _UNRESOLVED

    if parts[0] == "memory":
        from sandcastle.engine.memory import format_memories_for_prompt

        return format_memories_for_prompt(context.memories)

    if var_path == "context":
        return context._resolved_context or ""

    if var_path == "run_id":
        return context.run_id

    if var_path == "date":
        return datetime.now(timezone.utc).date().isoformat()

    # env.VAR_NAME -> resolve from environment variables
    if parts[0] == "env" and len(parts) == 2:
        import os
        value = os.environ.get(parts[1], "").strip()
        if value:
            return value
        return _UNRESOLVED

    return _UNRESOLVED


def _escape_braces(value: str) -> str:
    """Escape curly braces in resolved values to prevent re-resolution."""
    return value.replace("{", "{{").replace("}", "}}")


# Maximum template string size to prevent ReDoS or memory issues.
_MAX_TEMPLATE_SIZE = 1024 * 1024  # 1MB


def resolve_templates(
    template: str,
    context: RunContext,
    depends_on: list[str] | None = None,
) -> str:
    """Replace {var.path} template variables in a string.

    If *depends_on* is provided, outputs from dependency steps that are NOT
    explicitly referenced in the template are automatically injected so that
    DAG edges imply data flow without requiring manual ``{steps.X.output}``
    placeholders.

    Behavior notes:
    - Nested template variables (e.g. ``{steps.{input.name}.output}``) are NOT
      supported. The inner braces break the regex and the variable is left as-is.
    - Resolved values from step outputs are brace-escaped to prevent
      re-resolution (injection protection).
    - Missing/unresolvable variables are left as their original placeholder.
    """
    if len(template) > _MAX_TEMPLATE_SIZE:
        raise ValueError(
            f"Template string too large ({len(template)} bytes, "
            f"max {_MAX_TEMPLATE_SIZE})"
        )

    def _replace(match: re.Match) -> str:
        var_path = match.group(1)
        value = resolve_variable(var_path, context)
        if value is _UNRESOLVED:
            logger.warning(f"Unresolved variable '{var_path}' in template, leaving placeholder")
            return match.group(0)
        if value is None:
            return "None"
        if isinstance(value, (dict, list)):
            raw = json.dumps(value)
        else:
            raw = str(value)
        # Escape braces in user-provided input values, step outputs, and
        # memory values to prevent template injection. Only built-in
        # variables (run_id, date) and env vars are trusted sources.
        if var_path.startswith(("steps.", "input.")) or var_path == "memory":
            return _escape_braces(raw)
        return raw

    resolved = _TEMPLATE_RE.sub(_replace, template)

    # Auto-inject unreferenced dependency outputs
    if depends_on:
        missing = [
            dep
            for dep in depends_on
            if not re.search(r"\{steps\." + re.escape(dep) + r"[\.\}]", template)
            and dep in context.step_outputs
        ]
        if missing:
            parts = []
            for dep in missing:
                data = context.step_outputs[dep]
                formatted = json.dumps(data) if isinstance(data, (dict, list)) else str(data)
                # Apply output_max_tokens truncation for auto-injected outputs
                max_tok = context._step_output_max_tokens.get(dep, 0)
                if max_tok > 0:
                    max_chars = max_tok * 4
                    if len(formatted) > max_chars:
                        formatted = formatted[:max_chars] + "\n[... truncated to fit context window ...]"
                parts.append(f"[{dep}]: {_escape_braces(formatted)}")
            context_block = "\n".join(parts)
            resolved = f"{resolved}\n\nContext from previous steps:\n{context_block}"

    return resolved


async def resolve_storage_refs(
    prompt: str,
    storage: StorageBackend,
    context: RunContext | None = None,
) -> str:
    """Replace {storage.PATH} references with stored content.

    Uses offset-based rebuilding instead of str.replace() to avoid
    replacing the wrong occurrence when the same storage ref appears
    multiple times or resolved content contains the same pattern.

    When *context* is provided, file reads are cached to avoid redundant
    storage lookups within the same run.
    """
    matches = list(_STORAGE_RE.finditer(prompt))
    if not matches:
        return prompt

    parts: list[str] = []
    last_end = 0
    for match in matches:
        parts.append(prompt[last_end:match.start()])
        path = match.group(1)
        # Check file read cache first
        if context is not None and path in context._file_read_cache:
            context._file_read_counts[path] = context._file_read_counts.get(path, 1) + 1
            if context._file_read_counts[path] > 2:
                logger.warning(
                    "File '%s' read %d times in same run - consider caching",
                    path, context._file_read_counts[path],
                )
            content = context._file_read_cache[path]
        else:
            content = await storage.read(path)
            if context is not None and content is not None:
                context._file_read_cache[path] = content
                context._file_read_counts[path] = 1
        parts.append(content if content is not None else match.group(0))
        last_end = match.end()
    parts.append(prompt[last_end:])
    return "".join(parts)


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to approximate token limit (rough: 1 token ~ 4 chars)."""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"


async def _context_search_memory(query: str, scope_id: str, limit: int = 5) -> str:
    """Search agent memory for relevant context."""
    try:
        from sandcastle.engine.memory import load_memories

        memories = await load_memories(scope_id, query=query, limit=limit)
        if not memories:
            return ""
        return "\n\n".join(m.get("memory", "") for m in memories if m.get("memory"))
    except Exception as e:
        logger.warning(f"Context query memory search failed: {e}")
        return ""


async def _context_search_web(query: str, limit: int = 3) -> str:
    """Search the web for context using tavily if available."""
    try:
        import os
        api_key = os.environ.get("TOOL_TAVILY_API_KEY", "")
        if not api_key:
            logger.debug("Context query web search skipped: TOOL_TAVILY_API_KEY not set")
            return ""

        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": api_key, "query": query, "max_results": limit},
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if not results:
                return ""
            return "\n\n".join(
                f"[{r.get('title', 'Result')}]\n{r.get('content', '')}"
                for r in results[:limit]
            )
    except Exception as e:
        logger.warning(f"Context query web search failed: {e}")
        return ""


def _context_search_files(query: str, limit: int = 5) -> str:
    """Search local workflow YAML files for relevant context by keyword overlap."""
    try:
        from sandcastle.config import settings
        workflows_dir = Path(settings.workflows_dir).resolve()
    except Exception:
        return ""

    if not workflows_dir.is_dir():
        return ""

    query_words = set(query.lower().split())
    if not query_words:
        return ""

    scored: list[tuple[float, str]] = []
    try:
        yaml_files = list(workflows_dir.glob("*.yaml")) + list(workflows_dir.glob("*.yml"))
    except OSError:
        return ""

    for path in yaml_files:
        try:
            content = path.read_text(errors="replace")[:4000]
            content_words = set(content.lower().split())
            overlap = len(query_words & content_words)
            if overlap > 0:
                score = overlap / len(query_words)
                scored.append((score, content))
        except OSError:
            continue

    scored.sort(key=lambda x: x[0], reverse=True)
    return "\n\n---\n\n".join(s[1] for s in scored[:limit])


async def _context_run_command(command: str) -> str:
    """Run a shell command and capture its stdout as context."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "sh", "-c", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            logger.warning(
                f"Context query custom command failed (rc={proc.returncode}): "
                f"{stderr.decode(errors='replace')[:200]}"
            )
            return ""
        return stdout.decode(errors="replace")
    except asyncio.TimeoutError:
        logger.warning("Context query custom command timed out after 30s")
        return ""
    except Exception as e:
        logger.warning(f"Context query custom command error: {e}")
        return ""


async def _resolve_context_query(
    step: StepDefinition,
    context: RunContext,
) -> str:
    """Fetch dynamic context for a step's context_query.

    Resolves template variables in the query first, then dispatches
    to the appropriate source (memory, web, files, custom).
    Returns empty string if no context_query is set.
    """
    if not step.context_query:
        return ""

    # Resolve template variables in the query itself
    query = resolve_templates(step.context_query, context, step.depends_on)

    if step.context_source == "memory":
        # When the workflow has no top-level `memory:` block, _memory_scope_id
        # is None. Synthesize a workflow-scoped id from the workflow name so
        # the search still hits a valid scope and the user sees results
        # instead of a silent no-op (the raw workflow name fails
        # _VALID_SCOPE_RE).
        scope_id = context._memory_scope_id or (
            f"workflow:{context.workflow_name}" if context.workflow_name else None
        )
        if not scope_id:
            result = ""
        else:
            result = await _context_search_memory(query, scope_id, limit=5)

    elif step.context_source == "web":
        result = await _context_search_web(query, limit=3)

    elif step.context_source == "files":
        result = _context_search_files(query, limit=5)

    elif step.context_source == "custom":
        if not context.admin_trusted:
            logger.warning(
                "Step '%s' uses context_source=custom but workflow is not admin-trusted - skipping",
                step.id,
            )
            result = ""
        else:
            result = await _context_run_command(query)

    else:
        result = ""

    if not result:
        return ""

    return _truncate_to_tokens(result, step.context_max_tokens)


def _enforce_data_residency(model_info: Any) -> None:
    """Raise if data_residency setting prohibits the model's region.

    When settings.data_residency is "eu", only models with region "eu" or
    "local" are allowed. When "local", only region "local" is allowed.
    No-op when data_residency is empty (no restriction).
    """
    from sandcastle.config import settings

    residency = settings.data_residency
    if not residency:
        return
    allowed = {"eu": {"eu", "local"}, "local": {"local"}}
    ok_regions = allowed.get(residency)
    if ok_regions is None:
        return  # Unknown residency value - no restriction
    if model_info.region not in ok_regions:
        raise ValueError(
            f"Data residency violation: model '{model_info.api_model_id}' "
            f"(region={model_info.region}) is not allowed under "
            f"data_residency='{residency}'. Use an EU or local model."
        )


def _safe_cost(input_tokens: int, output_tokens: int, input_price_per_m: float, output_price_per_m: float) -> float:
    """Calculate LLM cost safely, clamping to 0.0 on invalid values.

    Guards against None prices (from misconfigured providers) and negative
    token counts which would produce nonsensical cost values.
    """
    import math

    in_p = input_price_per_m if input_price_per_m else 0.0
    out_p = output_price_per_m if output_price_per_m else 0.0
    in_t = max(input_tokens or 0, 0)
    out_t = max(output_tokens or 0, 0)
    cost = in_t * in_p / 1_000_000 + out_t * out_p / 1_000_000
    if math.isnan(cost) or math.isinf(cost) or cost < 0:
        logger.warning(
            "Cost calculation produced invalid value %s "
            "(in_tok=%s, out_tok=%s, in_price=%s, out_price=%s)",
            cost, input_tokens, output_tokens, input_price_per_m, output_price_per_m,
        )
        return 0.0
    return cost


def _backoff_delay(attempt: int, backoff: str = "exponential") -> float:
    """Calculate backoff delay with jitter to prevent thundering herd."""
    import random

    if backoff == "exponential":
        base = min(2**attempt, 60)  # Cap at 60s
        # Full jitter: uniform random between 0 and base delay
        return random.uniform(0, base)
    return random.uniform(1.0, 3.0)  # Fixed ~2s with jitter


async def _emit_audit_event(
    event_type: str,
    run_id: str | None,
    actor_id: str,
    payload: dict,
) -> None:
    """Append an audit event in its own session. Failures are silently logged.

    Isolation from the caller's session means audit failures never abort execution.
    """
    try:
        from sandcastle.engine.audit import append_audit_event
        from sandcastle.models.db import async_session

        async with async_session() as session:
            await append_audit_event(
                session=session,
                event_type=event_type,
                run_id=run_id,
                actor_id=actor_id,
                payload=payload,
            )
            await session.commit()
    except Exception as exc:
        logger.warning("Audit event '%s' could not be persisted: %s", event_type, exc)


async def _save_run_step(
    run_id: str,
    step_id: str,
    status: str,
    parallel_index: int | None = None,
    output: Any = None,
    cost_usd: float = 0.0,
    duration_seconds: float = 0.0,
    attempt: int = 1,
    error: str | None = None,
    model: str | None = None,
    input_prompt: str | None = None,
) -> None:
    """Create or update a RunStep record in the database.

    Uses upsert: INSERT on first call (running), UPDATE on completion/failure.
    The ``input_prompt`` is the resolved text sent to the LLM for audit purposes.
    """
    try:
        from sqlalchemy import select as sa_select

        from sandcastle.models.db import RunStep, StepStatus, async_session

        status_map = {
            "pending": StepStatus.PENDING,
            "running": StepStatus.RUNNING,
            "completed": StepStatus.COMPLETED,
            "failed": StepStatus.FAILED,
            "skipped": StepStatus.SKIPPED,
            "awaiting_approval": StepStatus.AWAITING_APPROVAL,
        }

        now = datetime.now(timezone.utc)
        db_status = status_map.get(status, StepStatus.PENDING)
        # Store output as-is, consistent with context.step_outputs.
        # JSON columns accept any JSON-serializable value (str, list, dict, etc.)
        output_data = output

        async with async_session() as session:
            # Try to find existing step record (from the "running" INSERT)
            run_uuid = uuid.UUID(run_id)
            existing = await session.scalar(
                sa_select(RunStep).where(
                    RunStep.run_id == run_uuid,
                    RunStep.step_id == step_id,
                    RunStep.parallel_index == parallel_index,
                )
            )

            if existing:
                # Update existing record
                existing.status = db_status
                if output_data is not None:
                    existing.output_data = output_data
                if cost_usd:
                    existing.cost_usd = cost_usd
                if duration_seconds:
                    existing.duration_seconds = duration_seconds
                existing.attempt = attempt
                existing.error = error
                if model:
                    existing.model = model
                if input_prompt is not None:
                    existing.input_prompt = input_prompt
                if status in ("completed", "failed", "skipped"):
                    existing.completed_at = now
            else:
                # Create new record
                step = RunStep(
                    run_id=run_uuid,
                    step_id=step_id,
                    parallel_index=parallel_index,
                    status=db_status,
                    output_data=output_data,
                    cost_usd=cost_usd,
                    duration_seconds=duration_seconds,
                    attempt=attempt,
                    error=error,
                    model=model,
                    input_prompt=input_prompt,
                    started_at=now if status == "running" else None,
                    completed_at=(now if status in ("completed", "failed", "skipped") else None),
                )
                session.add(step)
            await session.commit()
    except Exception as e:
        logger.warning(f"Could not save RunStep for {step_id}: {e}")


async def _save_checkpoint(
    run_id: str,
    step_id: str,
    stage_index: int,
    context: RunContext,
) -> None:
    """Save a checkpoint after completing a stage for replay/fork support."""
    try:
        from sandcastle.models.db import RunCheckpoint, async_session

        async with async_session() as session:
            checkpoint = RunCheckpoint(
                run_id=uuid.UUID(run_id),
                step_id=step_id,
                stage_index=stage_index,
                context_snapshot=context.snapshot(),
            )
            session.add(checkpoint)
            await session.commit()
    except Exception as e:
        logger.warning(f"Could not save checkpoint for step {step_id}: {e}")


_browser_action_cache: collections.OrderedDict[str, list[dict]] = collections.OrderedDict()
_BROWSER_CACHE_MAX = 500
_browser_cache_lock = asyncio.Lock()


def _cache_key(url: str, intent: str) -> str:
    """Generate cache key from URL pattern and intent."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return f"{parsed.netloc}{parsed.path}:{intent[:100]}"


async def _get_cached_actions(url: str, intent: str) -> list[dict] | None:
    """Get cached action sequence for a URL+intent pair."""
    key = _cache_key(url, intent)
    async with _browser_cache_lock:
        actions = _browser_action_cache.get(key)
        if actions is not None:
            _browser_action_cache.move_to_end(key)
        return actions


async def _save_cached_actions(url: str, intent: str, actions: list[dict]) -> None:
    """Save successful action sequence to cache."""
    key = _cache_key(url, intent)
    async with _browser_cache_lock:
        _browser_action_cache[key] = actions
        _browser_action_cache.move_to_end(key)
        while len(_browser_action_cache) > _BROWSER_CACHE_MAX:
            _browser_action_cache.popitem(last=False)


def _escape_js_string(text: str) -> str:
    """Escape a string for safe embedding inside JavaScript single-quoted string literals.

    NOTE: This does NOT make the string safe for shell interpolation.
    Callers should avoid wrapping the result in ``bash -c`` commands;
    prefer passing scripts directly to ``node -e`` instead.
    """
    return (
        text.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        .replace("`", "\\`")
        .replace("\x00", "")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _validate_browser_url(url: str) -> str:
    """Validate and sanitize a URL for browser navigation.

    Rejects dangerous schemes (javascript:, data:, file:) and ensures
    the URL uses http or https. Returns the validated URL.
    Raises ValueError for invalid/dangerous URLs.
    """
    from urllib.parse import urlparse

    if not url or not url.strip():
        raise ValueError("Browser URL must not be empty")

    url = url.strip()

    # Reject dangerous schemes before parsing
    lower = url.lower().lstrip()
    for scheme in ("javascript:", "data:", "file:", "vbscript:", "blob:"):
        if lower.startswith(scheme):
            raise ValueError(f"Dangerous URL scheme rejected: {scheme}")

    parsed = urlparse(url)
    if parsed.scheme and parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(
            f"Unsupported URL scheme '{parsed.scheme}' - only http/https allowed"
        )

    # If no scheme, prepend https
    if not parsed.scheme:
        url = f"https://{url}"

    return url


# In-memory cancel flags for local mode (no Redis).
# Uses OrderedDict for true FIFO eviction (set iteration order is
# insertion-order in CPython 3.7+ but dict is the guaranteed contract).
_cancel_flags: collections.OrderedDict[str, None] = collections.OrderedDict()
_MAX_CANCEL_FLAGS = 10000
_cancel_flags_lock = asyncio.Lock()

# Global emergency stop flag for local mode (no Redis).
# When set, ALL runs are treated as cancelled.
_emergency_stop_local: bool = False
_emergency_stop_lock = asyncio.Lock()


async def cancel_run_local(run_id: str) -> None:
    """Set cancel flag in-memory (local mode without Redis)."""
    async with _cancel_flags_lock:
        if len(_cancel_flags) >= _MAX_CANCEL_FLAGS:
            # Evict oldest half (FIFO) to preserve recent cancels
            evict_count = len(_cancel_flags) // 2
            for _ in range(evict_count):
                _cancel_flags.popitem(last=False)
            logger.warning(
                "Cancel flags set exceeded %d entries,"
                " evicted %d oldest",
                _MAX_CANCEL_FLAGS,
                evict_count,
            )
        _cancel_flags[run_id] = None


async def set_emergency_stop_local() -> None:
    """Activate the global emergency stop flag (local mode without Redis)."""
    global _emergency_stop_local
    async with _emergency_stop_lock:
        _emergency_stop_local = True


async def clear_emergency_stop_local() -> None:
    """Clear the global emergency stop flag (local mode without Redis)."""
    global _emergency_stop_local
    async with _emergency_stop_lock:
        _emergency_stop_local = False


async def is_emergency_stop_active() -> bool:
    """Return True if the global emergency stop is currently active."""
    from sandcastle.config import settings

    if not settings.redis_url:
        async with _emergency_stop_lock:
            return _emergency_stop_local

    try:
        r = await _get_redis()
        result = await r.get("emergency_stop:global")
        return result is not None
    except Exception:
        return False


_redis_pool = None
_redis_pool_lock = asyncio.Lock()


async def _get_redis():
    """Return a shared Redis connection (reused across cancel checks)."""
    global _redis_pool
    if _redis_pool is not None:
        return _redis_pool
    async with _redis_pool_lock:
        if _redis_pool is not None:
            return _redis_pool
        import redis.asyncio as aioredis

        from sandcastle.config import settings

        _redis_pool = aioredis.from_url(settings.redis_url)
        return _redis_pool


async def _check_cancel(run_id: str) -> bool:
    """Check if a run has been cancelled via Redis flag or in-memory set.

    Also checks the global emergency stop flag which cancels ALL runs.
    """
    from sandcastle.config import settings

    if not settings.redis_url:
        async with _emergency_stop_lock:
            if _emergency_stop_local:
                return True
        async with _cancel_flags_lock:
            if run_id in _cancel_flags:
                return True
            return False

    try:
        r = await _get_redis()
        # Check global emergency stop first (fastest path)
        emergency = await r.get("emergency_stop:global")
        if emergency is not None:
            return True
        result = await r.get(f"cancel:{run_id}")
        return result is not None
    except Exception:
        return False


def _check_budget(context: RunContext) -> str | None:
    """Check if the run has exceeded its budget.

    Returns None if OK, "warning" at 80%, "exceeded" at 100%.
    Guards against NaN/Infinity in cost values which could silently
    bypass budget enforcement.
    """
    if context.max_cost_usd is None or context.max_cost_usd <= 0:
        return None
    import math

    total = context.total_cost
    # NaN or Infinity in accumulated costs is a bug - treat as exceeded
    # to fail safely rather than silently ignoring budget limits.
    if math.isnan(total) or math.isinf(total):
        logger.error(
            "Budget check: total_cost is %s (likely a cost calculation bug)", total
        )
        return "exceeded"
    ratio = total / context.max_cost_usd
    if ratio >= 1.0:
        return "exceeded"
    if ratio >= 0.8:
        return "warning"
    return None


def _write_csv_output(
    step: StepDefinition,
    output: Any,
    run_id: str,
) -> None:
    """Write step output to a CSV file if csv_output is configured."""
    cfg = step.csv_output
    if cfg is None:
        return

    directory = Path(cfg.directory).expanduser().resolve()

    # Enforce sandbox root when configured
    from sandcastle.config import settings

    if settings.sandbox_root:
        sandbox = Path(settings.sandbox_root).expanduser().resolve()
        if not directory.is_relative_to(sandbox):
            logger.warning(
                "csv_output directory %s is outside sandbox root %s - skipping",
                directory,
                sandbox,
            )
            return

    directory.mkdir(parents=True, exist_ok=True)

    base_name = cfg.filename or step.id

    # Normalize output to list of dicts for CSV rows
    rows: list[dict] = []
    if isinstance(output, list):
        for item in output:
            if isinstance(item, dict):
                rows.append(item)
            else:
                rows.append({"value": item})
    elif isinstance(output, dict):
        rows.append(output)
    elif isinstance(output, str):
        # Try to parse as JSON first
        try:
            parsed = json.loads(output)
            if isinstance(parsed, list):
                for item in parsed:
                    rows.append(item if isinstance(item, dict) else {"value": item})
            elif isinstance(parsed, dict):
                rows.append(parsed)
            else:
                rows.append({"value": parsed})
        except (json.JSONDecodeError, ValueError):
            rows.append({"value": output})
    else:
        rows.append({"value": str(output) if output is not None else ""})

    if not rows:
        return

    # Collect all fieldnames across all rows
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)

    if cfg.mode == "append":
        filepath = directory / f"{base_name}.csv"
        file_exists = filepath.exists() and filepath.stat().st_size > 0
        if file_exists:
            # Read existing header and merge with new fieldnames to keep columns aligned
            with open(filepath, "r", newline="", encoding="utf-8") as rf:
                reader = csv.reader(rf)
                existing_header = next(reader, None)
            if existing_header:
                for col in existing_header:
                    if col not in seen:
                        fieldnames.append(col)
                        seen.add(col)
                # Preserve existing column order: existing columns first, then new ones
                merged = list(existing_header)
                for col in fieldnames:
                    if col not in existing_header:
                        merged.append(col)
                fieldnames = merged
        with open(filepath, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            writer.writerows(rows)
        logger.info(f"CSV append: {len(rows)} rows -> {filepath}")
    else:
        # new_file mode: include datetime in filename
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filepath = directory / f"{base_name}_{ts}.csv"
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        logger.info(f"CSV new file: {len(rows)} rows -> {filepath}")


_PDF_REPORT_VISUAL = (
    "\n\nVISUAL REPORT STRUCTURE:\n"
    "1. Start with # Title as the first line.\n"
    "2. Right after the title, include a KPI metrics line:\n"
    "   <!-- kpi: Metric1=Value1(+Change%)|Metric2=Value2(-Change%)|... -->\n"
    "   Example: <!-- kpi: Revenue=$2.4M(+12%)|Customers=12,450(+15%)|NPS=72(+5pts) -->\n"
    "3. Include ## Executive Summary with 3-5 bullet points of key findings.\n"
    "4. Use tables with numeric data columns - they will be auto-visualized as charts.\n"
    "5. For important notes use: > [!NOTE] text, > [!WARNING] text, > [!IMPORTANT] text\n"
    "6. Use #### for sub-labels (rendered as colored chips).\n"
    "7. Keep tables complete with full text - no abbreviations.\n"
)

_PDF_REPORT_INSTRUCTIONS: dict[str, str] = {
    "en": (
        "FORMATTING: Structure your response as a well-organized report in English. "
        "Use markdown headings (## and ###), bullet points, numbered lists, "
        "bold for emphasis, and tables where data is tabular. "
        "Organize content into logical sections with descriptive headings. "
        "Be thorough but concise." + _PDF_REPORT_VISUAL
    ),
    "cs": (
        "FORMATOVANI: Strukturuj svou odpoved jako prehledny report v cestine. "
        "Pouzij markdown nadpisy (## a ###), odrazky, cislovane seznamy, "
        "tucne pismo pro dulezite informace a tabulky pro tabulkova data. "
        "Organizuj obsah do logickych sekci s popisnymi nadpisy. "
        "Bud dustkladny, ale strucny." + _PDF_REPORT_VISUAL
    ),
    "de": (
        "FORMATIERUNG: Strukturiere deine Antwort als gut organisierten Bericht auf Deutsch. "
        "Verwende Markdown-Uberschriften (## und ###), Aufzahlungszeichen, nummerierte Listen, "
        "Fettdruck fur Hervorhebungen und Tabellen fur tabellarische Daten. "
        "Organisiere den Inhalt in logische Abschnitte mit beschreibenden Uberschriften. "
        "Sei grundlich, aber pragnant." + _PDF_REPORT_VISUAL
    ),
    "es": (
        "FORMATO: Estructura tu respuesta como un informe bien organizado en espanol. "
        "Usa encabezados markdown (## y ###), puntos, listas numeradas, "
        "negrita para enfasis y tablas donde los datos sean tabulares. "
        "Organiza el contenido en secciones logicas con encabezados descriptivos. "
        "Se minucioso pero conciso." + _PDF_REPORT_VISUAL
    ),
    "fr": (
        "FORMATAGE: Structurez votre reponse comme un rapport bien organise en francais. "
        "Utilisez les titres markdown (## et ###), les puces, les listes numerotees, "
        "le gras pour l'emphase et les tableaux pour les donnees tabulaires. "
        "Organisez le contenu en sections logiques avec des titres descriptifs. "
        "Soyez complet mais concis." + _PDF_REPORT_VISUAL
    ),
    "ja": (
        "FORMATTING: Structure your response as a well-organized report in Japanese. "
        "Use markdown headings (## and ###), bullet points, numbered lists, "
        "bold for emphasis, and tables where data is tabular. "
        "Organize content into logical sections with descriptive headings. "
        "Be thorough but concise." + _PDF_REPORT_VISUAL
    ),
    "zh": (
        "FORMATTING: Structure your response as a well-organized report in Chinese. "
        "Use markdown headings (## and ###), bullet points, numbered lists, "
        "bold for emphasis, and tables where data is tabular. "
        "Organize content into logical sections with descriptive headings. "
        "Be thorough but concise." + _PDF_REPORT_VISUAL
    ),
}


def _get_pdf_report_instruction(language: str) -> str:
    """Get the PDF report formatting instruction for a given language."""
    if language in _PDF_REPORT_INSTRUCTIONS:
        return _PDF_REPORT_INSTRUCTIONS[language]
    # Fallback for unknown languages
    return (
        f"FORMATTING: Structure your response as a well-organized report in {language}. "
        "Use markdown headings (## and ###), bullet points, numbered lists, "
        "bold for emphasis, and tables where data is tabular. "
        "Include a clear title as the first line (# Title). "
        "Organize content into logical sections with descriptive headings. "
        "Be thorough but concise."
    )


def _write_pdf_report(
    step: StepDefinition,
    output: Any,
    run_id: str,
) -> str | None:
    """Generate a branded PDF report from step output. Returns the file path or None."""
    cfg = step.pdf_report
    if cfg is None:
        return None

    directory = Path(cfg.directory).expanduser().resolve()

    # Enforce sandbox root when configured
    from sandcastle.config import settings

    if settings.sandbox_root:
        sandbox = Path(settings.sandbox_root).expanduser().resolve()
        if not directory.is_relative_to(sandbox):
            logger.warning(
                "pdf_report directory %s is outside sandbox root %s - skipping",
                directory,
                sandbox,
            )
            return None

    # Extract markdown text from output
    if isinstance(output, str):
        markdown_text = output
    elif isinstance(output, dict):
        # Try common keys, then fall back to JSON dump
        for key in ("result", "text", "content", "report", "markdown", "output"):
            if key in output and isinstance(output[key], str) and output[key].strip():
                markdown_text = output[key]
                break
        else:
            markdown_text = json.dumps(output, indent=2, ensure_ascii=False)
    elif isinstance(output, list):
        markdown_text = json.dumps(output, indent=2, ensure_ascii=False)
    else:
        markdown_text = str(output) if output is not None else ""

    # Guard: skip PDF generation if there is no content
    if not markdown_text.strip():
        logger.warning(
            "PDF report skipped for step '%s': empty output (type=%s)",
            step.id,
            type(output).__name__,
        )
        return None

    logger.info(
        "PDF report for step '%s': %d chars of markdown (output type=%s)",
        step.id,
        len(markdown_text),
        type(output).__name__,
    )

    base_name = cfg.filename or step.id
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filepath = directory / f"{base_name}_{ts}.pdf"

    try:
        from sandcastle.engine.pdf import generate_branded_pdf

        generate_branded_pdf(markdown_text, filepath, cfg.language)
        logger.info(f"PDF report generated: {filepath}")
        return str(filepath)
    except Exception as e:
        logger.error(f"PDF generation failed for step '{step.id}': {e}")
        return None


async def execute_step_with_retry(
    step: StepDefinition,
    context: RunContext,
    sandbox: SandshoreRuntime,
    storage: StorageBackend,
    parallel_index: int | None = None,
    step_overrides: dict | None = None,
) -> StepResult:
    """Execute a step with retry logic and exponential backoff."""
    # Apply step overrides for fork (use dataclasses.replace to preserve all fields)
    if step_overrides:
        override_fields = {}
        for key in ("prompt", "model", "max_turns", "timeout"):
            if key in step_overrides:
                override_fields[key] = step_overrides[key]
        if override_fields:
            step = dataclasses.replace(step, **override_fields)

    # AutoPilot: pick variant if configured
    autopilot_experiment = None
    autopilot_variant = None
    original_step = step

    if step.autopilot and step.autopilot.enabled and step.autopilot.variants:
        try:
            import random

            from sandcastle.engine.autopilot import (
                apply_variant,
                get_or_create_experiment,
                pick_variant,
            )

            if random.random() <= step.autopilot.sample_rate:
                experiment = await get_or_create_experiment(
                    workflow_name=context.workflow_name,
                    step_id=step.id,
                    config=step.autopilot,
                )
                autopilot_experiment = experiment
                variant = await pick_variant(experiment.id, step.autopilot.variants)
                if variant:
                    autopilot_variant = variant
                    step = apply_variant(step, variant)
                    logger.info(f"AutoPilot: step '{step.id}' using variant '{variant.id}'")
        except Exception as e:
            logger.warning(f"AutoPilot variant selection failed, using baseline: {e}")

    max_attempts = step.retry.max_attempts if step.retry else 1
    backoff = step.retry.backoff if step.retry else "exponential"

    # Record step as running
    await _save_run_step(
        run_id=context.run_id,
        step_id=step.id,
        status="running",
        parallel_index=parallel_index,
    )

    # Broadcast step.started event
    event_bus.publish(
        "step.started",
        {
            "run_id": context.run_id,
            "step_name": step.id,
            "step_id": step.id,
            "step_type": step.type,
            "workflow": context.workflow_name,
        },
    )
    _started_payload: dict = {"step_id": step.id, "step_type": step.type, "workflow": context.workflow_name}
    if step.responsibility:
        _started_payload["responsibility"] = step.responsibility
    if step.source_hint:
        _started_payload["source_hint"] = step.source_hint
    await _emit_audit_event(
        "step.started",
        run_id=context.run_id,
        actor_id="system",
        payload=_started_payload,
    )

    # Open OTEL step span (no-op when tracer not initialized).
    # Wrapped in try/finally to ensure the span is closed even when
    # WorkflowPaused or StepBlocked propagate out of the retry loop.
    from sandcastle.engine.otel import record_step_result, step_span

    _step_span_cm = step_span(context.run_id, step.id, step.type, model=step.model)
    _step_otel_span = _step_span_cm.__enter__()
    _otel_span_exited = False

    try:
        for attempt in range(1, max_attempts + 1):
            result = await _execute_step_once(step, context, sandbox, storage, parallel_index, attempt)

            if result.status == "completed":
                # AutoPilot: evaluate and save sample
                if autopilot_experiment and autopilot_variant:
                    try:
                        from sandcastle.engine.autopilot import (
                            evaluate_result,
                            maybe_complete_experiment,
                            save_sample,
                        )

                        score = await evaluate_result(
                            original_step.autopilot, original_step, result.output
                        )
                        await save_sample(
                            experiment_id=autopilot_experiment.id,
                            run_id=context.run_id,
                            variant=autopilot_variant,
                            output=result.output,
                            quality_score=score,
                            cost_usd=result.cost_usd,
                            duration_seconds=result.duration_seconds,
                        )
                        await maybe_complete_experiment(
                            autopilot_experiment.id, original_step.autopilot
                        )
                    except Exception as e:
                        logger.warning(f"AutoPilot sample recording failed: {e}")

                # Write CSV output if configured (blocking I/O offloaded to thread)
                if step.csv_output:
                    try:
                        await asyncio.to_thread(
                            _write_csv_output, step, result.output, context.run_id
                        )
                    except Exception as e:
                        logger.warning(f"CSV export failed for step '{step.id}': {e}")

                # Generate PDF report if configured (blocking I/O offloaded to thread)
                pdf_path = None
                if step.pdf_report:
                    try:
                        pdf_path = await asyncio.to_thread(
                            _write_pdf_report, step, result.output, context.run_id
                        )
                    except Exception as e:
                        logger.warning(f"PDF report failed for step '{step.id}': {e}")

                # Store PDF artifact path in output_data for API access
                if pdf_path and isinstance(result.output, dict):
                    result.output["_pdf_artifact"] = pdf_path
                elif pdf_path:
                    result = StepResult(
                        step_id=result.step_id,
                        parallel_index=result.parallel_index,
                        output={"result": result.output, "_pdf_artifact": pdf_path},
                        cost_usd=result.cost_usd,
                        duration_seconds=result.duration_seconds,
                        status=result.status,
                        attempt=result.attempt,
                    )

                # Record step completion
                await _save_run_step(
                    run_id=context.run_id,
                    step_id=step.id,
                    status="completed",
                    parallel_index=parallel_index,
                    output=result.output,
                    cost_usd=result.cost_usd,
                    duration_seconds=result.duration_seconds,
                    attempt=attempt,
                    model=step.model,
                    input_prompt=result.input_prompt,
                )

                # Broadcast step.completed event (truncate large outputs for event bus)
                _evt_output = result.output
                if isinstance(_evt_output, str) and len(_evt_output) > 4096:
                    _evt_output = _evt_output[:4096] + "...[truncated]"
                event_bus.publish(
                    "step.completed",
                    {
                        "run_id": context.run_id,
                        "step_name": step.id,
                        "step_id": step.id,
                        "status": "completed",
                        "output": _evt_output,
                        "cost_usd": result.cost_usd,
                        "duration_seconds": result.duration_seconds,
                    },
                )
                _completed_payload: dict = {"step_id": step.id, "cost_usd": result.cost_usd, "duration_seconds": result.duration_seconds}
                if step.responsibility:
                    _completed_payload["responsibility"] = step.responsibility
                if step.source_hint:
                    _completed_payload["source_hint"] = step.source_hint
                await _emit_audit_event(
                    "step.completed",
                    run_id=context.run_id,
                    actor_id="system",
                    payload=_completed_payload,
                )

                record_step_result(
                    _step_otel_span,
                    status="completed",
                    cost=result.cost_usd,
                    duration=result.duration_seconds,
                )
                _otel_span_exited = True
                _step_span_cm.__exit__(None, None, None)
                return result

            # Last attempt - check for fallback
            if attempt >= max_attempts:
                on_failure = step.retry.on_failure if step.retry else "abort"

                # Try fallback prompt if configured
                if on_failure == "fallback" and step.fallback and step.fallback.prompt:
                    logger.info(f"Step '{step.id}' failed, trying fallback prompt")
                    fallback_result = await _execute_fallback(
                        step, context, sandbox, storage, parallel_index, attempt
                    )
                    if fallback_result.status == "completed":
                        await _save_run_step(
                            run_id=context.run_id,
                            step_id=step.id,
                            status="completed",
                            parallel_index=parallel_index,
                            output=fallback_result.output,
                            cost_usd=result.cost_usd + fallback_result.cost_usd,
                            duration_seconds=result.duration_seconds + fallback_result.duration_seconds,
                            attempt=attempt,
                        )
                        # Combine costs from all attempts + fallback
                        fallback_result.cost_usd += result.cost_usd
                        fallback_result.duration_seconds += result.duration_seconds
                        record_step_result(
                            _step_otel_span,
                            status="completed",
                            cost=fallback_result.cost_usd,
                            duration=fallback_result.duration_seconds,
                        )
                        _otel_span_exited = True
                        _step_span_cm.__exit__(None, None, None)
                        return fallback_result

                logger.warning(f"Step '{step.id}' failed after {max_attempts} attempts: {result.error}")
                from sandcastle.engine.telemetry import capture_step_error

                capture_step_error(
                    Exception(result.error or "Step failed after retries"),
                    step_id=step.id,
                    step_type=step.type,
                    model=step.model,
                    workflow_name=context.workflow_name,
                    run_id=context.run_id,
                    attempt=attempt,
                )
                # Record step failure
                await _save_run_step(
                    run_id=context.run_id,
                    step_id=step.id,
                    status="failed",
                    parallel_index=parallel_index,
                    cost_usd=result.cost_usd,
                    duration_seconds=result.duration_seconds,
                    attempt=attempt,
                    error=result.error,
                    model=step.model,
                    input_prompt=result.input_prompt,
                )

                # Broadcast step.failed event
                event_bus.publish(
                    "step.failed",
                    {
                        "run_id": context.run_id,
                        "step_name": step.id,
                        "step_id": step.id,
                        "error": result.error,
                    },
                )
                await _emit_audit_event(
                    "step.failed",
                    run_id=context.run_id,
                    actor_id="system",
                    payload={"step_id": step.id, "error": result.error, "attempts": attempt},
                )

                record_step_result(
                    _step_otel_span,
                    status="failed",
                    cost=result.cost_usd,
                    duration=result.duration_seconds,
                )
                _otel_span_exited = True
                _step_span_cm.__exit__(None, None, None)
                return result

            delay = _backoff_delay(attempt, backoff)
            logger.info(f"Step '{step.id}' attempt {attempt} failed, retrying in {delay}s...")
            await asyncio.sleep(delay)

        _otel_span_exited = True
        _step_span_cm.__exit__(None, None, None)
        return result  # Should not reach here

    finally:
        # Ensure OTEL span is always closed, even on WorkflowPaused/StepBlocked
        if not _otel_span_exited:
            _step_span_cm.__exit__(None, None, None)


async def _execute_fallback(
    step: StepDefinition,
    context: RunContext,
    sandbox: SandshoreRuntime,
    storage: StorageBackend,
    parallel_index: int | None = None,
    attempt: int = 1,
) -> StepResult:
    """Execute the fallback prompt for a step."""
    started_at = datetime.now(timezone.utc)
    try:
        prompt = resolve_templates(step.fallback.prompt, context, step.depends_on)
        prompt = await resolve_storage_refs(prompt, storage, context)
        prompt = _STEP_SYSTEM_PREFIX + prompt

        request: dict[str, Any] = {
            "prompt": prompt,
            "model": step.fallback.model,
            "max_turns": step.max_turns,
            "timeout": step.timeout,
        }

        logger.info(f"Executing fallback for step '{step.id}' (model={step.fallback.model})")
        result = await sandbox.query(request)
        output = result.structured_output if result.structured_output else result.text
        if isinstance(output, str):
            text = output.strip()
            if text.startswith("```"):
                first_nl = text.find("\n")
                if first_nl >= 0:
                    text = text[first_nl + 1 :]
                if text.endswith("```"):
                    text = text[:-3].rstrip()
            try:
                parsed = json.loads(text)
                if isinstance(parsed, (dict, list)):
                    output = parsed
            except (json.JSONDecodeError, ValueError):
                pass
        duration = (datetime.now(timezone.utc) - started_at).total_seconds()

        return StepResult(
            step_id=step.id,
            parallel_index=parallel_index,
            output=output,
            cost_usd=result.total_cost_usd,
            duration_seconds=duration,
            status="completed",
            attempt=attempt,
        )
    except Exception as e:
        duration = (datetime.now(timezone.utc) - started_at).total_seconds()
        logger.error(f"Fallback for step '{step.id}' also failed: {e}")
        from sandcastle.engine.telemetry import capture_step_error

        capture_step_error(
            e,
            step_id=step.id,
            step_type=step.type,
            model=step.model,
            workflow_name=context.workflow_name,
            run_id=context.run_id,
        )
        return StepResult(
            step_id=step.id,
            parallel_index=parallel_index,
            cost_usd=0.0,
            duration_seconds=duration,
            status="failed",
            error=f"Fallback failed: {e}",
            attempt=attempt,
        )


def _is_cacheable_output(output: Any) -> bool:
    """Check if a step output is worth caching.

    Returns False for empty results or outputs that indicate the agent
    could not complete the task (e.g. missing input, no data found).
    """
    if output is None or output == "" or output == [] or output == {}:
        return False

    # Check dict outputs for empty data indicators
    if isinstance(output, dict):
        result_val = output.get("result", "")
        if isinstance(result_val, str) and len(result_val) < 200:
            lower = result_val.lower()
            if any(kw in lower for kw in _FAILED_OUTPUT_KEYWORDS):
                return False
        # Check for zero-data indicators like total_mentions: 0
        if output.get("total_mentions") == 0 and output.get("mentions") == []:
            return False

    # Check string outputs
    if isinstance(output, str) and len(output) < 200:
        lower = output.lower()
        if any(kw in lower for kw in _FAILED_OUTPUT_KEYWORDS):
            return False

    return True


_MAX_STEP_OUTPUT_SIZE = 10 * 1024 * 1024  # 10MB per step output


def _truncate_output(output: Any, max_size: int = _MAX_STEP_OUTPUT_SIZE) -> Any:
    """Truncate step output if it exceeds the size limit."""
    if output is None:
        return output
    try:
        serialized = json.dumps(output, default=str) if not isinstance(output, str) else output
    except (TypeError, ValueError):
        serialized = str(output)
    if len(serialized) <= max_size:
        return output
    logger.warning(
        "Step output truncated: %d bytes > %d byte limit",
        len(serialized),
        max_size,
    )
    if isinstance(output, str):
        return output[:max_size] + "\n...[TRUNCATED]"
    return {"_truncated": True, "_original_size": len(serialized), "result": serialized[:max_size]}


_FAILED_OUTPUT_KEYWORDS = [
    "please provide",
    "please paste",
    "no article",
    "no content was provided",
    "i need you to provide",
    "i don't have access",
    "i can't access",
    "i cannot visit",
    "unable to access",
    "cannot browse",
]


def _compute_cache_key(
    workflow_name: str,
    step_id: str,
    prompt: str,
    model: str,
    tenant_id: str | None = None,
) -> str:
    """Compute a deterministic cache key for a step execution.

    The key includes *tenant_id* so that two tenants running the same
    workflow with identical prompts never share cached outputs — without
    this, tenant B would read tenant A's cached agent response (a real
    cross-tenant data leak, parallel to the round 9 memory isolation fix).
    None tenant_id (single-tenant / local mode) is encoded explicitly as
    the literal string so its key shape is stable across runs.
    """
    tenant_part = tenant_id if tenant_id else "_none_"
    raw = f"{tenant_part}:{workflow_name}:{step_id}:{model}:{prompt}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def _get_cached_result(cache_key: str) -> dict | None:
    """Look up a cached step result. Returns output_data dict or None."""
    try:
        from sqlalchemy import select as sa_select

        from sandcastle.models.db import StepCache, async_session

        now = datetime.now(timezone.utc)
        async with async_session() as session:
            row = await session.scalar(
                sa_select(StepCache).where(
                    StepCache.cache_key == cache_key,
                    (StepCache.expires_at.is_(None)) | (StepCache.expires_at > now),
                )
            )
            if row:
                row.hit_count += 1
                await session.commit()
                return {
                    "output": row.output_data,
                    "cost_usd": row.cost_usd,
                }
    except Exception as e:
        logger.debug(f"Cache lookup failed: {e}")
    return None


async def _save_to_cache(
    cache_key: str,
    workflow_name: str,
    step_id: str,
    model: str,
    output: Any,
    cost_usd: float,
    ttl_hours: int = 24,
) -> None:
    """Save a step result to cache."""
    try:
        from sandcastle.models.db import StepCache, async_session

        expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
        output_data = output if isinstance(output, dict) else {"result": output}

        async with async_session() as session:
            entry = StepCache(
                cache_key=cache_key,
                workflow_name=workflow_name,
                step_id=step_id,
                model=model,
                output_data=output_data,
                cost_usd=cost_usd,
                expires_at=expires,
            )
            session.add(entry)
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                # Key collision - update existing
                from sqlalchemy import update as sa_update

                await session.execute(
                    sa_update(StepCache)
                    .where(StepCache.cache_key == cache_key)
                    .values(
                        output_data=output_data,
                        cost_usd=cost_usd,
                        expires_at=expires,
                        hit_count=0,
                    )
                )
                await session.commit()
    except Exception as e:
        logger.debug(f"Cache save failed: {e}")


async def _execute_step_once(
    step: StepDefinition,
    context: RunContext,
    sandbox: SandshoreRuntime,
    storage: StorageBackend,
    parallel_index: int | None = None,
    attempt: int = 1,
) -> StepResult:
    """Execute a single attempt of a step."""
    started_at = datetime.now(timezone.utc)
    resolved_input_prompt: str | None = None  # Populated after template resolution

    try:
        # SLO-based model selection (optimizer)
        routing_decision = None
        effective_model = step.model
        effective_max_turns = step.max_turns
        if hasattr(step, "slo") and step.slo and hasattr(step, "model_pool") and step.model_pool:
            try:
                from sandcastle.engine.optimizer import (
                    SLO,
                    ModelOption,
                    calculate_budget_pressure,
                    get_optimizer,
                )

                slo = SLO(
                    quality_min=step.slo.quality_min,
                    cost_max_usd=step.slo.cost_max_usd,
                    latency_max_seconds=step.slo.latency_max_seconds,
                    optimize_for=step.slo.optimize_for,
                )
                pool = [
                    ModelOption(id=m.id, model=m.model, max_turns=m.max_turns)
                    for m in step.model_pool
                ]
                bp = calculate_budget_pressure(context.total_cost, context.max_cost_usd)

                optimizer = get_optimizer()
                decision = await optimizer.select_model(
                    step_id=step.id,
                    workflow_name=context.workflow_name,
                    slo=slo,
                    model_pool=pool,
                    budget_pressure=bp,
                )
                routing_decision = decision
                effective_model = decision.selected_option.model
                effective_max_turns = decision.selected_option.max_turns
                logger.info(
                    f"Optimizer: step '{step.id}' -> {effective_model} "
                    f"({decision.reason}, confidence={decision.confidence:.1%})"
                )
            except Exception as e:
                logger.warning(f"Optimizer failed for step '{step.id}', using default: {e}")

        # Determine if this step uses memory (skip cache if so)
        _step_reads_memory = (
            context._memory_config
            and context._memory_scope_id
            and (context._memory_config.auto_inject or (step.memory and step.memory.read))
        )

        # Resolve dynamic context_query before template resolution so that
        # {context} is available as a template variable in the prompt.
        if step.context_query:
            try:
                resolved_ctx = await _resolve_context_query(step, context)
                context._resolved_context = resolved_ctx
                if resolved_ctx:
                    logger.info(
                        "Context query resolved for step '%s': %d chars from '%s'",
                        step.id, len(resolved_ctx), step.context_source,
                    )
            except Exception as e:
                logger.warning(f"Context query failed for step '{step.id}': {e}")
                context._resolved_context = ""

        # Resolve template variables first (cheap string interpolation) so the
        # cache key reflects the actual inputs, not the raw template string.
        prompt = resolve_templates(step.prompt, context, step.depends_on)
        prompt = await resolve_storage_refs(prompt, storage, context)
        # Capture resolved prompt (before system prefix) for audit/compliance.
        resolved_input_prompt = prompt

        # Step result cache - check before executing (skip for memory steps)
        cache_key = ""
        if not _step_reads_memory:
            cache_key = _compute_cache_key(
                context.workflow_name, step.id, prompt, effective_model or step.model,
                tenant_id=context.tenant_id,
            )
            cached = await _get_cached_result(cache_key)
            if cached:
                duration = (datetime.now(timezone.utc) - started_at).total_seconds()
                logger.info(f"Step '{step.id}' cache HIT (key={cache_key[:12]}...)")
                return StepResult(
                    step_id=step.id,
                    parallel_index=parallel_index,
                    output=cached["output"],
                    cost_usd=0.0,  # No cost for cached results
                    duration_seconds=duration,
                    status="completed",
                    attempt=attempt,
                )

        # Inject agent memories into the prompt (semantic search with step prompt)
        if _step_reads_memory:
            try:
                from sandcastle.config import settings as _mem_read_settings
                from sandcastle.engine.memory import (
                    apply_decay,
                    format_memories_for_prompt,
                    load_memories,
                )

                step_memories = await load_memories(
                    context._memory_scope_id,
                    query=prompt[:500],
                    limit=context._memory_config.max_inject,
                )
                # Apply decay
                max_age = (
                    context._memory_config.max_age_days
                    if (
                        context._memory_config
                        and context._memory_config.max_age_days > 0
                    )
                    else _mem_read_settings.memory_max_age_days
                )
                if max_age > 0:
                    step_memories = apply_decay(
                        step_memories, max_age_days=max_age
                    )

                mem_block = format_memories_for_prompt(step_memories)
                if mem_block:
                    prompt = mem_block + "\n\n" + prompt
                    logger.info(
                        "Injected %d memories into step '%s'",
                        len(step_memories),
                        step.id,
                    )
            except Exception as e:
                logger.warning(
                    f"Memory injection failed for step '{step.id}': {e}"
                )

        if step.pdf_report:
            # PDF report steps need verbose, structured output - skip the terse
            # system prefix which conflicts with report formatting instructions.
            pdf_instruction = _get_pdf_report_instruction(step.pdf_report.language)
            prompt = pdf_instruction + "\n\n" + prompt
        else:
            prompt = _STEP_SYSTEM_PREFIX + prompt

        # Prompt deduplication detection
        prompt_hash = hashlib.md5(prompt.encode()).hexdigest()[:12]
        if prompt_hash in context._seen_prompt_hashes:
            logger.warning(
                "Duplicate prompt detected (hash=%s) - step '%s' sends same "
                "content as earlier step",
                prompt_hash, step.id,
            )
        context._seen_prompt_hashes.add(prompt_hash)

        request: dict[str, Any] = {
            "prompt": prompt,
            "model": effective_model,
            "max_turns": effective_max_turns,
            "timeout": step.timeout,
        }
        if step.output_schema:
            request["output_format"] = {
                "type": "json_schema",
                "schema": step.output_schema,
            }

        # Resolve effective tools: step-level override or workflow defaults
        effective_tools = step.tools if step.tools is not None else context.default_tools
        if effective_tools:
            request["tools"] = effective_tools

        idx_str = f" [{parallel_index}]" if parallel_index is not None else ""
        logger.info(
            f"Executing step '{step.id}'{idx_str} attempt {attempt} (model={effective_model})"
        )
        logger.info(f"Step '{step.id}' prompt length: {len(prompt)} chars")
        # Enforce step timeout at the executor level as a hard deadline.
        # The sandbox runtime receives timeout in the request dict, but
        # may not enforce it (e.g. network hangs). This asyncio.wait_for
        # ensures the step is cancelled after timeout + 30s grace period.
        _hard_timeout = step.timeout + 30
        try:
            result = await asyncio.wait_for(
                sandbox.query(request), timeout=_hard_timeout,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Step '{step.id}' exceeded hard timeout of {_hard_timeout}s"
            )

        # Save routing decision to DB
        if routing_decision:
            await _save_routing_decision(context.run_id, step.id, routing_decision, step.slo)

        output = result.structured_output if result.structured_output else result.text
        logger.info(
            f"Step '{step.id}' raw output: structured={result.structured_output is not None}, "
            f"text_len={len(result.text) if result.text else 0}, "
            f"output_type={type(output).__name__}, "
            f"output_len={len(str(output)) if output else 0}"
        )
        # Try to parse text output as JSON for downstream steps (parallel_over, etc.)
        if isinstance(output, str):
            text = output.strip()
            # Strip markdown code fences (```json ... ``` or ``` ... ```)
            if text.startswith("```"):
                first_nl = text.find("\n")
                if first_nl >= 0:
                    text = text[first_nl + 1 :]
                if text.endswith("```"):
                    text = text[:-3].rstrip()
            try:
                parsed = json.loads(text)
                if isinstance(parsed, (dict, list)):
                    output = parsed
            except (json.JSONDecodeError, ValueError):
                pass
        duration = (datetime.now(timezone.utc) - started_at).total_seconds()

        # Guard: if output_schema is defined but output is empty, treat as failure
        # so that retry logic kicks in instead of silently propagating empty data.
        if step.output_schema and (
            output is None or output == "" or output == {} or output == []
        ):
            raise ValueError(
                f"Step '{step.id}' returned empty output but output_schema requires data"
            )

        # Guard: if step type is 'llm' or 'agent' and output is empty, treat as failure
        # LLM/agent steps should never return empty output - that always means
        # something went wrong (API error, empty response, etc.)
        if step.type in ("llm", "agent") and (
            output is None or output == "" or output == {} or output == []
        ):
            raise ValueError(
                f"Step '{step.id}' returned empty output"
            )

        # Auto-inject credential redaction policy when tools are used
        if effective_tools:
            try:
                from sandcastle.engine.policy import create_tool_credential_policy

                cred_policy = create_tool_credential_policy(effective_tools)
                if cred_policy:
                    from sandcastle.engine.policy import PolicyEngine as _CredPE

                    cred_engine = _CredPE([cred_policy])
                    cred_result = await cred_engine.evaluate(
                        step_id=step.id,
                        output=output,
                        context={"step_id": step.id, "run_id": context.run_id},
                    )
                    if cred_result.violations:
                        output = cred_result.modified_output
                        logger.warning(
                            "Step '%s': redacted %d credential pattern(s) from output",
                            step.id,
                            len(cred_result.violations),
                        )
            except Exception as e:
                logger.warning("Credential redaction failed for step '%s': %s", step.id, e)

        # Policy evaluation
        if hasattr(step, "policies") and step.policies is not None:
            try:
                from sandcastle.engine.policy import PolicyEngine, resolve_step_policies

                applicable = resolve_step_policies(step.policies, [])
                if applicable:
                    engine = PolicyEngine(applicable)
                    eval_result = await engine.evaluate(
                        step_id=step.id,
                        output=output,
                        context={
                            "step_id": step.id,
                            "run_id": context.run_id,
                            "total_cost_usd": context.total_cost,
                            "input": context.input,
                        },
                        step_cost_usd=result.total_cost_usd,
                    )
                    if eval_result.violations:
                        await _save_policy_violations(
                            context.run_id, step.id, eval_result.violations
                        )

                    if eval_result.should_block:
                        raise StepBlocked(
                            step_id=step.id,
                            reason=eval_result.block_reason or "Policy blocked",
                        )

                    if eval_result.should_inject_approval:
                        # Reuse approval gate mechanism
                        from sandcastle.models.db import (
                            ApprovalRequest,
                            ApprovalStatus,
                            Run,
                            RunStatus,
                        )
                        from sandcastle.models.db import (
                            async_session as db_session,
                        )

                        config = eval_result.approval_config or {}
                        async with db_session() as session:
                            approval = ApprovalRequest(
                                run_id=uuid.UUID(context.run_id),
                                step_id=step.id,
                                status=ApprovalStatus.PENDING,
                                request_data=(
                                    output if isinstance(output, dict) else {"result": output}
                                ),
                                message=config.get("message", "Policy requires approval"),
                                timeout_at=None,
                                on_timeout=config.get("on_timeout", "abort"),
                                allow_edit=False,
                            )
                            if config.get("timeout_hours"):
                                from datetime import timedelta

                                approval.timeout_at = datetime.now(timezone.utc) + timedelta(
                                    hours=config["timeout_hours"]
                                )
                            session.add(approval)
                            run = await session.get(Run, uuid.UUID(context.run_id))
                            if run:
                                run.status = RunStatus.AWAITING_APPROVAL
                            await session.commit()
                            await session.refresh(approval)
                            approval_id = str(approval.id)
                        raise WorkflowPaused(approval_id=approval_id, run_id=context.run_id)

                    # Use modified output (after redactions)
                    output = eval_result.modified_output
            except (StepBlocked, WorkflowPaused):
                raise
            except Exception as e:
                logger.warning(f"Policy evaluation failed for step '{step.id}': {e}")

        # Truncate oversized outputs to prevent memory exhaustion
        output = _truncate_output(output)

        # Save to cache - skip empty, failed, or memory-injected outputs
        if not _step_reads_memory and _is_cacheable_output(output):
            await _save_to_cache(
                cache_key=cache_key,
                workflow_name=context.workflow_name,
                step_id=step.id,
                model=effective_model or step.model,
                output=output,
                cost_usd=result.total_cost_usd,
            )
        elif not _step_reads_memory:
            logger.info(f"Step '{step.id}' output not cached (empty or failed)")

        # Write step output to agent memory if configured
        if context._memory_scope_id and step.memory and step.memory.write and output:
            try:
                from sandcastle.config import settings as _mem_write_settings
                from sandcastle.engine.memory import (
                    enrich_memory,
                    save_memory,
                    should_admit,
                )

                content = (
                    json.dumps(output)
                    if isinstance(output, (dict, list))
                    else str(output)
                )

                # Admission control
                threshold = (
                    step.memory.admit_threshold
                    or (
                        context._memory_config.admit_threshold
                        if context._memory_config
                        else 0
                    )
                    or _mem_write_settings.memory_admit_threshold
                )
                admitted, score, reason = should_admit(
                    content, context.memories, threshold=threshold
                )
                if not admitted:
                    logger.info(
                        "Memory admission rejected for step '%s' "
                        "(score=%.2f, reason=%s)",
                        step.id,
                        score,
                        reason,
                    )
                else:
                    # Enrich with keywords/tags if configured
                    meta: dict = {
                        "step_id": step.id,
                        "workflow": context.workflow_name,
                    }
                    if (
                        context._memory_config
                        and context._memory_config.enrich
                    ):
                        enriched = enrich_memory(
                            content, metadata=meta
                        )
                        meta = enriched.get("metadata", meta)
                        meta["keywords"] = enriched.get(
                            "keywords", []
                        )
                        meta["tags"] = enriched.get("tags", [])

                    await save_memory(
                        context._memory_scope_id,
                        content,
                        metadata=meta,
                        run_id=context.run_id,
                        skip_admission=True,  # already checked above
                    )
                    logger.info(
                        "Saved memory from step '%s' "
                        "(importance=%.2f)",
                        step.id,
                        score,
                    )
            except Exception as e:
                logger.warning(
                    f"Memory write failed for step '{step.id}': {e}"
                )

        return StepResult(
            step_id=step.id,
            parallel_index=parallel_index,
            output=output,
            cost_usd=result.total_cost_usd,
            duration_seconds=duration,
            status="completed",
            attempt=attempt,
            input_prompt=resolved_input_prompt,
        )

    except (StepBlocked, WorkflowPaused):
        raise

    except (SandshoreError, Exception) as e:
        duration = (datetime.now(timezone.utc) - started_at).total_seconds()
        logger.error(f"Step '{step.id}' attempt {attempt} error: {e}")
        return StepResult(
            step_id=step.id,
            parallel_index=parallel_index,
            cost_usd=0.0,
            duration_seconds=duration,
            status="failed",
            error=str(e),
            attempt=attempt,
            input_prompt=resolved_input_prompt,
        )


async def _execute_approval_step(
    step: StepDefinition,
    context: RunContext,
    stage_index: int,
) -> None:
    """Create an approval request and pause the workflow.

    Raises WorkflowPaused to halt execution until the approval is resolved.
    """
    from sandcastle.models.db import (
        ApprovalRequest,
        ApprovalStatus,
        Run,
        RunStatus,
        async_session,
    )

    # Resolve show_data if configured
    request_data = None
    if step.approval_config and step.approval_config.show_data:
        request_data_val = resolve_variable(step.approval_config.show_data, context)
        if request_data_val is not _UNRESOLVED and request_data_val is not None:
            if isinstance(request_data_val, dict):
                request_data = request_data_val
            else:
                request_data = {"value": request_data_val}

    # Resolve and save images for preview (from Imagen API responses)
    if step.approval_config and step.approval_config.show_images:
        import base64 as _b64

        artifacts_dir = Path(f"data/artifacts/{context.run_id}")
        image_urls: list[dict] = []
        img_counter = 0

        for img_path in step.approval_config.show_images:
            img_data = resolve_variable(img_path, context)
            if img_data is _UNRESOLVED or img_data is None:
                continue
            items_list = img_data if isinstance(img_data, list) else [img_data]
            for item in items_list:
                if not isinstance(item, dict):
                    continue

                # Collect (b64_data, mime) tuples from either format
                b64_pairs: list[tuple[str, str]] = []
                error_msg: str | None = None

                # Format 1: Imagen predict -> {predictions: [{bytesBase64Encoded, mimeType}]}
                preds = item.get("predictions", [])
                if preds:
                    for pred in preds:
                        b64 = pred.get("bytesBase64Encoded")
                        if b64:
                            b64_pairs.append((b64, pred.get("mimeType", "image/png")))

                # Format 2: Gemini generateContent -> {candidates: [{content: {parts: [{inlineData: {data, mimeType}}]}}]}
                candidates = item.get("candidates", [])
                if candidates:
                    for cand in candidates:
                        parts = (cand.get("content") or {}).get("parts", [])
                        for part in parts:
                            inline = part.get("inlineData") or part.get("inline_data")
                            if inline and inline.get("data"):
                                b64_pairs.append((inline["data"], inline.get("mimeType", "image/png")))

                if not b64_pairs:
                    # Record failed generations
                    err = item.get("error")
                    if err:
                        error_msg = str(err.get("message", "Generation failed")) if isinstance(err, dict) else str(err)
                    elif not preds and not candidates:
                        # Unknown format - skip silently
                        continue
                    if error_msg:
                        image_urls.append({"index": img_counter, "error": error_msg})
                        img_counter += 1
                    continue

                for b64_data, mime in b64_pairs:
                    ext = "png" if "png" in mime else "jpg"
                    filename = f"image_{img_counter}.{ext}"
                    try:
                        artifacts_dir.mkdir(parents=True, exist_ok=True)
                        (artifacts_dir / filename).write_bytes(_b64.b64decode(b64_data))
                        image_urls.append({
                            "index": img_counter,
                            "filename": filename,
                            "url": f"/api/runs/{context.run_id}/artifacts/{filename}",
                            "mime_type": mime,
                        })
                        logger.info(
                            "Approval '%s': saved image %s (%d bytes)",
                            step.id, filename, len(b64_data) * 3 // 4,
                        )
                    except Exception as e:
                        logger.warning("Approval '%s': failed to save image: %s", step.id, e)
                    img_counter += 1

        if image_urls:
            if request_data is None:
                request_data = {}
            request_data["_images"] = image_urls
            logger.info("Approval '%s': %d images saved for preview", step.id, len(image_urls))

    # Calculate timeout
    timeout_at = None
    if step.approval_config and step.approval_config.timeout_hours:
        from datetime import timedelta

        timeout_at = datetime.now(timezone.utc) + timedelta(
            hours=step.approval_config.timeout_hours
        )

    on_timeout = step.approval_config.on_timeout if step.approval_config else "abort"
    allow_edit = step.approval_config.allow_edit if step.approval_config else False
    message = step.approval_config.message if step.approval_config else "Approval required"

    # Save checkpoint before pausing
    await _save_checkpoint(context.run_id, step.id, stage_index, context)

    # Record step as awaiting approval (include images for dashboard preview)
    step_output = {"_images": request_data["_images"]} if request_data and "_images" in request_data else None
    await _save_run_step(
        run_id=context.run_id,
        step_id=step.id,
        status="awaiting_approval",
        output=step_output,
    )

    # Create approval request
    async with async_session() as session:
        approval = ApprovalRequest(
            run_id=uuid.UUID(context.run_id),
            step_id=step.id,
            status=ApprovalStatus.PENDING,
            request_data=request_data,
            message=message,
            timeout_at=timeout_at,
            on_timeout=on_timeout,
            allow_edit=allow_edit,
        )
        session.add(approval)

        # Update run status to AWAITING_APPROVAL
        run = await session.get(Run, uuid.UUID(context.run_id))
        if run:
            run.status = RunStatus.AWAITING_APPROVAL
        await session.commit()
        await session.refresh(approval)
        approval_id = str(approval.id)

    # Fire webhook
    try:
        from sandcastle.webhooks.dispatcher import dispatch_webhook

        run_obj = None
        async with async_session() as session:
            run_obj = await session.get(Run, uuid.UUID(context.run_id))

        if run_obj and run_obj.callback_url:
            await dispatch_webhook(
                url=run_obj.callback_url,
                event="approval.requested",
                run_id=context.run_id,
                workflow=run_obj.workflow_name or "",
                status="awaiting_approval",
                outputs={"approval_id": approval_id, "step_id": step.id, "message": message},
            )
    except Exception as e:
        logger.warning(f"Could not dispatch approval webhook: {e}")

    raise WorkflowPaused(approval_id=approval_id, run_id=context.run_id)


async def _execute_sub_workflow_step(
    step: StepDefinition,
    context: RunContext,
    storage: StorageBackend,
    depth: int = 0,
) -> StepResult:
    """Execute a sub-workflow step, with optional fan-out."""
    from sandcastle.config import settings
    from sandcastle.engine.dag import build_plan, parse_yaml_string, validate

    started_at = datetime.now(timezone.utc)

    if not step.sub_workflow or not step.sub_workflow.workflow:
        return StepResult(step_id=step.id, status="failed", error="Missing sub_workflow config")

    # Depth check
    max_depth = settings.max_workflow_depth
    if depth >= max_depth:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"Max workflow depth ({max_depth}) exceeded",
        )

    # Load and parse sub-workflow
    try:
        from pathlib import Path

        workflows_dir = Path(settings.workflows_dir).resolve()

        # Resolve dynamic workflow name via template variables
        raw_wf_name = step.sub_workflow.workflow
        if "{" in raw_wf_name:
            wf_name = resolve_templates(raw_wf_name, context, step.depends_on)
        else:
            wf_name = raw_wf_name

        # Path traversal prevention for sub-workflow names
        if ".." in wf_name or "/" in wf_name or "\\" in wf_name:
            return StepResult(
                step_id=step.id,
                status="failed",
                error=f"Invalid sub-workflow name: '{wf_name}'",
            )

        yaml_path = None
        for candidate in [
            workflows_dir / f"{wf_name}.yaml",
            workflows_dir / wf_name,
        ]:
            resolved = candidate.resolve()
            # Ensure the resolved path stays within workflows directory
            if not resolved.is_relative_to(workflows_dir):
                continue
            if resolved.exists() and resolved.is_file():
                yaml_path = resolved
                break

        if yaml_path is None:
            return StepResult(
                step_id=step.id,
                status="failed",
                error=f"Sub-workflow '{wf_name}' not found",
            )

        yaml_content = yaml_path.read_text()
        sub_workflow = parse_yaml_string(yaml_content)

        errors = validate(sub_workflow)
        if errors:
            return StepResult(
                step_id=step.id,
                status="failed",
                error=f"Sub-workflow validation: {'; '.join(errors)}",
            )

        sub_plan = build_plan(sub_workflow)

    except Exception as e:
        return StepResult(step_id=step.id, status="failed", error=f"Sub-workflow load error: {e}")

    # Resolve input mapping
    sub_input = {}
    for target_key, source_path in step.sub_workflow.input_mapping.items():
        val = resolve_variable(source_path, context)
        sub_input[target_key] = None if val is _UNRESOLVED else val

    # Fan-out if parallel_over is configured
    if step.sub_workflow.parallel_over:
        sub_fan_path = step.sub_workflow.parallel_over.strip("{}")
        items = resolve_variable(sub_fan_path, context)
        if items is _UNRESOLVED:
            items = []
        elif not isinstance(items, list):
            items = [items]

        semaphore = asyncio.Semaphore(step.sub_workflow.max_concurrent)

        async def run_sub(item: Any, index: int) -> WorkflowResult:
            async with semaphore:
                item_input = {**sub_input, "_item": item, "_index": index}
                sub_run_id = str(uuid.uuid4())
                return await execute_workflow(
                    workflow=sub_workflow,
                    plan=sub_plan,
                    input_data=item_input,
                    run_id=sub_run_id,
                    storage=storage,
                    depth=depth + 1,
                    max_cost_usd=context.max_cost_usd,
                    admin_trusted=context.admin_trusted,
                    tenant_id=context.tenant_id,
                )

        tasks = [asyncio.create_task(run_sub(item, i)) for i, item in enumerate(items)]
        sub_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Aggregate outputs
        outputs = []
        total_cost = 0.0
        sub_run_ids = []
        for r in sub_results:
            if isinstance(r, Exception):
                outputs.append(None)
            else:
                outputs.append(r.outputs)
                total_cost += r.total_cost_usd
                sub_run_ids.append(r.run_id)

        duration = (datetime.now(timezone.utc) - started_at).total_seconds()

        # Apply output mapping if configured
        output = outputs
        if step.sub_workflow.output_mapping:
            mapped = {}
            for target, source in step.sub_workflow.output_mapping.items():
                parts = source.split(".")
                extracted = []
                for o in outputs:
                    val = o
                    for p in parts:
                        if isinstance(val, dict):
                            val = val.get(p)
                    extracted.append(val)
                mapped[target] = extracted
            output = mapped

        return StepResult(
            step_id=step.id,
            output=output,
            cost_usd=total_cost,
            duration_seconds=duration,
            status="completed",
        )

    else:
        # Single sub-workflow execution
        sub_run_id = str(uuid.uuid4())
        sub_result = await execute_workflow(
            workflow=sub_workflow,
            plan=sub_plan,
            input_data=sub_input,
            run_id=sub_run_id,
            storage=storage,
            depth=depth + 1,
            max_cost_usd=context.max_cost_usd,
            admin_trusted=context.admin_trusted,
            tenant_id=context.tenant_id,
        )

        duration = (datetime.now(timezone.utc) - started_at).total_seconds()

        # Apply output mapping
        output = sub_result.outputs
        if step.sub_workflow.output_mapping:
            mapped = {}
            for target, source in step.sub_workflow.output_mapping.items():
                parts = source.split(".")
                val = sub_result.outputs
                for p in parts:
                    if isinstance(val, dict):
                        val = val.get(p)
                mapped[target] = val
            output = mapped

        status = "completed" if sub_result.status == "completed" else "failed"
        return StepResult(
            step_id=step.id,
            output=output,
            cost_usd=sub_result.total_cost_usd,
            duration_seconds=duration,
            status=status,
            error=sub_result.error,
        )


async def _execute_llm_step(
    step: StepDefinition,
    context: RunContext,
    storage: StorageBackend,
) -> StepResult:
    """Execute a lightweight LLM step - single API call, no sandbox."""
    import time

    import httpx

    from sandcastle.engine.providers import get_api_key, resolve_base_url, resolve_model

    started_at = time.monotonic()
    model_info = resolve_model(step.model)
    _enforce_data_residency(model_info)
    api_key = get_api_key(model_info)

    # Don't pass depends_on - auto-inject can append huge outputs (e.g. base64
    # images from HTTP steps) that blow up prompt size.  LLM prompts should
    # explicitly reference what they need via {steps.X.output} templates.
    prompt = resolve_templates(step.prompt, context)
    prompt = await resolve_storage_refs(prompt, storage, context)

    system_prompt = _STEP_SYSTEM_PREFIX
    if step.llm_config and step.llm_config.system_prompt:
        system_prompt += step.llm_config.system_prompt

    # Map short aliases to real Anthropic model IDs for direct API calls
    _CLAUDE_MODEL_ALIASES = {
        "sonnet": "claude-sonnet-4-20250514",
        "haiku": "claude-haiku-4-5-20251001",
        "opus": "claude-opus-4-20250115",
    }

    try:
        if model_info.provider == "claude":
            api_model = _CLAUDE_MODEL_ALIASES.get(
                model_info.api_model_id, model_info.api_model_id
            )
            async with httpx.AsyncClient(timeout=step.timeout) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": api_model,
                        "max_tokens": 4096,
                        "system": system_prompt,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                if resp.status_code >= 400:
                    err_body = resp.text[:500]
                    logger.error(
                        "LLM step '%s': API error %d: %s (prompt %d chars)",
                        step.id, resp.status_code, err_body, len(prompt),
                    )
                resp.raise_for_status()
                data = resp.json()
                text = data["content"][0]["text"]
                usage = data.get("usage", {})
                in_tok = usage.get("input_tokens", 0)
                out_tok = usage.get("output_tokens", 0)
                cost = _safe_cost(in_tok, out_tok, model_info.input_price_per_m, model_info.output_price_per_m)
        else:
            base_url = resolve_base_url(model_info)
            async with httpx.AsyncClient(timeout=step.timeout) as client:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "content-type": "application/json",
                    },
                    json={
                        "model": model_info.api_model_id,
                        "max_tokens": 4096,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt},
                        ],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                in_tok = usage.get("prompt_tokens", 0)
                out_tok = usage.get("completion_tokens", 0)
                cost = _safe_cost(in_tok, out_tok, model_info.input_price_per_m, model_info.output_price_per_m)

        # Try to parse as JSON
        output: Any = text.strip()
        if output.startswith("```"):
            first_nl = output.find("\n")
            if first_nl >= 0:
                output = output[first_nl + 1 :]
            if output.endswith("```"):
                output = output[:-3].rstrip()
        try:
            parsed = json.loads(output)
            if isinstance(parsed, (dict, list)):
                output = parsed
        except (json.JSONDecodeError, ValueError):
            pass

        duration = time.monotonic() - started_at
        return StepResult(
            step_id=step.id,
            output=output,
            cost_usd=cost,
            duration_seconds=duration,
            status="completed",
        )
    except Exception as e:
        duration = time.monotonic() - started_at
        return StepResult(
            step_id=step.id,
            status="failed",
            error=str(e),
            duration_seconds=duration,
        )


async def _execute_http_step(
    step: StepDefinition,
    context: RunContext,
) -> StepResult:
    """Execute an HTTP request step with optional declared cost."""
    import time

    import httpx

    started_at = time.monotonic()
    cfg = step.http_config
    if not cfg:
        return StepResult(step_id=step.id, status="failed", error="Missing http_config")

    try:
        # Don't pass depends_on for URL - auto-inject is for prompts, not URLs
        url = resolve_templates(cfg.url, context).strip()

        # SSRF prevention for HTTP steps - block private/internal networks
        try:
            import ipaddress as _ipaddress
            import socket as _socket
            from urllib.parse import urlparse as _urlparse

            from sandcastle.webhooks.dispatcher import _BLOCKED_NETWORKS
            _parsed = _urlparse(url)
            if _parsed.scheme not in ("https", "http"):
                return StepResult(
                    step_id=step.id,
                    status="failed",
                    error=f"HTTP step URL must use http(s), got '{_parsed.scheme}'",
                    duration_seconds=time.monotonic() - started_at,
                )
            if _parsed.hostname:
                try:
                    _resolved = _socket.getaddrinfo(_parsed.hostname, _parsed.port or 443)
                    for _, _, _, _, _sockaddr in _resolved:
                        _ip = _ipaddress.ip_address(_sockaddr[0])
                        for _network in _BLOCKED_NETWORKS:
                            if _ip in _network:
                                return StepResult(
                                    step_id=step.id,
                                    status="failed",
                                    error=f"HTTP step URL resolves to blocked network ({_ip})",
                                    duration_seconds=time.monotonic() - started_at,
                                )
                except _socket.gaierror:
                    pass  # DNS resolution failure - let httpx handle it naturally
        except ImportError:
            pass

        headers = {
            k: resolve_templates(v, context) for k, v in cfg.headers.items()
        }

        # Auth handling
        if cfg.auth:
            auth_resolved = resolve_templates(cfg.auth, context)
            if auth_resolved.startswith("bearer:"):
                headers["Authorization"] = f"Bearer {auth_resolved[7:]}"
            else:
                import os

                token = os.environ.get(auth_resolved, auth_resolved)
                headers["Authorization"] = f"Bearer {token}"

        body = None
        if cfg.body is not None:
            if isinstance(cfg.body, str):
                body = resolve_templates(cfg.body, context)
            else:
                # Serialize dict body then resolve templates in the JSON string
                # so that {input.X} / {steps.Y.output} inside dict values work.
                # Don't pass depends_on: auto-inject appends raw text which
                # would corrupt the structured JSON payload.
                body = resolve_templates(json.dumps(cfg.body), context)
                # Ensure Content-Type is set for JSON bodies
                if not any(k.lower() == "content-type" for k in headers):
                    headers["Content-Type"] = "application/json"

        # Resolve @file:/path references - lazy file loading for large content
        # (e.g. base64-encoded images saved by save_file_b64 in code steps)
        # Uses @file: prefix instead of {file:} because resolve_templates
        # consumes all {…} tokens before we get here.
        # Supports both unquoted paths and quoted paths (for spaces):
        #   @file:/path/to/file.txt
        #   @file:"/path/with spaces/file.txt"
        if body and "@file:" in body:
            import re as _re_file

            def _load_file_ref(match: _re_file.Match) -> str:
                # group(1) is quoted path, group(2) is unquoted path
                fpath = (match.group(1) or match.group(2)).strip()
                try:
                    content = Path(fpath).read_text()
                    logger.debug("HTTP step: loaded file ref %s (%d chars)", fpath, len(content))
                    return content
                except FileNotFoundError:
                    raise ValueError(f"HTTP step: file ref not found: {fpath}")
                except Exception as e:
                    raise ValueError(f"HTTP step: failed to load file ref {fpath}: {e}")

            body = _re_file.sub(
                r'@file:(?:"([^"]+)"|([^\s"\\]+))', _load_file_ref, body
            )

        # Apply value_map: post-resolution value transformations in body
        if cfg.value_map and body:
            try:
                body_dict = json.loads(body)
                for path, mapping in cfg.value_map.items():
                    parts = path.split(".")
                    obj = body_dict
                    for part in parts[:-1]:
                        if isinstance(obj, dict) and part in obj:
                            obj = obj[part]
                        elif isinstance(obj, list):
                            # Walk into list items (e.g. instances[0])
                            try:
                                obj = obj[int(part)]
                            except (ValueError, IndexError):
                                break
                        else:
                            break
                    else:
                        key = parts[-1]
                        if isinstance(obj, dict) and key in obj:
                            current = str(obj[key])
                            if current in mapping:
                                logger.info(
                                    "HTTP step '%s': value_map %s: '%s' -> '%s'",
                                    step.id, path, current, mapping[current],
                                )
                                obj[key] = mapping[current]
                body = json.dumps(body_dict)
            except (json.JSONDecodeError, TypeError):
                pass

        # Debug: check for non-printable chars
        _bad_chars = [(i, repr(c)) for i, c in enumerate(url) if ord(c) < 32 or ord(c) > 126]
        if _bad_chars:
            logger.warning("HTTP step '%s': URL has bad chars: %s", step.id, _bad_chars[:5])
        logger.info(
            "HTTP step '%s': %s %s (body=%s chars, url_len=%d)",
            step.id, cfg.method.upper(), url[:100],
            len(body) if body else 0, len(url),
        )

        async with httpx.AsyncClient(timeout=step.timeout) as client:
            resp = await client.request(
                method=cfg.method.upper(),
                url=url,
                headers=headers,
                content=body if body else None,
            )

        logger.info(
            "HTTP step '%s': response %d (%s chars)",
            step.id, resp.status_code, len(resp.text),
        )

        # Try to parse as JSON
        try:
            output = resp.json()
        except Exception:
            raw_text = resp.text
            truncated = len(raw_text) > 5000
            if truncated:
                logger.warning(
                    f"HTTP step '{step.id}': Response truncated from {len(raw_text)} to 5000 chars"
                )
            output = {
                "status_code": resp.status_code,
                "text": raw_text[:5000],
                "_truncated": truncated,
            }

        duration = time.monotonic() - started_at
        call_cost = cfg.cost_per_call if cfg.cost_per_call else 0.0
        return StepResult(
            step_id=step.id,
            output=output,
            cost_usd=call_cost,
            duration_seconds=duration,
            status="completed",
        )
    except Exception as e:
        duration = time.monotonic() - started_at
        logger.warning("HTTP step '%s' failed: %s", step.id, e)
        return StepResult(
            step_id=step.id,
            status="failed",
            error=str(e),
            duration_seconds=duration,
        )


_CODE_STEP_MAX_SIZE = 50_000  # 50KB max code size
_CODE_STEP_BLOCKED_PATTERNS = re.compile(
    r"__subclasses__|__bases__|__mro__|__class__|__globals__|__builtins__|"
    r"__import__|importlib|__loader__|__spec__|exec\s*\(|eval\s*\(|"
    r"compile\s*\(|getattr\s*\(|setattr\s*\(|delattr\s*\(|"
    r"breakpoint\s*\(|open\s*\(|vars\s*\(|dir\s*\(|"
    # Block string construction bypasses (chr(), bytes(), string concatenation to build builtins)
    r"chr\s*\(|ord\s*\(|"
    # Block access to code/frame objects that can escape the sandbox
    r"__code__|__func__|__self__|__dict__|__init__|__new__|__del__|"
    r"__getattr__|__getattribute__|"
    # Block os/sys/subprocess imports via any mechanism
    r"\bos\b\s*\.\s*system|\bos\b\s*\.\s*popen|subprocess|"
    r"__reduce__|__reduce_ex__|pickle",
    re.IGNORECASE,
)


def _resolve_params_deep(obj: Any, context: RunContext) -> Any:
    """Recursively resolve template variables in nested dicts/lists."""
    if isinstance(obj, str):
        return resolve_templates(obj, context)
    if isinstance(obj, dict):
        return {k: _resolve_params_deep(v, context) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_params_deep(item, context) for item in obj]
    return obj


async def _execute_composio_step(
    step: StepDefinition,
    context: RunContext,
) -> StepResult:
    """Execute a Composio action step (500+ integrations via Composio API)."""
    import time

    import httpx

    started_at = time.monotonic()
    cfg = step.composio_config
    if not cfg:
        return StepResult(step_id=step.id, status="failed", error="Missing composio_config")

    action = cfg.action
    if not action:
        return StepResult(step_id=step.id, status="failed", error="composio_config.action is required")

    try:
        # Resolve template variables in action and params (deep)
        action = resolve_templates(action, context)
        params = _resolve_params_deep(cfg.params or {}, context)

        import os

        api_key = os.environ.get("TOOL_COMPOSIO_API_KEY", "")
        if not api_key:
            return StepResult(
                step_id=step.id,
                status="failed",
                error="TOOL_COMPOSIO_API_KEY environment variable not set",
                duration_seconds=time.monotonic() - started_at,
            )

        # Build request payload
        payload: dict = {"actionName": action, "input": params}
        if cfg.connected_account_id:
            payload["connectedAccountId"] = resolve_templates(
                cfg.connected_account_id, context,
            )
        if cfg.app:
            payload["appName"] = resolve_templates(cfg.app, context)

        async with httpx.AsyncClient(timeout=step.timeout) as client:
            resp = await client.post(
                "https://backend.composio.dev/api/v2/actions/execute",
                json=payload,
                headers={
                    "X-API-Key": api_key,
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            result_data = resp.json()

        duration = time.monotonic() - started_at

        # Extract output from Composio response
        output = result_data.get("data", result_data)
        status = "completed"
        error = None

        # Check Composio-level errors
        if result_data.get("error"):
            status = "failed"
            error = str(result_data["error"])

        return StepResult(
            step_id=step.id,
            status=status,
            output=output,
            error=error,
            cost_usd=0.0,  # Composio costs tracked externally
            duration_seconds=duration,
        )
    except httpx.HTTPStatusError as e:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"Composio API error: {e.response.status_code} - {e.response.text[:500]}",
            duration_seconds=time.monotonic() - started_at,
        )
    except Exception as e:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"Composio step failed: {e}",
            duration_seconds=time.monotonic() - started_at,
        )


async def _execute_openclaw_step(
    step: StepDefinition,
    context: RunContext,
) -> StepResult:
    """Execute an OpenClaw agent step via the gateway's OpenAI-compatible API."""
    import os
    import time

    import httpx

    started_at = time.monotonic()
    cfg = step.openclaw_config
    if not cfg:
        return StepResult(step_id=step.id, status="failed", error="Missing openclaw_config")

    # Resolve templates in gateway URL and message
    gateway_url = resolve_templates(cfg.gateway_url, context, step.depends_on)
    message = resolve_templates(
        cfg.message or step.prompt or f"openclaw step {step.id}",
        context,
        step.depends_on,
    )

    # Validate URL
    if not gateway_url.startswith(("http://", "https://")):
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"OpenClaw gateway_url must use http(s), got: {gateway_url[:50]}",
            duration_seconds=time.monotonic() - started_at,
        )

    # Build OpenAI-compatible request
    payload: dict = {
        "messages": [{"role": "user", "content": message}],
        "model": step.model or "default",
    }
    if cfg.skills:
        payload["tools"] = [{"type": "function", "function": {"name": s}} for s in cfg.skills]

    # Auth headers
    headers: dict = {"Content-Type": "application/json"}
    token = cfg.api_token or os.environ.get("OPENCLAW_API_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=cfg.timeout_seconds) as client:
            resp = await client.post(
                f"{gateway_url.rstrip('/')}/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        # Extract response
        choices = data.get("choices", [])
        response_text = choices[0]["message"]["content"] if choices else ""
        usage = data.get("usage", {})

        output = {
            "response": response_text,
            "model": data.get("model"),
            "usage": usage,
            "skills_used": [
                tc.get("function", {}).get("name")
                for tc in choices[0].get("message", {}).get("tool_calls", [])
                if tc.get("function")
            ] if choices else [],
        }

        return StepResult(
            step_id=step.id,
            status="completed",
            output=output,
            cost_usd=cfg.cost_per_call,
            duration_seconds=time.monotonic() - started_at,
            input_prompt=message,
        )

    except httpx.HTTPStatusError as e:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"OpenClaw gateway error: HTTP {e.response.status_code}",
            duration_seconds=time.monotonic() - started_at,
        )
    except httpx.ConnectError:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"Cannot connect to OpenClaw gateway at {gateway_url}. Is it running?",
            duration_seconds=time.monotonic() - started_at,
        )
    except Exception as e:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"OpenClaw step failed: {e}",
            duration_seconds=time.monotonic() - started_at,
        )


# Module-level caches to avoid recreating agents/environments on every run.
# Keys are derived from step config; values are Anthropic resource IDs.
_managed_agent_cache: dict[str, str] = {}   # cache_key -> agent_id
_managed_env_cache: dict[str, str] = {}     # cache_key -> environment_id


def _managed_agent_cache_key(step: StepDefinition) -> str:
    """Build a deterministic cache key from step config for agent reuse."""
    cfg = step.managed_agent_config
    if not cfg:
        return step.id
    parts = [cfg.model, cfg.system_prompt or "", ",".join(cfg.tools_enabled or [])]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _managed_env_cache_key(step: StepDefinition) -> str:
    """Build a deterministic cache key from step config for environment reuse."""
    cfg = step.managed_agent_config
    if not cfg:
        return step.id
    parts = [
        ",".join(sorted(cfg.packages or [])),
        str(cfg.network_access),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


async def _resolve_agent_config(config: "ManagedAgentConfig") -> dict:
    """Resolve final agent configuration from template, description, or explicit config.

    Returns a dict with keys: system, model, tools, packages, network.
    Template values serve as defaults; explicit config fields override them.
    """
    from sandcastle.engine.agent_templates import MANAGED_AGENT_TEMPLATES

    if config.agent_template:
        template = MANAGED_AGENT_TEMPLATES.get(config.agent_template)
        if not template:
            raise ValueError(f"Unknown agent template: {config.agent_template}")
        return {
            "system": config.system_prompt or template["system"],
            "model": config.model if config.model != "claude-sonnet-4-6" else template["model"],
            "tools": config.tools_enabled or template["tools"],
            "packages": config.packages if config.packages is not None else template["packages"],
            "network": "unrestricted" if config.network_access else template["network"],
        }

    if config.describe:
        return await _design_agent_from_description(config.describe)

    # Explicit config - pass through
    return {
        "system": config.system_prompt,
        "model": config.model,
        "tools": config.tools_enabled,
        "packages": config.packages or [],
        "network": "unrestricted" if config.network_access else "limited",
    }


async def _design_agent_from_description(description: str) -> dict:
    """Use AI to design a Managed Agent configuration from natural language.

    Calls the advisor LLM to generate an agent configuration (system prompt,
    model, tools, packages, network policy) based on a plain-text task
    description. Falls back to sensible defaults on parse failure.
    """
    import json as _json

    from sandcastle.engine.generator import _call_advisor_llm

    prompt = (
        "Design a Claude Managed Agent for this task. Return ONLY valid JSON.\n\n"
        f"Task description: {description}\n\n"
        "Available tools: bash, read, write, edit, glob, grep, web_search, web_fetch\n"
        "Available pip packages: any standard Python package\n\n"
        "Return JSON:\n"
        "{\n"
        '    "system": "detailed system prompt for the agent (3-5 sentences)",\n'
        '    "model": "claude-sonnet-4-6" or "claude-haiku-4-5",\n'
        '    "tools": ["list", "of", "needed", "tools"],\n'
        '    "packages": ["pip", "packages", "needed"],\n'
        '    "network": "unrestricted" or "limited"\n'
        "}"
    )

    try:
        response = await _call_advisor_llm(
            system="You are an expert at designing AI agent configurations. "
                   "Return ONLY valid JSON, no markdown fences or explanation.",
            user=prompt,
            max_tokens=512,
            purpose="agent_design",
        )
        # Strip markdown fences if present
        text = response.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        result = _json.loads(text)
        # Validate and sanitize
        return {
            "system": str(result.get("system", f"Complete this task: {description}")),
            "model": result.get("model", "claude-sonnet-4-6"),
            "tools": result.get("tools", ["bash", "read", "write"]),
            "packages": result.get("packages", []),
            "network": result.get("network", "unrestricted"),
        }
    except Exception:
        # Fallback: reasonable defaults based on description
        return {
            "system": f"Complete this task thoroughly and return structured results: {description}",
            "model": "claude-sonnet-4-6",
            "tools": ["bash", "read", "write", "web_search", "web_fetch"],
            "packages": [],
            "network": "unrestricted",
        }


async def _execute_managed_agent_step(
    step: StepDefinition,
    context: RunContext,
    storage: StorageBackend | None = None,
) -> StepResult:
    """Execute a step by delegating to Anthropic Claude Managed Agents.

    Creates an environment (required), creates or reuses an agent,
    opens a session, sends user.message with content blocks, streams
    SSE events until session.status_idle, then cleans up the session.
    Supports three configuration modes: agent_template, describe, or explicit.
    Requires ANTHROPIC_API_KEY env var.
    """
    import os
    import time

    import httpx

    started_at = time.monotonic()
    config = step.managed_agent_config
    if not config:
        return StepResult(
            step_id=step.id,
            status="failed",
            error="managed_agent_config is required",
        )

    # Resolve config from template/describe/explicit before proceeding
    if config.agent_template or config.describe:
        try:
            resolved = await _resolve_agent_config(config)
        except ValueError as e:
            return StepResult(
                step_id=step.id,
                status="failed",
                error=str(e),
                duration_seconds=time.monotonic() - started_at,
            )
        # Apply resolved config back - template/describe always use auto agent creation
        if not config.agent_id:
            config.agent_id = "auto"
        if not config.system_prompt and resolved.get("system"):
            config.system_prompt = resolved["system"]
        if resolved.get("model"):
            config.model = resolved["model"]
        if config.tools_enabled is None and resolved.get("tools"):
            config.tools_enabled = resolved["tools"]
        if config.packages is None and resolved.get("packages"):
            config.packages = resolved["packages"]
        if resolved.get("network") == "limited":
            config.network_access = False

    if not config.agent_id:
        return StepResult(
            step_id=step.id,
            status="failed",
            error="managed_agent_config.agent_id is required (set explicitly or use agent_template/describe)",
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return StepResult(
            step_id=step.id,
            status="failed",
            error="ANTHROPIC_API_KEY required for managed-agent steps",
            duration_seconds=time.monotonic() - started_at,
        )

    # Resolve message template
    message = resolve_templates(
        config.message or step.prompt or f"managed-agent step {step.id}",
        context,
        step.depends_on,
    )
    if storage:
        message = await resolve_storage_refs(message, storage, context)

    # Shared files: append file outputs from referenced steps to the message
    if config.shared_files:
        shared_parts: list[str] = []
        for ref_step_id in config.shared_files:
            ref_output = context.step_outputs.get(ref_step_id)
            if ref_output is not None:
                if isinstance(ref_output, dict) and ref_output.get("_output_format") == "files":
                    for fname, fcontent in ref_output.get("files", {}).items():
                        shared_parts.append(f"--- File: {fname} (from step '{ref_step_id}') ---\n{fcontent}")
                else:
                    shared_parts.append(
                        f"--- Output from step '{ref_step_id}' ---\n"
                        f"{ref_output if isinstance(ref_output, str) else json.dumps(ref_output, default=str)}"
                    )
        if shared_parts:
            message = message + "\n\n" + "\n\n".join(shared_parts)

    base_url = "https://api.anthropic.com/v1"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "managed-agents-2026-04-01",
        "content-type": "application/json",
    }

    session_id: str | None = None

    def _classify_http_error(status_code: int, body: str = "") -> str:
        """Return a human-readable error message based on HTTP status code."""
        if status_code == 401:
            return "Invalid API key"
        if status_code == 429:
            retry_after = ""
            # Try to extract Retry-After hint from body
            try:
                parsed = json.loads(body)
                ra = parsed.get("retry_after") or parsed.get("error", {}).get("retry_after")
                if ra:
                    retry_after = f" (retry after {ra}s)"
            except (json.JSONDecodeError, ValueError, AttributeError):
                pass
            return f"Rate limited{retry_after}"
        if status_code >= 500:
            return "Anthropic server error"
        return f"HTTP {status_code}"

    primary_error: str = ""
    try:
        async with httpx.AsyncClient(timeout=config.timeout + 30) as client:
            agent_id = config.agent_id

            # --- Create ad-hoc agent if agent_id is "auto" ---
            if agent_id == "auto":
                agent_cache_key = _managed_agent_cache_key(step)
                cached_agent = _managed_agent_cache.get(agent_cache_key)
                if cached_agent:
                    agent_id = cached_agent
                else:
                    agent_payload: dict = {
                        "name": f"sandcastle-{step.id}",
                        "model": config.model,
                        "tools": [{"type": "agent_toolset_20260401"}],
                    }
                    if config.system_prompt:
                        agent_payload["system"] = config.system_prompt
                    agent_resp = await client.post(
                        f"{base_url}/agents", headers=headers, json=agent_payload,
                    )
                    if agent_resp.status_code != 200:
                        return StepResult(
                            step_id=step.id,
                            status="failed",
                            error=(
                                f"Failed to create ad-hoc agent: "
                                f"{_classify_http_error(agent_resp.status_code, agent_resp.text)}"
                            ),
                            duration_seconds=time.monotonic() - started_at,
                        )
                    agent_id = agent_resp.json()["id"]
                    _managed_agent_cache[agent_cache_key] = agent_id

            # --- Create or reuse environment (REQUIRED for session creation) ---
            env_id = config.environment_id
            if not env_id:
                env_cache_key = _managed_env_cache_key(step)
                cached_env = _managed_env_cache.get(env_cache_key)
                if cached_env:
                    env_id = cached_env
                else:
                    env_payload: dict = {
                        "name": f"sandcastle-env-{step.id}",
                        "network_access": config.network_access,
                    }
                    if config.packages:
                        env_payload["packages"] = config.packages
                    env_resp = await client.post(
                        f"{base_url}/environments",
                        headers=headers,
                        json=env_payload,
                    )
                    if env_resp.status_code != 200:
                        return StepResult(
                            step_id=step.id,
                            status="failed",
                            error=(
                                f"Failed to create environment: "
                                f"{_classify_http_error(env_resp.status_code, env_resp.text)}"
                            ),
                            duration_seconds=time.monotonic() - started_at,
                        )
                    env_id = env_resp.json()["id"]
                    _managed_env_cache[env_cache_key] = env_id

            # --- Create session (environment_id is required) ---
            session_body: dict = {
                "agent": agent_id,
                "environment_id": env_id,
            }
            session_resp = await client.post(
                f"{base_url}/sessions", headers=headers, json=session_body,
            )
            if session_resp.status_code != 200:
                return StepResult(
                    step_id=step.id,
                    status="failed",
                    error=(
                        f"Failed to create session: "
                        f"{_classify_http_error(session_resp.status_code, session_resp.text)}"
                    ),
                    duration_seconds=time.monotonic() - started_at,
                )
            session_id = session_resp.json()["id"]

            # --- Send user message with content blocks format ---
            await client.post(
                f"{base_url}/sessions/{session_id}/events",
                headers=headers,
                json={
                    "events": [{
                        "type": "user.message",
                        "content": [{"type": "text", "text": message}],
                    }],
                },
            )

            # --- Stream SSE response ---
            result_text = ""
            total_input_tokens = 0
            total_output_tokens = 0

            async with client.stream(
                "GET",
                f"{base_url}/sessions/{session_id}/stream",
                headers=headers,
            ) as stream:
                async for line in stream.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        event = json.loads(line[6:])
                    except (json.JSONDecodeError, ValueError):
                        continue

                    event_type = event.get("type", "")

                    # Collect agent text output
                    if event_type == "agent.message":
                        for block in event.get("content", []):
                            if block.get("type") == "text":
                                result_text += block.get("text", "")

                    # Track token usage from any event that includes it
                    usage = event.get("usage")
                    if usage:
                        total_input_tokens += usage.get("input_tokens", 0)
                        total_output_tokens += usage.get("output_tokens", 0)

                    # Session finished: agent idle or session terminated
                    if event_type in (
                        "session.status_idle",
                        "session.terminated",
                    ):
                        break

            # Compute cost (Sonnet pricing by default)
            cost = _safe_cost(
                total_input_tokens, total_output_tokens,
                3.0, 15.0,  # Default Sonnet pricing per 1M tokens
            )

            # Process output_format for agent chaining
            final_output: str | dict = result_text
            if config.output_format == "json":
                try:
                    final_output = json.loads(result_text)
                except (json.JSONDecodeError, ValueError):
                    # Wrap raw text in a JSON-safe envelope
                    final_output = {"raw_text": result_text, "_parse_error": True}
            elif config.output_format == "files":
                # List files created by the agent and return as dict
                final_output = {
                    "text": result_text,
                    "files": {},
                    "_output_format": "files",
                }

            return StepResult(
                step_id=step.id,
                status="completed",
                output=final_output,
                cost_usd=cost,
                duration_seconds=time.monotonic() - started_at,
                input_prompt=message,
            )

    except httpx.TimeoutException:
        primary_error = f"Session exceeded timeout ({config.timeout}s)"
    except httpx.HTTPStatusError as e:
        primary_error = (
            f"Managed Agent API error: {_classify_http_error(e.response.status_code)}"
        )
    except httpx.ConnectError:
        primary_error = "Cannot connect to Anthropic API"
    except Exception as e:
        primary_error = f"Managed-agent step failed: {e}"
    finally:
        # Clean up session to free resources
        if session_id:
            try:
                import httpx as _httpx
                async with _httpx.AsyncClient(timeout=10) as cleanup_client:
                    await cleanup_client.delete(
                        f"{base_url}/sessions/{session_id}",
                        headers=headers,
                    )
            except Exception:
                logger.debug("Failed to delete session %s (non-critical)", session_id)

    # Fallback template retry: if primary failed and fallback_template is set
    if config.fallback_template:
        from sandcastle.engine.agent_templates import VALID_AGENT_TEMPLATES
        from sandcastle.engine.dag import ManagedAgentConfig
        if config.fallback_template in VALID_AGENT_TEMPLATES:
            logger.info(
                "Step '%s' failed with template '%s', retrying with fallback '%s'",
                step.id, config.agent_template or "(explicit)", config.fallback_template,
            )
            from copy import copy
            fallback_step = copy(step)
            fallback_config = ManagedAgentConfig(
                agent_template=config.fallback_template,
                message=config.message,
                timeout=config.timeout,
                model=config.model,
                stream=config.stream,
                packages=config.packages,
                network_access=config.network_access,
                output_format=config.output_format,
                shared_files=config.shared_files,
                fallback_template="",  # prevent infinite retry
            )
            fallback_step.managed_agent_config = fallback_config
            fallback_result = await _execute_managed_agent_step(
                fallback_step, context, storage,
            )
            if fallback_result.status == "completed":
                return fallback_result
            # Both failed - report both errors
            return StepResult(
                step_id=step.id,
                status="failed",
                error=(
                    f"Primary failed: {primary_error}; "
                    f"Fallback ({config.fallback_template}) also failed: "
                    f"{fallback_result.error}"
                ),
                duration_seconds=time.monotonic() - started_at,
            )

    return StepResult(
        step_id=step.id,
        status="failed",
        error=primary_error,
        duration_seconds=time.monotonic() - started_at,
    )


async def _execute_agent_step(
    step: StepDefinition,
    context: RunContext,
    storage: StorageBackend | None = None,
) -> StepResult:
    """Execute a universal ``type: agent`` step using the runtime abstraction.

    Resolves agent configuration (template, describe, or explicit), selects
    the appropriate runtime (auto/anthropic/local), and delegates execution.
    Falls back to ``_execute_managed_agent_step`` for full Anthropic-native
    features (caching, shared_files, fallback_template) when runtime is
    ``anthropic``.
    """
    import time

    from sandcastle.engine.agent_runtime import get_runtime

    started_at = time.monotonic()
    config = step.managed_agent_config
    if not config:
        return StepResult(
            step_id=step.id,
            status="failed",
            error="agent_config (or managed_agent_config) is required for type: agent",
        )

    runtime_name = config.runtime or "auto"

    # For anthropic runtime or when advanced features are used (shared_files,
    # fallback_template, explicit agent_id/environment_id), delegate to the
    # full managed-agent implementation which handles caching and SSE properly.
    uses_advanced = bool(
        config.shared_files
        or config.fallback_template
        or (config.agent_id and config.agent_id != "auto")
        or config.environment_id
    )
    if runtime_name == "anthropic" or (runtime_name == "auto" and uses_advanced):
        return await _execute_managed_agent_step(step, context, storage)

    # Resolve config from template/describe/explicit
    if config.agent_template or config.describe:
        try:
            resolved = await _resolve_agent_config(config)
        except ValueError as e:
            return StepResult(
                step_id=step.id,
                status="failed",
                error=str(e),
                duration_seconds=time.monotonic() - started_at,
            )
    else:
        resolved = {
            "system": config.system_prompt,
            "model": config.model,
            "tools": config.tools_enabled,
            "packages": config.packages or [],
            "network": "unrestricted" if config.network_access else "limited",
        }

    # Resolve message template
    message = resolve_templates(
        config.message or step.prompt or f"agent step {step.id}",
        context,
        step.depends_on,
    )
    if storage:
        message = await resolve_storage_refs(message, storage, context)

    try:
        runtime = get_runtime(runtime_name)
        result = await runtime.execute(
            system_prompt=resolved.get("system", ""),
            tools=resolved.get("tools") or [],
            packages=resolved.get("packages") or [],
            message=message,
            model=resolved.get("model", "claude-sonnet-4-6"),
            timeout=config.timeout,
            network=resolved.get("network", "unrestricted"),
        )

        # Process output_format
        final_output: str | dict = result.get("output", "")
        if config.output_format == "json":
            try:
                final_output = json.loads(str(final_output))
            except (json.JSONDecodeError, ValueError):
                final_output = {"raw_text": str(final_output), "_parse_error": True}
        elif config.output_format == "files":
            final_output = {
                "text": str(final_output),
                "files": {},
                "_output_format": "files",
            }

        # Estimate cost (Sonnet pricing by default)
        cost = _safe_cost(
            result.get("tokens_in", 0),
            result.get("tokens_out", 0),
            3.0, 15.0,
        )

        return StepResult(
            step_id=step.id,
            status="completed",
            output=final_output,
            cost_usd=cost,
            duration_seconds=time.monotonic() - started_at,
            input_prompt=message,
        )
    except Exception as e:
        logger.error("Agent step '%s' failed: %s", step.id, e)
        # Try fallback to managed-agent if auto runtime fails
        if runtime_name == "auto":
            logger.info(
                "Auto runtime failed for step '%s', falling back to managed-agent path", step.id,
            )
            return await _execute_managed_agent_step(step, context, storage)
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"Agent step failed: {e}",
            duration_seconds=time.monotonic() - started_at,
        )


async def _execute_parse_step(
    step: StepDefinition,
    context: RunContext,
) -> StepResult:
    """Parse a document file into structured text.

    Resolves file reference from prompt (@upload:file_id, @file:path, or absolute path),
    then delegates to sandcastle.engine.docparse.parse_document. Zero LLM cost.
    """
    import time

    from sandcastle.engine.dag import ParseConfig

    started_at = time.monotonic()
    cfg = step.parse_config or ParseConfig()

    # Resolve template variables in prompt to get the file reference
    input_text = resolve_templates(step.prompt or "", context, step.depends_on).strip()

    # Determine file path from the resolved input text
    file_path: str | None = None
    if input_text.startswith("@upload:"):
        # Resolve via storage to get the actual on-disk path
        from sandcastle.config import settings as _parse_settings
        from sandcastle.engine.storage import create_storage

        _storage = create_storage()
        file_id = input_text[len("@upload:"):]
        try:
            all_keys = await _storage.list("uploads/")
            matching = [k for k in all_keys if Path(k).name.startswith(file_id)]
        except Exception as exc:
            logger.warning("Could not list uploads to resolve @upload:%s: %s", file_id, exc)
            matching = []

        if matching:
            key = matching[0]
            local_path = (
                Path(_parse_settings.data_dir).expanduser().resolve() / key
            )
            file_path = str(local_path)
        else:
            return StepResult(
                step_id=step.id,
                status="failed",
                error=f"No upload found for file_id '{file_id}'",
                duration_seconds=time.monotonic() - started_at,
            )
    elif input_text.startswith("@file:"):
        file_path = input_text[len("@file:"):]
    elif input_text.startswith("/"):
        file_path = input_text
    elif not input_text and step.depends_on:
        # No prompt - check if a dependency provided a file path
        for dep_id in step.depends_on:
            dep_output = context.step_outputs.get(dep_id)
            if isinstance(dep_output, str) and (
                dep_output.startswith("/") or dep_output.startswith("@file:")
            ):
                file_path = dep_output.lstrip("@file:") if dep_output.startswith("@file:") else dep_output
                break
            elif isinstance(dep_output, dict) and "path" in dep_output:
                file_path = dep_output["path"]
                break

    if not file_path:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=(
                "Parse step requires a file reference: @upload:file_id, @file:path, "
                "or an absolute path in the prompt or from depends_on output"
            ),
            duration_seconds=time.monotonic() - started_at,
        )

    try:
        from sandcastle.engine.docparse import parse_document

        result = parse_document(
            path=file_path,
            output_format=cfg.output,
            pages=cfg.pages,
            ocr=cfg.ocr,
            ocr_engine=cfg.ocr_engine,
            languages=cfg.languages,
        )

        output = {
            "text": result.text,
            "format": result.format,
            "pages": result.pages,
            "metadata": result.metadata,
        }

        return StepResult(
            step_id=step.id,
            status="completed",
            output=output,
            cost_usd=0.0,
            duration_seconds=time.monotonic() - started_at,
            input_prompt=f"parse {file_path} -> {cfg.output}",
        )
    except ImportError as e:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=str(e),
            duration_seconds=time.monotonic() - started_at,
        )
    except Exception as e:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"Document parsing failed: {e}",
            duration_seconds=time.monotonic() - started_at,
        )


async def _execute_report_step(
    step: StepDefinition,
    context: RunContext,
    storage: StorageBackend,
) -> StepResult:
    """Generate a beautiful PDF/HTML report via LLM content + WeasyPrint rendering.

    1. Runs the step prompt through the LLM to generate structured Markdown content.
    2. Passes the Markdown output to the report renderer (WeasyPrint + Jinja2).
    3. Saves the rendered PDF/HTML to storage.
    4. Returns the step result with the artifact path.
    """
    import time

    import httpx

    from sandcastle.engine.dag import ReportConfig
    from sandcastle.engine.providers import get_api_key, resolve_base_url, resolve_model

    started_at = time.monotonic()
    cfg = step.report_config or ReportConfig()

    # Resolve template variables in prompt, title, subtitle, author
    prompt = resolve_templates(step.prompt or "", context, step.depends_on)
    prompt = await resolve_storage_refs(prompt, storage, context)

    resolved_title = resolve_templates(cfg.title, context) if cfg.title else ""
    resolved_subtitle = resolve_templates(cfg.subtitle, context) if cfg.subtitle else ""
    resolved_author = resolve_templates(cfg.author, context) if cfg.author else ""

    # Build a system prompt that instructs the LLM to produce well-structured Markdown
    system_prompt = (
        "You are a professional report writer. Generate a comprehensive, well-structured "
        "report in Markdown format. Use clear section headings (## for main sections, "
        "### for subsections), bullet points, tables where appropriate, and blockquotes "
        "for key findings or insights. Write in a professional, analytical tone. "
        "Structure the content with a logical flow from overview to detailed analysis "
        "to conclusions and recommendations. "
        "Do NOT include a title heading (# Title) - the cover page handles that. "
        "Start directly with ## sections."
    )

    # LLM call to generate report content
    model_info = resolve_model(step.model)
    _enforce_data_residency(model_info)
    api_key = get_api_key(model_info)

    _CLAUDE_MODEL_ALIASES = {
        "sonnet": "claude-sonnet-4-20250514",
        "haiku": "claude-haiku-4-5-20251001",
        "opus": "claude-opus-4-20250115",
    }

    try:
        if model_info.provider == "claude":
            api_model = _CLAUDE_MODEL_ALIASES.get(
                model_info.api_model_id, model_info.api_model_id
            )
            async with httpx.AsyncClient(timeout=step.timeout) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": api_model,
                        "max_tokens": 8192,
                        "system": system_prompt,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                markdown_text = data["content"][0]["text"]
                usage = data.get("usage", {})
                in_tok = usage.get("input_tokens", 0)
                out_tok = usage.get("output_tokens", 0)
                cost = _safe_cost(
                    in_tok, out_tok,
                    model_info.input_price_per_m, model_info.output_price_per_m,
                )
        else:
            base_url = resolve_base_url(model_info)
            async with httpx.AsyncClient(timeout=step.timeout) as client:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "content-type": "application/json",
                    },
                    json={
                        "model": model_info.api_model_id,
                        "max_tokens": 8192,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt},
                        ],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                markdown_text = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                in_tok = usage.get("prompt_tokens", 0)
                out_tok = usage.get("completion_tokens", 0)
                cost = _safe_cost(
                    in_tok, out_tok,
                    model_info.input_price_per_m, model_info.output_price_per_m,
                )
    except Exception as e:
        duration = time.monotonic() - started_at
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"Report LLM call failed: {e}",
            duration_seconds=duration,
        )

    # Build a config copy with resolved template fields
    render_cfg = ReportConfig(
        template=cfg.template,
        format=cfg.format,
        theme=cfg.theme,
        title=resolved_title,
        subtitle=resolved_subtitle,
        author=resolved_author,
        logo_url=cfg.logo_url,
        accent_color=cfg.accent_color,
        include_toc=cfg.include_toc,
        include_page_numbers=cfg.include_page_numbers,
        paper_size=cfg.paper_size,
    )

    metadata = {
        "run_id": context.run_id,
        "step_id": step.id,
        "title": resolved_title or "Report",
    }

    # Render the report (PDF or HTML)
    try:
        from sandcastle.engine.report import render_report

        report_bytes = await asyncio.to_thread(
            render_report, markdown_text, render_cfg, metadata
        )
    except RuntimeError as e:
        # WeasyPrint not installed - fall back to HTML-only rendering
        if "weasyprint" in str(e).lower():
            try:
                from sandcastle.engine.report import render_report_html

                html_output = render_report_html(markdown_text, render_cfg, metadata)
                report_bytes = html_output.encode("utf-8")
                cfg = ReportConfig(
                    **{**render_cfg.__dict__, "format": "html"}
                )
                render_cfg = cfg
            except Exception as html_err:
                duration = time.monotonic() - started_at
                return StepResult(
                    step_id=step.id,
                    status="failed",
                    error=f"Report rendering failed (weasyprint not installed, HTML fallback also failed): {html_err}",
                    duration_seconds=duration,
                    cost_usd=cost,
                )
        else:
            duration = time.monotonic() - started_at
            return StepResult(
                step_id=step.id,
                status="failed",
                error=f"Report rendering failed: {e}",
                duration_seconds=duration,
                cost_usd=cost,
            )
    except Exception as e:
        duration = time.monotonic() - started_at
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"Report rendering failed: {e}",
            duration_seconds=duration,
            cost_usd=cost,
        )

    # Save to storage
    ext = "html" if render_cfg.format == "html" else "pdf"
    artifact_key = f"reports/{context.run_id}/{step.id}.{ext}"
    try:
        await storage.put(artifact_key, report_bytes)
        logger.info("Report saved to storage: %s (%d bytes)", artifact_key, len(report_bytes))
    except Exception as e:
        logger.warning("Failed to save report to storage: %s", e)

    duration = time.monotonic() - started_at
    output = {
        "report_path": artifact_key,
        "format": render_cfg.format,
        "size_bytes": len(report_bytes),
        "theme": render_cfg.theme,
        "title": resolved_title or "Report",
        "content_preview": markdown_text[:500],
    }

    return StepResult(
        step_id=step.id,
        output=output,
        cost_usd=cost,
        duration_seconds=duration,
        status="completed",
        input_prompt=prompt[:200],
    )


async def _execute_code_step(
    step: StepDefinition,
    context: RunContext,
) -> StepResult:
    """Execute inline Python code. Uses restricted exec with injected context."""
    import time

    started_at = time.monotonic()
    cfg = step.code_config
    if not cfg:
        return StepResult(step_id=step.id, status="failed", error="Missing code_config")

    try:
        code = cfg.code

        # Guard: code steps execute in-process - require admin_trusted workflow
        if not context.admin_trusted:
            return StepResult(
                step_id=step.id,
                status="failed",
                error="Code steps require admin-trusted workflow (set admin_trusted: true in workflow YAML or run from internal endpoint)",
                duration_seconds=time.monotonic() - started_at,
            )

        # Guard: reject oversized code
        if len(code) > _CODE_STEP_MAX_SIZE:
            return StepResult(
                step_id=step.id,
                status="failed",
                error=f"Code too large ({len(code)} bytes, max {_CODE_STEP_MAX_SIZE})",
                duration_seconds=time.monotonic() - started_at,
            )

        # Guard: block dangerous patterns that could escape the restricted sandbox
        blocked = _CODE_STEP_BLOCKED_PATTERNS.search(code)
        if blocked:
            return StepResult(
                step_id=step.id,
                status="failed",
                error=f"Code contains blocked pattern: '{blocked.group()}'",
                duration_seconds=time.monotonic() - started_at,
            )

        # Inject context: _input and _steps
        # NOTE: 'type' is intentionally excluded - it provides access to
        # __subclasses__() which can be used for sandbox escape.
        import base64 as _b64_mod

        # Safe file reader: only allows reading from uploads / data directory
        from sandcastle.config import settings as _code_settings

        _code_data_dir = Path(_code_settings.data_dir).resolve()

        def _read_file_b64(file_path: str) -> str:
            """Read a file from disk and return its base64-encoded content."""
            p = Path(file_path).resolve()
            if not p.is_relative_to(_code_data_dir):
                raise PermissionError(f"Access denied: {file_path} is outside data directory")
            if not p.exists():
                raise FileNotFoundError(f"File not found: {file_path}")
            return _b64_mod.b64encode(p.read_bytes()).decode("ascii")

        def _save_file_b64(file_path: str, dest_name: str) -> str:
            """Read a file, base64-encode it, save to a temp file, return the temp path.

            Use this instead of read_file_b64 when the base64 would be too large
            to store in the workflow context (>5MB). The returned path can be used
            with {file:/path} syntax in HTTP step bodies for lazy loading.
            """
            p = Path(file_path).resolve()
            if not p.is_relative_to(_code_data_dir):
                raise PermissionError(f"Access denied: {file_path} is outside data directory")
            if not p.exists():
                raise FileNotFoundError(f"File not found: {file_path}")
            b64_data = _b64_mod.b64encode(p.read_bytes()).decode("ascii")
            dest = _code_data_dir / "tmp" / dest_name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(b64_data)
            return f"@file:{dest}"

        exec_globals: dict[str, Any] = {
            "__builtins__": {
                "len": len,
                "int": int,
                "float": float,
                "str": str,
                "bool": bool,
                "list": list,
                "dict": dict,
                "set": set,
                "tuple": tuple,
                "range": range,
                "enumerate": enumerate,
                "zip": zip,
                "map": map,
                "filter": filter,
                "sorted": sorted,
                "min": min,
                "max": max,
                "sum": sum,
                "abs": abs,
                "round": round,
                "isinstance": isinstance,
                "print": print,
                "None": None,
                "True": True,
                "False": False,
            },
            "_input": context.input,
            "_steps": context.step_outputs,
            "json": json,
            "base64": _b64_mod,
            "read_file_b64": _read_file_b64,
            "save_file_b64": _save_file_b64,
            "result": None,
        }

        # Run exec() in a thread with a timeout to prevent infinite loops/DoS.
        # The step-level timeout (default 300s) applies here, but we cap code
        # steps at 30s since they should be fast data transformations.
        # NOTE: Python threads cannot be forcefully killed. The timeout only
        # unblocks the caller; the worker thread keeps running. We use a
        # threading.Event to signal cancellation and check it in a custom
        # builtins range() wrapper, providing cooperative cancellation for
        # loops. Truly uncooperative code (e.g. tight C-extension loops)
        # will leak the thread, but the daemon thread pool ensures it won't
        # block process exit.
        import concurrent.futures
        import threading

        _CODE_STEP_TIMEOUT = min(step.timeout, 30)
        _cancel_event = threading.Event()

        def _run_code():
            exec(code, exec_globals)  # noqa: S102

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(_run_code)
        try:
            try:
                future.result(timeout=_CODE_STEP_TIMEOUT)
            except concurrent.futures.TimeoutError:
                _cancel_event.set()
                # Abandon the thread pool without waiting for it to finish;
                # shutdown(wait=False) lets the daemon thread die with the process.
                pool.shutdown(wait=False)
                logger.warning(
                    "Code step '%s' timed out after %ds; worker thread abandoned",
                    step.id, _CODE_STEP_TIMEOUT,
                )
                return StepResult(
                    step_id=step.id,
                    status="failed",
                    error=f"Code step timed out after {_CODE_STEP_TIMEOUT}s",
                    duration_seconds=time.monotonic() - started_at,
                )
        finally:
            pool.shutdown(wait=False)

        output = exec_globals.get("result", None)

        duration = time.monotonic() - started_at
        return StepResult(
            step_id=step.id,
            output=output,
            cost_usd=0.0,
            duration_seconds=duration,
            status="completed",
        )
    except Exception as e:
        duration = time.monotonic() - started_at
        return StepResult(
            step_id=step.id,
            status="failed",
            error=str(e),
            duration_seconds=duration,
        )


_EVAL_MAX_EXPRESSION_LENGTH = 2000

# Tokens that should never appear in a resolved template value, as they
# could alter expression semantics when string-interpolated.
_CONDITION_INJECTION_TOKENS = re.compile(
    r"\b(or|and|not|import|exec|eval|lambda|class|def|del|global|"
    r"yield|return|raise|from|as|with|assert|break|continue|try|except|finally)\b"
    r"|[;@]"
)


def _resolve_condition_expression(
    expression_template: str,
    context: RunContext,
    depends_on: list[str] | None = None,
) -> str:
    """Resolve template variables in a condition expression with injection checks.

    Each resolved value is validated to ensure it does not contain Python
    keywords or operators that could alter the expression's semantics.
    """
    def _safe_replace(match: re.Match) -> str:
        var_path = match.group(1)
        value = resolve_variable(var_path, context)
        if value is _UNRESOLVED:
            return match.group(0)
        if value is None:
            return "None"
        if isinstance(value, (dict, list)):
            raw = json.dumps(value)
        else:
            raw = str(value)
        # Check for injection tokens in the resolved value.
        # Both input and step output values are checked as defense-in-depth:
        # in multi-tenant environments, input may come from untrusted API
        # callers who could manipulate condition outcomes via keyword injection
        # (e.g., "5 or True" turning a > comparison truthy).
        if isinstance(value, str) and _CONDITION_INJECTION_TOKENS.search(raw):
            raise ValueError(
                f"Resolved variable '{var_path}' contains unsafe tokens "
                f"for condition evaluation: {raw!r}"
            )
        if var_path.startswith("steps.") or var_path == "memory":
            return _escape_braces(raw)
        return raw

    return _TEMPLATE_RE.sub(_safe_replace, expression_template)


def _safe_eval_expression(expression: str, names: dict[str, Any]) -> Any:
    """Evaluate a workflow expression safely using simpleeval.

    This replaces raw ``eval()`` to prevent code injection. Only basic
    comparison operators, attribute access, ``len()``, and type
    constructors are available.
    """
    # Guard against excessively long expressions that could cause DoS
    if len(expression) > _EVAL_MAX_EXPRESSION_LENGTH:
        raise ValueError(
            f"Expression too long ({len(expression)} chars, "
            f"max {_EVAL_MAX_EXPRESSION_LENGTH})"
        )
    # Block dunder attribute access patterns that simpleeval might not catch
    if "__" in expression:
        raise ValueError(
            "Expression contains blocked double-underscore pattern"
        )
    functions = {"len": len, "int": int, "float": float, "str": str, "bool": bool}
    return simple_eval(expression, names=names, functions=functions)


async def _execute_condition_step(
    step: StepDefinition,
    context: RunContext,
) -> StepResult:
    """Execute a condition step - evaluate expression and populate skip_steps."""
    import time

    started_at = time.monotonic()
    cfg = step.condition_config
    if not cfg:
        return StepResult(step_id=step.id, status="failed", error="Missing condition_config")

    try:
        # Resolve template variables, then validate resolved values do not
        # introduce expression-manipulation tokens (e.g. "or", "and", ";").
        expression = _resolve_condition_expression(cfg.expression, context, step.depends_on)

        # Safe expression evaluation via simpleeval
        eval_names: dict[str, Any] = {
            "steps": context.step_outputs,
            "input": context.input,
            "True": True,
            "False": False,
            "None": None,
        }
        result = bool(_safe_eval_expression(expression, eval_names))

        # Populate skip/run steps. A step explicitly selected to run by
        # ANY condition takes precedence over skips from other conditions.
        if result:
            skip_candidates = set(cfg.else_steps) - context.branch_run_steps
            context.branch_skip_steps.update(skip_candidates)
            context.branch_run_steps.update(cfg.then_steps)
            context.branch_skip_steps -= set(cfg.then_steps)
        else:
            skip_candidates = set(cfg.then_steps) - context.branch_run_steps
            context.branch_skip_steps.update(skip_candidates)
            context.branch_run_steps.update(cfg.else_steps)
            context.branch_skip_steps -= set(cfg.else_steps)

        duration = time.monotonic() - started_at
        return StepResult(
            step_id=step.id,
            output={"condition": result, "expression": cfg.expression},
            cost_usd=0.0,
            duration_seconds=duration,
            status="completed",
        )
    except Exception as e:
        duration = time.monotonic() - started_at
        return StepResult(
            step_id=step.id,
            status="failed",
            error=str(e),
            duration_seconds=duration,
        )


async def _execute_classify_step(
    step: StepDefinition,
    context: RunContext,
    storage: StorageBackend,
) -> StepResult:
    """Execute a classify step - LLM-based routing to branches."""
    import time

    import httpx

    from sandcastle.engine.providers import get_api_key, resolve_base_url, resolve_model

    started_at = time.monotonic()
    cfg = step.classify_config
    if not cfg:
        return StepResult(step_id=step.id, status="failed", error="Missing classify_config")

    try:
        input_text = resolve_templates(cfg.input, context, step.depends_on)
        input_text = await resolve_storage_refs(input_text, storage, context)

        model_info = resolve_model(cfg.model)
        _enforce_data_residency(model_info)
        api_key = get_api_key(model_info)

        categories_str = ", ".join(cfg.categories)
        classify_prompt = (
            "Classify the following text into exactly one"
            f" of these categories: {categories_str}\n\n"
            f"Text: {input_text}\n\n"
            f"Respond with ONLY the category name, nothing else."
        )

        if model_info.provider == "claude":
            async with httpx.AsyncClient(timeout=step.timeout) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": model_info.api_model_id,
                        "max_tokens": 64,
                        "messages": [{"role": "user", "content": classify_prompt}],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                raw_category = data["content"][0]["text"].strip().lower()
                usage = data.get("usage", {})
                in_tok = usage.get("input_tokens", 0)
                out_tok = usage.get("output_tokens", 0)
        else:
            base_url = resolve_base_url(model_info)
            async with httpx.AsyncClient(timeout=step.timeout) as client:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "content-type": "application/json",
                    },
                    json={
                        "model": model_info.api_model_id,
                        "max_tokens": 64,
                        "messages": [{"role": "user", "content": classify_prompt}],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                raw_category = data["choices"][0]["message"]["content"].strip().lower()
                usage = data.get("usage", {})
                in_tok = usage.get("prompt_tokens", 0)
                out_tok = usage.get("completion_tokens", 0)

        cost = _safe_cost(in_tok, out_tok, model_info.input_price_per_m, model_info.output_price_per_m)

        # Match to category list: exact first, then guarded substring.
        matched = None
        for cat in cfg.categories:
            if cat.lower() == raw_category:
                matched = cat
                break
        if not matched:
            # Substring containment with a length-ratio guard to avoid
            # short fragments matching wrong categories (e.g. "pos"
            # spuriously matching "position").  The shorter string must
            # be >= 60% of the longer one.
            best_cat = None
            best_ratio = 0.0
            for cat in cfg.categories:
                cat_lower = cat.lower()
                if cat_lower in raw_category or raw_category in cat_lower:
                    shorter = min(len(cat_lower), len(raw_category))
                    longer = max(len(cat_lower), len(raw_category))
                    ratio = shorter / longer if longer > 0 else 0.0
                    if ratio >= 0.6 and ratio > best_ratio:
                        best_ratio = ratio
                        best_cat = cat
            matched = best_cat
        if not matched:
            if not cfg.categories:
                raise ValueError(
                    f"Classify step '{step.id}': no categories defined"
                )
            logger.warning(
                f"Classify step '{step.id}': LLM response '{raw_category}'"
                f" didn't match any category, defaulting to '{cfg.categories[0]}'"
            )
            matched = cfg.categories[0]

        # Mark matched branch steps as run, skip non-matching branches
        matched_steps = set(cfg.branches.get(matched, []))
        context.branch_run_steps.update(matched_steps)
        context.branch_skip_steps -= matched_steps
        for cat, branch_steps in cfg.branches.items():
            if cat != matched:
                skip_candidates = set(branch_steps) - context.branch_run_steps
                context.branch_skip_steps.update(skip_candidates)

        duration = time.monotonic() - started_at
        return StepResult(
            step_id=step.id,
            output={"category": matched, "raw": raw_category},
            cost_usd=cost,
            duration_seconds=duration,
            status="completed",
        )
    except Exception as e:
        duration = time.monotonic() - started_at
        return StepResult(
            step_id=step.id,
            status="failed",
            error=str(e),
            duration_seconds=duration,
        )


async def _execute_loop_step(
    step: StepDefinition,
    context: RunContext,
    sandbox: Any,
    storage: StorageBackend,
    workflow: WorkflowDefinition,
    depth: int,
) -> StepResult:
    """Execute a loop step - iterate over list, run sub-steps for each item."""
    import time

    started_at = time.monotonic()
    cfg = step.loop_config
    if not cfg:
        return StepResult(step_id=step.id, status="failed", error="Missing loop_config")

    try:
        # Resolve the 'over' variable path
        over_path = cfg.over.strip("{}")
        items = resolve_variable(over_path, context)
        if items is _UNRESOLVED:
            items = []
        elif not isinstance(items, list):
            items = [items]

        # Limit iterations
        items = items[: cfg.max_iterations]

        # Validate step_ids only when there are items to iterate over;
        # an empty list with empty step_ids is a valid no-op.
        if items and not cfg.step_ids:
            return StepResult(
                step_id=step.id,
                status="failed",
                error="Loop step_ids must not be empty",
                duration_seconds=time.monotonic() - started_at,
            )

        results = []
        total_cost = 0.0

        for i, item in enumerate(items):
            if await _check_cancel(context.run_id):
                return StepResult(
                    step_id=step.id,
                    output={"status": "cancelled", "completed_iterations": i},
                    cost_usd=total_cost,
                    duration_seconds=time.monotonic() - started_at,
                    status="failed",
                )
            child_context = context.with_item(item, i)

            for sub_step_id in cfg.step_ids:
                try:
                    sub_step = workflow.get_step(sub_step_id)
                except ValueError:
                    continue

                sub_result = await execute_step_with_retry(
                    sub_step,
                    child_context,
                    sandbox,
                    storage,
                )
                child_context.step_outputs[sub_step_id] = sub_result.output
                child_context.step_results[sub_step_id] = sub_result
                total_cost += sub_result.cost_usd

            # Sync child costs back to parent so budget checks see ongoing costs
            context.costs.extend(child_context.costs)
            child_context.costs.clear()

            # Collect last sub-step output as the loop iteration result
            last_step = cfg.step_ids[-1] if cfg.step_ids else None
            iteration_output = child_context.step_outputs.get(last_step) if last_step else item
            results.append(iteration_output)

            # Check 'until' break condition
            if cfg.until:
                eval_names: dict[str, Any] = {
                    "output": iteration_output,
                    "index": i,
                    "True": True,
                    "False": False,
                    "None": None,
                }
                if bool(_safe_eval_expression(cfg.until, eval_names)):
                    break

        duration = time.monotonic() - started_at
        return StepResult(
            step_id=step.id,
            output=results,
            cost_usd=total_cost,
            duration_seconds=duration,
            status="completed",
        )
    except WorkflowPaused:
        # Approval gates inside loops must propagate to the main executor
        raise
    except Exception as e:
        duration = time.monotonic() - started_at
        return StepResult(
            step_id=step.id,
            status="failed",
            error=str(e),
            duration_seconds=duration,
        )


async def _execute_race_step(
    step: StepDefinition,
    context: RunContext,
    sandbox: Any,
    storage: StorageBackend,
    workflow: WorkflowDefinition,
    depth: int,
) -> StepResult:
    """Execute a race step - run branches in parallel, take first valid result."""
    import time

    started_at = time.monotonic()
    cfg = step.race_config
    if not cfg:
        return StepResult(step_id=step.id, status="failed", error="Missing race_config")

    try:
        # Track partial costs per branch so cancelled branches still count
        branch_costs: dict[int, float] = {}

        async def run_branch(branch_idx: int, branch_steps: list[str]) -> dict:
            """Execute a sequence of steps and return the last output."""
            branch_context = RunContext(
                run_id=context.run_id,
                input=dict(context.input),
                step_outputs=dict(context.step_outputs),
                costs=list(context.costs),
                workflow_name=context.workflow_name,
                default_tools=context.default_tools,
            )
            branch_cost = 0.0
            last_output = None
            for sub_step_id in branch_steps:
                try:
                    sub_step = workflow.get_step(sub_step_id)
                except ValueError:
                    continue
                sub_result = await execute_step_with_retry(
                    sub_step,
                    branch_context,
                    sandbox,
                    storage,
                )
                branch_context.step_outputs[sub_step_id] = sub_result.output
                branch_context.step_results[sub_step_id] = sub_result
                branch_cost += sub_result.cost_usd
                # Update shared tracker after each sub-step so partial
                # costs from cancelled branches are not lost
                branch_costs[branch_idx] = branch_cost
                last_output = sub_result.output
                if sub_result.status == "failed":
                    raise RuntimeError(f"Branch step '{sub_step_id}' failed: {sub_result.error}")
            return {"output": last_output, "cost": branch_cost}

        # Run all branches in parallel, cancel remaining after first valid result
        tasks = {
            asyncio.create_task(run_branch(i, branch)): i
            for i, branch in enumerate(cfg.branches)
        }
        pending = set(tasks.keys())
        winning_output = None
        fallback_output = None  # best non-validated result as fallback

        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                try:
                    result = task.result()
                except WorkflowPaused:
                    # Cancel all remaining tasks before propagating
                    for t in pending:
                        t.cancel()
                    if pending:
                        await asyncio.wait(pending)
                    raise
                except Exception:
                    continue
                # Validate if validator expression is set
                if cfg.validator:
                    eval_names: dict[str, Any] = {
                        "output": result["output"],
                        "True": True,
                        "False": False,
                        "None": None,
                    }
                    try:
                        if bool(_safe_eval_expression(cfg.validator, eval_names)):
                            winning_output = result["output"]
                    except Exception:
                        pass
                    if fallback_output is None:
                        fallback_output = result["output"]
                else:
                    # No validator - take first non-error result
                    winning_output = result["output"]

            if winning_output is not None:
                # Cancel remaining tasks
                for t in pending:
                    t.cancel()
                # Wait for cancelled tasks to finish
                if pending:
                    await asyncio.wait(pending)
                pending = set()
                break

        # Accumulate costs from ALL branches (winners, losers, and cancelled)
        total_cost = sum(branch_costs.values())

        if winning_output is None:
            winning_output = fallback_output

        duration = time.monotonic() - started_at
        if winning_output is None:
            return StepResult(
                step_id=step.id,
                status="failed",
                error="All race branches failed",
                cost_usd=total_cost,
                duration_seconds=duration,
            )

        return StepResult(
            step_id=step.id,
            output=winning_output,
            cost_usd=total_cost,
            duration_seconds=duration,
            status="completed",
        )
    except WorkflowPaused:
        # Approval gates inside race branches must propagate to the main executor
        raise
    except Exception as e:
        duration = time.monotonic() - started_at
        return StepResult(
            step_id=step.id,
            status="failed",
            error=str(e),
            duration_seconds=duration,
        )


async def _execute_sensor_step(
    step: StepDefinition,
    context: RunContext,
) -> StepResult:
    """Execute a sensor step - poll URL until condition is met or timeout."""
    import random
    import time

    import httpx

    started_at = time.monotonic()
    cfg = step.sensor_config
    if not cfg:
        return StepResult(step_id=step.id, status="failed", error="Missing sensor_config")

    try:
        url = resolve_templates(cfg.url, context, step.depends_on)

        # SSRF prevention for sensor steps - block private/internal networks
        try:
            import ipaddress as _ipaddress
            import socket as _socket
            from urllib.parse import urlparse as _urlparse

            from sandcastle.webhooks.dispatcher import _BLOCKED_NETWORKS
            _parsed = _urlparse(url)
            if _parsed.scheme not in ("https", "http"):
                return StepResult(
                    step_id=step.id,
                    status="failed",
                    error=f"Sensor step URL must use http(s), got '{_parsed.scheme}'",
                    duration_seconds=time.monotonic() - started_at,
                )
            if _parsed.hostname:
                try:
                    _resolved = _socket.getaddrinfo(_parsed.hostname, _parsed.port or 443)
                    for _, _, _, _, _sockaddr in _resolved:
                        _ip = _ipaddress.ip_address(_sockaddr[0])
                        for _network in _BLOCKED_NETWORKS:
                            if _ip in _network:
                                return StepResult(
                                    step_id=step.id,
                                    status="failed",
                                    error=f"Sensor step URL resolves to blocked network ({_ip})",
                                    duration_seconds=time.monotonic() - started_at,
                                )
                except _socket.gaierror:
                    pass  # DNS resolution failure - let httpx handle it naturally
        except ImportError:
            pass

        headers = {
            k: resolve_templates(v, context, step.depends_on) for k, v in cfg.headers.items()
        }
        deadline = time.monotonic() + cfg.timeout
        current_interval = cfg.check_interval
        max_interval = min(cfg.check_interval * 32, cfg.timeout / 4)

        async with httpx.AsyncClient(timeout=30) as client:
            while time.monotonic() < deadline:
                # Check for cancellation between polls
                if await _check_cancel(context.run_id):
                    duration = time.monotonic() - started_at
                    return StepResult(
                        step_id=step.id,
                        status="failed",
                        error="Run cancelled during sensor polling",
                        duration_seconds=duration,
                    )

                try:
                    resp = await client.request(
                        method=cfg.method.upper(),
                        url=url,
                        headers=headers,
                    )
                    try:
                        response_data = resp.json()
                    except Exception:
                        raw_text = resp.text
                        truncated = len(raw_text) > 5000
                        if truncated:
                            logger.warning(
                                f"HTTP step '{step.id}': Response truncated from"
                                f" {len(raw_text)} to 5000 chars"
                            )
                        response_data = {
                            "status_code": resp.status_code,
                            "text": raw_text[:5000],
                            "_truncated": truncated,
                        }

                    # Evaluate condition safely via simpleeval
                    eval_names: dict[str, Any] = {
                        "response": response_data,
                        "status_code": resp.status_code,
                        "True": True,
                        "False": False,
                        "None": None,
                    }
                    condition_met = bool(_safe_eval_expression(cfg.condition, eval_names))

                    if condition_met:
                        duration = time.monotonic() - started_at
                        return StepResult(
                            step_id=step.id,
                            output=response_data,
                            cost_usd=0.0,
                            duration_seconds=duration,
                            status="completed",
                        )
                except Exception as poll_err:
                    logger.debug(f"Sensor poll error for step '{step.id}': {poll_err}")

                # Exponential backoff with +/- 20% jitter
                jitter = current_interval * random.uniform(-0.2, 0.2)
                await asyncio.sleep(current_interval + jitter)
                current_interval = min(current_interval * 2, max_interval)

        # Timeout reached
        duration = time.monotonic() - started_at
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"Sensor timed out after {cfg.timeout}s",
            duration_seconds=duration,
        )
    except Exception as e:
        duration = time.monotonic() - started_at
        return StepResult(
            step_id=step.id,
            status="failed",
            error=str(e),
            duration_seconds=duration,
        )


async def _execute_gate_step(
    step: StepDefinition,
    context: RunContext,
    storage: StorageBackend,
) -> StepResult:
    """Execute a gate step - iterate through approval strategies."""
    import time

    started_at = time.monotonic()
    cfg = step.gate_config
    if not cfg:
        return StepResult(step_id=step.id, status="failed", error="Missing gate_config")

    try:
        # Collect results from all strategies before returning.
        # All strategies must approve; any rejection short-circuits.
        total_cost = 0.0
        strategy_results: list[dict] = []
        pending_approval_ids: list[str] = []

        for strategy in cfg.strategies:
            strategy_type = strategy.get("type", "")
            strategy_config = strategy.get("config", {})

            if strategy_type == "llm_eval":
                # LLM-based evaluation (similar to classify step pattern)
                import httpx

                from sandcastle.engine.providers import get_api_key, resolve_base_url, resolve_model

                eval_prompt = strategy_config.get(
                    "prompt", "Evaluate this input and respond with 'approved' or 'rejected'."
                )
                eval_input = strategy_config.get("input", "")
                if eval_input:
                    eval_input = resolve_templates(eval_input, context, step.depends_on)
                    eval_prompt = f"{eval_prompt}\n\nInput: {eval_input}"

                model_name = strategy_config.get("model", "haiku")
                model_info = resolve_model(model_name)
                _enforce_data_residency(model_info)
                api_key = get_api_key(model_info)

                if model_info.provider == "claude":
                    async with httpx.AsyncClient(timeout=step.timeout) as client:
                        resp = await client.post(
                            "https://api.anthropic.com/v1/messages",
                            headers={
                                "x-api-key": api_key,
                                "anthropic-version": "2023-06-01",
                                "content-type": "application/json",
                            },
                            json={
                                "model": model_info.api_model_id,
                                "max_tokens": 256,
                                "messages": [{"role": "user", "content": eval_prompt}],
                            },
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        llm_response = data["content"][0]["text"].strip().lower()
                        usage = data.get("usage", {})
                        in_tok = usage.get("input_tokens", 0)
                        out_tok = usage.get("output_tokens", 0)
                else:
                    base_url = resolve_base_url(model_info)
                    async with httpx.AsyncClient(timeout=step.timeout) as client:
                        resp = await client.post(
                            f"{base_url}/chat/completions",
                            headers={
                                "Authorization": f"Bearer {api_key}",
                                "content-type": "application/json",
                            },
                            json={
                                "model": model_info.api_model_id,
                                "max_tokens": 256,
                                "messages": [{"role": "user", "content": eval_prompt}],
                            },
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        llm_response = data["choices"][0]["message"]["content"].strip().lower()
                        usage = data.get("usage", {})
                        in_tok = usage.get("prompt_tokens", 0)
                        out_tok = usage.get("completion_tokens", 0)

                cost = _safe_cost(in_tok, out_tok, model_info.input_price_per_m, model_info.output_price_per_m)
                total_cost += cost
                approved = "approved" in llm_response or "approve" in llm_response

                if not approved:
                    # Immediate rejection - no need to check further strategies
                    duration = time.monotonic() - started_at
                    return StepResult(
                        step_id=step.id,
                        output={
                            "decision": "rejected",
                            "reason": llm_response,
                            "strategy": "llm_eval",
                        },
                        cost_usd=total_cost,
                        duration_seconds=duration,
                        status="completed",
                    )

                strategy_results.append({
                    "decision": "approved",
                    "reason": llm_response,
                    "strategy": "llm_eval",
                })

            elif strategy_type == "human":
                # Create approval request - collect all before pausing
                from sandcastle.models.db import (
                    ApprovalRequest,
                    ApprovalStatus,
                    Run,
                    RunStatus,
                    async_session,
                )

                message = strategy_config.get("message", "Gate requires human approval")
                timeout_hours = strategy_config.get("timeout_hours")
                on_timeout = strategy_config.get("on_timeout", "abort")

                async with async_session() as session:
                    approval = ApprovalRequest(
                        run_id=uuid.UUID(context.run_id),
                        step_id=step.id,
                        status=ApprovalStatus.PENDING,
                        request_data={"gate_strategy": "human"},
                        message=message,
                        timeout_at=None,
                        on_timeout=on_timeout,
                        allow_edit=False,
                    )
                    if timeout_hours:
                        approval.timeout_at = datetime.now(timezone.utc) + timedelta(
                            hours=timeout_hours
                        )
                    session.add(approval)
                    run = await session.get(Run, uuid.UUID(context.run_id))
                    if run:
                        run.status = RunStatus.AWAITING_APPROVAL
                    await session.commit()
                    await session.refresh(approval)
                    pending_approval_ids.append(str(approval.id))

            elif strategy_type == "timeout":
                # Auto-approve or reject after a delay
                delay = strategy_config.get("seconds", 60)
                action = strategy_config.get("action", "approve")
                await asyncio.sleep(delay)

                if action != "approve":
                    # Immediate rejection
                    duration = time.monotonic() - started_at
                    return StepResult(
                        step_id=step.id,
                        output={
                            "decision": "rejected",
                            "reason": f"Auto-{action} after {delay}s timeout",
                            "strategy": "timeout",
                        },
                        cost_usd=total_cost,
                        duration_seconds=duration,
                        status="completed",
                    )

                strategy_results.append({
                    "decision": "approved",
                    "reason": f"Auto-{action} after {delay}s timeout",
                    "strategy": "timeout",
                })

        # If any human approvals are pending, pause the workflow.
        # All approval requests have been created above.
        if pending_approval_ids:
            raise WorkflowPaused(
                approval_id=pending_approval_ids[0],
                run_id=context.run_id,
            )

        # All non-human strategies approved
        if strategy_results:
            duration = time.monotonic() - started_at
            return StepResult(
                step_id=step.id,
                output={
                    "decision": "approved",
                    "strategies": strategy_results,
                },
                cost_usd=total_cost,
                duration_seconds=duration,
                status="completed",
            )

        # No strategy matched
        duration = time.monotonic() - started_at
        return StepResult(
            step_id=step.id,
            status="failed",
            error="No gate strategy matched or all strategies failed",
            duration_seconds=duration,
        )
    except WorkflowPaused:
        raise
    except Exception as e:
        duration = time.monotonic() - started_at
        return StepResult(
            step_id=step.id,
            status="failed",
            error=str(e),
            duration_seconds=duration,
        )


async def _execute_transform_step(
    step: StepDefinition,
    context: RunContext,
) -> StepResult:
    """Execute a template-based data transformation step - $0 cost."""
    import time

    started_at = time.monotonic()
    cfg = step.transform_config
    if not cfg:
        return StepResult(step_id=step.id, status="failed", error="Missing transform_config")

    try:
        template = cfg.template

        # Guard against oversized templates
        if len(template) > _MAX_TEMPLATE_SIZE:
            return StepResult(
                step_id=step.id,
                status="failed",
                error=(
                    f"Transform template too large ({len(template)} bytes, "
                    f"max {_MAX_TEMPLATE_SIZE})"
                ),
                duration_seconds=time.monotonic() - started_at,
            )

        # Resolve {var} template variables
        rendered = resolve_templates(template, context, step.depends_on)

        # Support basic Jinja2-like {{ var }} syntax (safe regex-based
        # resolution; does NOT use jinja2.Environment to prevent SSTI).
        def _jinja_replace(match: re.Match) -> str:
            expr = match.group(1).strip()
            # Handle tojson filter
            if "|" in expr:
                parts = expr.split("|", 1)
                var_path = parts[0].strip()
                filter_name = parts[1].strip()
                value = resolve_variable(var_path, context)
                if value is _UNRESOLVED:
                    return ""
                if filter_name == "tojson":
                    return json.dumps(value) if value is not None else "null"
                return str(value) if value is not None else ""
            else:
                value = resolve_variable(expr, context)
                if value is _UNRESOLVED:
                    return ""
                if isinstance(value, (dict, list)):
                    return json.dumps(value)
                return str(value) if value is not None else ""

        rendered = re.sub(r"\{\{(.+?)\}\}", _jinja_replace, rendered)

        # Try to parse as JSON (preserves all JSON types: dict, list, int, float, bool, null)
        output: Any = rendered
        try:
            output = json.loads(rendered)
        except (json.JSONDecodeError, ValueError):
            pass

        duration = time.monotonic() - started_at
        return StepResult(
            step_id=step.id,
            output=output,
            cost_usd=0.0,
            duration_seconds=duration,
            status="completed",
        )
    except Exception as e:
        duration = time.monotonic() - started_at
        return StepResult(
            step_id=step.id,
            status="failed",
            error=str(e),
            duration_seconds=duration,
        )


async def _execute_notify_step(
    step: StepDefinition,
    context: RunContext,
) -> StepResult:
    """Execute a notification step - resolve message and log notification. $0 cost."""
    import time

    started_at = time.monotonic()
    cfg = step.notify_config
    if not cfg:
        return StepResult(step_id=step.id, status="failed", error="Missing notify_config")

    try:
        message = resolve_templates(cfg.message, context, step.depends_on)
        # Channel is a target identifier (Slack channel, email, webhook URL)
        # and should NOT have dependency context auto-injected into it.
        channel = resolve_templates(cfg.channel, context) if cfg.channel else ""

        logger.info(
            "Notify step '%s': service=%s channel=%s message=%s",
            step.id,
            cfg.service,
            channel,
            message[:200],
        )

        output = {
            "service": cfg.service,
            "channel": channel,
            "message": message,
            "status": "logged",
            "note": "Notification logged only - no delivery connector configured",
        }

        duration = time.monotonic() - started_at
        return StepResult(
            step_id=step.id,
            output=output,
            cost_usd=0.0,
            duration_seconds=duration,
            status="completed",
        )
    except Exception as e:
        duration = time.monotonic() - started_at
        return StepResult(
            step_id=step.id,
            status="failed",
            error=str(e),
            duration_seconds=duration,
        )


async def _execute_delegate_step(
    step: StepDefinition,
    context: RunContext,
    storage: StorageBackend,
    depth: int = 0,
) -> StepResult:
    """Execute a delegate step - run another workflow as a sub-workflow."""
    import time

    started_at = time.monotonic()
    cfg = step.delegate_config
    if not cfg:
        return StepResult(step_id=step.id, status="failed", error="Missing delegate_config")

    # Early depth check - reject before loading/parsing sub-workflow YAML
    try:
        from sandcastle.config import settings as _delegate_settings

        if depth + 1 >= _delegate_settings.max_workflow_depth:
            duration = time.monotonic() - started_at
            return StepResult(
                step_id=step.id,
                status="failed",
                error=(
                    f"Max workflow depth ({_delegate_settings.max_workflow_depth}) "
                    f"would be exceeded by delegate step"
                ),
                duration_seconds=duration,
            )
    except Exception:
        pass  # If settings unavailable, let execute_workflow handle it

    try:
        task_description = resolve_templates(
            cfg.task_description,
            context,
            step.depends_on,
        )

        # Try to load and execute the target workflow
        try:
            from pathlib import Path

            from sandcastle.config import settings
            from sandcastle.engine.dag import build_plan
            from sandcastle.engine.dag import parse as parse_workflow

            workflows_dir = (
                Path(settings.workflows_dir)
                if hasattr(settings, "workflows_dir")
                else Path("workflows")
            ).resolve()

            # Resolve dynamic workflow name via template variables
            raw_wf_name = cfg.workflow
            if "{" in raw_wf_name:
                wf_name = resolve_templates(raw_wf_name, context, step.depends_on)
            else:
                wf_name = raw_wf_name

            # Path traversal guard
            if ".." in wf_name or "/" in wf_name or "\\" in wf_name:
                raise ValueError(
                    f"Delegate workflow name '{wf_name}'"
                    " contains path traversal characters"
                )

            wf_path = (workflows_dir / f"{wf_name}.yaml").resolve()
            if not str(wf_path).startswith(str(workflows_dir)):
                raise ValueError("Delegate workflow path escapes workflows directory")

            if wf_path.exists():
                sub_wf = parse_workflow(str(wf_path))
                sub_plan = build_plan(sub_wf)
                sub_run_id = str(uuid.uuid4())
                sub_context = RunContext(
                    run_id=sub_run_id,
                    input={**context.input, "task_description": task_description},
                    step_outputs={},
                    workflow_name=wf_name,
                )

                # Emit progress: sub-run created and delegated
                event_bus.publish(
                    "step.progress",
                    {
                        "run_id": context.run_id,
                        "step_id": step.id,
                        "step_name": step.id,
                        "sub_run_id": sub_run_id,
                        "status": "delegated",
                        "workflow": wf_name,
                        "depth": depth,
                    },
                )

                sub_result = await execute_workflow(
                    workflow=sub_wf,
                    plan=sub_plan,
                    input_data=sub_context.input,
                    storage=storage,
                    max_cost_usd=context.max_cost_usd,
                    depth=depth + 1,
                    admin_trusted=context.admin_trusted,
                    tenant_id=context.tenant_id,
                )

                # Emit progress: sub-run finished
                event_bus.publish(
                    "step.progress",
                    {
                        "run_id": context.run_id,
                        "step_id": step.id,
                        "step_name": step.id,
                        "sub_run_id": sub_run_id,
                        "status": "sub_completed",
                        "workflow": wf_name,
                        "sub_status": sub_result.status,
                        "depth": depth,
                    },
                )

                duration = time.monotonic() - started_at
                if sub_result.status != "completed":
                    sub_error = sub_result.error or "unknown error"
                    return StepResult(
                        step_id=step.id,
                        output=sub_result.outputs,
                        cost_usd=sub_result.total_cost_usd,
                        duration_seconds=duration,
                        status="failed",
                        error=(
                            f"Delegate sub-workflow '{wf_name}' (sub_run={sub_run_id}, "
                            f"depth={depth + 1}) failed: {sub_error}"
                        ),
                    )
                return StepResult(
                    step_id=step.id,
                    output=sub_result.outputs,
                    cost_usd=sub_result.total_cost_usd,
                    duration_seconds=duration,
                    status="completed",
                    error=None,
                )
        except Exception as exc:
            logger.warning("Delegate step '%s' failed to execute sub-workflow: %s", step.id, exc)

        # Fallback: sub-workflow not found or failed to load
        output = {
            "workflow": cfg.workflow,
            "task_description": task_description,
            "status": "delegated",
        }

        duration = time.monotonic() - started_at
        return StepResult(
            step_id=step.id,
            output=output,
            cost_usd=0.0,
            duration_seconds=duration,
            status="failed",
            error=(
                f"Delegate sub-workflow '{cfg.workflow}' not found or failed to load "
                f"(depth={depth + 1})"
            ),
        )
    except Exception as e:
        duration = time.monotonic() - started_at
        return StepResult(
            step_id=step.id,
            status="failed",
            error=(
                f"Delegate step failed (workflow='{cfg.workflow if cfg else 'unknown'}', "
                f"depth={depth + 1}): {e}"
            ),
            duration_seconds=duration,
        )


async def _execute_browser_step(
    step: StepDefinition,
    context: RunContext,
    sandbox: Any,
    storage: StorageBackend,
) -> StepResult:
    """Execute a browser automation step.

    Supports five modes:
    - playwright: Agent writes and executes Playwright scripts inside the
      sandbox via the standard agent loop. The step prompt is augmented with
      browser tool instructions, viewport config, and start URL context.
    - computer_use: Screenshot-action loop via the Anthropic computer_use
      beta API. Takes screenshots of a headless browser running inside the
      sandbox, sends them to Claude, and executes returned mouse/keyboard
      actions. Loops until the task is complete or the timeout is reached.
    - dom: Uses the accessibility tree instead of screenshots for faster and
      cheaper data extraction from known page layouts.
    - lightpanda: Launches the LightPanda browser (fast, low-memory) via CDP
      and connects Playwright to it. Follows the same flow as dom mode.
    - browserbase: Uses Browserbase cloud browser infrastructure. Creates a
      managed session via the Browserbase API and connects Playwright to it.
    """
    import os
    import time

    started_at = time.monotonic()
    cfg = step.browser_config
    if not cfg:
        return StepResult(
            step_id=step.id,
            status="failed",
            error="Missing browser_config",
        )

    prompt = resolve_templates(step.prompt, context, step.depends_on)
    prompt = await resolve_storage_refs(prompt, storage, context)

    # Resolve credentials from environment if configured
    credentials_json = None
    if cfg.credentials_env:
        credentials_json = os.environ.get(cfg.credentials_env)

    # Feature F: execution replay - collect screenshots across modes
    replay_screenshots: list[dict] = []

    try:
        if cfg.mode == "dom":
            result = await _browser_dom_mode(
                step,
                cfg,
                sandbox,
                context.run_id,
                context.input,
                context=context,
                storage=storage,
            )
        elif cfg.mode == "computer_use":
            result = await _browser_computer_use_mode(
                step,
                cfg,
                prompt,
                credentials_json,
                context,
                sandbox,
                storage,
                started_at,
                replay_screenshots,
            )
        elif cfg.mode == "playwright":
            result = await _browser_playwright_mode(
                step,
                cfg,
                prompt,
                credentials_json,
                context,
                sandbox,
                storage,
                started_at,
            )
        elif cfg.mode == "lightpanda":
            result = await _browser_lightpanda_mode(
                step,
                cfg,
                prompt,
                credentials_json,
                context,
                sandbox,
                storage,
                started_at,
            )
        elif cfg.mode == "browserbase":
            result = await _browser_browserbase_mode(
                step,
                cfg,
                prompt,
                credentials_json,
                context,
                sandbox,
                storage,
                started_at,
            )
        else:
            return StepResult(
                step_id=step.id,
                status="failed",
                error=f"Unknown browser mode: {cfg.mode}",
                duration_seconds=time.monotonic() - started_at,
            )

        # Feature F: attach replay data to the result output
        if replay_screenshots and result.status == "completed":
            output = result.output
            if isinstance(output, dict):
                output["replay"] = replay_screenshots
                output["replay_count"] = len(replay_screenshots)
            else:
                output = {
                    "result": output,
                    "replay": replay_screenshots,
                    "replay_count": len(replay_screenshots),
                }
            result = StepResult(
                step_id=result.step_id,
                parallel_index=result.parallel_index,
                output=output,
                cost_usd=result.cost_usd,
                duration_seconds=result.duration_seconds,
                status=result.status,
                attempt=result.attempt,
            )

        return result
    except Exception as e:
        duration = time.monotonic() - started_at
        logger.error(f"Browser step '{step.id}' failed: {e}")
        return StepResult(
            step_id=step.id,
            status="failed",
            error=str(e),
            duration_seconds=duration,
        )


async def _browser_playwright_mode(
    step: StepDefinition,
    cfg: Any,
    prompt: str,
    credentials_json: str | None,
    context: RunContext,
    sandbox: Any,
    storage: StorageBackend,
    started_at: float,
) -> StepResult:
    """Playwright mode: augment the step prompt with browser tool instructions
    and delegate to the standard sandbox agent execution.
    """
    import time

    # Build augmented prompt with browser context
    browser_instructions = (
        "You have access to a headless Chromium browser via Playwright.\n"
        "Playwright and Chromium are pre-installed in the sandbox.\n\n"
        "## Browser Tools\n"
        "Write and execute Node.js scripts using Playwright to automate "
        "the browser. Use the Bash tool to run scripts like:\n\n"
        "```bash\n"
        'node -e "\n'
        "const { chromium } = require('playwright');\n"
        "(async () => {\n"
        f"  const browser = await chromium.launch("
        f"{{ headless: {'true' if cfg.headless else 'false'} }});\n"
        f"  const page = await browser.newPage("
        f"{{ viewport: {{ width: {cfg.viewport_width}, "
        f"height: {cfg.viewport_height} }} }});\n"
    )

    if cfg.start_url:
        start_url = resolve_templates(
            cfg.start_url,
            context,
            step.depends_on,
        )
        try:
            start_url = _validate_browser_url(start_url)
        except ValueError as e:
            return StepResult(
                step_id=step.id,
                status="failed",
                error=f"Invalid browser URL: {e}",
                duration_seconds=time.monotonic() - started_at,
            )
        browser_instructions += f"  await page.goto('{_escape_js_string(start_url)}');\n"

    browser_instructions += (
        "  // ... your automation code here ...\n"
        "  await browser.close();\n"
        "})();\n"
        '"\n'
        "```\n\n"
        "## Available Playwright Actions\n"
        "- **Navigate:** `await page.goto(url)`\n"
        "- **Click:** `await page.click(selector)` or "
        "`await page.locator(text).click()`\n"
        "- **Type:** `await page.fill(selector, text)` or "
        "`await page.type(selector, text)`\n"
        "- **Screenshot:** "
        "`await page.screenshot({ path: 'screenshot.png' })`\n"
        "- **Wait:** `await page.waitForSelector(selector)` or "
        "`await page.waitForTimeout(ms)`\n"
        "- **Get text:** `await page.textContent(selector)` or "
        "`await page.innerText(selector)`\n"
        "- **Evaluate:** "
        "`await page.evaluate(() => document.title)`\n"
        "- **Select:** `await page.selectOption(selector, value)`\n"
        "- **Check/Uncheck:** `await page.check(selector)` / "
        "`await page.uncheck(selector)`\n"
        "- **Press key:** `await page.keyboard.press('Enter')`\n\n"
        f"Wait {cfg.wait_after_action}s between major actions for page "
        "stability.\n"
    )

    if credentials_json:
        browser_instructions += (
            "\n## Credentials\n"
            "Credentials are available as environment variables in the sandbox. "
            "Use process.env.VARIABLE_NAME to access them. "
            f"Available variables: {', '.join(credentials_json.split(','))}\n"
            "Use these for any login forms or authentication.\n"
        )

    if cfg.screenshot_on_error:
        browser_instructions += (
            "\n## Error Handling\n"
            "If an action fails, take a screenshot for debugging:\n"
            "`await page.screenshot({ path: 'error-screenshot.png' })`\n"
        )

    augmented_prompt = f"{browser_instructions}\n## Task\n{prompt}\n"

    # Feature A: inject cached action hints for self-healing selectors
    cached = await _get_cached_actions(cfg.start_url, step.prompt)
    if cached:
        cache_hint = "\n\nPreviously successful actions for similar pages:\n"
        for act in cached[:10]:
            cache_hint += (
                f"  - {act.get('action', 'unknown')}: selector={act.get('selector', 'N/A')}\n"
            )
        augmented_prompt += (
            cache_hint + "\nTry these selectors first. If they fail, find the correct elements.\n"
        )

    # Install playwright in sandbox (idempotent)
    try:
        runtime = get_sandshore_runtime()
        await runtime.sandbox_exec(
            sandbox,
            "bash",
            [
                "-c",
                "command -v npx && "
                "npx playwright install chromium 2>/dev/null || "
                "(npm install -g playwright && "
                "npx playwright install chromium)",
            ],
            timeout=60,
        )
    except Exception as install_err:
        logger.warning(f"Playwright install warning (may already be present): {install_err}")

    # Create a modified step with the browser-augmented prompt and
    # delegate to the standard agent execution path.
    from sandcastle.engine.dag import StepDefinition as _StepDef

    browser_step = _StepDef(
        id=step.id,
        prompt=augmented_prompt,
        depends_on=step.depends_on,
        model=step.model,
        max_turns=step.max_turns,
        timeout=max(step.timeout, cfg.timeout_seconds),
        output_schema=step.output_schema,
        retry=step.retry,
        fallback=step.fallback,
        type="standard",
        tools=step.tools,
    )

    result = await execute_step_with_retry(
        browser_step,
        context,
        sandbox,
        storage,
    )
    result.step_id = step.id
    return result


async def _browser_dom_mode(
    step: StepDefinition,
    cfg: Any,
    sandbox: Any,
    run_id: str,
    input_data: dict,
    context: RunContext | None = None,
    storage: StorageBackend | None = None,
) -> StepResult:
    """DOM extraction mode - uses accessibility tree instead of screenshots.

    Faster and cheaper than vision-based modes. Best for structured data
    extraction from known page layouts.
    """

    logger.info("Browser DOM mode: %s -> %s", step.id, cfg.start_url)

    # Validate URL before proceeding
    if not cfg.start_url:
        return StepResult(
            step_id=step.id,
            status="failed",
            error="DOM mode requires a start_url",
        )

    try:
        validated_url = _validate_browser_url(cfg.start_url)
    except ValueError as e:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"Invalid browser URL: {e}",
        )

    runtime = get_sandshore_runtime()

    # Install playwright in sandbox
    try:
        await runtime.sandbox_exec(
            sandbox,
            "bash",
            [
                "-c",
                "command -v npx && "
                "npx playwright install chromium 2>/dev/null || "
                "(npm install -g playwright && "
                "npx playwright install chromium)",
            ],
            timeout=60,
        )
    except Exception as install_err:
        logger.warning(f"Playwright install warning (may already be present): {install_err}")

    # Navigate to URL
    safe_url = _escape_js_string(validated_url)
    nav_script = (
        "const { chromium } = require('playwright');\n"
        "(async () => {\n"
        "  const browser = await chromium.launch({ headless: true });\n"
        f"  const page = await browser.newPage({{ viewport: "
        f"{{ width: {cfg.viewport_width}, height: {cfg.viewport_height} }} }});\n"
        f"  await page.goto('{safe_url}');\n"
        "  // Get interactive elements\n"
        "  const elements = await page.evaluate(() => {\n"
        "    const items = [];\n"
        "    const selectors = 'a, button, input, select, "
        "textarea, [role=\"button\"], [onclick]';\n"
        "    document.querySelectorAll(selectors).forEach((el, i) => {\n"
        "      items.push({\n"
        "        index: i,\n"
        "        tag: el.tagName.toLowerCase(),\n"
        "        type: el.getAttribute('type') || '',\n"
        "        text: (el.textContent || '').trim().slice(0, 100),\n"
        "        placeholder: el.getAttribute('placeholder') || '',\n"
        "        name: el.getAttribute('name') || '',\n"
        "        href: el.getAttribute('href') || '',\n"
        "        role: el.getAttribute('role') || '',\n"
        "      });\n"
        "    });\n"
        "    return items;\n"
        "  });\n"
        "  // Get page info\n"
        "  const title = await page.title();\n"
        "  const url = page.url();\n"
        "  const text = await page.evaluate(() => document.body.innerText.slice(0, 5000));\n"
        "  console.log(JSON.stringify({\n"
        "    elements: elements,\n"
        "    page_info: { title: title, url: url, body_text: text }\n"
        "  }));\n"
        "  await browser.close();\n"
        "})();\n"
    )

    try:
        result = await runtime.sandbox_exec(
            sandbox,
            "node",
            ["-e", nav_script],
            timeout=30,
        )
        dom_output = result.get("stdout", "") if isinstance(result, dict) else str(result)
    except Exception as e:
        dom_output = f"DOM extraction failed: {e}"

    # Build context for the LLM
    dom_context = f"Page: {cfg.start_url}\n{dom_output}"

    # Build augmented prompt
    augmented_prompt = (
        "You are a browser automation agent working in DOM mode. "
        "You can interact with elements by their index number or CSS selector.\n\n"
        "Available tools:\n"
        "- click(selector) - click an element\n"
        "- type_text(selector, text) - type into an element\n"
        "- select_option(selector, value) - select dropdown option\n"
        "- get_text(selector) - get element text\n"
        "- navigate(url) - go to a URL\n\n"
        f"Current page state:\n{dom_context}\n\n"
        f"Task: {step.prompt}"
    )

    if cfg.output_schema:
        augmented_prompt += (
            f"\n\nExtract data matching this schema:\n"
            f"{json.dumps(cfg.output_schema, indent=2)}\n"
            f"Return the extracted data as valid JSON."
        )

    # Execute as a standard LLM step with browser context
    dom_step = StepDefinition(
        id=step.id,
        prompt=augmented_prompt,
        depends_on=step.depends_on,
        model=step.model or "sonnet",
        max_turns=step.max_turns or 5,
        timeout=max(step.timeout, cfg.timeout_seconds),
        output_schema=step.output_schema,
        type="standard",
    )

    # Use the standard sandbox execution path
    ctx = (
        context
        if context is not None
        else RunContext(
            run_id=run_id,
            input=input_data,
        )
    )

    # Use provided storage or create a minimal local storage
    if storage is None:
        from sandcastle.engine.storage import LocalStorage

        storage = LocalStorage("/tmp/sandcastle_dom_storage")

    result = await execute_step_with_retry(
        dom_step,
        ctx,
        sandbox,
        storage,
    )
    result.step_id = step.id
    return result


async def _browser_computer_use_mode(
    step: StepDefinition,
    cfg: Any,
    prompt: str,
    credentials_json: str | None,
    context: RunContext,
    sandbox: Any,
    storage: StorageBackend,
    started_at: float,
    replay_screenshots: list[dict] | None = None,
) -> StepResult:
    """Computer use mode: screenshot-action loop via Anthropic API.

    Launches a headless browser in the sandbox, takes screenshots, sends
    them to Claude with the computer_use tool, and executes returned
    mouse/keyboard actions until task completion or timeout.
    """
    import time

    import httpx

    from sandcastle.engine.providers import get_api_key, resolve_base_url, resolve_model

    if replay_screenshots is None:
        replay_screenshots = []

    model_info = resolve_model(step.model)
    _enforce_data_residency(model_info)
    api_key = get_api_key(model_info)

    runtime = get_sandshore_runtime()
    deadline = time.monotonic() + cfg.timeout_seconds
    total_cost = 0.0
    action_count = 0
    max_actions = 200  # Safety limit
    iteration = 0

    # Resolve and validate start URL
    start_url = cfg.start_url
    if start_url:
        start_url = resolve_templates(start_url, context, step.depends_on)
        try:
            start_url = _validate_browser_url(start_url)
        except ValueError as e:
            return StepResult(
                step_id=step.id,
                status="failed",
                error=f"Invalid browser URL: {e}",
                duration_seconds=time.monotonic() - started_at,
            )

    # Build and run browser launch script in sandbox
    safe_start_url = _escape_js_string(start_url) if start_url else ""
    launch_script = (
        "const { chromium } = require('playwright');\n"
        "const fs = require('fs');\n"
        "(async () => {\n"
        "  const browser = await chromium.launch({ headless: true });\n"
        f"  const page = await browser.newPage({{ viewport: "
        f"{{ width: {cfg.viewport_width}, "
        f"height: {cfg.viewport_height} }} }});\n"
    )
    if start_url:
        launch_script += f"  await page.goto('{safe_start_url}');\n"
    launch_script += (
        "  await page.screenshot({ path: '/tmp/screenshot.png' });\n"
        "  console.log('BROWSER_READY');\n"
        "  // Keep browser alive for actions\n"
        "  global._page = page;\n"
        "  global._browser = browser;\n"
        "})();\n"
    )

    # Install playwright and launch browser
    await runtime.sandbox_exec(
        sandbox,
        "bash",
        [
            "-c",
            "command -v npx && "
            "npx playwright install chromium 2>/dev/null || "
            "(npm install -g playwright && "
            "npx playwright install chromium)",
        ],
        timeout=60,
    )

    await runtime.sandbox_exec(
        sandbox,
        "bash",
        [
            "-c",
            f"cat > /tmp/browser_launch.js << 'SCRIPT_EOF'\n"
            f"{launch_script}\nSCRIPT_EOF\nnode /tmp/browser_launch.js",
        ],
        timeout=30,
    )

    # Build conversation for computer_use loop
    task_prompt = f"You are controlling a browser to complete a task.\n\nTask: {prompt}"
    if credentials_json:
        task_prompt += (
            "\n\nCredentials are available as environment variables in the sandbox. "
            "Use process.env.VARIABLE_NAME to access them. "
            f"Available variables: {', '.join(credentials_json.split(','))}"
        )

    messages: list[dict] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": task_prompt},
            ],
        }
    ]

    # Take initial screenshot
    screenshot_data = await _take_sandbox_screenshot(runtime, sandbox)
    if screenshot_data:
        messages[0]["content"].append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": screenshot_data,
                },
            }
        )

    last_result = None

    while time.monotonic() < deadline and action_count < max_actions:
        iteration += 1
        # Call Claude with computer_use tool
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "anthropic-beta": "computer-use-2025-01-24",
                        "content-type": "application/json",
                    },
                    json={
                        "model": model_info.api_model_id,
                        "max_tokens": 4096,
                        "system": (
                            "You are a browser automation agent. Use the "
                            "computer tool to interact with the browser. "
                            "When the task is complete, respond with a "
                            "text block containing ONLY the result."
                        ),
                        "tools": [
                            {
                                "type": "computer_20250124",
                                "name": "computer",
                                "display_width_px": cfg.viewport_width,
                                "display_height_px": cfg.viewport_height,
                                "display_number": 1,
                            }
                        ],
                        "messages": messages,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as api_err:
            logger.error(f"Computer use API call failed: {api_err}")
            break

        # Track cost
        usage = data.get("usage", {})
        in_tok = usage.get("input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        total_cost += _safe_cost(in_tok, out_tok, model_info.input_price_per_m, model_info.output_price_per_m)

        # Process response
        content_blocks = data.get("content", [])
        stop_reason = data.get("stop_reason", "")

        # Feature E: CAPTCHA detection in AI response
        for block in content_blocks:
            if block.get("type") == "text":
                text_lower = block.get("text", "").lower()
                if any(
                    kw in text_lower
                    for kw in [
                        "captcha",
                        "recaptcha",
                        "hcaptcha",
                        "verify you're human",
                        "turnstile",
                    ]
                ):
                    if cfg.captcha_strategy == "pause":
                        logger.warning(
                            "CAPTCHA detected in step %s - pausing for human intervention",
                            step.id,
                        )
                        return StepResult(
                            step_id=step.id,
                            output={
                                "status": "captcha_detected",
                                "message": "CAPTCHA detected. Human intervention required.",
                                "iterations": iteration,
                                "captcha_type": "detected_by_ai",
                                "requires_approval": True,
                            },
                            cost_usd=total_cost,
                            duration_seconds=time.monotonic() - started_at,
                            status="completed",
                        )
                    elif cfg.captcha_strategy == "fail":
                        return StepResult(
                            step_id=step.id,
                            output={
                                "status": "failed",
                                "message": "CAPTCHA detected and captcha_strategy is 'fail'.",
                                "iterations": iteration,
                            },
                            cost_usd=total_cost,
                            duration_seconds=time.monotonic() - started_at,
                            status="failed",
                            error="CAPTCHA detected and captcha_strategy is 'fail'.",
                        )
                    # "skip" strategy: continue and hope it resolves
                    break

        has_tool_use = any(b.get("type") == "tool_use" for b in content_blocks)
        if stop_reason == "end_turn" and not has_tool_use:
            text_parts = [b["text"] for b in content_blocks if b.get("type") == "text"]
            last_result = "\n".join(text_parts) if text_parts else "Task completed"
            break

        # Process tool calls
        assistant_content = content_blocks
        tool_results = []

        for block in content_blocks:
            if block.get("type") != "tool_use":
                continue

            tool_input = block.get("input", {})
            action_type = tool_input.get("action", "")
            action_count += 1

            action_script = _build_computer_use_action_script(
                action_type,
                tool_input,
                cfg,
            )

            try:
                exec_result = await runtime.sandbox_exec(
                    sandbox,
                    "node",
                    ["-e", action_script],
                    timeout=15,
                )
                action_output = (
                    exec_result.get("stdout", "")
                    if isinstance(exec_result, dict)
                    else str(exec_result)
                )
            except Exception as exec_err:
                action_output = f"Action failed: {exec_err}"

            await asyncio.sleep(cfg.wait_after_action)

            # Take screenshot after action
            screenshot_data = await _take_sandbox_screenshot(
                runtime,
                sandbox,
            )

            tool_result_content: list[dict] = []
            if action_output:
                tool_result_content.append(
                    {
                        "type": "text",
                        "text": action_output[:2000],
                    }
                )
            if screenshot_data:
                tool_result_content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": screenshot_data,
                        },
                    }
                )
            if not tool_result_content:
                tool_result_content.append(
                    {
                        "type": "text",
                        "text": "Action executed",
                    }
                )

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": tool_result_content,
                }
            )

        messages.append(
            {
                "role": "assistant",
                "content": assistant_content,
            }
        )
        messages.append({"role": "user", "content": tool_results})

        # Feature B: Post-action validation - store screenshots for replay
        if cfg.capture_screenshots or cfg.screenshot_on_error:
            try:
                validation_screenshot = await _take_sandbox_screenshot(
                    runtime,
                    sandbox,
                )
                if validation_screenshot:
                    # Store for execution replay
                    if cfg.capture_screenshots:
                        replay_screenshots.append(
                            {
                                "iteration": iteration,
                                "action": action_type if tool_results else "unknown",
                                "screenshot_b64": validation_screenshot,
                            }
                        )
            except Exception:
                pass

    duration = time.monotonic() - started_at

    timed_out = last_result is None
    if last_result is None:
        if action_count >= max_actions:
            last_result = f"Browser automation stopped after {max_actions} actions (safety limit)"
        else:
            last_result = f"Browser automation timed out after {cfg.timeout_seconds}s"

    # Try to parse result as JSON
    output: Any = last_result
    try:
        parsed = json.loads(last_result)
        if isinstance(parsed, (dict, list)):
            output = parsed
    except (json.JSONDecodeError, ValueError):
        pass

    # Timeout or action limit exceeded -> report as failed
    if timed_out:
        return StepResult(
            step_id=step.id,
            output=output,
            cost_usd=total_cost,
            duration_seconds=duration,
            status="failed",
            error=str(last_result),
        )

    return StepResult(
        step_id=step.id,
        output=output,
        cost_usd=total_cost,
        duration_seconds=duration,
        status="completed",
    )


def _build_computer_use_action_script(
    action: str,
    tool_input: dict,
    cfg: Any,
) -> str:
    """Build a Node.js script to execute a computer_use action in the
    sandbox browser.

    All text values are escaped via _escape_js_string for safe embedding
    in JavaScript string literals. The script is passed directly to
    ``node -e`` without shell interpolation.
    """
    coordinate = tool_input.get("coordinate", [0, 0])
    if not isinstance(coordinate, (list, tuple)):
        coordinate = [0, 0]
    x = int(coordinate[0]) if len(coordinate) > 0 else 0
    y = int(coordinate[1]) if len(coordinate) > 1 else 0
    # Clamp coordinates to viewport bounds
    x = max(0, min(x, cfg.viewport_width))
    y = max(0, min(y, cfg.viewport_height))
    text = _escape_js_string(tool_input.get("text", ""))

    if action == "screenshot":
        return "const fs = require('fs'); console.log('screenshot_taken');"
    elif action in ("click", "left_click"):
        return (
            "const page = global._page; "
            f"(async () => {{ await page.mouse.click({x}, {y}); "
            f"console.log('clicked {x},{y}'); }})();"
        )
    elif action == "right_click":
        return (
            "const page = global._page; "
            f"(async () => {{ await page.mouse.click("
            f"{x}, {y}, {{button: 'right'}}); "
            f"console.log('right_clicked {x},{y}'); }})();"
        )
    elif action == "double_click":
        return (
            "const page = global._page; "
            f"(async () => {{ await page.mouse.dblclick({x}, {y}); "
            f"console.log('double_clicked {x},{y}'); }})();"
        )
    elif action == "type":
        return (
            "const page = global._page; "
            f"(async () => {{ await page.keyboard.type('{text}'); "
            "console.log('typed text'); }})();"
        )
    elif action == "key":
        key = _escape_js_string(tool_input.get("text", "Enter"))
        return (
            "const page = global._page; "
            f"(async () => {{ await page.keyboard.press('{key}'); "
            f"console.log('pressed {_escape_js_string(key)}'); }})();"
        )
    elif action == "scroll":
        scroll_dir = tool_input.get("coordinate", [0, 0])
        scroll_y = scroll_dir[1] if len(scroll_dir) > 1 else -100
        return (
            "const page = global._page; "
            f"(async () => {{ await page.mouse.wheel(0, {scroll_y}); "
            f"console.log('scrolled {scroll_y}'); }})();"
        )
    elif action == "mouse_move":
        return (
            "const page = global._page; "
            f"(async () => {{ await page.mouse.move({x}, {y}); "
            f"console.log('moved to {x},{y}'); }})();"
        )
    else:
        safe_action = _escape_js_string(action)
        return f"console.log('Unknown action: {safe_action}');"


# Maximum base64 screenshot size: ~10 MB PNG -> ~13.3 MB base64
_MAX_SCREENSHOT_B64_SIZE = 14_000_000


async def _take_sandbox_screenshot(
    runtime: Any,
    sandbox: Any,
) -> str | None:
    """Take a screenshot in the sandbox and return base64-encoded PNG."""
    try:
        script = (
            "const page = global._page; "
            "(async () => { "
            "  await page.screenshot({ path: '/tmp/screenshot.png' }); "
            "  const fs = require('fs'); "
            "  const data = fs.readFileSync('/tmp/screenshot.png'); "
            "  console.log(data.toString('base64')); "
            "})();"
        )
        result = await runtime.sandbox_exec(
            sandbox,
            "node",
            ["-e", script],
            timeout=10,
        )
        output = (
            result.get("stdout", "").strip() if isinstance(result, dict) else str(result).strip()
        )
        if output and len(output) > 100:
            if len(output) > _MAX_SCREENSHOT_B64_SIZE:
                logger.warning(
                    "Screenshot too large (%d bytes base64), discarding",
                    len(output),
                )
                return None
            return output
    except Exception as e:
        logger.debug(f"Screenshot failed: {e}")
    return None


async def _browser_lightpanda_mode(
    step: StepDefinition,
    cfg: Any,
    prompt: str,
    credentials_json: str | None,
    context: RunContext,
    sandbox: Any,
    storage: StorageBackend,
    started_at: float,
) -> StepResult:
    """LightPanda mode: launch the LightPanda browser via CDP and connect Playwright to it.

    LightPanda is a fast, low-memory browser that exposes a Chrome DevTools Protocol
    endpoint. This mode launches the binary, connects Playwright over CDP, then follows
    the same accessibility-tree extraction flow as dom mode.
    """
    import asyncio
    import os
    import time

    logger.info("Browser LightPanda mode: %s -> %s", step.id, cfg.start_url)

    # Validate URL before proceeding
    if not cfg.start_url:
        return StepResult(
            step_id=step.id,
            status="failed",
            error="LightPanda mode requires a start_url",
            duration_seconds=time.monotonic() - started_at,
        )

    try:
        validated_url = _validate_browser_url(cfg.start_url)
    except ValueError as e:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"Invalid browser URL: {e}",
            duration_seconds=time.monotonic() - started_at,
        )

    # Resolve LightPanda binary path
    from sandcastle.config import settings as _settings

    lp_binary = _settings.lightpanda_path or os.environ.get("LIGHTPANDA_PATH") or "lightpanda"

    cdp_port = 9222
    lp_proc = None
    try:
        # Launch LightPanda with CDP enabled
        lp_proc = await asyncio.create_subprocess_exec(
            lp_binary,
            "--remote-debugging-port",
            str(cdp_port),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        # Give the process a moment to bind the port
        await asyncio.sleep(0.5)

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            if lp_proc:
                lp_proc.terminate()
            return StepResult(
                step_id=step.id,
                status="failed",
                error="playwright package is required for lightpanda mode: pip install playwright",
                duration_seconds=time.monotonic() - started_at,
            )

        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(
                f"http://localhost:{cdp_port}"
            )
            try:
                page = await browser.new_page(
                    viewport={"width": cfg.viewport_width, "height": cfg.viewport_height}
                )
                await page.goto(validated_url, timeout=cfg.timeout_seconds * 1000)

                # Extract accessibility tree / interactive elements (same as dom mode)
                elements = await page.evaluate(
                    """() => {
                        const items = [];
                        const selectors = 'a, button, input, select, textarea, [role="button"], [onclick]';
                        document.querySelectorAll(selectors).forEach((el, i) => {
                            items.push({
                                index: i,
                                tag: el.tagName.toLowerCase(),
                                type: el.getAttribute('type') || '',
                                text: (el.textContent || '').trim().slice(0, 100),
                                placeholder: el.getAttribute('placeholder') || '',
                                name: el.getAttribute('name') || '',
                                href: el.getAttribute('href') || '',
                                role: el.getAttribute('role') || '',
                            });
                        });
                        return items;
                    }"""
                )
                title = await page.title()
                current_url = page.url
                body_text = await page.evaluate(
                    "() => document.body.innerText.slice(0, 5000)"
                )
                dom_output = json.dumps({
                    "elements": elements,
                    "page_info": {"title": title, "url": current_url, "body_text": body_text},
                })
            finally:
                await browser.close()

    except FileNotFoundError:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=(
                f"LightPanda binary not found at '{lp_binary}'. "
                "Set LIGHTPANDA_PATH env var or install lightpanda on PATH."
            ),
            duration_seconds=time.monotonic() - started_at,
        )
    except Exception as e:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"LightPanda mode error: {e}",
            duration_seconds=time.monotonic() - started_at,
        )
    finally:
        if lp_proc is not None:
            try:
                lp_proc.terminate()
                await lp_proc.wait()
            except Exception:
                pass

    # Build augmented prompt and delegate to LLM (same pattern as dom mode)
    dom_context = f"Page: {cfg.start_url}\n{dom_output}"
    augmented_prompt = (
        "You are a browser automation agent working in DOM mode (via LightPanda). "
        "You can interact with elements by their index number or CSS selector.\n\n"
        "Available tools:\n"
        "- click(selector) - click an element\n"
        "- type_text(selector, text) - type into an element\n"
        "- select_option(selector, value) - select dropdown option\n"
        "- get_text(selector) - get element text\n"
        "- navigate(url) - go to a URL\n\n"
        f"Current page state:\n{dom_context}\n\n"
        f"Task: {step.prompt}"
    )

    if cfg.output_schema:
        augmented_prompt += (
            f"\n\nExtract data matching this schema:\n"
            f"{json.dumps(cfg.output_schema, indent=2)}\n"
            f"Return the extracted data as valid JSON."
        )

    lp_step = StepDefinition(
        id=step.id,
        prompt=augmented_prompt,
        depends_on=step.depends_on,
        model=step.model or "sonnet",
        max_turns=step.max_turns or 5,
        timeout=max(step.timeout, cfg.timeout_seconds),
        output_schema=step.output_schema,
        type="standard",
    )

    if storage is None:
        from sandcastle.engine.storage import LocalStorage

        storage = LocalStorage("/tmp/sandcastle_lp_storage")

    result = await execute_step_with_retry(lp_step, context, sandbox, storage)
    result.step_id = step.id
    return result


async def _browser_browserbase_mode(
    step: StepDefinition,
    cfg: Any,
    prompt: str,
    credentials_json: str | None,
    context: RunContext,
    sandbox: Any,
    storage: StorageBackend,
    started_at: float,
) -> StepResult:
    """Browserbase cloud browser mode.

    Creates a managed browser session via the Browserbase API and connects
    Playwright to it over CDP. Follows the same flow as playwright mode once
    connected. Cleans up the session on exit.

    Requires:
    - BROWSERBASE_API_KEY env var (or browserbase_api_key in settings)
    - Optional: BROWSERBASE_PROJECT_ID env var (or browserbase_project_id)
    """
    import os
    import time

    import httpx

    logger.info("Browser Browserbase mode: %s -> %s", step.id, cfg.start_url)

    from sandcastle.config import settings as _settings

    api_key = (
        _settings.browserbase_api_key
        or os.environ.get("BROWSERBASE_API_KEY", "")
    )
    if not api_key:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=(
                "Browserbase mode requires BROWSERBASE_API_KEY. "
                "Set it in .env or as an environment variable."
            ),
            duration_seconds=time.monotonic() - started_at,
        )

    project_id = (
        _settings.browserbase_project_id
        or os.environ.get("BROWSERBASE_PROJECT_ID", "")
    )

    session_id: str | None = None
    connect_url: str | None = None

    # Create Browserbase session
    try:
        session_payload: dict = {}
        if project_id:
            session_payload["projectId"] = project_id

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://www.browserbase.com/v1/sessions",
                headers={
                    "x-bb-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json=session_payload,
            )
            resp.raise_for_status()
            session_data = resp.json()
            session_id = session_data.get("id")
            connect_url = session_data.get("connectUrl")

    except httpx.HTTPStatusError as e:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"Browserbase session creation failed (HTTP {e.response.status_code}): {e}",
            duration_seconds=time.monotonic() - started_at,
        )
    except Exception as e:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"Browserbase session creation failed: {e}",
            duration_seconds=time.monotonic() - started_at,
        )

    if not connect_url:
        return StepResult(
            step_id=step.id,
            status="failed",
            error="Browserbase API did not return a connectUrl",
            duration_seconds=time.monotonic() - started_at,
        )

    try:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return StepResult(
                step_id=step.id,
                status="failed",
                error="playwright package is required for browserbase mode: pip install playwright",
                duration_seconds=time.monotonic() - started_at,
            )

        # Connect to the Browserbase session via CDP
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(connect_url)
            try:
                page = await browser.new_page(
                    viewport={"width": cfg.viewport_width, "height": cfg.viewport_height}
                )

                if cfg.start_url:
                    start_url = _validate_browser_url(cfg.start_url)
                    await page.goto(start_url, timeout=cfg.timeout_seconds * 1000)

                # Inject credentials into local storage / cookies if provided
                if credentials_json:
                    logger.debug("Browserbase: credentials env available for step %s", step.id)

                # Capture current state
                title = await page.title()
                current_url = page.url
                body_text = await page.evaluate(
                    "() => document.body.innerText.slice(0, 5000)"
                )
            finally:
                await browser.close()

    except Exception as e:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"Browserbase browser error: {e}",
            duration_seconds=time.monotonic() - started_at,
        )
    finally:
        # Clean up the Browserbase session
        if session_id:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.delete(
                        f"https://www.browserbase.com/v1/sessions/{session_id}",
                        headers={"x-bb-api-key": api_key},
                    )
            except Exception as cleanup_err:
                logger.warning(
                    "Failed to delete Browserbase session %s: %s", session_id, cleanup_err
                )

    # Build augmented prompt (same pattern as playwright mode)
    browser_instructions = (
        "You have accessed a page via Browserbase cloud browser.\n\n"
        f"Page title: {title}\n"
        f"Current URL: {current_url}\n"
        f"Page content (first 5000 chars):\n{body_text}\n\n"
    )

    if credentials_json:
        browser_instructions += (
            "## Credentials\n"
            f"Available variables: {', '.join(credentials_json.split(','))}\n"
        )

    augmented_prompt = f"{browser_instructions}\n## Task\n{step.prompt}\n"

    if cfg.output_schema:
        augmented_prompt += (
            f"\n\nExtract data matching this schema:\n"
            f"{json.dumps(cfg.output_schema, indent=2)}\n"
            f"Return the extracted data as valid JSON."
        )

    from sandcastle.engine.dag import StepDefinition as _StepDef

    bb_step = _StepDef(
        id=step.id,
        prompt=augmented_prompt,
        depends_on=step.depends_on,
        model=step.model,
        max_turns=step.max_turns,
        timeout=max(step.timeout, cfg.timeout_seconds),
        output_schema=step.output_schema,
        retry=step.retry,
        fallback=step.fallback,
        type="standard",
        tools=step.tools,
    )

    result = await execute_step_with_retry(bb_step, context, sandbox, storage)
    result.step_id = step.id
    return result


async def _prepare_and_run_step(
    step_id: str,
    workflow: WorkflowDefinition,
    context: RunContext,
    sandbox: Any,
    storage: StorageBackend,
    global_policies: list,
    step_overrides: dict[str, dict] | None,
    depth: int,
    privacy_router: Any = None,
) -> None:
    """Execute one step, update context in place. Raises on abort failure."""
    step = workflow.get_step(step_id)
    overrides = (step_overrides or {}).get(step_id)
    use_dead_letter = workflow.on_failure and workflow.on_failure.dead_letter

    # Resolve policies (use dataclasses.replace to preserve all fields)
    if global_policies and step.policies is None:
        step = dataclasses.replace(step, policies=global_policies)
    elif global_policies and step.policies:
        try:
            from sandcastle.engine.policy import resolve_step_policies

            resolved = resolve_step_policies(step.policies, global_policies)
            step = dataclasses.replace(step, policies=resolved)
        except Exception as e:
            logger.warning(f"Could not resolve step policies: {e}")

    # Skip steps that were excluded by condition/classify branching.
    # Lock guards the check-then-write to prevent a race where a
    # concurrent condition task updates branch_skip_steps between
    # our read and our write to step_outputs.
    async with context._lock:
        if step_id in context.branch_skip_steps:
            context.step_outputs[step_id] = None
            context.step_results[step_id] = StepResult(
                step_id=step_id, status="skipped",
            )
    if step_id in context.step_results and context.step_results[step_id].status == "skipped":
        await _save_run_step(
            run_id=context.run_id,
            step_id=step.id,
            status="skipped",
            output=None,
        )
        return

    # Approval gate
    if step.type == "approval":
        await _execute_approval_step(step, context, 0)
        return  # WorkflowPaused raised above

    async def _handle_step_result(
        result: StepResult,
        *,
        model: str | None = None,
    ) -> None:
        if result.status == "completed":
            # Apply PII redaction to output if privacy router is active and
            # "outputs" is in the apply_to list.
            output = result.output
            if (
                privacy_router is not None
                and "outputs" in privacy_router.config.apply_to
            ):
                try:
                    scrubbed, matches = privacy_router.scrub_dict(output)
                    if matches:
                        logger.info(
                            "PrivacyRouter: redacted %d PII match(es) from step '%s' output",
                            len(matches),
                            step_id,
                        )
                    output = scrubbed
                except Exception as priv_err:
                    logger.warning(
                        "PrivacyRouter scrub failed for step '%s': %s", step_id, priv_err
                    )
            # Guard shared context mutations with lock to prevent races
            # between concurrent steps writing to the same dicts/lists.
            async with context._lock:
                context.costs.append(result.cost_usd)
                context.step_results[step_id] = result
                context.step_outputs[step_id] = output
            await _save_run_step(
                run_id=context.run_id,
                step_id=step.id,
                status="completed",
                output=output,
                cost_usd=result.cost_usd,
                duration_seconds=result.duration_seconds,
                model=model,
            )
        else:
            async with context._lock:
                context.costs.append(result.cost_usd)
                context.step_results[step_id] = result
            await _save_run_step(
                run_id=context.run_id,
                step_id=step.id,
                status="failed",
                cost_usd=result.cost_usd,
                duration_seconds=result.duration_seconds,
                error=result.error,
                model=model,
            )
            on_fail = step.retry.on_failure if step.retry else "abort"
            if use_dead_letter:
                await _send_to_dead_letter(
                    run_id=context.run_id,
                    step_id=step_id,
                    error=result.error,
                    input_data=context.input,
                    attempts=result.attempt,
                )
                async with context._lock:
                    context.step_outputs[step_id] = None
            elif on_fail == "abort":
                raise StepExecutionError(
                    f"Step '{step_id}' failed: {result.error}"
                )
            else:
                async with context._lock:
                    context.step_outputs[step_id] = None

    # --- Helper to dispatch hybrid step types ---
    _HYBRID_TYPES = {
        "llm", "http", "code", "condition", "classify", "loop",
        "race", "sensor", "gate", "transform", "notify", "delegate",
        "browser", "sub_workflow", "composio", "openclaw", "parse",
        "report", "managed-agent", "agent",
    }

    async def _run_hybrid(s: StepDefinition, ctx: RunContext) -> StepResult:
        """Dispatch a single hybrid step type and return its result."""
        if s.type == "llm":
            return await _execute_llm_step(s, ctx, storage)
        if s.type == "http":
            return await _execute_http_step(s, ctx)
        if s.type == "code":
            return await _execute_code_step(s, ctx)
        if s.type == "condition":
            return await _execute_condition_step(s, ctx)
        if s.type == "classify":
            return await _execute_classify_step(s, ctx, storage)
        if s.type == "loop":
            return await _execute_loop_step(
                s, ctx, sandbox, storage, workflow, depth,
            )
        if s.type == "race":
            return await _execute_race_step(
                s, ctx, sandbox, storage, workflow, depth,
            )
        if s.type == "sensor":
            return await _execute_sensor_step(s, ctx)
        if s.type == "gate":
            return await _execute_gate_step(s, ctx, storage)
        if s.type == "transform":
            return await _execute_transform_step(s, ctx)
        if s.type == "notify":
            return await _execute_notify_step(s, ctx)
        if s.type == "delegate":
            return await _execute_delegate_step(
                s, ctx, storage, depth=depth,
            )
        if s.type == "browser":
            return await _execute_browser_step(s, ctx, sandbox, storage)
        if s.type == "sub_workflow":
            return await _execute_sub_workflow_step(
                s, ctx, storage, depth=depth,
            )
        if s.type == "composio":
            return await _execute_composio_step(s, ctx)
        if s.type == "openclaw":
            return await _execute_openclaw_step(s, ctx)
        if s.type == "parse":
            return await _execute_parse_step(s, ctx)
        if s.type == "report":
            return await _execute_report_step(s, ctx, storage)
        if s.type == "managed-agent":
            return await _execute_managed_agent_step(s, ctx, storage)
        if s.type == "agent":
            return await _execute_agent_step(s, ctx, storage)
        raise StepExecutionError(f"Unknown hybrid type '{s.type}'")

    # --- Fan-out (parallel_over) - must come BEFORE type dispatch ---
    # so that hybrid types (llm, http, etc.) also get per-item contexts.
    if step.parallel_over:
        # Strip {braces} - parallel_over may come from YAML as "{steps.x.output.y}"
        fan_out_path = step.parallel_over.strip("{}")
        items = resolve_variable(fan_out_path, context)
        if items is _UNRESOLVED:
            items = []
        elif not isinstance(items, list):
            items = [items]

        is_hybrid = step.type in _HYBRID_TYPES
        logger.info(
            "Fan-out step '%s' (type=%s, hybrid=%s): %d items",
            step_id, step.type, is_hybrid, len(items),
        )
        if is_hybrid:
            tasks = [
                asyncio.create_task(
                    _run_hybrid(step, context.with_item(item, i))
                )
                for i, item in enumerate(items)
            ]
        else:
            tasks = [
                asyncio.create_task(
                    execute_step_with_retry(
                        step,
                        context.with_item(item, i),
                        sandbox,
                        storage,
                        parallel_index=i,
                        step_overrides=overrides,
                    )
                )
                for i, item in enumerate(items)
            ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        fan_out_items: list = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(
                    "Fan-out step '%s' item %d: exception %s",
                    step_id, i, result,
                )
                result = StepResult(
                    step_id=step_id,
                    status="failed",
                    error=str(result),
                )
            else:
                logger.info(
                    "Fan-out step '%s' item %d: status=%s output_type=%s",
                    step_id, i, result.status,
                    type(result.output).__name__ if result.output else "None",
                )
            async with context._lock:
                context.costs.append(result.cost_usd)
            if result.status == "failed":
                on_fail = step.retry.on_failure if step.retry else "abort"
                if use_dead_letter:
                    dlq_ok = await _send_to_dead_letter(
                        run_id=context.run_id,
                        step_id=step_id,
                        error=result.error,
                        input_data={"_item_index": i},
                        attempts=result.attempt,
                        parallel_index=i,
                    )
                    if not dlq_ok:
                        logger.warning(f"DLQ persist failed for step '{step_id}' item {i}")
                    fan_out_items.append(None)
                elif on_fail == "abort":
                    raise StepExecutionError(f"Step '{step_id}' item {i} failed: {result.error}")
                else:
                    fan_out_items.append(None)
            else:
                fan_out_items.append(result.output)
        async with context._lock:
            context.step_outputs[step_id] = fan_out_items
        return

    # --- Hybrid step types (single execution, no parallel_over) ---
    if step.type in _HYBRID_TYPES:
        result = await _run_hybrid(step, context)
        model = step.model if step.type in ("llm", "browser") else None
        await _handle_step_result(result, model=model)
        return

    # Regular (standard/sandbox) step
    result = await execute_step_with_retry(
        step,
        context,
        sandbox,
        storage,
        step_overrides=overrides,
    )
    async with context._lock:
        context.costs.append(result.cost_usd)
        context.step_results[step_id] = result
    if result.status == "failed":
        on_fail = step.retry.on_failure if step.retry else "abort"
        if use_dead_letter:
            dlq_ok = await _send_to_dead_letter(
                run_id=context.run_id,
                step_id=step_id,
                error=result.error,
                input_data=context.input,
                attempts=result.attempt,
            )
            if not dlq_ok:
                logger.warning(f"DLQ persist failed for step '{step_id}'")
            async with context._lock:
                context.step_outputs[step_id] = None
        elif on_fail == "abort":
            raise StepExecutionError(f"Step '{step_id}' failed: {result.error}")
        else:
            async with context._lock:
                context.step_outputs[step_id] = None
    else:
        async with context._lock:
            context.step_outputs[step_id] = result.output


# Text file extensions that should be inlined as plain text content
_TEXT_EXTENSIONS = frozenset(
    {".txt", ".csv", ".json", ".yaml", ".yml", ".md", ".html", ".xml", ".log"}
)


async def _resolve_file_input(value: str, storage: StorageBackend) -> str:
    """Resolve ``@upload:file_id`` references to file content.

    For text files (CSV, TXT, JSON, YAML, etc.) the raw content is returned as
    a UTF-8 string so it can be inlined directly into a prompt.  For binary
    files (PDF, images, Office documents) an ``@file:`` reference is returned
    so that the existing @file: handler in the sandbox runtime can read and
    convert the content appropriately.

    Non-@upload: values are returned unchanged (passthrough).
    """
    if not isinstance(value, str) or not value.startswith("@upload:"):
        return value

    file_id = value[len("@upload:"):]
    if not file_id:
        logger.warning("Empty @upload: file_id in workflow input - returning empty string")
        return ""

    # Try to read from the uploads/ prefix in storage
    # storage.read() returns str | None; for local storage it reads from disk.
    # We construct the storage key pattern that matches dest_name set in routes.py.
    # Since we only have the file_id (uuid hex prefix), list storage to locate it.
    try:
        all_keys = await storage.list("uploads/")
        matching = [k for k in all_keys if Path(k).name.startswith(file_id)]
    except Exception as exc:
        logger.warning("Could not list uploads to resolve @upload:%s: %s", file_id, exc)
        return value

    if not matching:
        logger.warning("No upload found for file_id '%s'", file_id)
        return value

    # Use the first match (should be unique by uuid prefix)
    key = matching[0]
    suffix = Path(key).suffix.lower()

    if suffix in _TEXT_EXTENSIONS:
        # Inline text content directly
        try:
            content = await storage.read(key)
            if content is None:
                logger.warning("Upload '%s' read returned None", key)
                return value
            return content
        except Exception as exc:
            logger.warning("Failed to read upload '%s': %s", key, exc)
            return value
    else:
        # For PDF/DOCX/XLSX: try auto-parse if docparse is available.
        # This gives a seamless experience - PDFs are auto-converted to text
        # without requiring an explicit type: parse step.
        if suffix in (".pdf", ".docx", ".xlsx"):
            from sandcastle.config import settings as _file_settings

            local_path = (
                Path(_file_settings.data_dir).expanduser().resolve() / key
            )
            try:
                from sandcastle.engine.docparse import parse_document

                parse_result = parse_document(str(local_path), output_format="text")
                return parse_result.text
            except ImportError:
                pass  # docparse optional - fall through to @file: reference
            except Exception:
                pass  # parse error - fall through to @file: reference

        # Return @file: reference for binary files - existing handler will process it
        from sandcastle.config import settings as _file_settings

        local_path = (
            Path(_file_settings.data_dir).expanduser().resolve() / key
        )
        return f"@file:{local_path}"


def _compute_token_report(context: RunContext) -> dict:
    """Analyze token usage for waste detection after a workflow run."""
    report: dict[str, Any] = {
        "duplicate_file_reads": {
            path: count
            for path, count in context._file_read_counts.items()
            if count > 1
        },
        "duplicate_prompt_count": 0,
        "cached_file_reads": len(context._file_read_cache),
        "recommendations": [],
    }

    # Count duplicate prompts (hashes seen more than once cannot be tracked
    # individually with a set, but we log a warning when detected - report
    # the total number of unique prompts observed).
    report["unique_prompts"] = len(context._seen_prompt_hashes)

    # Check for large context injection
    for step_id, output in context.step_outputs.items():
        if isinstance(output, str) and len(output) > 20000:
            report["recommendations"].append(
                f"Step '{step_id}' output is {len(output)} chars "
                f"(~{len(output) // 4} tokens). Consider adding "
                f"output_max_tokens to limit context passed to dependents."
            )
        elif isinstance(output, (dict, list)):
            serialized = json.dumps(output)
            if len(serialized) > 20000:
                report["recommendations"].append(
                    f"Step '{step_id}' output is {len(serialized)} chars "
                    f"(~{len(serialized) // 4} tokens). Consider adding "
                    f"output_max_tokens to limit context passed to dependents."
                )

    # Check duplicate reads
    for path, count in report["duplicate_file_reads"].items():
        report["recommendations"].append(
            f"File '{path}' was read {count} times. "
            f"Content is cached after first read."
        )

    return report


async def execute_workflow(
    workflow: WorkflowDefinition,
    plan: ExecutionPlan,
    input_data: dict,
    run_id: str | None = None,
    storage: StorageBackend | None = None,
    max_cost_usd: float | None = None,
    initial_context: dict | None = None,
    skip_steps: set[str] | None = None,
    step_overrides: dict[str, dict] | None = None,
    depth: int = 0,
    admin_trusted: bool = False,
    tenant_id: str | None = None,
) -> WorkflowResult:
    """Execute a full workflow with parallel stages and retry logic.

    Args:
        workflow: Parsed workflow definition.
        plan: Execution plan with topologically sorted stages.
        input_data: Input data for the workflow.
        run_id: Optional run UUID (generated if not provided).
        storage: Storage backend for file references.
        max_cost_usd: Budget limit - hard stop at 100%.
        initial_context: Pre-loaded context for replay/fork (skip_steps outputs).
        skip_steps: Set of step IDs to skip (already completed in replay).
        step_overrides: Per-step overrides for fork (e.g. {"score": {"model": "opus"}}).
        depth: Current nesting depth for hierarchical workflows.
    """
    from sandcastle.config import settings
    from sandcastle.engine.storage import create_storage

    # Depth check for hierarchical workflows
    if depth >= settings.max_workflow_depth:
        return WorkflowResult(
            run_id=run_id or str(uuid.uuid4()),
            outputs={},
            total_cost_usd=0.0,
            status="failed",
            error=f"Max workflow depth ({settings.max_workflow_depth}) exceeded",
        )

    # EU AI Act: block unacceptable risk workflows before any execution begins
    risk_level = getattr(workflow, "risk_level", "minimal")
    if risk_level == "unacceptable":
        return WorkflowResult(
            run_id=run_id or str(uuid.uuid4()),
            outputs={},
            total_cost_usd=0.0,
            status="failed",
            error="Unacceptable risk workflows cannot be executed under EU AI Act",
        )

    # Compliance mode enforcement
    compliance_mode = getattr(settings, "compliance_mode", "")
    if compliance_mode == "eu_ai_act":
        logger.info("EU AI Act compliance mode active")

    # EU AI Act: warn (or fail in compliance mode) if high-risk workflow has no approval step
    if risk_level == "high":
        has_approval = any(s.type == "approval" for s in workflow.steps)
        if not has_approval:
            if compliance_mode == "eu_ai_act":
                return WorkflowResult(
                    run_id=run_id or str(uuid.uuid4()),
                    outputs={},
                    total_cost_usd=0.0,
                    status="failed",
                    error=(
                        "EU AI Act compliance mode: high-risk workflow '"
                        + workflow.name
                        + "' requires a human approval step but none was found."
                    ),
                )
            logger.warning(
                "Workflow '%s' is classified as high-risk (EU AI Act) but has no "
                "approval step. Human oversight is recommended.",
                workflow.name,
            )

    if run_id is None:
        run_id = str(uuid.uuid4())

    if storage is None:
        storage = create_storage()

    started_at = datetime.now(timezone.utc)

    # Merge input_schema defaults with provided input_data
    merged_input = {}
    if workflow.input_schema and "properties" in workflow.input_schema:
        for key, prop in workflow.input_schema["properties"].items():
            if "default" in prop:
                merged_input[key] = prop["default"]
    merged_input.update(input_data)

    # Resolve @upload:file_id references - expand uploaded files into content
    for key, val in list(merged_input.items()):
        if isinstance(val, str) and val.startswith("@upload:"):
            try:
                merged_input[key] = await _resolve_file_input(val, storage)
            except Exception as _file_exc:
                logger.warning(
                    "Failed to resolve file input '%s' for key '%s': %s",
                    val,
                    key,
                    _file_exc,
                )

    # Build output_max_tokens mapping from step definitions
    step_output_max_tokens = {
        s.id: s.output_max_tokens
        for s in workflow.steps
        if s.output_max_tokens > 0
    }

    context = RunContext(
        run_id=run_id,
        input=merged_input,
        max_cost_usd=max_cost_usd,
        workflow_name=workflow.name,
        default_tools=getattr(workflow, "default_tools", []),
        _step_output_max_tokens=step_output_max_tokens,
        admin_trusted=admin_trusted,
        tenant_id=tenant_id,
    )

    # Set telemetry context for this workflow run
    from sandcastle.engine.telemetry import set_workflow_context

    set_workflow_context(
        workflow_name=workflow.name,
        run_id=run_id,
        sandbox_backend=getattr(settings, "sandbox_backend", ""),
    )

    # Initialize OpenTelemetry tracing if enabled
    from sandcastle.engine.otel import init_otel, workflow_span

    if settings.otel_enabled and settings.otel_endpoint:
        init_otel(settings.otel_service_name, settings.otel_endpoint)

    # Initialize agent memory if configured
    if workflow.memory:
        try:
            from sandcastle.config import settings as _mem_settings

            if _mem_settings.memory_enabled:
                from sandcastle.engine.memory import (
                    apply_decay,
                    load_memories,
                    resolve_scope_id,
                )

                context._memory_config = workflow.memory
                context._memory_scope_id = resolve_scope_id(
                    workflow.memory,
                    workflow.name,
                    tenant_id=tenant_id,
                )
                context.memories = await load_memories(
                    context._memory_scope_id,
                    limit=workflow.memory.max_inject,
                )
                # Apply memory decay (TTL filtering)
                max_age = (
                    workflow.memory.max_age_days
                    or _mem_settings.memory_max_age_days
                )
                if max_age > 0:
                    context.memories = apply_decay(
                        context.memories, max_age_days=max_age
                    )
                logger.info(
                    "Loaded %d memories for scope '%s'",
                    len(context.memories),
                    context._memory_scope_id,
                )
        except Exception as e:
            logger.warning(f"Failed to initialize memory: {e}")

    # Restore context from checkpoint if doing replay/fork
    if initial_context:
        context.step_outputs = initial_context.get("step_outputs", {})
        context.costs = initial_context.get("costs", [])

    # Resolve global policies from workflow definition
    global_policies = []
    if hasattr(workflow, "policies") and workflow.policies:
        try:
            from sandcastle.engine.policy import (
                PolicyAction as PEPolicyAction,
            )
            from sandcastle.engine.policy import (
                PolicyDefinition as PEPolicyDefinition,
            )
            from sandcastle.engine.policy import (
                PolicyPattern as PEPolicyPattern,
            )
            from sandcastle.engine.policy import (
                PolicyTrigger as PEPolicyTrigger,
            )

            for gp in workflow.policies:
                # Convert DAG dataclasses to policy engine dataclasses
                pe_trigger = PEPolicyTrigger(
                    type=gp.trigger.type,
                    patterns=[
                        PEPolicyPattern(type=p.type, pattern=p.pattern)
                        for p in (gp.trigger.patterns or [])
                    ]
                    if gp.trigger.patterns
                    else None,
                    expression=gp.trigger.expression,
                )
                pe_action = PEPolicyAction(
                    type=gp.action.type,
                    replacement=gp.action.replacement,
                    apply_to=gp.action.apply_to,
                    approval_config=gp.action.approval_config,
                    message=gp.action.message,
                    notify=gp.action.notify,
                )
                global_policies.append(
                    PEPolicyDefinition(
                        id=gp.id,
                        trigger=pe_trigger,
                        action=pe_action,
                        description=gp.description,
                        severity=gp.severity,
                    )
                )
        except Exception as e:
            logger.warning(f"Could not load global policies: {e}")

    # Build PrivacyRouter if privacy is configured at workflow or server level.
    privacy_router = None
    try:
        from sandcastle.engine.privacy import PrivacyRouter

        server_privacy = {
            "enabled": settings.privacy_enabled,
            "entities": settings.privacy_entities,
            "apply_to": settings.privacy_apply_to,
        }
        privacy_router = PrivacyRouter.from_workflow(
            workflow_privacy=getattr(workflow, "privacy", None),
            server_config=server_privacy,
        )
        if privacy_router:
            logger.info(
                "PrivacyRouter enabled for run %s: entities=%s, mode=%s, apply_to=%s",
                run_id,
                privacy_router.config.entities,
                privacy_router.config.mode,
                privacy_router.config.apply_to,
            )
    except Exception as e:
        logger.warning("Could not initialize PrivacyRouter: %s", e)

    logger.info(
        "Sandshore runtime: e2b_key=%s, backend=%s",
        "set" if settings.e2b_api_key else "unset",
        settings.sandbox_backend,
    )
    sandbox = get_sandshore_runtime(
        anthropic_api_key=settings.anthropic_api_key,
        e2b_api_key=settings.e2b_api_key,
        template=settings.e2b_template,
        max_concurrent=settings.max_concurrent_sandboxes,
        sandbox_backend=settings.sandbox_backend,
        docker_image=settings.docker_image,
        docker_url=settings.docker_url or None,
        cloudflare_worker_url=settings.cloudflare_worker_url,
    )

    # Broadcast run.started event
    event_bus.publish(
        "run.started",
        {
            "run_id": run_id,
            "workflow": workflow.name,
        },
    )
    await _emit_audit_event(
        "run.started",
        run_id=run_id,
        actor_id="system",
        payload={"workflow": workflow.name},
    )

    # Dependency-based scheduler: start steps as soon as deps complete
    all_step_ids = [s.id for s in workflow.steps]
    step_deps = {s.id: set(s.depends_on) for s in workflow.steps}

    # Add implicit deps: condition/classify branch steps must wait for
    # the condition/classify step to complete before starting, even if
    # the branch steps don't have an explicit depends_on for it.
    for s in workflow.steps:
        if s.type == "condition" and s.condition_config:
            for branch_sid in s.condition_config.then_steps:
                if branch_sid in step_deps:
                    step_deps[branch_sid].add(s.id)
            for branch_sid in s.condition_config.else_steps:
                if branch_sid in step_deps:
                    step_deps[branch_sid].add(s.id)
        elif s.type == "classify" and s.classify_config:
            for branch_steps in s.classify_config.branches.values():
                for branch_sid in branch_steps:
                    if branch_sid in step_deps:
                        step_deps[branch_sid].add(s.id)

    done_steps: set[str] = set(skip_steps or ())
    running: dict[str, asyncio.Task] = {}
    checkpoint_counter = len(done_steps)

    for sid in done_steps:
        logger.info(f"Skipping step '{sid}' (replay/fork)")

    def _find_ready() -> list[str]:
        return sorted(
            sid
            for sid in all_step_ids
            if sid not in done_steps
            and sid not in running
            and step_deps[sid].issubset(done_steps | context.branch_skip_steps)
        )

    async def _cancel_running() -> None:
        for t in running.values():
            t.cancel()
        if running:
            await asyncio.gather(*running.values(), return_exceptions=True)
            running.clear()

    # Enter OTEL workflow span (no-op when tracer not initialized)
    _wf_span_cm = workflow_span(run_id, workflow.name)
    _wf_span = _wf_span_cm.__enter__()
    try:
        while True:
            # Cancel check
            if await _check_cancel(run_id):
                await _cancel_running()
                logger.info(f"Run {run_id} cancelled")
                return WorkflowResult(
                    run_id=run_id,
                    outputs=context.step_outputs,
                    total_cost_usd=context.total_cost,
                    status="cancelled",
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc),
                )

            # Budget check
            budget_status = _check_budget(context)
            if budget_status == "exceeded":
                await _cancel_running()
                cost = context.total_cost
                limit = context.max_cost_usd
                logger.warning(f"Run {run_id} budget exceeded (${cost:.4f} / ${limit:.4f})")
                return WorkflowResult(
                    run_id=run_id,
                    outputs=context.step_outputs,
                    total_cost_usd=cost,
                    status="budget_exceeded",
                    error=f"Budget exceeded: ${cost:.4f} >= ${limit:.4f}",
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc),
                )
            elif budget_status == "warning":
                logger.warning(
                    f"Run {run_id} at 80%+ budget "
                    f"(${context.total_cost:.4f} / "
                    f"${context.max_cost_usd:.4f})"
                )

            # Launch ready steps
            for sid in _find_ready():
                running[sid] = asyncio.create_task(
                    _prepare_and_run_step(
                        sid,
                        workflow,
                        context,
                        sandbox,
                        storage,
                        global_policies,
                        step_overrides,
                        depth,
                        privacy_router=privacy_router,
                    )
                )

            if not running:
                break  # All steps done

            # Wait for at least one step to finish
            done_tasks, _ = await asyncio.wait(
                running.values(),
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in done_tasks:
                sid = next(k for k, v in running.items() if v is task)
                del running[sid]

                exc = task.exception()
                if exc is not None:
                    await _cancel_running()
                    raise exc

                done_steps.add(sid)
                # Also mark branch-skipped steps as done so dependents unblock
                for skip_sid in list(context.branch_skip_steps):
                    if skip_sid not in done_steps:
                        done_steps.add(skip_sid)
                        context.step_outputs.setdefault(skip_sid, None)
                checkpoint_counter += 1
                await _save_checkpoint(
                    run_id,
                    sid,
                    checkpoint_counter,
                    context,
                )

        completed_at = datetime.now(timezone.utc)

        # Store results if configured
        if workflow.on_complete and workflow.on_complete.storage_path:
            storage_path = resolve_templates(workflow.on_complete.storage_path, context)
            await storage.write(storage_path, json.dumps(context.step_outputs))

        # Broadcast run.completed event
        duration = (completed_at - started_at).total_seconds()
        event_bus.publish(
            "run.completed",
            {
                "run_id": run_id,
                "status": "completed",
                "workflow": workflow.name,
                "duration_seconds": duration,
                "total_cost_usd": context.total_cost,
            },
        )
        await _emit_audit_event(
            "run.completed",
            run_id=run_id,
            actor_id="system",
            payload={"workflow": workflow.name, "duration_seconds": duration, "total_cost_usd": context.total_cost},
        )

        # Compute token efficiency report
        token_report = _compute_token_report(context)

        return WorkflowResult(
            run_id=run_id,
            outputs=context.step_outputs,
            total_cost_usd=context.total_cost,
            status="completed",
            started_at=started_at,
            completed_at=completed_at,
            token_report=token_report,
        )

    except WorkflowPaused:
        return WorkflowResult(
            run_id=run_id,
            outputs=context.step_outputs,
            total_cost_usd=context.total_cost,
            status="awaiting_approval",
            error=None,
            started_at=started_at,
            completed_at=None,
        )

    except StepBlocked as e:
        completed_at = datetime.now(timezone.utc)
        event_bus.publish(
            "run.failed",
            {
                "run_id": run_id,
                "workflow": workflow.name,
                "error": f"Policy blocked: {e}",
            },
        )
        await _emit_audit_event(
            "run.failed",
            run_id=run_id,
            actor_id="system",
            payload={"workflow": workflow.name, "error": f"Policy blocked: {e}", "reason": "policy_blocked"},
        )
        return WorkflowResult(
            run_id=run_id,
            outputs=context.step_outputs,
            total_cost_usd=context.total_cost,
            status="failed",
            error=f"Policy blocked: {e}",
            started_at=started_at,
            completed_at=completed_at,
        )

    except StepExecutionError as e:
        completed_at = datetime.now(timezone.utc)
        event_bus.publish(
            "run.failed",
            {
                "run_id": run_id,
                "workflow": workflow.name,
                "error": str(e),
            },
        )
        await _emit_audit_event(
            "run.failed",
            run_id=run_id,
            actor_id="system",
            payload={"workflow": workflow.name, "error": str(e), "reason": "step_execution_error"},
        )
        return WorkflowResult(
            run_id=run_id,
            outputs=context.step_outputs,
            total_cost_usd=context.total_cost,
            status="failed",
            error=str(e),
            started_at=started_at,
            completed_at=completed_at,
        )

    except Exception as e:
        completed_at = datetime.now(timezone.utc)
        logger.error(f"Workflow '{workflow.name}' failed: {e}")
        event_bus.publish(
            "run.failed",
            {
                "run_id": run_id,
                "workflow": workflow.name,
                "error": str(e),
            },
        )
        await _emit_audit_event(
            "run.failed",
            run_id=run_id,
            actor_id="system",
            payload={"workflow": workflow.name, "error": str(e), "reason": "unexpected_error"},
        )
        return WorkflowResult(
            run_id=run_id,
            outputs=context.step_outputs,
            total_cost_usd=context.total_cost,
            status="failed",
            error=str(e),
            started_at=started_at,
            completed_at=completed_at,
        )

    finally:
        async with _cancel_flags_lock:
            _cancel_flags.pop(run_id, None)
        _wf_span_cm.__exit__(None, None, None)


async def _save_routing_decision(
    run_id: str,
    step_id: str,
    decision: Any,
    slo_config: Any = None,
) -> None:
    """Save an optimizer routing decision to the database."""
    try:
        from sandcastle.models.db import RoutingDecision as DBRoutingDecision
        from sandcastle.models.db import async_session

        slo_data = None
        if slo_config:
            slo_data = {
                "quality_min": slo_config.quality_min,
                "cost_max_usd": slo_config.cost_max_usd,
                "latency_max_seconds": slo_config.latency_max_seconds,
                "optimize_for": slo_config.optimize_for,
            }

        alternatives_data = [
            {
                "id": a.id,
                "model": a.model,
                "avg_quality": a.avg_quality,
                "avg_cost": a.avg_cost,
            }
            for a in decision.alternatives
        ]

        async with async_session() as session:
            rd = DBRoutingDecision(
                run_id=uuid.UUID(run_id),
                step_id=step_id,
                selected_model=decision.selected_option.model,
                selected_variant_id=decision.selected_option.id,
                reason=decision.reason,
                budget_pressure=decision.budget_pressure,
                confidence=decision.confidence,
                alternatives=alternatives_data,
                slo=slo_data,
            )
            session.add(rd)
            await session.commit()
    except Exception as e:
        logger.warning(f"Could not save routing decision for {step_id}: {e}")


async def _save_policy_violations(
    run_id: str,
    step_id: str,
    violations: list,
) -> None:
    """Save policy violations to the database."""
    try:
        from sandcastle.models.db import PolicyViolation as DBPolicyViolation
        from sandcastle.models.db import async_session

        async with async_session() as session:
            for v in violations:
                pv = DBPolicyViolation(
                    run_id=uuid.UUID(run_id),
                    step_id=step_id,
                    policy_id=v.policy_id,
                    severity=v.severity,
                    trigger_details=v.trigger_details,
                    action_taken=v.action_taken,
                    output_modified=v.output_modified,
                )
                session.add(pv)
            await session.commit()
    except Exception as e:
        logger.warning(f"Could not save policy violations for {step_id}: {e}")


async def _send_to_dead_letter(
    run_id: str,
    step_id: str,
    error: str | None,
    input_data: dict | None,
    attempts: int,
    parallel_index: int | None = None,
) -> bool:
    """Insert a failed step into the dead letter queue.

    Returns True if the item was persisted, False on failure.
    """
    try:
        from sandcastle.models.db import DeadLetterItem, async_session

        async with async_session() as session:
            dlq_item = DeadLetterItem(
                run_id=uuid.UUID(run_id),
                step_id=step_id,
                parallel_index=parallel_index,
                error=error,
                input_data=input_data,
                attempts=attempts,
            )
            session.add(dlq_item)
            await session.commit()

        # Broadcast dlq.new event
        event_bus.publish(
            "dlq.new",
            {
                "run_id": run_id,
                "step_name": step_id,
                "error": error,
            },
        )
        await _emit_audit_event(
            "dlq.created",
            run_id=run_id,
            actor_id="system",
            payload={"step_id": step_id, "error": error, "attempts": attempts},
        )

        logger.info(f"Step '{step_id}' sent to dead letter queue")
        return True
    except Exception as e:
        logger.error(f"Failed to insert into dead letter queue: {e}")
        return False


class StepExecutionError(Exception):
    """A step failed and the workflow should abort."""


class StepBlocked(Exception):
    """A step was blocked by a policy."""

    def __init__(self, step_id: str, reason: str):
        self.step_id = step_id
        self.reason = reason
        super().__init__(f"Step '{step_id}' blocked: {reason}")


class WorkflowPaused(Exception):
    """A workflow is paused waiting for human approval."""

    def __init__(self, approval_id: str, run_id: str):
        self.approval_id = approval_id
        self.run_id = run_id
        super().__init__(f"Workflow paused: approval {approval_id} for run {run_id}")
