"""Pluggable signing backends for the black-box audit trail.

The default backend is HMAC-SHA256 keyed by the ``audit_key`` setting
(``SANDCASTLE_AUDIT_KEY`` / ``AUDIT_KEY`` env var) - stdlib only, no extra
dependencies. The ``AuditSigner`` interface is deliberately minimal so an
asymmetric backend (e.g. Ed25519 via the optional ``cryptography`` extra)
can be registered later without touching any caller: signatures embed the
``alg`` identifier, and verification dispatches on it.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class AuditSigner(ABC):
    """A signing backend: produces and verifies detached signatures.

    Implementations must set ``alg`` to a stable identifier that is stored
    alongside every signature, so files remain verifiable after new
    backends are added.
    """

    alg: str

    @abstractmethod
    def sign(self, message: bytes) -> str:
        """Return a hex-encoded detached signature over *message*."""

    @abstractmethod
    def verify(self, message: bytes, signature: str) -> bool:
        """True if *signature* is a valid signature over *message*."""


class HmacSigner(AuditSigner):
    """HMAC-SHA256 signer keyed by a shared secret (stdlib only)."""

    alg = "hmac-sha256"

    def __init__(self, key: str) -> None:
        if not key:
            raise ValueError("HmacSigner requires a non-empty key")
        self._key = key.encode("utf-8")

    def sign(self, message: bytes) -> str:
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def verify(self, message: bytes, signature: str) -> bool:
        if not signature:
            return False
        return hmac.compare_digest(self.sign(message), signature)


# Registry of available backends by algorithm identifier. An asymmetric
# backend registers itself here (alg -> constructor taking the key material).
_BACKENDS: dict[str, type[AuditSigner]] = {
    HmacSigner.alg: HmacSigner,
}


def get_signer(key: str | None = None) -> AuditSigner | None:
    """Return the configured signer, or None when no audit key is set.

    *key* overrides the ``audit_key`` setting (used by the verify CLI's
    ``--key`` flag and by tests).
    """
    if key is None:
        from sandcastle.config import settings

        key = getattr(settings, "audit_key", "") or ""
    if not key:
        return None
    return HmacSigner(key)


def get_verifier(alg: str, key: str | None = None) -> AuditSigner | None:
    """Return a verifier for *alg*, or None when the backend or key is missing."""
    backend = _BACKENDS.get(alg)
    if backend is None:
        logger.warning("Unknown audit signature algorithm: %s", alg)
        return None
    if key is None:
        from sandcastle.config import settings

        key = getattr(settings, "audit_key", "") or ""
    if not key:
        return None
    try:
        return backend(key)
    except Exception as exc:
        logger.error("Failed to initialize %s verifier: %s", alg, exc)
        return None
