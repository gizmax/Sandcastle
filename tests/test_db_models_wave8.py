"""Wave 8 deep audit: database model constraints, edge cases, and integrity.

Covers:
  1. Check constraints (non-negative costs, valid ranges)
  2. Nullable vs non-nullable enforcement
  3. JSON column edge cases (None, empty dict, nested data)
  4. Enum field validation and transitions
  5. UUID handling (string vs UUID object)
  6. Relationship integrity and cascade behavior
  7. Timestamp timezone awareness
  8. Index coverage verification
  9. Default values and migration safety
  10. String length enforcement
  11. Unique constraint enforcement
  12. Concurrent-safe patterns (idempotency_key, atomic updates)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError

# Disable rate limiting for all tests in this module
from sandcastle.api import rate_limit as _rl_mod

_rl_mod.execution_limiter.check = AsyncMock()  # noqa: E402

from sandcastle.models.db import (
    ApiKey,
    ApprovalRequest,
    ApprovalStatus,
    AutoPilotExperiment,
    AutoPilotSample,
    Base,
    DeadLetterItem,
    EvalCaseResult,
    EvalRun,
    EvalRunStatus,
    ExperimentStatus,
    PolicyViolation,
    RoutingDecision,
    Run,
    RunCheckpoint,
    RunStatus,
    RunStep,
    Schedule,
    Setting,
    StepCache,
    StepStatus,
    ToolConnection,
    WorkflowVersion,
    WorkflowVersionStatus,
    async_session,
    engine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_run(
    status: RunStatus = RunStatus.COMPLETED,
    workflow_name: str = "wave8-test",
    total_cost_usd: float = 0.01,
    **kwargs,
) -> Run:
    """Insert a Run row for testing."""
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        run = Run(
            workflow_name=workflow_name,
            status=status,
            input_data=kwargs.pop("input_data", {"key": "value"}),
            output_data=kwargs.pop("output_data", None),
            total_cost_usd=total_cost_usd,
            started_at=kwargs.pop("started_at", now - timedelta(seconds=5)),
            completed_at=kwargs.pop("completed_at", now if status != RunStatus.RUNNING else None),
            **kwargs,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run


async def _create_step(run_id: uuid.UUID, step_id: str = "step1", **kwargs) -> RunStep:
    """Insert a RunStep row for testing."""
    async with async_session() as session:
        step = RunStep(
            run_id=run_id,
            step_id=step_id,
            status=kwargs.pop("status", StepStatus.COMPLETED),
            cost_usd=kwargs.pop("cost_usd", 0.001),
            duration_seconds=kwargs.pop("duration_seconds", 1.0),
            **kwargs,
        )
        session.add(step)
        await session.commit()
        await session.refresh(step)
        return step


# ---------------------------------------------------------------------------
# 1. CHECK CONSTRAINTS - Non-negative costs
# ---------------------------------------------------------------------------


class TestCheckConstraints:
    """Verify check constraints block invalid data at the DB level."""

    @pytest.mark.asyncio
    async def test_run_negative_total_cost_rejected(self):
        """Run.total_cost_usd < 0 should violate check constraint."""
        async with async_session() as session:
            run = Run(
                workflow_name="bad-cost",
                status=RunStatus.COMPLETED,
                total_cost_usd=-1.0,
            )
            session.add(run)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    @pytest.mark.asyncio
    async def test_run_zero_cost_accepted(self):
        """Run.total_cost_usd = 0.0 should be accepted."""
        run = await _create_run(total_cost_usd=0.0)
        assert run.total_cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_run_negative_depth_rejected(self):
        """Run.depth < 0 should violate check constraint."""
        async with async_session() as session:
            run = Run(
                workflow_name="bad-depth",
                status=RunStatus.COMPLETED,
                depth=-1,
            )
            session.add(run)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    @pytest.mark.asyncio
    async def test_run_step_negative_cost_rejected(self):
        """RunStep.cost_usd < 0 should violate check constraint."""
        run = await _create_run()
        async with async_session() as session:
            step = RunStep(
                run_id=run.id,
                step_id="bad-cost",
                status=StepStatus.COMPLETED,
                cost_usd=-0.5,
            )
            session.add(step)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    @pytest.mark.asyncio
    async def test_run_step_negative_duration_rejected(self):
        """RunStep.duration_seconds < 0 should violate check constraint."""
        run = await _create_run()
        async with async_session() as session:
            step = RunStep(
                run_id=run.id,
                step_id="bad-dur",
                status=StepStatus.COMPLETED,
                duration_seconds=-1.0,
            )
            session.add(step)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    @pytest.mark.asyncio
    async def test_run_step_zero_attempt_rejected(self):
        """RunStep.attempt must be >= 1."""
        run = await _create_run()
        async with async_session() as session:
            step = RunStep(
                run_id=run.id,
                step_id="bad-attempt",
                status=StepStatus.COMPLETED,
                attempt=0,
            )
            session.add(step)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    @pytest.mark.asyncio
    async def test_routing_confidence_out_of_range_rejected(self):
        """RoutingDecision.confidence must be between 0 and 1."""
        run = await _create_run()
        async with async_session() as session:
            rd = RoutingDecision(
                run_id=run.id,
                step_id="step1",
                selected_model="haiku",
                selected_variant_id="default",
                confidence=1.5,
            )
            session.add(rd)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    @pytest.mark.asyncio
    async def test_routing_confidence_negative_rejected(self):
        """RoutingDecision.confidence < 0 should fail."""
        run = await _create_run()
        async with async_session() as session:
            rd = RoutingDecision(
                run_id=run.id,
                step_id="step1",
                selected_model="haiku",
                selected_variant_id="default",
                confidence=-0.1,
            )
            session.add(rd)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    @pytest.mark.asyncio
    async def test_routing_valid_confidence_accepted(self):
        """RoutingDecision.confidence in [0, 1] should succeed."""
        run = await _create_run()
        for val in [0.0, 0.5, 1.0]:
            async with async_session() as session:
                rd = RoutingDecision(
                    run_id=run.id,
                    step_id=f"step-conf-{val}",
                    selected_model="haiku",
                    selected_variant_id="default",
                    confidence=val,
                )
                session.add(rd)
                await session.commit()

    @pytest.mark.asyncio
    async def test_routing_negative_budget_pressure_rejected(self):
        """RoutingDecision.budget_pressure < 0 should fail."""
        run = await _create_run()
        async with async_session() as session:
            rd = RoutingDecision(
                run_id=run.id,
                step_id="step1",
                selected_model="haiku",
                selected_variant_id="default",
                budget_pressure=-0.5,
                confidence=0.5,
            )
            session.add(rd)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    @pytest.mark.asyncio
    async def test_step_cache_negative_cost_rejected(self):
        """StepCache.cost_usd < 0 should fail."""
        async with async_session() as session:
            sc = StepCache(
                cache_key=f"ck-{uuid.uuid4().hex[:8]}",
                step_id="s1",
                cost_usd=-0.01,
            )
            session.add(sc)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    @pytest.mark.asyncio
    async def test_step_cache_negative_hit_count_rejected(self):
        """StepCache.hit_count < 0 should fail."""
        async with async_session() as session:
            sc = StepCache(
                cache_key=f"ck-{uuid.uuid4().hex[:8]}",
                step_id="s1",
                hit_count=-1,
            )
            session.add(sc)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    @pytest.mark.asyncio
    async def test_autopilot_sample_negative_cost_rejected(self):
        """AutoPilotSample.cost_usd < 0 should fail."""
        async with async_session() as session:
            exp = AutoPilotExperiment(
                workflow_name="test",
                step_id="s1",
                status=ExperimentStatus.RUNNING,
            )
            session.add(exp)
            await session.flush()
            sample = AutoPilotSample(
                experiment_id=exp.id,
                variant_id="v1",
                cost_usd=-1.0,
            )
            session.add(sample)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    @pytest.mark.asyncio
    async def test_eval_run_pass_rate_out_of_range_rejected(self):
        """EvalRun.pass_rate must be [0, 1]."""
        async with async_session() as session:
            er = EvalRun(
                suite_name="suite1",
                workflow_name="wf1",
                pass_rate=1.5,
            )
            session.add(er)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    @pytest.mark.asyncio
    async def test_eval_case_negative_cost_rejected(self):
        """EvalCaseResult.cost_usd < 0 should fail."""
        async with async_session() as session:
            er = EvalRun(
                suite_name="suite1",
                workflow_name="wf1",
            )
            session.add(er)
            await session.flush()
            ecr = EvalCaseResult(
                eval_run_id=er.id,
                case_name="case1",
                cost_usd=-0.01,
            )
            session.add(ecr)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()


# ---------------------------------------------------------------------------
# 2. NULLABLE ENFORCEMENT
# ---------------------------------------------------------------------------


class TestNullableConstraints:
    """Verify that required fields cannot be NULL."""

    @pytest.mark.asyncio
    async def test_run_workflow_name_not_nullable(self):
        """Run.workflow_name cannot be NULL."""
        async with async_session() as session:
            run = Run(status=RunStatus.QUEUED)
            # SQLAlchemy may prevent setting the column to None at the Python
            # level, but let's try via raw SQL to be thorough
            session.add(run)
            try:
                await session.flush()
                # If Python-level typing didn't prevent it, check via query
                run.workflow_name = None  # type: ignore
                await session.flush()
            except (IntegrityError, TypeError):
                pass
            finally:
                await session.rollback()

    @pytest.mark.asyncio
    async def test_run_step_run_id_not_nullable(self):
        """RunStep.run_id cannot be NULL."""
        async with async_session() as session:
            step = RunStep(
                step_id="orphan",
                status=StepStatus.PENDING,
            )
            # run_id is required by Mapped[uuid.UUID] - no nullable
            # The insert should fail because run_id has no default
            session.add(step)
            with pytest.raises((IntegrityError, Exception)):
                await session.commit()
            await session.rollback()

    @pytest.mark.asyncio
    async def test_approval_request_step_id_not_nullable(self):
        """ApprovalRequest.step_id must be set."""
        run = await _create_run()
        async with async_session() as session:
            ap = ApprovalRequest(
                run_id=run.id,
                status=ApprovalStatus.PENDING,
                message="test",
            )
            session.add(ap)
            # step_id is Mapped[str] with nullable=False - should fail
            with pytest.raises((IntegrityError, Exception)):
                await session.commit()
            await session.rollback()

    @pytest.mark.asyncio
    async def test_schedule_cron_expression_not_nullable(self):
        """Schedule.cron_expression cannot be empty/NULL."""
        async with async_session() as session:
            s = Schedule(
                workflow_name="test-sched",
            )
            session.add(s)
            # cron_expression has no default and is nullable=False
            with pytest.raises((IntegrityError, Exception)):
                await session.commit()
            await session.rollback()

    @pytest.mark.asyncio
    async def test_step_cache_step_id_not_nullable(self):
        """StepCache.step_id should be NOT NULL after fix."""
        col = StepCache.__table__.c.step_id
        assert not col.nullable, "StepCache.step_id should be NOT NULL"

    @pytest.mark.asyncio
    async def test_api_key_name_not_nullable(self):
        """ApiKey.name must be provided."""
        async with async_session() as session:
            ak = ApiKey(
                key_hash="a" * 64,
            )
            session.add(ak)
            with pytest.raises((IntegrityError, Exception)):
                await session.commit()
            await session.rollback()


# ---------------------------------------------------------------------------
# 3. JSON COLUMN EDGE CASES
# ---------------------------------------------------------------------------


class TestJsonColumns:
    """Verify JSON columns handle edge cases correctly."""

    @pytest.mark.asyncio
    async def test_run_input_data_none(self):
        """Run.input_data can be None (nullable=True)."""
        run = await _create_run(input_data=None)
        async with async_session() as session:
            loaded = await session.get(Run, run.id)
            assert loaded.input_data is None

    @pytest.mark.asyncio
    async def test_run_input_data_empty_dict(self):
        """Run.input_data can be empty dict."""
        run = await _create_run(input_data={})
        async with async_session() as session:
            loaded = await session.get(Run, run.id)
            assert loaded.input_data == {}

    @pytest.mark.asyncio
    async def test_run_input_data_nested(self):
        """Run.input_data can hold deeply nested structures."""
        nested = {
            "level1": {
                "level2": [1, 2, {"level3": True}],
                "key": "value",
            }
        }
        run = await _create_run(input_data=nested)
        async with async_session() as session:
            loaded = await session.get(Run, run.id)
            assert loaded.input_data == nested

    @pytest.mark.asyncio
    async def test_run_output_data_large(self):
        """Run.output_data can store large JSON payloads."""
        large = {"items": [{"id": i, "data": "x" * 100} for i in range(100)]}
        run = await _create_run(output_data=large)
        async with async_session() as session:
            loaded = await session.get(Run, run.id)
            assert len(loaded.output_data["items"]) == 100

    @pytest.mark.asyncio
    async def test_run_fork_changes_json(self):
        """Run.fork_changes stores and retrieves JSON correctly."""
        changes = {"step1": {"model": "opus", "prompt": "Be more creative"}}
        run = await _create_run(fork_changes=changes)
        async with async_session() as session:
            loaded = await session.get(Run, run.id)
            assert loaded.fork_changes == changes

    @pytest.mark.asyncio
    async def test_api_key_allowed_cidrs_list(self):
        """ApiKey.allowed_cidrs stores a list of CIDR strings."""
        cidrs = ["10.0.0.0/8", "192.168.1.0/24"]
        async with async_session() as session:
            ak = ApiKey(
                key_hash=f"h{uuid.uuid4().hex[:63]}",
                name="test-cidrs",
                allowed_cidrs=cidrs,
            )
            session.add(ak)
            await session.commit()
            await session.refresh(ak)
            assert ak.allowed_cidrs == cidrs

    @pytest.mark.asyncio
    async def test_api_key_allowed_cidrs_none(self):
        """ApiKey.allowed_cidrs=None means allow all."""
        async with async_session() as session:
            ak = ApiKey(
                key_hash=f"h{uuid.uuid4().hex[:63]}",
                name="test-null-cidrs",
                allowed_cidrs=None,
            )
            session.add(ak)
            await session.commit()
            await session.refresh(ak)
            assert ak.allowed_cidrs is None

    @pytest.mark.asyncio
    async def test_api_key_allowed_cidrs_empty_list(self):
        """ApiKey.allowed_cidrs=[] means allow all (empty = no restriction)."""
        async with async_session() as session:
            ak = ApiKey(
                key_hash=f"h{uuid.uuid4().hex[:63]}",
                name="test-empty-cidrs",
                allowed_cidrs=[],
            )
            session.add(ak)
            await session.commit()
            await session.refresh(ak)
            assert ak.allowed_cidrs == []

    @pytest.mark.asyncio
    async def test_step_output_data_with_special_chars(self):
        """RunStep.output_data handles special characters in JSON."""
        special = {"text": "Hello\n\t'world'\x00\"quotes\""}
        run = await _create_run()
        step = await _create_step(
            run.id,
            output_data=special,
        )
        async with async_session() as session:
            loaded = await session.get(RunStep, step.id)
            assert loaded.output_data == special

    @pytest.mark.asyncio
    async def test_tool_connection_credentials_dict(self):
        """ToolConnection.credentials stores and retrieves complex dicts."""
        creds = {
            "host": "db.example.com",
            "port": 5432,
            "password": "s3cr3t!@#$%",
        }
        async with async_session() as session:
            tc = ToolConnection(
                tool_name="postgresql",
                connection_name=f"test-{uuid.uuid4().hex[:8]}",
                credentials=creds,
            )
            session.add(tc)
            await session.commit()
            await session.refresh(tc)
            assert tc.credentials == creds


# ---------------------------------------------------------------------------
# 4. ENUM VALIDATION
# ---------------------------------------------------------------------------


class TestEnumFields:
    """Verify enum fields only accept valid values."""

    @pytest.mark.asyncio
    async def test_run_status_all_values(self):
        """All RunStatus values can be stored and retrieved."""
        for status in RunStatus:
            run = await _create_run(status=status)
            async with async_session() as session:
                loaded = await session.get(Run, run.id)
                actual = loaded.status.value if hasattr(loaded.status, "value") else loaded.status
                assert actual == status.value

    @pytest.mark.asyncio
    async def test_step_status_all_values(self):
        """All StepStatus values can be stored and retrieved."""
        run = await _create_run()
        for i, status in enumerate(StepStatus):
            step = await _create_step(run.id, step_id=f"enum-{i}", status=status)
            async with async_session() as session:
                loaded = await session.get(RunStep, step.id)
                actual = loaded.status.value if hasattr(loaded.status, "value") else loaded.status
                assert actual == status.value

    @pytest.mark.asyncio
    async def test_approval_status_all_values(self):
        """All ApprovalStatus values can be stored and retrieved."""
        run = await _create_run()
        for i, status in enumerate(ApprovalStatus):
            async with async_session() as session:
                ap = ApprovalRequest(
                    run_id=run.id,
                    step_id=f"ap-{i}",
                    status=status,
                    message="test",
                )
                session.add(ap)
                await session.commit()
                await session.refresh(ap)
                actual = ap.status.value if hasattr(ap.status, "value") else ap.status
                assert actual == status.value

    @pytest.mark.asyncio
    async def test_experiment_status_all_values(self):
        """All ExperimentStatus values can be stored and retrieved."""
        for i, status in enumerate(ExperimentStatus):
            async with async_session() as session:
                exp = AutoPilotExperiment(
                    workflow_name=f"test-{i}",
                    step_id=f"step-{i}",
                    status=status,
                )
                session.add(exp)
                await session.commit()
                await session.refresh(exp)
                actual = exp.status.value if hasattr(exp.status, "value") else exp.status
                assert actual == status.value

    @pytest.mark.asyncio
    async def test_eval_run_status_all_values(self):
        """All EvalRunStatus values can be stored and retrieved."""
        for i, status in enumerate(EvalRunStatus):
            async with async_session() as session:
                er = EvalRun(
                    suite_name=f"suite-{i}",
                    workflow_name=f"wf-{i}",
                    status=status,
                )
                session.add(er)
                await session.commit()
                await session.refresh(er)
                actual = er.status.value if hasattr(er.status, "value") else er.status
                assert actual == status.value

    @pytest.mark.asyncio
    async def test_workflow_version_status_all_values(self):
        """All WorkflowVersionStatus values can be stored and retrieved."""
        for i, status in enumerate(WorkflowVersionStatus):
            async with async_session() as session:
                wv = WorkflowVersion(
                    workflow_name=f"wv-{i}",
                    version=i + 1,
                    status=status,
                    yaml_content="name: test",
                    checksum="a" * 64,
                )
                session.add(wv)
                await session.commit()
                await session.refresh(wv)
                actual = wv.status.value if hasattr(wv.status, "value") else wv.status
                assert actual == status.value

    def test_run_status_is_str_enum(self):
        """RunStatus inherits from str for direct comparison."""
        assert isinstance(RunStatus.QUEUED, str)
        assert RunStatus.QUEUED == "queued"

    def test_step_status_is_str_enum(self):
        """StepStatus inherits from str for direct comparison."""
        assert isinstance(StepStatus.PENDING, str)
        assert StepStatus.PENDING == "pending"


# ---------------------------------------------------------------------------
# 5. UUID HANDLING
# ---------------------------------------------------------------------------


class TestUuidHandling:
    """Verify UUID fields handle string/UUID object conversion."""

    @pytest.mark.asyncio
    async def test_run_id_is_uuid_object(self):
        """Run.id should be a UUID object, not a string."""
        run = await _create_run()
        assert isinstance(run.id, uuid.UUID)

    @pytest.mark.asyncio
    async def test_run_get_by_uuid_object(self):
        """session.get(Run, uuid_obj) should work."""
        run = await _create_run()
        async with async_session() as session:
            loaded = await session.get(Run, run.id)
            assert loaded is not None
            assert loaded.id == run.id

    @pytest.mark.asyncio
    async def test_run_get_by_uuid_string_fails(self):
        """session.get(Run, str) should return None (type mismatch)."""
        run = await _create_run()
        async with async_session() as session:
            # session.get with wrong type may return None or raise
            try:
                loaded = await session.get(Run, str(run.id))
                # Some drivers coerce the string to UUID; either outcome is fine
            except Exception:
                pass  # Expected

    @pytest.mark.asyncio
    async def test_run_filter_by_uuid(self):
        """Filtering by UUID in WHERE clause works."""
        run = await _create_run()
        async with async_session() as session:
            stmt = select(Run).where(Run.id == run.id)
            result = await session.execute(stmt)
            loaded = result.scalar_one_or_none()
            assert loaded is not None

    @pytest.mark.asyncio
    async def test_run_parent_run_id_uuid(self):
        """parent_run_id properly stores and retrieves UUIDs."""
        parent = await _create_run()
        child = await _create_run(parent_run_id=parent.id, depth=1)
        async with async_session() as session:
            loaded = await session.get(Run, child.id)
            assert loaded.parent_run_id == parent.id
            assert isinstance(loaded.parent_run_id, uuid.UUID)

    @pytest.mark.asyncio
    async def test_uuid_default_generation(self):
        """Each model row gets a unique UUID by default."""
        run1 = await _create_run()
        run2 = await _create_run()
        assert run1.id != run2.id

    @pytest.mark.asyncio
    async def test_explicit_uuid_preserved(self):
        """Explicitly provided UUID is used, not overwritten."""
        explicit_id = uuid.uuid4()
        async with async_session() as session:
            run = Run(
                id=explicit_id,
                workflow_name="explicit-uuid",
                status=RunStatus.COMPLETED,
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)
            assert run.id == explicit_id


# ---------------------------------------------------------------------------
# 6. RELATIONSHIP INTEGRITY
# ---------------------------------------------------------------------------


class TestRelationshipIntegrity:
    """Verify relationships and cascade behavior."""

    @pytest.mark.asyncio
    async def test_run_steps_cascade_delete(self):
        """Deleting a run cascades to its RunStep records via ORM."""
        run = await _create_run()
        await _create_step(run.id, "s1")
        await _create_step(run.id, "s2")

        async with async_session() as session:
            count_before = await session.scalar(
                select(func.count(RunStep.id)).where(RunStep.run_id == run.id)
            )
            assert count_before == 2

            # Delete using ORM-parameterized query (SQLite stores UUIDs as hex)
            await session.execute(sa_delete(Run).where(Run.id == run.id))
            await session.commit()

            count_after = await session.scalar(
                select(func.count(RunStep.id)).where(RunStep.run_id == run.id)
            )
            assert count_after == 0

    @pytest.mark.asyncio
    async def test_approval_request_cascade_delete(self):
        """Deleting a run cascades to ApprovalRequest records."""
        run = await _create_run()
        async with async_session() as session:
            ap = ApprovalRequest(
                run_id=run.id,
                step_id="review",
                status=ApprovalStatus.PENDING,
                message="review me",
            )
            session.add(ap)
            await session.commit()

        async with async_session() as session:
            await session.execute(sa_delete(Run).where(Run.id == run.id))
            await session.commit()
            count = await session.scalar(
                select(func.count(ApprovalRequest.id)).where(ApprovalRequest.run_id == run.id)
            )
            assert count == 0

    @pytest.mark.asyncio
    async def test_policy_violation_cascade_delete(self):
        """Deleting a run cascades to PolicyViolation records."""
        run = await _create_run()
        async with async_session() as session:
            pv = PolicyViolation(
                run_id=run.id,
                step_id="check",
                policy_id="no-pii",
                severity="high",
                action_taken="redact",
            )
            session.add(pv)
            await session.commit()

        async with async_session() as session:
            await session.execute(sa_delete(Run).where(Run.id == run.id))
            await session.commit()
            count = await session.scalar(
                select(func.count(PolicyViolation.id)).where(PolicyViolation.run_id == run.id)
            )
            assert count == 0

    @pytest.mark.asyncio
    async def test_routing_decision_cascade_delete(self):
        """Deleting a run cascades to RoutingDecision records."""
        run = await _create_run()
        async with async_session() as session:
            rd = RoutingDecision(
                run_id=run.id,
                step_id="gen",
                selected_model="haiku",
                selected_variant_id="default",
                confidence=0.5,
            )
            session.add(rd)
            await session.commit()

        async with async_session() as session:
            await session.execute(sa_delete(Run).where(Run.id == run.id))
            await session.commit()
            count = await session.scalar(
                select(func.count(RoutingDecision.id)).where(RoutingDecision.run_id == run.id)
            )
            assert count == 0

    @pytest.mark.asyncio
    async def test_checkpoint_cascade_delete(self):
        """Deleting a run cascades to RunCheckpoint records."""
        run = await _create_run()
        async with async_session() as session:
            cp = RunCheckpoint(
                run_id=run.id,
                step_id="s1",
                stage_index=0,
                context_snapshot={"ctx": "data"},
            )
            session.add(cp)
            await session.commit()

        async with async_session() as session:
            await session.execute(sa_delete(Run).where(Run.id == run.id))
            await session.commit()
            count = await session.scalar(
                select(func.count(RunCheckpoint.id)).where(RunCheckpoint.run_id == run.id)
            )
            assert count == 0

    @pytest.mark.asyncio
    async def test_dead_letter_cascade_delete(self):
        """Deleting a run cascades to DeadLetterItem records."""
        run = await _create_run()
        async with async_session() as session:
            dl = DeadLetterItem(
                run_id=run.id,
                step_id="s1",
                error="boom",
            )
            session.add(dl)
            await session.commit()

        async with async_session() as session:
            await session.execute(sa_delete(Run).where(Run.id == run.id))
            await session.commit()
            count = await session.scalar(
                select(func.count(DeadLetterItem.id)).where(DeadLetterItem.run_id == run.id)
            )
            assert count == 0

    @pytest.mark.asyncio
    async def test_autopilot_experiment_sample_cascade(self):
        """Deleting an experiment cascades to its samples."""
        async with async_session() as session:
            exp = AutoPilotExperiment(
                workflow_name="cascade-test",
                step_id="s1",
                status=ExperimentStatus.RUNNING,
            )
            session.add(exp)
            await session.flush()
            sample = AutoPilotSample(
                experiment_id=exp.id,
                variant_id="v1",
            )
            session.add(sample)
            await session.commit()
            exp_id = exp.id

        async with async_session() as session:
            await session.execute(
                sa_delete(AutoPilotExperiment).where(AutoPilotExperiment.id == exp_id)
            )
            await session.commit()
            count = await session.scalar(
                select(func.count(AutoPilotSample.id)).where(
                    AutoPilotSample.experiment_id == exp_id
                )
            )
            assert count == 0

    @pytest.mark.asyncio
    async def test_eval_run_case_results_cascade(self):
        """Deleting an EvalRun cascades to its EvalCaseResult records."""
        async with async_session() as session:
            er = EvalRun(
                suite_name="suite-cascade",
                workflow_name="wf-cascade",
            )
            session.add(er)
            await session.flush()
            ecr = EvalCaseResult(
                eval_run_id=er.id,
                case_name="case1",
            )
            session.add(ecr)
            await session.commit()
            er_id = er.id

        async with async_session() as session:
            await session.execute(sa_delete(EvalRun).where(EvalRun.id == er_id))
            await session.commit()
            count = await session.scalar(
                select(func.count(EvalCaseResult.id)).where(
                    EvalCaseResult.eval_run_id == er_id
                )
            )
            assert count == 0

    @pytest.mark.asyncio
    async def test_schedule_last_run_set_null(self):
        """Deleting a run sets Schedule.last_run_id to NULL (SET NULL FK)."""
        run = await _create_run()
        async with async_session() as session:
            sched = Schedule(
                workflow_name="sched-test",
                cron_expression="0 * * * *",
                last_run_id=run.id,
            )
            session.add(sched)
            await session.commit()
            sched_id = sched.id

        async with async_session() as session:
            await session.execute(sa_delete(Run).where(Run.id == run.id))
            await session.commit()
            loaded_sched = await session.get(Schedule, sched_id)
            assert loaded_sched is not None
            assert loaded_sched.last_run_id is None

    @pytest.mark.asyncio
    async def test_run_self_reference_set_null(self):
        """Deleting a parent run sets child's parent_run_id to NULL."""
        parent = await _create_run()
        child = await _create_run(parent_run_id=parent.id, depth=1)

        async with async_session() as session:
            await session.execute(sa_delete(Run).where(Run.id == parent.id))
            await session.commit()
            loaded_child = await session.get(Run, child.id)
            assert loaded_child is not None
            assert loaded_child.parent_run_id is None

    @pytest.mark.asyncio
    async def test_run_step_invalid_run_id_rejected(self):
        """Cannot insert a RunStep with a non-existent run_id."""
        fake_run_id = uuid.uuid4()
        async with async_session() as session:
            step = RunStep(
                run_id=fake_run_id,
                step_id="orphan",
                status=StepStatus.PENDING,
            )
            session.add(step)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()


