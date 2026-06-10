"""Tests for The Architect - the autonomous generate -> run -> evaluate -> refine loop.

The LLM surfaces (generator, judge) are mocked; the runs are real executor runs
against a mocked provider runtime, so the recorded cassette and the packed
.sctpl bundle are genuine - the success test proves the bundle with the same
``verify_bundle`` that gates template installs.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from sandcastle.engine.architect import (
    ArchitectResult,
    _derive_test_input,
    design_workflow,
)
from sandcastle.engine.bundle import verify_bundle
from sandcastle.engine.dag import parse_yaml_string
from sandcastle.engine.generator import GenerateResult
from sandcastle.engine.sandshore import SandshoreResult, SandshoreRuntime
from sandcastle.main import app

client = TestClient(app)

GEN_PATCH = "sandcastle.engine.generator.generate_workflow"
JUDGE_PATCH = "sandcastle.engine.architect._judge_output"
RUNTIME_PATCH = "sandcastle.engine.executor.get_sandshore_runtime"


def _wf(marker: str, name: str = "architect-test") -> str:
    """A single standard-step workflow; *marker* keeps step cache keys unique."""
    return f"""name: {name}
description: Summarize the given text.
default_model: sonnet
input_schema:
  required: []
  properties:
    q: {{ type: string, default: "hi" }}
steps:
  - id: summarize
    prompt: "Summarize ({marker}): {{input.q}}"
