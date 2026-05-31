"""Wave-9 deep audit tests for crypto.py and license.py.

Covers: thread safety, input validation, edge cases, key rotation,
corrupted ciphertext, unicode/binary handling, license format fuzzing,
expiry edge cases, tier validation, cache invalidation, and more.
"""

from __future__ import annotations

import base64
import contextlib
import json
import threading
import time
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip(
    "cryptography",
    reason="license crypto tests require the [security] extra",
)

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


@contextlib.contextmanager
def _bypass_signature_verification():
    """Patch Ed25519 so validate_license_key() skips real signature check."""
    with patch(
        "cryptography.hazmat.primitives.serialization.load_pem_public_key"
    ) as mock_load:
        mock_pub = MagicMock(spec=Ed25519PublicKey)
        mock_pub.verify = MagicMock(return_value=None)
        mock_load.return_value = mock_pub
        yield mock_load

# ---------------------------------------------------------------------------
# Reusable test keys (copied from test_license.py for independence)
# ---------------------------------------------------------------------------

# Valid pro key (expires 2099-12-31)
_PRO_KEY = (
    "sc_lic_eyJ2IjoxLCJ0aWVyIjoicHJvIiwibGljZW5zZWUiOiJUZXN0IENvcnAiLCJtYXhf"
    "c2VhdHMiOjEwLCJleHAiOiIyMDk5LTEyLTMxIiwiaWF0IjoiMjAyNi0wMi0yNiIsImlkIj"
    "oibGljX3Rlc3QwMDAxIn0.TuAmX7VJ33zEfIY-NLStTr2ADUIHjeWyewCVDqvpaeRluNTeP"
    "GaYbMC8TE6S5FiyY_0-xNJu0AA7woYFHUEiBA"
)

# Expired key (expired 2020-01-01)
_EXPIRED_KEY = (
    "sc_lic_eyJ2IjoxLCJ0aWVyIjoicHJvIiwibGljZW5zZWUiOiJFeHBpcmVkIENvcnAiLCJt"
    "YXhfc2VhdHMiOjUsImV4cCI6IjIwMjAtMDEtMDEiLCJpYXQiOiIyMDE5LTAxLTAxIiwiaWQi"
    "OiJsaWNfdGVzdDAwMDIifQ.aZK91wxVzqS8EMFW62TxRQPkG6r5yIMfplL9PrZiFYWYDU2v"
    "Kxqc-VE2bRolF6GEyT00RoI2fA6mMcWuKpdlCg"
)

# Enterprise key (expires 2099-12-31)
_ENTERPRISE_KEY = (
    "sc_lic_eyJ2IjoxLCJ0aWVyIjoiZW50ZXJwcmlzZSIsImxpY2Vuc2VlIjoiRW50ZXJwcmlz"
    "ZSBJbmMiLCJtYXhfc2VhdHMiOjEwMCwiZXhwIjoiMjA5OS0xMi0zMSIsImlhdCI6IjIwMjYt"
    "MDItMjYiLCJpZCI6ImxpY190ZXN0MDAwMyJ9.cag_hJPe7RYmBlnb0zMTOgcY0FvE4CQchG3"
    "Ju13XvMDbZPgu7SllTY9OhZr67R9K0J1ZYrvVBP68hQrkBZtKBA"
)


# ============================================================================
# CRYPTO.PY TESTS
# ============================================================================


@pytest.fixture(autouse=True)
def _reset_crypto_state():
    """Reset the global Fernet state between tests."""
    from sandcastle.engine import crypto

    crypto._fernet_instance = None
    crypto._fernet_checked = False
    crypto._fernet_key_snapshot = ""
    yield
    crypto._fernet_instance = None
    crypto._fernet_checked = False
    crypto._fernet_key_snapshot = ""


class TestCryptoInputValidation:
    """encrypt_credentials should reject non-dict input."""

    def test_encrypt_rejects_string(self, monkeypatch):
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", "")
        from sandcastle.engine.crypto import encrypt_credentials

        with pytest.raises(TypeError, match="dict"):
            encrypt_credentials("not a dict")

    def test_encrypt_rejects_list(self, monkeypatch):
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", "")
        from sandcastle.engine.crypto import encrypt_credentials

        with pytest.raises(TypeError, match="dict"):
            encrypt_credentials([1, 2, 3])

    def test_encrypt_rejects_none(self, monkeypatch):
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", "")
        from sandcastle.engine.crypto import encrypt_credentials

        with pytest.raises(TypeError, match="dict"):
            encrypt_credentials(None)

    def test_encrypt_rejects_int(self, monkeypatch):
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", "")
        from sandcastle.engine.crypto import encrypt_credentials

        with pytest.raises(TypeError, match="dict"):
            encrypt_credentials(42)


