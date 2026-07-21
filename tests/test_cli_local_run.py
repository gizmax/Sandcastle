"""Tests for `sandcastle run --local` - in-process execution without a server.

The local path fixes the first-run experience: a fresh `pip install` could not run
anything because `sandcastle run` is an HTTP client that needs `sandcastle serve`
first. `--local` parses the workflow and drives the engine executor directly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from sandcastle.__main__ import _run_local
from sandcastle.engine.executor import WorkflowResult

CODE_ONLY_WORKFLOW = """name: cli-local-test
description: A code-only workflow that runs with no server and no API keys.
default_model: sonnet
input_schema:
  required: []
  properties:
    who: { type: string, default: "world" }
steps:
  - id: greet
    type: code
    code_config:
      code: |
        result = {"msg": "hi " + str(_input.get("who", "world")), "n": 6 * 7}
"""


def test_run_local_executes_code_only_workflow(tmp_path, capsys):
    """A code-only workflow runs in-process and prints its output - no server, no keys."""
    wf = tmp_path / "wf.yaml"
    wf.write_text(CODE_ONLY_WORKFLOW)
    _run_local(str(wf), {"who": "tester"}, None)
    out = capsys.readouterr().out
    assert "completed" in out
    assert "hi tester" in out
    assert "42" in out  # 6 * 7, proving the code step actually executed


def test_run_local_unknown_workflow_exits_cleanly(capsys):
    """An unknown name (not a file, not a built-in template) exits 1 with a clear error."""
    with pytest.raises(SystemExit) as exc_info:
        _run_local("/nonexistent/path/nope.yaml", {}, None)
    assert exc_info.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_run_local_resolves_builtin_template_name(capsys):
    """A bare built-in template name resolves and is passed to the executor."""
    from sandcastle.templates import get_template

    result = WorkflowResult(
        run_id="mock-run",
        outputs={"summary": "mocked"},
        total_cost_usd=0.0,
        status="completed",
    )
    with (
        patch("sandcastle.templates.get_template", wraps=get_template) as get_template_mock,
        patch(
            "sandcastle.models.db.init_db",
            new_callable=AsyncMock,
            side_effect=RuntimeError("database disabled for this unit test"),
        ),
        patch(
            "sandcastle.engine.executor.execute_workflow",
            new_callable=AsyncMock,
            return_value=result,
        ) as execute_workflow_mock,
    ):
        _run_local("summarize", {"text": "hello"}, None)

    get_template_mock.assert_called_once_with("summarize")
    execute_workflow_mock.assert_awaited_once()
    execution_args = execute_workflow_mock.await_args.kwargs
    assert execution_args["workflow"].name == "text-summarizer"
    assert execution_args["plan"].stages
    assert execution_args["input_data"] == {"text": "hello"}
    output = capsys.readouterr().out
    assert "completed" in output
    assert "mocked" in output


# ---------------------------------------------------------------------------
# Audit sweep 0.40.2 - CLI regression tests
# ---------------------------------------------------------------------------


def test_run_missing_yaml_reports_file_not_found(capsys):
    """Fix 7: a missing .yaml arg on the remote path fails clearly, not as a 404."""
    import argparse

    from sandcastle.__main__ import _cmd_run

    args = argparse.Namespace(
        workflow="/nonexistent/does_not_exist.yaml",
        max_cost=None,
        local=False,
        record=None,
        replay=None,
    )
    with pytest.raises(SystemExit) as exc_info:
        _cmd_run(args)
    assert exc_info.value.code == 1
    assert "file not found" in capsys.readouterr().err


def test_parse_ollama_target_defaults():
    """Fix 6: unset OLLAMA_HOST falls back to localhost:11434."""
    from sandcastle.__main__ import _parse_ollama_target

    assert _parse_ollama_target(None) == ("localhost", 11434)
    assert _parse_ollama_target("") == ("localhost", 11434)


def test_parse_ollama_target_respects_docker_host():
    """Fix 6: OLLAMA_HOST with scheme and port is parsed into host/port."""
    from sandcastle.__main__ import _parse_ollama_target

    assert _parse_ollama_target("http://host.docker.internal:11434") == (
        "host.docker.internal",
        11434,
    )
    assert _parse_ollama_target("host.docker.internal:9999") == (
        "host.docker.internal",
        9999,
    )
    assert _parse_ollama_target("http://myhost") == ("myhost", 11434)
