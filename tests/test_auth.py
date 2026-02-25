"""Tests for auth module - key hashing and helpers."""

from sandcastle.api.auth import generate_api_key, hash_key


class TestHashKey:

    def test_deterministic(self):
        h1 = hash_key("my-secret-key")
        h2 = hash_key("my-secret-key")
        assert h1 == h2

    def test_different_keys_different_hashes(self):
        h1 = hash_key("key-1")
        h2 = hash_key("key-2")
        assert h1 != h2

    def test_returns_hex_string(self):
        h = hash_key("test-key")
        assert isinstance(h, str)
        assert len(h) == 64  # SHA256 hex digest
        int(h, 16)  # should be valid hex


class TestGenerateApiKey:

    def test_prefix(self):
        key = generate_api_key()
        assert key.startswith("sc_")

    def test_uniqueness(self):
        keys = {generate_api_key() for _ in range(100)}
        assert len(keys) == 100

    def test_sufficient_length(self):
        key = generate_api_key()
        assert len(key) > 30
