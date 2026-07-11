"""Tests for out-of-process execution of workflow ``code`` steps.

These exercise ``code_subprocess_runner.run_code_in_subprocess`` directly (the
child really is a separate Python process) plus the ``_execute_code_step``
integration that dispatches to it.
"""

from __future__ import annotations

import asyncio

import pytest

from sandcastle.engine.code_subprocess_runner import (
    SubprocessInfraError,
    run_code_in_subprocess,
)


def _run(coro):
    return asyncio.run(coro)


class TestRunCodeInSubprocess:
    """Direct exercise of the subprocess runner."""

    def test_normal_json_transform(self, tmp_path) -> None:
        res = _run(
            run_code_in_subprocess(
                code="result = sum(_input['items']) + _steps['prev']['n']",
                input_data={"items": [1, 2, 3]},
                step_outputs={"prev": {"n": 10}},
                data_dir=str(tmp_path),
                timeout=15,
            )
        )
        assert res["status"] == "completed"
        assert res["output"] == 16

    def test_blocklist_violation_rejected(self, tmp_path) -> None:
        res = _run(
            run_code_in_subprocess(
                code="result = eval('1+1')",
                input_data={},
                step_outputs={},
                data_dir=str(tmp_path),
                timeout=15,
            )
        )
        assert res["status"] == "failed"
        assert "blocked pattern" in res["error"].lower()

    def test_import_rejected_by_ast(self, tmp_path) -> None:
        res = _run(
            run_code_in_subprocess(
                code="import os\nresult = 1",
                input_data={},
                step_outputs={},
                data_dir=str(tmp_path),
                timeout=15,
            )
        )
        assert res["status"] == "failed"
        assert "import" in res["error"].lower()

    def test_child_has_no_parent_settings(self, tmp_path) -> None:
        # The parent's `settings` object must not exist in the child globals.
        res = _run(
            run_code_in_subprocess(
                code="result = settings",
                input_data={},
                step_outputs={},
                data_dir=str(tmp_path),
                timeout=15,
            )
        )
        assert res["status"] == "failed"
        assert "settings" in res["error"]

    def test_timeout_kills_child(self, tmp_path) -> None:
        res = _run(
            run_code_in_subprocess(
                code="while True:\n    x = 1",
                input_data={},
                step_outputs={},
                data_dir=str(tmp_path),
                timeout=2,
            )
        )
        assert res["status"] == "failed"
        assert "timed out" in res["error"].lower()

    def test_read_file_b64_helper_works(self, tmp_path) -> None:
        src = tmp_path / "note.txt"
        src.write_text("hello")
        res = _run(
            run_code_in_subprocess(
                code=f"result = read_file_b64({str(src)!r})",
                input_data={},
                step_outputs={},
                data_dir=str(tmp_path),
                timeout=15,
            )
        )
        assert res["status"] == "completed"
        # base64 of "hello"
        assert res["output"] == "aGVsbG8="

    def test_save_file_b64_helper_works(self, tmp_path) -> None:
        src = tmp_path / "big.bin"
        src.write_bytes(b"data")
        res = _run(
            run_code_in_subprocess(
                code=f"result = save_file_b64({str(src)!r}, 'out.txt')",
                input_data={},
                step_outputs={},
                data_dir=str(tmp_path),
                timeout=15,
            )
        )
        assert res["status"] == "completed"
        assert res["output"].startswith("@file:")
        saved = tmp_path / "tmp" / "out.txt"
        assert saved.exists()

    def test_non_serializable_input_raises_infra_error(self, tmp_path) -> None:
        # A non-JSON-serializable input signals an infra failure so the caller
        # can fall back to the in-process path.
        with pytest.raises(SubprocessInfraError):
            _run(
                run_code_in_subprocess(
                    code="result = 1",
                    input_data={"bad": object()},
                    step_outputs={},
                    data_dir=str(tmp_path),
                    timeout=15,
                )
            )


class TestExecuteCodeStepIntegration:
    """The executor dispatches code steps out-of-process by default."""

    @pytest.mark.asyncio
    async def test_code_step_runs_out_of_process(self, monkeypatch) -> None:
        from sandcastle.config import settings
        from sandcastle.engine.dag import CodeConfig, StepDefinition
        from sandcastle.engine.executor import RunContext, _execute_code_step

        # The suite defaults code steps to in-process (conftest); force the
        # out-of-process path here so this integration test actually exercises it.
        monkeypatch.setattr(settings, "code_steps_out_of_process", True)

        step = StepDefinition(
            id="t",
            type="code",
            code_config=CodeConfig(code="result = len(_input['text'])"),
        )
        ctx = RunContext(
            run_id="r1",
            input={"text": "hello"},
            step_outputs={},
            admin_trusted=True,
        )
        result = await _execute_code_step(step, ctx)
        assert result.status == "completed"
        assert result.output == 5
        assert result.cost_usd == 0.0
