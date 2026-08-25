"""``type: accept`` - the outcome gate.

A worker step finishing and a worker step succeeding are different facts.
``accept`` records the second one: deterministic checks first (free), then a
panel of LLM judges, then a bounded re-work loop, with an evidence pack that
makes the verdict auditable instead of a bare boolean.

These tests pin the parts that are easy to get quietly wrong: that a rejection
actually stops the run and cannot be re-rolled by a step-level ``retry:``, that
checks can reject for $0 before a judge is paid, that a judge which errors
fails closed rather than fabricating a verdict, and that the re-work loop is
bounded from four directions.
"""

import tempfile
from unittest.mock import AsyncMock, patch

import pytest

from sandcastle.engine.dag import (
    MAX_ACCEPT_ROUNDS,
    AcceptConfig,
    AcceptJudge,
    StepDefinition,
    parse_yaml_string,
    validate,
)
from sandcastle.engine.eval import AssertionDef
from sandcastle.engine.executor import (
    RunContext,
    StepResult,
    _execute_accept_step,
    _parse_judge_verdict,
)
from sandcastle.engine.storage import LocalStorage


def _storage():
    return LocalStorage(base_dir=tempfile.mkdtemp())


def _ctx(output="A haiku about sand.", **kwargs):
    ctx = RunContext(
        run_id="00000000-0000-0000-0000-000000000042",
        workflow_name="t",
        input={},
        **kwargs,
    )
    ctx.step_outputs["write"] = output
    ctx.step_results["write"] = StepResult(step_id="write", output=output, cost_usd=0.01)
    return ctx


def _step(**cfg):
    cfg.setdefault("targets", ["write"])
    cfg.setdefault("judges", [AcceptJudge(rubric="Is it good?", model="haiku", name="j1")])
    return StepDefinition(id="acc", type="accept", accept_config=AcceptConfig(**cfg))


def _wf(prompt="Write a haiku about sand."):
    return parse_yaml_string(
        f"""
name: t
steps:
  - id: write
    type: llm
    prompt: "{prompt}"
  - id: acc
    type: accept
    accept_config:
      target: write
      judges:
        - model: haiku
          rubric: "Is it good?"
"""
    )


def _verdict(approved, reason="because", cost=0.001, name="j1"):
    async def _fake(judge, prompt, timeout):
        return {
            "name": judge.name or name,
            "model": judge.model,
            "verdict": "approved" if approved else "rejected",
            "reason": reason,
            "cost_usd": cost,
        }

    return _fake


async def _run(step, ctx, workflow=None, depth=0):
    return await _execute_accept_step(
        step, ctx, None, _storage(), workflow or _wf(), depth
    )


class TestVerdictParsing:
    """A judge's reply is parsed strictly, and never guessed at."""

    def test_approve_and_reject(self):
        assert _parse_judge_verdict("APPROVE\nfine")[0] is True
        assert _parse_judge_verdict("REJECT\nnope")[0] is False
        assert _parse_judge_verdict("VERDICT: APPROVE")[0] is True

    def test_prose_is_not_a_verdict(self):
        """'I would not approve' must not be read as approval."""
        assert _parse_judge_verdict("I would not approve this")[0] is None
        assert _parse_judge_verdict("")[0] is None


class TestChecksRunFirstAndFree:
    @pytest.mark.asyncio
    async def test_failing_check_rejects_before_any_judge_is_paid(self):
        step = _step(checks=[AssertionDef(type="contains", value="mountain")])
        judge = AsyncMock()
        with patch("sandcastle.engine.executor._run_accept_judge", judge):
            r = await _run(step, _ctx())
        assert r.status == "failed"
        assert r.cost_usd == 0.0
        judge.assert_not_awaited()
        assert r.output["rejected_by"] == "checks"

    @pytest.mark.asyncio
    async def test_passing_checks_with_no_judges_approves_for_free(self):
        step = _step(judges=[], checks=[AssertionDef(type="not_empty")])
        r = await _run(step, _ctx())
        assert r.status == "completed"
        assert r.output["decision"] == "approved"
        assert r.cost_usd == 0.0