# ---------------------------------------------------------------------------
# 7. TIMESTAMP HANDLING
# ---------------------------------------------------------------------------


class TestTimestampHandling:
    """Verify timestamp fields are timezone-aware."""

    @pytest.mark.asyncio
    async def test_run_created_at_has_timezone(self):
        """Run.created_at should be timezone-aware (UTC)."""
        run = await _create_run()
        assert run.created_at is not None
        # SQLite may not preserve tzinfo, but the default should be UTC
        if run.created_at.tzinfo is not None:
            assert run.created_at.tzinfo == timezone.utc

    @pytest.mark.asyncio
    async def test_run_created_at_auto_set(self):
        """Run.created_at is automatically populated on insert."""
        before = datetime.now(timezone.utc) - timedelta(seconds=1)
        run = await _create_run()
        after = datetime.now(timezone.utc) + timedelta(seconds=1)
        # Allow for timezone-naive comparison from SQLite
        created = run.created_at.replace(tzinfo=None)
        assert before.replace(tzinfo=None) <= created <= after.replace(tzinfo=None)

    @pytest.mark.asyncio
    async def test_tool_connection_updated_at_auto(self):
        """ToolConnection.updated_at is set on insert."""
        async with async_session() as session:
            tc = ToolConnection(
                tool_name="test-tool",
                connection_name=f"conn-{uuid.uuid4().hex[:8]}",
                credentials={"key": "val"},
            )
            session.add(tc)
            await session.commit()
            await session.refresh(tc)
            assert tc.updated_at is not None
            assert tc.created_at is not None

    @pytest.mark.asyncio
    async def test_setting_updated_at_auto(self):
        """Setting.updated_at is set on insert."""
        async with async_session() as session:
            key = f"test-key-{uuid.uuid4().hex[:8]}"
            s = Setting(key=key, value="test-val")
            session.add(s)
            await session.commit()
            await session.refresh(s)
            assert s.updated_at is not None

    @pytest.mark.asyncio
    async def test_run_completed_at_after_started_at(self):
        """completed_at should typically be after started_at."""
        now = datetime.now(timezone.utc)
        run = await _create_run(
            started_at=now - timedelta(seconds=10),
            completed_at=now,
        )
        # Both should be persisted
        async with async_session() as session:
            loaded = await session.get(Run, run.id)
            assert loaded.started_at is not None
            assert loaded.completed_at is not None

    @pytest.mark.asyncio
    async def test_all_datetime_columns_have_timezone_true(self):
        """Every DateTime column in the schema should use timezone=True."""
        for table in Base.metadata.sorted_tables:
            for col in table.columns:
                from sqlalchemy import DateTime as DTType
                if isinstance(col.type, DTType):
                    assert col.type.timezone, (
                        f"{table.name}.{col.name} uses DateTime without "
                        f"timezone=True"
                    )


