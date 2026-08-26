"""The `type: acp` step: registration, parsing, validation, dispatch, ledger, cost.

The protocol layer is covered in ``tests/test_acp_client.py``. This file is
about the step type being wired into the engine correctly - which is mostly a
set of registrations that fail *silently* when forgotten, so most of these are
one-line assertions guarding a trap rather than tests of interesting logic.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from sandcastle.config import settings
from sandcastle.engine import executor as _executor_mod
from sandcastle.engine.acp_client import AcpTurnResult
from sandcastle.engine.dag import (
    NON_LLM_TYPES,
    NON_PROMPT_TYPES,
    VALID_STEP_TYPES,
    AcpConfig,
    StepDefinition,
    WorkflowDefinition,
    build_plan,
    parse_yaml_string,
    validate,
)
from sandcastle.engine.executor import RunContext, execute_workflow
from sandcastle.engine.storage import LocalStorage

_execute_acp_step = _executor_mod._execute_acp_step
_acp_cost = _executor_mod._acp_cost

FAKE_AGENT = Path(__file__).parent / "fixtures" / "fake_acp_agent.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_yaml(steps_yaml: str) -> str:
    return (
        "name: acp-test\n"
        "description: test the acp step\n"
        "steps:\n" + steps_yaml
    )


def _step(**cfg_overrides) -> StepDefinition:
    cfg = dict(command=sys.executable, args=[str(FAKE_AGENT), "echo"], cwd="/tmp/repo")
    cfg.update(cfg_overrides)
    return StepDefinition(id="implement", type="acp", acp_config=AcpConfig(**cfg))


def _context(**overrides) -> RunContext:
    defaults = dict(
        run_id="run-acp-1",
        input={},
        step_outputs={},
        step_results={},
        admin_trusted=True,
    )
    defaults.update(overrides)
    return RunContext(**defaults)


def _wf(step: StepDefinition) -> WorkflowDefinition:
    return WorkflowDefinition(
        name="t",
        description="",
        default_model="sonnet",
        default_max_turns=10,
        default_timeout=300,
        steps=[step],
    )


def _acp_yaml(workspace: Path, agent_mode: str, log: Path | None = None, extra: str = "") -> str:
    env_lines = f'        FAKE_ACP_LOG: "{log}"\n' if log else ""
    return f"""
name: acp-e2e
description: drive the fake ACP agent end to end
default_model: sonnet
steps:
  - id: implement
    type: acp
    acp_config:
      command: "{sys.executable}"
      args: ["{FAKE_AGENT}", "{agent_mode}"]
      cwd: "{workspace}"
      env:
{env_lines if env_lines else '        NOOP: "1"'}
      timeout: 30
      idle_timeout: 0
{extra}
"""


@pytest.fixture
def allow_roots(tmp_path):
    """Point settings.acp_allowed_roots at the test's own tmp dir."""
    with patch.object(settings, "acp_allowed_roots", [str(tmp_path)]):
        yield tmp_path


# ===================================================================
# 1. Registration - the silent-failure traps
# ===================================================================

