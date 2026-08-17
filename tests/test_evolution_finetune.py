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
async def test_mutate_finetune_keeps_valid_falsey_training_values():
    evals = [
        {"input": "", "expected": "empty input"},
        {"input": 0, "expected": 0},
        {"input": False, "expected": False},
    ]

    new_yaml, desc = await mutate_finetune(WF, evals, min_samples=3)

    assert new_yaml != WF
    assert "on 3 samples" in desc


@pytest.mark.asyncio
async def test_mutate_finetune_excludes_failed_generated_outputs(monkeypatch):
    captured_pairs = []

    class _CapturingTrainer:
        async def train(self, _base_model, pairs, _settings):
            from sandcastle.engine.training.trainer import TrainingResult

            captured_pairs.extend(pairs)
            return TrainingResult(
                adapter_id="safe-training-data",
                base_model="sonnet",
                samples=len(pairs),
                metrics={"eval_score": 1.0},
                lora_config={},
            )

    monkeypatch.setattr(
        "sandcastle.engine.training.trainer.get_trainer",
        lambda: _CapturingTrainer(),
    )
    evals = [
        {"input": "good", "output": "accepted", "passed": True},
        {"input": "bad", "output": "known wrong answer", "passed": False},
        {
            "input": "ground truth",
            "expected": "correct answer",
            "output": "wrong answer",
            "passed": False,
        },
    ]

    new_yaml, desc = await mutate_finetune(WF, evals, min_samples=2)

    assert new_yaml != WF
    assert "on 2 samples" in desc
    assert captured_pairs == [
        {"input": "good", "output": "accepted"},
        {"input": "ground truth", "output": "correct answer"},
    ]


def test_mutation_context_includes_original_eval_inputs():
    from sandcastle.engine.eval import CaseResult, parse_eval_suite_string
    from sandcastle.engine.evolution import _build_mutation_eval_results

    suite = parse_eval_suite_string(
        """
workflow: t
cases:
  - name: first
    input:
      query: hello
    assertions:
      - type: not_empty
"""
    )
    context = _build_mutation_eval_results(
        suite.cases,
        [CaseResult(name="first", passed=True, output="world")],
    )

    assert context == [
        {
            "name": "first",
            "input": {"query": "hello"},
            "passed": True,
            "error": None,
            "output": "world",
            "cost_usd": 0.0,
            "duration_seconds": 0.0,
        }
    ]


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
