"""Tests for the Night Shift (Overnight Self-Tune) API: adapter list + nightly history.

All run with a tmp filesystem registry - no GPU, no real training.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

import sandcastle.engine.adapter_registry as adapter_registry
from sandcastle.config import settings
from sandcastle.engine.adapter_registry import AdapterRegistry
from sandcastle.main import app

client = TestClient(app)

# 2026-06-01T23:30:00Z and 2026-06-02T23:30:00Z - two consecutive "nights" (UTC)
NIGHT_1 = datetime(2026, 6, 1, 23, 30, tzinfo=timezone.utc).timestamp()
NIGHT_2 = datetime(2026, 6, 2, 23, 30, tzinfo=timezone.utc).timestamp()


@pytest.fixture(autouse=True)
def _tmp_dirs(tmp_path, monkeypatch):
    """Redirect the adapter registry + workflows dir to per-test tmp paths."""
    monkeypatch.setattr(adapter_registry, "default_adapters_dir", lambda: tmp_path / "adapters")
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    monkeypatch.setattr(settings, "workflows_dir", str(workflows))
    return tmp_path


def _register(adapter_id: str, *, score: float, created_at: float, parent: str | None = None):
    AdapterRegistry().register(
        adapter_id=adapter_id,
        base_model="sonnet",
        metrics={"loss": 0.1, "eval_score": score},
        samples=12,
        lora_config={"r": 8, "alpha": 16, "lr": 0.0001, "epochs": 3},
        created_at=created_at,
        dataset_hash="abc123" if parent else None,
        parent_adapter_id=parent,
    )


# ---- registry lineage (backward-compatible metadata) -------------------------


def test_registry_lineage_round_trip(tmp_path):
    reg = AdapterRegistry(tmp_path)
    reg.register("parent", "sonnet", {}, 5, {}, created_at=1.0)
    reg.register(
        "child", "sonnet", {}, 9, {}, created_at=2.0,
        dataset_hash="deadbeef", parent_adapter_id="parent",
    )
    assert reg.get("parent")["parent_adapter_id"] is None
    assert reg.get("parent")["dataset_hash"] is None
    assert reg.get("child")["parent_adapter_id"] == "parent"
    assert reg.get("child")["dataset_hash"] == "deadbeef"


def test_registry_reads_legacy_metadata_without_lineage_keys(tmp_path):
    """Pre-lineage metadata.json files (no new keys) must still be readable."""
    import json

    d = tmp_path / "legacy"
    d.mkdir(parents=True)
    (d / "metadata.json").write_text(json.dumps({
        "adapter_id": "legacy",
        "base_model": "sonnet",
        "metrics": {"eval_score": 0.7},
        "samples": 10,
        "lora_config": {"r": 8},
        "created_at": 1.0,
    }))
    meta = AdapterRegistry(tmp_path).get("legacy")
    assert meta is not None
    assert meta.get("parent_adapter_id") is None  # absent key -> no lineage
    assert meta.get("dataset_hash") is None


# ---- GET /api/adapters --------------------------------------------------------


class TestAdaptersEndpoint:
    def test_empty_registry_returns_empty_list(self):
        response = client.get("/api/adapters")
        assert response.status_code == 200
        assert response.json()["data"] == []

    def test_lists_adapters_with_metadata_and_lineage(self):
        _register("gen1", score=0.71, created_at=NIGHT_1)
        _register("gen2", score=0.78, created_at=NIGHT_2, parent="gen1")

        response = client.get("/api/adapters")
        assert response.status_code == 200
        adapters = response.json()["data"]
        assert [a["adapter_id"] for a in adapters] == ["gen1", "gen2"]  # oldest first

        gen1, gen2 = adapters
        assert gen1["parent_adapter_id"] is None
        assert gen2["parent_adapter_id"] == "gen1"
        assert gen2["dataset_hash"] == "abc123"
        assert gen2["base_model"] == "sonnet"
        assert gen2["samples"] == 12
        assert gen2["lora_config"]["r"] == 8
        assert gen2["metrics"]["eval_score"] == pytest.approx(0.78)
        assert gen2["created_at"] == pytest.approx(NIGHT_2)

    def test_served_flag_set_when_a_workflow_routes_to_adapter(self, _tmp_dirs):
        _register("gen1", score=0.71, created_at=NIGHT_1)
        _register("gen2", score=0.78, created_at=NIGHT_2, parent="gen1")
        wf = _tmp_dirs / "workflows" / "tuned.yaml"
        wf.write_text("name: tuned\ndefault_model: adapter/gen2\nsteps:\n  - id: a\n    prompt: hi\n")

        adapters = client.get("/api/adapters").json()["data"]
        served = {a["adapter_id"]: a["served"] for a in adapters}
        assert served == {"gen1": False, "gen2": True}


# ---- GET /api/self-tune/nights -------------------------------------------------


class TestSelfTuneNightsEndpoint:
    def test_empty_history(self):
        response = client.get("/api/self-tune/nights")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["total_adapters"] == 0
        assert data["enabled"] is False  # evolution_auto_finetune defaults to off
        # No adapters in the tmp registry -> no nights contributed by this test
        assert all(n["adapters_produced"] == 0 for n in data["nights"])

    def test_enabled_reflects_setting(self, monkeypatch):
        monkeypatch.setattr(settings, "evolution_auto_finetune", True)
        assert client.get("/api/self-tune/nights").json()["data"]["enabled"] is True

    def test_nights_aggregate_scores_and_delta(self):
        _register("n1-a", score=0.70, created_at=NIGHT_1)
        _register("n1-b", score=0.74, created_at=NIGHT_1 + 60)
        _register("n2-a", score=0.81, created_at=NIGHT_2, parent="n1-b")

        data = client.get("/api/self-tune/nights").json()["data"]
        assert data["total_adapters"] == 3
        nights = {n["night"]: n for n in data["nights"]}

        night1 = nights["2026-06-01"]
        assert night1["adapters_produced"] == 2
        assert night1["best_eval_score"] == pytest.approx(0.74)
        assert sorted(night1["adapter_ids"]) == ["n1-a", "n1-b"]

        night2 = nights["2026-06-02"]
        assert night2["adapters_produced"] == 1
        assert night2["best_eval_score"] == pytest.approx(0.81)
        assert night2["best_delta"] == pytest.approx(0.81 - 0.74)

        # Chronological order, oldest first
        listed = [n["night"] for n in data["nights"]]
        assert listed == sorted(listed)

    @pytest.mark.asyncio
    async def test_nights_count_finetune_mutations_from_iterations(self):
        """Finetune iterations in the DB are counted as tried/kept per night."""
        from sandcastle.models.db import (
            Base,
            EvolutionIteration,
            WorkflowEvolution,
            async_session,
            engine,
        )

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Use a day far from other tests so their iterations cannot interfere.
        night = datetime(2031, 1, 15, 23, 45, tzinfo=timezone.utc)
        async with async_session() as session:
            evo = WorkflowEvolution(workflow_name=f"night-shift-{uuid.uuid4().hex[:8]}")
            session.add(evo)
            await session.flush()
            session.add(EvolutionIteration(
                evolution_id=evo.id, iteration_number=4, mutation_type="finetune",
                mutation_description="trained LoRA adapter", status="keep", created_at=night,
            ))
            session.add(EvolutionIteration(
                evolution_id=evo.id, iteration_number=9, mutation_type="finetune",
                mutation_description="finetune skipped", status="discard", created_at=night,
            ))
            session.add(EvolutionIteration(
                evolution_id=evo.id, iteration_number=5, mutation_type="prompt",
                mutation_description="not a finetune", status="keep", created_at=night,
            ))
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/api/self-tune/nights")
        assert response.status_code == 200
        nights = {n["night"]: n for n in response.json()["data"]["nights"]}
        assert nights["2031-01-15"]["mutations_tried"] == 2  # prompt mutation excluded
        assert nights["2031-01-15"]["mutations_kept"] == 1
        assert nights["2031-01-15"]["adapters_produced"] == 0


# ---- mutate_finetune records lineage -------------------------------------------


@pytest.mark.asyncio
async def test_mutate_finetune_records_parent_and_dataset_hash(monkeypatch):
    from sandcastle.engine.evolution import mutate_finetune

    monkeypatch.setattr(settings, "trainer_backend", "mock")
    evals = [{"input": f"q{i}", "expected": f"a{i}"} for i in range(12)]

    # First generation: workflow on a base model -> no parent.
    wf = "name: t\ndefault_model: sonnet\nsteps:\n  - id: a\n    model: sonnet\n    prompt: hi\n"
    new_yaml, desc = await mutate_finetune(wf, evals, base_model="sonnet", min_samples=2)
    assert "trained LoRA adapter" in desc
    gen1_id = [m for m in AdapterRegistry().list()][-1]["adapter_id"]
    gen1 = AdapterRegistry().get(gen1_id)
    assert gen1["parent_adapter_id"] is None
    assert isinstance(gen1["dataset_hash"], str) and len(gen1["dataset_hash"]) == 64

    # Second generation: workflow already routed to gen1 -> gen1 is the parent.
    evals2 = evals + [{"input": "q12", "expected": "a12"}]
    _, desc2 = await mutate_finetune(new_yaml, evals2, base_model="sonnet", min_samples=2)
    assert "trained LoRA adapter" in desc2
    children = [m for m in AdapterRegistry().list() if m["adapter_id"] != gen1_id]
    assert len(children) == 1
    assert children[0]["parent_adapter_id"] == gen1_id
    assert children[0]["dataset_hash"] != gen1["dataset_hash"]  # different data