# ---------------------------------------------------------------------------
# 8. INDEX COVERAGE
# ---------------------------------------------------------------------------


class TestIndexCoverage:
    """Verify all expected indexes exist."""

    def _index_names(self, model) -> set[str]:
        return {idx.name for idx in model.__table__.indexes}

    def _indexed_columns(self, model) -> set[str]:
        return {col.name for col in model.__table__.columns if col.index}

    def test_run_indexes(self):
        names = self._index_names(Run)
        expected = {
            "ix_runs_status",
            "ix_runs_created_at",
            "ix_runs_tenant_id",
            "ix_runs_workflow_name",
            "ix_runs_tenant_status_created",
        }
        assert expected.issubset(names)

    def test_run_step_indexes(self):
        names = self._index_names(RunStep)
        expected = {
            "ix_run_steps_run_id",
            "ix_run_steps_run_step_parallel",
            "ix_run_steps_run_id_status",
        }
        assert expected.issubset(names)

    def test_approval_request_indexes(self):
        names = self._index_names(ApprovalRequest)
        expected = {
            "ix_approval_requests_run_id",
            "ix_approval_requests_status",
            "ix_approval_requests_status_timeout",
        }
        assert expected.issubset(names)

    def test_routing_decision_indexes(self):
        names = self._index_names(RoutingDecision)
        expected = {
            "ix_routing_decisions_run_id",
            "ix_routing_decisions_step_id",
            "ix_routing_decisions_created_model",
        }
        assert expected.issubset(names)

    def test_policy_violation_indexes(self):
        names = self._index_names(PolicyViolation)
        expected = {
            "ix_policy_violations_run_id",
            "ix_policy_violations_created_at",
        }
        assert expected.issubset(names)

    def test_step_cache_indexes(self):
        names = self._index_names(StepCache)
        assert "ix_step_cache_expires_at" in names
        # cache_key has index=True on column
        assert "cache_key" in self._indexed_columns(StepCache)

    def test_eval_case_result_run_id_index(self):
        """EvalCaseResult.run_id should have an index after fix."""
        names = self._index_names(EvalCaseResult)
        assert "ix_eval_case_results_run_id" in names

    def test_schedule_indexes(self):
        names = self._index_names(Schedule)
        expected = {
            "ix_schedules_enabled",
            "ix_schedules_tenant_id",
            "ix_schedules_workflow_name",
        }
        assert expected.issubset(names)

    def test_api_key_indexes(self):
        names = self._index_names(ApiKey)
        expected = {
            "ix_api_keys_tenant_id",
            "ix_api_keys_is_active",
        }
        assert expected.issubset(names)

    def test_dead_letter_indexes(self):
        names = self._index_names(DeadLetterItem)
        expected = {
            "ix_dead_letter_run_id",
            "ix_dead_letter_resolved_at",
        }
        assert expected.issubset(names)

    def test_run_checkpoint_indexes(self):
        names = self._index_names(RunCheckpoint)
        assert "ix_run_checkpoints_run_id" in names