"""


def _gen_result(yaml_text: str, validation_errors: list[str] | None = None) -> GenerateResult:
    """Build a GenerateResult the way the real generator would for valid YAML."""
    if validation_errors:
        return GenerateResult(yaml_content=yaml_text, validation_errors=validation_errors)
    wf = parse_yaml_string(yaml_text)
    return GenerateResult(
        yaml_content=yaml_text,
        name=wf.name,
        description=wf.description,
        steps_count=len(wf.steps),
        validation_errors=[],
        input_schema=wf.input_schema,
    )


def _sandbox(text: str, cost: float = 0.02) -> MagicMock:
    """A mock SandshoreRuntime whose query returns a fixed result."""
    sb = MagicMock(spec=SandshoreRuntime)

    async def _query(request):
        return SandshoreResult(
            text=text, structured_output=None, total_cost_usd=cost,
            input_tokens=5, output_tokens=5,
        )

    sb.query = _query
    return sb


def _unique_input() -> dict:
    """Unique test input so the shared DB step cache never short-circuits a run."""
    return {"q": f"hi-{uuid.uuid4().hex}"}


def _design(description: str = "Summarize text", **kwargs) -> ArchitectResult:
    return asyncio.run(design_workflow(description, **kwargs))


# ---------------------------------------------------------------------------
# the loop
# ---------------------------------------------------------------------------


def test_success_on_first_iteration_produces_verified_bundle(tmp_path):
    """One good generation -> run -> judge pass yields a proven, installed bundle
    whose proof passes the same verify_bundle used by template install."""
    yaml_text = _wf(uuid.uuid4().hex)
    gen = AsyncMock(return_value=_gen_result(yaml_text))
    judge = AsyncMock(return_value=(0.9, "judge scored 0.90"))

    with patch(GEN_PATCH, gen), patch(JUDGE_PATCH, judge), \
            patch(RUNTIME_PATCH, return_value=_sandbox("A FINE SUMMARY")):
        result = _design(
            test_input=_unique_input(),
            output_dir=tmp_path / "out",
            install_dir=tmp_path / "installed",
        )

    assert result.status == "proven"
    assert result.proven is True
    assert len(result.iterations) == 1
    it = result.iterations[0]
    assert it.run_status == "completed"
    assert it.judge_score == 0.9
    assert it.hard_check_failures == []
    assert result.total_cost_usd == pytest.approx(0.02)
    gen.assert_awaited_once()

    # The bundle is real and passes the install-gating verification.
    assert result.bundle_path and Path(result.bundle_path).exists()
    verify = verify_bundle(result.bundle_path)
    assert verify.ok, f"{verify.errors} {[c.detail for c in verify.cassette_results]}"
    assert verify.manifest["author"] == "the-architect"

    # Installed like `template install`: workflow + proof cassette side by side.
    assert result.installed_path and Path(result.installed_path).exists()
    assert Path(result.installed_path).read_text() == yaml_text
    assert list((tmp_path / "installed").glob("*.cassette.json"))


def test_refine_then_succeed(tmp_path):
    """A low judge score feeds back into the generator's refine path; the
    refined workflow passes on the second iteration."""
    yaml_v1 = _wf(uuid.uuid4().hex)
    yaml_v2 = _wf(uuid.uuid4().hex)
    gen = AsyncMock(side_effect=[_gen_result(yaml_v1), _gen_result(yaml_v2)])
    judge = AsyncMock(side_effect=[(0.3, "weak"), (0.95, "strong")])

    with patch(GEN_PATCH, gen), patch(JUDGE_PATCH, judge), \
            patch(RUNTIME_PATCH, return_value=_sandbox("BETTER SUMMARY")):
        result = _design(
            test_input=_unique_input(),
            output_dir=tmp_path / "out",
            install=False,
        )

    assert result.status == "proven"
    assert result.proven is True
    assert len(result.iterations) == 2
    assert result.iterations[0].judge_score == 0.3
    assert result.iterations[1].judge_score == 0.95
    assert result.yaml_content == yaml_v2

    # Second generation went through refine_from/refine_instruction.
    assert gen.await_count == 2
    refine_kwargs = gen.await_args_list[1].kwargs
    assert refine_kwargs["refine_from"] == yaml_v1
    assert "0.30" in refine_kwargs["refine_instruction"]
    # The judge feedback for the next pass is recorded in the iteration log.
    assert result.iterations[1].refinement_note


def test_validation_errors_consume_an_iteration_and_feed_back(tmp_path):
    """A generation with validation errors is never run; the errors become the
    refine instruction for the next iteration."""
    good_yaml = _wf(uuid.uuid4().hex)
    gen = AsyncMock(side_effect=[
        _gen_result("name: broken", validation_errors=["step list is empty"]),
        _gen_result(good_yaml),
    ])
    judge = AsyncMock(return_value=(0.9, "ok"))

    with patch(GEN_PATCH, gen), patch(JUDGE_PATCH, judge), \
            patch(RUNTIME_PATCH, return_value=_sandbox("OUT")):
        result = _design(
            test_input=_unique_input(),
            output_dir=tmp_path / "out",
            install=False,
        )

    assert result.proven is True
    assert len(result.iterations) == 2
    assert result.iterations[0].validation_errors == ["step list is empty"]
    assert result.iterations[0].run_status == "skipped (validation errors)"
    assert "step list is empty" in gen.await_args_list[1].kwargs["refine_instruction"]


def test_budget_exceeded_aborts_cleanly(tmp_path):
    """When live-run spend reaches the budget the loop stops with
    budget_exceeded instead of burning further iterations."""
    gen = AsyncMock(side_effect=lambda *a, **kw: _gen_result(_wf(uuid.uuid4().hex)))
    judge = AsyncMock(return_value=(0.1, "bad"))  # never passes the threshold

    with patch(GEN_PATCH, gen), patch(JUDGE_PATCH, judge), \
            patch(RUNTIME_PATCH, return_value=_sandbox("OUT", cost=0.02)):
        result = _design(
            test_input=_unique_input(),
            budget_usd=0.03,
            max_iterations=5,
            output_dir=tmp_path / "out",
            install=False,
        )

    assert result.status == "budget_exceeded"
    assert result.proven is False
    assert "budget" in (result.error or "")
    assert len(result.iterations) < 5
    assert result.total_cost_usd <= 0.03 + 0.02  # never overshoots by more than one run


def test_max_iterations_exhausted_returns_best_effort_unproven(tmp_path):
    """Exhausting the loop returns proven=False with the best-scoring YAML."""
    yaml_v1 = _wf(uuid.uuid4().hex)
    yaml_v2 = _wf(uuid.uuid4().hex)
    gen = AsyncMock(side_effect=[_gen_result(yaml_v1), _gen_result(yaml_v2)])
    judge = AsyncMock(side_effect=[(0.4, "meh"), (0.2, "worse")])

    with patch(GEN_PATCH, gen), patch(JUDGE_PATCH, judge), \
            patch(RUNTIME_PATCH, return_value=_sandbox("OUT")):
        result = _design(
            test_input=_unique_input(),
            max_iterations=2,
            output_dir=tmp_path / "out",
            install=False,
        )

    assert result.status == "max_iterations"
    assert result.proven is False
    assert result.bundle_path is None
    assert len(result.iterations) == 2
    assert result.best_score == 0.4
    assert result.yaml_content == yaml_v1  # the best attempt, not the last one


def test_failed_run_counts_as_hard_check_failure(tmp_path):
    """A run that crashes/fails never reaches the judge and feeds the failure back."""
    gen = AsyncMock(side_effect=lambda *a, **kw: _gen_result(_wf(uuid.uuid4().hex)))
    judge = AsyncMock(return_value=(0.9, "ok"))
    sb = MagicMock(spec=SandshoreRuntime)

    async def _boom(request):
        raise RuntimeError("provider exploded")

    sb.query = _boom
    with patch(GEN_PATCH, gen), patch(JUDGE_PATCH, judge), \
            patch(RUNTIME_PATCH, return_value=sb):
        result = _design(
            test_input=_unique_input(),
            max_iterations=2,
            output_dir=tmp_path / "out",
            install=False,
        )

    assert result.proven is False
    assert result.status == "max_iterations"
    judge.assert_not_awaited()
    assert all(it.hard_check_failures for it in result.iterations)


# ---------------------------------------------------------------------------
# test input derivation
# ---------------------------------------------------------------------------


def test_derive_test_input_uses_schema_defaults_without_llm():
    schema = {"properties": {"q": {"type": "string", "default": "hello"}}}
    with patch(
        "sandcastle.engine.generator._call_advisor_llm",
        AsyncMock(side_effect=AssertionError("LLM must not be called")),
    ):
        out = asyncio.run(_derive_test_input("anything", schema))
    assert out == {"q": "hello"}


def test_derive_test_input_falls_back_to_placeholders_when_llm_fails():
    schema = {"properties": {"text": {"type": "string"}, "count": {"type": "integer"}}}
    with patch(
        "sandcastle.engine.generator._call_advisor_llm",
        AsyncMock(side_effect=RuntimeError("offline")),
    ):
        out = asyncio.run(_derive_test_input("anything", schema))
    assert out == {"text": "example", "count": 1}


def test_derive_test_input_uses_llm_for_missing_values():
    schema = {"properties": {"city": {"type": "string"}}}
    with patch(
        "sandcastle.engine.generator._call_advisor_llm",
        AsyncMock(return_value='{"city": "Prague"}'),
    ):
        out = asyncio.run(_derive_test_input("weather report", schema))
    assert out == {"city": "Prague"}


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def test_architect_settings_defaults():
    from sandcastle.config import settings

    assert settings.architect_max_iterations == 3
    assert settings.architect_budget_usd == 1.0
    assert settings.architect_score_threshold == 0.7


# ---------------------------------------------------------------------------
# API job lifecycle
# ---------------------------------------------------------------------------


def _fake_result() -> ArchitectResult:
    return ArchitectResult(
        status="proven", proven=True, description="d",
        bundle_path="/tmp/x.sctpl", template_name="x",
    )


class TestArchitectApi:
    def test_post_requires_api_key(self):
        from sandcastle.config import settings

        with patch.object(settings, "anthropic_api_key", ""), \
                patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            resp = client.post("/api/architect", json={"description": "Build me a thing"})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "MISSING_API_KEY"

    def test_post_starts_job_and_get_returns_it(self):
        from sandcastle.config import settings

        with patch.object(settings, "anthropic_api_key", "sk-test"), \
                patch(
                    "sandcastle.engine.architect.design_workflow",
                    AsyncMock(return_value=_fake_result()),
                ):
            resp = client.post("/api/architect", json={"description": "Summarize text"})
            assert resp.status_code == 200
            job_id = resp.json()["data"]["job_id"]
            assert resp.json()["data"]["status"] == "queued"

            poll = client.get(f"/api/architect/{job_id}")
        assert poll.status_code == 200
        job = poll.json()["data"]
        assert job["job_id"] == job_id
        assert job["status"] in ("queued", "running", "completed")
        assert job["description"] == "Summarize text"
        assert "log" in job and "result" in job

    def test_get_unknown_job_404(self):
        resp = client.get(f"/api/architect/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_job_runner_completes_and_stores_result(self):
        """The job coroutine transitions queued -> completed with the result dict."""
        from sandcastle.api.routes import _architect_jobs, _run_architect_job
        from sandcastle.api.schemas import ArchitectRequest

        job_id = f"test-{uuid.uuid4().hex}"
        _architect_jobs[job_id] = {
            "job_id": job_id, "status": "queued", "description": "d",
            "created_at": "now", "completed_at": None,
            "log": [], "result": None, "error": None,
        }
        try:
            with patch(
                "sandcastle.engine.architect.design_workflow",
                AsyncMock(return_value=_fake_result()),
            ):
                asyncio.run(_run_architect_job(job_id, ArchitectRequest(description="d")))
            job = _architect_jobs[job_id]
            assert job["status"] == "completed"
            assert job["result"]["proven"] is True
            assert job["result"]["bundle_path"] == "/tmp/x.sctpl"
            assert job["completed_at"]
        finally:
            _architect_jobs.pop(job_id, None)

    def test_job_runner_records_failure(self):
        from sandcastle.api.routes import _architect_jobs, _run_architect_job
        from sandcastle.api.schemas import ArchitectRequest

        job_id = f"test-{uuid.uuid4().hex}"
        _architect_jobs[job_id] = {
            "job_id": job_id, "status": "queued", "description": "d",
            "created_at": "now", "completed_at": None,
            "log": [], "result": None, "error": None,
        }
        try:
            with patch(
                "sandcastle.engine.architect.design_workflow",
                AsyncMock(side_effect=RuntimeError("kaboom")),
            ):
                asyncio.run(_run_architect_job(job_id, ArchitectRequest(description="d")))
            job = _architect_jobs[job_id]
            assert job["status"] == "failed"
            assert "kaboom" in job["error"]
        finally:
            _architect_jobs.pop(job_id, None)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestArchitectCli:
    def test_parser_accepts_architect_args(self):
        from sandcastle.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args(
            ["architect", "Summarize my emails", "-i", "q=hi",
             "--budget", "0.5", "--max-iterations", "2", "--threshold", "0.8"]
        )
        assert args.command == "architect"
        assert args.description == "Summarize my emails"
        assert args.input == ["q=hi"]
        assert args.budget == 0.5
        assert args.max_iterations == 2
        assert args.threshold == 0.8

    def test_cli_exits_zero_with_bundle_path_on_success(self, capsys):
        import argparse

        from sandcastle.__main__ import _cmd_architect

        args = argparse.Namespace(
            description="Summarize text", input=None, input_file=None,
            budget=None, max_iterations=None, threshold=None,
            output=None, no_install=False, json=False,
        )
        with patch(
            "sandcastle.engine.architect.design_workflow",
            AsyncMock(return_value=_fake_result()),
        ):
            _cmd_architect(args)  # must not raise SystemExit
        out = capsys.readouterr().out
        assert "PROVEN" in out
        assert "/tmp/x.sctpl" in out

    def test_cli_exits_2_when_not_proven(self, capsys):
        import argparse

        from sandcastle.__main__ import _cmd_architect

        unproven = ArchitectResult(
            status="max_iterations", proven=False, description="d",
            error="no iteration reached the threshold",
        )
        args = argparse.Namespace(
            description="Summarize text", input=None, input_file=None,
            budget=None, max_iterations=None, threshold=None,
            output=None, no_install=False, json=False,
        )
        with patch(
            "sandcastle.engine.architect.design_workflow",
            AsyncMock(return_value=unproven),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _cmd_architect(args)
        assert exc_info.value.code == 2
        assert "NOT PROVEN" in capsys.readouterr().err