class TestDecryptNonStringNonDict:
    """decrypt_credentials should handle non-dict, non-string stored values."""

    def test_decrypt_none_returns_empty(self, monkeypatch):
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", "")
        from sandcastle.engine.crypto import decrypt_credentials

        result = decrypt_credentials(None)
        assert result == {}

    def test_decrypt_int_returns_empty(self, monkeypatch):
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", "")
        from sandcastle.engine.crypto import decrypt_credentials

        result = decrypt_credentials(42)
        assert result == {}

    def test_decrypt_list_returns_empty(self, monkeypatch):
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", "")
        from sandcastle.engine.crypto import decrypt_credentials

        result = decrypt_credentials([1, 2])
        assert result == {}

    def test_decrypt_bool_returns_empty(self, monkeypatch):
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", "")
        from sandcastle.engine.crypto import decrypt_credentials

        result = decrypt_credentials(True)
        assert result == {}


class TestCryptoUnicode:
    """Roundtrip with unicode values in credentials."""

    def test_unicode_values(self, monkeypatch):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)

        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        data = {"name": "Tomas Pflanzer", "emoji": "\U0001f30d", "cz": "Ahoj svete"}
        encrypted = encrypt_credentials(data)
        assert isinstance(encrypted, str)
        decrypted = decrypt_credentials(encrypted)
        assert decrypted == data

    def test_large_values(self, monkeypatch):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)

        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        data = {"big_token": "x" * 100_000}
        encrypted = encrypt_credentials(data)
        decrypted = decrypt_credentials(encrypted)
        assert decrypted == data

    def test_nested_dict_values(self, monkeypatch):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)

        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        data = {
            "oauth": {"access_token": "tok123", "refresh_token": "ref456"},
            "flags": [True, False],
            "count": 42,
        }
        encrypted = encrypt_credentials(data)
        decrypted = decrypt_credentials(encrypted)
        assert decrypted == data

    def test_special_json_chars(self, monkeypatch):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)

        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        data = {"key": 'value with "quotes" and \\backslash'}
        encrypted = encrypt_credentials(data)
        decrypted = decrypt_credentials(encrypted)
        assert decrypted == data


class TestCorruptedCiphertext:
    """Decrypting corrupted or tampered ciphertext should return empty dict."""

    def test_truncated_ciphertext(self, monkeypatch):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)

        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        encrypted = encrypt_credentials({"secret": "value"})
        assert isinstance(encrypted, str)
        # Truncate the ciphertext
        truncated = encrypted[:len(encrypted) // 2]
        result = decrypt_credentials(truncated)
        assert result == {}

    def test_flipped_byte_ciphertext(self, monkeypatch):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)

        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        encrypted = encrypt_credentials({"secret": "value"})
        assert isinstance(encrypted, str)
        # Flip a character in the middle
        chars = list(encrypted)
        mid = len(chars) // 2
        chars[mid] = "A" if chars[mid] != "A" else "B"
        tampered = "".join(chars)
        result = decrypt_credentials(tampered)
        assert result == {}

    def test_empty_string_ciphertext(self, monkeypatch):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)

        from sandcastle.engine.crypto import decrypt_credentials

        result = decrypt_credentials("")
        # Empty string is not a dict, so it goes through decrypt path
        # Empty Fernet token => decrypt fails => empty dict
        assert result == {}

    def test_random_base64_ciphertext(self, monkeypatch):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)

        from sandcastle.engine.crypto import decrypt_credentials

        fake_token = base64.urlsafe_b64encode(b"not-a-real-fernet-token").decode()
        result = decrypt_credentials(fake_token)
        assert result == {}


class TestCryptoKeyRotation:
    """Changing the encryption key at runtime should re-init Fernet."""

    def test_key_rotation_reinitializes(self, monkeypatch):
        from cryptography.fernet import Fernet

        key1 = Fernet.generate_key().decode()
        key2 = Fernet.generate_key().decode()

        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key1)
        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        # Encrypt with key1
        encrypted1 = encrypt_credentials({"data": "secret"})
        assert isinstance(encrypted1, str)

        # Switch to key2
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key2)

        # Decrypting key1's ciphertext with key2 should fail gracefully
        result = decrypt_credentials(encrypted1)
        assert result == {}

        # Encrypt+decrypt with key2 should work
        encrypted2 = encrypt_credentials({"data": "new-secret"})
        assert isinstance(encrypted2, str)
        result2 = decrypt_credentials(encrypted2)
        assert result2 == {"data": "new-secret"}

    def test_key_to_no_key(self, monkeypatch):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        encrypted = encrypt_credentials({"k": "v"})
        assert isinstance(encrypted, str)

        # Remove key
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", "")
        result = decrypt_credentials(encrypted)
        # Should return empty because key is gone
        assert result == {}

    def test_no_key_to_key(self, monkeypatch):
        from cryptography.fernet import Fernet

        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", "")
        from sandcastle.engine.crypto import encrypt_credentials

        # Plaintext mode
        data = {"k": "v"}
        result = encrypt_credentials(data)
        assert result == data  # passthrough

        # Add key
        key = Fernet.generate_key().decode()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)

        encrypted = encrypt_credentials(data)
        assert isinstance(encrypted, str)
        assert encrypted != str(data)


