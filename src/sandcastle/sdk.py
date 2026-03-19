"""Sandcastle Python SDK - sync and async clients for the Sandcastle API."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncGenerator, Generator, Iterator, Optional

import httpx

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SandcastleError(Exception):
    """Error returned by the Sandcastle API."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(f"[{status_code}] {code}: {message}")


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class Step:
    """A single step within a workflow run."""

    step_id: str
    status: str
    parallel_index: Optional[int] = None
    output: Any = None
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    attempt: int = 1
    error: Optional[str] = None


@dataclass
class Run:
    """Workflow run status and details."""

    run_id: str
    status: str
    workflow_name: str = ""
    input_data: Optional[dict[str, Any]] = None
    outputs: Optional[dict[str, Any]] = None
    total_cost_usd: float = 0.0
    max_cost_usd: Optional[float] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    steps: Optional[list[Step]] = None
    parent_run_id: Optional[str] = None
    replay_from_step: Optional[str] = None
    fork_changes: Optional[dict[str, Any]] = None
    depth: int = 0
    sub_workflow_of_step: Optional[str] = None
    sub_runs: Optional[list[dict[str, Any]]] = None

    # Extra fields for replay/fork responses
    new_run_id: Optional[str] = None
    fork_from_step: Optional[str] = None
    changes: Optional[dict[str, Any]] = None


@dataclass
class RunListItem:
    """Summary item returned by list_runs."""

    run_id: str
    workflow_name: str
    status: str
    total_cost_usd: float = 0.0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    parent_run_id: Optional[str] = None


@dataclass
class Workflow:
    """Workflow metadata."""

    name: str
    description: str
    steps_count: int
    file_name: str


@dataclass
class Schedule:
    """Workflow schedule."""

    id: str
    workflow_name: str
    cron_expression: str
    input_data: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    last_run_id: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class Approval:
    """Approval gate request."""

    id: str
    run_id: str
    step_id: str
    status: str
    request_data: Optional[dict[str, Any]] = None
    response_data: Optional[dict[str, Any]] = None
    message: str = ""
    reviewer_id: Optional[str] = None
    reviewer_comment: Optional[str] = None
    timeout_at: Optional[datetime] = None
    on_timeout: str = "abort"
    allow_edit: bool = False
    created_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None


@dataclass
class HealthStatus:
    """Health check result."""

    status: str
    runtime: bool
    database: bool
    redis: Optional[bool] = None


@dataclass
class RuntimeInfo:
    """Runtime mode information."""

    mode: str
    database: str
    queue: str
    storage: str
    data_dir: Optional[str] = None
    sandbox_backend: str = ""
    license: dict[str, Any] | None = None


@dataclass
class Stats:
    """Dashboard statistics."""

    total_runs_today: int = 0
    success_rate: float = 0.0
    total_cost_today: float = 0.0
    avg_duration_seconds: float = 0.0
    runs_by_day: list[dict[str, Any]] = field(default_factory=list)
    cost_by_workflow: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PaginatedList:
    """A paginated list of items with metadata."""

    items: list[Any]
    total: int = 0
    limit: int = 50
    offset: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TERMINAL_STATUSES = frozenset({
    "completed", "failed", "partial", "cancelled", "error",
    "budget_exceeded", "awaiting_approval",
})


def _validate_pagination(limit: int, offset: int) -> None:
    """Validate pagination parameters."""
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    if limit > 200:
        raise ValueError("limit must not exceed 200")
    if not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer")


def _validate_path_param(value: str, name: str) -> str:
    """Validate a value used in URL path segments.

    Raises ValueError for empty strings, strings with slashes (path traversal),
    or strings with only whitespace.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if "/" in value or "\\" in value:
        raise ValueError(f"{name} must not contain path separators")
    return value


def _parse_datetime(value: Any) -> Optional[datetime]:
    """Parse an ISO datetime string, returning None on failure."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _parse_step(data: dict[str, Any]) -> Step:
    """Build a Step from a dict."""
    return Step(
        step_id=data.get("step_id", ""),
        status=data.get("status", "unknown"),
        parallel_index=data.get("parallel_index"),
        output=data.get("output"),
        cost_usd=data.get("cost_usd", 0.0),
        duration_seconds=data.get("duration_seconds", 0.0),
        attempt=data.get("attempt", 1),
        error=data.get("error"),
    )


def _parse_run(data: dict[str, Any]) -> Run:
    """Build a Run from an API response dict."""
    steps = None
    if data.get("steps") is not None:
        steps = [_parse_step(s) for s in data["steps"]]

    return Run(
        run_id=data.get("run_id", data.get("new_run_id", "")),
        status=data.get("status", "unknown"),
        workflow_name=data.get("workflow_name", ""),
        input_data=data.get("input_data"),
        outputs=data.get("outputs"),
        total_cost_usd=data.get("total_cost_usd", 0.0),
        max_cost_usd=data.get("max_cost_usd"),
        started_at=_parse_datetime(data.get("started_at")),
        completed_at=_parse_datetime(data.get("completed_at")),
        error=data.get("error"),
        steps=steps,
        parent_run_id=data.get("parent_run_id"),
        replay_from_step=data.get("replay_from_step"),
        fork_changes=data.get("fork_changes"),
        depth=data.get("depth", 0),
        sub_workflow_of_step=data.get("sub_workflow_of_step"),
        sub_runs=data.get("sub_runs"),
        new_run_id=data.get("new_run_id"),
        fork_from_step=data.get("fork_from_step"),
        changes=data.get("changes"),
    )


def _parse_run_list_item(data: dict[str, Any]) -> RunListItem:
    """Build a RunListItem from an API response dict."""
    return RunListItem(
        run_id=data.get("run_id", ""),
        workflow_name=data.get("workflow_name", ""),
        status=data.get("status", "unknown"),
        total_cost_usd=data.get("total_cost_usd", 0.0),
        started_at=_parse_datetime(data.get("started_at")),
        completed_at=_parse_datetime(data.get("completed_at")),
        parent_run_id=data.get("parent_run_id"),
    )


