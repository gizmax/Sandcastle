"""Offline license key validation for Sandcastle.

License keys use Ed25519 signatures. The private key stays with the
publisher; only the public key is embedded here. No network calls are
made - validation is purely cryptographic.

Key format: ``sc_lic_<base64url_payload>.<base64url_signature>``
"""

from __future__ import annotations

import base64
import enum
import json
import logging
import threading
from dataclasses import dataclass
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class LicenseTier(str, enum.Enum):
    community = "community"
    pro = "pro"
    enterprise = "enterprise"


class LicenseStatus(str, enum.Enum):
    valid = "valid"
    expired = "expired"
    invalid = "invalid"
    missing = "missing"


# ---------------------------------------------------------------------------
# LicenseInfo
# ---------------------------------------------------------------------------

_PREFIX = "sc_lic_"

# Maximum accepted license key length to reject absurdly large inputs
_MAX_KEY_LENGTH = 4096


@dataclass(frozen=True)
class LicenseInfo:
    status: LicenseStatus
    tier: LicenseTier
    licensee: str = ""
    max_seats: int = 0
    expires: str = ""
    issued: str = ""
    license_id: str = ""
    detail: str = ""

    @property
    def is_production(self) -> bool:
        """True when the key is valid and tier is pro or enterprise."""
        return self.status == LicenseStatus.valid and self.tier in (
            LicenseTier.pro,
            LicenseTier.enterprise,
        )


COMMUNITY_MODE = LicenseInfo(
    status=LicenseStatus.missing,
    tier=LicenseTier.community,
    detail="No license key configured",
)

# ---------------------------------------------------------------------------
# Embedded Ed25519 public key (matches the private key held by publisher)
# ---------------------------------------------------------------------------

_PUBLIC_KEY_PEM = """\
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAMO3w0OZBMLhX38M46XHAW+vHaxRqUTXNYJoX/GvFggo=
-----END PUBLIC KEY-----
"""

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _b64url_decode(data: str) -> bytes:
    """Decode base64url with padding recovery."""
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data)


def validate_license_key(key: str) -> LicenseInfo:
    """Validate a license key string. Never raises - always returns LicenseInfo."""
    if not isinstance(key, str) or not key.strip():
        return COMMUNITY_MODE

    key = key.strip()

    # Reject absurdly large keys early to prevent DoS via base64 decode
    if len(key) > _MAX_KEY_LENGTH:
        return LicenseInfo(
            status=LicenseStatus.invalid,
            tier=LicenseTier.community,
            detail="Invalid key format: key exceeds maximum length",
        )

    # Check prefix
    if not key.startswith(_PREFIX):
        return LicenseInfo(
            status=LicenseStatus.invalid,
            tier=LicenseTier.community,
            detail="Invalid key format: missing sc_lic_ prefix",
        )

    body = key[len(_PREFIX):]

    # Split payload.signature
    parts = body.split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return LicenseInfo(
            status=LicenseStatus.invalid,
            tier=LicenseTier.community,
            detail="Invalid key format: expected payload.signature",
        )

    payload_b64, sig_b64 = parts

    # Decode payload
    try:
        payload_bytes = _b64url_decode(payload_b64)
    except Exception:
        return LicenseInfo(
            status=LicenseStatus.invalid,
            tier=LicenseTier.community,
            detail="Invalid key format: payload is not valid base64url",
        )

    # Decode signature
    try:
        sig_bytes = _b64url_decode(sig_b64)
    except Exception:
        return LicenseInfo(
            status=LicenseStatus.invalid,
            tier=LicenseTier.community,
            detail="Invalid key format: signature is not valid base64url",
        )

    # Verify Ed25519 signature
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
    except ImportError:
        logger.warning(
            "cryptography package not installed - cannot verify license key. "
            "Install with: pip install cryptography"
        )
        return LicenseInfo(
            status=LicenseStatus.invalid,
            tier=LicenseTier.community,
            detail="cryptography package not installed",
        )

    try:
        pub_key = load_pem_public_key(_PUBLIC_KEY_PEM.encode())
        assert isinstance(pub_key, Ed25519PublicKey)
        pub_key.verify(sig_bytes, payload_bytes)
    except Exception:
        return LicenseInfo(
            status=LicenseStatus.invalid,
            tier=LicenseTier.community,
            detail="Invalid signature",
        )

    # Parse payload JSON
    try:
        data: dict[str, Any] = json.loads(payload_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return LicenseInfo(
            status=LicenseStatus.invalid,
            tier=LicenseTier.community,
            detail="Invalid payload JSON",
        )

    if not isinstance(data, dict):
        return LicenseInfo(
            status=LicenseStatus.invalid,
            tier=LicenseTier.community,
            detail="Invalid payload: expected JSON object",
        )

    # Version check
    version = data.get("v", 0)
    if version != 1:
        return LicenseInfo(
            status=LicenseStatus.invalid,
            tier=LicenseTier.community,
            detail=f"Unsupported license version: {version}",
        )

    # Extract fields
    tier_str = data.get("tier", "community")
    try:
        tier = LicenseTier(tier_str)
    except ValueError:
        logger.warning("Unknown license tier '%s', falling back to community", tier_str)
        tier = LicenseTier.community

    licensee = str(data.get("licensee", ""))
    raw_seats = data.get("max_seats", 0)
    max_seats = max(0, int(raw_seats)) if isinstance(raw_seats, (int, float)) else 0
    exp_str = str(data.get("exp", ""))
    iat_str = str(data.get("iat", ""))
    license_id = str(data.get("id", ""))

    # Check expiry - malformed date is treated as invalid (not silently ignored)
    if exp_str:
        try:
            exp_date = date.fromisoformat(exp_str)
        except ValueError:
            logger.warning("Malformed expiry date '%s' in license", exp_str)
            return LicenseInfo(
                status=LicenseStatus.invalid,
                tier=tier,
                licensee=licensee,
                max_seats=max_seats,
                expires=exp_str,
                issued=iat_str,
                license_id=license_id,
                detail=f"Malformed expiry date: {exp_str}",
            )
        if exp_date < date.today():
            return LicenseInfo(
                status=LicenseStatus.expired,
                tier=tier,
                licensee=licensee,
                max_seats=max_seats,
                expires=exp_str,
                issued=iat_str,
                license_id=license_id,
                detail=f"License expired on {exp_str}",
            )

    return LicenseInfo(
        status=LicenseStatus.valid,
        tier=tier,
        licensee=licensee,
        max_seats=max_seats,
        expires=exp_str,
        issued=iat_str,
        license_id=license_id,
    )


# ---------------------------------------------------------------------------
# Cached singleton
# ---------------------------------------------------------------------------

_cached: LicenseInfo | None = None
_cache_lock = threading.Lock()
_cached_key_snapshot: str = ""


def get_license() -> LicenseInfo:
    """Return cached license info. Re-validates if LICENSE_KEY has changed.

    Thread-safe via module-level lock.
    """
    global _cached, _cached_key_snapshot

    from sandcastle.config import settings

    current_key = settings.license_key or ""

    with _cache_lock:
        if _cached is not None and current_key == _cached_key_snapshot:
            return _cached

        _cached = validate_license_key(current_key)
        _cached_key_snapshot = current_key
        return _cached


def reset_cache() -> None:
    """Clear cached license info (for testing)."""
    global _cached, _cached_key_snapshot
    with _cache_lock:
        _cached = None
        _cached_key_snapshot = ""
