"""Tests for compliance quick-wins: input_prompt population and emergency stop.

Covers:
  Task 1 - input_prompt in _save_run_step():
    - _save_run_step() accepts and persists input_prompt
    - StepResult carries input_prompt field
    - _execute_step_once() sets input_prompt on StepResult
    - input_prompt flows into completed/failed _save_run_step() calls

  Task 2 - Global Emergency Stop:
    - POST /admin/emergency-stop requires admin privileges
    - Bulk-cancels all RUNNING and QUEUED runs
    - Sets in-memory emergency stop flag (local mode)
    - _check_cancel() returns True when emergency stop is active
    - is_emergency_stop_active() reflects the flag state
    - EmergencyStopResponse schema is correct
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Disable rate limiting for all tests in this module
from sandcastle.api import rate_limit as _rl_mod

_rl_mod.execution_limiter.check = AsyncMock()

from sandcastle.main import app  # noqa: E402

client = TestClient(app)


# ---------------------------------------------------------------------------
# Task 1: StepResult.input_prompt field
# ---------------------------------------------------------------------------


class TestStepResultInputPrompt:
    """StepResult dataclass should carry the resolved input_prompt."""

    def test_step_result_has_input_prompt_field(self):
        from sandcastle.engine.executor import StepResult

        result = StepResult(step_id="s1", status="completed")
        assert hasattr(result, "input_prompt")
        assert result.input_prompt is None

    def test_step_result_stores_input_prompt(self):
        from sandcastle.engine.executor import StepResult

        result = StepResult(
            step_id="s1",
            status="completed",
            input_prompt="What is 2+2?",
        )
        assert result.input_prompt == "What is 2+2?"

    def test_step_result_default_input_prompt_is_none(self):
        from sandcastle.engine.executor import StepResult

        r = StepResult(step_id="s1")
        assert r.input_prompt is None


# ---------------------------------------------------------------------------
# Task 1: _save_run_step() accepts and persists input_prompt
# ---------------------------------------------------------------------------


class TestSaveRunStepInputPrompt:
    """_save_run_step() should write input_prompt to the DB."""

    @pytest.mark.asyncio
    async def test_save_run_step_signature_accepts_input_prompt(self):
        """_save_run_step() must accept input_prompt keyword argument."""
        import inspect

        from sandcastle.engine.executor import _save_run_step

        sig = inspect.signature(_save_run_step)
        assert "input_prompt" in sig.parameters

    @pytest.mark.asyncio
    async def test_save_run_step_persists_input_prompt(self):
        """input_prompt is written to the RunStep record."""
        from sandcastle.engine.executor import _save_run_step

        run_id = str(uuid.uuid4())
        step_id = "test_step"
        prompt_text = "Resolve this problem: {input.x}"

        saved_prompt = {}

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        # scalar() returns None -> triggers INSERT path
        mock_session.scalar = AsyncMock(return_value=None)

        def capture_add(obj):
            saved_prompt["input_prompt"] = obj.input_prompt

        mock_session.add = capture_add
        mock_session.commit = AsyncMock()

        # _save_run_step imports async_session from sandcastle.models.db inside the function
        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            await _save_run_step(
                run_id=run_id,
                step_id=step_id,
                status="running",
                input_prompt=prompt_text,
            )

        # The RunStep object added to session should have input_prompt set
        assert saved_prompt.get("input_prompt") == prompt_text

    @pytest.mark.asyncio
    async def test_save_run_step_updates_existing_input_prompt(self):
        """When updating an existing record, input_prompt is set if provided."""
        from sandcastle.engine.executor import _save_run_step

        run_id = str(uuid.uuid4())

        existing = MagicMock()
        existing.input_prompt = None

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.scalar = AsyncMock(return_value=existing)
        mock_session.commit = AsyncMock()

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            await _save_run_step(
                run_id=run_id,
                step_id="step1",
                status="completed",
                input_prompt="resolved prompt here",
            )

        assert existing.input_prompt == "resolved prompt here"

    @pytest.mark.asyncio
    async def test_save_run_step_does_not_overwrite_with_none(self):
        """When input_prompt=None, existing value should not be overwritten."""
        from sandcastle.engine.executor import _save_run_step

        run_id = str(uuid.uuid4())

        existing = MagicMock()
        existing.input_prompt = "original prompt"

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.scalar = AsyncMock(return_value=existing)
        mock_session.commit = AsyncMock()

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            await _save_run_step(
                run_id=run_id,
                step_id="step1",
                status="completed",
                input_prompt=None,  # should NOT overwrite
            )

        # Should remain unchanged
        assert existing.input_prompt == "original prompt"


# ---------------------------------------------------------------------------
# Task 1: _execute_step_once sets input_prompt on returned StepResult
# ---------------------------------------------------------------------------


class TestExecuteStepOnceInputPrompt:
    """_execute_step_once() should capture the resolved prompt."""

    @pytest.mark.asyncio
    async def test_execute_step_once_sets_input_prompt_on_success(self):
        from sandcastle.engine.dag import StepDefinition
        from sandcastle.engine.executor import RunContext, _execute_step_once
        from sandcastle.engine.sandshore import SandshoreResult

        step = StepDefinition(
            id="s1",
            prompt="Hello {input.name}",
            type="llm",
        )
        context = RunContext(
            run_id=str(uuid.uuid4()),
            input={"name": "World"},
            workflow_name="test",
        )

        mock_sandbox = AsyncMock()
        mock_sandbox.query = AsyncMock(
            return_value=SandshoreResult(
                text="Hello World",
                structured_output=None,
                total_cost_usd=0.01,
            )
        )
        mock_storage = AsyncMock()
        mock_storage.read = AsyncMock(side_effect=FileNotFoundError)

        with (
            patch("sandcastle.engine.executor._get_cached_result", AsyncMock(return_value=None)),
            patch("sandcastle.engine.executor._save_to_cache", AsyncMock()),
            patch("sandcastle.engine.executor._save_routing_decision", AsyncMock()),
        ):
            result = await _execute_step_once(step, context, mock_sandbox, mock_storage)

        assert result.status == "completed"
        assert result.input_prompt is not None
        # Should contain the resolved value (name=World)
        assert "World" in result.input_prompt

    @pytest.mark.asyncio
    async def test_execute_step_once_sets_input_prompt_on_failure(self):
        from sandcastle.engine.dag import StepDefinition
        from sandcastle.engine.executor import RunContext, _execute_step_once

        step = StepDefinition(
            id="s1",
            prompt="Analyze {input.data}",
            type="llm",
        )
        context = RunContext(
            run_id=str(uuid.uuid4()),
            input={"data": "some text"},
            workflow_name="test",
        )

        mock_sandbox = AsyncMock()
        mock_sandbox.query = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
        mock_storage = AsyncMock()
        mock_storage.read = AsyncMock(side_effect=FileNotFoundError)

        with (
            patch("sandcastle.engine.executor._get_cached_result", AsyncMock(return_value=None)),
        ):
            result = await _execute_step_once(step, context, mock_sandbox, mock_storage)

        assert result.status == "failed"
        # Even on failure, input_prompt should be set (prompt was resolved before error)
        assert result.input_prompt is not None
        assert "some text" in result.input_prompt


# ---------------------------------------------------------------------------
# Task 2: EmergencyStopResponse schema
# ---------------------------------------------------------------------------


class TestEmergencyStopSchema:
    """EmergencyStopResponse Pydantic model validation."""

    def test_schema_fields(self):
        from sandcastle.api.schemas import EmergencyStopResponse

        resp = EmergencyStopResponse(cancelled_count=5, active=True)
        assert resp.cancelled_count == 5
        assert resp.active is True

    def test_schema_default_active(self):
        from sandcastle.api.schemas import EmergencyStopResponse

        resp = EmergencyStopResponse(cancelled_count=0)
        assert resp.active is True

    def test_schema_cancelled_count_non_negative(self):
        from pydantic import ValidationError

        from sandcastle.api.schemas import EmergencyStopResponse

        with pytest.raises(ValidationError):
            EmergencyStopResponse(cancelled_count=-1)

    def test_schema_serialisation(self):
        from sandcastle.api.schemas import EmergencyStopResponse

        resp = EmergencyStopResponse(cancelled_count=3, active=True)
        data = resp.model_dump()
        assert data == {"cancelled_count": 3, "active": True}


# ---------------------------------------------------------------------------
# Task 2: Emergency stop flag - local mode (no Redis)
# ---------------------------------------------------------------------------


class TestEmergencyStopLocalFlag:
    """In-memory emergency stop flag behaviour."""

    @pytest.mark.asyncio
    async def test_set_and_clear_emergency_stop(self):
        from sandcastle.engine.executor import (
            clear_emergency_stop_local,
            is_emergency_stop_active,
            set_emergency_stop_local,
        )

        # is_emergency_stop_active() imports settings internally; patch at source
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.redis_url = None

            # Start clean
            await clear_emergency_stop_local()
            assert await is_emergency_stop_active() is False

            await set_emergency_stop_local()
            assert await is_emergency_stop_active() is True

            await clear_emergency_stop_local()
            assert await is_emergency_stop_active() is False

    @pytest.mark.asyncio
    async def test_check_cancel_returns_true_when_emergency_stop_active(self):
        from sandcastle.engine.executor import (
            _check_cancel,
            clear_emergency_stop_local,
            set_emergency_stop_local,
        )

        run_id = str(uuid.uuid4())

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.redis_url = None

            await clear_emergency_stop_local()
            assert await _check_cancel(run_id) is False

            await set_emergency_stop_local()
            # Emergency stop should cancel ANY run regardless of run_id
            assert await _check_cancel(run_id) is True

            await clear_emergency_stop_local()
            assert await _check_cancel(run_id) is False

    @pytest.mark.asyncio
    async def test_check_cancel_still_checks_per_run_flag(self):
        """Individual cancel flags still work when emergency stop is off."""
        from sandcastle.engine.executor import (
            _check_cancel,
            cancel_run_local,
            clear_emergency_stop_local,
        )

        run_id = str(uuid.uuid4())
        other_id = str(uuid.uuid4())

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.redis_url = None

            await clear_emergency_stop_local()
            await cancel_run_local(run_id)

            assert await _check_cancel(run_id) is True
            assert await _check_cancel(other_id) is False


# ---------------------------------------------------------------------------
# Task 2: Emergency stop Redis mode
# ---------------------------------------------------------------------------


class TestEmergencyStopRedis:
    """is_emergency_stop_active() checks Redis key in Redis mode."""

    @pytest.mark.asyncio
    async def test_is_emergency_stop_active_redis_key_present(self):
        from sandcastle.engine.executor import is_emergency_stop_active

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=b"1")

        with (
            patch("sandcastle.config.settings") as mock_settings,
            patch("sandcastle.engine.executor._get_redis", AsyncMock(return_value=mock_redis)),
        ):
            mock_settings.redis_url = "redis://localhost:6379"
            result = await is_emergency_stop_active()

        assert result is True
        mock_redis.get.assert_called_once_with("emergency_stop:global")

    @pytest.mark.asyncio
    async def test_is_emergency_stop_active_redis_key_absent(self):
        from sandcastle.engine.executor import is_emergency_stop_active

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        with (
            patch("sandcastle.config.settings") as mock_settings,
            patch("sandcastle.engine.executor._get_redis", AsyncMock(return_value=mock_redis)),
        ):
            mock_settings.redis_url = "redis://localhost:6379"
            result = await is_emergency_stop_active()

        assert result is False

    @pytest.mark.asyncio
    async def test_check_cancel_checks_emergency_stop_key_in_redis(self):
        from sandcastle.engine.executor import _check_cancel

        run_id = str(uuid.uuid4())
        mock_redis = AsyncMock()
        # emergency_stop:global is set, cancel:{run_id} is not
        mock_redis.get = AsyncMock(
            side_effect=lambda key: b"1" if key == "emergency_stop:global" else None
        )

        with (
            patch("sandcastle.config.settings") as mock_settings,
            patch("sandcastle.engine.executor._get_redis", AsyncMock(return_value=mock_redis)),
        ):
            mock_settings.redis_url = "redis://localhost:6379"
            result = await _check_cancel(run_id)

        assert result is True


# ---------------------------------------------------------------------------
# Task 2: POST /admin/emergency-stop endpoint
# ---------------------------------------------------------------------------


class TestEmergencyStopEndpoint:
    """API endpoint behaviour for POST /admin/emergency-stop."""

    def test_emergency_stop_requires_admin(self):
        """Non-admin request should receive 403."""
        with patch("sandcastle.api.routes.is_admin", return_value=False):
            resp = client.post("/api/admin/emergency-stop")
        assert resp.status_code == 403

    def test_emergency_stop_admin_success_no_active_runs(self):
        """Admin can call endpoint even when no runs are active."""
        mock_session_instance = AsyncMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=False)

        # COUNT returns 0
        count_result = MagicMock()
        count_result.scalar_one = MagicMock(return_value=0)
        mock_session_instance.execute = AsyncMock(return_value=count_result)
        mock_session_instance.commit = AsyncMock()

        with (
            patch("sandcastle.api.routes.is_admin", return_value=True),
            patch("sandcastle.api.routes.async_session", return_value=mock_session_instance),
            patch(
                "sandcastle.engine.executor.set_emergency_stop_local",
                AsyncMock(),
            ),
            patch("sandcastle.api.routes.settings") as mock_settings,
        ):
            mock_settings.redis_url = None
            resp = client.post("/api/admin/emergency-stop")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["active"] is True
        assert body["data"]["cancelled_count"] == 0

    def test_emergency_stop_admin_success_with_active_runs(self):
        """Admin call cancels all active runs and returns count."""
        mock_session_instance = AsyncMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=False)

        # COUNT returns 3
        count_result = MagicMock()
        count_result.scalar_one = MagicMock(return_value=3)
        update_result = MagicMock()
        mock_session_instance.execute = AsyncMock(side_effect=[count_result, update_result])
        mock_session_instance.commit = AsyncMock()

        with (
            patch("sandcastle.api.routes.is_admin", return_value=True),
            patch("sandcastle.api.routes.async_session", return_value=mock_session_instance),
            patch(
                "sandcastle.engine.executor.set_emergency_stop_local",
                AsyncMock(),
            ),
            patch("sandcastle.api.routes.settings") as mock_settings,
        ):
            mock_settings.redis_url = None
            resp = client.post("/api/admin/emergency-stop")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["cancelled_count"] == 3
        assert body["data"]["active"] is True

    def test_emergency_stop_sets_redis_flag_when_redis_configured(self):
        """When Redis is available, the endpoint sets the Redis key with 24h TTL."""
        mock_session_instance = AsyncMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=False)

        count_result = MagicMock()
        count_result.scalar_one = MagicMock(return_value=1)
        update_result = MagicMock()
        mock_session_instance.execute = AsyncMock(side_effect=[count_result, update_result])
        mock_session_instance.commit = AsyncMock()

        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock()

        with (
            patch("sandcastle.api.routes.is_admin", return_value=True),
            patch("sandcastle.api.routes.async_session", return_value=mock_session_instance),
            patch("sandcastle.api.routes.settings") as mock_settings,
            patch(
                "sandcastle.engine.executor._get_redis",
                AsyncMock(return_value=mock_redis),
            ),
        ):
            mock_settings.redis_url = "redis://localhost:6379"
            resp = client.post("/api/admin/emergency-stop")

        assert resp.status_code == 200
        mock_redis.set.assert_called_once_with("emergency_stop:global", "1", ex=86400)

    def test_emergency_stop_response_shape(self):
        """Response must conform to the EmergencyStopResponse schema."""
        mock_session_instance = AsyncMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=False)

        count_result = MagicMock()
        count_result.scalar_one = MagicMock(return_value=7)
        update_result = MagicMock()
        mock_session_instance.execute = AsyncMock(side_effect=[count_result, update_result])
        mock_session_instance.commit = AsyncMock()

        with (
            patch("sandcastle.api.routes.is_admin", return_value=True),
            patch("sandcastle.api.routes.async_session", return_value=mock_session_instance),
            patch("sandcastle.engine.executor.set_emergency_stop_local", AsyncMock()),
            patch("sandcastle.api.routes.settings") as mock_settings,
        ):
            mock_settings.redis_url = None
            resp = client.post("/api/admin/emergency-stop")

        assert resp.status_code == 200
        body = resp.json()
        data = body["data"]
        assert "cancelled_count" in data
        assert "active" in data
        assert isinstance(data["cancelled_count"], int)
        assert isinstance(data["active"], bool)
