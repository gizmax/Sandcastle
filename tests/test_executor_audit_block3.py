"""Regression coverage for execution-engine audit block 3."""

from __future__ import annotations

import asyncio
import copy
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.dag import (
    CodeConfig,
    ExecutionPlan,
    LoopConfig,
    RaceConfig,
    RetryConfig,
    StepDefinition,
    WorkflowDefinition,
    parse_yaml_string,
)
from sandcastle.engine.executor import (
    RunContext,
    StepResult,
    WorkflowPaused,
    _execute_llm_step,
    _execute_loop_step,
    _execute_race_step,
    _prepare_and_run_step,
    execute_step_with_retry,
    execute_workflow,
)


@pytest.fixture
def engine_persistence_mocks():
    """Keep engine tests local to their execution behavior."""
    with (
        patch("sandcastle.engine.executor._save_run_step", new_callable=AsyncMock),
        patch("sandcastle.engine.executor._save_checkpoint", new_callable=AsyncMock),
        patch("sandcastle.engine.executor._emit_audit_event", new_callable=AsyncMock),
        patch("sandcastle.engine.executor.event_bus"),
    ):
        yield


def _workflow(step: StepDefinition, name: str = "audit-block-3") -> WorkflowDefinition:
    return WorkflowDefinition(
        name=name,
        description="test",
        default_model="sonnet",
        default_max_turns=1,
        default_timeout=60,
        steps=[step],
    )


@pytest.mark.asyncio
async def test_parallel_cache_key_remains_tenant_scoped(engine_persistence_mocks):
    """Fan-out children must not reuse another tenant's cached result."""
    step = StepDefinition(
        id="process",
        prompt="Process {input._item}",
        parallel_over="{input.items}",
    )
    workflow = _workflow(step)
    cache: dict[str, dict] = {}

    async def get_cached(cache_key: str) -> dict | None:
        return cache.get(cache_key)

    async def save_cached(**kwargs) -> None:
        cache[kwargs["cache_key"]] = {"output": {"result": kwargs["output"]}}

    sandbox = MagicMock()
    sandbox.query = AsyncMock(
        side_effect=[
            SimpleNamespace(text="tenant-a-only", structured_output=None, total_cost_usd=0.01),
            SimpleNamespace(text="tenant-b-only", structured_output=None, total_cost_usd=0.01),
        ]
    )
    storage = MagicMock()
    tenant_a = RunContext(
        run_id="run-a",
        input={"items": ["same"]},
        workflow_name=workflow.name,
        tenant_id="tenant-a",
    )
    tenant_b = RunContext(
        run_id="run-b",
        input={"items": ["same"]},
        workflow_name=workflow.name,
        tenant_id="tenant-b",
    )

    with (
        patch("sandcastle.engine.executor._get_cached_result", new=get_cached),
        patch("sandcastle.engine.executor._save_to_cache", new=save_cached),
    ):
        await _prepare_and_run_step("process", workflow, tenant_a, sandbox, storage, [], None, 0)
        await _prepare_and_run_step("process", workflow, tenant_b, sandbox, storage, [], None, 0)

    assert tenant_a.step_outputs["process"] == ["tenant-a-only"]
    assert tenant_b.step_outputs["process"] == ["tenant-b-only"]
    assert sandbox.query.await_count == 2


@pytest.mark.asyncio
async def test_parallel_code_step_preserves_admin_trust(engine_persistence_mocks):
    """Admin-trusted code steps execute for every fan-out child context."""
    step = StepDefinition(
        id="transform",
        type="code",
        prompt="",
        parallel_over="{input.items}",
        code_config=CodeConfig(code='result = _input["_item"]'),
    )
    workflow = _workflow(step)
    context = RunContext(
        run_id="run-code",
        input={"items": ["first", "second"]},
        workflow_name=workflow.name,
        admin_trusted=True,
    )

    await _prepare_and_run_step(
        "transform", workflow, context, MagicMock(), MagicMock(), [], None, 0
    )

    assert context.step_outputs["transform"] == ["first", "second"]


def test_with_item_preserves_tenant_and_cassette_fields():
    cassette = object()
    parent = RunContext(
        run_id="run-context",
        input={},
        tenant_id="tenant-a",
        admin_trusted=True,
        cassette=cassette,
        cassette_mode="record",
    )

    child = parent.with_item("item", 0)

    assert child.tenant_id == "tenant-a"
    assert child.admin_trusted is True
    assert child.cassette is cassette
    assert child.cassette_mode == "record"


