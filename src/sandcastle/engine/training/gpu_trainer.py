"""Real GPU LoRA trainer - SFT with torch + transformers + peft (Overnight Self-Tune).

Trains a LoRA adapter over a Hugging Face base model on the workflow's own eval pairs
and writes it where the adapter registry (and the local vLLM/NIM server) can pick it up.
Heavy deps (torch/transformers/peft/datasets) are imported lazily inside ``train`` so
importing this module never requires them; missing deps and missing CUDA both raise a
clear, actionable ``RuntimeError``. Use ``trainer_backend="mock"`` everywhere else.

Dataset format: each training pair is ``{"input": .., "output": ..}`` (the shape
``evolution.mutate_finetune`` builds and MockTrainer hashes); ``prompt``/``completion``
aliases and pre-built ``{"messages": [..]}`` chat rows are accepted too. On disk the
trainer persists the normalized chat JSONL (one ``{"messages": [...]}`` per line)
next to the adapter weights for full reproducibility.

Quantization: defaults to **bf16** full-precision base + LoRA with a small default base
model (Qwen2.5-7B-Instruct) - this is guaranteed to work on the DGX Spark's aarch64
(GB10, ~128 GB unified memory). bitsandbytes 4-bit/8-bit (``lora_quantize``) is opt-in
for 70B-class bases because aarch64 CUDA wheels are not universally available.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sandcastle.engine.training.trainer import TrainingResult

logger = logging.getLogger(__name__)

_INSTALL_HINT = (
    "GPUTrainer needs torch + transformers + peft + datasets + accelerate. "
    "Install the training extras with: pip install 'sandcastle-ai[training]' "
    "- or set trainer_backend='mock' to run the Self-Tune loop without a GPU."
)

# Default HF base model for real fine-tunes. Small enough to train comfortably in bf16
# on a 128 GB DGX Spark; for 70B-class bases set lora_base_model_id + lora_quantize.
_DEFAULT_BASE_MODEL_ID = "Qwen/Qwen2.5-7B-Instruct"

# Fraction of pairs held out for eval loss (only when there are enough samples).
_EVAL_HOLDOUT = 0.1
_MIN_SAMPLES_FOR_HOLDOUT = 10


@dataclass(frozen=True)
class TrainParams:
    """Resolved hyperparameters of one GPU LoRA run - hashed into the adapter id."""

    base_model_id: str
    source_model: str  # the Sandcastle model the adapter replaces (e.g. "sonnet")
    r: int
    alpha: int
    dropout: float
    lr: float
    epochs: int
    max_steps: int  # 0 = no cap (epochs decide)
    seed: int
    batch_size: int
    grad_accum: int
    max_seq_len: int
    quantize: str  # "none" | "8bit" | "4bit"


def normalize_pairs(training_pairs: list[dict]) -> list[dict]:
    """Normalize raw training pairs into chat rows: ``{"messages": [user, assistant]}``.

    Accepts ``input``/``output`` (the mutate_finetune shape), ``prompt``/``completion``
    aliases, or pass-through ``messages`` rows. Unusable entries are silently skipped.
    """
    rows: list[dict] = []
    for p in training_pairs or []:
        if not isinstance(p, dict):
            continue
        if isinstance(p.get("messages"), list) and p["messages"]:
            rows.append({"messages": p["messages"]})
            continue
        prompt = p.get("input") or p.get("prompt")
        completion = p.get("output") or p.get("completion")
        if prompt and completion:
            rows.append(
                {
                    "messages": [
                        {"role": "user", "content": str(prompt)},
                        {"role": "assistant", "content": str(completion)},
                    ]
                }
            )
    return rows


def dataset_sha256(rows: list[dict]) -> str:
    """Stable sha256 of the normalized chat dataset (canonical JSONL)."""
    canonical = "\n".join(json.dumps(r, sort_keys=True, ensure_ascii=True) for r in rows)
    return hashlib.sha256(canonical.encode()).hexdigest()


def compute_adapter_id(params: TrainParams, rows: list[dict]) -> str:
    """Deterministic adapter id: pure function of (hyperparams, dataset).

    Same base model + same data + same hyperparams always yields the same id, so
    re-running a nightly tune on unchanged data is idempotent for the registry.
    """
    canonical = json.dumps(
        {"params": asdict(params), "dataset_sha256": dataset_sha256(rows)}, sort_keys=True
    )
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    base = params.base_model_id.replace("/", "_")
    return f"lora-{base}-{digest[:12]}"


class GPUTrainer:
    """Real LoRA SFT trainer. Needs the ``[training]`` extras and a CUDA device."""

    async def train(
        self, base_model: str, training_pairs: list[dict], config: Any
    ) -> TrainingResult:
        """Train a LoRA adapter on ``training_pairs``; blocking work runs in a thread."""
        params = self._resolve_params(base_model, config)
        rows = normalize_pairs(training_pairs)
        if not rows:
            raise ValueError("no usable training pairs (need input/output or messages)")
        adapter_id = compute_adapter_id(params, rows)
        out_dir = self._output_dir(config) / adapter_id

        deps = self._load_deps()
        self._require_cuda(deps.torch)
        logger.info(
            "GPU LoRA train start: adapter=%s base=%s samples=%d",
            adapter_id, params.base_model_id, len(rows),
        )
        return await asyncio.to_thread(self._train_sync, deps, params, rows, adapter_id, out_dir)

    # ---- pure / cheap helpers (no heavy deps) --------------------------------

    def _resolve_params(self, base_model: str, config: Any) -> TrainParams:
        """Read hyperparameters off ``config`` (settings) with sensible defaults."""
        return TrainParams(
            base_model_id=getattr(config, "lora_base_model_id", _DEFAULT_BASE_MODEL_ID)
            or _DEFAULT_BASE_MODEL_ID,
            source_model=base_model,
            r=int(getattr(config, "lora_r", 8)),
            alpha=int(getattr(config, "lora_alpha", 16)),
            dropout=float(getattr(config, "lora_dropout", 0.05)),
            lr=float(getattr(config, "lora_lr", 1e-4)),
            epochs=int(getattr(config, "lora_epochs", 3)),
            max_steps=int(getattr(config, "lora_max_steps", 0)),
            seed=int(getattr(config, "lora_seed", 42)),
            batch_size=int(getattr(config, "lora_batch_size", 1)),
            grad_accum=int(getattr(config, "lora_grad_accum", 8)),
            max_seq_len=int(getattr(config, "lora_max_seq_len", 2048)),
            quantize=str(getattr(config, "lora_quantize", "none") or "none"),
        )

    def _output_dir(self, config: Any) -> Path:
        """Adapter output root: ``lora_output_dir`` override or the adapter registry."""
        override = getattr(config, "lora_output_dir", "") or ""
        if override:
            return Path(override)
        from sandcastle.engine.adapter_registry import default_adapters_dir

        return default_adapters_dir()

    def _load_deps(self) -> SimpleNamespace:
        """Lazily import the heavy training stack; actionable error when missing."""
        try:
            import datasets
            import peft
            import torch
            import transformers
        except ImportError as e:
            raise RuntimeError(_INSTALL_HINT) from e
        return SimpleNamespace(
            torch=torch, transformers=transformers, peft=peft, datasets=datasets
        )

    def _require_cuda(self, torch: Any) -> None:
        """Raise unless a CUDA device is visible (DGX Spark / any NVIDIA GPU)."""
        if not torch.cuda.is_available():
            raise RuntimeError(
                "GPUTrainer needs a CUDA device and none is available. Run on a GPU "
                "host (e.g. a DGX Spark), or set trainer_backend='mock'."
            )

    # ---- the blocking training kernel ----------------------------------------

    def _train_sync(
        self,
        deps: SimpleNamespace,
        params: TrainParams,
        rows: list[dict],
        adapter_id: str,
        out_dir: Path,
    ) -> TrainingResult:
        """SFT a LoRA adapter and write weights + dataset + metadata to ``out_dir``."""
        torch, tf, peft, datasets = deps.torch, deps.transformers, deps.peft, deps.datasets
        tf.set_seed(params.seed)

        tokenizer = tf.AutoTokenizer.from_pretrained(params.base_model_id)
        if getattr(tokenizer, "pad_token", None) is None:
            tokenizer.pad_token = tokenizer.eos_token

        model_kwargs: dict[str, Any] = {"torch_dtype": torch.bfloat16, "device_map": "auto"}
        if params.quantize in ("4bit", "8bit"):
            # Opt-in: needs bitsandbytes with CUDA aarch64 support on a Spark.
            model_kwargs["quantization_config"] = tf.BitsAndBytesConfig(
                load_in_4bit=params.quantize == "4bit",
                load_in_8bit=params.quantize == "8bit",
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_quant_type="nf4",
            )
        model = tf.AutoModelForCausalLM.from_pretrained(params.base_model_id, **model_kwargs)
        if params.quantize in ("4bit", "8bit"):
            model = peft.prepare_model_for_kbit_training(model)
        lora_cfg = peft.LoraConfig(
            r=params.r,
            lora_alpha=params.alpha,
            lora_dropout=params.dropout,
            task_type="CAUSAL_LM",
            target_modules="all-linear",
        )
        model = peft.get_peft_model(model, lora_cfg)

        def _tokenize(row: dict) -> dict:
            # Loss only on the assistant completion: prompt tokens get label -100.
            msgs = row["messages"]
            full = list(tokenizer.apply_chat_template(msgs, tokenize=True))
            prompt = tokenizer.apply_chat_template(
                msgs[:-1], tokenize=True, add_generation_prompt=True
            )
            full = full[: params.max_seq_len]
            plen = min(len(prompt), len(full))
            labels = [-100] * plen + full[plen:]
            return {"input_ids": full, "attention_mask": [1] * len(full), "labels": labels}

        ds = datasets.Dataset.from_list(rows).map(_tokenize, remove_columns=["messages"])
        eval_ds = None
        if len(rows) >= _MIN_SAMPLES_FOR_HOLDOUT:
            split = ds.train_test_split(test_size=_EVAL_HOLDOUT, seed=params.seed)
            ds, eval_ds = split["train"], split["test"]

        out_dir.mkdir(parents=True, exist_ok=True)
        args = tf.TrainingArguments(
            output_dir=str(out_dir / "hf_runs"),
            num_train_epochs=params.epochs,
            max_steps=params.max_steps if params.max_steps > 0 else -1,
            learning_rate=params.lr,
            per_device_train_batch_size=params.batch_size,
            gradient_accumulation_steps=params.grad_accum,
            bf16=True,
            seed=params.seed,
            logging_steps=10,
            save_strategy="no",
            report_to=[],
        )
        collator = tf.DataCollatorForSeq2Seq(tokenizer, padding=True, label_pad_token_id=-100)
        trainer = tf.Trainer(
            model=model,
            args=args,
            train_dataset=ds,
            eval_dataset=eval_ds,
            data_collator=collator,
        )
        train_out = trainer.train()
        train_loss = round(float(getattr(train_out, "training_loss", 0.0)), 6)
        eval_loss = train_loss
        if eval_ds is not None:
            eval_loss = round(float(trainer.evaluate().get("eval_loss", train_loss)), 6)
        # Bounded 0..0.99 score so registry comparisons match MockTrainer's scale.
        eval_score = round(max(0.0, min(0.99, 1.0 / (1.0 + eval_loss))), 4)

        model.save_pretrained(str(out_dir))  # adapter_model.safetensors + adapter_config.json
        tokenizer.save_pretrained(str(out_dir))

        lora_config = {**asdict(params), "dataset_sha256": dataset_sha256(rows)}
        metrics = {"loss": train_loss, "eval_loss": eval_loss, "eval_score": eval_score}
        self._write_artifacts(out_dir, adapter_id, params, rows, metrics, deps)
        logger.info("GPU LoRA train done: adapter=%s eval_loss=%s", adapter_id, eval_loss)
        return TrainingResult(
            adapter_id=adapter_id,
            base_model=params.base_model_id,
            samples=len(rows),
            metrics=metrics,
            lora_config=lora_config,
        )

    def _write_artifacts(
        self,
        out_dir: Path,
        adapter_id: str,
        params: TrainParams,
        rows: list[dict],
        metrics: dict[str, float],
        deps: SimpleNamespace,
    ) -> None:
        """Persist the training dataset + a reproducibility metadata json beside weights."""
        (out_dir / "dataset.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=True) for r in rows) + "\n"
        )
        meta = {
            "adapter_id": adapter_id,
            "base_model": params.base_model_id,
            "source_model": params.source_model,
            "dataset_sha256": dataset_sha256(rows),
            "samples": len(rows),
            "hyperparams": asdict(params),
            "metrics": metrics,
            "versions": {
                "torch": getattr(deps.torch, "__version__", "?"),
                "transformers": getattr(deps.transformers, "__version__", "?"),
                "peft": getattr(deps.peft, "__version__", "?"),
            },
        }
        (out_dir / "training_metadata.json").write_text(json.dumps(meta, indent=2, sort_keys=True))
