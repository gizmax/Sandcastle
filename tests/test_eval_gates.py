"""Tests for golden datasets + eval gates that block workflow promotion."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from sandcastle.main import app
from sandcastle.models.db import (
    GoldenCase,
    GoldenDataset,
    WorkflowVersion,
    WorkflowVersionStatus,
    async_session,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_dataset(
    workflow_name: str,
    *,
    name: str | None = None,
    is_active: bool = True,
    cases: list[dict] | None = None,
    tenant_id: str | None = None,
    version: int = 1,
) -> str:
    """Insert a GoldenDataset directly into the test DB and return its id."""
    ds_name = name or f"{workflow_name}-golden"
    ds = GoldenDataset(
        tenant_id=tenant_id,
        name=ds_name,
        workflow_name=workflow_name,
        version=version,
        description="seeded",
        is_active=is_active,
    )
    case_rows: list[GoldenCase] = []
    for c in cases or []:
        case_rows.append(
            GoldenCase(
                case_label=c.get("case_label", "case"),
                input_data=c.get("input_data") or {},
                expected_output=c.get("expected_output"),
                expected_score_min=c.get("expected_score_min", 0.7),
            )
        )
    ds.cases = case_rows
    async with async_session() as session:
        session.add(ds)
        await session.commit()
    return str(ds.id)


async def _seed_production_version(workflow_name: str, version: int = 1) -> None:
    """Insert a PRODUCTION WorkflowVersion so /publish has something to flip."""
    wv = WorkflowVersion(
        workflow_name=workflow_name,
        version=version,
        status=WorkflowVersionStatus.PRODUCTION,
        yaml_content="name: " + workflow_name + "\nsteps: []\n",
        description="",
        steps_count=0,
        checksum="x" * 64,
    )
    async with async_session() as session:
        session.add(wv)
        await session.commit()


# ---------------------------------------------------------------------------
# Model + API: dataset creation & retrieval
# ---------------------------------------------------------------------------


def test_create_golden_dataset_via_api():
    """POST /api/golden-datasets persists dataset + cases."""
    body = {
        "name": "summarize-golden-create",
        "workflow_name": "summarize-create",
        "description": "tiny suite",
        "cases": [
            {
                "case_label": "short",
                "input_data": {"text": "hi"},
                "expected_output": {"summary": "hi"},
                "expected_score_min": 0.5,
            },
            {
                "case_label": "long",
                "input_data": {"text": "x" * 100},
                "expected_output": {"summary": "x"},
                "expected_score_min": 0.7,
            },
        ],
    }
    resp = client.post("/api/golden-datasets", json=body)
    assert resp.status_code == 201, resp.text
    payload = resp.json()["data"]
    assert payload["name"] == "summarize-golden-create"
    assert payload["workflow_name"] == "summarize-create"
    assert len(payload["cases"]) == 2
    assert payload["is_active"] is True


def test_create_golden_dataset_requires_name_and_workflow():
    """Missing name or workflow_name returns 422."""
    resp = client.post("/api/golden-datasets", json={"name": "x"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"]["code"] == "INVALID_DATASET"


def test_get_golden_dataset_by_name():
    """GET /api/golden-datasets/{name} returns the most recent matching dataset."""
    import asyncio

    asyncio.run(
        _seed_dataset(
            "wf-get",
            name="wf-get-golden",
            cases=[{"case_label": "c1", "input_data": {"a": 1}, "expected_output": {"a": 1}}],
        )
    )
    resp = client.get("/api/golden-datasets/wf-get-golden")
    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["name"] == "wf-get-golden"
    assert payload["workflow_name"] == "wf-get"
    assert len(payload["cases"]) == 1


def test_get_golden_dataset_not_found():
    """Unknown dataset name returns 404."""
    resp = client.get("/api/golden-datasets/does-not-exist-xyz")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# evaluate_against_golden + gate_promotion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_against_golden_returns_aggregate_score():
    """All-matching outputs yield aggregate_score=1.0 and passed=True."""
    from sandcastle.engine import evals

    dataset_id = await _seed_dataset(
        "wf-eval-agg",
        cases=[
            {
                "case_label": "c1",
                "input_data": {"x": 1},
                "expected_output": {"out": "ok"},
                "expected_score_min": 0.5,
            },
            {
                "case_label": "c2",
                "input_data": {"x": 2},
                "expected_output": {"out": "ok"},
                "expected_score_min": 0.5,
            },
        ],
    )
    with patch.object(
        evals,
        "_execute_workflow_for_gate",
        new=AsyncMock(return_value={"out": "ok"}),
    ):
        result = await evals.evaluate_against_golden(
            workflow_name="wf-eval-agg",
            version=1,
            dataset_id=dataset_id,
            threshold=0.7,
        )
    assert result.total_cases == 2
    assert result.passed_cases == 2
    assert result.aggregate_score == pytest.approx(1.0)
    assert result.passed is True


@pytest.mark.asyncio
async def test_gate_promotion_passes_when_score_meets_threshold():
    """gate_promotion returns (True, result) when aggregate >= threshold."""
    from sandcastle.engine import evals

    await _seed_dataset(
        "wf-gate-pass",
        cases=[
            {
                "case_label": "c1",
                "input_data": {},
                "expected_output": {"answer": 42},
                "expected_score_min": 0.5,
            }
        ],
    )
    with patch.object(
        evals,
        "_execute_workflow_for_gate",
        new=AsyncMock(return_value={"answer": 42}),
    ):
        passed, result = await evals.gate_promotion(
            workflow_name="wf-gate-pass",
            target_version=1,
            min_score=0.7,
        )
    assert passed is True
    assert result is not None
    assert result.aggregate_score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_gate_promotion_rejects_below_threshold():
    """gate_promotion returns (False, result) when scores are too low."""
    from sandcastle.engine import evals

    await _seed_dataset(
        "wf-gate-fail",
        cases=[
            {
                "case_label": "c1",
                "input_data": {},
                "expected_output": {"answer": 42},
                "expected_score_min": 0.9,
            }
        ],
    )
    # Returned dict has no matching keys, so similarity = 0.
    with patch.object(
        evals,
        "_execute_workflow_for_gate",
        new=AsyncMock(return_value={"wrong": "stuff"}),
    ):
        passed, result = await evals.gate_promotion(
            workflow_name="wf-gate-fail",
            target_version=1,
            min_score=0.8,
        )
    assert passed is False
    assert result is not None
    assert result.aggregate_score < 0.8


@pytest.mark.asyncio
async def test_gate_promotion_no_dataset_is_noop():
    """When no active dataset exists, gate_promotion returns (True, None)."""
    from sandcastle.engine import evals

    passed, result = await evals.gate_promotion(
        workflow_name="wf-without-dataset-zzz",
        target_version=1,
    )
    assert passed is True
    assert result is None


# ---------------------------------------------------------------------------
# /workflows/{name}/eval-gate endpoint
# ---------------------------------------------------------------------------


def test_workflow_eval_gate_endpoint_passes():
    """The /eval-gate endpoint returns passed=true when the workflow matches."""
    import asyncio

    asyncio.run(
        _seed_dataset(
            "wf-gate-endpoint",
            cases=[
                {
                    "case_label": "c1",
                    "input_data": {},
                    "expected_output": {"v": 1},
                    "expected_score_min": 0.5,
                }
            ],
        )
    )
    with patch(
        "sandcastle.engine.evals._execute_workflow_for_gate",
        new=AsyncMock(return_value={"v": 1}),
    ):
        resp = client.post("/api/workflows/wf-gate-endpoint/eval-gate?min_score=0.7")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["passed"] is True
    assert data["aggregate_score"] >= 0.7


def test_workflow_eval_gate_endpoint_skipped_without_dataset():
    """No dataset -> endpoint returns passed=true and skipped=true."""
    resp = client.post("/api/workflows/wf-no-dataset/eval-gate")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["passed"] is True
    assert data.get("skipped") is True


# ---------------------------------------------------------------------------
# /publish endpoint with strict=true
# ---------------------------------------------------------------------------


def test_publish_strict_blocks_when_gate_fails():
    """publish?strict=true returns 422 when the eval gate fails."""
    import asyncio

    asyncio.run(_seed_production_version("wf-publish-fail"))
    asyncio.run(
        _seed_dataset(
            "wf-publish-fail",
            cases=[
                {
                    "case_label": "c1",
                    "input_data": {},
                    "expected_output": {"k": "v"},
                    "expected_score_min": 0.9,
                }
            ],
        )
    )
    with patch(
        "sandcastle.engine.evals._execute_workflow_for_gate",
        new=AsyncMock(return_value={"k": "wrong"}),
    ):
        resp = client.post("/api/workflows/wf-publish-fail/publish?strict=true&min_score=0.9")
    assert resp.status_code == 422, resp.text
    err = resp.json()["detail"]["error"]
    assert err["code"] == "EVAL_GATE_FAILED"
    assert err["details"]["aggregate_score"] < 0.9


def test_publish_strict_passes_when_gate_passes():
    """publish?strict=true succeeds when the gate clears the threshold."""
    import asyncio

    asyncio.run(_seed_production_version("wf-publish-pass"))
    asyncio.run(
        _seed_dataset(
            "wf-publish-pass",
            cases=[
                {
                    "case_label": "c1",
                    "input_data": {},
                    "expected_output": {"ok": True},
                    "expected_score_min": 0.5,
                }
            ],
        )
    )
    with patch(
        "sandcastle.engine.evals._execute_workflow_for_gate",
        new=AsyncMock(return_value={"ok": True}),
    ):
        resp = client.post("/api/workflows/wf-publish-pass/publish?strict=true&min_score=0.7")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["is_public"] is True


def test_publish_non_strict_does_not_run_gate():
    """Without strict=true, /publish does not consult the eval gate."""
    import asyncio

    asyncio.run(_seed_production_version("wf-publish-loose"))
    asyncio.run(
        _seed_dataset(
            "wf-publish-loose",
            cases=[
                {
                    "case_label": "c1",
                    "input_data": {},
                    "expected_output": {"x": 1},
                    "expected_score_min": 0.95,
                }
            ],
        )
    )
    # If the gate ran, this mock would make it fail. Without strict=true the
    # endpoint must still succeed and never call the executor wrapper.
    with patch(
        "sandcastle.engine.evals._execute_workflow_for_gate",
        new=AsyncMock(return_value={"wrong": True}),
    ) as exec_mock:
        resp = client.post("/api/workflows/wf-publish-loose/publish")
    assert resp.status_code == 200, resp.text
    exec_mock.assert_not_called()


def test_publish_strict_requires_dataset():
    """strict=true without an active dataset returns EVAL_GATE_DATASET_MISSING."""
    import asyncio

    asyncio.run(_seed_production_version("wf-publish-no-ds"))
    resp = client.post("/api/workflows/wf-publish-no-ds/publish?strict=true")
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"]["code"] == "EVAL_GATE_DATASET_MISSING"
