"""Focused regression coverage for engine robustness wave C2."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from sandcastle.config import settings
from sandcastle.engine.dag import (
    CodeConfig,
    HttpConfig,
    RaceConfig,
    SensorConfig,
    StepDefinition,
    WorkflowDefinition,
    build_plan,
    parse_yaml_string,
)
from sandcastle.engine.executor import (
    RunContext,
    StepResult,
    _execute_code_step,
    _execute_http_step,
    _execute_race_step,
    _execute_sensor_step,
    _validate_browser_url_with_ssrf_guard,
    execute_workflow,
)


class _DNSLoop:
    def __init__(self, ip: str = "93.184.216.34") -> None:
        self.ip = ip
        self.calls: list[tuple[str, int]] = []

    async def getaddrinfo(self, host: str, port: int):
        self.calls.append((host, port))
        return [(0, 0, 0, "", (self.ip, port))]


class _Response:
    status_code = 200
    text = "{}"

    def json(self):
        return {}


class _AsyncClient:
    instances: list["_AsyncClient"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.requests: list[dict] = []
        self.__class__.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, **kwargs):
        self.requests.append(kwargs)
        return _Response()


@pytest.mark.asyncio
async def test_ssrf_preflights_use_async_getaddrinfo(monkeypatch):
    """Browser, HTTP, and sensor preflights never call blocking socket DNS."""
    import sandcastle.engine.executor as executor

    loop = _DNSLoop()
    monkeypatch.setattr(executor.asyncio, "get_running_loop", lambda: loop)
    monkeypatch.setattr("httpx.AsyncClient", _AsyncClient)
    _AsyncClient.instances.clear()

    assert await _validate_browser_url_with_ssrf_guard("https://browser.example/path")

    http_result = await _execute_http_step(
        StepDefinition(id="http", type="http", http_config=HttpConfig(url="https://http.example")),
        RunContext(run_id="http", input={}),
    )
    sensor_result = await _execute_sensor_step(
        StepDefinition(
            id="sensor",
            type="sensor",
            sensor_config=SensorConfig(
                url="https://sensor.example",
                condition="status_code == 200",
                check_interval=1,
                timeout=2,
            ),
        ),
        RunContext(run_id="sensor", input={}),
    )

    assert http_result.status == sensor_result.status == "completed"
    assert loop.calls == [
        ("browser.example", 443),
        ("http.example", 443),
        ("sensor.example", 443),
    ]


@pytest.mark.asyncio
async def test_sensor_pins_validated_address_for_all_polls(monkeypatch):
    """A sensor only dials the validated public address after DNS preflight."""
    import sandcastle.engine.executor as executor

    loop = _DNSLoop("93.184.216.34")
    pinned_hosts: list[dict[str, str]] = []
    marker = object()
    monkeypatch.setattr(executor.asyncio, "get_running_loop", lambda: loop)
    monkeypatch.setattr("httpx.AsyncClient", _AsyncClient)
    monkeypatch.setattr(
        executor,
        "_build_pinned_transport",
        lambda hosts: pinned_hosts.append(hosts) or marker,
    )
    _AsyncClient.instances.clear()

    result = await _execute_sensor_step(
        StepDefinition(
            id="sensor",
            type="sensor",
            sensor_config=SensorConfig(
                url="https://rebind.example/status",
                condition="status_code == 200",
                check_interval=1,
                timeout=2,
            ),
        ),
        RunContext(run_id="sensor", input={}),
    )

    assert result.status == "completed"
    assert loop.calls == [("rebind.example", 443)]
    assert pinned_hosts == [{"rebind.example": "93.184.216.34"}]
    assert _AsyncClient.instances[0].kwargs["transport"] is marker


@pytest.mark.asyncio
async def test_fan_out_splits_budget_and_surfaces_aggregate_overshoot(monkeypatch):
    """Four children receive $2.50 each and the parent ends budget_exceeded."""
    import sandcastle.engine.executor as executor

    workflow = parse_yaml_string(
        """
name: fan-out-budget
steps:
  - id: work
    prompt: work
    parallel_over: "{input.items}"
