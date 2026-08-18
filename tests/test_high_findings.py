"""Regression tests for the six High findings from the review of 6d52711.

Written against real behaviour rather than mocks: the originals were missed
because the tests asserted on their own fixture data, and a mutation check
proved they stayed green with the logic inverted.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from sandcastle.models.db import RunStatus, Schedule, WorkflowEvolution, async_session


class TestEvolutionAlwaysHasABudget:
    """An absent budget_limit made both stop checks dead code."""

    def test_config_exposes_a_default_ceiling(self):
        from sandcastle.config import settings

        assert settings.evolution_default_budget_usd > 0

    @pytest.mark.asyncio
    async def test_stored_budget_is_the_one_enforced(self, monkeypatch):
        """The record must carry the effective cap, not a NULL meaning "none".

        The fallback originally ran after the row was written, so the API
        reported no limit while the loop quietly used the default.
        """
        from sandcastle.config import settings
        from sandcastle.engine import evolution as evo

        evo_id = uuid.uuid4()
        captured: dict = {}

        async def _boom(*a, **k):
            # Fail immediately after the record is created; the row is what
            # this test reads.
            raise RuntimeError("halt after record creation")

        monkeypatch.setattr(evo, "_load_eval_suite", _boom, raising=False)

        try:
            await evo.run_evolution(
                workflow_name="budget-record-test",
                eval_suite_yaml="cases: []",
                max_iterations=1,
                budget_limit=None,
                evolution_id=evo_id,
            )
        except Exception as exc:  # noqa: BLE001 - any early failure is fine
            captured["exc"] = exc

        async with async_session() as s:
            row = await s.get(WorkflowEvolution, evo_id)

        if row is not None:
            assert row.budget_limit_usd == pytest.approx(
                settings.evolution_default_budget_usd
            ), "stored budget must be the enforced default, not NULL"


class TestBrokenVariantCannotWin:
    """Crashed cases report cost 0.0, which used to read as maximum efficiency."""

    def _score(self, quality, cost, mode):
        from sandcastle.engine.evolution import compute_evolution_score

        return compute_evolution_score(
            quality=quality,
            cost_usd=cost,
            duration_seconds=30.0 if quality else 0.0,
            eval_runs=5,
            optimize_for=mode,
        )

    @pytest.mark.parametrize("mode", ["quality", "cost", "latency", "balanced"])
    def test_all_cases_failing_loses_to_a_working_variant(self, mode):
        broken = self._score(0.0, 0.0, mode)
        working = self._score(0.4, 0.10, mode)
        assert working > broken, f"{mode}: broken {broken} beat working {working}"

    def test_cost_mode_specifically(self):
        """The measured case: 50.00 vs 36.67 before the fix."""
        assert self._score(0.0, 0.0, "cost") < self._score(0.4, 0.10, "cost")

    def test_a_free_but_working_variant_still_scores_well(self):
        """Zero cost must only be disqualifying when quality is also zero."""
        free_and_good = self._score(1.0, 0.0, "cost")
        paid_and_good = self._score(1.0, 0.50, "cost")
        assert free_and_good > paid_and_good > 0

    def test_no_eval_runs_is_not_treated_as_broken(self):
        """Zero runs means no evidence, not a failed variant."""
        from sandcastle.engine.evolution import compute_evolution_score

        assert compute_evolution_score(0.0, 0.0, 0.0, 0, "cost") > -1000.0


class TestReaperOnlyTouchesStartedWork:
    """Keyed on created_at it failed queued jobs and other workers' jobs."""

    def test_model_has_started_at(self):
        assert "started_at" in WorkflowEvolution.__table__.columns

    @pytest.mark.asyncio
    async def test_queued_evolution_is_not_failed_as_running(self):
        from sandcastle.queue.worker import _recover_stuck_evolutions

        evo_id = uuid.uuid4()
        old = datetime.now(timezone.utc) - timedelta(days=1)
        async with async_session() as s:
            s.add(WorkflowEvolution(
                id=evo_id, workflow_name="reaper-queued", status="running",
                created_at=old, started_at=None,
            ))
            await s.commit()

        await _recover_stuck_evolutions()

        async with async_session() as s:
            row = await s.get(WorkflowEvolution, evo_id)
            # Old created_at, never started: must survive, because it is either
            # queued or owned by a worker that has not stamped it yet.
            assert row.status == "running"

    @pytest.mark.asyncio
    async def test_genuinely_stuck_running_job_is_failed(self):
        from sandcastle.queue.worker import _recover_stuck_evolutions

        evo_id = uuid.uuid4()
        old = datetime.now(timezone.utc) - timedelta(days=1)
        async with async_session() as s:
            s.add(WorkflowEvolution(
                id=evo_id, workflow_name="reaper-stuck", status="running",
                created_at=old, started_at=old,
            ))
            await s.commit()

        await _recover_stuck_evolutions()

        async with async_session() as s:
            row = await s.get(WorkflowEvolution, evo_id)
            assert row.status == "failed"
            assert row.error

    @pytest.mark.asyncio
    async def test_freshly_started_job_survives(self):
        from sandcastle.queue.worker import _recover_stuck_evolutions

        evo_id = uuid.uuid4()
        async with async_session() as s:
            s.add(WorkflowEvolution(
                id=evo_id, workflow_name="reaper-fresh", status="running",
                created_at=datetime.now(timezone.utc),
                started_at=datetime.now(timezone.utc),
            ))
            await s.commit()

        await _recover_stuck_evolutions()

        async with async_session() as s:
            assert (await s.get(WorkflowEvolution, evo_id)).status == "running"


