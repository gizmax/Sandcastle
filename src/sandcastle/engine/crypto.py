"""Credential encryption at rest using Fernet symmetric encryption.

When CREDENTIAL_ENCRYPTION_KEY is set, tool connection credentials are
encrypted before storage and decrypted on read. When the key is unset,
credentials pass through as plaintext (backwards compatible).
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_fernet_instance = None
_fernet_checked = False
_fernet_key_snapshot: str = ""
_fernet_lock = threading.Lock()


def _get_fernet():
    """Lazy-init Fernet from CREDENTIAL_ENCRYPTION_KEY. Returns None if unset.

    Re-initializes if the key has changed since last check (e.g. via settings API).
    Thread-safe via module-level lock.
    """
    global _fernet_instance, _fernet_checked, _fernet_key_snapshot

    from sandcastle.config import settings

    current_key = settings.credential_encryption_key or ""

    with _fernet_lock:
        # Re-check if key changed (handles runtime key rotation via settings API)
        if _fernet_checked and current_key == _fernet_key_snapshot:
            return _fernet_instance

        _fernet_checked = True
        _fernet_key_snapshot = current_key

        if not current_key:
            _fernet_instance = None
            return None
        try:
            from cryptography.fernet import Fernet

            key = (
                current_key.encode()
                if isinstance(current_key, str)
                else current_key
            )
            _fernet_instance = Fernet(key)
        except Exception as exc:
            logger.error("Invalid CREDENTIAL_ENCRYPTION_KEY: %s", exc)
            _fernet_instance = None
        return _fernet_instance


def encrypt_credentials(data: dict[str, Any]) -> dict[str, Any] | str:
    """Encrypt credentials dict to a Fernet token string.

    Returns the original dict if no encryption key is configured.
    Raises TypeError if *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(f"encrypt_credentials expects a dict, got {type(data).__name__}")
    f = _get_fernet()
    if f is None:
        return data
    serialized = json.dumps(data).encode("utf-8")
    return f.encrypt(serialized).decode("utf-8")


def decrypt_credentials(stored: dict[str, Any] | str) -> dict[str, Any]:
    """Decrypt a Fernet token back to a credentials dict.

    If *stored* is already a dict (plaintext), returns it as-is.
    Non-dict, non-string values (None, int, list, etc.) are treated as
    empty credentials to prevent unexpected attribute errors.
    """
    if isinstance(stored, dict):
        return stored
    if not isinstance(stored, str):
        logger.warning(
            "decrypt_credentials received unexpected type %s, returning empty dict",
            type(stored).__name__,
        )
        return {}
    f = _get_fernet()
    if f is None:
        # No key configured but stored value is a string - likely encrypted
        # by a previous config. Return empty to avoid leaking tokens.
        logger.warning("Encrypted credentials found but no CREDENTIAL_ENCRYPTION_KEY set")
        return {}
    try:
        decrypted = f.decrypt(stored.encode("utf-8"))
        result = json.loads(decrypted)
        if not isinstance(result, dict):
            logger.error(
                "Decrypted credentials are not a dict (got %s), returning empty dict",
                type(result).__name__,
            )
            return {}
        return result
    except Exception as exc:
        logger.error("Failed to decrypt credentials: %s", exc)
        return {}