class TestRegistration:
    def test_acp_is_a_valid_step_type(self):
        assert "acp" in VALID_STEP_TYPES

    def test_step_type_count_is_twenty_seven(self):
        """Was 26 in 0.45; 0.46 adds `accept`, the outcome gate."""
        assert len(VALID_STEP_TYPES) == 27

    def test_acp_needs_no_prompt(self):
        """The brief lives in acp_config.message."""
        assert "acp" in NON_PROMPT_TYPES

    def test_acp_skips_model_validation(self):
        """The model an external harness picks is not ours to validate."""
        assert "acp" in NON_LLM_TYPES

    def test_acp_is_in_hybrid_step_types(self):
        """The single easiest registration to forget, and it fails silently.

        A step type missing from _HYBRID_STEP_TYPES does not raise. It falls
        through to _execute_step_once - the generic LLM-in-sandbox path - and
        runs the synthesized placeholder prompt ("acp step") as an LLM call.
        The workflow then reports `completed` having never spawned the agent.
        """
        assert "acp" in _executor_mod._HYBRID_STEP_TYPES

    def test_acp_is_dispatched_by_type(self):
        """And the dispatcher must have an arm for it, or it raises."""
        import inspect

        source = inspect.getsource(_executor_mod._execute_step_by_type)
        assert 'step.type == "acp"' in source
        assert "_execute_acp_step" in source

    def test_acp_is_not_mesh_routable(self):
        """A deliberate decision, asserted so it stays one.

        cwd is a local path: routing an acp step to a node with a different
        filesystem is a silent correctness bug, not a scheduling win. The wire
        payload would also have to carry env_passthrough credential *names*,
        and session/cancel plus the session/update stream are both bound to a
        live stdio pipe on the executing node.
        """
        from sandcastle.engine.mesh import (
            _PAYLOAD_CONFIG_TYPES,
            ROUTABLE_STEP_TYPES,
        )

        assert "acp" not in ROUTABLE_STEP_TYPES
        assert "acp_config" not in _PAYLOAD_CONFIG_TYPES

    def test_acp_costs_nothing_in_the_pre_run_estimator(self):
        """An LLM-token estimate for an acp step would be invented."""
        import inspect

        from sandcastle.api import routes

        source = inspect.getsource(routes)
        assert '"acp",' in source

    def test_step_progress_is_a_registered_event_type(self):
        from sandcastle.engine.events import EventBus

        assert "step.progress" in EventBus.EVENT_TYPES

    def test_generator_prompt_documents_acp(self):
        from sandcastle.engine.generator import _build_system_prompt

        prompt = _build_system_prompt()
        assert "acp" in prompt
        assert "acp_config" in prompt


# ===================================================================
# 2. Config dataclass
# ===================================================================

class TestAcpConfigDefaults:
    def test_defaults_are_the_closed_ones(self):
        cfg = AcpConfig()
        assert cfg.command == ""
        assert cfg.args == []
        assert cfg.agent == ""
        assert cfg.env == {}
        assert cfg.env_passthrough == []
        assert cfg.cwd == ""
        assert cfg.additional_directories == []
        assert cfg.mcp_servers == []
        assert cfg.mode == ""
        assert cfg.config_options == {}
        assert cfg.message == ""
        assert cfg.permission == "reject"
        assert cfg.permission_rules == []
        assert cfg.filesystem == "none"
        assert cfg.terminal is False
        assert cfg.elicitation == "decline"
        assert cfg.timeout == 900
        assert cfg.idle_timeout == 180
        assert cfg.max_output_chars == 200000
        assert cfg.cost_per_call == 0.0
        assert cfg.protocol_version == 1
        assert cfg.strict_version is True
        assert cfg.output_format == "text"
        assert cfg.include_thoughts is False
        assert cfg.include_tool_calls is True

    def test_field_count(self):
        fields = dataclasses.fields(AcpConfig)
        assert len(fields) == 25, [f.name for f in fields]

    def test_step_definition_has_acp_config(self):
        names = [f.name for f in dataclasses.fields(StepDefinition)]
        assert "acp_config" in names
        assert StepDefinition(id="x").acp_config is None


# ===================================================================
# 3. YAML parsing
# ===================================================================