class TestRejectionStopsTheRun:
    @pytest.mark.asyncio
    async def test_rejection_fails_the_step(self):
        with patch("sandcastle.engine.executor._run_accept_judge", _verdict(False)):
            r = await _run(_step(), _ctx())
        assert r.status == "failed"
        assert "Accept rejected" in (r.error or "")

    @pytest.mark.asyncio
    async def test_rejection_is_not_retryable(self):
        """Without this a step-level retry: re-judges and can flip the verdict by luck."""
        with patch("sandcastle.engine.executor._run_accept_judge", _verdict(False)):
            r = await _run(_step(), _ctx())
        assert r.retryable is False

    @pytest.mark.asyncio
    async def test_reason_is_restated_in_error(self):
        """A failed step publishes no output, so the verdict must reach .error."""
        with patch(
            "sandcastle.engine.executor._run_accept_judge",
            _verdict(False, reason="missing the summary"),
        ):
            r = await _run(_step(), _ctx())
        assert "missing the summary" in (r.error or "")

    @pytest.mark.asyncio
    async def test_fail_on_reject_false_keeps_the_verdict_advisory(self):
        with patch("sandcastle.engine.executor._run_accept_judge", _verdict(False)):
            r = await _run(_step(fail_on_reject=False), _ctx())
        assert r.status == "completed"
        assert r.output["decision"] == "rejected"

    @pytest.mark.asyncio
    async def test_approval_completes(self):
        with patch("sandcastle.engine.executor._run_accept_judge", _verdict(True)):
            r = await _run(_step(), _ctx())
        assert r.status == "completed"
        assert r.output["decision"] == "approved"


class TestQuorum:
    @staticmethod
    def _panel(*verdicts):
        calls = list(verdicts)

        async def _fake(judge, prompt, timeout):
            approved = calls.pop(0)
            return {
                "name": judge.name,
                "model": judge.model,
                "verdict": "approved" if approved else "rejected",
                "reason": "r",
                "cost_usd": 0.001,
            }

        return _fake

    @pytest.mark.asyncio
    async def test_default_quorum_is_unanimous(self):
        step = _step(
            judges=[
                AcceptJudge(rubric="a", model="haiku", name="j1"),
                AcceptJudge(rubric="b", model="haiku", name="j2"),
            ]
        )
        with patch("sandcastle.engine.executor._run_accept_judge", self._panel(True, False)):
            r = await _run(step, _ctx())
        assert r.status == "failed"
        assert r.output["rounds"][-1]["quorum"] == {
            "required": 2,
            "approved": 1,
            "total": 2,
        }

    @pytest.mark.asyncio
    async def test_n_of_m_passes(self):
        step = _step(
            quorum=2,
            judges=[
                AcceptJudge(rubric="a", model="haiku", name="j1"),
                AcceptJudge(rubric="b", model="haiku", name="j2"),
                AcceptJudge(rubric="c", model="haiku", name="j3"),
            ],
        )
        with patch(
            "sandcastle.engine.executor._run_accept_judge",
            self._panel(True, False, True),
        ):
            r = await _run(step, _ctx())
        assert r.status == "completed"
        assert r.output["rounds"][-1]["quorum"]["approved"] == 2


class TestJudgeFailsClosed:
    """The eval harness fabricates 0.5 on error. This must not."""

    @pytest.mark.asyncio
    async def test_http_error_is_a_rejection_not_an_approval(self):
        import httpx

        step = _step()
        with patch("httpx.AsyncClient", side_effect=httpx.ConnectError("boom")):
            r = await _run(step, _ctx())
        assert r.status == "failed"
        judge = r.output["rounds"][-1]["judges"][0]
        assert judge["verdict"] == "rejected"
        assert "error" in judge

    @pytest.mark.asyncio
    async def test_unparseable_reply_is_a_rejection(self):
        """A reply the parser cannot read is a reject, with the reason recorded."""
        from sandcastle.engine.executor import _run_accept_judge

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "content": [{"text": "hmm, hard to say"}],
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                }

        class _Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **k):
                return _Resp()

        with patch("httpx.AsyncClient", _Client):
            record = await _run_accept_judge(
                AcceptJudge(rubric="r", model="haiku", name="j1"), "p", 60
            )
        assert record["verdict"] == "rejected"
        assert record["error"] == "unparseable verdict"


class TestJudgeCostAccounting:
    """Judges bill through _safe_cost, unlike the eval-harness path."""

    @pytest.mark.asyncio
    async def test_cost_is_recorded_from_token_usage(self):
        from sandcastle.engine.executor import _run_accept_judge

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "content": [{"text": "APPROVE\nfine"}],
                    "usage": {"input_tokens": 10000, "output_tokens": 500},
                }

        class _Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **k):
                return _Resp()

        with patch("httpx.AsyncClient", _Client):
            record = await _run_accept_judge(
                AcceptJudge(rubric="r", model="haiku", name="j1"), "p", 60
            )
        assert record["verdict"] == "approved"
        assert record["cost_usd"] > 0.0