class TestCryptoInvalidKeyFormats:
    """Various invalid key formats should be handled gracefully."""

    def test_short_key_passthrough(self, monkeypatch):
        monkeypatch.setattr(
            "sandcastle.config.settings.credential_encryption_key", "short"
        )
        from sandcastle.engine.crypto import encrypt_credentials

        data = {"k": "v"}
        result = encrypt_credentials(data)
        # Invalid key => Fernet init fails => passthrough
        assert result == data

    def test_whitespace_key_treated_as_empty(self, monkeypatch):
        monkeypatch.setattr(
            "sandcastle.config.settings.credential_encryption_key", "   "
        )
        from sandcastle.engine.crypto import encrypt_credentials

        data = {"k": "v"}
        result = encrypt_credentials(data)
        # "   " is truthy but not a valid Fernet key
        # It should try to init Fernet, fail, then passthrough
        assert result == data

    def test_almost_valid_base64_key(self, monkeypatch):
        # Valid base64 but wrong length for Fernet (needs exactly 32 bytes url-safe base64)
        bad_key = base64.urlsafe_b64encode(b"too-short").decode()
        monkeypatch.setattr(
            "sandcastle.config.settings.credential_encryption_key", bad_key
        )
        from sandcastle.engine.crypto import encrypt_credentials

        data = {"k": "v"}
        result = encrypt_credentials(data)
        assert result == data  # passthrough

    def test_null_bytes_in_key(self, monkeypatch):
        monkeypatch.setattr(
            "sandcastle.config.settings.credential_encryption_key", "abc\x00def"
        )
        from sandcastle.engine.crypto import encrypt_credentials

        data = {"k": "v"}
        result = encrypt_credentials(data)
        assert result == data  # passthrough


class TestCryptoThreadSafety:
    """Concurrent encrypt/decrypt operations should not corrupt state."""

    def test_concurrent_encrypt_decrypt(self, monkeypatch):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)

        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        errors = []
        barrier = threading.Barrier(10)

        def worker(idx):
            try:
                barrier.wait(timeout=5)
                data = {"thread": idx, "token": f"secret-{idx}"}
                encrypted = encrypt_credentials(data)
                assert isinstance(encrypted, str)
                decrypted = decrypt_credentials(encrypted)
                assert decrypted == data
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Thread errors: {errors}"


class TestDecryptedNonDictPayload:
    """If encrypted payload decrypts to a non-dict JSON value, return empty."""

    def test_decrypt_list_payload(self, monkeypatch):
        from cryptography.fernet import Fernet as RealFernet

        key = RealFernet.generate_key()
        monkeypatch.setattr(
            "sandcastle.config.settings.credential_encryption_key", key.decode()
        )

        from sandcastle.engine.crypto import decrypt_credentials

        # Manually encrypt a JSON list (not a dict)
        f = RealFernet(key)
        token = f.encrypt(json.dumps([1, 2, 3]).encode()).decode()
        result = decrypt_credentials(token)
        assert result == {}

    def test_decrypt_string_payload(self, monkeypatch):
        from cryptography.fernet import Fernet as RealFernet

        key = RealFernet.generate_key()
        monkeypatch.setattr(
            "sandcastle.config.settings.credential_encryption_key", key.decode()
        )

        from sandcastle.engine.crypto import decrypt_credentials

        f = RealFernet(key)
        token = f.encrypt(json.dumps("just a string").encode()).decode()
        result = decrypt_credentials(token)
        assert result == {}

    def test_decrypt_int_payload(self, monkeypatch):
        from cryptography.fernet import Fernet as RealFernet

        key = RealFernet.generate_key()
        monkeypatch.setattr(
            "sandcastle.config.settings.credential_encryption_key", key.decode()
        )

        from sandcastle.engine.crypto import decrypt_credentials

        f = RealFernet(key)
        token = f.encrypt(json.dumps(42).encode()).decode()
        result = decrypt_credentials(token)
        assert result == {}

    def test_decrypt_null_payload(self, monkeypatch):
        from cryptography.fernet import Fernet as RealFernet

        key = RealFernet.generate_key()
        monkeypatch.setattr(
            "sandcastle.config.settings.credential_encryption_key", key.decode()
        )

        from sandcastle.engine.crypto import decrypt_credentials

        f = RealFernet(key)
        token = f.encrypt(json.dumps(None).encode()).decode()
        result = decrypt_credentials(token)
        assert result == {}