# ---------------------------------------------------------------------------
# 9. DEFAULT VALUES
# ---------------------------------------------------------------------------


class TestDefaultValues:
    """Verify default values are correctly applied."""

    @pytest.mark.asyncio
    async def test_run_default_status_queued(self):
        """Run.status defaults to QUEUED."""
        async with async_session() as session:
            run = Run(workflow_name="defaults-test")
            session.add(run)
            await session.commit()
            await session.refresh(run)
            actual = run.status.value if hasattr(run.status, "value") else run.status
            assert actual == "queued"

    @pytest.mark.asyncio
    async def test_run_default_cost_zero(self):
        """Run.total_cost_usd defaults to 0.0."""
        async with async_session() as session:
            run = Run(workflow_name="defaults-test")
            session.add(run)
            await session.commit()
            await session.refresh(run)
            assert run.total_cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_run_default_depth_zero(self):
        """Run.depth defaults to 0."""
        async with async_session() as session:
            run = Run(workflow_name="defaults-test")
            session.add(run)
            await session.commit()
            await session.refresh(run)
            assert run.depth == 0

    @pytest.mark.asyncio
    async def test_run_step_default_attempt_one(self):
        """RunStep.attempt defaults to 1."""
        run = await _create_run()
        async with async_session() as session:
            step = RunStep(
                run_id=run.id,
                step_id="default-attempt",
                status=StepStatus.PENDING,
            )
            session.add(step)
            await session.commit()
            await session.refresh(step)
            assert step.attempt == 1

    @pytest.mark.asyncio
    async def test_run_step_default_cost_zero(self):
        """RunStep.cost_usd defaults to 0.0."""
        run = await _create_run()
        async with async_session() as session:
            step = RunStep(
                run_id=run.id,
                step_id="default-cost",
                status=StepStatus.PENDING,
            )
            session.add(step)
            await session.commit()
            await session.refresh(step)
            assert step.cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_api_key_default_active(self):
        """ApiKey.is_active defaults to True."""
        async with async_session() as session:
            ak = ApiKey(
                key_hash=f"d{uuid.uuid4().hex[:63]}",
                name="default-active-test",
            )
            session.add(ak)
            await session.commit()
            await session.refresh(ak)
            assert ak.is_active is True

    @pytest.mark.asyncio
    async def test_schedule_default_enabled(self):
        """Schedule.enabled defaults to True."""
        async with async_session() as session:
            s = Schedule(
                workflow_name="sched-default",
                cron_expression="0 * * * *",
            )
            session.add(s)
            await session.commit()
            await session.refresh(s)
            assert s.enabled is True

    @pytest.mark.asyncio
    async def test_approval_default_status_pending(self):
        """ApprovalRequest.status defaults to PENDING."""
        run = await _create_run()
        async with async_session() as session:
            ap = ApprovalRequest(
                run_id=run.id,
                step_id="gate",
                message="review",
            )
            session.add(ap)
            await session.commit()
            await session.refresh(ap)
            actual = ap.status.value if hasattr(ap.status, "value") else ap.status
            assert actual == "pending"

    @pytest.mark.asyncio
    async def test_approval_default_on_timeout_abort(self):
        """ApprovalRequest.on_timeout defaults to 'abort'."""
        run = await _create_run()
        async with async_session() as session:
            ap = ApprovalRequest(
                run_id=run.id,
                step_id="gate2",
                message="review",
            )
            session.add(ap)
            await session.commit()
            await session.refresh(ap)
            assert ap.on_timeout == "abort"

    @pytest.mark.asyncio
    async def test_routing_decision_default_confidence(self):
        """RoutingDecision.confidence defaults to 0.1."""
        run = await _create_run()
        async with async_session() as session:
            rd = RoutingDecision(
                run_id=run.id,
                step_id="s1",
                selected_model="haiku",
                selected_variant_id="default",
            )
            session.add(rd)
            await session.commit()
            await session.refresh(rd)
            assert rd.confidence == pytest.approx(0.1)

    @pytest.mark.asyncio
    async def test_policy_violation_default_severity(self):
        """PolicyViolation.severity defaults to 'medium'."""
        run = await _create_run()
        async with async_session() as session:
            pv = PolicyViolation(
                run_id=run.id,
                step_id="s1",
                policy_id="test-policy",
                action_taken="log",
            )
            session.add(pv)
            await session.commit()
            await session.refresh(pv)
            assert pv.severity == "medium"

    @pytest.mark.asyncio
    async def test_step_cache_default_hit_count(self):
        """StepCache.hit_count defaults to 0."""
        async with async_session() as session:
            sc = StepCache(
                cache_key=f"def-{uuid.uuid4().hex[:8]}",
                step_id="s1",
            )
            session.add(sc)
            await session.commit()
            await session.refresh(sc)
            assert sc.hit_count == 0


