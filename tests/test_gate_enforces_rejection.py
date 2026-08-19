"""A gate that rejects must stop the workflow.

Before 0.45 a rejected gate returned status="completed" and nothing in the
engine read output.decision, so every gate in the template library was
advisory: it evaluated its strategies, said "rejected", and the run walked
straight past it.  These tests pin the corrected behavior.
"""

import tempfile
from unittest.mock import AsyncMock, patch

import pytest

from sandcastle.engine.dag import GateConfig, StepDefinition
from sandcastle.engine.executor import _execute_gate_step
from sandcastle.engine.storage import LocalStorage


def _storage():
    return LocalStorage(base_dir=tempfile.mkdtemp())


def _gate(**cfg_kwargs):
    return StepDefinition(
        id="gate",
        type="gate",
        gate_config=GateConfig(
            strategies=[
                {"type": "timeout", "config": {"seconds": 0, "action": "reject"}}
            ],
            **cfg_kwargs,
        ),
    )


def _ctx():
    from sandcastle.engine.executor import RunContext

    return RunContext(
        run_id="00000000-0000-0000-0000-000000000001", workflow_name="t", input={}
    )


class TestGateRejectionStops:
    @pytest.mark.asyncio
    async def test_rejection_fails_the_step_by_default(self):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            r = await _execute_gate_step(_gate(), _ctx(), _storage())
        assert r.status == "failed"
        assert "Gate rejected" in (r.error or "")

    @pytest.mark.asyncio
    async def test_rejection_is_not_retryable(self):
        """Re-running a judge until it says yes turns a guard rail into a dice roll."""
        with patch("asyncio.sleep", new_callable=AsyncMock):
            r = await _execute_gate_step(_gate(), _ctx(), _storage())
        assert r.retryable is False

    @pytest.mark.asyncio
    async def test_verdict_survives_on_the_failed_result(self):
        """Templates that read {steps.gate.decision} must keep working."""
        with patch("asyncio.sleep", new_callable=AsyncMock):
            r = await _execute_gate_step(_gate(), _ctx(), _storage())
        assert r.output["decision"] == "rejected"
        assert r.output["strategy"] == "timeout"

    @pytest.mark.asyncio
    async def test_fail_on_reject_false_keeps_legacy_behavior(self):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            r = await _execute_gate_step(
                _gate(fail_on_reject=False), _ctx(), _storage()
            )
        assert r.status == "completed"
        assert r.output["decision"] == "rejected"

    @pytest.mark.asyncio
    async def test_approval_is_unaffected(self):
        step = StepDefinition(
            id="gate",
            type="gate",
            gate_config=GateConfig(
                strategies=[
                    {"type": "timeout", "config": {"seconds": 0, "action": "approve"}}
                ]
            ),
        )
        with patch("asyncio.sleep", new_callable=AsyncMock):
            r = await _execute_gate_step(step, _ctx(), _storage())
        assert r.status == "completed"
        assert r.output["decision"] == "approved"


class TestGateConfigDefault:
    def test_yaml_defaults_to_enforcing(self):
        from sandcastle.engine.dag import _parse_gate_config

        cfg = _parse_gate_config({"strategies": []})
        assert cfg.fail_on_reject is True

    def test_yaml_can_opt_out(self):
        from sandcastle.engine.dag import _parse_gate_config

        cfg = _parse_gate_config({"strategies": [], "fail_on_reject": False})
        assert cfg.fail_on_reject is False
