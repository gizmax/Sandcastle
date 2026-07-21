"""Tests for the Run Assistant endpoint (POST /runs/{run_id}/assistant).

0.42.1: the dashboard's Run Assistant is backed by the advisor LLM instead of
client-side regex heuristics. The endpoint serializes the run (status, steps,
errors, output tails - secret-scrubbed) and answers via _call_advisor_llm.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from sandcastle.main import app
from sandcastle.models.db import Run, RunStatus, RunStep, StepStatus, async_session

client = TestClient(app)


async def _seed_run(status: RunStatus = RunStatus.FAILED, error: str | None = "boom") -> Run:
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        run = Run(
            id=uuid.uuid4(),
            workflow_name="lucerna-articles",
            status=status,
            input_data={"q": "Lucerna"},
            total_cost_usd=0.02,
            started_at=now - timedelta(seconds=30),
            completed_at=now,
            error=error,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        step = RunStep(
            run_id=run.id,
            step_id="search-web",
            status=StepStatus.FAILED if status == RunStatus.FAILED else StepStatus.COMPLETED,
            error="Sandbox backend 'e2b' is not available" if status == RunStatus.FAILED else None,
            output_data=None if status == RunStatus.FAILED else {"articles": 5},
            cost_usd=0.001,
            duration_seconds=2.0,
        )
        session.add(step)
        await session.commit()
        return run


@pytest.mark.asyncio
async def test_answers_via_advisor_with_run_context():
    run = await _seed_run()
    with patch(
        "sandcastle.engine.generator.run_assistant_answer",
        new=AsyncMock(return_value="Krok search-web selhal: sandbox e2b neni dostupny."),
    ) as mock_answer, patch(
        "sandcastle.engine.generator._resolve_api_key", return_value="key"
    ):
        resp = client.post(
            f"/api/runs/{run.id}/assistant", json={"question": "proc to spadlo?"}
        )
    assert resp.status_code == 200
    assert "search-web" in resp.json()["data"]["answer"]
    # The run context handed to the LLM must carry the real step error
    ctx = mock_answer.call_args.kwargs["run_context"]
    assert "search-web" in ctx
    assert "Sandbox backend 'e2b' is not available" in ctx
    assert mock_answer.call_args.kwargs["question"] == "proc to spadlo?"


@pytest.mark.asyncio
async def test_history_is_forwarded_and_bounded():
    run = await _seed_run(status=RunStatus.COMPLETED, error=None)
    history = [{"role": "user", "text": "ahoj"}, {"role": "assistant", "text": "zdravim"}]
    with patch(
        "sandcastle.engine.generator.run_assistant_answer",
        new=AsyncMock(return_value="ok"),
    ) as mock_answer, patch(
        "sandcastle.engine.generator._resolve_api_key", return_value="key"
    ):
        resp = client.post(
            f"/api/runs/{run.id}/assistant",
            json={"question": "shrn to", "history": history},
        )
    assert resp.status_code == 200
    assert mock_answer.call_args.kwargs["history"] == history


@pytest.mark.asyncio
async def test_no_provider_returns_400_no_provider():
    run = await _seed_run()
    with patch("sandcastle.engine.generator._resolve_api_key", return_value=""):
        resp = client.post(
            f"/api/runs/{run.id}/assistant", json={"question": "why?"}
        )
    assert resp.status_code == 400
    assert "NO_PROVIDER" in resp.text


@pytest.mark.asyncio
async def test_unknown_run_404():
    with patch("sandcastle.engine.generator._resolve_api_key", return_value="key"):
        resp = client.post(
            f"/api/runs/{uuid.uuid4()}/assistant", json={"question": "why?"}
        )
    assert resp.status_code == 404


def test_invalid_run_id_400():
    resp = client.post("/api/runs/not-a-uuid/assistant", json={"question": "why?"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_advisor_failure_returns_502():
    run = await _seed_run()
    with patch(
        "sandcastle.engine.generator.run_assistant_answer",
        new=AsyncMock(side_effect=RuntimeError("upstream")),
    ), patch("sandcastle.engine.generator._resolve_api_key", return_value="key"):
        resp = client.post(
            f"/api/runs/{run.id}/assistant", json={"question": "why?"}
        )
    assert resp.status_code == 502
    assert "ASSISTANT_FAILED" in resp.text


def test_question_required():
    resp = client.post(f"/api/runs/{uuid.uuid4()}/assistant", json={"question": ""})
    assert resp.status_code == 422
