"""Real GPU LoRA trainer - STUB. Needs torch + peft + transformers and a CUDA GPU.

The training kernel is hardware-gated and intentionally not implemented here: the rest
of Overnight Self-Tune (mutation, scoring, registry, A/B promotion) is exercised with
the deterministic MockTrainer. On a real DGX Spark, implement ``train`` with
``peft.LoraConfig`` over the locally-served base model and write the adapter weights to
the registry path. torch/peft are imported lazily so importing this module never
requires them.
"""

from __future__ import annotations

from typing import Any

from sandcastle.engine.training.trainer import TrainingResult


class GPUTrainer:
    """Hardware-gated LoRA trainer. Raises until implemented on real GPU hardware."""

    async def train(
        self, base_model: str, training_pairs: list[dict], config: Any
    ) -> TrainingResult:
        try:
            import peft  # noqa: F401
            import torch  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "GPUTrainer needs torch + peft (and a CUDA GPU). Install the training "
                "extras and run on a GPU host, or use trainer_backend='mock'."
            ) from e
        raise NotImplementedError(
            "Real LoRA training is not yet implemented - run on a DGX Spark with the "
            "training extras. Use trainer_backend='mock' to exercise the Self-Tune loop."
        )