class TestYamlParsing:
    def test_minimal(self):
        wf = parse_yaml_string(
            _base_yaml(
                "  - id: a\n"
                "    type: acp\n"
                "    acp_config:\n"
                "      agent: claude\n"
                "      cwd: /srv/repo\n"
            )
        )
        cfg = wf.steps[0].acp_config
        assert cfg.agent == "claude"
        assert cfg.cwd == "/srv/repo"
        assert cfg.permission == "reject"

    def test_absent_acp_config_is_none(self):
        wf = parse_yaml_string(_base_yaml("  - id: a\n    type: llm\n    prompt: hi\n"))
        assert wf.steps[0].acp_config is None

    def test_scalar_args_become_a_one_element_list(self):
        """`args: --acp` means one argument, not five characters."""
        wf = parse_yaml_string(
            _base_yaml(
                "  - id: a\n"
                "    type: acp\n"
                "    acp_config:\n"
                "      command: goose\n"
                "      args: acp\n"
                "      cwd: /srv/repo\n"
            )
        )
        assert wf.steps[0].acp_config.args == ["acp"]

    def test_numeric_env_values_are_stringified(self):
        wf = parse_yaml_string(
            _base_yaml(
                "  - id: a\n"
                "    type: acp\n"
                "    acp_config:\n"
                "      command: goose\n"
                "      cwd: /srv/repo\n"
                "      env:\n"
                "        RETRIES: 3\n"
            )
        )
        assert wf.steps[0].acp_config.env == {"RETRIES": "3"}

    def test_full_config_round_trips(self):
        wf = parse_yaml_string(
            _base_yaml(
                "  - id: a\n"
                "    type: acp\n"
                "    acp_config:\n"
                "      command: npx\n"
                "      args: [\"@agentclientprotocol/claude-agent-acp\"]\n"
                "      cwd: /srv/repo\n"
                "      env_passthrough: [ANTHROPIC_API_KEY]\n"
                "      message: do it\n"
                "      permission: ask\n"
                "      permission_rules:\n"
                "        - kind: edit\n"
                "          decision: allow_once\n"
                "      filesystem: readwrite\n"
                "      timeout: 1200\n"
                "      idle_timeout: 240\n"
                "      max_output_chars: 5000\n"
                "      cost_per_call: 0.25\n"
                "      output_format: full\n"
                "      include_thoughts: true\n"
                "      include_tool_calls: false\n"
            )
        )
        cfg = wf.steps[0].acp_config
        assert cfg.args == ["@agentclientprotocol/claude-agent-acp"]
        assert cfg.env_passthrough == ["ANTHROPIC_API_KEY"]
        assert cfg.permission_rules == [{"kind": "edit", "decision": "allow_once"}]
        assert cfg.filesystem == "readwrite"
        assert cfg.cost_per_call == 0.25
        assert cfg.output_format == "full"
        assert cfg.include_thoughts is True
        assert cfg.include_tool_calls is False

    def test_no_prompt_needed(self):
        wf = parse_yaml_string(
            _base_yaml(
                "  - id: a\n"
                "    type: acp\n"
                "    acp_config:\n"
                "      agent: claude\n"
                "      cwd: /srv/repo\n"
            )
        )
        assert wf.steps[0].prompt == "acp step"


# ===================================================================
# 4. Validation
# ===================================================================

def _errors(step: StepDefinition) -> list[str]:
    return validate(_wf(step))


class TestValidation:
    def test_valid_step_has_no_errors(self):
        assert _errors(_step()) == []

    def test_missing_acp_config(self):
        step = StepDefinition(id="a", type="acp")
        assert any("must have acp_config" in e for e in _errors(step))

    def test_neither_command_nor_agent(self):
        step = _step(command="", args=[])
        assert any("exactly one of" in e for e in _errors(step))

    def test_both_command_and_agent(self):
        step = _step(agent="claude")
        assert any("exactly one of" in e for e in _errors(step))

    def test_unknown_agent_shorthand(self):
        step = _step(command="", args=[], agent="not-a-real-harness")
        errors = _errors(step)
        assert any("unknown agent" in e for e in errors)

    def test_missing_cwd(self):
        step = _step(cwd="")
        assert any("cwd" in e for e in _errors(step))

    def test_terminal_true_is_refused(self):
        step = _step(terminal=True)
        assert any("terminal is not supported" in e for e in _errors(step))

    def test_elicitation_ask_is_refused(self):
        step = _step(elicitation="ask")
        assert any("elicitation 'ask' is not supported" in e for e in _errors(step))

    def test_protocol_version_two_is_refused(self):
        """v2 is an unstable draft that restructures exactly what we use."""
        step = _step(protocol_version=2)
        assert any("protocol_version must be 1" in e for e in _errors(step))

    @pytest.mark.parametrize(
        "overrides,needle",
        [
            ({"timeout": 0}, "timeout must be > 0"),
            ({"idle_timeout": -1}, "idle_timeout must be >= 0"),
            ({"max_output_chars": 0}, "max_output_chars must be > 0"),
            ({"permission": "sure"}, "invalid permission"),
            ({"filesystem": "everything"}, "invalid filesystem"),
            ({"output_format": "yaml"}, "invalid output_format"),
            ({"elicitation": "maybe"}, "invalid elicitation"),
        ],
    )
    def test_field_rejections(self, overrides, needle):
        assert any(needle in e for e in _errors(_step(**overrides)))

    def test_invalid_permission_rule_decision(self):
        step = _step(permission_rules=[{"kind": "edit", "decision": "sure why not"}])
        assert any("permission_rules[0]" in e for e in _errors(step))

    def test_mcp_server_needs_a_name(self):
        step = _step(mcp_servers=[{"command": "x", "args": []}])
        assert any("needs a name" in e for e in _errors(step))

    def test_mcp_server_needs_a_transport(self):
        step = _step(mcp_servers=[{"name": "x"}])
        assert any("command" in e and "url" in e for e in _errors(step))

    def test_mcp_server_entry_must_be_a_mapping(self):
        step = _step(mcp_servers=["oops"])
        assert any("must be a mapping" in e for e in _errors(step))

    def test_bare_acp_step_is_not_an_unknown_type(self):
        """Registration check: a bare acp step complains about config, not type."""
        errors = _errors(StepDefinition(id="a", type="acp"))
        assert not any("unknown type" in e.lower() for e in errors)


