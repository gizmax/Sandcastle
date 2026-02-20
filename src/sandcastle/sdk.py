"""Sandcastle Python SDK - sync and async clients for the Sandcastle API."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Generator, Iterator, Optional

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
    "completed", "failed", "partial", "cancelled",
    "budget_exceeded", "awaiting_approval",
})


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
            message = response.text
        raise SandcastleError(response.status_code, code, message)

    body = response.json()
    return body.get("data", body)


def _parse_sse_lines(raw: str) -> Iterator[dict[str, Any]]:
    """Parse raw SSE text into event dicts."""
    event_type = ""
    data_buf = ""

    for line in raw.split("\n"):
        if line.startswith("event:"):
            event_type = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_buf = line[len("data:"):].strip()
        elif line == "" and data_buf:
            try:
                parsed = json.loads(data_buf)
            except json.JSONDecodeError:
                parsed = {"raw": data_buf}
            parsed["_event"] = event_type
            yield parsed
            event_type = ""
            data_buf = ""


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
        headers: dict[str, str] = {}
        if api_key:
            headers["X-API-Key"] = api_key
        self._client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=timeout,
        )

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

    def _poll_until_done(self, run_id: str, poll_interval: float) -> Run:
        """Poll get_run until the run reaches a terminal status."""
        while True:
            time.sleep(poll_interval)
            run = self.get_run(run_id)
            if run.status in _TERMINAL_STATUSES:
                return run

    # -- Run operations --

    def get_run(self, run_id: str) -> Run:
        """Get the status and details of a specific run.

        Args:
            run_id: The UUID of the run to retrieve.

        Returns:
            Run object with full details including steps.
        """
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
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status
        if workflow is not None:
            params["workflow"] = workflow

        resp = self._client.get("/api/runs", params=params)
        body = resp.json()
        if resp.status_code >= 400:
            _extract_data(resp)  # will raise

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

        Args:
            run_id: The UUID of the run to stream.

        Yields:
            Event dicts parsed from SSE.
        """
        with self._client.stream("GET", f"/api/runs/{run_id}/stream") as resp:
            if resp.status_code >= 400:
                resp.read()
                _extract_data(resp)  # will raise

            event_type = ""
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    event_type = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data_str = line[len("data:"):].strip()
                    try:
                        parsed = json.loads(data_str)
                    except json.JSONDecodeError:
                        parsed = {"raw": data_str}
                    parsed["_event"] = event_type
                    yield parsed
                    event_type = ""

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
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        resp = self._client.get("/api/schedules", params=params)
        body = resp.json()
        if resp.status_code >= 400:
            _extract_data(resp)

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
        headers: dict[str, str] = {}
        if api_key:
            headers["X-API-Key"] = api_key
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=timeout,
        )

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

    async def _poll_until_done(self, run_id: str, poll_interval: float) -> Run:
        """Poll get_run until the run reaches a terminal status."""
        import asyncio

        while True:
            await asyncio.sleep(poll_interval)
            run = await self.get_run(run_id)
            if run.status in _TERMINAL_STATUSES:
                return run

    # -- Run operations --

    async def get_run(self, run_id: str) -> Run:
        """Get the status and details of a specific run.

        Args:
            run_id: The UUID of the run to retrieve.

        Returns:
            Run object with full details including steps.
        """
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
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status
        if workflow is not None:
            params["workflow"] = workflow

        resp = await self._client.get("/api/runs", params=params)
        body = resp.json()
        if resp.status_code >= 400:
            _extract_data(resp)

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

    async def stream(self, run_id: str):
        """Stream live events for a run via SSE.

        Yields dicts with an ``_event`` key indicating the event type
        (``status``, ``step``, ``result``, ``error``).

        Args:
            run_id: The UUID of the run to stream.

        Yields:
            Event dicts parsed from SSE.
        """
        async with self._client.stream("GET", f"/api/runs/{run_id}/stream") as resp:
            if resp.status_code >= 400:
                await resp.aread()
                _extract_data(resp)  # will raise

            event_type = ""
            async for line in resp.aiter_lines():
                if line.startswith("event:"):
                    event_type = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data_str = line[len("data:"):].strip()
                    try:
                        parsed = json.loads(data_str)
                    except json.JSONDecodeError:
                        parsed = {"raw": data_str}
                    parsed["_event"] = event_type
                    yield parsed
                    event_type = ""

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
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        resp = await self._client.get("/api/schedules", params=params)
        body = resp.json()
        if resp.status_code >= 400:
            _extract_data(resp)

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
