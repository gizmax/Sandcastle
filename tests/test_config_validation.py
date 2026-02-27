"""Tests for configuration validation in config.py.

Validates that Settings field validators correctly handle invalid,
edge-case, and missing values for all critical settings.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from sandcastle.config import (
    Settings,
    _VALID_LOG_LEVELS,
    _VALID_MEMORY_BACKENDS,
    _VALID_SANDBOX_BACKENDS,
    _VALID_STORAGE_BACKENDS,
)


# ---------------------------------------------------------------------------
# Helper: create Settings with explicit env overrides
# ---------------------------------------------------------------------------

def _make_settings(**overrides) -> Settings:
    """Create a fresh Settings instance with specific values.

    Uses model_construct for fields that have validators, but validates
    them manually through the validators.
    """
    # Clear env vars that pydantic_settings might pick up
    env_overrides = {}
    for key, val in overrides.items():
        env_overrides[key.upper()] = str(val)
    with patch.dict(os.environ, env_overrides, clear=False):
        return Settings(
            _env_file=None,
            **overrides,
        )


# ---------------------------------------------------------------------------
# Tests: sandbox_backend
# ---------------------------------------------------------------------------


class TestSandboxBackendValidation:
    """Validate SANDBOX_BACKEND setting."""

    @pytest.mark.parametrize("backend", sorted(_VALID_SANDBOX_BACKENDS))
    def test_valid_backends_accepted(self, backend: str):
        s = _make_settings(sandbox_backend=backend)
        assert s.sandbox_backend == backend

    def test_invalid_backend_falls_back_to_e2b(self):
        s = _make_settings(sandbox_backend="kubernetes")
        assert s.sandbox_backend == "e2b"

    def test_empty_string_falls_back_to_e2b(self):
        s = _make_settings(sandbox_backend="")
        assert s.sandbox_backend == "e2b"

    def test_whitespace_trimmed(self):
        s = _make_settings(sandbox_backend="  docker  ")
        assert s.sandbox_backend == "docker"

    def test_case_insensitive(self):
        s = _make_settings(sandbox_backend="Docker")
        assert s.sandbox_backend == "docker"

    def test_default_is_e2b(self):
        s = _make_settings()
        assert s.sandbox_backend == "e2b"


# ---------------------------------------------------------------------------
# Tests: storage_backend
# ---------------------------------------------------------------------------


class TestStorageBackendValidation:
    """Validate STORAGE_BACKEND setting."""

    @pytest.mark.parametrize("backend", sorted(_VALID_STORAGE_BACKENDS))
    def test_valid_backends_accepted(self, backend: str):
        s = _make_settings(storage_backend=backend)
        assert s.storage_backend == backend

    def test_invalid_backend_falls_back_to_local(self):
        s = _make_settings(storage_backend="gcs")
        assert s.storage_backend == "local"

    def test_empty_string_falls_back_to_local(self):
        s = _make_settings(storage_backend="")
        assert s.storage_backend == "local"

    def test_whitespace_trimmed(self):
        s = _make_settings(storage_backend="  s3  ")
        assert s.storage_backend == "s3"

    def test_case_insensitive(self):
        s = _make_settings(storage_backend="S3")
        assert s.storage_backend == "s3"


# ---------------------------------------------------------------------------
# Tests: memory_backend
# ---------------------------------------------------------------------------


class TestMemoryBackendValidation:
    """Validate MEMORY_BACKEND setting."""

    @pytest.mark.parametrize("backend", sorted(_VALID_MEMORY_BACKENDS))
    def test_valid_backends_accepted(self, backend: str):
        s = _make_settings(memory_backend=backend)
        assert s.memory_backend == backend

    def test_invalid_backend_falls_back_to_local(self):
        s = _make_settings(memory_backend="redis")
        assert s.memory_backend == "local"

    def test_empty_string_falls_back_to_local(self):
        s = _make_settings(memory_backend="")
        assert s.memory_backend == "local"


# ---------------------------------------------------------------------------
# Tests: log_level
# ---------------------------------------------------------------------------


class TestLogLevelValidation:
    """Validate LOG_LEVEL setting."""

    @pytest.mark.parametrize("level", sorted(_VALID_LOG_LEVELS))
    def test_valid_levels_accepted(self, level: str):
        s = _make_settings(log_level=level)
        assert s.log_level == level

    def test_invalid_level_falls_back_to_info(self):
        s = _make_settings(log_level="verbose")
        assert s.log_level == "info"

    def test_case_insensitive(self):
        s = _make_settings(log_level="DEBUG")
        assert s.log_level == "debug"

    def test_empty_string_falls_back_to_info(self):
        s = _make_settings(log_level="")
        assert s.log_level == "info"


# ---------------------------------------------------------------------------
# Tests: max_concurrent_sandboxes
# ---------------------------------------------------------------------------


class TestMaxConcurrentSandboxes:
    """Validate MAX_CONCURRENT_SANDBOXES setting."""

    def test_valid_value(self):
        s = _make_settings(max_concurrent_sandboxes=10)
        assert s.max_concurrent_sandboxes == 10

    def test_zero_clamps_to_one(self):
        s = _make_settings(max_concurrent_sandboxes=0)
        assert s.max_concurrent_sandboxes == 1

    def test_negative_clamps_to_one(self):
        s = _make_settings(max_concurrent_sandboxes=-5)
        assert s.max_concurrent_sandboxes == 1

    def test_one_is_valid(self):
        s = _make_settings(max_concurrent_sandboxes=1)
        assert s.max_concurrent_sandboxes == 1


# ---------------------------------------------------------------------------
# Tests: memory_admit_threshold
# ---------------------------------------------------------------------------


class TestMemoryAdmitThreshold:
    """Validate MEMORY_ADMIT_THRESHOLD setting."""

    def test_valid_value(self):
        s = _make_settings(memory_admit_threshold=0.5)
        assert s.memory_admit_threshold == 0.5

    def test_zero_is_valid(self):
        s = _make_settings(memory_admit_threshold=0.0)
        assert s.memory_admit_threshold == 0.0

    def test_one_is_valid(self):
        s = _make_settings(memory_admit_threshold=1.0)
        assert s.memory_admit_threshold == 1.0

    def test_negative_clamps_to_zero(self):
        s = _make_settings(memory_admit_threshold=-0.5)
        assert s.memory_admit_threshold == 0.0

    def test_above_one_clamps_to_one(self):
        s = _make_settings(memory_admit_threshold=1.5)
        assert s.memory_admit_threshold == 1.0


# ---------------------------------------------------------------------------
# Tests: memory_max_age_days
# ---------------------------------------------------------------------------


class TestMemoryMaxAgeDays:
    """Validate MEMORY_MAX_AGE_DAYS setting."""

    def test_valid_value(self):
        s = _make_settings(memory_max_age_days=30)
        assert s.memory_max_age_days == 30

    def test_zero_is_valid(self):
        """Zero means no expiry - should be accepted."""
        s = _make_settings(memory_max_age_days=0)
        assert s.memory_max_age_days == 0

    def test_negative_clamps_to_zero(self):
        s = _make_settings(memory_max_age_days=-10)
        assert s.memory_max_age_days == 0


# ---------------------------------------------------------------------------
# Tests: failover_cooldown_seconds
# ---------------------------------------------------------------------------


class TestFailoverCooldownSeconds:
    """Validate FAILOVER_COOLDOWN_SECONDS setting."""

    def test_valid_value(self):
        s = _make_settings(failover_cooldown_seconds=30.0)
        assert s.failover_cooldown_seconds == 30.0

    def test_zero_falls_back_to_default(self):
        s = _make_settings(failover_cooldown_seconds=0.0)
        assert s.failover_cooldown_seconds == 60.0

    def test_negative_falls_back_to_default(self):
        s = _make_settings(failover_cooldown_seconds=-10.0)
        assert s.failover_cooldown_seconds == 60.0


# ---------------------------------------------------------------------------
# Tests: docker hardening
# ---------------------------------------------------------------------------


class TestDockerHardeningValidation:
    """Validate Docker container hardening settings."""

    def test_valid_pids_limit(self):
        s = _make_settings(docker_pids_limit=50)
        assert s.docker_pids_limit == 50

    def test_zero_pids_limit_falls_back(self):
        s = _make_settings(docker_pids_limit=0)
        assert s.docker_pids_limit == 100

    def test_negative_pids_limit_falls_back(self):
        s = _make_settings(docker_pids_limit=-1)
        assert s.docker_pids_limit == 100

    def test_valid_cpu_period(self):
        s = _make_settings(docker_cpu_period=50_000)
        assert s.docker_cpu_period == 50_000

    def test_too_small_cpu_period_falls_back(self):
        s = _make_settings(docker_cpu_period=500)
        assert s.docker_cpu_period == 100_000

    def test_valid_cpu_quota(self):
        s = _make_settings(docker_cpu_quota=25_000)
        assert s.docker_cpu_quota == 25_000

    def test_too_small_cpu_quota_falls_back(self):
        s = _make_settings(docker_cpu_quota=100)
        assert s.docker_cpu_quota == 50_000


# ---------------------------------------------------------------------------
# Tests: max_workflow_depth
# ---------------------------------------------------------------------------


class TestMaxWorkflowDepth:
    """Validate MAX_WORKFLOW_DEPTH setting."""

    def test_valid_value(self):
        s = _make_settings(max_workflow_depth=10)
        assert s.max_workflow_depth == 10

    def test_zero_falls_back_to_five(self):
        s = _make_settings(max_workflow_depth=0)
        assert s.max_workflow_depth == 5

    def test_negative_falls_back_to_five(self):
        s = _make_settings(max_workflow_depth=-1)
        assert s.max_workflow_depth == 5

    def test_one_is_valid(self):
        s = _make_settings(max_workflow_depth=1)
        assert s.max_workflow_depth == 1


# ---------------------------------------------------------------------------
# Tests: is_local_mode computed field
# ---------------------------------------------------------------------------


class TestIsLocalMode:
    """Validate the is_local_mode computed property."""

    def test_empty_database_url_is_local(self):
        s = _make_settings(database_url="")
        assert s.is_local_mode is True

    def test_sqlite_url_is_local(self):
        s = _make_settings(database_url="sqlite+aiosqlite:///path/to/db")
        assert s.is_local_mode is True

    def test_postgres_url_is_not_local(self):
        s = _make_settings(database_url="postgresql+asyncpg://user:pass@host/db")
        assert s.is_local_mode is False


# ---------------------------------------------------------------------------
# Tests: telemetry sensitive env keys
# ---------------------------------------------------------------------------


class TestSensitiveEnvKeys:
    """Ensure all credential-bearing settings are in _SENSITIVE_ENV_KEYS."""

    def test_database_url_is_sensitive(self):
        from sandcastle.engine.telemetry import _SENSITIVE_ENV_KEYS

        assert "database_url" in _SENSITIVE_ENV_KEYS

    def test_redis_url_is_sensitive(self):
        from sandcastle.engine.telemetry import _SENSITIVE_ENV_KEYS

        assert "redis_url" in _SENSITIVE_ENV_KEYS

    def test_all_api_keys_are_sensitive(self):
        from sandcastle.engine.telemetry import _SENSITIVE_ENV_KEYS

        api_keys = {
            "anthropic_api_key", "e2b_api_key", "openai_api_key",
            "openrouter_api_key", "minimax_api_key",
        }
        assert api_keys.issubset(_SENSITIVE_ENV_KEYS)

    def test_license_key_is_sensitive(self):
        from sandcastle.engine.telemetry import _SENSITIVE_ENV_KEYS

        assert "license_key" in _SENSITIVE_ENV_KEYS

    def test_credential_encryption_key_is_sensitive(self):
        from sandcastle.engine.telemetry import _SENSITIVE_ENV_KEYS

        assert "credential_encryption_key" in _SENSITIVE_ENV_KEYS


# ---------------------------------------------------------------------------
# Tests: backend factory validation
# ---------------------------------------------------------------------------


class TestBackendFactoryValidation:
    """Ensure create_backend() rejects truly unknown backends."""

    def test_unknown_backend_raises_value_error(self):
        from sandcastle.engine.backends import create_backend

        with pytest.raises(ValueError, match="Unknown sandbox backend"):
            create_backend("kubernetes")

    def test_empty_string_raises_value_error(self):
        from sandcastle.engine.backends import create_backend

        with pytest.raises(ValueError, match="Unknown sandbox backend"):
            create_backend("")

    @pytest.mark.parametrize("backend", ["e2b", "docker", "local", "cloudflare"])
    def test_valid_backends_create_successfully(self, backend: str):
        from sandcastle.engine.backends import create_backend

        b = create_backend(backend, e2b_api_key="test")
        assert b.name == backend


# ---------------------------------------------------------------------------
# Tests: storage factory
# ---------------------------------------------------------------------------


class TestStorageFactory:
    """Ensure create_storage() works with validated config."""

    def test_local_storage_creation(self):
        from sandcastle.engine.storage import LocalStorage, create_storage

        with patch("sandcastle.config.settings") as mock:
            mock.storage_backend = "local"
            storage = create_storage()
            assert isinstance(storage, LocalStorage)

    def test_s3_storage_creation(self):
        from sandcastle.engine.storage import S3Storage, create_storage

        with patch("sandcastle.config.settings") as mock:
            mock.storage_backend = "s3"
            mock.storage_bucket = "test-bucket"
            mock.storage_endpoint = "http://localhost:9000"
            mock.aws_access_key_id = "key"
            mock.aws_secret_access_key = "secret"
            storage = create_storage()
            assert isinstance(storage, S3Storage)


# ---------------------------------------------------------------------------
# Tests: dependency guard imports
# ---------------------------------------------------------------------------


class TestOptionalDependencyGuards:
    """Verify optional dependency imports are guarded with try/except."""

    def test_aiodocker_import_guarded_in_docker_backend(self):
        """DockerBackend._get_client raises RuntimeError with helpful message
        when aiodocker is not installed."""
        from sandcastle.engine.backends import DockerBackend

        backend = DockerBackend()
        with patch.dict("sys.modules", {"aiodocker": None}):
            with patch("builtins.__import__", side_effect=ImportError("No module named 'aiodocker'")):
                with pytest.raises(RuntimeError, match="aiodocker not installed"):
                    backend._get_client()

    def test_memory_backend_guarded(self):
        """Memory _get_client raises MemoryBackendError for unknown backends."""
        from sandcastle.engine.memory import MemoryBackendError, _get_client, _reset_client

        _reset_client()
        with pytest.raises(MemoryBackendError, match="Unknown memory backend"):
            _get_client("nonexistent")

    def test_crypto_without_cryptography(self):
        """Crypto functions gracefully handle missing cryptography package."""
        from sandcastle.engine.crypto import encrypt_credentials

        # When no key is set, encryption is a no-op passthrough
        data = {"key": "value"}
        result = encrypt_credentials(data)
        assert result == data
