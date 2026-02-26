"""Tests for credential encryption at rest."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_fernet():
    """Reset the global Fernet state between tests."""
    from sandcastle.engine import crypto

    crypto._fernet_instance = None
    crypto._fernet_checked = False
    yield
    crypto._fernet_instance = None
    crypto._fernet_checked = False


class TestEncryptDecryptRoundtrip:
    """Roundtrip encryption/decryption with a valid key."""

    def test_roundtrip(self, monkeypatch):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)

        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        data = {"API_KEY": "secret-value", "TOKEN": "another-secret"}
        encrypted = encrypt_credentials(data)

        # Encrypted value should be a string (Fernet token)
        assert isinstance(encrypted, str)
        assert encrypted != str(data)

        # Decrypt should return original dict
        decrypted = decrypt_credentials(encrypted)
        assert decrypted == data

    def test_roundtrip_empty_dict(self, monkeypatch):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)

        from sandcastle.engine.crypto import decrypt_credentials, encrypt_credentials

        data = {}
        encrypted = encrypt_credentials(data)
        assert isinstance(encrypted, str)
        decrypted = decrypt_credentials(encrypted)
        assert decrypted == data


class TestNoKeyPassthrough:
    """When no encryption key is set, credentials pass through as plaintext."""

    def test_encrypt_returns_dict(self, monkeypatch):
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", "")

        from sandcastle.engine.crypto import encrypt_credentials

        data = {"KEY": "value"}
        result = encrypt_credentials(data)
        assert result == data
        assert isinstance(result, dict)

    def test_decrypt_dict_passthrough(self, monkeypatch):
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", "")

        from sandcastle.engine.crypto import decrypt_credentials

        data = {"KEY": "value"}
        result = decrypt_credentials(data)
        assert result == data


class TestDecryptPlaintextDict:
    """Decrypting a plaintext dict should return it as-is."""

    def test_decrypt_dict_with_key_set(self, monkeypatch):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", key)

        from sandcastle.engine.crypto import decrypt_credentials

        data = {"KEY": "value"}
        result = decrypt_credentials(data)
        assert result == data


class TestInvalidKey:
    """Invalid encryption key should be handled gracefully."""

    def test_invalid_key_encrypt_passthrough(self, monkeypatch):
        monkeypatch.setattr(
            "sandcastle.config.settings.credential_encryption_key", "not-a-valid-fernet-key"
        )

        from sandcastle.engine.crypto import encrypt_credentials

        data = {"KEY": "value"}
        result = encrypt_credentials(data)
        # With invalid key, Fernet init fails, so passthrough
        assert result == data

    def test_decrypt_encrypted_string_without_key(self, monkeypatch):
        """Encrypted string but no key configured should return empty dict."""
        monkeypatch.setattr("sandcastle.config.settings.credential_encryption_key", "")

        from sandcastle.engine.crypto import decrypt_credentials

        result = decrypt_credentials("gAAAAABh...")
        assert result == {}