# ===================================================================
# 5. Template variables and implicit dependencies
# ===================================================================

class TestTemplateFields:
    def test_message_creates_an_implicit_edge(self):
        wf = parse_yaml_string(
            _base_yaml(
                "  - id: plan\n"
                "    type: llm\n"
                "    prompt: write a brief\n"
                "  - id: implement\n"
                "    type: acp\n"
                "    acp_config:\n"
                "      agent: claude\n"
                "      cwd: /srv/repo\n"
                "      message: \"Brief: {steps.plan.output}\"\n"
            )
        )
        plan = build_plan(wf)
        assert plan.stages[0] == ["plan"]
        assert plan.stages[1] == ["implement"]

    def test_cwd_creates_an_implicit_edge(self):
        wf = parse_yaml_string(
            _base_yaml(
                "  - id: checkout\n"
                "    type: llm\n"
                "    prompt: check out the repo\n"
                "  - id: implement\n"
                "    type: acp\n"
                "    acp_config:\n"
                "      agent: claude\n"
                "      cwd: \"{steps.checkout.output}\"\n"
            )
        )
        plan = build_plan(wf)
        assert plan.stages[1] == ["implement"]

    def test_env_values_are_scanned_too(self):
        from sandcastle.engine.dag import _collect_step_template_fields

        step = _step(env={"BRIEF": "{steps.plan.output}"})
        assert "{steps.plan.output}" in _collect_step_template_fields(step)

    def test_command_is_never_template_resolved(self):
        """An upstream step's output must not be able to become the executable."""
        from sandcastle.engine.dag import _collect_step_template_fields

        step = _step(command="{steps.evil.output}", args=["{steps.evil.output}"])
        assert "{steps.evil.output}" not in _collect_step_template_fields(step)


# ===================================================================
# 6. The effect ledger (workstream B integration)
# ===================================================================

