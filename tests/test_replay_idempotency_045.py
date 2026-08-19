"""Workstream B: a replayed side effect must not fire twice.

The guarantee under test is the durable step effect ledger (engine/effects.py)
plus the cassette hook that finally reaches the hybrid step types. Every test
here deliberately avoids ``skip_steps``: run 2 is always a *full* re-execution
of the whole workflow in the same effect scope, so nothing can pass because the
scheduler happened not to schedule the step.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.dag import HttpConfig, StepDefinition, build_plan, parse_yaml_string
from sandcastle.engine.effects import (
    EffectLedger,
    compute_effect_key,
    effect_mode_for,
    step_effect_fingerprint,
)
from sandcastle.engine.executor import RunContext, execute_workflow
from sandcastle.engine.storage import LocalStorage

WF_POST_THEN_TRANSFORM = """
name: post-then-transform
description: A POST followed by a pure reshape
default_model: sonnet
steps:
  - id: post
    type: http
    http_config:
      url: https://billing.internal/v1/invoices
      method: POST
      body: '{"amount": 100}'
  - id: summarize
    type: transform
    depends_on: [post]
    transform_config:
      expression: "'done'"
"""

WF_POST_ONLY = """
name: post-only
description: A single POST
default_model: sonnet
steps:
  - id: post
    type: http
    http_config:
      url: https://billing.internal/v1/invoices
      method: POST
      body: '{"amount": 100}'
"""


class _Recorder:
    """Counts outbound requests made through the patched httpx client."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    @property
    def count(self) -> int:
        return len(self.calls)


