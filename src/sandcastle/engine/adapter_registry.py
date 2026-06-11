"""Filesystem registry of trained LoRA adapters (Overnight Self-Tune).

Each adapter lives under ``~/.sandcastle/adapters/<adapter_id>/`` with a
``metadata.json`` and a (stub) weights file. This is the single source of truth:
``providers.resolve_model`` reads it so a trained adapter becomes a callable
``adapter/<id>`` local model - no DB row or migration required.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def default_adapters_dir() -> Path:
    return Path.home() / ".sandcastle" / "adapters"


class AdapterRegistry:
    """Register / look up / prune trained adapters on disk."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else default_adapters_dir()

    def register(
        self,
        adapter_id: str,
        base_model: str,
        metrics: dict[str, Any],
        samples: int,
        lora_config: dict[str, Any],
        created_at: float = 0.0,
        dataset_hash: str | None = None,
        parent_adapter_id: str | None = None,
    ) -> Path:
        """Persist an adapter's metadata + a stub weights file; return its directory.

        ``dataset_hash`` fingerprints the training pairs and ``parent_adapter_id``
        records lineage (the adapter the workflow was routed to when this one was
        trained). Both are optional, so pre-existing callers and metadata files
        without them keep working unchanged.
        """
        d = self.root / adapter_id
        d.mkdir(parents=True, exist_ok=True)
        meta = {
            "adapter_id": adapter_id,
            "base_model": base_model,
            "metrics": metrics,
            "samples": samples,
            "lora_config": lora_config,
            "created_at": created_at,
            "dataset_hash": dataset_hash,
            "parent_adapter_id": parent_adapter_id,
        }
        (d / "metadata.json").write_text(json.dumps(meta, indent=2, sort_keys=True))
        # Real weights (GPUTrainer writes adapter_model.safetensors before registering)
        # win; the stub only marks mock-trained adapters so the layout stays uniform.
        if not (d / "adapter_model.safetensors").exists():
            (d / "adapter_model.safetensors.stub").write_text(adapter_id)
        return d

    def get(self, adapter_id: str) -> dict | None:
        """Return an adapter's metadata, or None if it is not registered."""
        meta = self.root / adapter_id / "metadata.json"
        if not meta.exists():
            return None
        try:
            return json.loads(meta.read_text())
        except (OSError, ValueError):
            return None

    def get_path(self, adapter_id: str) -> Path:
        """Return an adapter's directory, raising KeyError if unknown."""
        d = self.root / adapter_id
        if not (d / "metadata.json").exists():
            raise KeyError(f"unknown adapter {adapter_id!r}")
        return d

    def list(self) -> list[dict]:
        """All registered adapters, oldest first (by created_at)."""
        if not self.root.exists():
            return []
        out = [m for child in self.root.iterdir() if (m := self.get(child.name))]
        return sorted(out, key=lambda m: m.get("created_at", 0.0))

    def cleanup_old(self, keep_n: int = 5) -> int:
        """Delete all but the ``keep_n`` most recent adapters; return how many removed."""
        if keep_n <= 0:
            doomed = self.list()
        else:
            doomed = self.list()[:-keep_n]
        for m in doomed:
            d = self.root / m["adapter_id"]
            for f in d.glob("*"):
                f.unlink(missing_ok=True)
            d.rmdir()
        return len(doomed)