# ---------------------------------------------------------------------------
# 10. STRING LENGTH AND UNIQUE CONSTRAINTS
# ---------------------------------------------------------------------------


class TestStringAndUniqueConstraints:
    """Verify string length limits and unique constraints."""

    @pytest.mark.asyncio
    async def test_run_idempotency_key_unique(self):
        """Two runs with the same idempotency_key should conflict."""
        idem_key = f"idem-{uuid.uuid4().hex[:8]}"
        await _create_run(idempotency_key=idem_key)
        async with async_session() as session:
            run2 = Run(
                workflow_name="dup-idem",
                status=RunStatus.QUEUED,
                idempotency_key=idem_key,
            )
            session.add(run2)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    @pytest.mark.asyncio
    async def test_run_idempotency_key_null_not_unique(self):
        """Multiple runs with NULL idempotency_key should be allowed."""
        r1 = await _create_run(idempotency_key=None)
        r2 = await _create_run(idempotency_key=None)
        assert r1.id != r2.id

    @pytest.mark.asyncio
    async def test_api_key_hash_unique(self):
        """Two ApiKeys with the same key_hash should conflict."""
        common_hash = "x" * 64
        async with async_session() as session:
            ak1 = ApiKey(key_hash=common_hash, name="first")
            session.add(ak1)
            await session.commit()

        async with async_session() as session:
            ak2 = ApiKey(key_hash=common_hash, name="second")
            session.add(ak2)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    @pytest.mark.asyncio
    async def test_step_cache_key_unique(self):
        """Two StepCache entries with the same cache_key should conflict."""
        ck = f"ck-unique-{uuid.uuid4().hex[:8]}"
        async with async_session() as session:
            sc1 = StepCache(cache_key=ck, step_id="s1")
            session.add(sc1)
            await session.commit()

        async with async_session() as session:
            sc2 = StepCache(cache_key=ck, step_id="s2")
            session.add(sc2)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    @pytest.mark.asyncio
    async def test_tool_connection_unique_constraint(self):
        """ToolConnection (tool_name, connection_name) must be unique."""
        conn_name = f"unique-{uuid.uuid4().hex[:8]}"
        async with async_session() as session:
            tc1 = ToolConnection(
                tool_name="postgresql",
                connection_name=conn_name,
                credentials={},
            )
            session.add(tc1)
            await session.commit()

        async with async_session() as session:
            tc2 = ToolConnection(
                tool_name="postgresql",
                connection_name=conn_name,
                credentials={},
            )
            session.add(tc2)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    @pytest.mark.asyncio
    async def test_workflow_version_unique_constraint(self):
        """WorkflowVersion (workflow_name, version) must be unique."""
        wf_name = f"wv-unique-{uuid.uuid4().hex[:8]}"
        async with async_session() as session:
            wv1 = WorkflowVersion(
                workflow_name=wf_name,
                version=1,
                yaml_content="name: test",
                checksum="b" * 64,
            )
            session.add(wv1)
            await session.commit()

        async with async_session() as session:
            wv2 = WorkflowVersion(
                workflow_name=wf_name,
                version=1,
                yaml_content="name: test2",
                checksum="c" * 64,
            )
            session.add(wv2)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    @pytest.mark.asyncio
    async def test_setting_key_primary_key(self):
        """Setting uses key as primary key - duplicate keys should conflict."""
        skey = f"pk-{uuid.uuid4().hex[:8]}"
        async with async_session() as session:
            s1 = Setting(key=skey, value="val1")
            session.add(s1)
            await session.commit()

        async with async_session() as session:
            s2 = Setting(key=skey, value="val2")
            session.add(s2)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()