@pytest.mark.asyncio
async def test_output_schema_failure_is_not_retried(engine_persistence_mocks):
    """A deterministic output-schema failure must not spend retry attempts."""
    step = StepDefinition(
        id="charged-empty-output",
        prompt="Return structured data",
        output_schema={"type": "object"},
        retry=RetryConfig(max_attempts=2, backoff="fixed", on_failure="skip"),
    )
    workflow = _workflow(step)
    sandbox = MagicMock()
    sandbox.query = AsyncMock(
        return_value=SimpleNamespace(text="", structured_output=None, total_cost_usd=0.10)
    )

    with (
        patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sandbox),
        patch(
            "sandcastle.engine.executor._get_cached_result",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("sandcastle.engine.executor._save_to_cache", new_callable=AsyncMock),
        patch("sandcastle.engine.executor.asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await execute_workflow(
            workflow,
            ExecutionPlan(stages=[[step.id]]),
            {},
            storage=MagicMock(),
            max_cost_usd=1.00,
        )

    assert sandbox.query.await_count == 1
    assert result.total_cost_usd == pytest.approx(0.10)
    assert result.status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_error", "expected_calls"),
    [
        ("HTTP 401: unauthorized", 1),
        ("HTTP 429: rate limited", 3),
    ],
)
async def test_retry_only_retries_retriable_provider_errors(
    engine_persistence_mocks,
    provider_error: str,
    expected_calls: int,
):
    step = StepDefinition(
        id="provider-error",
        prompt="Call provider",
        retry=RetryConfig(max_attempts=3, backoff="fixed", on_failure="skip"),
    )
    sandbox = MagicMock()
    sandbox.query = AsyncMock(side_effect=Exception(provider_error))

    with (
        patch(
            "sandcastle.engine.executor._get_cached_result",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("sandcastle.engine.executor.asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await execute_step_with_retry(
            step,
            RunContext(run_id=str(uuid.uuid4()), input={}, workflow_name="retry-errors"),
            sandbox,
            MagicMock(),
        )

    assert sandbox.query.await_count == expected_calls
    assert result.attempt == expected_calls


@pytest.mark.asyncio
async def test_budget_exhaustion_between_attempts_stops_retry(engine_persistence_mocks):
    """A charged retriable failure stops before a second paid attempt."""
    step = StepDefinition(
        id="charged-retry",
        prompt="Call provider",
        retry=RetryConfig(max_attempts=3, backoff="fixed", on_failure="abort"),
    )
    workflow = _workflow(step)
    attempt = AsyncMock(
        return_value=StepResult(
            step_id=step.id,
            status="failed",
            error="HTTP 429: rate limited",
            cost_usd=0.10,
        )
    )

    with (
        patch("sandcastle.engine.executor._execute_step_once", new=attempt),
        patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=MagicMock()),
        patch("sandcastle.engine.executor.asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await execute_workflow(
            workflow,
            ExecutionPlan(stages=[[step.id]]),
            {},
            storage=MagicMock(),
            max_cost_usd=0.05,
        )

    assert attempt.await_count == 1
    assert result.status == "budget_exceeded"
    assert result.total_cost_usd == pytest.approx(0.10)


@pytest.mark.asyncio
async def test_hybrid_http_step_honors_retry(engine_persistence_mocks):
    """Hybrid HTTP steps use the same retry count and cost aggregation."""
    step = StepDefinition(
        id="http-retry",
        type="http",
        prompt="",
        retry=RetryConfig(max_attempts=2, backoff="fixed", on_failure="abort"),
    )
    workflow = _workflow(step)
    http_attempt = AsyncMock(
        side_effect=[
            StepResult(step_id=step.id, status="failed", error="HTTP 429: rate limited"),
            StepResult(step_id=step.id, status="completed", output={"ok": True}),
        ]
    )

    with (
        patch("sandcastle.engine.executor._execute_http_step", new=http_attempt),
        patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=MagicMock()),
        patch("sandcastle.engine.executor.asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await execute_workflow(
            workflow,
            ExecutionPlan(stages=[[step.id]]),
            {},
            storage=MagicMock(),
        )

    assert http_attempt.await_count == 2
    assert result.status == "completed"
    assert result.outputs[step.id] == {"ok": True}


@pytest.mark.asyncio
async def test_paid_pause_checkpoints_sibling_without_replaying_it(engine_persistence_mocks):
    """A paid policy pause retains its cost and completed sibling on resume."""
    from sandcastle.engine.policy import (
        PolicyAction,
        PolicyDefinition,
        PolicyPattern,
        PolicyTrigger,
    )

    policy = PolicyDefinition(
        id="approval-after-query",
        trigger=PolicyTrigger(
            type="output_contains",
            patterns=[PolicyPattern(type="regex", pattern="paid")],
        ),
        action=PolicyAction(type="inject_approval"),
    )
    paid_pause = StepDefinition(
        id="paid_pause",
        prompt="Generate paid output",
        policies=[policy],
    )
    sibling = StepDefinition(
        id="sibling",
        type="code",
        prompt="",
        code_config=CodeConfig(code='result = "completed before pause"'),
    )
    after = StepDefinition(
        id="after",
        type="code",
        prompt="",
        depends_on=[paid_pause.id, sibling.id],
        code_config=CodeConfig(code='result = "after approval"'),
    )
    workflow = WorkflowDefinition(
        name="paid-pause",
        description="test",
        default_model="sonnet",
        default_max_turns=1,
        default_timeout=60,
        steps=[paid_pause, sibling, after],
    )
    sandbox = MagicMock()
    sandbox.query = AsyncMock(
        return_value=SimpleNamespace(text="paid", structured_output=None, total_cost_usd=0.25)
    )
    checkpoints: list[dict] = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def add(self, _obj):
            return None

        async def get(self, _model, _key):
            return SimpleNamespace(status=None)

        async def commit(self):
            return None

        async def refresh(self, approval):
            approval.id = uuid.uuid4()

    async def save_checkpoint(_run_id, _step_id, _index, context):
        checkpoints.append(copy.deepcopy(context.snapshot()))

    with (
        patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sandbox),
        patch(
            "sandcastle.engine.executor._get_cached_result",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("sandcastle.engine.executor._save_to_cache", new_callable=AsyncMock),
        patch("sandcastle.engine.executor._save_policy_violations", new_callable=AsyncMock),
        patch("sandcastle.engine.executor._save_checkpoint", new=save_checkpoint),
        patch("sandcastle.models.db.async_session", return_value=FakeSession()),
    ):
        paused = await execute_workflow(
            workflow,
            ExecutionPlan(stages=[[paid_pause.id, sibling.id], [after.id]]),
            {},
            run_id=str(uuid.uuid4()),
            storage=MagicMock(),
            admin_trusted=True,
        )

    assert paused.status == "awaiting_approval"
    assert checkpoints
    pause_snapshot = checkpoints[-1]
    assert sum(pause_snapshot["costs"]) == pytest.approx(0.25)
    assert pause_snapshot["step_outputs"][sibling.id] == "completed before pause"

    resume_snapshot = copy.deepcopy(pause_snapshot)
    resume_snapshot["step_outputs"][paid_pause.id] = {"decision": "approved"}
    resumed_steps: list[str] = []

    async def resume_step(step_id, _workflow, context, *_args, **_kwargs):
        resumed_steps.append(step_id)
        context.step_outputs[step_id] = "after approval"

    with (
        patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=MagicMock()),
        patch("sandcastle.engine.executor._prepare_and_run_step", new=resume_step),
    ):
        resumed = await execute_workflow(
            workflow,
            ExecutionPlan(stages=[[paid_pause.id, sibling.id], [after.id]]),
            {},
            run_id=str(uuid.uuid4()),
            storage=MagicMock(),
            initial_context=resume_snapshot,
            skip_steps=set(resume_snapshot["step_outputs"]),
            admin_trusted=True,
        )

    assert resumed.status == "completed"
    assert resumed_steps == [after.id]


@pytest.mark.asyncio
async def test_loop_pause_carries_completed_iteration_cost():
    """A loop adds finished sub-step costs to the propagated pause."""
    sub_step = StepDefinition(id="loop-sub-step", prompt="sub-step")
    loop_step = StepDefinition(
        id="loop",
        type="loop",
        prompt="",
        loop_config=LoopConfig(
            over="{input.items}",
            step_ids=[sub_step.id],
            max_iterations=2,
        ),
    )
    workflow = _workflow(loop_step)
    workflow.steps.append(sub_step)
    calls = 0

    async def execute_sub_step(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return StepResult(step_id=sub_step.id, status="completed", cost_usd=0.10)
        raise WorkflowPaused("loop-approval", "run-loop", accrued_cost_usd=0.20)

    with patch("sandcastle.engine.executor.execute_step_with_retry", new=execute_sub_step):
        with pytest.raises(WorkflowPaused) as paused:
            await _execute_loop_step(
                loop_step,
                RunContext(run_id="run-loop", input={"items": [1, 2]}),
                MagicMock(),
                MagicMock(),
                workflow,
                0,
            )

    assert paused.value.accrued_cost_usd == pytest.approx(0.30)


@pytest.mark.asyncio
async def test_race_pause_carries_cancelled_sibling_partial_cost():
    """A pausing race records a cancelled sibling's accrued attempt cost."""
    pausing = StepDefinition(id="pausing", prompt="pause")
    sibling = StepDefinition(id="sibling", prompt="work")
    race_step = StepDefinition(
        id="race",
        type="race",
        prompt="",
        race_config=RaceConfig(branches=[[pausing.id], [sibling.id]]),
    )
    workflow = _workflow(race_step)
    workflow.steps.extend([pausing, sibling])
    sibling_started = asyncio.Event()

    async def execute_branch(sub_step, *_args, **_kwargs):
        if sub_step.id == sibling.id:
            sibling_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as cancelled:
                cancelled.accrued_cost_usd = 0.10
                raise
        await sibling_started.wait()
        raise WorkflowPaused("race-approval", "run-race", accrued_cost_usd=0.20)

    with patch("sandcastle.engine.executor.execute_step_with_retry", new=execute_branch):
        with pytest.raises(WorkflowPaused) as paused:
            await _execute_race_step(
                race_step,
                RunContext(run_id="run-race", input={}),
                MagicMock(),
                MagicMock(),
                workflow,
                0,
            )

    assert paused.value.accrued_cost_usd == pytest.approx(0.30)


@pytest.mark.asyncio
async def test_outer_cancellation_cancels_running_step(engine_persistence_mocks):
    """Cancelling execute_workflow also cancels its in-flight step task."""
    step = StepDefinition(id="long-running", prompt="wait")
    workflow = _workflow(step)
    step_started = asyncio.Event()
    step_cancelled = asyncio.Event()

    async def long_running_step(*args, **kwargs) -> None:
        step_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            step_cancelled.set()
            raise

    with (
        patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=MagicMock()),
        patch("sandcastle.engine.executor._prepare_and_run_step", new=long_running_step),
    ):
        run_task = asyncio.create_task(
            execute_workflow(
                workflow,
                ExecutionPlan(stages=[[step.id]]),
                {},
                storage=MagicMock(),
            )
        )
        await asyncio.wait_for(step_started.wait(), timeout=1)
        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run_task

    await asyncio.wait_for(step_cancelled.wait(), timeout=1)


def test_llm_config_parses_sampling_and_token_limit():
    workflow = parse_yaml_string(
        """
name: llm-config
steps:
  - id: summarize
    type: llm
    prompt: Summarize this
    llm_config:
      temperature: 0.1
      max_tokens: 8192
"""
    )

    config = workflow.get_step("summarize").llm_config

    assert config is not None
    assert config.temperature == pytest.approx(0.1)
    assert config.max_tokens == 8192


@pytest.mark.parametrize(
    "config, message",
    [
        ("temperature: -0.1", "temperature"),
        ("temperature: 2.1", "temperature"),
        ("max_tokens: 0", "max_tokens"),
        ("max_tokens: 1.5", "max_tokens"),
    ],
)
def test_llm_config_rejects_invalid_sampling_and_token_limit(config: str, message: str):
    with pytest.raises(ValueError, match=message):
        parse_yaml_string(
            f"""
name: invalid-llm-config
steps:
  - id: summarize
    type: llm
    prompt: Summarize this
    llm_config:
      {config}
"""
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "provider", "response_data"),
    [
        (
            "haiku",
            "claude",
            {
                "content": [{"text": "Anthropic response"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        ),
        (
            "gpt-4o-mini",
            "openai",
            {
                "choices": [{"message": {"content": "OpenAI response"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        ),
    ],
)
async def test_llm_config_reaches_provider_payload(
    model: str,
    provider: str,
    response_data: dict,
):
    workflow = parse_yaml_string(
        f"""
name: llm-provider-config
steps:
  - id: summarize
    type: llm
    model: {model}
    prompt: Summarize this
    llm_config:
      temperature: 0.1
      max_tokens: 8192
"""
    )
    step = workflow.get_step("summarize")
    model_info = SimpleNamespace(
        provider=provider,
        api_model_id=model,
        input_price_per_m=1.0,
        output_price_per_m=1.0,
    )
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status = MagicMock()
    response.json.return_value = response_data
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.post.return_value = response

    with (
        patch("sandcastle.engine.providers.resolve_model", return_value=model_info),
        patch("sandcastle.engine.providers.maybe_spark_nim_route", side_effect=lambda value: value),
        patch("sandcastle.engine.providers.get_api_key", return_value="test-key"),
        patch(
            "sandcastle.engine.providers.resolve_base_url", return_value="https://provider.test/v1"
        ),
        patch("httpx.AsyncClient", return_value=client),
    ):
        result = await _execute_llm_step(
            step,
            RunContext(run_id="run-llm", input={}),
            MagicMock(),
        )

    assert result.status == "completed"
    payload = client.post.call_args.kwargs["json"]
    assert payload["temperature"] == pytest.approx(0.1)
    assert payload["max_tokens"] == 8192