@pytest.fixture
def patched_httpx():
    """Patch httpx.AsyncClient so no request can leave the process.

    The recorder counts every ``request()`` call, which is what the headline
    assertion reads. A step that reaches the network at all shows up here.
    """
    recorder = _Recorder()

    response = MagicMock()
    response.json.return_value = {"invoice": "INV-1001"}
    response.status_code = 200
    response.text = '{"invoice": "INV-1001"}'
    response.headers = {"content-type": "application/json"}

    async def _request(**kwargs):
        recorder.calls.append(kwargs)
        return response

    with patch("httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.request = AsyncMock(side_effect=_request)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client_cls.return_value = client
        yield recorder


async def _run(wf, tmp_path, *, scope, tenant_id=None, max_cost_usd=None, input_data=None):
    return await execute_workflow(
        workflow=wf,
        plan=build_plan(wf),
        input_data=input_data or {},
        run_id=str(uuid.uuid4()),
        storage=LocalStorage(str(tmp_path)),
        effect_scope_id=scope,
        tenant_id=tenant_id,
        max_cost_usd=max_cost_usd,
    )


# ---------------------------------------------------------------------------
# The headline guarantee
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replayed_http_step_does_not_fire_twice(tmp_path, patched_httpx):
    """The load-bearing guarantee of workstream B.

    Two complete runs of the same workflow in one effect scope. The POST fires
    on the first and is memoized on the second, at $0.
    """
    wf = parse_yaml_string(WF_POST_THEN_TRANSFORM)
    scope = str(uuid.uuid4())

    r1 = await _run(wf, tmp_path, scope=scope)
    assert r1.status == "completed", r1.error
    assert patched_httpx.count == 1

    r2 = await _run(wf, tmp_path, scope=scope)

    assert patched_httpx.count == 1  # <-- THE assertion
    assert r2.status == "completed", r2.error
    assert r2.outputs["post"] == r1.outputs["post"]
    assert r2.total_cost_usd == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_different_effect_scope_fires_again(tmp_path, patched_httpx):
    """Anti-false-pass guard for the headline test.

    A ledger that failed closed on everything would suppress the second POST
    above too. Two unrelated runs are two lineages, so the POST must fire twice.
    """
    wf = parse_yaml_string(WF_POST_ONLY)

    await _run(wf, tmp_path, scope=str(uuid.uuid4()))
    await _run(wf, tmp_path, scope=str(uuid.uuid4()))

    assert patched_httpx.count == 2


@pytest.mark.asyncio
async def test_effect_key_is_tenant_scoped(tmp_path, patched_httpx):
    """Two tenants never share an effect - mirrors the cache-key tenant fold."""
    wf = parse_yaml_string(WF_POST_ONLY)
    scope = str(uuid.uuid4())

    await _run(wf, tmp_path, scope=scope, tenant_id="tenant-a")
    await _run(wf, tmp_path, scope=scope, tenant_id="tenant-b")

    assert patched_httpx.count == 2


@pytest.mark.asyncio
async def test_changed_request_body_fires_again(tmp_path, patched_httpx):
    """The fingerprint is the dependency analysis.

    Same scope, same step id, different resolved body -> a different effect,
    which is what makes a fork with an upstream change still re-POST.
    """
    wf = parse_yaml_string(
        """
name: post-templated
description: POST whose body comes from the input
default_model: sonnet
steps:
  - id: post
    type: http
    http_config:
      url: https://billing.internal/v1/invoices
      method: POST
      body: '{"amount": "{input.amount}"}'
"""
    )
    scope = str(uuid.uuid4())

    await _run(wf, tmp_path, scope=scope, input_data={"amount": 100})
    assert patched_httpx.count == 1

    await _run(wf, tmp_path, scope=scope, input_data={"amount": 100})
    assert patched_httpx.count == 1  # identical request -> memoized

    await _run(wf, tmp_path, scope=scope, input_data={"amount": 250})
    assert patched_httpx.count == 2  # changed body -> new effect


@pytest.mark.asyncio
async def test_get_requests_default_to_live(tmp_path, patched_httpx):
    """A GET is a read: re-running a replay should see fresh data."""
    wf = parse_yaml_string(
        """
name: get-rate
description: A safe-method read
default_model: sonnet
steps:
  - id: rate
    type: http
    http_config:
      url: https://rates.example.com/usd
      method: GET
"""
    )
    scope = str(uuid.uuid4())

    await _run(wf, tmp_path, scope=scope)
    await _run(wf, tmp_path, scope=scope)

    assert patched_httpx.count == 2


@pytest.mark.asyncio
async def test_replay_live_opt_out_refires(tmp_path, patched_httpx):
    """``replay: live`` opts a POST back into re-execution."""
    wf = parse_yaml_string(
        """
name: post-live
description: An explicitly live POST
default_model: sonnet
steps:
  - id: post
    type: http
    replay: live
    http_config:
      url: https://billing.internal/v1/ping
      method: POST
      body: '{}'
"""
    )
    scope = str(uuid.uuid4())

    await _run(wf, tmp_path, scope=scope)
    await _run(wf, tmp_path, scope=scope)

    assert patched_httpx.count == 2


@pytest.mark.asyncio
async def test_replay_memoize_opt_in_on_get(tmp_path, patched_httpx):
    """``replay: memoize`` overrides the GET-is-live heuristic."""
    wf = parse_yaml_string(
        """
name: get-memoized
description: A GET the author wants frozen
default_model: sonnet
steps:
  - id: rate
    type: http
    replay: memoize
    http_config:
      url: https://rates.example.com/usd
      method: GET
"""
    )
    scope = str(uuid.uuid4())

    await _run(wf, tmp_path, scope=scope)
    await _run(wf, tmp_path, scope=scope)

    assert patched_httpx.count == 1


@pytest.mark.asyncio
async def test_kill_switch_disables_ledger_entirely(tmp_path, patched_httpx):
    """EFFECT_LEDGER_ENABLED=0 restores the pre-0.45 behaviour exactly."""
    from sandcastle.config import settings

    wf = parse_yaml_string(WF_POST_ONLY)
    scope = str(uuid.uuid4())

    with patch.object(settings, "effect_ledger_enabled", False):
        await _run(wf, tmp_path, scope=scope)
        await _run(wf, tmp_path, scope=scope)

    assert patched_httpx.count == 2


@pytest.mark.asyncio
async def test_loop_iterations_are_distinct_effects(tmp_path, patched_httpx):
    """A loop over a constant URL must send once per iteration, not once total.

    Guards the fingerprint-collapse hazard: without the iteration index in the
    key, three iterations would share one effect and two sends would vanish.
    The loop body is also a top-level step in this engine, so it runs a fourth
    time with no iteration index - hence four sends and four distinct effects.
    """
    from sqlalchemy import select as sa_select

    from sandcastle.models.db import StepEffect, async_session

    wf = parse_yaml_string(
        """
name: loop-post
description: POST once per item
default_model: sonnet
steps:
  - id: fan
    type: loop
    loop_config:
      over: "{input.items}"
      step_ids: [ping]
  - id: ping
    type: http
    http_config:
      url: https://billing.internal/v1/ping
      method: POST
      body: '{"constant": true}'
"""
    )
    scope = str(uuid.uuid4())

    await _run(wf, tmp_path, scope=scope, input_data={"items": [1, 2, 3]})

    assert patched_httpx.count == 4
    async with async_session() as session:
        rows = list(
            await session.scalars(
                sa_select(StepEffect).where(StepEffect.effect_scope_id == scope)
            )
        )
    assert {r.iteration_index for r in rows} == {None, 0, 1, 2}
    assert len({r.effect_key for r in rows}) == 4
    assert all(r.status == "committed" for r in rows)


@pytest.mark.asyncio
async def test_fanout_items_are_distinct_effects(tmp_path, patched_httpx):
    """The same guard for ``parallel_over``: N items are N effects."""
    wf = parse_yaml_string(
        """
name: fanout-post
description: POST once per item
default_model: sonnet
steps:
  - id: post
    type: http
    parallel_over: "{input.items}"
    http_config:
      url: https://billing.internal/v1/ping
      method: POST
      body: '{"constant": true}'
"""
    )
    scope = str(uuid.uuid4())

    await _run(wf, tmp_path, scope=scope, input_data={"items": [1, 2, 3]})

    assert patched_httpx.count == 3


# ---------------------------------------------------------------------------
# Half-completion: the three states, no guessing
# ---------------------------------------------------------------------------


def _http_step(step_id: str = "post", **overrides) -> StepDefinition:
    fields = {
        "id": step_id,
        "type": "http",
        "http_config": HttpConfig(
            url="https://billing.internal/v1/invoices", method="POST", body='{"a": 1}'
        ),
    }
    fields.update(overrides)
    return StepDefinition(**fields)


async def _preinsert_in_flight(step, context, *, lease_delta: timedelta):
    """Write an abandoned claim for *step* as if another run had left it."""
    from sandcastle.models.db import StepEffect, async_session

    key = compute_effect_key(
        context.effect_scope_id or context.run_id,
        context.tenant_id,
        step.id,
        step_effect_fingerprint(step, context),
    )
    async with async_session() as session:
        session.add(
            StepEffect(
                id=uuid.uuid4(),
                effect_key=key,
                effect_scope_id=context.effect_scope_id or context.run_id,
                run_id="abandoned-run",
                step_id=step.id,
                step_type=step.type,
                status="in_flight",
                created_at=datetime.now(timezone.utc),
                lease_expires_at=datetime.now(timezone.utc) + lease_delta,
            )
        )
        await session.commit()
    return key


@pytest.mark.asyncio
async def test_half_completed_effect_fails_rather_than_refiring(tmp_path, patched_httpx):
    """An abandoned claim past its lease fails the step. It does not re-POST."""
    from sandcastle.engine.executor import execute_step_with_retry

    step = _http_step()
    context = RunContext(
        run_id=str(uuid.uuid4()), input={}, effect_scope_id=str(uuid.uuid4())
    )
    await _preinsert_in_flight(step, context, lease_delta=timedelta(seconds=-1))

    result = await execute_step_with_retry(
        step, context, MagicMock(), LocalStorage(str(tmp_path))
    )

    assert result.status == "failed"
    assert "EffectUncertain" in (result.error or "")
    assert result.retryable is False
    assert patched_httpx.count == 0


@pytest.mark.asyncio
async def test_live_claim_that_never_settles_is_uncertain(tmp_path, patched_httpx):
    """A claim still inside its lease is waited on briefly, then reported."""
    from sandcastle.config import settings
    from sandcastle.engine.executor import execute_step_with_retry

    step = _http_step()
    context = RunContext(
        run_id=str(uuid.uuid4()), input={}, effect_scope_id=str(uuid.uuid4())
    )
    await _preinsert_in_flight(step, context, lease_delta=timedelta(seconds=900))

    with patch.object(settings, "effect_claim_wait_seconds", 0.05):
        result = await execute_step_with_retry(
            step, context, MagicMock(), LocalStorage(str(tmp_path))
        )

    assert result.status == "failed"
    assert "did not settle" in (result.error or "")
    assert patched_httpx.count == 0


@pytest.mark.asyncio
async def test_cancelled_in_flight_step_is_uncertain_not_refired(tmp_path):
    """The H2 case, with a real cancellation rather than a pre-inserted row.

    An approval pause cancels its in-flight siblings, so a POST that already
    reached the server leaves no step_output, is therefore absent from the
    checkpoint, and is therefore not in skip_steps on resume. Before the ledger
    it simply re-POSTed. Now the abandoned claim survives the cancellation and
    the second attempt reports uncertainty.
    """
    import asyncio

    from sandcastle.config import settings
    from sandcastle.engine.executor import execute_step_with_retry

    step = _http_step(step_id="post_invoice")
    scope = str(uuid.uuid4())
    started = asyncio.Event()
    sent = []

    async def _request(**kwargs):
        sent.append(kwargs)
        started.set()
        await asyncio.sleep(30)  # cancelled here, after the request landed

    client = AsyncMock()
    client.request = AsyncMock(side_effect=_request)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=client):
        ctx1 = RunContext(run_id=str(uuid.uuid4()), input={}, effect_scope_id=scope)
        task = asyncio.create_task(
            execute_step_with_retry(step, ctx1, MagicMock(), LocalStorage(str(tmp_path)))
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert len(sent) == 1

        # Resume in the same lineage: the abandoned claim is still in flight.
        ctx2 = RunContext(run_id=str(uuid.uuid4()), input={}, effect_scope_id=scope)
        with patch.object(settings, "effect_claim_wait_seconds", 0.05):
            result = await execute_step_with_retry(
                step, ctx2, MagicMock(), LocalStorage(str(tmp_path))
            )

    assert result.status == "failed"
    assert "EffectUncertain" in (result.error or "")
    assert len(sent) == 1  # the POST was not sent a second time


@pytest.mark.asyncio
async def test_stale_in_flight_with_on_uncertain_retry_refires(tmp_path, patched_httpx):
    """``on_uncertain: retry`` takes the claim over for an idempotent endpoint."""
    from sandcastle.engine.executor import execute_step_with_retry

    step = _http_step(on_uncertain="retry")
    context = RunContext(
        run_id=str(uuid.uuid4()), input={}, effect_scope_id=str(uuid.uuid4())
    )
    key = await _preinsert_in_flight(step, context, lease_delta=timedelta(seconds=-1))

    result = await execute_step_with_retry(
        step, context, MagicMock(), LocalStorage(str(tmp_path))
    )

    assert result.status == "completed", result.error
    assert patched_httpx.count == 1
    row = await EffectLedger().lookup(key)
    assert row["status"] == "committed"


@pytest.mark.asyncio
async def test_failed_effect_is_retried_not_memoized(tmp_path):
    """A ``failed`` claim means the effect did not land, so it runs again."""
    from sandcastle.engine.executor import execute_step_with_retry

    step = _http_step(step_id="flaky")
    context = RunContext(
        run_id=str(uuid.uuid4()), input={}, effect_scope_id=str(uuid.uuid4())
    )
    key = compute_effect_key(
        context.effect_scope_id,
        None,
        step.id,
        step_effect_fingerprint(step, context),
    )

    calls = []

    def _client_factory(fail: bool):
        response = MagicMock()
        response.json.return_value = {"ok": True}
        response.status_code = 200
        response.text = "{}"
        response.headers = {}

        async def _request(**kwargs):
            calls.append(kwargs)
            if fail:
                raise RuntimeError("connection reset")
            return response

        client = AsyncMock()
        client.request = AsyncMock(side_effect=_request)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        return client

    with patch("httpx.AsyncClient", return_value=_client_factory(True)):
        first = await execute_step_with_retry(
            step, context, MagicMock(), LocalStorage(str(tmp_path))
        )
    assert first.status == "failed"
    assert (await EffectLedger().lookup(key))["status"] == "failed"

    with patch("httpx.AsyncClient", return_value=_client_factory(False)):
        second = await execute_step_with_retry(
            step, context, MagicMock(), LocalStorage(str(tmp_path))
        )
    assert second.status == "completed", second.error
    assert len(calls) == 2
    assert (await EffectLedger().lookup(key))["status"] == "committed"


@pytest.mark.asyncio
async def test_concurrent_claim_only_one_wins(tmp_path):
    """Two coroutines racing for one effect key: exactly one owns it."""
    import asyncio

    ledger = EffectLedger()
    key = "c" * 64
    scope = str(uuid.uuid4())

    async def _claim(run_id: str):
        return await ledger.claim(
            effect_key=key,
            scope_id=scope,
            run_id=run_id,
            tenant_id=None,
            step_id="post",
            step_type="http",
            wait_seconds=0.0,
        )

    first, second = await asyncio.gather(_claim("run-a"), _claim("run-b"))
    outcomes = sorted([first.outcome, second.outcome])
    assert outcomes.count("owned") == 1
    # The loser sees a live claim it cannot resolve, which is the uncertain case.
    assert outcomes == ["owned", "uncertain"]


@pytest.mark.asyncio
async def test_ledger_unavailable_degrades_to_live_by_default(tmp_path, patched_httpx):
    """No database is not a reason to fail every POST - it is a reason to warn.

    ``sandcastle run --local`` documents that it works without a DB. Flip
    ``effect_ledger_required`` to make the ledger a hard dependency instead.
    """
    from sandcastle.engine.executor import execute_step_with_retry

    step = _http_step()
    context = RunContext(
        run_id=str(uuid.uuid4()), input={}, effect_scope_id=str(uuid.uuid4())
    )

    with patch(
        "sandcastle.models.db.async_session",
        side_effect=Exception("no such table: run_step_effects"),
    ):
        result = await execute_step_with_retry(
            step, context, MagicMock(), LocalStorage(str(tmp_path))
        )

    assert result.status == "completed", result.error
    assert patched_httpx.count == 1


def test_ledger_requirement_follows_the_deployment():
    """Unset, the fail-closed decision comes from the deployment, not a flag.

    Local mode has no database, so failing every side-effecting step would
    break a documented mode. A server deployment does have one, so a ledger it
    cannot reach is a fault - and running live there is the duplicate POST the
    ledger exists to prevent.
    """
    from sandcastle.engine.effects import _ledger_is_required

    class _S:
        def __init__(self, required, local):
            self.effect_ledger_required = required
            self.is_local_mode = local

    assert _ledger_is_required(_S(None, True)) is False
    assert _ledger_is_required(_S(None, False)) is True
    # An explicit setting always wins over the deployment default.
    assert _ledger_is_required(_S(False, False)) is False
    assert _ledger_is_required(_S(True, True)) is True


@pytest.mark.asyncio
async def test_server_deployment_fails_closed_without_being_told(
    tmp_path, patched_httpx
):
    """The dangerous default is the one most people run, so it must be safe."""
    from sandcastle.config import settings
    from sandcastle.engine.executor import execute_step_with_retry

    step = _http_step()
    context = RunContext(
        run_id=str(uuid.uuid4()), input={}, effect_scope_id=str(uuid.uuid4())
    )

    with patch.object(settings, "effect_ledger_required", None), patch.object(
        type(settings), "is_local_mode", property(lambda self: False)
    ), patch(
        "sandcastle.models.db.async_session",
        side_effect=Exception("no such table: run_step_effects"),
    ):
        result = await execute_step_with_retry(
            step, context, MagicMock(), LocalStorage(str(tmp_path))
        )

    assert result.status == "failed"
    assert patched_httpx.count == 0


@pytest.mark.asyncio
async def test_ledger_required_fails_closed(tmp_path, patched_httpx):
    """With ``effect_ledger_required``, an unreachable ledger fails the step."""
    from sandcastle.config import settings
    from sandcastle.engine.executor import execute_step_with_retry

    step = _http_step()
    context = RunContext(
        run_id=str(uuid.uuid4()), input={}, effect_scope_id=str(uuid.uuid4())
    )

    with patch.object(settings, "effect_ledger_required", True), patch(
        "sandcastle.models.db.async_session",
        side_effect=Exception("no such table: run_step_effects"),
    ):
        result = await execute_step_with_retry(
            step, context, MagicMock(), LocalStorage(str(tmp_path))
        )

    assert result.status == "failed"
    assert "unreachable" in (result.error or "")
    assert patched_httpx.count == 0


# ---------------------------------------------------------------------------
# Cost accounting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memoized_llm_step_does_not_recharge_budget(tmp_path):
    """The cassette gap that bit hardest: ``llm`` re-spending on replay.

    The budget is tight enough that a second charge would trip _check_budget,
    so a passing run 2 is itself proof no tokens were bought.
    """
    from sandcastle.engine.executor import StepResult

    wf = parse_yaml_string(
        """
name: llm-twice
description: One paid model call
default_model: sonnet
steps:
  - id: extract
    type: llm
    prompt: Extract the invoice total.
"""
    )
    scope = str(uuid.uuid4())
    calls = {"n": 0}

    async def _fake_llm(step, context, storage):
        calls["n"] += 1
        return StepResult(step_id=step.id, output="42", cost_usd=0.42, status="completed")

    with patch("sandcastle.engine.executor._execute_llm_step", side_effect=_fake_llm):
        r1 = await _run(wf, tmp_path, scope=scope, max_cost_usd=0.5)
        assert r1.status == "completed", r1.error
        assert r1.total_cost_usd == pytest.approx(0.42)

        r2 = await _run(wf, tmp_path, scope=scope, max_cost_usd=0.5)

    assert calls["n"] == 1
    assert r2.status == "completed", r2.error
    assert r2.outputs["extract"] == "42"
    assert r2.total_cost_usd == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_memoized_result_carries_original_cost(tmp_path):
    """``replayed`` / ``original_cost_usd`` let the UI show what was saved."""
    from sandcastle.engine.executor import StepResult, execute_step_with_retry

    step = StepDefinition(id="extract", type="llm", prompt="Extract.")
    scope = str(uuid.uuid4())

    async def _fake_llm(s, context, storage):
        return StepResult(step_id=s.id, output="42", cost_usd=0.42, status="completed")

    with patch("sandcastle.engine.executor._execute_llm_step", side_effect=_fake_llm):
        ctx1 = RunContext(run_id=str(uuid.uuid4()), input={}, effect_scope_id=scope)
        first = await execute_step_with_retry(
            step, ctx1, MagicMock(), LocalStorage(str(tmp_path))
        )
        ctx2 = RunContext(run_id=str(uuid.uuid4()), input={}, effect_scope_id=scope)
        second = await execute_step_with_retry(
            step, ctx2, MagicMock(), LocalStorage(str(tmp_path))
        )

    assert first.replayed is False
    assert first.cost_usd == pytest.approx(0.42)
    assert second.replayed is True
    assert second.cost_usd == pytest.approx(0.0)
    assert second.original_cost_usd == pytest.approx(0.42)


# ---------------------------------------------------------------------------
# Cassette coverage for the hybrid types
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cassette_replay_covers_llm_step(tmp_path):
    """Record then replay a workflow with ``type: llm``: zero provider calls.

    Before 0.45 the cassette hook lived inside ``_execute_step_once``, which no
    hybrid type reaches, so this replay cost full price.
    """
    from sandcastle.engine.cassette import CassetteStore
    from sandcastle.engine.executor import StepResult

    wf = parse_yaml_string(
        """
name: llm-cassette
description: One paid model call
default_model: sonnet
steps:
  - id: extract
    type: llm
    prompt: Extract the invoice total.
"""
    )
    calls = {"n": 0}

    async def _fake_llm(step, context, storage):
        calls["n"] += 1
        return StepResult(step_id=step.id, output="42", cost_usd=0.42, status="completed")

    path = tmp_path / "run.cassette.json"
    with patch("sandcastle.engine.executor._execute_llm_step", side_effect=_fake_llm):
        recorder = CassetteStore(path, "record")
        await execute_workflow(
            workflow=wf,
            plan=build_plan(wf),
            input_data={},
            run_id=str(uuid.uuid4()),
            storage=LocalStorage(str(tmp_path)),
            cassette=recorder,
            cassette_mode="record",
            # Isolate the cassette assertion from the ledger.
            effect_scope_id=str(uuid.uuid4()),
        )
        recorder.save()
        assert calls["n"] == 1

        player = CassetteStore(path, "replay")
        result = await execute_workflow(
            workflow=wf,
            plan=build_plan(wf),
            input_data={},
            run_id=str(uuid.uuid4()),
            storage=LocalStorage(str(tmp_path)),
            cassette=player,
            cassette_mode="replay",
            effect_scope_id=str(uuid.uuid4()),
        )

    assert calls["n"] == 1  # zero live calls on replay
    assert player.replay_hits == 1
    assert result.outputs["extract"] == "42"
    assert result.total_cost_usd == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_cassette_replay_covers_http_step(tmp_path, patched_httpx):
    """Same for ``http``: a recorded POST is not re-sent on replay."""
    from sandcastle.engine.cassette import CassetteStore

    wf = parse_yaml_string(WF_POST_ONLY)
    path = tmp_path / "http.cassette.json"

    recorder = CassetteStore(path, "record")
    await execute_workflow(
        workflow=wf,
        plan=build_plan(wf),
        input_data={},
        run_id=str(uuid.uuid4()),
        storage=LocalStorage(str(tmp_path)),
        cassette=recorder,
        cassette_mode="record",
        effect_scope_id=str(uuid.uuid4()),
    )
    recorder.save()
    assert patched_httpx.count == 1

    player = CassetteStore(path, "replay")
    result = await execute_workflow(
        workflow=wf,
        plan=build_plan(wf),
        input_data={},
        run_id=str(uuid.uuid4()),
        storage=LocalStorage(str(tmp_path)),
        cassette=player,
        cassette_mode="replay",
        effect_scope_id=str(uuid.uuid4()),
    )

    assert patched_httpx.count == 1
    assert result.status == "completed", result.error


# ---------------------------------------------------------------------------
# Units: fingerprint, mode selection, bundle safety
# ---------------------------------------------------------------------------


def test_effect_mode_defaults_by_type():
    assert effect_mode_for(StepDefinition(id="a", type="llm")) == "memoize"
    assert effect_mode_for(StepDefinition(id="a", type="notify")) == "memoize"
    assert effect_mode_for(StepDefinition(id="a", type="transform")) == "live"
    assert effect_mode_for(StepDefinition(id="a", type="code")) == "live"
    assert effect_mode_for(StepDefinition(id="a", type="condition")) == "live"
    assert effect_mode_for(_http_step()) == "memoize"
    assert (
        effect_mode_for(
            StepDefinition(
                id="a", type="http", http_config=HttpConfig(url="u", method="GET")
            )
        )
        == "live"
    )
    assert effect_mode_for(_http_step(replay="live")) == "live"


def test_fingerprint_does_not_leak_auth_material():
    """A bearer token must not be recoverable from what reaches the ledger."""
    step = StepDefinition(
        id="post",
        type="http",
        http_config=HttpConfig(
            url="https://api.example.com/x",
            method="POST",
            headers={"Authorization": "Bearer super-secret-token"},
            auth="bearer:super-secret-token",
        ),
    )
    context = RunContext(run_id="r", input={})
    fingerprint = step_effect_fingerprint(step, context)
    assert "super-secret-token" not in fingerprint
    assert len(fingerprint) == 64

    rotated = StepDefinition(
        id="post",
        type="http",
        http_config=HttpConfig(
            url="https://api.example.com/x",
            method="POST",
            headers={"Authorization": "Bearer other-token"},
            auth="bearer:other-token",
        ),
    )
    assert step_effect_fingerprint(rotated, context) != fingerprint


def test_fingerprint_tracks_resolved_values_not_templates():
    step = StepDefinition(
        id="post",
        type="http",
        http_config=HttpConfig(
            url="https://api.example.com/{input.id}", method="POST", body='{"a": 1}'
        ),
    )
    one = step_effect_fingerprint(step, RunContext(run_id="r", input={"id": "1"}))
    two = step_effect_fingerprint(step, RunContext(run_id="r", input={"id": "2"}))
    assert one != two


def test_invalid_replay_mode_is_rejected():
    """A typo'd ``replay: memoise`` must not silently mean "live"."""
    with pytest.raises(ValueError, match="Invalid replay mode"):
        parse_yaml_string(
            """
name: typo
description: bad replay mode
default_model: sonnet
steps:
  - id: post
    type: http
    replay: memoise
    http_config:
      url: https://x.example.com
      method: POST
"""
        )


def test_invalid_on_uncertain_is_rejected():
    with pytest.raises(ValueError, match="Invalid on_uncertain"):
        parse_yaml_string(
            """
name: typo
description: bad on_uncertain
default_model: sonnet
steps:
  - id: post
    type: http
    on_uncertain: shrug
    http_config:
      url: https://x.example.com
      method: POST
"""
        )


def test_bundle_verify_still_rejects_side_effecting_types():
    """Widening REPLAY_SAFE_STEP_TYPES must not let http into verification."""
    from sandcastle.engine.bundle import REPLAY_SAFE_STEP_TYPES

    assert "llm" in REPLAY_SAFE_STEP_TYPES
    for unsafe in (
        "http",
        "notify",
        "code",
        "browser",
        "computer-use",
        "tool",
        "composio",
        "openclaw",
        "agent",
        "managed-agent",
        "delegate",
        "sub_workflow",
        "report",
    ):
        assert unsafe not in REPLAY_SAFE_STEP_TYPES


@pytest.mark.asyncio
async def test_prune_removes_only_expired_rows():
    from sandcastle.engine.effects import prune_expired_effects
    from sandcastle.models.db import StepEffect, async_session

    fresh_key = "f" * 64
    stale_key = "e" * 64
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        session.add_all(
            [
                StepEffect(
                    id=uuid.uuid4(),
                    effect_key=fresh_key,
                    effect_scope_id="s",
                    run_id="r",
                    step_id="a",
                    step_type="http",
                    status="committed",
                    expires_at=now + timedelta(days=1),
                ),
                StepEffect(
                    id=uuid.uuid4(),
                    effect_key=stale_key,
                    effect_scope_id="s",
                    run_id="r",
                    step_id="b",
                    step_type="http",
                    status="committed",
                    expires_at=now - timedelta(days=1),
                ),
            ]
        )
        await session.commit()

    removed = await prune_expired_effects()

    assert removed >= 1
    ledger = EffectLedger()
    assert await ledger.lookup(stale_key) is None
    assert await ledger.lookup(fresh_key) is not None


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_alembic_single_head():
    """One head, always.

    This repo has already had a three-way collision at revision 019 - the head
    file's own docstring records it (021_runstep_token_accounting.py). Two
    branches, each valid on its own, produce a tree that ``alembic upgrade
    head`` refuses to apply. Catch it here instead of in a deploy.
    """
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()

    assert len(heads) == 1, f"alembic has {len(heads)} heads: {heads}"


def test_alembic_revision_ids_are_unique():
    """Two files claiming the same revision id is the other half of a collision."""
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    revisions = [
        script.revision for script in ScriptDirectory.from_config(config).walk_revisions()
    ]

    assert len(revisions) == len(set(revisions))


def test_step_effect_ledger_revision_upgrades_and_downgrades_sqlite(tmp_path):
    """022 is reversible on a scratch SQLite baseline, and matches the models."""
    import importlib.util
    from pathlib import Path
    from unittest.mock import patch as _patch

    import sqlalchemy as sa
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    from sandcastle.models.db import Base

    path = Path(__file__).parents[1] / "alembic/versions/022_step_effect_ledger.py"
    spec = importlib.util.spec_from_file_location("migration_022", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "022"
    assert module.down_revision == "021"

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ledger.sqlite'}")
    with engine.begin() as connection:
        metadata = sa.MetaData()
        for table_name in ("runs", "run_steps"):
            sa.Table(table_name, metadata, sa.Column("id", sa.Uuid(), primary_key=True))
        metadata.create_all(connection)

        operations = Operations(MigrationContext.configure(connection))
        with _patch.object(module, "op", operations):
            module.upgrade()

        inspector = sa.inspect(connection)
        assert "run_step_effects" in inspector.get_table_names()

        model_table = Base.metadata.tables["run_step_effects"]
        migrated_columns = {c["name"] for c in inspector.get_columns("run_step_effects")}
        assert migrated_columns == set(model_table.columns.keys())
        assert {
            c["name"] for c in inspector.get_columns("run_step_effects") if not c["nullable"]
        } == {c.name for c in model_table.columns if not c.nullable}
        assert {i["name"] for i in inspector.get_indexes("run_step_effects")} >= {
            i.name for i in model_table.indexes
        }
        assert "effect_scope_id" in {c["name"] for c in inspector.get_columns("runs")}
        assert {"replayed", "original_cost_usd"} <= {
            c["name"] for c in inspector.get_columns("run_steps")
        }

        with _patch.object(module, "op", operations):
            module.downgrade()

        inspector = sa.inspect(connection)
        assert "run_step_effects" not in inspector.get_table_names()
        assert "effect_scope_id" not in {c["name"] for c in inspector.get_columns("runs")}
        assert "replayed" not in {c["name"] for c in inspector.get_columns("run_steps")}