class TestEvidencePack:
    @pytest.mark.asyncio
    async def test_pack_records_what_was_judged(self):
        with patch("sandcastle.engine.executor._run_accept_judge", _verdict(True)):
            r = await _run(_step(), _ctx())
        pack = r.output
        assert pack["targets"] == ["write"]
        assert pack["rounds_used"] == 1
        last = pack["rounds"][-1]
        assert last["targets"]["write"]["digest"].startswith("sha256:")
        assert last["judges"][0]["model"] == "haiku"
        assert last["judges"][0]["verdict"] == "approved"
        assert last["judges"][0]["cost_usd"] == 0.001
        assert last["round"] == 1

    @pytest.mark.asyncio
    async def test_digest_tracks_the_output(self):
        from sandcastle.engine.executor import _accept_digest

        assert _accept_digest("a") != _accept_digest("b")
        assert _accept_digest("a") == _accept_digest("a")

    @pytest.mark.asyncio
    async def test_decision_reaches_the_audit_chain(self):
        emitted = []

        async def _spy(event_type, run_id, actor_id, payload):
            emitted.append((event_type, payload))

        with patch("sandcastle.engine.executor._run_accept_judge", _verdict(False)), patch(
            "sandcastle.engine.executor._emit_audit_event", _spy
        ):
            await _run(_step(), _ctx())
        assert emitted, "accept must leave an audit event"
        event_type, payload = emitted[-1]
        assert event_type == "step.accept"
        assert payload["decision"] == "rejected"
        assert payload["evidence"]["rounds"][-1]["judges"][0]["model"] == "haiku"


class TestReworkLoop:
    @staticmethod
    def _rerun_spy(results):
        calls = []

        async def _fake(step, context, sandbox, storage, **kwargs):
            calls.append((step, kwargs.get("step_overrides")))
            return StepResult(step_id=step.id, output=results.pop(0), cost_usd=0.02)

        return _fake, calls

    @pytest.mark.asyncio
    async def test_retry_target_reruns_and_can_succeed(self):
        verdicts = [False, True]

        async def _judge(judge, prompt, timeout):
            approved = verdicts.pop(0)
            return {
                "name": judge.name,
                "model": judge.model,
                "verdict": "approved" if approved else "rejected",
                "reason": "needs work",
                "cost_usd": 0.001,
            }

        rerun, calls = self._rerun_spy(["a better haiku"])
        step = _step(on_reject="retry_target", max_rounds=2)
        with patch("sandcastle.engine.executor._run_accept_judge", _judge), patch(
            "sandcastle.engine.executor.execute_step_with_retry", rerun
        ):
            r = await _run(step, _ctx())
        assert r.status == "completed"
        assert r.output["rounds_used"] == 2
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_max_rounds_bounds_the_loop(self):
        rerun, calls = self._rerun_spy(["x"] * 10)
        step = _step(on_reject="retry_target", max_rounds=3)
        with patch("sandcastle.engine.executor._run_accept_judge", _verdict(False)), patch(
            "sandcastle.engine.executor.execute_step_with_retry", rerun
        ):
            r = await _run(step, _ctx())
        assert r.status == "failed"
        assert r.output["rounds_used"] == 3
        assert len(calls) == 2  # re-work happens between rounds, not after the last

    @pytest.mark.asyncio
    async def test_critique_is_escaped_before_reinjection(self):
        """A judge that writes a template into its reason must not have it resolved."""
        rerun, calls = self._rerun_spy(["x"])
        step = _step(on_reject="retry_target", max_rounds=2)
        with patch(
            "sandcastle.engine.executor._run_accept_judge",
            _verdict(False, reason="add {steps.secret.output} here"),
        ), patch("sandcastle.engine.executor.execute_step_with_retry", rerun):
            await _run(step, _ctx())
        injected = calls[0][1]["prompt"]
        assert "{{steps.secret.output}}" in injected
        assert "{steps.secret.output}" not in injected.replace("{{", "").replace("}}", "")

    @pytest.mark.asyncio
    async def test_accept_local_budget_stops_rework(self):
        rerun, calls = self._rerun_spy(["x"] * 10)
        step = _step(on_reject="retry_target", max_rounds=5, max_cost_usd=0.0005)
        with patch("sandcastle.engine.executor._run_accept_judge", _verdict(False)), patch(
            "sandcastle.engine.executor.execute_step_with_retry", rerun
        ):
            r = await _run(step, _ctx())
        assert r.status == "failed"
        assert r.output["rounds_used"] == 1
        assert calls == []
        assert "accept budget" in r.output["reason"]

    @pytest.mark.asyncio
    async def test_run_budget_projection_stops_rework(self):
        rerun, calls = self._rerun_spy(["x"] * 10)
        ctx = _ctx()
        ctx.max_cost_usd = 0.005
        ctx.costs.append(0.0049)
        step = _step(on_reject="retry_target", max_rounds=5)
        with patch("sandcastle.engine.executor._run_accept_judge", _verdict(False)), patch(
            "sandcastle.engine.executor.execute_step_with_retry", rerun
        ):
            r = await _run(step, ctx)
        assert r.status == "failed"
        assert calls == []
        assert "run budget" in r.output["reason"]

    @pytest.mark.asyncio
    async def test_depth_guard_refuses_to_recurse_forever(self):
        from sandcastle.engine.executor import _execute_step_by_type

        step = _step(on_reject="retry_target")
        r = await _execute_step_by_type(
            step, _ctx(), None, _storage(), workflow=_wf(), depth=10_000
        )
        assert r.status == "failed"
        assert "depth" in (r.error or "").lower()
        assert r.retryable is False