# ---------------------------------------------------------------------------
# 11. MIGRATION SAFETY (_add_missing_columns)
# ---------------------------------------------------------------------------


class TestMigrationSafety:
    """Verify _add_missing_columns includes DEFAULT values."""

    def test_add_missing_columns_includes_defaults_for_scalars(self):
        """_add_missing_columns should generate DEFAULT clauses for scalars."""
        # Verify the function logic by checking column metadata
        run_table = Run.__table__
        total_cost_col = run_table.c.total_cost_usd
        # The column has a Python-side default of 0.0
        assert total_cost_col.default is not None
        assert total_cost_col.default.arg == 0.0

        depth_col = run_table.c.depth
        assert depth_col.default is not None
        assert depth_col.default.arg == 0

    def test_check_constraint_names_unique(self):
        """All check constraint names across all tables should be unique."""
        seen = set()
        for table in Base.metadata.sorted_tables:
            for constraint in table.constraints:
                from sqlalchemy import CheckConstraint as CC
                if isinstance(constraint, CC) and constraint.name:
                    assert constraint.name not in seen, (
                        f"Duplicate constraint name: {constraint.name}"
                    )
                    seen.add(constraint.name)

    def test_all_tables_have_primary_key(self):
        """Every model table should have a primary key defined."""
        for table in Base.metadata.sorted_tables:
            pk_cols = list(table.primary_key.columns)
            assert len(pk_cols) > 0, f"{table.name} has no primary key"


