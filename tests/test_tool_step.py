"""Tests for the `tool` step type - deterministic connector invocation."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.dag import StepDefinition, ToolConfig, parse_yaml_string, validate
from sandcastle.engine.executor import RunContext, _execute_tool_step


def _fake_proc(stdout: str, stderr: str = "", returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
    proc.kill = MagicMock()
    return proc


def _ctx(**inp) -> RunContext:
    return RunContext(run_id="t", input=inp, step_outputs={})


def _step(**kw) -> StepDefinition:
    return StepDefinition(
        id=kw.get("id", "gen"),
        type="tool",
        timeout=kw.get("timeout", 60),
        tool_config=kw.get("tool_config"),
    )


# --- Parsing & validation ---


def test_tool_step_parses_from_yaml():
    wf = parse_yaml_string(
        """
name: t
steps:
  - id: gen
    type: tool
    tool_config:
      tool: nano-banana
      function: generate
      arguments:
        - "a prompt"
        - { model: pro, aspect: "4:5" }
"""
    )
    s = wf.steps[0]
    assert s.type == "tool"
    assert s.tool_config.tool == "nano-banana"
    assert s.tool_config.function == "generate"
    assert s.tool_config.arguments[1] == {"model": "pro", "aspect": "4:5"}
    assert validate(wf) == []


def test_tool_step_validation_requires_tool_and_function():
    wf = parse_yaml_string(
        """
name: t
steps:
  - id: gen
    type: tool
    tool_config: { tool: nano-banana }
"""
    )
    errors = validate(wf)
    assert any("must have tool_config" in e for e in errors)


# --- Execution ---


@pytest.mark.asyncio
async def test_tool_step_success_parses_json_and_cost():
    out = json.dumps({"ok": True, "files": ["/tmp/x.png"], "estimated_cost": 0.089})
    step = _step(
        tool_config=ToolConfig(tool="nano-banana", function="generate", arguments=["p"])
    )
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_fake_proc(out))):
        res = await _execute_tool_step(step, _ctx())
    assert res.status == "completed"
    assert res.output["files"] == ["/tmp/x.png"]
    assert res.cost_usd == pytest.approx(0.089)


@pytest.mark.asyncio
async def test_tool_step_resolves_templates_in_args():
    out = json.dumps({"ok": True, "files": ["/tmp/x.png"]})
    step = _step(
        tool_config=ToolConfig(
            tool="nano-banana",
            function="generate",
            arguments=["prompt", {"reference_images": ["{input.product_photo}"]}],
        )
    )
    mock_exec = AsyncMock(return_value=_fake_proc(out))
    with patch("asyncio.create_subprocess_exec", new=mock_exec):
        res = await _execute_tool_step(step, _ctx(product_photo="/photos/bottle.png"))
    assert res.status == "completed"
    # The dict arg is JSON-encoded with the template resolved before spawning.
    passed_args = mock_exec.call_args.args
    assert any("/photos/bottle.png" in a for a in passed_args if isinstance(a, str))


@pytest.mark.asyncio
async def test_tool_step_missing_config_fails():
    res = await _execute_tool_step(_step(tool_config=None), _ctx())
    assert res.status == "failed"
    assert "missing tool_config" in res.error.lower()


@pytest.mark.asyncio
async def test_tool_step_unknown_tool_fails():
    step = _step(tool_config=ToolConfig(tool="does-not-exist", function="generate"))
    res = await _execute_tool_step(step, _ctx())
    assert res.status == "failed"
    assert "does-not-exist" in res.error


@pytest.mark.asyncio
async def test_tool_step_nonzero_exit_fails():
    step = _step(
        tool_config=ToolConfig(tool="nano-banana", function="generate", arguments=["p"])
    )
    proc = _fake_proc("", stderr="connector boom", returncode=1)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        res = await _execute_tool_step(step, _ctx())
    assert res.status == "failed"
    assert "boom" in res.error
