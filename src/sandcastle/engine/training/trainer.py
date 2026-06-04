"""Trainer interface + factory for Overnight Self-Tune LoRA fine-tuning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class TrainingResult:
    """Outcome of a ``Trainer.train`` call."""

    adapter_id: str
    base_model: str
    samples: int
    metrics: dict[str, float]  # e.g. {"loss": .., "eval_score": ..}
    lora_config: dict[str, Any]


class Trainer(Protocol):
    """A LoRA adapter trainer."""

    async def train(
        self, base_model: str, training_pairs: list[dict], config: Any
    ) -> TrainingResult:
        """Train an adapter on ``training_pairs`` and return its result."""
        ...


def get_trainer(backend: str | None = None) -> Trainer:
    """Return the configured trainer: ``"mock"`` (default) or ``"gpu"`` (stub).

    The backend defaults to ``settings.trainer_backend`` so the whole Self-Tune
    loop runs deterministically (mock) unless a real GPU trainer is requested.
    """
    if backend is None:
        from sandcastle.config import settings

        backend = settings.trainer_backend
    if backend == "mock":
        from sandcastle.engine.training.mock_trainer import MockTrainer

        return MockTrainer()
    if backend == "gpu":
        from sandcastle.engine.training.gpu_trainer import GPUTrainer

        return GPUTrainer()
    raise ValueError(f"unknown trainer_backend {backend!r} (expected 'mock' or 'gpu')")