def _parse_workflow(data: dict[str, Any]) -> Workflow:
    """Build a Workflow from an API response dict."""
    return Workflow(
        name=data.get("name", ""),
        description=data.get("description", ""),
        steps_count=data.get("steps_count", 0),
        file_name=data.get("file_name", ""),
    )


def _parse_schedule(data: dict[str, Any]) -> Schedule:
    """Build a Schedule from an API response dict."""
    return Schedule(
        id=data.get("id", ""),
        workflow_name=data.get("workflow_name", ""),
        cron_expression=data.get("cron_expression", ""),
        input_data=data.get("input_data", {}),
        enabled=data.get("enabled", True),
        last_run_id=data.get("last_run_id"),
        created_at=_parse_datetime(data.get("created_at")),
    )


def _parse_health(data: dict[str, Any]) -> HealthStatus:
    """Build a HealthStatus from an API response dict."""
    return HealthStatus(
        status=data.get("status", "unknown"),
        runtime=data.get("runtime", False),
        database=data.get("database", False),
        redis=data.get("redis"),
    )


def _parse_runtime(data: dict[str, Any]) -> RuntimeInfo:
    """Build a RuntimeInfo from an API response dict."""
    return RuntimeInfo(
        mode=data.get("mode", "unknown"),
        database=data.get("database", "unknown"),
        queue=data.get("queue", "unknown"),
        storage=data.get("storage", "unknown"),
        data_dir=data.get("data_dir"),
        sandbox_backend=data.get("sandbox_backend", ""),
        license=data.get("license"),
    )


def _parse_stats(data: dict[str, Any]) -> Stats:
    """Build a Stats from an API response dict."""
    return Stats(
        total_runs_today=data.get("total_runs_today", 0),
        success_rate=data.get("success_rate", 0.0),
        total_cost_today=data.get("total_cost_today", 0.0),
        avg_duration_seconds=data.get("avg_duration_seconds", 0.0),
        runs_by_day=data.get("runs_by_day", []),
        cost_by_workflow=data.get("cost_by_workflow", []),
    )


def _parse_approval(data: dict[str, Any]) -> Approval:
    """Build an Approval from an API response dict."""
    return Approval(
        id=data.get("id", ""),
        run_id=data.get("run_id", ""),
        step_id=data.get("step_id", ""),
        status=data.get("status", "unknown"),
        request_data=data.get("request_data"),
        response_data=data.get("response_data"),
        message=data.get("message", ""),
        reviewer_id=data.get("reviewer_id"),
        reviewer_comment=data.get("reviewer_comment"),
        timeout_at=_parse_datetime(data.get("timeout_at")),
        on_timeout=data.get("on_timeout", "abort"),
        allow_edit=data.get("allow_edit", False),
        created_at=_parse_datetime(data.get("created_at")),
        resolved_at=_parse_datetime(data.get("resolved_at")),
    )


def _extract_data(response: httpx.Response) -> Any:
    """Extract data from an API response, raising SandcastleError on failure."""
    if response.status_code >= 400:
        # Try to parse structured error
        try:
            body = response.json()
            # The API wraps errors in {"detail": {"error": {...}}}
            detail = body if isinstance(body, dict) else {}
            if "detail" in detail:
                detail = detail["detail"]
            err = detail.get("error", {})
            code = err.get("code", "API_ERROR")
            message = err.get("message", response.text)
        except Exception:
            code = "API_ERROR"
            message = response.text[:500] if response.text else "Unknown API error"
        raise SandcastleError(response.status_code, code, message)

    # Handle 204 No Content
    if response.status_code == 204:
        return {}

    try:
        body = response.json()
    except Exception:
        raise SandcastleError(
            response.status_code,
            "INVALID_RESPONSE",
            f"Expected JSON response, got: {response.text[:500]}"
        )
    return body.get("data", body)


def _parse_sse_lines(raw: str) -> Iterator[dict[str, Any]]:
    """Parse raw SSE text into event dicts.

    Follows the SSE spec: multiple ``data:`` lines are concatenated with
    newline characters before JSON parsing.  Per the spec, only a single
    leading space after the colon is removed (not arbitrary whitespace).
    """
    event_type = ""
    data_lines: list[str] = []

    for line in raw.split("\n"):
        if line.startswith("event:"):
            value = line[len("event:"):]
            # Per SSE spec: strip exactly one leading space after colon
            if value.startswith(" "):
                value = value[1:]
            event_type = value
        elif line.startswith("data:"):
            value = line[len("data:"):]
            if value.startswith(" "):
                value = value[1:]
            data_lines.append(value)
        elif line == "" and data_lines:
            data_buf = "\n".join(data_lines)
            try:
                parsed = json.loads(data_buf)
            except json.JSONDecodeError:
                parsed = {"raw": data_buf}
            parsed["_event"] = event_type
            yield parsed
            event_type = ""
            data_lines = []

    # Flush any remaining data if stream ended without trailing blank line
    if data_lines:
        data_buf = "\n".join(data_lines)
        try:
            parsed = json.loads(data_buf)
        except json.JSONDecodeError:
            parsed = {"raw": data_buf}
        parsed["_event"] = event_type
        yield parsed


# ---------------------------------------------------------------------------
# Synchronous client
# ---------------------------------------------------------------------------


