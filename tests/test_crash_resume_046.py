"""Workstream 3: a crashed run resumes instead of dying.

0.45 gave the worker a durable step effect ledger. 0.46 lets it act on one:
``_recover_stuck_runs`` requeues a run stranded in RUNNING rather than marking
it FAILED, and the ledger memoizes whatever already landed. The headline test
kills a run after its POST has committed and asserts the POST fires **zero**
more times while the run still reaches COMPLETED.

Every test here goes through the real worker entry points - ``enqueue_workflow``
in its local (in-process) mode and ``run_workflow_job`` - rather than calling
``execute_workflow`` directly, because the thing under test is the *recovery
path*, not the executor.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from sandcastle.models.db import Run, RunStatus, RunStep, StepEffect, async_session
from sandcastle.queue import worker as worker_mod
from sandcastle.queue.worker import _recover_stuck_runs

# A POST that must never fire twice, then a GET. The GET is deliberate: an
# http GET is ``replay: live`` (SAFE_HTTP_METHODS), so it takes no ledger claim
# at all. Hanging it is therefore a clean way to strand a run *after* the POST
# has committed and *before* the run finishes, with no stray in_flight row -
# which is the crash this workstream exists to survive.
WF_POST_THEN_POLL = """
name: crash-resume-post-then-poll
description: A POST that must fire once, then a poll that hangs the first time
default_model: sonnet
steps:
  - id: charge
    type: http
    http_config:
      url: https://billing.internal/v1/charges
      method: POST
      body: '{"amount": 4200}'
  - id: poll
    type: http
    depends_on: [charge]
    http_config:
      url: https://billing.internal/v1/status
      method: GET
"""


class _Recorder:
    """Counts outbound requests, split by method, made through patched httpx."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.hang_on_get = False

    def count(self, method: str) -> int:
        return sum(
            1 for c in self.calls if str(c.get("method", "")).upper() == method.upper()
        )