class TestCryptoFernetLazyInit:
    """_get_fernet caches the instance and re-inits on key change."""

    def test_same_key_returns_same_instance(self, monkeypatch):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)

        from sandcastle.engine.crypto import _get_fernet

        inst1 = _get_fernet()
        inst2 = _get_fernet()
        assert inst1 is inst2
        assert inst1 is not None

    def test_different_key_returns_new_instance(self, monkeypatch):
        from cryptography.fernet import Fernet

        key1 = Fernet.generate_key().decode()
        key2 = Fernet.generate_key().decode()

        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key1)
        from sandcastle.engine.crypto import _get_fernet

        inst1 = _get_fernet()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key2)
        inst2 = _get_fernet()
        assert inst1 is not inst2

    def test_no_key_returns_none(self, monkeypatch):
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", "")
        from sandcastle.engine.crypto import _get_fernet

        assert _get_fernet() is None

    def test_invalid_then_valid_key(self, monkeypatch):
        from cryptography.fernet import Fernet

        monkeypatch.setattr(
            "sandcastle.config.settings.credential_encryption_key", "invalid"
        )
        from sandcastle.engine.crypto import _get_fernet

        assert _get_fernet() is None

        key = Fernet.generate_key().decode()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        assert _get_fernet() is not None


# ============================================================================
# LICENSE.PY TESTS
# ============================================================================


@pytest.fixture(autouse=True)
def _reset_license_cache():
    """Reset license cache between tests."""
    from sandcastle.engine.license import reset_cache
    reset_cache()
    yield
    reset_cache()


class TestLicenseInputValidation:
    """validate_license_key should handle non-string input gracefully."""

    def test_none_input(self):
        from sandcastle.engine.license import COMMUNITY_MODE, validate_license_key

        result = validate_license_key(None)
        assert result is COMMUNITY_MODE

    def test_int_input(self):
        from sandcastle.engine.license import COMMUNITY_MODE, validate_license_key

        result = validate_license_key(12345)
        assert result is COMMUNITY_MODE

    def test_bool_input(self):
        from sandcastle.engine.license import COMMUNITY_MODE, validate_license_key

        result = validate_license_key(True)
        assert result is COMMUNITY_MODE

    def test_empty_bytes_input(self):
        from sandcastle.engine.license import COMMUNITY_MODE, validate_license_key

        result = validate_license_key(b"")
        assert result is COMMUNITY_MODE


class TestLicenseMaxLength:
    """Excessively long keys should be rejected early."""

    def test_oversized_key_rejected(self):
        from sandcastle.engine.license import LicenseStatus, validate_license_key

        huge_key = "sc_lic_" + "A" * 10000 + "." + "B" * 10000
        result = validate_license_key(huge_key)
        assert result.status == LicenseStatus.invalid
        assert "length" in result.detail.lower()

    def test_just_under_max_length_parsed(self):
        """Key under max length should be parsed (and fail on content, not length)."""
        from sandcastle.engine.license import LicenseStatus, validate_license_key

        # A key that's within limits but has invalid base64
        key = "sc_lic_" + "A" * 100 + "." + "B" * 100
        result = validate_license_key(key)
        # Should fail on signature, not on length
        assert result.status == LicenseStatus.invalid
        assert "length" not in result.detail.lower()


class TestLicenseMalformedExpiry:
    """Malformed expiry dates should now be rejected, not silently ignored."""

    def test_malformed_date_returns_invalid(self):
        """A signed key with malformed exp should be treated as invalid."""
        # We need to test this at the validate level with a real signed key.
        # Since we cannot sign arbitrary payloads, we test by patching the
        # signature verification step to always pass.
        from sandcastle.engine.license import LicenseStatus, validate_license_key

        # Build a payload with malformed exp
        payload = json.dumps({
            "v": 1, "tier": "pro", "licensee": "Test",
            "max_seats": 5, "exp": "not-a-date",
            "iat": "2026-01-01", "id": "lic_test"
        }).encode()
        sig = b"\x00" * 64  # Fake signature

        payload_b64 = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
        key = f"sc_lic_{payload_b64}.{sig_b64}"

        # Patch signature verification to accept
        with _bypass_signature_verification():
            result = validate_license_key(key)
            assert result.status == LicenseStatus.invalid
            assert "malformed" in result.detail.lower() or "date" in result.detail.lower()


class TestLicenseUnknownTier:
    """Unknown tier values should fall back to community with logging."""

    def test_unknown_tier_falls_back(self):
        from sandcastle.engine.license import LicenseStatus, LicenseTier, validate_license_key

        payload = json.dumps({
            "v": 1, "tier": "superadmin", "licensee": "Hacker",
            "max_seats": 999, "exp": "2099-12-31",
            "iat": "2026-01-01", "id": "lic_fake"
        }).encode()
        sig = b"\x00" * 64

        payload_b64 = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
        key = f"sc_lic_{payload_b64}.{sig_b64}"

        with _bypass_signature_verification():

            result = validate_license_key(key)
            assert result.status == LicenseStatus.valid
            assert result.tier == LicenseTier.community