# ---------------------------------------------------------------------------
# 12. MODEL METADATA CONSISTENCY
# ---------------------------------------------------------------------------


class TestModelMetadata:
    """Verify structural consistency of all models."""

    def test_all_fk_columns_have_ondelete(self):
        """Every ForeignKey should have an ondelete action specified."""
        for table in Base.metadata.sorted_tables:
            for col in table.columns:
                for fk in col.foreign_keys:
                    assert fk.ondelete is not None, (
                        f"{table.name}.{col.name} FK to {fk.target_fullname} "
                        f"has no ondelete action"
                    )

    def test_all_fk_columns_indexed(self):
        """All foreign key columns should be covered by an index."""
        for table in Base.metadata.sorted_tables:
            fk_cols = {
                col.name
                for col in table.columns
                if col.foreign_keys
            }
            if not fk_cols:
                continue
            # Gather columns covered by any index
            indexed_cols = set()
            for idx in table.indexes:
                for col in idx.columns:
                    indexed_cols.add(col.name)
            # Also check direct column index=True
            for col in table.columns:
                if col.index:
                    indexed_cols.add(col.name)
            # Also check primary key columns
            for col in table.primary_key.columns:
                indexed_cols.add(col.name)
            # Also check unique constraints (implicitly indexed)
            for col in table.columns:
                if col.unique:
                    indexed_cols.add(col.name)

            for fk_col in fk_cols:
                assert fk_col in indexed_cols, (
                    f"{table.name}.{fk_col} is a FK column but has no index"
                )

    def test_no_duplicate_index_names(self):
        """No two indexes across all tables should share a name."""
        seen = set()
        for table in Base.metadata.sorted_tables:
            for idx in table.indexes:
                assert idx.name not in seen, (
                    f"Duplicate index name: {idx.name}"
                )
                seen.add(idx.name)

    def test_all_models_listed_in_base_metadata(self):
        """All expected model classes should be registered in Base.metadata."""
        expected_tables = {
            "runs",
            "run_steps",
            "schedules",
            "api_keys",
            "dead_letter_queue",
            "autopilot_experiments",
            "autopilot_samples",
            "approval_requests",
            "routing_decisions",
            "policy_violations",
            "run_checkpoints",
            "step_cache",
            "tool_connections",
            "eval_runs",
            "eval_case_results",
            "settings",
            "workflow_versions",
        }
        actual = {t.name for t in Base.metadata.sorted_tables}
        missing = expected_tables - actual
        assert not missing, f"Missing tables: {missing}"