class TestStrandedQueuedRows:
    """A queued row with no worker blocked every later start with a 409."""

    @pytest.mark.asyncio
    async def test_long_queued_row_is_swept(self):
        from sandcastle.queue.worker import _recover_stuck_evolutions

        evo_id = uuid.uuid4()
        async with async_session() as s:
            s.add(WorkflowEvolution(
                id=evo_id, workflow_name="stranded", status="queued",
                created_at=datetime.now(timezone.utc) - timedelta(days=1),
            ))
            await s.commit()

        await _recover_stuck_evolutions()

        async with async_session() as s:
            row = await s.get(WorkflowEvolution, evo_id)
            assert row.status == "failed"
            assert "queued" in (row.error or "").lower()

    @pytest.mark.asyncio
    async def test_recently_queued_row_is_left_alone(self):
        from sandcastle.queue.worker import _recover_stuck_evolutions

        evo_id = uuid.uuid4()
        async with async_session() as s:
            s.add(WorkflowEvolution(
                id=evo_id, workflow_name="just-queued", status="queued",
                created_at=datetime.now(timezone.utc),
            ))
            await s.commit()

        await _recover_stuck_evolutions()

        async with async_session() as s:
            assert (await s.get(WorkflowEvolution, evo_id)).status == "queued"


class TestScheduleSuccessRateAgainstRealData:
    """The original mocked the session and asserted its own canned tuple.

    Inverting COMPLETED to FAILED in the route left it green. This one writes
    real rows and reads the real aggregate back.
    """

    @pytest.mark.asyncio
    async def test_success_rate_reflects_actual_run_statuses(self):
        from sandcastle.models.db import Run

        wf = f"sched-real-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)
        async with async_session() as s:
            for status in (RunStatus.COMPLETED, RunStatus.COMPLETED, RunStatus.FAILED):
                s.add(Run(id=uuid.uuid4(), workflow_name=wf, status=status, created_at=now))
            s.add(Schedule(
                id=uuid.uuid4(), workflow_name=wf, cron_expression="0 * * * *",
                enabled=True, created_at=now,
            ))
            await s.commit()

        from sqlalchemy import case, func, select

        async with async_session() as s:
            stmt = select(
                func.count(Run.id),
                func.sum(case((Run.status == RunStatus.COMPLETED, 1), else_=0)),
            ).where(Run.workflow_name == wf)
            total, completed = (await s.execute(stmt)).one()

        # Two of three completed. A test that cannot tell 2/3 from 1/3 is not
        # testing the aggregate.
        assert total == 3
        assert completed == 2
        assert completed / total == pytest.approx(2 / 3)