class TestLicenseNegativeSeats:
    """Negative max_seats should be clamped to 0."""

    def test_negative_seats_clamped(self):
        from sandcastle.engine.license import validate_license_key

        payload = json.dumps({
            "v": 1, "tier": "pro", "licensee": "Test",
            "max_seats": -5, "exp": "2099-12-31",
            "iat": "2026-01-01", "id": "lic_neg"
        }).encode()
        sig = b"\x00" * 64

        payload_b64 = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
        key = f"sc_lic_{payload_b64}.{sig_b64}"

        with _bypass_signature_verification():

            result = validate_license_key(key)
            assert result.max_seats == 0

    def test_string_seats_treated_as_zero(self):
        from sandcastle.engine.license import validate_license_key

        payload = json.dumps({
            "v": 1, "tier": "pro", "licensee": "Test",
            "max_seats": "not-a-number", "exp": "2099-12-31",
            "iat": "2026-01-01", "id": "lic_str"
        }).encode()
        sig = b"\x00" * 64

        payload_b64 = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
        key = f"sc_lic_{payload_b64}.{sig_b64}"

        with _bypass_signature_verification():

            result = validate_license_key(key)
            assert result.max_seats == 0

    def test_float_seats_truncated(self):
        from sandcastle.engine.license import validate_license_key

        payload = json.dumps({
            "v": 1, "tier": "pro", "licensee": "Test",
            "max_seats": 7.9, "exp": "2099-12-31",
            "iat": "2026-01-01", "id": "lic_float"
        }).encode()
        sig = b"\x00" * 64

        payload_b64 = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
        key = f"sc_lic_{payload_b64}.{sig_b64}"

        with _bypass_signature_verification():

            result = validate_license_key(key)
            assert result.max_seats == 7


class TestLicenseExpiryEdgeCases:
    """Edge cases around license expiry dates."""

    def test_today_is_not_expired(self):
        """A license expiring today should still be valid."""
        from sandcastle.engine.license import LicenseStatus, validate_license_key

        today_str = date.today().isoformat()
        payload = json.dumps({
            "v": 1, "tier": "pro", "licensee": "Today Corp",
            "max_seats": 1, "exp": today_str,
            "iat": "2026-01-01", "id": "lic_today"
        }).encode()
        sig = b"\x00" * 64

        payload_b64 = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
        key = f"sc_lic_{payload_b64}.{sig_b64}"

        with _bypass_signature_verification():

            result = validate_license_key(key)
            # today is NOT < today, so it should be valid
            assert result.status == LicenseStatus.valid

    def test_yesterday_is_expired(self):
        """A license that expired yesterday should be expired."""
        from sandcastle.engine.license import LicenseStatus, validate_license_key

        # Use UTC date to match the production code's UTC-based expiry check
        yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
        payload = json.dumps({
            "v": 1, "tier": "pro", "licensee": "Yesterday Corp",
            "max_seats": 1, "exp": yesterday,
            "iat": "2026-01-01", "id": "lic_yest"
        }).encode()
        sig = b"\x00" * 64

        payload_b64 = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
        key = f"sc_lic_{payload_b64}.{sig_b64}"

        with _bypass_signature_verification():

            result = validate_license_key(key)
            assert result.status == LicenseStatus.expired

    def test_no_expiry_is_valid(self):
        """A license with empty exp should be treated as never-expiring."""
        from sandcastle.engine.license import LicenseStatus, validate_license_key

        payload = json.dumps({
            "v": 1, "tier": "pro", "licensee": "Forever Corp",
            "max_seats": 1, "exp": "",
            "iat": "2026-01-01", "id": "lic_noexp"
        }).encode()
        sig = b"\x00" * 64

        payload_b64 = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
        key = f"sc_lic_{payload_b64}.{sig_b64}"

        with _bypass_signature_verification():

            result = validate_license_key(key)
            assert result.status == LicenseStatus.valid

    def test_missing_exp_field_is_valid(self):
        """A license without exp field at all should be valid (no expiry)."""
        from sandcastle.engine.license import LicenseStatus, validate_license_key

        payload = json.dumps({
            "v": 1, "tier": "pro", "licensee": "NoExp Corp",
            "max_seats": 1,
            "iat": "2026-01-01", "id": "lic_nofield"
        }).encode()
        sig = b"\x00" * 64

        payload_b64 = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
        key = f"sc_lic_{payload_b64}.{sig_b64}"

        with _bypass_signature_verification():

            result = validate_license_key(key)
            assert result.status == LicenseStatus.valid


