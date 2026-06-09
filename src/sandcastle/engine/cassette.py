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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CASSETTE_VERSION = 2

# Sentinel prev_hash for the first record in a chain (matches the audit trail).
CHAIN_GENESIS = "genesis"


def compute_record_hash(cache_key: str, record: dict[str, Any], prev_hash: str) -> str:
    """SHA-256 over the canonical JSON of a record plus its predecessor's hash.

    Including ``prev_hash`` links every record to the one before it, so any
    edit, removal, or reordering breaks every hash downstream of the change.
    """
    canonical = json.dumps(
        {"cache_key": cache_key, "record": record, "prev_hash": prev_hash},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def default_cassette_path(run_id: str) -> Path:
    """Canonical on-disk location of a run's auto-recorded cassette.

    Used by black-box compliance mode (auto-record) and by
    ``sandcastle audit verify <run-id>``.
    """
    from sandcastle.config import settings

    return Path(settings.data_dir) / "cassettes" / f"{run_id}.cassette.json"


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
        # Ordered hash chain: one entry per recorded step, each linking to the
        # previous via prev_hash (tamper-evident, see compute_record_hash).
        self.chain: list[dict[str, Any]] = []
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
        self.chain = data.get("chain", [])
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
        """Record a step output under its cache key (record mode only).

        Every put also appends a hash-chain entry: record_hash = SHA-256 over
        the canonical record + the previous entry's hash. Re-recording an
        existing key (rare: same cache key hit twice) rebuilds the chain so
        hashes always match the stored records.
        """
        if self.mode != "record" or not cache_key:
            return
        rerecord = cache_key in self.records
        self.records[cache_key] = {
            "output": output,
            "cost_usd": cost_usd,
            "model": model,
            "step_id": step_id,
        }
        if rerecord:
            self._rebuild_chain()
            return
        prev_hash = self.chain[-1]["record_hash"] if self.chain else CHAIN_GENESIS
        self.chain.append(
            {
                "index": len(self.chain),
                "cache_key": cache_key,
                "step_id": step_id,
                "prev_hash": prev_hash,
                "record_hash": compute_record_hash(cache_key, self.records[cache_key], prev_hash),
            }
        )

    def _rebuild_chain(self) -> None:
        """Recompute every chain hash in append order (after a re-record)."""
        prev_hash = CHAIN_GENESIS
        for entry in self.chain:
            entry["prev_hash"] = prev_hash
            entry["record_hash"] = compute_record_hash(
                entry["cache_key"], self.records.get(entry["cache_key"], {}), prev_hash
            )
            prev_hash = entry["record_hash"]

    def _canonical(self) -> str:
        return json.dumps(self.records, sort_keys=True, default=str)

    def signature(self) -> str:
        """SHA-256 over the canonical records - tamper-evidence, recomputable offline."""
        return hashlib.sha256(self._canonical().encode()).hexdigest()

    def verify(self) -> bool:
        """True if the loaded file's stored signature matches its records."""
        return self.meta.get("signature") == self.signature()

    # -- hash chain + keyed signature ------------------------------------------
    def chain_head(self) -> str:
        """The last record_hash in the chain (CHAIN_GENESIS when empty)."""
        return self.chain[-1]["record_hash"] if self.chain else CHAIN_GENESIS

    def _signing_payload(self) -> bytes:
        """Canonical bytes the keyed signature covers: chain head + length."""
        return json.dumps(
            {
                "chain_head": self.chain_head(),
                "record_count": len(self.chain),
                "version": CASSETTE_VERSION,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    def verify_chain(self) -> tuple[bool, int | None, str | None]:
        """Re-walk the hash chain and recompute every record hash.

        Returns (valid, first_broken_index, reason). first_broken_index is the
        chain index of the first entry that fails, or None when intact.
        """
        chain_keys = {e.get("cache_key") for e in self.chain}
        expected_prev = CHAIN_GENESIS
        for idx, entry in enumerate(self.chain):
            cache_key = entry.get("cache_key", "")
            if entry.get("prev_hash") != expected_prev:
                return False, idx, "prev_hash does not match the previous record_hash"
            record = self.records.get(cache_key)
            if record is None:
                return False, idx, f"chained record '{cache_key}' is missing from records"
            computed = compute_record_hash(cache_key, record, expected_prev)
            if computed != entry.get("record_hash"):
                return False, idx, "record_hash mismatch (record was modified)"
            expected_prev = entry["record_hash"]
        extra = set(self.records) - chain_keys
        if extra:
            return False, len(self.chain), f"records not covered by the chain: {sorted(extra)}"
        return True, None, None

    def verify_signature(self, key: str | None = None) -> bool | None:
        """Check the keyed signature over the chain head.

        Returns True/False for a signed cassette, or None when the file has
        no chain signature or no key is available to verify it.
        """
        from sandcastle.engine.signing import get_verifier

        sig = self.meta.get("chain_signature") or {}
        if not sig.get("value"):
            return None
        verifier = get_verifier(sig.get("alg", ""), key=key)
        if verifier is None:
            return None
        return verifier.verify(self._signing_payload(), sig["value"])

    def save(self) -> None:
        """Write the signed cassette file (record mode).

        The file always carries the hash chain and its head; when an audit
        key is configured (``AUDIT_KEY`` / ``SANDCASTLE_AUDIT_KEY``), the
        chain head is additionally signed so the whole file is bound to the
        key holder, not just internally consistent.
        """
        if self.mode != "record":
            return
        from sandcastle.engine.signing import get_signer

        meta: dict[str, Any] = {
            "version": CASSETTE_VERSION,
            "step_count": len(self.records),
            "total_cost_usd": round(
                sum(float(r.get("cost_usd", 0.0) or 0.0) for r in self.records.values()), 6
            ),
            "signature": self.signature(),
            "chain_head": self.chain_head(),
        }
        signer = get_signer()
        if signer is not None:
            meta["chain_signature"] = {
                "alg": signer.alg,
                "value": signer.sign(self._signing_payload()),
            }
        payload = {"meta": meta, "records": self.records, "chain": self.chain}
        self.meta = meta
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2, default=str))
        logger.info("Recorded cassette %s (%d steps)", self.path, len(self.records))


@dataclass
class CassetteVerification:
    """Result of verifying a cassette's hash chain and keyed signature."""

    valid: bool
    chain_ok: bool
    signature_ok: bool | None  # None = unsigned or no key available
    chain_length: int
    chain_head: str
    first_broken_index: int | None
    reason: str | None

    @property
    def status(self) -> str:
        return "PASS" if self.valid else "FAIL"


def verify_cassette(path: str | Path, key: str | None = None) -> CassetteVerification:
    """Verify a cassette file: re-walk the hash chain and check the signature.

    The Python API behind ``sandcastle audit verify``. A cassette is valid
    when the chain recomputes cleanly AND the keyed signature (if present
    and a key is available) verifies. *key* overrides the configured
    ``audit_key``.
    """
    store = CassetteStore.__new__(CassetteStore)  # bypass mode validation/load
    store.path = Path(path)
    store.mode = "replay"
    store.records = {}
    store.chain = []
    store.meta = {}
    store._signature_ok = None
    store.replay_hits = 0
    store.replay_misses = 0
    if not store.path.exists():
        return CassetteVerification(
            valid=False, chain_ok=False, signature_ok=None, chain_length=0,
            chain_head=CHAIN_GENESIS, first_broken_index=None,
            reason=f"cassette not found: {path}",
        )
    try:
        data = json.loads(store.path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return CassetteVerification(
            valid=False, chain_ok=False, signature_ok=None, chain_length=0,
            chain_head=CHAIN_GENESIS, first_broken_index=None,
            reason=f"unreadable cassette: {exc}",
        )
    store.records = data.get("records", {})
    store.chain = data.get("chain", [])
    store.meta = data.get("meta", {})

    # Legacy v1 cassette (recorded before the hash chain existed): no chain to
    # walk, so fall back to the whole-file signature it was recorded with.
    if not store.chain and store.records and "chain" not in data:
        chain_ok = store.verify()
        return CassetteVerification(
            valid=chain_ok,
            chain_ok=chain_ok,
            signature_ok=None,
            chain_length=0,
            chain_head=CHAIN_GENESIS,
            first_broken_index=None,
            reason=None if chain_ok else "legacy cassette signature mismatch (v1, no chain)",
        )

    chain_ok, broken_idx, reason = store.verify_chain()
    stored_head = store.meta.get("chain_head")
    if chain_ok and stored_head is not None and stored_head != store.chain_head():
        chain_ok = False
        broken_idx = len(store.chain)
        reason = "stored chain_head does not match the recomputed chain head"
    signature_ok = store.verify_signature(key=key)
    if signature_ok is False and reason is None:
        reason = "chain signature does not verify (wrong key or tampered head)"
    valid = chain_ok and signature_ok is not False
    return CassetteVerification(
        valid=valid,
        chain_ok=chain_ok,
        signature_ok=signature_ok,
        chain_length=len(store.chain),
        chain_head=store.chain_head(),
        first_broken_index=broken_idx,
        reason=reason,
    )


def read_attestation(path: str | Path) -> dict[str, Any] | None:
    """Lightweight attestation for API responses: {signed, chain_head} or None.

    ``signed`` is True only when the cassette carries a keyed chain signature
    that verifies with the currently configured audit key.
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        verification = verify_cassette(p)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to read cassette attestation %s: %s", p, exc)
        return None
    return {
        "signed": verification.signature_ok is True and verification.chain_ok,
        "chain_head": verification.chain_head,
    }