@pytest.fixture
def patched_httpx():
    """Patch httpx.AsyncClient so no request can leave the process."""
    recorder = _Recorder()

    response = MagicMock()
    response.json.return_value = {"charge": "CH-4200"}
    response.status_code = 200
    response.text = '{"charge": "CH-4200"}'
    response.headers = {"content-type": "application/json"}

    async def _request(**kwargs):
        recorder.calls.append(kwargs)
        if recorder.hang_on_get and str(kwargs.get("method", "")).upper() == "GET":
            # The worker dies here. asyncio.wait_for cancels the task, exactly
            # as arq's job timeout or a SIGKILL would abandon it.
            await asyncio.sleep(3600)
        return response

    with patch("httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.request = AsyncMock(side_effect=_request)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client_cls.return_value = client
        yield recorder


@pytest.fixture
def workflows_on_disk(tmp_path):
    """Point ``workflows_dir`` at a tmp dir and return a writer for it.

    Recovery reloads the YAML through the same version-aware loader replay and
    fork use, which falls back to disk when the run has no ``workflow_version``.
    """
    from sandcastle.config import settings

    settings.workflows_dir = str(tmp_path)

    def _write(name: str, yaml_text: str) -> None:
        (tmp_path / f"{name}.yaml").write_text(yaml_text)

    return _write


async def _insert_stuck_run(
    workflow_name: str,
    *,
    recovery_attempts: int = 0,
    stale_seconds: int = 86_400,
    effect_scope_id=None,
) -> uuid.UUID:
    """Insert a run in exactly the state a dead worker leaves behind.

    RUNNING, started long enough ago that no live worker could still own it
    (``_recover_stuck_runs`` uses twice the job timeout), never completed.
    """
    run_id = uuid.uuid4()
    async with async_session() as session:
        session.add(
            Run(
                id=run_id,
                workflow_name=workflow_name,
                status=RunStatus.RUNNING,
                input_data={},
                started_at=datetime.now(timezone.utc)
                - timedelta(seconds=stale_seconds),
                recovery_attempts=recovery_attempts,
                effect_scope_id=effect_scope_id,
            )
        )
        await session.commit()
    return run_id


async def _drain_local_queue() -> None:
    """Await the in-process tasks ``enqueue_workflow`` spawns in local mode."""
    for _ in range(50):
        pending = [t for t in list(worker_mod._background_tasks) if not t.done()]
        if not pending:
            break
        await asyncio.gather(*pending, return_exceptions=True)
    worker_mod._background_tasks.clear()


async def _load_run(run_id: uuid.UUID) -> Run:
    async with async_session() as session:
        return await session.get(Run, run_id)


async def _crash_after_the_charge(run_id: uuid.UUID, recorder: _Recorder) -> None:
    """Execute the run for real, then abandon it mid-flight.

    The POST lands and commits to the ledger; the GET hangs and the task is
    cancelled underneath it. The run row is put back into the stranded RUNNING
    state a killed worker would have left, because ``run_workflow_job``'s own
    failure handling never gets to run when the process dies.
    """
    from sandcastle.engine.dag import build_plan, parse_yaml_string
    from sandcastle.engine.executor import execute_workflow
    from sandcastle.engine.storage import create_storage

    workflow = parse_yaml_string(WF_POST_THEN_POLL)
    recorder.hang_on_get = True
    task = asyncio.create_task(
        execute_workflow(
            workflow=workflow,
            plan=build_plan(workflow),
            input_data={},
            run_id=str(run_id),
            storage=create_storage(),
            effect_scope_id=str(run_id),
        )
    )
    # Long enough for the POST to commit and the GET to start hanging.
    await asyncio.sleep(0.25)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    recorder.hang_on_get = False


# ---------------------------------------------------------------------------
# The headline guarantee
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crashed_run_resumes_and_the_post_fires_exactly_once(
    patched_httpx, workflows_on_disk
):
    """The load-bearing guarantee of workstream 3.

    A worker dies with a committed POST behind it. Recovery requeues the run,
    it replays from the top, and the POST does not fire again - it comes back
    out of the ledger, billed at $0.
    """
    workflows_on_disk("crash-resume-post-then-poll", WF_POST_THEN_POLL)
    run_id = await _insert_stuck_run("crash-resume-post-then-poll")

    await _crash_after_the_charge(run_id, patched_httpx)
    assert patched_httpx.count("POST") == 1, "the charge should have landed once"

    await _recover_stuck_runs()
    await _drain_local_queue()

    assert patched_httpx.count("POST") == 1  # <-- THE assertion

    # The live half of the workflow did re-run - otherwise the test would pass
    # on a run that never restarted at all.
    assert patched_httpx.count("GET") == 2

    run = await _load_run(run_id)
    assert run.status == RunStatus.COMPLETED, run.error
    assert run.recovery_attempts == 1

    # The memoized prefix is billed at $0 and says so in the Black Box.
    async with async_session() as session:
        charge_step = await session.scalar(
            select(RunStep).where(
                RunStep.run_id == run_id, RunStep.step_id == "charge"
            )
        )
    assert charge_step.replayed is True
    assert charge_step.cost_usd == 0.0


@pytest.mark.asyncio
async def test_recovery_keeps_the_run_in_its_own_effect_scope(
    patched_httpx, workflows_on_disk
):
    """Anti-false-pass guard for the headline test.

    The POST above is suppressed only because the requeued run replays in the
    scope the effect was claimed in. Pin that: the ledger row and the run must
    agree on the scope after recovery.
    """
    workflows_on_disk("crash-resume-post-then-poll", WF_POST_THEN_POLL)
    run_id = await _insert_stuck_run("crash-resume-post-then-poll")

    await _crash_after_the_charge(run_id, patched_httpx)
    await _recover_stuck_runs()
    await _drain_local_queue()

    run = await _load_run(run_id)
    assert run.effect_scope_id == run_id

    async with async_session() as session:
        rows = (
            await session.scalars(
                select(StepEffect).where(StepEffect.effect_scope_id == str(run_id))
            )
        ).all()
    assert rows, "the charge should have left a ledger row in the run's own scope"


@pytest.mark.asyncio
async def test_a_fresh_scope_would_fire_the_post_again(patched_httpx, workflows_on_disk):
    """The ledger is doing the work, not an accident of scheduling.

    Same crash, but the replay runs in an unrelated scope. The POST fires
    again - which is what makes the headline assertion meaningful.
    """
    from sandcastle.engine.dag import build_plan, parse_yaml_string
    from sandcastle.engine.executor import execute_workflow
    from sandcastle.engine.storage import create_storage

    workflows_on_disk("crash-resume-post-then-poll", WF_POST_THEN_POLL)
    run_id = await _insert_stuck_run("crash-resume-post-then-poll")
    await _crash_after_the_charge(run_id, patched_httpx)

    workflow = parse_yaml_string(WF_POST_THEN_POLL)
    await execute_workflow(
        workflow=workflow,
        plan=build_plan(workflow),
        input_data={},
        run_id=str(uuid.uuid4()),
        storage=create_storage(),
        effect_scope_id=str(uuid.uuid4()),
    )

    assert patched_httpx.count("POST") == 2


# ---------------------------------------------------------------------------
# Bounds: a poison run must not requeue forever
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attempt_cap_fails_the_run_with_the_attempt_count(workflows_on_disk):
    """A run past its cap is FAILED, and the error names the attempts."""
    from sandcastle.config import settings

    settings.max_recovery_attempts = 2
    workflows_on_disk("crash-resume-post-then-poll", WF_POST_THEN_POLL)
    run_id = await _insert_stuck_run(
        "crash-resume-post-then-poll", recovery_attempts=2
    )

    await _recover_stuck_runs()
    await _drain_local_queue()

    run = await _load_run(run_id)
    assert run.status == RunStatus.FAILED
    assert "2 recovery attempt(s)" in run.error
    assert "max_recovery_attempts=2" in run.error
    assert run.recovery_attempts == 2, "a refused recovery must not count as one"


@pytest.mark.asyncio
async def test_attempt_cap_error_names_the_step_it_died_on(workflows_on_disk):
    """Poison-run diagnostics: say *where* it kept dying when that is knowable.

    ``run_steps`` rows are written as RUNNING before a step executes, so a step
    still marked RUNNING on a stranded run is the one that was in flight.
    """
    from sandcastle.config import settings

    settings.max_recovery_attempts = 1
    workflows_on_disk("crash-resume-post-then-poll", WF_POST_THEN_POLL)
    run_id = await _insert_stuck_run(
        "crash-resume-post-then-poll", recovery_attempts=1
    )
    async with async_session() as session:
        session.add(
            RunStep(
                run_id=run_id,
                step_id="poll",
                status="running",
                started_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    await _recover_stuck_runs()

    run = await _load_run(run_id)
    assert run.status == RunStatus.FAILED
    assert "while executing step 'poll'" in run.error


@pytest.mark.asyncio
async def test_each_recovery_increments_the_durable_counter(workflows_on_disk):
    """The bound is durable, not process-local: it survives the crash it counts."""
    from sandcastle.config import settings

    settings.max_recovery_attempts = 3
    workflows_on_disk("crash-resume-post-then-poll", WF_POST_THEN_POLL)
    run_id = await _insert_stuck_run("crash-resume-post-then-poll")

    with patch(
        "sandcastle.queue.worker.enqueue_workflow", new_callable=AsyncMock
    ) as enqueued:
        await _recover_stuck_runs()
        assert (await _load_run(run_id)).recovery_attempts == 1
        assert enqueued.await_args.kwargs["job_id"].endswith(":recovery:1")

        # Crash it again.
        async with async_session() as session:
            run = await session.get(Run, run_id)
            run.status = RunStatus.RUNNING
            run.started_at = datetime.now(timezone.utc) - timedelta(days=1)
            await session.commit()

        await _recover_stuck_runs()
        assert (await _load_run(run_id)).recovery_attempts == 2
        assert enqueued.await_args.kwargs["job_id"].endswith(":recovery:2")


# ---------------------------------------------------------------------------
# Refusals: where FAILED is still the right answer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_opt_out_restores_the_pre_046_behaviour(workflows_on_disk):
    """``crash_resume_enabled=False`` means FAILED, as before, with a reason."""
    from sandcastle.config import settings

    settings.crash_resume_enabled = False
    workflows_on_disk("crash-resume-post-then-poll", WF_POST_THEN_POLL)
    run_id = await _insert_stuck_run("crash-resume-post-then-poll")

    with patch(
        "sandcastle.queue.worker.enqueue_workflow", new_callable=AsyncMock
    ) as enqueued:
        await _recover_stuck_runs()

    enqueued.assert_not_awaited()
    run = await _load_run(run_id)
    assert run.status == RunStatus.FAILED
    assert "crash resume is disabled" in run.error
    assert run.recovery_attempts == 0


@pytest.mark.asyncio
async def test_disabled_ledger_means_failed_not_resumed(workflows_on_disk):
    """Without the ledger a replay would re-fire everything. Do not replay."""
    from sandcastle.config import settings

    settings.effect_ledger_enabled = False
    workflows_on_disk("crash-resume-post-then-poll", WF_POST_THEN_POLL)
    run_id = await _insert_stuck_run("crash-resume-post-then-poll")

    with patch(
        "sandcastle.queue.worker.enqueue_workflow", new_callable=AsyncMock
    ) as enqueued:
        await _recover_stuck_runs()

    enqueued.assert_not_awaited()
    run = await _load_run(run_id)
    assert run.status == RunStatus.FAILED
    assert "step effect ledger is disabled" in run.error


@pytest.mark.asyncio
async def test_unreachable_ledger_means_failed_not_resumed(workflows_on_disk):
    """A ledger the worker cannot read is a fault, and failing closed is 0.45."""
    workflows_on_disk("crash-resume-post-then-poll", WF_POST_THEN_POLL)
    run_id = await _insert_stuck_run("crash-resume-post-then-poll")

    with (
        patch(
            "sandcastle.engine.effects.EffectLedger.lookup",
            new_callable=AsyncMock,
            side_effect=RuntimeError("no such table: run_step_effects"),
        ),
        patch(
            "sandcastle.queue.worker.enqueue_workflow", new_callable=AsyncMock
        ) as enqueued,
    ):
        await _recover_stuck_runs()

    enqueued.assert_not_awaited()
    run = await _load_run(run_id)
    assert run.status == RunStatus.FAILED
    assert "unreachable" in run.error
    assert "no such table" in run.error


@pytest.mark.asyncio
async def test_a_run_whose_workflow_vanished_is_failed_not_left_running(
    workflows_on_disk,
):
    """No YAML, no replay - but the run must not stay stranded either."""
    workflows_on_disk("something-else", WF_POST_THEN_POLL)
    run_id = await _insert_stuck_run("workflow-that-was-deleted")

    await _recover_stuck_runs()

    run = await _load_run(run_id)
    assert run.status == RunStatus.FAILED
    assert "could not be loaded" in run.error


# ---------------------------------------------------------------------------
# Half-completed effects
# ---------------------------------------------------------------------------


WF_SINGLE_POST = """
name: crash-resume-single-post
description: One POST, nothing else
default_model: sonnet
steps:
  - id: charge
    type: http
    http_config:
      url: https://billing.internal/v1/charges
      method: POST
      body: '{"amount": 4200}'
"""


@pytest.mark.asyncio
async def test_in_flight_claim_fails_the_step_rather_than_re_firing(
    patched_httpx, workflows_on_disk
):
    """The uncertain case, reached through recovery rather than replay.

    A worker died *during* the POST: the claim row is in_flight and past its
    lease, so nobody knows whether the charge landed. ``on_uncertain`` defaults
    to ``fail``, and the recovered run must respect that instead of charging
    the card a second time.
    """
    from sandcastle.engine.dag import parse_yaml_string
    from sandcastle.engine.effects import (
        EFFECT_IN_FLIGHT,
        EffectLedger,
        compute_effect_key,
        step_effect_fingerprint,
    )
    from sandcastle.engine.executor import RunContext

    workflows_on_disk("crash-resume-single-post", WF_SINGLE_POST)
    run_id = await _insert_stuck_run("crash-resume-single-post")

    # Plant the abandoned claim the dead worker left behind, keyed exactly as
    # the executor's guard will key it on the replay.
    workflow = parse_yaml_string(WF_SINGLE_POST)
    step = workflow.steps[0]
    context = RunContext(run_id=str(run_id), input={})
    effect_key = compute_effect_key(
        str(run_id),
        None,
        step.id,
        step_effect_fingerprint(step, context),
    )
    async with async_session() as session:
        session.add(
            StepEffect(
                id=uuid.uuid4(),
                effect_key=effect_key,
                effect_scope_id=str(run_id),
                run_id=str(run_id),
                step_id=step.id,
                step_type=step.type,
                status=EFFECT_IN_FLIGHT,
                created_at=datetime.now(timezone.utc) - timedelta(hours=2),
                lease_expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            )
        )
        await session.commit()

    await _recover_stuck_runs()
    await _drain_local_queue()

    assert patched_httpx.count("POST") == 0, "an uncertain effect must not re-fire"
    run = await _load_run(run_id)
    assert run.status == RunStatus.FAILED
    assert "EffectUncertain" in (run.error or "")

    ledger = EffectLedger()
    row = await ledger.lookup(effect_key)
    assert row["status"] == EFFECT_IN_FLIGHT, "the claim stays unresolved, not settled"


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_alembic_head_is_023_and_single():
    """023 is the head, and there is exactly one.

    This repo has already had a three-way collision at 019. Two workstreams are
    building off this branch in parallel, so the guard is not theoretical.
    """
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()

    assert list(heads) == ["023"], f"expected a single head 023, got {heads}"


def test_recovery_attempts_column_matches_the_model():
    """The migration and the model must not drift - `alembic check` reads both."""
    column = Run.__table__.c.recovery_attempts
    assert column.nullable is False
    assert column.server_default.arg == "0"


# ---------------------------------------------------------------------------
# The money claim
# ---------------------------------------------------------------------------


WF_LLM_THEN_POLL = """
name: crash-resume-llm-then-poll
description: A paid extraction, then a poll that hangs the first time
default_model: sonnet
steps:
  - id: extract
    type: llm
    prompt: Extract the invoice total.
  - id: poll
    type: http
    depends_on: [extract]
    http_config:
      url: https://billing.internal/v1/status
      method: GET
"""


@pytest.mark.asyncio
async def test_the_memoized_prefix_costs_nothing_the_second_time(
    patched_httpx, workflows_on_disk
):
    """"Steps 1..N cost $0 the second time" as an assertion, not a slogan.

    The http headline test proves nothing about money - an http step costs
    zero either way. This one puts a paid ``llm`` step in front of the crash:
    it bills $0.42 before the worker dies and $0.00 after recovery, and the
    provider is called exactly once across both.
    """
    from sandcastle.engine.dag import build_plan, parse_yaml_string
    from sandcastle.engine.executor import StepResult, execute_workflow
    from sandcastle.engine.storage import create_storage

    workflows_on_disk("crash-resume-llm-then-poll", WF_LLM_THEN_POLL)
    run_id = await _insert_stuck_run("crash-resume-llm-then-poll")

    calls = {"n": 0}

    async def _fake_llm(step, context, storage):
        calls["n"] += 1
        return StepResult(
            step_id=step.id, output="42", cost_usd=0.42, status="completed"
        )

    with patch("sandcastle.engine.executor._execute_llm_step", side_effect=_fake_llm):
        workflow = parse_yaml_string(WF_LLM_THEN_POLL)
        patched_httpx.hang_on_get = True
        crashing = asyncio.create_task(
            execute_workflow(
                workflow=workflow,
                plan=build_plan(workflow),
                input_data={},
                run_id=str(run_id),
                storage=create_storage(),
                effect_scope_id=str(run_id),
            )
        )
        await asyncio.sleep(0.25)
        crashing.cancel()
        await asyncio.gather(crashing, return_exceptions=True)
        patched_httpx.hang_on_get = False
        assert calls["n"] == 1

        await _recover_stuck_runs()
        await _drain_local_queue()

    assert calls["n"] == 1, "the paid step must not be paid for twice"
    run = await _load_run(run_id)
    assert run.status == RunStatus.COMPLETED, run.error
    assert run.total_cost_usd == pytest.approx(0.0)

    async with async_session() as session:
        extract_step = await session.scalar(
            select(RunStep).where(
                RunStep.run_id == run_id, RunStep.step_id == "extract"
            )
        )
    assert extract_step.replayed is True
    assert extract_step.cost_usd == pytest.approx(0.0)
    assert extract_step.original_cost_usd == pytest.approx(0.42)