"""
    )
    seen_budgets: list[float | None] = []

    async def run_item(step, context, sandbox, storage, **kwargs):
        seen_budgets.append(context.max_cost_usd)
        return StepResult(step_id=step.id, output=context.input["_item"], cost_usd=3.0)

    monkeypatch.setattr(executor, "execute_step_with_retry", run_item)
    monkeypatch.setattr(executor, "_emit_audit_event", AsyncMock())
    monkeypatch.setattr(executor, "_save_run_step", AsyncMock())
    monkeypatch.setattr(executor, "_save_checkpoint", AsyncMock())
    monkeypatch.setattr(executor, "get_sandshore_runtime", MagicMock())

    result = await execute_workflow(
        workflow=workflow,
        plan=build_plan(workflow),
        input_data={"items": [1, 2, 3, 4]},
        max_cost_usd=10.0,
        storage=MagicMock(),
    )

    assert seen_budgets == [2.5, 2.5, 2.5, 2.5]
    assert result.status == "budget_exceeded"
    assert result.total_cost_usd == 12.0


@pytest.mark.asyncio
async def test_subprocess_infra_error_fails_closed_by_default(monkeypatch):
    """Isolation infrastructure errors cannot silently fall back to exec()."""
    from sandcastle.engine.code_subprocess_runner import SubprocessInfraError

    monkeypatch.setattr(settings, "code_steps_out_of_process", True)
    monkeypatch.setattr(settings, "code_steps_allow_inprocess_fallback", False)
    monkeypatch.setattr(
        "sandcastle.engine.code_subprocess_runner.run_code_in_subprocess",
        AsyncMock(side_effect=SubprocessInfraError("spawn failed")),
    )

    result = await _execute_code_step(
        StepDefinition(id="code", type="code", code_config=CodeConfig(code="result = 1")),
        RunContext(run_id="code", input={}, admin_trusted=True),
    )

    assert result.status == "failed"
    assert "In-process fallback is disabled" in result.error


@pytest.mark.asyncio
async def test_opted_in_code_fallback_emits_audit_event(monkeypatch):
    """The explicit compatibility fallback stays observable in the audit trail."""
    import sandcastle.engine.executor as executor
    from sandcastle.engine.code_subprocess_runner import SubprocessInfraError

    audit_event = AsyncMock()
    monkeypatch.setattr(settings, "code_steps_out_of_process", True)
    monkeypatch.setattr(settings, "code_steps_allow_inprocess_fallback", True)
    monkeypatch.setattr(
        "sandcastle.engine.code_subprocess_runner.run_code_in_subprocess",
        AsyncMock(side_effect=SubprocessInfraError("spawn failed")),
    )
    monkeypatch.setattr(executor, "_emit_audit_event", audit_event)

    result = await _execute_code_step(
        StepDefinition(id="code", type="code", code_config=CodeConfig(code="result = 7")),
        RunContext(run_id="code", input={}, admin_trusted=True),
    )

    assert result.status == "completed"
    assert result.output == 7
    audit_event.assert_awaited_once_with(
        "code_step.inprocess_fallback",
        run_id="code",
        actor_id="system",
        payload={"step_id": "code", "reason": "spawn failed"},
    )


@pytest.mark.asyncio
async def test_race_branch_preserves_memory_scope_and_resolved_context(monkeypatch):
    """A race branch receives the memory context needed for a memory write."""
    import sandcastle.engine.executor as executor

    branch_step = StepDefinition(id="write", prompt="write")
    race = StepDefinition(
        id="race",
        type="race",
        race_config=RaceConfig(branches=[["write"]]),
    )
    workflow = WorkflowDefinition(
        name="race-memory",
        description="test",
        default_model="sonnet",
        default_max_turns=1,
        default_timeout=60,
        steps=[branch_step, race],
    )
    memory_config = SimpleNamespace(auto_inject=False, admit_threshold=0, enrich=False)
    parent = RunContext(
        run_id="race",
        input={},
        workflow_name="race-memory",
        memories=[{"content": "existing"}],
        _memory_scope_id="workflow:race-memory",
        _memory_config=memory_config,
        _resolved_context="resolved context",
    )
    saved_scopes: list[str] = []

    async def write_memory(step, context, sandbox, storage, **kwargs):
        assert context._resolved_context == "resolved context"
        assert context._memory_config is memory_config
        assert context.memories == [{"content": "existing"}]
        saved_scopes.append(context._memory_scope_id)
        return StepResult(step_id=step.id, output="saved")

    monkeypatch.setattr(executor, "execute_step_with_retry", write_memory)

    result = await _execute_race_step(race, parent, MagicMock(), MagicMock(), workflow, 0)

    assert result.status == "completed"
    assert saved_scopes == ["workflow:race-memory"]


@pytest.mark.asyncio
async def test_sandshore_allows_local_model_under_eu_residency(monkeypatch):
    """Sandshore uses the same EU allowance as regular LLM execution."""
    import sandcastle.engine.sandshore as sandshore

    runtime = sandshore.SandshoreRuntime.__new__(sandshore.SandshoreRuntime)

    async def stream_once(request, cancel_event=None):
        yield SimpleNamespace(event="result", data={"type": "result"})

    runtime._stream_backend_once = stream_once
    monkeypatch.setattr(settings, "data_residency", "eu")
    monkeypatch.setattr(
        "sandcastle.engine.providers.resolve_model",
        lambda model: SimpleNamespace(region="local", api_key_env="LOCAL"),
    )

    events = [event async for event in runtime._stream_backend({"model": "local/model"})]

    assert len(events) == 1


def test_runtime_pool_never_evicts_an_active_query(monkeypatch):
    """Pool pressure may exceed the cap temporarily rather than closing in-use work."""
    import sandcastle.engine.sandshore as sandshore

    class Runtime:
        def __init__(self, **kwargs) -> None:
            self._in_flight_queries = 0

        async def close(self) -> None:
            return None

    with sandshore._pool_lock:
        sandshore._client_pool.clear()
    try:
        monkeypatch.setattr(sandshore, "SandshoreRuntime", Runtime)
        runtimes = [
            sandshore.get_sandshore_runtime(
                anthropic_api_key=f"a-{index}",
                e2b_api_key=f"e-{index}",
                sandbox_backend="local",
            )
            for index in range(sandshore._MAX_POOL_SIZE)
        ]
        runtimes[0]._in_flight_queries = 1

        sandshore.get_sandshore_runtime(
            anthropic_api_key="new",
            e2b_api_key="new",
            sandbox_backend="local",
        )

        with sandshore._pool_lock:
            assert runtimes[0] in sandshore._client_pool.values()
    finally:
        with sandshore._pool_lock:
            sandshore._client_pool.clear()