class TestEffectLedgerIntegration:
    def test_acp_is_not_guard_exempt(self):
        """An external agent that edits files must not silently replay.

        Exempting acp would mean a replayed run re-spawns the harness and lets
        it redo its edits. Being guarded means the first turn is claimed, its
        output committed, and a replay in the same lineage returns that output
        at $0 instead of running an agent again.
        """
        from sandcastle.engine.effects import GUARD_EXEMPT_STEP_TYPES

        assert "acp" not in GUARD_EXEMPT_STEP_TYPES

    def test_acp_defaults_to_memoize(self):
        from sandcastle.engine.effects import effect_mode_for

        assert effect_mode_for(_step()) == "memoize"

    def test_acp_can_opt_into_live(self):
        from sandcastle.engine.effects import effect_mode_for

        step = _step()
        step.replay = "live"
        assert effect_mode_for(step) == "live"

    def test_on_uncertain_defaults_to_fail(self):
        """A half-finished agent turn left a half-edited repo. Do not guess."""
        from sandcastle.engine.effects import on_uncertain_for

        assert on_uncertain_for(_step()) == "fail"

    def test_fingerprint_covers_acp_config(self):
        """Without acp_config in the fingerprint every acp step hashes alike.

        acp is a NON_PROMPT type, so step.prompt is the synthesized placeholder
        "acp step" and step.model is the workflow default. Two acp steps in one
        workflow would then share a fingerprint and the ledger would hand one
        agent's transcript to the other.
        """
        from sandcastle.engine.effects import step_effect_fingerprint

        context = _context()
        a = step_effect_fingerprint(_step(message="implement feature A"), context)
        b = step_effect_fingerprint(_step(message="implement feature B"), context)
        assert a != b

    def test_fingerprint_changes_with_the_workspace(self):
        from sandcastle.engine.effects import step_effect_fingerprint

        context = _context()
        a = step_effect_fingerprint(_step(cwd="/srv/repo-a"), context)
        b = step_effect_fingerprint(_step(cwd="/srv/repo-b"), context)
        assert a != b

    def test_fingerprint_resolves_templates_in_the_message(self):
        from sandcastle.engine.effects import step_effect_fingerprint

        step = _step(message="Brief: {input.task}")
        a = step_effect_fingerprint(step, _context(input={"task": "one"}))
        b = step_effect_fingerprint(step, _context(input={"task": "two"}))
        assert a != b


# ===================================================================
# 7. Cost accounting
# ===================================================================

class TestCost:
    def test_reported_usd_wins(self):
        usage = {"used": 53000, "size": 200000, "cost": {"amount": 0.12, "currency": "USD"}}
        assert _acp_cost(AcpConfig(cost_per_call=0.5), usage, "s") == (0.12, "agent_reported")

    def test_foreign_currency_is_not_converted(self):
        """Sandcastle has no FX layer; inventing one corrupts max_cost_usd."""
        usage = {"used": 1, "size": 2, "cost": {"amount": 0.99, "currency": "EUR"}}
        cost, source = _acp_cost(AcpConfig(cost_per_call=0.25), usage, "s")
        assert cost == 0.25
        assert source == "declared_foreign_currency"

    def test_no_reported_cost_falls_back_to_declared(self):
        cost, source = _acp_cost(AcpConfig(cost_per_call=0.3), {"used": 1, "size": 2}, "s")
        assert cost == 0.3
        assert source == "declared"

    def test_no_usage_at_all_falls_back_to_declared(self):
        assert _acp_cost(AcpConfig(cost_per_call=0.0), None, "s") == (0.0, "declared")

    def test_used_is_never_turned_into_money(self):
        """`used` is context occupancy, not consumption. Pricing it is fiction."""
        big = {"used": 900000, "size": 1000000}
        small = {"used": 10, "size": 1000000}
        assert _acp_cost(AcpConfig(), big, "s") == _acp_cost(AcpConfig(), small, "s")

    def test_malformed_cost_amount_falls_back(self):
        usage = {"used": 1, "size": 2, "cost": {"amount": "lots", "currency": "USD"}}
        assert _acp_cost(AcpConfig(cost_per_call=0.1), usage, "s") == (0.1, "declared")


# ===================================================================
# 8. Handler guards
# ===================================================================