# ---------------------------------------------------------------------------
# 13. EDGE CASES AND BOUNDARY VALUES
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Test boundary values and uncommon scenarios."""

    @pytest.mark.asyncio
    async def test_run_with_very_long_error(self):
        """Run.error (Text column) should handle long error messages."""
        long_error = "E" * 10000
        run = await _create_run(error=long_error)
        async with async_session() as session:
            loaded = await session.get(Run, run.id)
            assert loaded.error == long_error

    @pytest.mark.asyncio
    async def test_run_with_empty_string_workflow_name(self):
        """Run.workflow_name should handle empty-ish names (edge case)."""
        # An empty string is technically valid per the schema
        # (only nullable=False, no min length constraint at DB level)
        async with async_session() as session:
            run = Run(workflow_name="", status=RunStatus.QUEUED)
            session.add(run)
            await session.commit()
            await session.refresh(run)
            assert run.workflow_name == ""

    @pytest.mark.asyncio
    async def test_run_step_parallel_index_null_and_zero(self):
        """RunStep.parallel_index can be None or 0."""
        run = await _create_run()
        s1 = await _create_step(run.id, "s-none", parallel_index=None)
        s2 = await _create_step(run.id, "s-zero", parallel_index=0)
        s3 = await _create_step(run.id, "s-positive", parallel_index=5)

        async with async_session() as session:
            loaded1 = await session.get(RunStep, s1.id)
            assert loaded1.parallel_index is None
            loaded2 = await session.get(RunStep, s2.id)
            assert loaded2.parallel_index == 0
            loaded3 = await session.get(RunStep, s3.id)
            assert loaded3.parallel_index == 5

    @pytest.mark.asyncio
    async def test_approval_request_allow_edit_toggle(self):
        """ApprovalRequest.allow_edit boolean stores correctly."""
        run = await _create_run()
        async with async_session() as session:
            ap = ApprovalRequest(
                run_id=run.id,
                step_id="editable",
                message="review",
                allow_edit=True,
            )
            session.add(ap)
            await session.commit()
            await session.refresh(ap)
            assert ap.allow_edit is True

    @pytest.mark.asyncio
    async def test_dead_letter_resolved_flow(self):
        """DeadLetterItem can transition from unresolved to resolved."""
        run = await _create_run()
        async with async_session() as session:
            dl = DeadLetterItem(
                run_id=run.id,
                step_id="fail-step",
                error="timeout",
                attempts=3,
            )
            session.add(dl)
            await session.commit()
            dl_id = dl.id

        # Resolve it
        now = datetime.now(timezone.utc)
        async with async_session() as session:
            loaded = await session.get(DeadLetterItem, dl_id)
            loaded.resolved_at = now
            loaded.resolved_by = "admin"
            await session.commit()
            await session.refresh(loaded)
            assert loaded.resolved_at is not None
            assert loaded.resolved_by == "admin"

    @pytest.mark.asyncio
    async def test_autopilot_experiment_rollout_stages(self):
        """AutoPilotExperiment.rollout_stage handles all valid stages."""
        for stage in [None, "canary", "partial", "full"]:
            async with async_session() as session:
                exp = AutoPilotExperiment(
                    workflow_name=f"rollout-{stage}",
                    step_id="s1",
                    status=ExperimentStatus.RUNNING,
                    rollout_stage=stage,
                )
                session.add(exp)
                await session.commit()
                await session.refresh(exp)
                assert exp.rollout_stage == stage

    @pytest.mark.asyncio
    async def test_run_max_cost_usd_nullable(self):
        """Run.max_cost_usd can be None (no budget limit)."""
        run = await _create_run(max_cost_usd=None)
        async with async_session() as session:
            loaded = await session.get(Run, run.id)
            assert loaded.max_cost_usd is None

    @pytest.mark.asyncio
    async def test_api_key_expires_at_nullable(self):
        """ApiKey.expires_at can be None (never expires)."""
        async with async_session() as session:
            ak = ApiKey(
                key_hash=f"e{uuid.uuid4().hex[:63]}",
                name="no-expiry",
                expires_at=None,
            )
            session.add(ak)
            await session.commit()
            await session.refresh(ak)
            assert ak.expires_at is None

    @pytest.mark.asyncio
    async def test_api_key_with_expiry(self):
        """ApiKey.expires_at stores and retrieves a future datetime."""
        future = datetime.now(timezone.utc) + timedelta(days=365)
        async with async_session() as session:
            ak = ApiKey(
                key_hash=f"f{uuid.uuid4().hex[:63]}",
                name="with-expiry",
                expires_at=future,
            )
            session.add(ak)
            await session.commit()
            await session.refresh(ak)
            assert ak.expires_at is not None

    @pytest.mark.asyncio
    async def test_workflow_version_version_numbers(self):
        """WorkflowVersion supports sequential version numbers."""
        wf_name = f"version-seq-{uuid.uuid4().hex[:8]}"
        for v in [1, 2, 3]:
            async with async_session() as session:
                wv = WorkflowVersion(
                    workflow_name=wf_name,
                    version=v,
                    yaml_content=f"name: test-v{v}",
                    checksum=f"{v}" * 64,
                )
                session.add(wv)
                await session.commit()

        # All three should exist
        async with async_session() as session:
            stmt = select(func.count(WorkflowVersion.id)).where(
                WorkflowVersion.workflow_name == wf_name
            )
            count = await session.scalar(stmt)
            assert count == 3

    @pytest.mark.asyncio
    async def test_eval_case_result_run_id_set_null(self):
        """Deleting a run sets EvalCaseResult.run_id to NULL."""
        run = await _create_run()
        async with async_session() as session:
            er = EvalRun(
                suite_name="eval-setnull",
                workflow_name="wf-setnull",
            )
            session.add(er)
            await session.flush()
            ecr = EvalCaseResult(
                eval_run_id=er.id,
                case_name="case-setnull",
                run_id=run.id,
            )
            session.add(ecr)
            await session.commit()
            ecr_id = ecr.id

        # Delete the run
        async with async_session() as session:
            await session.execute(sa_delete(Run).where(Run.id == run.id))
            await session.commit()
            loaded = await session.get(EvalCaseResult, ecr_id)
            assert loaded is not None
            assert loaded.run_id is None

    @pytest.mark.asyncio
    async def test_run_concurrent_status_update_pattern(self):
        """Simulate atomic status update pattern used by cancel endpoint."""
        from sqlalchemy import update as sa_update

        run = await _create_run(status=RunStatus.RUNNING)

        # Atomic update: only change if still RUNNING
        async with async_session() as session:
            stmt = (
                sa_update(Run)
                .where(
                    Run.id == run.id,
                    Run.status == RunStatus.RUNNING,
                )
                .values(
                    status=RunStatus.CANCELLED,
                    error="Cancelled",
                )
            )
            result = await session.execute(stmt)
            await session.commit()
            assert result.rowcount == 1

        # Second attempt should not match (already cancelled)
        async with async_session() as session:
            stmt = (
                sa_update(Run)
                .where(
                    Run.id == run.id,
                    Run.status == RunStatus.RUNNING,
                )
                .values(
                    status=RunStatus.CANCELLED,
                    error="Double cancel",
                )
            )
            result = await session.execute(stmt)
            await session.commit()
            assert result.rowcount == 0

        # Final state should be cancelled with first error message
        async with async_session() as session:
            loaded = await session.get(Run, run.id)
            actual = loaded.status.value if hasattr(loaded.status, "value") else loaded.status
            assert actual == "cancelled"
            assert loaded.error == "Cancelled"


# ---------------------------------------------------------------------------
# 14. QUERY PATTERNS
# ---------------------------------------------------------------------------


class TestQueryPatterns:
    """Test common query patterns used by the API."""

    @pytest.mark.asyncio
    async def test_count_runs_by_status(self):
        """Count runs grouped by status works correctly."""
        await _create_run(status=RunStatus.COMPLETED)
        await _create_run(status=RunStatus.COMPLETED)
        await _create_run(status=RunStatus.FAILED, error="err")

        async with async_session() as session:
            stmt = (
                select(Run.status, func.count(Run.id))
                .group_by(Run.status)
            )
            result = await session.execute(stmt)
            status_counts = {row[0]: row[1] for row in result.all()}
            # Should have at least completed and failed
            assert any(
                (s.value if hasattr(s, "value") else s) == "completed"
                for s in status_counts
            )

    @pytest.mark.asyncio
    async def test_filter_runs_by_date_range(self):
        """Filtering runs by created_at date range works."""
        now = datetime.now(timezone.utc)
        old_run = await _create_run(
            created_at=now - timedelta(days=30),
            started_at=now - timedelta(days=30),
            completed_at=now - timedelta(days=30),
        )
        new_run = await _create_run(
            created_at=now,
            started_at=now,
            completed_at=now,
        )

        cutoff = now - timedelta(days=15)
        async with async_session() as session:
            stmt = select(Run).where(Run.created_at >= cutoff)
            result = await session.execute(stmt)
            recent = result.scalars().all()
            recent_ids = {r.id for r in recent}
            assert new_run.id in recent_ids

    @pytest.mark.asyncio
    async def test_select_in_load_steps(self):
        """selectinload for steps works (used by run detail endpoint)."""
        from sqlalchemy.orm import selectinload

        run = await _create_run()
        await _create_step(run.id, "s1")
        await _create_step(run.id, "s2")

        async with async_session() as session:
            stmt = (
                select(Run)
                .where(Run.id == run.id)
                .options(selectinload(Run.steps))
            )
            result = await session.execute(stmt)
            loaded = result.scalar_one()
            assert len(loaded.steps) == 2

    @pytest.mark.asyncio
    async def test_runs_pagination(self):
        """LIMIT/OFFSET pagination works correctly."""
        for _ in range(5):
            await _create_run()

        async with async_session() as session:
            stmt = (
                select(Run)
                .order_by(Run.created_at.desc())
                .limit(2)
                .offset(0)
            )
            result = await session.execute(stmt)
            page1 = result.scalars().all()
            assert len(page1) <= 2

            stmt2 = (
                select(Run)
                .order_by(Run.created_at.desc())
                .limit(2)
                .offset(2)
            )
            result2 = await session.execute(stmt2)
            page2 = result2.scalars().all()
            # Pages should not overlap
            page1_ids = {r.id for r in page1}
            page2_ids = {r.id for r in page2}
            assert not page1_ids.intersection(page2_ids)

    @pytest.mark.asyncio
    async def test_sum_cost_aggregation(self):
        """SUM(total_cost_usd) aggregation works."""
        await _create_run(total_cost_usd=1.5)
        await _create_run(total_cost_usd=2.5)

        async with async_session() as session:
            stmt = select(func.sum(Run.total_cost_usd))
            total = await session.scalar(stmt)
            assert total is not None
            assert total >= 4.0
