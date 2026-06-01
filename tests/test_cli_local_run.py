"""Tests for `sandcastle run --local` - in-process execution without a server.

The local path fixes the first-run experience: a fresh `pip install` could not run
anything because `sandcastle run` is an HTTP client that needs `sandcastle serve`
first. `--local` parses the workflow and drives the engine executor directly.
"""

from __future__ import annotations

import pytest

from sandcastle.__main__ import _run_local

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


def test_run_local_resolves_builtin_template_name(tmp_path, capsys):
    """A bare built-in template name resolves (does not error as 'not found'). It may
    fail later for lack of provider keys, but resolution + planning must succeed."""
    # 'summarize' is a built-in template; with no key it will fail at the LLM step,
    # which exits 2 (run failed) - never the 'not found' path (exit 1).
    try:
        _run_local("summarize", {"text": "hello"}, None)
    except SystemExit as exc:
        assert exc.code in (0, 2), f"unexpected exit code {exc.code}"
    combined = capsys.readouterr()
    assert "not found" not in (combined.out + combined.err)
