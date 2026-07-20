"""Regression coverage for execution-engine audit block 3."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.dag import (
    CodeConfig,
    ExecutionPlan,
    RetryConfig,
    StepDefinition,
    WorkflowDefinition,
    parse_yaml_string,
)
from sandcastle.engine.executor import (
    RunContext,
    _execute_llm_step,
    _prepare_and_run_step,
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
async def test_empty_output_retry_cost_is_recorded_and_stops_budget(engine_persistence_mocks):
    """Both charged empty-output attempts count toward the workflow budget."""
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
            max_cost_usd=0.15,
        )

    assert sandbox.query.await_count == 2
    assert result.total_cost_usd == pytest.approx(0.20)
    assert result.status == "budget_exceeded"


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