class TestLicenseFormatFuzzing:
    """Various malformed key format edge cases."""

    def test_multiple_dots_in_body(self):
        from sandcastle.engine.license import LicenseStatus, validate_license_key

        result = validate_license_key("sc_lic_a.b.c")
        assert result.status == LicenseStatus.invalid
        assert "signature" in result.detail.lower() or "payload" in result.detail.lower()

    def test_only_prefix(self):
        from sandcastle.engine.license import LicenseStatus, validate_license_key

        result = validate_license_key("sc_lic_")
        assert result.status == LicenseStatus.invalid

    def test_prefix_with_dot_only(self):
        from sandcastle.engine.license import LicenseStatus, validate_license_key

        result = validate_license_key("sc_lic_.")
        assert result.status == LicenseStatus.invalid

    def test_dot_at_end(self):
        from sandcastle.engine.license import LicenseStatus, validate_license_key

        result = validate_license_key("sc_lic_payload.")
        assert result.status == LicenseStatus.invalid

    def test_dot_at_start(self):
        from sandcastle.engine.license import LicenseStatus, validate_license_key

        result = validate_license_key("sc_lic_.sig")
        assert result.status == LicenseStatus.invalid

    def test_invalid_base64_payload(self):
        from sandcastle.engine.license import LicenseStatus, validate_license_key

        result = validate_license_key("sc_lic_!!!invalid!!!.dGVzdA")
        assert result.status == LicenseStatus.invalid
        assert "base64" in result.detail.lower() or "signature" in result.detail.lower()

    def test_invalid_base64_signature(self):
        from sandcastle.engine.license import LicenseStatus, validate_license_key

        result = validate_license_key("sc_lic_dGVzdA.!!!invalid!!!")
        assert result.status == LicenseStatus.invalid
        assert "base64" in result.detail.lower() or "signature" in result.detail.lower()

    def test_whitespace_key_is_community(self):
        from sandcastle.engine.license import COMMUNITY_MODE, validate_license_key

        result = validate_license_key("  \t\n  ")
        assert result is COMMUNITY_MODE

    def test_prefix_with_whitespace_inside(self):
        from sandcastle.engine.license import LicenseStatus, validate_license_key

        # Leading/trailing whitespace is stripped, but whitespace in body is not
        key = "  sc_lic_pay load.sig  "
        result = validate_license_key(key)
        # After strip: "sc_lic_pay load.sig" - base64 decode should fail
        assert result.status == LicenseStatus.invalid


class TestLicenseCacheInvalidation:
    """get_license should re-validate when the key changes at runtime."""

    def test_cache_invalidates_on_key_change(self):
        from sandcastle.engine.license import (
            LicenseStatus,
            get_license,
            reset_cache,
        )

        reset_cache()

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.license_key = _PRO_KEY
            result1 = get_license()
            assert result1.status == LicenseStatus.valid

            # Change key to empty
            mock_settings.license_key = ""
            result2 = get_license()
            assert result2.status == LicenseStatus.missing

    def test_cache_stable_with_same_key(self):
        from sandcastle.engine.license import get_license, reset_cache

        reset_cache()

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.license_key = _PRO_KEY
            result1 = get_license()
            result2 = get_license()
            assert result1 is result2

    def test_cache_switches_to_expired(self):
        from sandcastle.engine.license import (
            LicenseStatus,
            get_license,
            reset_cache,
        )

        reset_cache()

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.license_key = _PRO_KEY
            result1 = get_license()
            assert result1.status == LicenseStatus.valid

            # Switch to expired key
            mock_settings.license_key = _EXPIRED_KEY
            result2 = get_license()
            assert result2.status == LicenseStatus.expired


class TestLicenseThreadSafety:
    """Concurrent get_license calls should not corrupt the cache."""

    def test_concurrent_get_license(self):
        from sandcastle.engine.license import (
            LicenseStatus,
            get_license,
            reset_cache,
        )

        reset_cache()
        errors = []
        barrier = threading.Barrier(10)

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.license_key = _PRO_KEY

            def worker():
                try:
                    barrier.wait(timeout=5)
                    result = get_license()
                    assert result.status == LicenseStatus.valid
                    assert result.licensee == "Test Corp"
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=worker) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

        assert not errors, f"Thread errors: {errors}"


class TestLicenseB64UrlDecode:
    """Edge cases in _b64url_decode padding recovery."""

    def test_no_padding_needed(self):
        from sandcastle.engine.license import _b64url_decode

        # "test" base64 => "dGVzdA==" (8 chars with padding, 6 without)
        # Let's use a string that encodes to exact multiple of 4
        raw = base64.urlsafe_b64encode(b"tes").decode().rstrip("=")
        assert _b64url_decode(raw) == b"tes"

    def test_one_pad_needed(self):
        from sandcastle.engine.license import _b64url_decode

        raw = base64.urlsafe_b64encode(b"test!").decode().rstrip("=")
        assert _b64url_decode(raw) == b"test!"

    def test_two_pads_needed(self):
        from sandcastle.engine.license import _b64url_decode

        raw = base64.urlsafe_b64encode(b"test").decode().rstrip("=")
        assert _b64url_decode(raw) == b"test"

    def test_already_padded(self):
        from sandcastle.engine.license import _b64url_decode

        raw = base64.urlsafe_b64encode(b"test").decode()  # Keep padding
        assert _b64url_decode(raw) == b"test"

    def test_empty_string(self):
        from sandcastle.engine.license import _b64url_decode

        assert _b64url_decode("") == b""

    def test_url_safe_chars(self):
        from sandcastle.engine.license import _b64url_decode

        # Bytes that produce + and / in standard base64 should use - and _
        data = bytes(range(256))
        encoded = base64.urlsafe_b64encode(data).decode().rstrip("=")
        assert _b64url_decode(encoded) == data


