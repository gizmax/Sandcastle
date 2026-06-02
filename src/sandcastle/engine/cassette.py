"""Deterministic cassettes: portable, signed record/replay of a workflow run.

A cassette is a single JSON file mapping each model step's deterministic cache key
(tenant + workflow + step_id + model + resolved_prompt, hashed) to the provider output
that step produced. Record a run once, then replay the same workflow offline at zero
cost, deterministically, and identically no matter which of the providers it targets -
the replay reads recorded outputs at the StepResult layer, strictly above every provider
client, so nothing calls a model.

Scope: v1 records the model-bearing ``standard`` prompt steps (where provider cost and
non-determinism live); deterministic ``code``/``http``/control-flow steps re-execute
live on replay (code is reproducible; you usually want http live or explicitly mocked).
Recording the explicit ``llm`` step type and full hybrid coverage is a follow-up.

This turns a run into a portable, tamper-evident fixture: free/fast/offline CI, golden
output diffing, deterministic eval as a regression gate, and attachable bug repros. It
generalizes the existing step-cache substrate (``_compute_cache_key`` / ``StepCache``)
from an in-database TTL cache into a shareable file.

Usage from the CLI: ``sandcastle run --local --record run.cassette.json <wf>`` then
``sandcastle run --local --replay run.cassette.json <wf>``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CASSETTE_VERSION = 1


class CassetteStore:
    """A portable record/replay store keyed by step cache key.

    mode "record": collect every cacheable step output and write a signed file on save().
    mode "replay": load a file and return recorded outputs; a key miss falls through to
    live execution (lenient) so a changed workflow does not hard-fail.
    """

    def __init__(self, path: str | Path, mode: str) -> None:
        if mode not in ("record", "replay"):
            raise ValueError(f"cassette mode must be 'record' or 'replay', got {mode!r}")
        self.path = Path(path)
        self.mode = mode
        self.records: dict[str, dict[str, Any]] = {}
        self.meta: dict[str, Any] = {}
        self._signature_ok: bool | None = None
        self.replay_hits = 0
        self.replay_misses = 0
        if mode == "replay":
            self._load()

    # -- replay --------------------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(f"cassette not found for replay: {self.path}")
        data = json.loads(self.path.read_text())
        self.records = data.get("records", {})
        self.meta = data.get("meta", {})
        self._signature_ok = self.verify()
        if self._signature_ok is False:
            msg = (
                f"Cassette signature mismatch for {self.path} - the file was modified "
                "after recording"
            )
            # STRICT mode (opt-in via SANDCASTLE_CASSETTE_STRICT=1) makes the
            # tamper-evidence enforceable: a modified cassette aborts the replay
            # instead of silently running stale/forged records. Recommended for any
            # deployment that treats a replay as an audit artifact.
            import os as _os

            if _os.getenv("SANDCASTLE_CASSETTE_STRICT", "").lower() in ("1", "true", "yes"):
                raise ValueError(f"{msg} (SANDCASTLE_CASSETTE_STRICT is enabled)")
            logger.warning("%s", msg)

    def get(self, cache_key: str) -> dict[str, Any] | None:
        """Return the recorded {output, cost_usd, model, step_id} for a key, or None."""
        if not cache_key:
            return None
        rec = self.records.get(cache_key)
        if rec is None:
            self.replay_misses += 1
            return None
        self.replay_hits += 1
        return rec

    # -- record --------------------------------------------------------------
    def put(self, cache_key: str, output: Any, cost_usd: float, model: str, step_id: str) -> None:
        """Record a step output under its cache key (record mode only)."""
        if self.mode != "record" or not cache_key:
            return
        self.records[cache_key] = {
            "output": output,
            "cost_usd": cost_usd,
            "model": model,
            "step_id": step_id,
        }

    def _canonical(self) -> str:
        return json.dumps(self.records, sort_keys=True, default=str)

    def signature(self) -> str:
        """SHA-256 over the canonical records - tamper-evidence, recomputable offline."""
        return hashlib.sha256(self._canonical().encode()).hexdigest()

    def verify(self) -> bool:
        """True if the loaded file's stored signature matches its records."""
        return self.meta.get("signature") == self.signature()

    def save(self) -> None:
        """Write the signed cassette file (record mode)."""
        if self.mode != "record":
            return
        payload = {
            "meta": {
                "version": CASSETTE_VERSION,
                "step_count": len(self.records),
                "total_cost_usd": round(
                    sum(float(r.get("cost_usd", 0.0) or 0.0) for r in self.records.values()), 6
                ),
                "signature": self.signature(),
            },
            "records": self.records,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2, default=str))
        logger.info("Recorded cassette %s (%d steps)", self.path, len(self.records))
