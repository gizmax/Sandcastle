"""Tests for the Self-Tune trainer abstraction + adapter registry (mock, no GPU)."""

from __future__ import annotations

import pytest

from sandcastle.config import settings
from sandcastle.engine.adapter_registry import AdapterRegistry
from sandcastle.engine.training.mock_trainer import MockTrainer
from sandcastle.engine.training.trainer import TrainingResult, get_trainer

PAIRS = [{"input": "q1", "output": "a1"}, {"input": "q2", "output": "a2"}]


# ---- factory ----------------------------------------------------------------


def test_get_trainer_mock_by_default(monkeypatch):
    monkeypatch.setattr(settings, "trainer_backend", "mock")
    assert isinstance(get_trainer(), MockTrainer)


def test_get_trainer_unknown_raises():
    with pytest.raises(ValueError, match="unknown trainer_backend"):
        get_trainer("bogus")


@pytest.mark.asyncio
async def test_gpu_trainer_is_gated():
    from sandcastle.engine.training.gpu_trainer import GPUTrainer

    # Without the [training] extras this raises (deps); with them but no GPU (CUDA).
    # Either way GPUTrainer never silently no-ops on a dev box. Full coverage of the
    # real path (with mocked deps) lives in tests/test_gpu_trainer.py.
    with pytest.raises(RuntimeError):
        await GPUTrainer().train("sonnet", PAIRS, settings)


# ---- mock trainer determinism ----------------------------------------------


@pytest.mark.asyncio
async def test_mock_trainer_is_deterministic():
    a = await MockTrainer().train("sonnet", PAIRS, settings)
    b = await MockTrainer().train("sonnet", PAIRS, settings)
    assert isinstance(a, TrainingResult)
    assert a == b  # same inputs -> identical result (id + metrics)
    assert a.samples == 2
    assert 0.0 <= a.metrics["loss"] <= 1.0
    assert 0.0 <= a.metrics["eval_score"] <= 0.99


@pytest.mark.asyncio
async def test_mock_trainer_input_sensitive_and_score_rises_with_samples():
    small = await MockTrainer().train("sonnet", PAIRS, settings)
    big = await MockTrainer().train("sonnet", PAIRS * 10, settings)
    assert small.adapter_id != big.adapter_id  # different data -> different adapter
    assert big.metrics["eval_score"] >= small.metrics["eval_score"]  # more data, >= score


# ---- adapter registry -------------------------------------------------------


def test_registry_register_and_get(tmp_path):
    reg = AdapterRegistry(tmp_path)
    reg.register("ad1", "sonnet", {"eval_score": 0.8}, 12, {"r": 8}, created_at=1.0)
    meta = reg.get("ad1")
    assert meta is not None and meta["base_model"] == "sonnet" and meta["samples"] == 12
    assert (reg.get_path("ad1") / "metadata.json").exists()
    assert reg.get("missing") is None
    with pytest.raises(KeyError):
        reg.get_path("missing")


def test_registry_list_sorted_and_cleanup(tmp_path):
    reg = AdapterRegistry(tmp_path)
    for i in range(7):
        reg.register(f"ad{i}", "sonnet", {}, i, {}, created_at=float(i))
    listed = reg.list()
    assert [m["adapter_id"] for m in listed] == [f"ad{i}" for i in range(7)]  # oldest first
    removed = reg.cleanup_old(keep_n=5)
    assert removed == 2
    remaining = [m["adapter_id"] for m in reg.list()]
    assert remaining == [f"ad{i}" for i in range(2, 7)]  # kept the 5 newest


def test_registry_cleanup_noop_when_under_keep(tmp_path):
    reg = AdapterRegistry(tmp_path)
    reg.register("a", "sonnet", {}, 1, {}, created_at=1.0)
    assert reg.cleanup_old(keep_n=5) == 0
    assert len(reg.list()) == 1
