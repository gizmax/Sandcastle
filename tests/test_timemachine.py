"""Tests for the Model Time Machine - counterfactual replay against a different model.

Covers: dry-run pricing math against hand-computed fixtures, cassette selection
filters, the explicit-budget contract for live replays (required + pre-flight
refusal), live-path aggregation with mocked model/judge calls, and the async
job lifecycle over the API.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from sandcastle.engine import timemachine as tm
from sandcastle.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wf_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _seed_run(
    workflow_name: str,
    steps: list[dict],
    created_at: datetime | None = None,
) -> str:
    """Insert a completed Run with completed RunSteps and return the run id."""
    from sandcastle.models.db import Run, RunStatus, RunStep, StepStatus, async_session

    run_id = uuid.uuid4()
    async with async_session() as session:
        session.add(
            Run(
                id=run_id,
                workflow_name=workflow_name,
                status=RunStatus.COMPLETED,
                total_cost_usd=sum(s.get("cost_usd", 0.0) for s in steps),
                created_at=created_at or datetime.now(timezone.utc),
            )
        )
        for i, s in enumerate(steps):
            session.add(
                RunStep(
                    run_id=run_id,
                    step_id=s.get("step_id", f"step{i}"),
                    status=StepStatus.COMPLETED,
                    input_prompt=s.get("prompt"),
                    output_data={"text": s.get("output", "")},
                    cost_usd=s.get("cost_usd", 0.0),
                    duration_seconds=s.get("duration", 1.0),
                    model=s.get("model"),
                    started_at=datetime.now(timezone.utc) + timedelta(seconds=i),
                )
            )
        await session.commit()
    return str(run_id)


# ---------------------------------------------------------------------------
# Dry-run pricing math (hand-computed fixture)
# ---------------------------------------------------------------------------


class TestEstimateMath:
    def test_estimate_tokens_chars_per_token(self):
        assert tm.estimate_tokens("") == 0
        assert tm.estimate_tokens("ab") == 1  # minimum 1 for non-empty
        assert tm.estimate_tokens("x" * 400) == 100

    def test_projected_cost_hand_computed(self):
        # 4000 chars prompt -> 1000 input tokens; 8000 chars output -> 2000 output
        # tokens. haiku pricing: $0.80/M input, $4.00/M output.
        cas = [
            tm.RunCassette(
                run_id="r1",
                workflow_name="wf",
                created_at=datetime.now(timezone.utc),
                steps=[tm.RecordedStep("s1", "sonnet", "x" * 4000, "y" * 8000, 0.05, 2.0)],
            )
        ]
        est = tm.estimate_replay_cost(cas, "haiku")
        assert est["input_tokens"] == 1000
        assert est["output_tokens"] == 2000
        expected = (1000 * 0.80 + 2000 * 4.0) / 1_000_000
        assert est["projected_cost_usd"] == pytest.approx(expected)
        assert est["original_cost_usd"] == pytest.approx(0.05)
        # No judge model -> no judge cost, total == target cost
        assert est["projected_judge_cost_usd"] == 0.0
        assert est["projected_total_live_cost_usd"] == pytest.approx(expected)

    def test_judge_cost_included_for_live_estimates(self):
        cas = [
            tm.RunCassette(
                run_id="r1",
                workflow_name="wf",
                created_at=None,
                steps=[tm.RecordedStep("s1", "sonnet", "x" * 400, "y" * 400, 0.01, 1.0)],
            )
        ]
        est = tm.estimate_replay_cost(cas, "haiku", judge_model="haiku")
        assert est["projected_judge_cost_usd"] > 0
        assert est["projected_total_live_cost_usd"] == pytest.approx(
            est["projected_cost_usd"] + est["projected_judge_cost_usd"]
        )

    def test_local_model_projects_to_zero(self):
        # nim/* resolves with region=local and $0 pricing.
        cas = [
            tm.RunCassette(
                run_id="r1",
                workflow_name="wf",
                created_at=None,
                steps=[tm.RecordedStep("s1", "opus", "p" * 4000, "o" * 4000, 1.25, 3.0)],
            )
        ]
        est = tm.estimate_replay_cost(cas, "nim/llama-3.1-70b")
        assert est["projected_cost_usd"] == 0.0
        assert est["original_cost_usd"] == pytest.approx(1.25)


class TestParseSince:
    def test_relative_windows(self):
        now = datetime.now(timezone.utc)
        assert (now - tm.parse_since("30d")).days in (29, 30)
        assert abs((now - tm.parse_since("12h")).total_seconds() - 43200) < 5
        assert (now - tm.parse_since("2w")).days in (13, 14)

    def test_iso_date(self):
        dt = tm.parse_since("2026-01-15")
        assert dt == datetime(2026, 1, 15, tzinfo=timezone.utc)

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            tm.parse_since("next tuesday")


# ---------------------------------------------------------------------------
# Cassette selection
# ---------------------------------------------------------------------------


class TestSelectCassettes:
    async def test_selects_only_model_steps_of_completed_runs(self):
        wf = _wf_name("tm-select")
        await _seed_run(
            wf,
            [
                {"step_id": "llm", "model": "sonnet", "prompt": "p", "output": "o",
                 "cost_usd": 0.01},
                {"step_id": "code", "model": None, "prompt": None, "output": "x"},
            ],
        )
        cassettes = await tm.select_cassettes(workflow=wf)
        assert len(cassettes) == 1
        assert [s.step_id for s in cassettes[0].steps] == ["llm"]
        assert cassettes[0].steps[0].output_text == '{"text": "o"}'

    async def test_workflow_filter_and_cap(self):
        wf_a, wf_b = _wf_name("tm-a"), _wf_name("tm-b")
        for _ in range(3):
            await _seed_run(wf_a, [{"model": "haiku", "prompt": "p", "output": "o"}])
        await _seed_run(wf_b, [{"model": "haiku", "prompt": "p", "output": "o"}])
        assert len(await tm.select_cassettes(workflow=wf_a)) == 3
        assert len(await tm.select_cassettes(workflow=wf_a, max_cassettes=2)) == 2
        assert len(await tm.select_cassettes(workflow=wf_b)) == 1

    async def test_date_range_filter(self):
        wf = _wf_name("tm-dates")
        old = datetime.now(timezone.utc) - timedelta(days=60)
        await _seed_run(wf, [{"model": "haiku", "prompt": "p", "output": "o"}], created_at=old)
        await _seed_run(wf, [{"model": "haiku", "prompt": "p", "output": "o"}])
        since = datetime.now(timezone.utc) - timedelta(days=30)
        assert len(await tm.select_cassettes(workflow=wf)) == 2
        assert len(await tm.select_cassettes(workflow=wf, since=since)) == 1


# ---------------------------------------------------------------------------
# Dry-run report (default mode - no API calls)
# ---------------------------------------------------------------------------


class TestDryRun:
    async def test_dry_run_report_math_and_shape(self):
        wf = _wf_name("tm-dry")
        await _seed_run(
            wf,
            [{"model": "sonnet", "prompt": "x" * 4000, "output": "y" * 8000,
              "cost_usd": 0.10, "duration": 2.0}],
        )
        report = await tm.run_time_machine("haiku", workflow=wf)
        assert report["mode"] == "dry_run"
        assert report["quality"] is None and report["live"] is None
        assert report["selection"]["runs"] == 1
        # Output JSON-serialized adds {"text": ...} wrapper: 8000 + 12 chars -> 2003 tokens
        out_tokens = (8000 + len('{"text": ""}')) // 4
        expected = (1000 * 0.80 + out_tokens * 4.0) / 1_000_000
        assert report["cost"]["new_usd"] == pytest.approx(expected, rel=1e-6)
        assert report["cost"]["original_usd"] == pytest.approx(0.10)
        assert report["cost"]["delta_usd"] < 0
        assert len(report["per_workflow"]) == 1
        row = report["per_workflow"][0]
        assert row["workflow"] == wf and row["steps"] == 1
        assert "projected" in report["verdict"]
        assert report["extrapolation"]["monthly_savings_usd"] > 0

    async def test_dry_run_empty_selection(self):
        report = await tm.run_time_machine("haiku", workflow=_wf_name("tm-none"))
        assert report["selection"]["runs"] == 0
        assert report["cost"]["original_usd"] == 0.0
        assert report["per_workflow"] == []

    async def test_unknown_model_raises(self):
        with pytest.raises(KeyError):
            await tm.run_time_machine("gpt-42-ultra", workflow="whatever")


# ---------------------------------------------------------------------------
# Budget contract for live replays
# ---------------------------------------------------------------------------


class TestBudget:
    async def test_live_requires_budget(self):
        with pytest.raises(tm.BudgetRequiredError):
            await tm.run_time_machine("haiku", workflow=_wf_name("x"), live=True)

    async def test_live_refuses_when_estimate_exceeds_budget(self):
        wf = _wf_name("tm-budget")
        await _seed_run(
            wf,
            [{"model": "sonnet", "prompt": "x" * 400000, "output": "y" * 400000,
              "cost_usd": 2.0}],
        )
        # opus replay of 100k in + 100k out tokens >> $0.000001
        with pytest.raises(tm.BudgetExceededError):
            await tm.run_time_machine("opus", workflow=wf, live=True, budget_usd=0.000001)


# ---------------------------------------------------------------------------
# Live replay aggregation (mocked model + judge)
# ---------------------------------------------------------------------------


class TestLiveAggregation:
    async def test_live_aggregates_cost_quality_latency(self, monkeypatch):
        wf = _wf_name("tm-live")
        await _seed_run(
            wf,
            [
                {"step_id": "a", "model": "sonnet", "prompt": "p1", "output": "o1",
                 "cost_usd": 0.10, "duration": 4.0},
                {"step_id": "b", "model": "sonnet", "prompt": "p2", "output": "o2",
                 "cost_usd": 0.30, "duration": 6.0},
            ],
        )

        async def fake_call_model(model_str, prompt, max_tokens=4096, system=None, timeout=300.0):
            return {
                "text": f"new output for {prompt}",
                "input_tokens": 100,
                "output_tokens": 50,
                "cost_usd": 0.02,
                "latency_seconds": 1.0,
            }

        async def fake_judge(judge_model, prompt, old_output, new_output):
            return {"score_old": 8.0, "score_new": 6.0, "cost_usd": 0.001}

        monkeypatch.setattr(tm, "call_model", fake_call_model)
        monkeypatch.setattr(tm, "judge_outputs", fake_judge)

        report = await tm.run_time_machine("haiku", workflow=wf, live=True, budget_usd=10.0)

        assert report["mode"] == "live"
        live = report["live"]
        assert live["steps_replayed"] == 2 and live["steps_failed"] == 0
        assert live["truncated"] is False
        # measured = 2 replay calls + 2 judge calls
        assert live["measured_cost_usd"] == pytest.approx(2 * 0.02 + 2 * 0.001)
        # new cost excludes judge overhead; original 0.40 -> 0.04
        assert report["cost"]["new_usd"] == pytest.approx(0.04)
        assert report["cost"]["original_usd"] == pytest.approx(0.40)
        # quality: 8.0 -> 6.0 = -25%
        assert report["quality"]["old_avg"] == pytest.approx(8.0)
        assert report["quality"]["new_avg"] == pytest.approx(6.0)
        assert report["quality"]["delta_pct"] == pytest.approx(-25.0)
        # latency: avg 5.0s -> 1.0s = -80%
        assert report["latency"]["old_avg_seconds"] == pytest.approx(5.0)
        assert report["latency"]["new_avg_seconds"] == pytest.approx(1.0)
        assert report["latency"]["delta_pct"] == pytest.approx(-80.0)
        row = report["per_workflow"][0]
        assert row["quality_delta_pct"] == pytest.approx(-25.0)
        assert "haiku" in report["verdict"]

    async def test_live_step_failure_is_non_fatal(self, monkeypatch):
        wf = _wf_name("tm-fail")
        await _seed_run(
            wf,
            [
                {"step_id": "ok", "model": "sonnet", "prompt": "p1", "output": "o1",
                 "cost_usd": 0.1},
                {"step_id": "boom", "model": "sonnet", "prompt": "p2", "output": "o2",
                 "cost_usd": 0.1},
            ],
        )
        calls = {"n": 0}

        async def flaky_call(model_str, prompt, max_tokens=4096, system=None, timeout=300.0):
            calls["n"] += 1
            if prompt == "p2":
                raise RuntimeError("provider 500")
            return {"text": "ok", "input_tokens": 10, "output_tokens": 10,
                    "cost_usd": 0.01, "latency_seconds": 0.5}

        async def fake_judge(judge_model, prompt, old_output, new_output):
            return {"score_old": 7.0, "score_new": 7.0, "cost_usd": 0.0}

        monkeypatch.setattr(tm, "call_model", flaky_call)
        monkeypatch.setattr(tm, "judge_outputs", fake_judge)

        report = await tm.run_time_machine("haiku", workflow=wf, live=True, budget_usd=10.0)
        assert report["live"]["steps_replayed"] == 1
        assert report["live"]["steps_failed"] == 1
        failed = [s for s in report["steps"] if s.get("error")]
        assert len(failed) == 1 and "provider 500" in failed[0]["error"]


# ---------------------------------------------------------------------------
# Judge response parsing
# ---------------------------------------------------------------------------


class TestJudgeParsing:
    def test_parses_plain_json(self):
        assert tm._parse_judge_scores('{"a": 7, "b": 9.5}') == (7.0, 9.5)

    def test_parses_fenced_and_prose(self):
        raw = 'Here are my scores:\n```json\n{"a": 3, "b": 11}\n```'
        a, b = tm._parse_judge_scores(raw)
        assert a == 3.0 and b == 10.0  # clamped to 0-10

    def test_garbage_returns_none(self):
        assert tm._parse_judge_scores("I refuse to answer") == (None, None)
        assert tm._parse_judge_scores("") == (None, None)


# ---------------------------------------------------------------------------
# API job lifecycle
# ---------------------------------------------------------------------------


class TestTimeMachineApi:
    def test_dry_run_job_lifecycle(self, monkeypatch, tmp_path):
        from sandcastle.config import settings

        monkeypatch.setattr(settings, "data_dir", str(tmp_path))

        canned = {"mode": "dry_run", "target_model": "haiku", "verdict": "ok",
                  "selection": {"runs": 0}}

        async def fake_run(**kwargs):
            return canned

        monkeypatch.setattr(tm, "run_time_machine", fake_run)

        with TestClient(app) as c:
            resp = c.post("/api/timemachine", json={"target_model": "haiku"})
            assert resp.status_code == 202, resp.text
            data = resp.json()["data"]
            job_id = data["job_id"]
            assert data["mode"] == "dry_run"

            job = None
            for _ in range(100):
                job = c.get(f"/api/timemachine/{job_id}").json()["data"]
                if job["status"] != "running":
                    break
                time.sleep(0.05)
            assert job is not None and job["status"] == "completed", job
            assert job["report"]["verdict"] == "ok"

            # job appears in the list endpoint
            listing = c.get("/api/timemachine").json()["data"]
            assert any(j["job_id"] == job_id for j in listing)

        # artifact persisted to {data_dir}/timemachine/{job_id}.json
        assert (tmp_path / "timemachine" / f"{job_id}.json").exists()
        # and readable via get_job after the in-memory registry is cleared
        tm._JOBS.pop(job_id, None)
        from_disk = tm.get_job(job_id)
        assert from_disk is not None and from_disk["status"] == "completed"

    def test_live_without_budget_is_rejected(self):
        resp = client.post(
            "/api/timemachine", json={"target_model": "haiku", "live": True}
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "BUDGET_REQUIRED"

    def test_live_over_budget_is_refused_preflight(self):
        wf = _wf_name("tm-api-budget")
        asyncio.run(
            _seed_run(
                wf,
                [{"model": "sonnet", "prompt": "x" * 400000, "output": "y" * 400000,
                  "cost_usd": 2.0}],
            )
        )
        resp = client.post(
            "/api/timemachine",
            json={"target_model": "opus", "workflow": wf, "live": True,
                  "budget_usd": 0.0001},
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"]["error"]["code"] == "BUDGET_EXCEEDED"

    def test_unknown_model_rejected(self):
        resp = client.post("/api/timemachine", json={"target_model": "gpt-42-ultra"})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "UNKNOWN_MODEL"

    def test_invalid_since_rejected(self):
        resp = client.post(
            "/api/timemachine", json={"target_model": "haiku", "since": "soonish"}
        )
        assert resp.status_code == 422

    def test_missing_job_404(self):
        resp = client.get(f"/api/timemachine/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_job_id_path_traversal_safe(self):
        resp = client.get("/api/timemachine/..%2F..%2Fetc%2Fpasswd")
        assert resp.status_code in (404, 422)