@pytest.mark.asyncio
class TestHandlerGuards:
    async def test_missing_config_fails_without_retrying(self):
        step = StepDefinition(id="a", type="acp")
        result = await _execute_acp_step(step, _context(), None)
        assert result.status == "failed"
        assert result.retryable is False

    async def test_non_admin_workflows_cannot_spawn_a_harness(self, tmp_path):
        result = await _execute_acp_step(
            _step(cwd=str(tmp_path)), _context(admin_trusted=False), LocalStorage(str(tmp_path))
        )
        assert result.status == "failed"
        assert "admin-trusted" in result.error
        assert result.retryable is False

    async def test_data_residency_fails_closed(self, tmp_path, allow_roots):
        """We do not know which model a harness calls, so we cannot promise a region."""
        with patch.object(settings, "data_residency", "eu"):
            result = await _execute_acp_step(
                _step(cwd=str(tmp_path)), _context(), LocalStorage(str(tmp_path))
            )
        assert result.status == "failed"
        assert "data_residency" in result.error
        assert result.retryable is False

    async def test_no_allowed_roots_disables_the_step(self, tmp_path):
        with patch.object(settings, "acp_allowed_roots", []):
            result = await _execute_acp_step(
                _step(cwd=str(tmp_path)), _context(), LocalStorage(str(tmp_path))
            )
        assert result.status == "failed"
        assert "disabled" in result.error
        assert result.retryable is False

    async def test_cwd_outside_the_allowed_root_is_refused(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        with patch.object(settings, "acp_allowed_roots", [str(root)]):
            result = await _execute_acp_step(
                _step(cwd=str(outside)), _context(), LocalStorage(str(tmp_path))
            )
        assert result.status == "failed"
        assert result.retryable is False

    async def test_unknown_agent_shorthand_is_not_retryable(self, tmp_path, allow_roots):
        step = _step(command="", args=[], agent="nope", cwd=str(tmp_path))
        result = await _execute_acp_step(step, _context(), LocalStorage(str(tmp_path)))
        assert result.status == "failed"
        assert result.retryable is False


# ===================================================================
# 9. Stop reason mapping and output shaping
# ===================================================================

def _fake_turn(**overrides) -> AcpTurnResult:
    defaults = dict(
        text="the answer",
        stop_reason="end_turn",
        session_id="sess_1",
        agent_info={"name": "fake", "version": "1"},
    )
    defaults.update(overrides)
    return AcpTurnResult(**defaults)


@pytest.mark.asyncio
class TestResultMapping:
    async def _run(self, step, turn, tmp_path, **ctx):
        # The handler imports run_acp_turn from acp_client at call time, so the
        # module attribute is the one that has to be patched.
        with patch.object(settings, "acp_allowed_roots", [str(tmp_path)]), patch(
            "sandcastle.engine.acp_client.run_acp_turn", AsyncMock(return_value=turn)
        ):
            return await _execute_acp_step(
                step, _context(**ctx), LocalStorage(str(tmp_path))
            )

    @pytest.mark.parametrize(
        "stop_reason,status,retryable",
        [
            ("end_turn", "completed", True),
            ("max_tokens", "completed", True),
            ("max_turn_requests", "completed", True),
            ("refusal", "failed", False),
            ("cancelled", "failed", False),
        ],
    )
    async def test_stop_reason_mapping(self, stop_reason, status, retryable, tmp_path):
        step = _step(cwd=str(tmp_path))
        result = await self._run(step, _fake_turn(stop_reason=stop_reason), tmp_path)
        assert result.status == status
        assert result.retryable is retryable

    async def test_refusal_is_not_retried(self, tmp_path):
        """A refusal is deterministic; retrying it buys a second no."""
        step = _step(cwd=str(tmp_path))
        result = await self._run(step, _fake_turn(stop_reason="refusal"), tmp_path)
        assert result.retryable is False

    async def test_text_output_format(self, tmp_path):
        step = _step(cwd=str(tmp_path), output_format="text")
        result = await self._run(step, _fake_turn(), tmp_path)
        assert result.output == "the answer"

    async def test_json_output_format(self, tmp_path):
        step = _step(cwd=str(tmp_path), output_format="json")
        result = await self._run(step, _fake_turn(text='{"verdict": "accept"}'), tmp_path)
        assert result.output == {"verdict": "accept"}

    async def test_json_output_format_on_malformed_json(self, tmp_path):
        step = _step(cwd=str(tmp_path), output_format="json")
        result = await self._run(step, _fake_turn(text="not json"), tmp_path)
        assert result.output == {"raw_text": "not json", "_parse_error": True}

    async def test_full_output_format(self, tmp_path):
        step = _step(cwd=str(tmp_path), output_format="full", include_thoughts=True)
        turn = _fake_turn(
            thoughts="hmm",
            tool_calls=[{"toolCallId": "tc_1", "kind": "edit"}],
            permissions=[{"decision": "reject_once"}],
            usage={"used": 1, "size": 2},
            plan=[{"content": "x"}],
            modes={"current": "code", "available": ["code"]},
        )
        result = await self._run(step, turn, tmp_path)
        out = result.output
        assert out["text"] == "the answer"
        assert out["stop_reason"] == "end_turn"
        assert out["session_id"] == "sess_1"
        assert out["thoughts"] == "hmm"
        assert out["tool_calls"] == [{"toolCallId": "tc_1", "kind": "edit"}]
        assert out["permissions"] == [{"decision": "reject_once"}]
        assert out["usage"]["cost_source"] == "declared"
        assert out["plan"] == [{"content": "x"}]
        assert out["modes"] == {"current": "code", "available": ["code"]}
        assert out["truncated"] is False

    async def test_tool_calls_can_be_suppressed(self, tmp_path):
        step = _step(cwd=str(tmp_path), output_format="full", include_tool_calls=False)
        result = await self._run(step, _fake_turn(tool_calls=[{"toolCallId": "x"}]), tmp_path)
        assert "tool_calls" not in result.output

    async def test_max_tokens_marks_the_output_truncated(self, tmp_path):
        step = _step(cwd=str(tmp_path), output_format="full")
        result = await self._run(step, _fake_turn(stop_reason="max_tokens"), tmp_path)
        assert result.output["truncated"] is True


# ===================================================================
# 10. End to end against the fake agent
# ===================================================================

async def _run_wf(yaml_text: str, tmp_path, *, scope=None, admin=True, **kwargs):
    wf = parse_yaml_string(yaml_text)
    assert validate(wf) == []
    return await execute_workflow(
        workflow=wf,
        plan=build_plan(wf),
        input_data=kwargs.pop("input_data", {}),
        run_id=str(uuid.uuid4()),
        storage=LocalStorage(str(tmp_path)),
        admin_trusted=admin,
        effect_scope_id=scope,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_end_to_end_drives_the_external_agent(tmp_path, allow_roots):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    log = tmp_path / "log.jsonl"
    result = await _run_wf(_acp_yaml(workspace, "echo", log), tmp_path)
    assert result.status == "completed", result.error
    assert result.outputs["implement"] == "Hello from the fake agent."
    events = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    assert [e["event"] for e in events if e["event"] == "initialize"]


@pytest.mark.asyncio
async def test_end_to_end_non_admin_run_is_refused(tmp_path, allow_roots):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    result = await _run_wf(_acp_yaml(workspace, "echo"), tmp_path, admin=False)
    assert result.status == "failed"
    assert "admin-trusted" in (result.error or "")


@pytest.mark.asyncio
async def test_end_to_end_refusal_fails_the_run(tmp_path, allow_roots):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    result = await _run_wf(_acp_yaml(workspace, "refusal"), tmp_path)
    assert result.status == "failed"
    assert "refusal" in (result.error or "")


@pytest.mark.asyncio
async def test_replayed_acp_step_does_not_respawn_the_agent(tmp_path, allow_roots):
    """The ledger decision, end to end.

    An external agent harness edits files and calls tools. Running it twice in
    one replay lineage would redo those effects, so the second run must return
    the committed transcript instead of spawning anything.
    """
    workspace = tmp_path / "repo"
    workspace.mkdir()
    log = tmp_path / "log.jsonl"
    scope = str(uuid.uuid4())
    yaml_text = _acp_yaml(workspace, "echo", log)

    first = await _run_wf(yaml_text, tmp_path, scope=scope)
    assert first.status == "completed", first.error
    spawns = log.read_text().count('"event": "initialize"')
    assert spawns == 1

    second = await _run_wf(yaml_text, tmp_path, scope=scope)
    assert second.status == "completed", second.error
    assert log.read_text().count('"event": "initialize"') == 1  # <-- the assertion
    assert second.outputs["implement"] == first.outputs["implement"]
    assert second.total_cost_usd == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_a_different_lineage_runs_the_agent_again(tmp_path, allow_roots):
    """Anti-false-pass guard: two unrelated runs are two lineages."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    log = tmp_path / "log.jsonl"
    yaml_text = _acp_yaml(workspace, "echo", log)

    await _run_wf(yaml_text, tmp_path, scope=str(uuid.uuid4()))
    await _run_wf(yaml_text, tmp_path, scope=str(uuid.uuid4()))

    assert log.read_text().count('"event": "initialize"') == 2


@pytest.mark.asyncio
async def test_cost_reaches_the_budget_from_the_agents_own_report(tmp_path, allow_roots):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    result = await _run_wf(_acp_yaml(workspace, "usage"), tmp_path)
    assert result.status == "completed", result.error
    assert result.total_cost_usd == pytest.approx(0.12)


@pytest.mark.asyncio
async def test_a_harness_reporting_nothing_bills_the_declared_cost(tmp_path, allow_roots):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    result = await _run_wf(
        _acp_yaml(workspace, "echo", extra="      cost_per_call: 0.4\n"), tmp_path
    )
    assert result.total_cost_usd == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_permission_decisions_are_in_the_output(tmp_path, allow_roots):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    result = await _run_wf(
        _acp_yaml(workspace, "permission", extra="      output_format: full\n"), tmp_path
    )
    assert result.status == "completed", result.error
    decisions = result.outputs["implement"]["permissions"]
    assert decisions[0]["kind"] == "execute"
    assert decisions[0]["decision"] == "reject_once"


# ===================================================================
# 11. The committed worked example
# ===================================================================

EXAMPLE = (
    Path(__file__).resolve().parent.parent
    / "workflows"
    / "acp"
    / "acp-refactor-and-review.yaml"
)


class TestWorkedExample:
    def test_it_exists(self):
        assert EXAMPLE.exists(), f"missing worked example at {EXAMPLE}"

    def test_it_parses_and_validates_clean(self):
        wf = parse_yaml_string(EXAMPLE.read_text())
        assert validate(wf) == []

    def test_the_acp_step_reads_the_brief_from_an_upstream_step(self):
        """The implicit-dependency edge through acp_config.message must exist."""
        wf = parse_yaml_string(EXAMPLE.read_text())
        plan = build_plan(wf)
        flat = [sid for stage in plan.stages for sid in stage]
        assert flat.index("plan") < flat.index("implement")

    def test_the_agent_is_followed_by_deterministic_verification(self):
        """'The agent finished' is not 'the task was done'.

        The example is the argument for the pattern, so a version of it without
        a git-diff step downstream of the agent would be making the wrong point.
        """
        wf = parse_yaml_string(EXAMPLE.read_text())
        diffstat = wf.get_step("diffstat")
        assert diffstat.type == "code"
        assert "diff" in diffstat.code_config.code
        assert "implement" in diffstat.depends_on

        review = wf.get_step("review")
        assert review.type == "llm"
        assert {"implement", "diffstat"} <= set(review.depends_on)

    def test_execution_is_rejected_by_default_even_in_ask_mode(self):
        wf = parse_yaml_string(EXAMPLE.read_text())
        cfg = wf.get_step("implement").acp_config
        rules = {r["kind"]: r["decision"] for r in cfg.permission_rules}
        assert rules["execute"] == "reject_once"
        assert cfg.terminal is False

    def test_credentials_are_forwarded_by_name_not_inherited(self):
        wf = parse_yaml_string(EXAMPLE.read_text())
        cfg = wf.get_step("implement").acp_config
        assert cfg.env_passthrough == ["ANTHROPIC_API_KEY"]


def test_docs_state_the_cost_honesty_and_the_black_box_status():
    """Two things the release must not leave ambiguous."""
    doc = (Path(__file__).resolve().parent.parent / "docs" / "acp.md").read_text()
    assert "cannot price them" in doc
    assert "ADVISORY" in doc
    assert "Black Box status" in doc
    assert "no seccomp or container isolation" in doc.lower()
