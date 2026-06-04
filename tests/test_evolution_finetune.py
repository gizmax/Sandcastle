"""Tests for the Overnight Self-Tune mutation (mutate_finetune) + adapter resolution.

All run with the deterministic MockTrainer and a tmp adapter registry - no GPU.
"""

from __future__ import annotations

import pytest

import sandcastle.engine.adapter_registry as adapter_registry
from sandcastle.config import settings
from sandcastle.engine.evolution import _pick_mutation_type, mutate_finetune
from sandcastle.engine.providers import is_known_model, resolve_base_url, resolve_model

WF = """name: t
default_model: sonnet
steps:
  - id: a
    model: sonnet
    prompt: hi
"""

EVALS = [{"input": f"q{i}", "expected": f"a{i}"} for i in range(12)]


@pytest.fixture(autouse=True)
def _tmp_registry(tmp_path, monkeypatch):
    """Redirect the adapter registry (used by both mutate_finetune and resolve_model)."""
    monkeypatch.setattr(adapter_registry, "default_adapters_dir", lambda: tmp_path)
    monkeypatch.setattr(settings, "trainer_backend", "mock")


@pytest.mark.asyncio
async def test_mutate_finetune_trains_registers_and_routes():
    new_yaml, desc = await mutate_finetune(WF, EVALS, base_model="sonnet", min_samples=2)
    assert "trained LoRA adapter" in desc
    # Every step's model + default_model now point at the trained adapter.
    import yaml as _yaml

    data = _yaml.safe_load(new_yaml)
    assert data["default_model"].startswith("adapter/")
    assert data["steps"][0]["model"] == data["default_model"]
    # The adapter is registered and resolves to a local, $0 model.
    adapter_id = data["default_model"]
    info = resolve_model(adapter_id)
    assert info.provider == "adapter" and info.region == "local"
    assert info.input_price_per_m == 0.0 and info.output_price_per_m == 0.0
    assert is_known_model(adapter_id)


@pytest.mark.asyncio
async def test_mutate_finetune_skips_when_too_few_samples():
    new_yaml, desc = await mutate_finetune(WF, EVALS[:1], min_samples=10)
    assert new_yaml == WF  # unchanged -> run_evolution records a discard
    assert "skipped" in desc and "< min 10" in desc


@pytest.mark.asyncio
async def test_mutate_finetune_graceful_on_trainer_failure(monkeypatch):
    class _BoomTrainer:
        async def train(self, *a, **k):
            raise RuntimeError("kaboom")

    monkeypatch.setattr(
        "sandcastle.engine.training.trainer.get_trainer", lambda *a, **k: _BoomTrainer()
    )
    new_yaml, desc = await mutate_finetune(WF, EVALS, min_samples=2)
    assert new_yaml == WF and "training failed" in desc


def test_picker_includes_finetune_only_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "evolution_auto_finetune", True)
    # iter 4 (i%5==4, i>0) -> finetune; iter 1 -> not.
    assert _pick_mutation_type(4, [], 0.0, "balanced") == "finetune"
    assert _pick_mutation_type(1, [], 0.0, "balanced") != "finetune"
    monkeypatch.setattr(settings, "evolution_auto_finetune", False)
    assert _pick_mutation_type(4, [], 0.0, "balanced") != "finetune"


def test_resolve_unregistered_adapter_raises():
    with pytest.raises(KeyError):
        resolve_model("adapter/does-not-exist")


def test_adapter_base_url_is_local(monkeypatch):
    monkeypatch.setattr(settings, "nim_base_url", "http://spark:8000")
    # provider 'adapter' resolves its base URL to the local serving endpoint.
    from sandcastle.engine.providers import ModelInfo

    info = ModelInfo("adapter", "x", "runner-openai.mjs", "", None, 0.0, 0.0, region="local")
    assert resolve_base_url(info) == "http://spark:8000/v1"