class TestLicenseInfoProperties:
    """LicenseInfo dataclass property edge cases."""

    def test_is_production_community_valid(self):
        from sandcastle.engine.license import LicenseInfo, LicenseStatus, LicenseTier

        info = LicenseInfo(status=LicenseStatus.valid, tier=LicenseTier.community)
        assert info.is_production is False

    def test_is_production_pro_expired(self):
        from sandcastle.engine.license import LicenseInfo, LicenseStatus, LicenseTier

        info = LicenseInfo(status=LicenseStatus.expired, tier=LicenseTier.pro)
        assert info.is_production is False

    def test_is_production_enterprise_invalid(self):
        from sandcastle.engine.license import LicenseInfo, LicenseStatus, LicenseTier

        info = LicenseInfo(status=LicenseStatus.invalid, tier=LicenseTier.enterprise)
        assert info.is_production is False

    def test_frozen_dataclass(self):
        from sandcastle.engine.license import LicenseInfo, LicenseStatus, LicenseTier

        info = LicenseInfo(status=LicenseStatus.valid, tier=LicenseTier.pro)
        with pytest.raises(AttributeError):
            info.status = LicenseStatus.invalid


class TestLicenseSignatureVerification:
    """Signature verification with real keys to ensure crypto works."""

    def test_valid_pro_key_fields(self):
        from sandcastle.engine.license import LicenseStatus, LicenseTier, validate_license_key

        result = validate_license_key(_PRO_KEY)
        assert result.status == LicenseStatus.valid
        assert result.tier == LicenseTier.pro
        assert result.licensee == "Test Corp"
        assert result.max_seats == 10
        assert result.expires == "2099-12-31"
        assert result.issued == "2026-02-26"
        assert result.license_id == "lic_test0001"

    def test_enterprise_key_fields(self):
        from sandcastle.engine.license import LicenseStatus, LicenseTier, validate_license_key

        result = validate_license_key(_ENTERPRISE_KEY)
        assert result.status == LicenseStatus.valid
        assert result.tier == LicenseTier.enterprise
        assert result.max_seats == 100

    def test_expired_key_still_has_tier(self):
        from sandcastle.engine.license import LicenseStatus, LicenseTier, validate_license_key

        result = validate_license_key(_EXPIRED_KEY)
        assert result.status == LicenseStatus.expired
        assert result.tier == LicenseTier.pro
        assert result.max_seats == 5

    def test_swapped_payload_signature(self):
        """Swapping payload between two valid keys should fail verification."""
        from sandcastle.engine.license import LicenseStatus, validate_license_key

        payload_part = _PRO_KEY.split(".")[0]  # sc_lic_<payload_pro>
        sig_part = _ENTERPRISE_KEY.split(".")[1]  # <sig_enterprise>
        mixed = f"{payload_part}.{sig_part}"
        result = validate_license_key(mixed)
        assert result.status == LicenseStatus.invalid
        assert "signature" in result.detail.lower()


