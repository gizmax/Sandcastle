"""Tests for the real GPU LoRA trainer - torch/transformers/peft fully mocked, no GPU.

The heavy stack is injected through ``GPUTrainer._load_deps`` (the single lazy-import
seam), so these tests exercise the full ``train()`` path - normalization, prompt-masked
tokenization, holdout split, artifact writing, registry handoff - on any machine.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sandcastle.engine.adapter_registry import AdapterRegistry
from sandcastle.engine.training.gpu_trainer import (
    GPUTrainer,
    TrainParams,
    compute_adapter_id,
    dataset_sha256,
    normalize_pairs,
)
from sandcastle.engine.training.trainer import TrainingResult

PAIRS = [{"input": f"q{i}", "output": f"a{i}"} for i in range(12)]


def _config(tmp_path: Path, **overrides) -> SimpleNamespace:
    base = dict(
        lora_base_model_id="acme/tiny-1b",
        lora_r=4,
        lora_alpha=8,
        lora_dropout=0.1,
        lora_lr=2e-4,
        lora_epochs=1,
        lora_max_steps=3,
        lora_seed=7,
        lora_batch_size=1,
        lora_grad_accum=2,
        lora_max_seq_len=64,
        lora_quantize="none",
        lora_output_dir=str(tmp_path / "adapters"),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _params(**overrides) -> TrainParams:
    base = dict(
        base_model_id="acme/tiny-1b", source_model="sonnet", r=4, alpha=8, dropout=0.1,
        lr=2e-4, epochs=1, max_steps=3, seed=7, batch_size=1, grad_accum=2,
        max_seq_len=64, quantize="none",
    )
    base.update(overrides)
    return TrainParams(**base)


# ---- fakes -------------------------------------------------------------------


class _FakeTokenizer:
    pad_token = "<pad>"
    eos_token = "<eos>"

    def apply_chat_template(self, msgs, tokenize=True, add_generation_prompt=False):
        n = sum(len(str(m.get("content", ""))) for m in msgs) + len(msgs)
        return list(range(n + (1 if add_generation_prompt else 0)))

    def save_pretrained(self, path):
        Path(path, "tokenizer_config.json").write_text("{}")


class _FakeModel:
    def save_pretrained(self, path):
        Path(path, "adapter_model.safetensors").write_bytes(b"fake-lora-weights")
        Path(path, "adapter_config.json").write_text("{}")


class _FakeDataset:
    def __init__(self, rows):
        self.rows = rows

    def map(self, fn, remove_columns=None):
        return _FakeDataset([fn(r) for r in self.rows])

    def train_test_split(self, test_size, seed):
        k = max(1, int(len(self.rows) * test_size))
        return {"train": _FakeDataset(self.rows[:-k]), "test": _FakeDataset(self.rows[-k:])}

    def __len__(self):
        return len(self.rows)


class _FakeHFTrainer:
    last = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _FakeHFTrainer.last = self

    def train(self):
        return SimpleNamespace(training_loss=0.5)

    def evaluate(self):
        return {"eval_loss": 0.25}


def _fake_deps() -> SimpleNamespace:
    torch = SimpleNamespace(
        bfloat16="bf16",
        cuda=SimpleNamespace(is_available=lambda: True),
        __version__="2.0-fake",
    )
    transformers = SimpleNamespace(
        set_seed=MagicMock(),
        AutoTokenizer=SimpleNamespace(from_pretrained=lambda mid: _FakeTokenizer()),
        AutoModelForCausalLM=SimpleNamespace(
            from_pretrained=MagicMock(return_value=_FakeModel())
        ),
        TrainingArguments=MagicMock(),
        BitsAndBytesConfig=MagicMock(),
        DataCollatorForSeq2Seq=MagicMock(),
        Trainer=_FakeHFTrainer,
        __version__="4.0-fake",
    )
    peft = SimpleNamespace(
        LoraConfig=MagicMock(),
        get_peft_model=MagicMock(side_effect=lambda model, cfg: model),
        prepare_model_for_kbit_training=MagicMock(side_effect=lambda model: model),
        __version__="0.1-fake",
    )
    datasets = SimpleNamespace(
        Dataset=SimpleNamespace(from_list=lambda rows: _FakeDataset(rows))
    )
    return SimpleNamespace(torch=torch, transformers=transformers, peft=peft, datasets=datasets)


# ---- happy path ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_trains_writes_artifacts_and_registers(tmp_path, monkeypatch):
    deps = _fake_deps()
    monkeypatch.setattr(GPUTrainer, "_load_deps", lambda self: deps)
    cfg = _config(tmp_path)

    result = await GPUTrainer().train("sonnet", PAIRS, cfg)

    assert isinstance(result, TrainingResult)
    assert result.adapter_id.startswith("lora-acme_tiny-1b-")
    assert result.base_model == "acme/tiny-1b"
    assert result.samples == 12
    assert result.metrics == {"loss": 0.5, "eval_loss": 0.25, "eval_score": 0.8}
    assert result.lora_config["r"] == 4
    assert result.lora_config["source_model"] == "sonnet"
    assert result.lora_config["dataset_sha256"] == dataset_sha256(normalize_pairs(PAIRS))

    # Hyperparams reached the HF TrainingArguments unchanged.
    args_kwargs = deps.transformers.TrainingArguments.call_args.kwargs
    assert args_kwargs["max_steps"] == 3 and args_kwargs["seed"] == 7
    assert args_kwargs["learning_rate"] == 2e-4 and args_kwargs["bf16"] is True

    # 12 samples -> a holdout eval split exists; labels are prompt-masked.
    hf = _FakeHFTrainer.last
    assert hf.kwargs["eval_dataset"] is not None
    row = hf.kwargs["train_dataset"].rows[0]
    assert row["labels"][0] == -100 and row["labels"][-1] != -100
    assert len(row["input_ids"]) == len(row["labels"]) <= cfg.lora_max_seq_len

    # Artifacts: weights + dataset JSONL + reproducibility metadata.
    out = Path(cfg.lora_output_dir) / result.adapter_id
    assert (out / "adapter_model.safetensors").exists()
    assert (out / "dataset.jsonl").read_text().count("\n") == 12
    meta = json.loads((out / "training_metadata.json").read_text())
    assert meta["base_model"] == "acme/tiny-1b"
    assert meta["metrics"]["eval_loss"] == 0.25
    assert meta["dataset_sha256"] == result.lora_config["dataset_sha256"]
    assert meta["hyperparams"]["seed"] == 7

    # Registry handoff: register() keeps real weights and skips the stub file.
    reg = AdapterRegistry(cfg.lora_output_dir)
    reg.register(
        result.adapter_id, result.base_model, result.metrics, result.samples,
        result.lora_config, created_at=1.0,
    )
    got = reg.get(result.adapter_id)
    assert got is not None and got["base_model"] == "acme/tiny-1b"
    assert got["metrics"]["eval_score"] == 0.8
    assert not (out / "adapter_model.safetensors.stub").exists()


@pytest.mark.asyncio
async def test_small_dataset_skips_holdout(tmp_path, monkeypatch):
    deps = _fake_deps()
    monkeypatch.setattr(GPUTrainer, "_load_deps", lambda self: deps)

    result = await GPUTrainer().train("sonnet", PAIRS[:4], _config(tmp_path))

    assert _FakeHFTrainer.last.kwargs["eval_dataset"] is None
    assert result.metrics["eval_loss"] == result.metrics["loss"] == 0.5


@pytest.mark.asyncio
async def test_quantize_4bit_wires_bitsandbytes(tmp_path, monkeypatch):
    deps = _fake_deps()
    monkeypatch.setattr(GPUTrainer, "_load_deps", lambda self: deps)

    await GPUTrainer().train("sonnet", PAIRS, _config(tmp_path, lora_quantize="4bit"))

    bnb_kwargs = deps.transformers.BitsAndBytesConfig.call_args.kwargs
    assert bnb_kwargs["load_in_4bit"] is True and bnb_kwargs["load_in_8bit"] is False
    deps.peft.prepare_model_for_kbit_training.assert_called_once()


# ---- gating errors --------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_deps_raise_actionable_error(tmp_path, monkeypatch):
    # None in sys.modules makes `import X` raise ImportError even if X is installed.
    for mod in ("torch", "transformers", "peft", "datasets"):
        monkeypatch.setitem(sys.modules, mod, None)
    with pytest.raises(RuntimeError, match=r"sandcastle-ai\[training\]"):
        await GPUTrainer().train("sonnet", PAIRS, _config(tmp_path))


@pytest.mark.asyncio
async def test_no_cuda_raises_clear_error(tmp_path, monkeypatch):
    deps = _fake_deps()
    deps.torch.cuda = SimpleNamespace(is_available=lambda: False)
    monkeypatch.setattr(GPUTrainer, "_load_deps", lambda self: deps)
    with pytest.raises(RuntimeError, match="CUDA"):
        await GPUTrainer().train("sonnet", PAIRS, _config(tmp_path))


@pytest.mark.asyncio
async def test_no_usable_pairs_raises_before_deps_load():
    # ValueError fires before any heavy import is attempted.
    with pytest.raises(ValueError, match="no usable training pairs"):
        await GPUTrainer().train("sonnet", [{"junk": 1}], SimpleNamespace())


# ---- deterministic config hashing ------------------------------------------------


def test_adapter_id_is_deterministic():
    rows = normalize_pairs(PAIRS)
    assert compute_adapter_id(_params(), rows) == compute_adapter_id(_params(), rows)


def test_adapter_id_changes_with_params_or_data():
    rows = normalize_pairs(PAIRS)
    base = compute_adapter_id(_params(), rows)
    assert compute_adapter_id(_params(r=8), rows) != base
    assert compute_adapter_id(_params(seed=8), rows) != base
    assert compute_adapter_id(_params(), normalize_pairs(PAIRS[:6])) != base


def test_dataset_sha256_order_sensitive_and_stable():
    rows = normalize_pairs(PAIRS)
    assert dataset_sha256(rows) == dataset_sha256(list(rows))
    assert dataset_sha256(rows) != dataset_sha256(list(reversed(rows)))


# ---- pair normalization -----------------------------------------------------------


def test_normalize_pairs_accepts_all_documented_shapes():
    rows = normalize_pairs(
        [
            {"input": "q", "output": "a"},
            {"prompt": "p", "completion": "c"},
            {"messages": [{"role": "user", "content": "m"}]},
            {"junk": 1},
            "not-a-dict",  # type: ignore[list-item]
            {"input": "", "output": "a"},  # empty prompt -> skipped
        ]
    )
    assert len(rows) == 3
    assert rows[0]["messages"][0] == {"role": "user", "content": "q"}
    assert rows[0]["messages"][1] == {"role": "assistant", "content": "a"}
    assert rows[1]["messages"][1] == {"role": "assistant", "content": "c"}
    assert rows[2]["messages"] == [{"role": "user", "content": "m"}]