class TestHumanEscalation:
    @pytest.mark.asyncio
    async def test_rejected_verdict_pauses_for_a_human(self):
        """on_reject: escalate_to_human hands the pack to the approval surface."""
        from unittest.mock import MagicMock

        from sandcastle.engine.executor import WorkflowPaused

        captured = {}
        session = AsyncMock()

        def _add(obj):
            captured["approval"] = obj

        session.add = MagicMock(side_effect=_add)

        async def _refresh(obj):
            obj.id = "11111111-1111-1111-1111-111111111111"

        session.refresh = AsyncMock(side_effect=_refresh)
        session.get = AsyncMock(return_value=MagicMock())

        step = _step(on_reject="escalate_to_human")
        with patch("sandcastle.engine.executor._run_accept_judge", _verdict(False)), patch(
            "sandcastle.engine.executor._emit_audit_event", new_callable=AsyncMock
        ), patch("sandcastle.models.db.async_session") as ctx_mgr:
            ctx_mgr.return_value.__aenter__ = AsyncMock(return_value=session)
            ctx_mgr.return_value.__aexit__ = AsyncMock(return_value=False)
            with pytest.raises(WorkflowPaused):
                await _run(step, _ctx())

        request_data = captured["approval"].request_data
        assert request_data["accept"]["decision"] == "rejected"
        # The overlay _resume_after_approval applies when a human approves.
        assert request_data["_on_approve"]["decision"] == "approved"
        assert request_data["_on_approve"]["escalated"] is True

    def test_resume_applies_the_on_approve_overlay(self):
        """The approval surface echoes request_data back; accept needs its pack."""
        import inspect

        from sandcastle.api import routes

        source = inspect.getsource(routes._resume_after_approval)
        assert '"_on_approve" in output_data' in source


class TestEngineRegistration:
    def test_accept_is_a_hybrid_type(self):
        """A type missing from this set silently falls through to the LLM path."""
        from sandcastle.engine.executor import _HYBRID_STEP_TYPES

        assert "accept" in _HYBRID_STEP_TYPES

    def test_accept_is_guard_exempt(self):
        """It blocks like a gate and recurses into a target guarded on its own."""
        from sandcastle.engine.effects import (
            _CONFIG_ATTRS,
            GUARD_EXEMPT_STEP_TYPES,
        )

        assert "accept" in GUARD_EXEMPT_STEP_TYPES
        # Exempt types are never fingerprinted, so the config must stay out of
        # _CONFIG_ATTRS - the same treatment gate_config gets.
        assert "accept_config" not in _CONFIG_ATTRS
        assert "gate_config" not in _CONFIG_ATTRS

    def test_dispatch_requires_a_workflow(self):
        import asyncio

        from sandcastle.engine.executor import _execute_step_by_type

        r = asyncio.run(
            _execute_step_by_type(
                _step(), _ctx(), None, _storage(), workflow=None, depth=0
            )
        )
        assert r.status == "failed"
        assert "workflow" in (r.error or "")