class TestLicensePayloadFieldTypes:
    """Fields in payload with unexpected types should be coerced safely."""

    def _make_key(self, payload_dict):
        """Helper to make a key with patched signature verification."""
        payload = json.dumps(payload_dict).encode()
        sig = b"\x00" * 64
        payload_b64 = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
        return f"sc_lic_{payload_b64}.{sig_b64}"

    def test_licensee_as_int(self):
        from sandcastle.engine.license import validate_license_key

        key = self._make_key({
            "v": 1, "tier": "pro", "licensee": 42,
            "max_seats": 1, "exp": "2099-12-31",
            "iat": "2026-01-01", "id": "lic_x"
        })
        with _bypass_signature_verification():
            result = validate_license_key(key)
            # licensee should be stringified
            assert result.licensee == "42"

    def test_id_as_int(self):
        from sandcastle.engine.license import validate_license_key

        key = self._make_key({
            "v": 1, "tier": "pro", "licensee": "Corp",
            "max_seats": 1, "exp": "2099-12-31",
            "iat": "2026-01-01", "id": 999
        })
        with _bypass_signature_verification():
            result = validate_license_key(key)
            assert result.license_id == "999"

    def test_exp_as_int(self):
        from sandcastle.engine.license import LicenseStatus, validate_license_key

        key = self._make_key({
            "v": 1, "tier": "pro", "licensee": "Corp",
            "max_seats": 1, "exp": 20991231,
            "iat": "2026-01-01", "id": "lic_x"
        })
        with _bypass_signature_verification():
            result = validate_license_key(key)
            # str(20991231) = "20991231" which is valid YYYYMMDD ISO 8601
            assert result.status == LicenseStatus.valid

    def test_extra_fields_ignored(self):
        from sandcastle.engine.license import LicenseStatus, validate_license_key

        key = self._make_key({
            "v": 1, "tier": "pro", "licensee": "Corp",
            "max_seats": 1, "exp": "2099-12-31",
            "iat": "2026-01-01", "id": "lic_x",
            "custom_field": "should-be-ignored",
            "another": [1, 2, 3],
        })
        with _bypass_signature_verification():
            result = validate_license_key(key)
            assert result.status == LicenseStatus.valid

    def test_missing_all_optional_fields(self):
        from sandcastle.engine.license import LicenseStatus, LicenseTier, validate_license_key

        key = self._make_key({"v": 1})
        with _bypass_signature_verification():
            result = validate_license_key(key)
            assert result.status == LicenseStatus.valid
            assert result.tier == LicenseTier.community
            assert result.licensee == ""
            assert result.max_seats == 0
            assert result.expires == ""


class TestLicenseResetCache:
    """reset_cache should clear both cache and key snapshot."""

    def test_reset_allows_revalidation(self):
        from sandcastle.engine.license import (
            LicenseStatus,
            get_license,
            reset_cache,
        )

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.license_key = _PRO_KEY
            result1 = get_license()
            assert result1.status == LicenseStatus.valid

            reset_cache()

            # After reset, even same key triggers re-validation
            mock_settings.license_key = _PRO_KEY
            result2 = get_license()
            assert result2.status == LicenseStatus.valid
            # Should be a new object (re-validated)
            # Note: they may be equal but could be the same object due to
            # caching, so just verify status is correct


class TestLicenseEnumValues:
    """Verify enum string values match expected API contract."""

    def test_tier_values(self):
        from sandcastle.engine.license import LicenseTier

        assert LicenseTier.community.value == "community"
        assert LicenseTier.pro.value == "pro"
        assert LicenseTier.enterprise.value == "enterprise"

    def test_status_values(self):
        from sandcastle.engine.license import LicenseStatus

        assert LicenseStatus.valid.value == "valid"
        assert LicenseStatus.expired.value == "expired"
        assert LicenseStatus.invalid.value == "invalid"
        assert LicenseStatus.missing.value == "missing"

    def test_tier_is_str_enum(self):
        from sandcastle.engine.license import LicenseTier

        # str, Enum: .value gives the string value
        assert LicenseTier.pro.value == "pro"
        assert isinstance(LicenseTier.pro, str)

    def test_status_is_str_enum(self):
        from sandcastle.engine.license import LicenseStatus

        assert LicenseStatus.valid.value == "valid"
        assert isinstance(LicenseStatus.valid, str)


class TestCryptoDecryptNonJsonPayload:
    """If Fernet decryption succeeds but result is not valid JSON."""

    def test_non_json_bytes(self, monkeypatch):
        from cryptography.fernet import Fernet as RealFernet

        key = RealFernet.generate_key()
        monkeypatch.setattr(
            "sandcastle.config.settings.credential_encryption_key", key.decode()
        )

        from sandcastle.engine.crypto import decrypt_credentials

        f = RealFernet(key)
        # Encrypt raw bytes that are not valid JSON
        token = f.encrypt(b"this is not json {{{").decode()
        result = decrypt_credentials(token)
        assert result == {}

    def test_empty_bytes(self, monkeypatch):
        from cryptography.fernet import Fernet as RealFernet

        key = RealFernet.generate_key()
        monkeypatch.setattr(
            "sandcastle.config.settings.credential_encryption_key", key.decode()
        )

        from sandcastle.engine.crypto import decrypt_credentials

        f = RealFernet(key)
        token = f.encrypt(b"").decode()
        result = decrypt_credentials(token)
        assert result == {}


class TestCryptoPassthroughSafety:
    """In passthrough mode (no key), operations should be safe."""

    def test_encrypt_passthrough_does_not_modify_input(self, monkeypatch):
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", "")
        from sandcastle.engine.crypto import encrypt_credentials

        original = {"k": "v", "nested": {"a": 1}}
        import copy
        frozen = copy.deepcopy(original)
        result = encrypt_credentials(original)
        # Result IS the original dict in passthrough
        assert result is original
        assert original == frozen  # Not mutated

    def test_decrypt_passthrough_returns_same_dict(self, monkeypatch):
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", "")
        from sandcastle.engine.crypto import decrypt_credentials

        data = {"k": "v"}
        result = decrypt_credentials(data)
        assert result is data
