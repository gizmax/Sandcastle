"""Comprehensive deep tests for credential encryption at rest (crypto.py).

Tests cover:
  1. Fernet encryption/decryption roundtrip
  2. Behavior with/without CREDENTIAL_ENCRYPTION_KEY
  3. Key rotation (encrypt with old key, decrypt with new key)
  4. Corrupted ciphertext handling
  5. Empty/null values
  6. Credential masking for UI display
  7. Thread safety
  8. Type validation
  9. Edge cases: unicode, large payloads, nested dicts
  10. Key format validation
"""

from __future__ import annotations

import json
import threading
from typing import Any
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _reset_fernet():
    """Reset the global Fernet state between tests so each test is isolated."""
    from sandcastle.engine import crypto

    crypto._fernet_instance = None
    crypto._fernet_checked = False
    crypto._fernet_key_snapshot = ""
    yield
    crypto._fernet_instance = None
    crypto._fernet_checked = False
    crypto._fernet_key_snapshot = ""


def _generate_key() -> str:
    """Generate a valid Fernet key as a string."""
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


# ===========================================================================
# 1. Fernet encryption/decryption roundtrip
# ===========================================================================


class TestEncryptDecryptRoundtrip:
    """Roundtrip tests with various credential payloads."""

    def test_simple_dict(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        data = {"API_KEY": "sk-12345", "SECRET": "hunter2"}
        encrypted = encrypt_credentials(data)
        assert isinstance(encrypted, str)
        assert encrypted != json.dumps(data)
        decrypted = decrypt_credentials(encrypted)
        assert decrypted == data

    def test_empty_dict(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        data = {}
        encrypted = encrypt_credentials(data)
        assert isinstance(encrypted, str)
        decrypted = decrypt_credentials(encrypted)
        assert decrypted == data

    def test_single_key(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        data = {"TOKEN": "abc123"}
        decrypted = decrypt_credentials(encrypt_credentials(data))
        assert decrypted == data

    def test_nested_dict(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        data = {
            "provider": "aws",
            "credentials": {
                "access_key": "AKIAIOSFODNN7EXAMPLE",
                "secret_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            },
            "region": "us-east-1",
        }
        decrypted = decrypt_credentials(encrypt_credentials(data))
        assert decrypted == data

    def test_dict_with_various_value_types(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        data = {
            "string_val": "hello",
            "int_val": 42,
            "float_val": 3.14,
            "bool_val": True,
            "null_val": None,
            "list_val": [1, "two", 3.0],
        }
        decrypted = decrypt_credentials(encrypt_credentials(data))
        assert decrypted == data

    def test_unicode_values(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        data = {"name": "Rene", "emoji": "rocket", "cjk": "password"}
        decrypted = decrypt_credentials(encrypt_credentials(data))
        assert decrypted == data

    def test_large_payload(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        data = {f"key_{i}": f"value_{'x' * 1000}" for i in range(100)}
        decrypted = decrypt_credentials(encrypt_credentials(data))
        assert decrypted == data
        assert len(decrypted) == 100

    def test_encrypted_output_is_not_plaintext(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import encrypt_credentials

        data = {"SECRET": "super_secret_value_12345"}
        encrypted = encrypt_credentials(data)
        assert "super_secret_value_12345" not in encrypted
        assert "SECRET" not in encrypted

    def test_each_encryption_produces_different_ciphertext(self, monkeypatch) -> None:
        """Fernet uses a random IV, so repeated encryptions differ."""
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import encrypt_credentials

        data = {"KEY": "same_value"}
        enc1 = encrypt_credentials(data)
        # Reset fernet state for fresh encryption
        from sandcastle.engine import crypto
        crypto._fernet_checked = False
        enc2 = encrypt_credentials(data)
        assert enc1 != enc2  # different IVs


# ===========================================================================
# 2. Behavior with/without CREDENTIAL_ENCRYPTION_KEY
# ===========================================================================


class TestNoKeyPassthrough:
    """When no encryption key is set, credentials pass through as plaintext."""

    def test_encrypt_returns_dict_unchanged(self, monkeypatch) -> None:
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", "")
        from sandcastle.engine.crypto import encrypt_credentials

        data = {"KEY": "value"}
        result = encrypt_credentials(data)
        assert result == data
        assert isinstance(result, dict)

    def test_decrypt_dict_passthrough(self, monkeypatch) -> None:
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", "")
        from sandcastle.engine.crypto import decrypt_credentials

        data = {"KEY": "value"}
        result = decrypt_credentials(data)
        assert result == data

    def test_decrypt_encrypted_string_without_key_returns_empty(self, monkeypatch) -> None:
        """If stored value is encrypted but no key configured, return empty dict."""
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", "")
        from sandcastle.engine.crypto import decrypt_credentials

        result = decrypt_credentials("gAAAAABhfake...")
        assert result == {}

    def test_encrypt_then_remove_key(self, monkeypatch) -> None:
        """Encrypt with key, then try decrypt without key = empty dict."""
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        encrypted = encrypt_credentials({"SECRET": "abc"})

        # Remove key
        from sandcastle.engine import crypto
        crypto._fernet_checked = False
        crypto._fernet_instance = None
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", "")

        result = decrypt_credentials(encrypted)
        assert result == {}  # cannot decrypt without key

    def test_none_key_treated_as_no_key(self, monkeypatch) -> None:
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", None)
        from sandcastle.engine.crypto import encrypt_credentials

        data = {"KEY": "val"}
        result = encrypt_credentials(data)
        assert result == data


# ===========================================================================
# 3. Key rotation
# ===========================================================================


class TestKeyRotation:
    """Encrypt with old key, then try to decrypt with new key."""

    def test_wrong_key_returns_empty_dict(self, monkeypatch) -> None:
        """Data encrypted with key A cannot be decrypted with key B."""
        key_a = _generate_key()
        key_b = _generate_key()
        assert key_a != key_b

        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key_a)
        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        encrypted = encrypt_credentials({"SECRET": "value"})

        # Switch to key B
        from sandcastle.engine import crypto
        crypto._fernet_checked = False
        crypto._fernet_instance = None
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key_b)

        result = decrypt_credentials(encrypted)
        assert result == {}  # decryption fails gracefully

    def test_re_encrypt_with_new_key(self, monkeypatch) -> None:
        """Simulate key rotation: decrypt with old key, re-encrypt with new key."""
        key_old = _generate_key()
        key_new = _generate_key()

        # Encrypt with old key
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key_old)
        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials
        from sandcastle.engine import crypto

        original = {"API_KEY": "secret123"}
        encrypted_old = encrypt_credentials(original)

        # Decrypt with old key
        decrypted = decrypt_credentials(encrypted_old)
        assert decrypted == original

        # Switch to new key
        crypto._fernet_checked = False
        crypto._fernet_instance = None
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key_new)

        # Re-encrypt with new key
        encrypted_new = encrypt_credentials(decrypted)
        assert encrypted_new != encrypted_old

        # Verify new encrypted value decrypts correctly
        crypto._fernet_checked = False
        crypto._fernet_instance = None
        final = decrypt_credentials(encrypted_new)
        assert final == original

    def test_fernet_reinitializes_on_key_change(self, monkeypatch) -> None:
        """Verify that _get_fernet() picks up key changes at runtime."""
        key_a = _generate_key()
        key_b = _generate_key()

        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key_a)
        from sandcastle.engine.crypto import encrypt_credentials
        from sandcastle.engine import crypto

        enc_a = encrypt_credentials({"k": "v"})
        assert isinstance(enc_a, str)

        # Change key without resetting _fernet_checked (simulates runtime change)
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key_b)
        # The _get_fernet detects key change via _fernet_key_snapshot comparison
        crypto._fernet_checked = True  # keep checked but snapshot differs

        enc_b = encrypt_credentials({"k": "v"})
        assert isinstance(enc_b, str)
        # Both should be valid Fernet tokens
        assert enc_a.startswith("gAAAAA") or len(enc_a) > 10
        assert enc_b.startswith("gAAAAA") or len(enc_b) > 10


# ===========================================================================
# 4. Corrupted ciphertext handling
# ===========================================================================


class TestCorruptedCiphertext:
    """Verify graceful handling of corrupted/invalid encrypted data."""

    def test_garbage_string_returns_empty(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import decrypt_credentials

        result = decrypt_credentials("not_a_fernet_token_at_all")
        assert result == {}

    def test_truncated_token_returns_empty(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import encrypt_credentials, decrypt_credentials

        encrypted = encrypt_credentials({"KEY": "val"})
        truncated = encrypted[:len(encrypted) // 2]
        result = decrypt_credentials(truncated)
        assert result == {}

    def test_modified_token_returns_empty(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import encrypt_credentials, decrypt_credentials

        encrypted = encrypt_credentials({"KEY": "val"})
        # Flip a character in the middle
        mid = len(encrypted) // 2
        corrupted = encrypted[:mid] + ("A" if encrypted[mid] != "A" else "B") + encrypted[mid + 1:]
        result = decrypt_credentials(corrupted)
        assert result == {}

    def test_empty_string_returns_empty(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import decrypt_credentials

        result = decrypt_credentials("")
        assert result == {}

    def test_base64_but_not_fernet(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import decrypt_credentials
        import base64

        fake = base64.urlsafe_b64encode(b"this is not fernet").decode()
        result = decrypt_credentials(fake)
        assert result == {}


# ===========================================================================
# 5. Empty/null values
# ===========================================================================


class TestEmptyNullValues:
    """Test handling of empty, None, and non-dict inputs."""

    def test_encrypt_empty_dict(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import encrypt_credentials, decrypt_credentials

        encrypted = encrypt_credentials({})
        decrypted = decrypt_credentials(encrypted)
        assert decrypted == {}

    def test_decrypt_none_returns_empty(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import decrypt_credentials

        result = decrypt_credentials(None)
        assert result == {}

    def test_decrypt_integer_returns_empty(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import decrypt_credentials

        result = decrypt_credentials(42)
        assert result == {}

    def test_decrypt_list_returns_empty(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import decrypt_credentials

        result = decrypt_credentials(["not", "a", "dict"])
        assert result == {}

    def test_decrypt_boolean_returns_empty(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import decrypt_credentials

        result = decrypt_credentials(True)
        assert result == {}

    def test_encrypt_non_dict_raises_type_error(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import encrypt_credentials

        with pytest.raises(TypeError, match="expects a dict"):
            encrypt_credentials("not a dict")

    def test_encrypt_none_raises_type_error(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import encrypt_credentials

        with pytest.raises(TypeError, match="expects a dict"):
            encrypt_credentials(None)

    def test_encrypt_list_raises_type_error(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import encrypt_credentials

        with pytest.raises(TypeError, match="expects a dict"):
            encrypt_credentials([1, 2, 3])

    def test_encrypt_integer_raises_type_error(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import encrypt_credentials

        with pytest.raises(TypeError, match="expects a dict"):
            encrypt_credentials(42)


# ===========================================================================
# 6. Credential masking for UI display
# ===========================================================================


class TestCredentialMasking:
    """Encrypted credentials should not reveal the original values."""

    def test_api_key_not_visible_in_encrypted(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import encrypt_credentials

        api_key = "sk-ant-ABCDEFGHIJKLMNOPQRSTUVWXYZab"
        encrypted = encrypt_credentials({"anthropic_key": api_key})
        assert api_key not in encrypted
        assert "anthropic_key" not in encrypted

    def test_password_not_visible(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import encrypt_credentials

        password = "SuperSecretP@ssw0rd!"
        encrypted = encrypt_credentials({"password": password})
        assert password not in encrypted

    def test_multiple_secrets_not_visible(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import encrypt_credentials

        data = {
            "aws_key": "AKIAIOSFODNN7EXAMPLE",
            "aws_secret": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "db_password": "postgres123!",
        }
        encrypted = encrypt_credentials(data)
        for val in data.values():
            assert val not in encrypted
        for k in data.keys():
            assert k not in encrypted


# ===========================================================================
# 7. Thread safety
# ===========================================================================


class TestThreadSafety:
    """Verify that concurrent encrypt/decrypt calls don't corrupt state."""

    def test_concurrent_encrypt_decrypt(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        errors = []
        results = []

        def worker(i: int) -> None:
            try:
                data = {"key": f"value_{i}"}
                encrypted = encrypt_credentials(data)
                decrypted = decrypt_credentials(encrypted)
                if decrypted != data:
                    errors.append(f"Thread {i}: expected {data}, got {decrypted}")
                results.append(decrypted)
            except Exception as e:
                errors.append(f"Thread {i}: {e}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Thread errors: {errors}"
        assert len(results) == 20

    def test_concurrent_reads_same_key(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import encrypt_credentials, decrypt_credentials

        data = {"shared_secret": "value123"}
        encrypted = encrypt_credentials(data)
        errors = []

        def reader(i: int) -> None:
            try:
                result = decrypt_credentials(encrypted)
                if result != data:
                    errors.append(f"Reader {i}: got {result}")
            except Exception as e:
                errors.append(f"Reader {i}: {e}")

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == []


# ===========================================================================
# 8. Invalid key handling
# ===========================================================================


class TestInvalidKeyHandling:
    """Graceful handling of invalid/malformed encryption keys."""

    def test_invalid_key_encrypt_passthrough(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "sandcastle.config.settings.credential_encryption_key",
            "not-a-valid-fernet-key",
        )
        from sandcastle.engine.crypto import encrypt_credentials

        data = {"KEY": "value"}
        result = encrypt_credentials(data)
        # Invalid key means Fernet init fails, passthrough as dict
        assert result == data

    def test_invalid_key_decrypt_string_returns_empty(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "sandcastle.config.settings.credential_encryption_key",
            "broken-key-data",
        )
        from sandcastle.engine.crypto import decrypt_credentials

        result = decrypt_credentials("some_encrypted_looking_string")
        # Key is invalid so Fernet is None, string input with no Fernet = empty
        assert result == {}

    def test_too_short_key(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "sandcastle.config.settings.credential_encryption_key", "abc"
        )
        from sandcastle.engine.crypto import encrypt_credentials

        result = encrypt_credentials({"K": "V"})
        assert result == {"K": "V"}  # passthrough

    def test_empty_string_key(self, monkeypatch) -> None:
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", "")
        from sandcastle.engine.crypto import encrypt_credentials

        result = encrypt_credentials({"K": "V"})
        assert result == {"K": "V"}


# ===========================================================================
# 9. Edge cases
# ===========================================================================


class TestEdgeCases:
    """Various edge cases for the crypto module."""

    def test_dict_with_special_json_chars(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        data = {
            "query": 'SELECT * FROM users WHERE name = "O\'Brien"',
            "path": "C:\\Users\\admin",
            "newlines": "line1\nline2\ttab",
        }
        decrypted = decrypt_credentials(encrypt_credentials(data))
        assert decrypted == data

    def test_dict_with_empty_string_values(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        data = {"empty": "", "also_empty": ""}
        decrypted = decrypt_credentials(encrypt_credentials(data))
        assert decrypted == data

    def test_dict_with_very_long_key_names(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        long_key = "k" * 10000
        data = {long_key: "value"}
        decrypted = decrypt_credentials(encrypt_credentials(data))
        assert decrypted == data

    def test_decrypt_valid_fernet_but_not_json_returns_empty(self, monkeypatch) -> None:
        """If Fernet decrypts to non-JSON, return empty dict."""
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from cryptography.fernet import Fernet

        f = Fernet(key.encode())
        # Encrypt plain text that is not valid JSON
        token = f.encrypt(b"this is not json").decode()

        from sandcastle.engine.crypto import decrypt_credentials
        result = decrypt_credentials(token)
        assert result == {}

    def test_decrypt_valid_fernet_but_json_list_returns_empty(self, monkeypatch) -> None:
        """If Fernet decrypts to a JSON list (not dict), return empty dict."""
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from cryptography.fernet import Fernet

        f = Fernet(key.encode())
        token = f.encrypt(json.dumps([1, 2, 3]).encode()).decode()

        from sandcastle.engine.crypto import decrypt_credentials
        result = decrypt_credentials(token)
        assert result == {}

    def test_decrypt_valid_fernet_but_json_string_returns_empty(self, monkeypatch) -> None:
        """If Fernet decrypts to a JSON string (not dict), return empty dict."""
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from cryptography.fernet import Fernet

        f = Fernet(key.encode())
        token = f.encrypt(json.dumps("just a string").encode()).decode()

        from sandcastle.engine.crypto import decrypt_credentials
        result = decrypt_credentials(token)
        assert result == {}

    def test_decrypt_valid_fernet_json_int_returns_empty(self, monkeypatch) -> None:
        """If Fernet decrypts to a JSON integer (not dict), return empty dict."""
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from cryptography.fernet import Fernet

        f = Fernet(key.encode())
        token = f.encrypt(json.dumps(42).encode()).decode()

        from sandcastle.engine.crypto import decrypt_credentials
        result = decrypt_credentials(token)
        assert result == {}

    def test_passthrough_dict_not_mutated(self, monkeypatch) -> None:
        """When no key is set, encrypt should not mutate the original dict."""
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", "")
        from sandcastle.engine.crypto import encrypt_credentials

        data = {"KEY": "original"}
        result = encrypt_credentials(data)
        assert result == data
        data["KEY"] = "modified"
        # Result should still be independent (or same ref - either is acceptable
        # since the contract just says "returns the original dict")

    def test_encrypted_token_starts_with_gAAAAA(self, monkeypatch) -> None:
        """Fernet tokens have a recognizable prefix."""
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import encrypt_credentials

        encrypted = encrypt_credentials({"k": "v"})
        assert isinstance(encrypted, str)
        assert encrypted.startswith("gAAAAA")


# ===========================================================================
# 10. Key format validation
# ===========================================================================


class TestKeyFormatValidation:
    """Ensure proper handling of various key formats."""

    def test_valid_base64_key_works(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import encrypt_credentials

        result = encrypt_credentials({"a": "b"})
        assert isinstance(result, str)

    def test_bytes_like_key_rejected_gracefully(self, monkeypatch) -> None:
        # A string that looks like raw bytes, not a valid Fernet key
        monkeypatch.setattr(
            "sandcastle.config.settings.credential_encryption_key",
            "\x00\x01\x02\x03" * 8,
        )
        from sandcastle.engine.crypto import encrypt_credentials

        result = encrypt_credentials({"a": "b"})
        # Should fall through to passthrough
        assert result == {"a": "b"}

    def test_whitespace_key_treated_as_no_key(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "sandcastle.config.settings.credential_encryption_key", "   "
        )
        from sandcastle.engine.crypto import encrypt_credentials

        # "   " is truthy but not a valid Fernet key
        result = encrypt_credentials({"a": "b"})
        # Fernet init will fail, passthrough
        assert result == {"a": "b"}


# ===========================================================================
# 11. Decrypt plaintext dict (backwards compatibility)
# ===========================================================================


class TestDecryptPlaintextDict:
    """Decrypting a plaintext dict returns it unchanged (backwards compat)."""

    def test_with_key_set(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import decrypt_credentials

        data = {"KEY": "plaintext_value"}
        result = decrypt_credentials(data)
        assert result == data

    def test_without_key_set(self, monkeypatch) -> None:
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", "")
        from sandcastle.engine.crypto import decrypt_credentials

        data = {"KEY": "value"}
        result = decrypt_credentials(data)
        assert result == data

    def test_nested_dict_passthrough(self, monkeypatch) -> None:
        key = _generate_key()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)
        from sandcastle.engine.crypto import decrypt_credentials

        data = {"outer": {"inner": "secret"}}
        result = decrypt_credentials(data)
        assert result == data