class TestCostEstimate:
    """The pre-run estimate must price the judge panel, not step.model."""

    @pytest.mark.asyncio
    async def test_estimate_prices_judges_per_round(self):
        from sandcastle.api.routes import estimate_run_cost
        from sandcastle.api.schemas import RunEstimateRequest

        yaml_text = """
name: t
steps:
  - id: write
    type: llm
    prompt: "Write."
  - id: acc
    type: accept
    accept_config:
      target: write
      max_rounds: 2
      judges:
        - model: sonnet
          rubric: "Good?"
        - model: haiku
          rubric: "Honest?"
"""
        resp = await estimate_run_cost(RunEstimateRequest(yaml_content=yaml_text))
        entry = next(s for s in resp.data["steps"] if s["step_id"] == "acc")
        assert entry["estimated_cost_usd"] > 0
        assert "sonnet" in entry["model"] and "haiku" in entry["model"]
        assert "2 judge(s) x 3 round(s)" in entry["note"]

    @pytest.mark.asyncio
    async def test_checks_only_accept_is_free(self):
        from sandcastle.api.routes import estimate_run_cost
        from sandcastle.api.schemas import RunEstimateRequest

        yaml_text = """
name: t
steps:
  - id: write
    type: llm
    prompt: "Write."
  - id: acc
    type: accept
    accept_config:
      target: write
      checks:
        - type: not_empty
"""
        resp = await estimate_run_cost(RunEstimateRequest(yaml_content=yaml_text))
        entry = next(s for s in resp.data["steps"] if s["step_id"] == "acc")
        assert entry["estimated_cost_usd"] == 0.0


class TestValidation:
    @staticmethod
    def _errors(yaml_text):
        return validate(parse_yaml_string(yaml_text))

    def test_valid_workflow_has_no_errors(self):
        assert self._errors(
            """
name: t
steps:
  - id: write
    type: llm
    prompt: "Write."
  - id: acc
    type: accept
    accept_config:
      target: write
      judges:
        - model: haiku
          rubric: "Good?"
"""
        ) == []

    def test_self_target_is_rejected(self):
        errors = self._errors(
            """
name: t
steps:
  - id: acc
    type: accept
    accept_config:
      target: acc
      judges:
        - model: haiku
          rubric: "Good?"
"""
        )
        assert any("cannot target itself" in e for e in errors)

    def test_unknown_target_is_rejected(self):
        errors = self._errors(
            """
name: t
steps:
  - id: acc
    type: accept
    accept_config:
      target: ghost
      judges:
        - model: haiku
          rubric: "Good?"
"""
        )
        assert any("targets unknown step 'ghost'" in e for e in errors)

    def test_cycle_is_rejected(self):
        errors = self._errors(
            """
name: t
steps:
  - id: write
    type: llm
    prompt: "Revise per {steps.acc.output}"
  - id: acc
    type: accept
    accept_config:
      target: write
      judges:
        - model: haiku
          rubric: "Good?"
"""
        )
        assert any("would never terminate" in e for e in errors)

    def test_quorum_above_judge_count_is_rejected(self):
        errors = self._errors(
            """
name: t
steps:
  - id: write
    type: llm
    prompt: "Write."
  - id: acc
    type: accept
    accept_config:
      target: write
      quorum: 4
      judges:
        - model: haiku
          rubric: "Good?"
"""
        )
        assert any("only 1 judge" in e for e in errors)

    def test_llm_judge_check_is_rejected(self):
        """checks: is the free, deterministic half. That path has no cost accounting."""
        errors = self._errors(
            """
name: t
steps:
  - id: write
    type: llm
    prompt: "Write."
  - id: acc
    type: accept
    accept_config:
      target: write
      checks:
        - type: llm_judge
          criteria: "quality"
"""
        )
        assert any("not allowed in checks" in e for e in errors)

    def test_max_rounds_is_hard_capped(self):
        errors = self._errors(
            f"""
name: t
steps:
  - id: write
    type: llm
    prompt: "Write."
  - id: acc
    type: accept
    accept_config:
      target: write
      max_rounds: {MAX_ACCEPT_ROUNDS + 1}
      judges:
        - model: haiku
          rubric: "Good?"
"""
        )
        assert any("max_rounds must be <=" in e for e in errors)

    def test_unknown_judge_model_is_rejected(self):
        errors = self._errors(
            """
name: t
steps:
  - id: write
    type: llm
    prompt: "Write."
  - id: acc
    type: accept
    accept_config:
      target: write
      judges:
        - model: not-a-real-model
          rubric: "Good?"
"""
        )
        assert any("Unknown model 'not-a-real-model'" in e for e in errors)

    def test_empty_config_needs_something_to_decide_with(self):
        errors = self._errors(
            """
name: t
steps:
  - id: write
    type: llm
    prompt: "Write."
  - id: acc
    type: accept
    accept_config:
      target: write
"""
        )
        assert any("nothing to decide with" in e for e in errors)

    def test_target_implies_ordering(self):
        from sandcastle.engine.dag import build_plan

        wf = parse_yaml_string(
            """
name: t
steps:
  - id: acc
    type: accept
    accept_config:
      target: write
      judges:
        - model: haiku
          rubric: "Good?"
  - id: write
    type: llm
    prompt: "Write."
"""
        )
        assert build_plan(wf).stages == [["write"], ["acc"]]
