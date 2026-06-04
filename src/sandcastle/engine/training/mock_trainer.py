"""Deterministic mock trainer - no GPU, stable metrics for the Self-Tune loop + tests."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sandcastle.engine.training.trainer import TrainingResult


class MockTrainer:
    """Produce a deterministic :class:`TrainingResult` from the training inputs.

    Adapter id and metrics are a pure function of (base_model, training_pairs) via an
    md5 of their canonical JSON - no RNG, no wall-clock - so the same data always
    yields the same result and tests are reproducible. ``eval_score`` rises with the
    number of samples so "more data -> better adapter" can be exercised deterministically.
    """

    async def train(
        self, base_model: str, training_pairs: list[dict], config: Any
    ) -> TrainingResult:
        canonical = json.dumps(
            {"base_model": base_model, "pairs": training_pairs},
            sort_keys=True,
            default=str,
        )
        digest = hashlib.md5(canonical.encode()).hexdigest()
        n = len(training_pairs)
        loss = round(int(digest[:4], 16) / 0xFFFF, 4)  # stable 0..1
        eval_score = round(min(0.99, 0.5 + n / 100.0 + int(digest[4:6], 16) / 5100.0), 4)
        return TrainingResult(
            adapter_id=f"mock-{base_model.replace('/', '_')}-{digest[:8]}",
            base_model=base_model,
            samples=n,
            metrics={"loss": loss, "eval_score": eval_score},
            lora_config={
                "r": getattr(config, "lora_r", 8),
                "alpha": getattr(config, "lora_alpha", 16),
                "lr": getattr(config, "lora_lr", 1e-4),
                "epochs": getattr(config, "lora_epochs", 3),
            },
        )