class SandcastleClient:
    """Synchronous client for the Sandcastle API.

    Usage::

        client = SandcastleClient(base_url="http://localhost:8080", api_key="sk-...")
        run = client.run("my-workflow", input={"key": "value"})
        print(run.run_id, run.status)

    Supports context manager protocol::

        with SandcastleClient() as client:
            run = client.run("my-workflow")
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        api_key: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        # Strip trailing slashes to avoid double-slash URLs when joining paths
        self._base_url = base_url.rstrip("/")
        self._has_api_key = bool(api_key)
        headers: dict[str, str] = {}
        if api_key:
            headers["X-API-Key"] = api_key
        self._client = httpx.Client(
            base_url=self._base_url,
            headers=headers,
            timeout=timeout,
        )

    def __repr__(self) -> str:
        key_str = "api_key=***" if self._has_api_key else "api_key=None"
        return f"SandcastleClient(base_url={self._base_url!r}, {key_str})"

    def __enter__(self) -> SandcastleClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    # -- Workflow execution --

    def run(
        self,
        workflow_name: str,
        *,
        input: Optional[dict[str, Any]] = None,
        max_cost_usd: Optional[float] = None,
        callback_url: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        wait: bool = False,
        poll_interval: float = 2.0,
    ) -> Run:
        """Run a workflow by name.

        Args:
            workflow_name: Name of the saved workflow to run.
            input: Input data for the workflow.
            max_cost_usd: Maximum cost limit for this run.
            callback_url: Webhook URL for completion notification.
            idempotency_key: Unique key to prevent duplicate runs.
            wait: If True, poll until the run reaches a terminal status.
            poll_interval: Seconds between polls when wait=True.

        Returns:
            Run object with run_id and status.
        """
        body: dict[str, Any] = {"workflow_name": workflow_name}
        if input is not None:
            body["input"] = input
        if max_cost_usd is not None:
            body["max_cost_usd"] = max_cost_usd
        if callback_url is not None:
            body["callback_url"] = callback_url
        if idempotency_key is not None:
            body["idempotency_key"] = idempotency_key

        resp = self._client.post("/api/workflows/run", json=body)
        data = _extract_data(resp)
        result = _parse_run(data)

        if wait and result.status not in _TERMINAL_STATUSES:
            result = self._poll_until_done(result.run_id, poll_interval)

        return result

    def run_yaml(
        self,
        yaml_content: str,
        *,
        input: Optional[dict[str, Any]] = None,
        max_cost_usd: Optional[float] = None,
        callback_url: Optional[str] = None,
        wait: bool = False,
        poll_interval: float = 2.0,
    ) -> Run:
        """Run a workflow from raw YAML content.

        Args:
            yaml_content: Raw YAML workflow definition.
            input: Input data for the workflow.
            max_cost_usd: Maximum cost limit for this run.
            callback_url: Webhook URL for completion notification.
            wait: If True, poll until the run reaches a terminal status.
            poll_interval: Seconds between polls when wait=True.

        Returns:
            Run object with run_id and status.
        """
        body: dict[str, Any] = {"workflow": yaml_content}
        if input is not None:
            body["input"] = input
        if max_cost_usd is not None:
            body["max_cost_usd"] = max_cost_usd
        if callback_url is not None:
            body["callback_url"] = callback_url

        resp = self._client.post("/api/workflows/run", json=body)
        data = _extract_data(resp)
        result = _parse_run(data)

        if wait and result.status not in _TERMINAL_STATUSES:
            result = self._poll_until_done(result.run_id, poll_interval)

        return result

    def _poll_until_done(
        self, run_id: str, poll_interval: float, max_wait: float = 3600.0,
    ) -> Run:
        """Poll get_run until the run reaches a terminal status.

        Args:
            run_id: Run UUID to poll.
            poll_interval: Seconds between polls.
            max_wait: Maximum total wait time in seconds (default 1 hour).

        Raises:
            TimeoutError: If max_wait is exceeded without reaching a terminal status.
        """
        deadline = time.monotonic() + max_wait
        while True:
            run = self.get_run(run_id)
            if run.status in _TERMINAL_STATUSES:
                return run
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Run '{run_id}' did not reach terminal status within "
                    f"{max_wait}s (last status: {run.status})"
                )
            time.sleep(poll_interval)

    # -- Run operations --

    def get_run(self, run_id: str) -> Run:
        """Get the status and details of a specific run.

        Args:
            run_id: The UUID of the run to retrieve.

        Returns:
            Run object with full details including steps.
        """
        _validate_path_param(run_id, "run_id")
        resp = self._client.get(f"/api/runs/{run_id}")
        data = _extract_data(resp)
        return _parse_run(data)

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        """Cancel a queued or running workflow.

        Args:
            run_id: The UUID of the run to cancel.

        Returns:
            Dict with ``cancelled`` and ``run_id`` keys.
        """
        _validate_path_param(run_id, "run_id")
        resp = self._client.post(f"/api/runs/{run_id}/cancel")
        return _extract_data(resp)

    def replay(self, run_id: str, from_step: str) -> Run:
        """Replay a run from a specific step.

        Args:
            run_id: The UUID of the original run.
            from_step: Step ID to replay from.

        Returns:
            Run object for the new replay run.
        """
        _validate_path_param(run_id, "run_id")
        resp = self._client.post(
            f"/api/runs/{run_id}/replay",
            json={"from_step": from_step},
        )
        data = _extract_data(resp)
        return _parse_run(data)

    def fork(
        self,
        run_id: str,
        from_step: str,
        changes: Optional[dict[str, Any]] = None,
    ) -> Run:
        """Fork a run from a specific step with overrides.

        Args:
            run_id: The UUID of the original run.
            from_step: Step ID to fork from.
            changes: Step overrides (e.g. model, prompt).

        Returns:
            Run object for the new forked run.
        """
        _validate_path_param(run_id, "run_id")
        body: dict[str, Any] = {"from_step": from_step}
        if changes is not None:
            body["changes"] = changes
        resp = self._client.post(f"/api/runs/{run_id}/fork", json=body)
        data = _extract_data(resp)
        return _parse_run(data)

    def list_runs(
        self,
        *,
        status: Optional[str] = None,
        workflow: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PaginatedList:
        """List workflow runs with optional filters and pagination.

        Args:
            status: Filter by run status (e.g. "completed", "failed").
            workflow: Filter by workflow name.
            limit: Max items to return (1-200).
            offset: Number of items to skip.

        Returns:
            PaginatedList of RunListItem objects.
        """
        _validate_pagination(limit, offset)
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status
        if workflow is not None:
            params["workflow"] = workflow

        resp = self._client.get("/api/runs", params=params)
        if resp.status_code >= 400:
            _extract_data(resp)  # will raise

        try:
            body = resp.json()
        except Exception:
            raise SandcastleError(
                resp.status_code, "INVALID_RESPONSE",
                f"Expected JSON response, got: {resp.text[:500]}"
            )

        data = body.get("data", [])
        meta = body.get("meta", {})

        items = [_parse_run_list_item(item) for item in data]
        return PaginatedList(
            items=items,
            total=meta.get("total", len(items)),
            limit=meta.get("limit", limit),
            offset=meta.get("offset", offset),
        )

    # -- SSE streaming --

    def stream(self, run_id: str) -> Generator[dict[str, Any], None, None]:
        """Stream live events for a run via SSE.

        Yields dicts with an ``_event`` key indicating the event type
        (``status``, ``step``, ``result``, ``error``).

        On connection errors mid-stream, yields a final error event with
        ``_event`` set to ``"stream_error"`` and a ``message`` key describing
        the failure, then stops iteration.

        Args:
            run_id: The UUID of the run to stream.

        Yields:
            Event dicts parsed from SSE.
        """
        _validate_path_param(run_id, "run_id")
        try:
            with self._client.stream("GET", f"/api/runs/{run_id}/stream") as resp:
                if resp.status_code >= 400:
                    resp.read()
                    _extract_data(resp)  # will raise

                event_type = ""
                data_lines: list[str] = []
                try:
                    for line in resp.iter_lines():
                        if line.startswith("event:"):
                            event_type = line[len("event:"):].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[len("data:"):].strip())
                        elif line == "" and data_lines:
                            data_buf = "\n".join(data_lines)
                            try:
                                parsed = json.loads(data_buf)
                            except json.JSONDecodeError:
                                parsed = {"raw": data_buf}
                            parsed["_event"] = event_type
                            yield parsed
                            event_type = ""
                            data_lines = []
                except httpx.StreamError as exc:
                    yield {
                        "_event": "stream_error",
                        "message": f"Stream interrupted: {exc}",
                    }
                    return

                # Flush remaining data if stream ended without trailing blank line
                if data_lines:
                    data_buf = "\n".join(data_lines)
                    try:
                        parsed = json.loads(data_buf)
                    except json.JSONDecodeError:
                        parsed = {"raw": data_buf}
                    parsed["_event"] = event_type
                    yield parsed
        except httpx.ConnectError as exc:
            raise SandcastleError(
                0, "CONNECTION_ERROR",
                f"Failed to connect to stream: {exc}",
            ) from exc
        except httpx.TimeoutException as exc:
            raise SandcastleError(
                0, "TIMEOUT_ERROR",
                f"Stream connection timed out: {exc}",
            ) from exc

    # -- Workflows --

    def list_workflows(self) -> list[Workflow]:
        """List available workflow definitions.

        Returns:
            List of Workflow objects.
        """
        resp = self._client.get("/api/workflows")
        data = _extract_data(resp)
        if isinstance(data, list):
            return [_parse_workflow(w) for w in data]
        return []

    def save_workflow(self, name: str, content: str) -> Workflow:
        """Save a workflow YAML file.

        Args:
            name: Workflow name (without .yaml extension).
            content: YAML content.

        Returns:
            Workflow object with metadata.
        """
        resp = self._client.post(
            "/api/workflows",
            json={"name": name, "content": content},
        )
        data = _extract_data(resp)
        return _parse_workflow(data)

    # -- Approvals --

    def list_approvals(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PaginatedList:
        """List approval requests with optional filters and pagination.

        Args:
            status: Filter by approval status (e.g. "pending", "approved").
            limit: Max items to return (1-200).
            offset: Number of items to skip.

        Returns:
            PaginatedList of Approval objects.
        """
        _validate_pagination(limit, offset)
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status

        resp = self._client.get("/api/approvals", params=params)
        if resp.status_code >= 400:
            _extract_data(resp)  # will raise

        try:
            body = resp.json()
        except Exception:
            raise SandcastleError(
                resp.status_code, "INVALID_RESPONSE",
                f"Expected JSON response, got: {resp.text[:500]}"
            )

        data = body.get("data", [])
        meta = body.get("meta", {})

        items = [_parse_approval(a) for a in data]
        return PaginatedList(
            items=items,
            total=meta.get("total", len(items)),
            limit=meta.get("limit", limit),
            offset=meta.get("offset", offset),
        )

    def approve(
        self,
        approval_id: str,
        *,
        output_data: Optional[dict[str, Any]] = None,
        comment: Optional[str] = None,
    ) -> dict[str, Any]:
        """Approve an approval gate and resume the workflow.

        Args:
            approval_id: The UUID of the approval to approve.
            output_data: Optional edited data to pass along with approval.
            comment: Optional reviewer comment.

        Returns:
            Dict with ``approved``, ``approval_id``, and ``run_id`` keys.
        """
        _validate_path_param(approval_id, "approval_id")
        body: dict[str, Any] = {}
        if output_data is not None:
            body["edited_data"] = output_data
        if comment is not None:
            body["comment"] = comment
        resp = self._client.post(
            f"/api/approvals/{approval_id}/approve",
            json=body if body else None,
        )
        return _extract_data(resp)

    def reject(
        self,
        approval_id: str,
        *,
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        """Reject an approval gate and fail the workflow.

        Args:
            approval_id: The UUID of the approval to reject.
            reason: Optional rejection reason.

        Returns:
            Dict with ``rejected``, ``approval_id``, and ``run_id`` keys.
        """
        _validate_path_param(approval_id, "approval_id")
        body: dict[str, Any] = {}
        if reason is not None:
            body["comment"] = reason
        resp = self._client.post(
            f"/api/approvals/{approval_id}/reject",
            json=body if body else None,
        )
        return _extract_data(resp)

    # -- Schedules --

    def list_schedules(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> PaginatedList:
        """List workflow schedules.

        Args:
            limit: Max items to return (1-200).
            offset: Number of items to skip.

        Returns:
            PaginatedList of Schedule objects.
        """
        _validate_pagination(limit, offset)
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        resp = self._client.get("/api/schedules", params=params)
        if resp.status_code >= 400:
            _extract_data(resp)

        try:
            body = resp.json()
        except Exception:
            raise SandcastleError(
                resp.status_code, "INVALID_RESPONSE",
                f"Expected JSON response, got: {resp.text[:500]}"
            )

        data = body.get("data", [])
        meta = body.get("meta", {})

        items = [_parse_schedule(s) for s in data]
        return PaginatedList(
            items=items,
            total=meta.get("total", len(items)),
            limit=meta.get("limit", limit),
            offset=meta.get("offset", offset),
        )

    def create_schedule(
        self,
        workflow_name: str,
        cron: str,
        *,
        input: Optional[dict[str, Any]] = None,
        enabled: bool = True,
    ) -> Schedule:
        """Create a scheduled workflow execution.

        Args:
            workflow_name: Name of the workflow to schedule.
            cron: Cron expression (e.g. "0 9 * * *").
            input: Input data for each scheduled run.
            enabled: Whether the schedule is active.

        Returns:
            Schedule object.
        """
        body: dict[str, Any] = {
            "workflow_name": workflow_name,
            "cron_expression": cron,
            "enabled": enabled,
        }
        if input is not None:
            body["input_data"] = input
        resp = self._client.post("/api/schedules", json=body)
        data = _extract_data(resp)
        return _parse_schedule(data)

    def update_schedule(
        self,
        schedule_id: str,
        *,
        enabled: Optional[bool] = None,
        cron: Optional[str] = None,
        input: Optional[dict[str, Any]] = None,
    ) -> Schedule:
        """Update a schedule.

        Args:
            schedule_id: The UUID of the schedule.
            enabled: Set schedule active/inactive.
            cron: New cron expression.
            input: New input data.

        Returns:
            Updated Schedule object.
        """
        _validate_path_param(schedule_id, "schedule_id")
        body: dict[str, Any] = {}
        if enabled is not None:
            body["enabled"] = enabled
        if cron is not None:
            body["cron_expression"] = cron
        if input is not None:
            body["input_data"] = input
        resp = self._client.patch(f"/api/schedules/{schedule_id}", json=body)
        data = _extract_data(resp)
        return _parse_schedule(data)

    def delete_schedule(self, schedule_id: str) -> dict[str, Any]:
        """Delete a workflow schedule.

        Args:
            schedule_id: The UUID of the schedule to delete.

        Returns:
            Dict with ``deleted`` and ``id`` keys.
        """
        _validate_path_param(schedule_id, "schedule_id")
        resp = self._client.delete(f"/api/schedules/{schedule_id}")
        return _extract_data(resp)

    # -- Health / Info --

    def health(self) -> HealthStatus:
        """Check the health of Sandcastle and its dependencies.

        Returns:
            HealthStatus object.
        """
        resp = self._client.get("/api/health")
        data = _extract_data(resp)
        return _parse_health(data)

    def runtime(self) -> RuntimeInfo:
        """Get runtime mode information.

        Returns:
            RuntimeInfo object.
        """
        resp = self._client.get("/api/runtime")
        data = _extract_data(resp)
        return _parse_runtime(data)

    def stats(self) -> Stats:
        """Get aggregated dashboard statistics.

        Returns:
            Stats object.
        """
        resp = self._client.get("/api/stats")
        data = _extract_data(resp)
        return _parse_stats(data)

    # --- Workflow as API ---

    def publish_workflow(self, workflow_name: str) -> dict[str, Any]:
        """Publish a workflow as a public API endpoint.

        Sets is_public=True on the production version so it can be called
        via :meth:`call_api`. Admin only.

        Args:
            workflow_name: Name of the workflow to publish.

        Returns:
            dict with endpoint_url, spec_url, example_curl, example_sdk, version.
        """
        resp = self._client.post(f"/api/workflows/{workflow_name}/publish")
        return _extract_data(resp)

    def call_api(
        self,
        workflow_name: str,
        input_data: dict[str, Any],
        *,
        callback_url: str | None = None,
        async_mode: bool = False,
    ) -> dict[str, Any]:
        """Call a published workflow via its public API endpoint.

        Args:
            workflow_name: Name of the published workflow.
            input_data: Input data dict to pass to the workflow.
            callback_url: Optional webhook URL for async completion notification.
            async_mode: If True, returns immediately with run_id (enqueues).
                        If False (default), waits for the run to complete.

        Returns:
            dict with run result (sync) or run_id (async).
        """
        headers: dict[str, str] = {}
        if async_mode:
            headers["Prefer"] = "respond-async"
        if callback_url:
            headers["X-Callback-URL"] = callback_url
        resp = self._client.post(
            f"/api/api/v1/{workflow_name}",
            json=input_data,
            headers=headers,
        )
        return _extract_data(resp)

    def get_workflow_api_spec(self, workflow_name: str) -> dict[str, Any]:
        """Get the OpenAPI-compatible spec for a published workflow.

        This is a public endpoint - no API key required.

        Args:
            workflow_name: Name of the published workflow.

        Returns:
            dict with input_schema, endpoint_url, auth_method, etc.
        """
        resp = self._client.get(f"/api/api/v1/{workflow_name}/spec")
        return _extract_data(resp)

    def get_workflow_api_usage(
        self, workflow_name: str, days: int = 30
    ) -> dict[str, Any]:
        """Get usage statistics for a published workflow API. Admin only.

        Args:
            workflow_name: Name of the published workflow.
            days: Number of days to include in the report (default 30).

        Returns:
            dict with total_runs, successful_runs, failed_runs, total_cost_usd,
            avg_duration_seconds, runs_by_day.
        """
        resp = self._client.get(
            f"/api/api/v1/{workflow_name}/usage", params={"days": days}
        )
        return _extract_data(resp)


# ---------------------------------------------------------------------------
# Async client
# ---------------------------------------------------------------------------


class AsyncSandcastleClient:
    """Asynchronous client for the Sandcastle API.

    Usage::

        async with AsyncSandcastleClient(base_url="http://localhost:8080") as client:
            run = await client.run("my-workflow", input={"key": "value"})
            print(run.run_id, run.status)
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        api_key: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        # Strip trailing slashes to avoid double-slash URLs when joining paths
        self._base_url = base_url.rstrip("/")
        self._has_api_key = bool(api_key)
        headers: dict[str, str] = {}
        if api_key:
            headers["X-API-Key"] = api_key
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=timeout,
        )

    def __repr__(self) -> str:
        key_str = "api_key=***" if self._has_api_key else "api_key=None"
        return f"AsyncSandcastleClient(base_url={self._base_url!r}, {key_str})"

    async def __aenter__(self) -> AsyncSandcastleClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    # -- Workflow execution --

    async def run(
        self,
        workflow_name: str,
        *,
        input: Optional[dict[str, Any]] = None,
        max_cost_usd: Optional[float] = None,
        callback_url: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        wait: bool = False,
        poll_interval: float = 2.0,
    ) -> Run:
        """Run a workflow by name.

        Args:
            workflow_name: Name of the saved workflow to run.
            input: Input data for the workflow.
            max_cost_usd: Maximum cost limit for this run.
            callback_url: Webhook URL for completion notification.
            idempotency_key: Unique key to prevent duplicate runs.
            wait: If True, poll until the run reaches a terminal status.
            poll_interval: Seconds between polls when wait=True.

        Returns:
            Run object with run_id and status.
        """
        body: dict[str, Any] = {"workflow_name": workflow_name}
        if input is not None:
            body["input"] = input
        if max_cost_usd is not None:
            body["max_cost_usd"] = max_cost_usd
        if callback_url is not None:
            body["callback_url"] = callback_url
        if idempotency_key is not None:
            body["idempotency_key"] = idempotency_key

        resp = await self._client.post("/api/workflows/run", json=body)
        data = _extract_data(resp)
        result = _parse_run(data)

        if wait and result.status not in _TERMINAL_STATUSES:
            result = await self._poll_until_done(result.run_id, poll_interval)

        return result

    async def run_yaml(
        self,
        yaml_content: str,
        *,
        input: Optional[dict[str, Any]] = None,
        max_cost_usd: Optional[float] = None,
        callback_url: Optional[str] = None,
        wait: bool = False,
        poll_interval: float = 2.0,
    ) -> Run:
        """Run a workflow from raw YAML content.

        Args:
            yaml_content: Raw YAML workflow definition.
            input: Input data for the workflow.
            max_cost_usd: Maximum cost limit for this run.
            callback_url: Webhook URL for completion notification.
            wait: If True, poll until the run reaches a terminal status.
            poll_interval: Seconds between polls when wait=True.

        Returns:
            Run object with run_id and status.
        """
        body: dict[str, Any] = {"workflow": yaml_content}
        if input is not None:
            body["input"] = input
        if max_cost_usd is not None:
            body["max_cost_usd"] = max_cost_usd
        if callback_url is not None:
            body["callback_url"] = callback_url

        resp = await self._client.post("/api/workflows/run", json=body)
        data = _extract_data(resp)
        result = _parse_run(data)

        if wait and result.status not in _TERMINAL_STATUSES:
            result = await self._poll_until_done(result.run_id, poll_interval)

        return result

    async def _poll_until_done(
        self, run_id: str, poll_interval: float, max_wait: float = 3600.0,
    ) -> Run:
        """Poll get_run until the run reaches a terminal status.

        Args:
            run_id: Run UUID to poll.
            poll_interval: Seconds between polls.
            max_wait: Maximum total wait time in seconds (default 1 hour).

        Raises:
            TimeoutError: If max_wait is exceeded without reaching a terminal status.
        """
        import asyncio

        deadline = time.monotonic() + max_wait
        while True:
            run = await self.get_run(run_id)
            if run.status in _TERMINAL_STATUSES:
                return run
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Run '{run_id}' did not reach terminal status within "
                    f"{max_wait}s (last status: {run.status})"
                )
            await asyncio.sleep(poll_interval)

    # -- Run operations --

    async def get_run(self, run_id: str) -> Run:
        """Get the status and details of a specific run.

        Args:
            run_id: The UUID of the run to retrieve.

        Returns:
            Run object with full details including steps.
        """
        _validate_path_param(run_id, "run_id")
        resp = await self._client.get(f"/api/runs/{run_id}")
        data = _extract_data(resp)
        return _parse_run(data)

    async def cancel_run(self, run_id: str) -> dict[str, Any]:
        """Cancel a queued or running workflow.

        Args:
            run_id: The UUID of the run to cancel.

        Returns:
            Dict with ``cancelled`` and ``run_id`` keys.
        """
        _validate_path_param(run_id, "run_id")
        resp = await self._client.post(f"/api/runs/{run_id}/cancel")
        return _extract_data(resp)

    async def replay(self, run_id: str, from_step: str) -> Run:
        """Replay a run from a specific step.

        Args:
            run_id: The UUID of the original run.
            from_step: Step ID to replay from.

        Returns:
            Run object for the new replay run.
        """
        _validate_path_param(run_id, "run_id")
        resp = await self._client.post(
            f"/api/runs/{run_id}/replay",
            json={"from_step": from_step},
        )
        data = _extract_data(resp)
        return _parse_run(data)

    async def fork(
        self,
        run_id: str,
        from_step: str,
        changes: Optional[dict[str, Any]] = None,
    ) -> Run:
        """Fork a run from a specific step with overrides.

        Args:
            run_id: The UUID of the original run.
            from_step: Step ID to fork from.
            changes: Step overrides (e.g. model, prompt).

        Returns:
            Run object for the new forked run.
        """
        _validate_path_param(run_id, "run_id")
        body: dict[str, Any] = {"from_step": from_step}
        if changes is not None:
            body["changes"] = changes
        resp = await self._client.post(f"/api/runs/{run_id}/fork", json=body)
        data = _extract_data(resp)
        return _parse_run(data)

    async def list_runs(
        self,
        *,
        status: Optional[str] = None,
        workflow: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PaginatedList:
        """List workflow runs with optional filters and pagination.

        Args:
            status: Filter by run status (e.g. "completed", "failed").
            workflow: Filter by workflow name.
            limit: Max items to return (1-200).
            offset: Number of items to skip.

        Returns:
            PaginatedList of RunListItem objects.
        """
        _validate_pagination(limit, offset)
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status
        if workflow is not None:
            params["workflow"] = workflow

        resp = await self._client.get("/api/runs", params=params)
        if resp.status_code >= 400:
            _extract_data(resp)

        try:
            body = resp.json()
        except Exception:
            raise SandcastleError(
                resp.status_code, "INVALID_RESPONSE",
                f"Expected JSON response, got: {resp.text[:500]}"
            )

        data = body.get("data", [])
        meta = body.get("meta", {})

        items = [_parse_run_list_item(item) for item in data]
        return PaginatedList(
            items=items,
            total=meta.get("total", len(items)),
            limit=meta.get("limit", limit),
            offset=meta.get("offset", offset),
        )

    # -- SSE streaming --

    async def stream(
        self, run_id: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream live events for a run via SSE.

        Yields dicts with an ``_event`` key indicating the event type
        (``status``, ``step``, ``result``, ``error``).

        On connection errors mid-stream, yields a final error event with
        ``_event`` set to ``"stream_error"`` and a ``message`` key describing
        the failure, then stops iteration.

        Args:
            run_id: The UUID of the run to stream.

        Yields:
            Event dicts parsed from SSE.
        """
        _validate_path_param(run_id, "run_id")
        try:
            async with self._client.stream("GET", f"/api/runs/{run_id}/stream") as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    _extract_data(resp)  # will raise

                event_type = ""
                data_lines: list[str] = []
                try:
                    async for line in resp.aiter_lines():
                        if line.startswith("event:"):
                            event_type = line[len("event:"):].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[len("data:"):].strip())
                        elif line == "" and data_lines:
                            data_buf = "\n".join(data_lines)
                            try:
                                parsed = json.loads(data_buf)
                            except json.JSONDecodeError:
                                parsed = {"raw": data_buf}
                            parsed["_event"] = event_type
                            yield parsed
                            event_type = ""
                            data_lines = []
                except httpx.StreamError as exc:
                    yield {
                        "_event": "stream_error",
                        "message": f"Stream interrupted: {exc}",
                    }
                    return

                # Flush remaining data if stream ended without trailing blank line
                if data_lines:
                    data_buf = "\n".join(data_lines)
                    try:
                        parsed = json.loads(data_buf)
                    except json.JSONDecodeError:
                        parsed = {"raw": data_buf}
                    parsed["_event"] = event_type
                    yield parsed
        except httpx.ConnectError as exc:
            raise SandcastleError(
                0, "CONNECTION_ERROR",
                f"Failed to connect to stream: {exc}",
            ) from exc
        except httpx.TimeoutException as exc:
            raise SandcastleError(
                0, "TIMEOUT_ERROR",
                f"Stream connection timed out: {exc}",
            ) from exc

    # -- Workflows --

    async def list_workflows(self) -> list[Workflow]:
        """List available workflow definitions.

        Returns:
            List of Workflow objects.
        """
        resp = await self._client.get("/api/workflows")
        data = _extract_data(resp)
        if isinstance(data, list):
            return [_parse_workflow(w) for w in data]
        return []

    async def save_workflow(self, name: str, content: str) -> Workflow:
        """Save a workflow YAML file.

        Args:
            name: Workflow name (without .yaml extension).
            content: YAML content.

        Returns:
            Workflow object with metadata.
        """
        resp = await self._client.post(
            "/api/workflows",
            json={"name": name, "content": content},
        )
        data = _extract_data(resp)
        return _parse_workflow(data)

    # -- Approvals --

    async def list_approvals(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PaginatedList:
        """List approval requests with optional filters and pagination.

        Args:
            status: Filter by approval status (e.g. "pending", "approved").
            limit: Max items to return (1-200).
            offset: Number of items to skip.

        Returns:
            PaginatedList of Approval objects.
        """
        _validate_pagination(limit, offset)
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status

        resp = await self._client.get("/api/approvals", params=params)
        if resp.status_code >= 400:
            _extract_data(resp)  # will raise

        try:
            body = resp.json()
        except Exception:
            raise SandcastleError(
                resp.status_code, "INVALID_RESPONSE",
                f"Expected JSON response, got: {resp.text[:500]}"
            )

        data = body.get("data", [])
        meta = body.get("meta", {})

        items = [_parse_approval(a) for a in data]
        return PaginatedList(
            items=items,
            total=meta.get("total", len(items)),
            limit=meta.get("limit", limit),
            offset=meta.get("offset", offset),
        )

    async def approve(
        self,
        approval_id: str,
        *,
        output_data: Optional[dict[str, Any]] = None,
        comment: Optional[str] = None,
    ) -> dict[str, Any]:
        """Approve an approval gate and resume the workflow.

        Args:
            approval_id: The UUID of the approval to approve.
            output_data: Optional edited data to pass along with approval.
            comment: Optional reviewer comment.

        Returns:
            Dict with ``approved``, ``approval_id``, and ``run_id`` keys.
        """
        _validate_path_param(approval_id, "approval_id")
        body: dict[str, Any] = {}
        if output_data is not None:
            body["edited_data"] = output_data
        if comment is not None:
            body["comment"] = comment
        resp = await self._client.post(
            f"/api/approvals/{approval_id}/approve",
            json=body if body else None,
        )
        return _extract_data(resp)

    async def reject(
        self,
        approval_id: str,
        *,
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        """Reject an approval gate and fail the workflow.

        Args:
            approval_id: The UUID of the approval to reject.
            reason: Optional rejection reason.

        Returns:
            Dict with ``rejected``, ``approval_id``, and ``run_id`` keys.
        """
        _validate_path_param(approval_id, "approval_id")
        body: dict[str, Any] = {}
        if reason is not None:
            body["comment"] = reason
        resp = await self._client.post(
            f"/api/approvals/{approval_id}/reject",
            json=body if body else None,
        )
        return _extract_data(resp)

    # -- Schedules --

    async def list_schedules(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> PaginatedList:
        """List workflow schedules.

        Args:
            limit: Max items to return (1-200).
            offset: Number of items to skip.

        Returns:
            PaginatedList of Schedule objects.
        """
        _validate_pagination(limit, offset)
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        resp = await self._client.get("/api/schedules", params=params)
        if resp.status_code >= 400:
            _extract_data(resp)

        try:
            body = resp.json()
        except Exception:
            raise SandcastleError(
                resp.status_code, "INVALID_RESPONSE",
                f"Expected JSON response, got: {resp.text[:500]}"
            )

        data = body.get("data", [])
        meta = body.get("meta", {})

        items = [_parse_schedule(s) for s in data]
        return PaginatedList(
            items=items,
            total=meta.get("total", len(items)),
            limit=meta.get("limit", limit),
            offset=meta.get("offset", offset),
        )

    async def create_schedule(
        self,
        workflow_name: str,
        cron: str,
        *,
        input: Optional[dict[str, Any]] = None,
        enabled: bool = True,
    ) -> Schedule:
        """Create a scheduled workflow execution.

        Args:
            workflow_name: Name of the workflow to schedule.
            cron: Cron expression (e.g. "0 9 * * *").
            input: Input data for each scheduled run.
            enabled: Whether the schedule is active.

        Returns:
            Schedule object.
        """
        body: dict[str, Any] = {
            "workflow_name": workflow_name,
            "cron_expression": cron,
            "enabled": enabled,
        }
        if input is not None:
            body["input_data"] = input
        resp = await self._client.post("/api/schedules", json=body)
        data = _extract_data(resp)
        return _parse_schedule(data)

    async def update_schedule(
        self,
        schedule_id: str,
        *,
        enabled: Optional[bool] = None,
        cron: Optional[str] = None,
        input: Optional[dict[str, Any]] = None,
    ) -> Schedule:
        """Update a schedule.

        Args:
            schedule_id: The UUID of the schedule.
            enabled: Set schedule active/inactive.
            cron: New cron expression.
            input: New input data.

        Returns:
            Updated Schedule object.
        """
        _validate_path_param(schedule_id, "schedule_id")
        body: dict[str, Any] = {}
        if enabled is not None:
            body["enabled"] = enabled
        if cron is not None:
            body["cron_expression"] = cron
        if input is not None:
            body["input_data"] = input
        resp = await self._client.patch(f"/api/schedules/{schedule_id}", json=body)
        data = _extract_data(resp)
        return _parse_schedule(data)

    async def delete_schedule(self, schedule_id: str) -> dict[str, Any]:
        """Delete a workflow schedule.

        Args:
            schedule_id: The UUID of the schedule to delete.

        Returns:
            Dict with ``deleted`` and ``id`` keys.
        """
        _validate_path_param(schedule_id, "schedule_id")
        resp = await self._client.delete(f"/api/schedules/{schedule_id}")
        return _extract_data(resp)

    # -- Health / Info --

    async def health(self) -> HealthStatus:
        """Check the health of Sandcastle and its dependencies.

        Returns:
            HealthStatus object.
        """
        resp = await self._client.get("/api/health")
        data = _extract_data(resp)
        return _parse_health(data)

    async def runtime(self) -> RuntimeInfo:
        """Get runtime mode information.

        Returns:
            RuntimeInfo object.
        """
        resp = await self._client.get("/api/runtime")
        data = _extract_data(resp)
        return _parse_runtime(data)

    async def stats(self) -> Stats:
        """Get aggregated dashboard statistics.

        Returns:
            Stats object.
        """
        resp = await self._client.get("/api/stats")
        data = _extract_data(resp)
        return _parse_stats(data)

    # --- Workflow as API ---

    async def publish_workflow(self, workflow_name: str) -> dict[str, Any]:
        """Publish a workflow as a public API endpoint.

        Sets is_public=True on the production version so it can be called
        via :meth:`call_api`. Admin only.

        Args:
            workflow_name: Name of the workflow to publish.

        Returns:
            dict with endpoint_url, spec_url, example_curl, example_sdk, version.
        """
        resp = await self._client.post(f"/api/workflows/{workflow_name}/publish")
        return _extract_data(resp)

    async def call_api(
        self,
        workflow_name: str,
        input_data: dict[str, Any],
        *,
        callback_url: str | None = None,
        async_mode: bool = False,
    ) -> dict[str, Any]:
        """Call a published workflow via its public API endpoint.

        Args:
            workflow_name: Name of the published workflow.
            input_data: Input data dict to pass to the workflow.
            callback_url: Optional webhook URL for async completion notification.
            async_mode: If True, returns immediately with run_id (enqueues).
                        If False (default), waits for the run to complete.

        Returns:
            dict with run result (sync) or run_id (async).
        """
        headers: dict[str, str] = {}
        if async_mode:
            headers["Prefer"] = "respond-async"
        if callback_url:
            headers["X-Callback-URL"] = callback_url
        resp = await self._client.post(
            f"/api/api/v1/{workflow_name}",
            json=input_data,
            headers=headers,
        )
        return _extract_data(resp)

    async def get_workflow_api_spec(self, workflow_name: str) -> dict[str, Any]:
        """Get the OpenAPI-compatible spec for a published workflow.

        This is a public endpoint - no API key required.

        Args:
            workflow_name: Name of the published workflow.

        Returns:
            dict with input_schema, endpoint_url, auth_method, etc.
        """
        resp = await self._client.get(f"/api/api/v1/{workflow_name}/spec")
        return _extract_data(resp)

    async def get_workflow_api_usage(
        self, workflow_name: str, days: int = 30
    ) -> dict[str, Any]:
        """Get usage statistics for a published workflow API. Admin only.

        Args:
            workflow_name: Name of the published workflow.
            days: Number of days to include in the report (default 30).

        Returns:
            dict with total_runs, successful_runs, failed_runs, total_cost_usd,
            avg_duration_seconds, runs_by_day.
        """
        resp = await self._client.get(
            f"/api/api/v1/{workflow_name}/usage", params={"days": days}
        )
        return _extract_data(resp)
