"""Tests for the unauthenticated network-bind startup guard."""

import pytest

from sandcastle.config import Settings, validate_server_bind


def _settings(*, auth_required: bool, allow_insecure_bind: bool) -> Settings:
    return Settings(
        _env_file=None,
        auth_required=auth_required,
        allow_insecure_bind=allow_insecure_bind,
    )


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_bind_without_auth_is_allowed(host):
    validate_server_bind(host, _settings(auth_required=False, allow_insecure_bind=False))


def test_network_bind_without_auth_is_rejected():
    with pytest.raises(RuntimeError, match="AUTH_REQUIRED=false"):
        validate_server_bind("0.0.0.0", _settings(auth_required=False, allow_insecure_bind=False))


def test_network_bind_error_explains_the_safe_remediations():
    with pytest.raises(RuntimeError) as exc_info:
        validate_server_bind("0.0.0.0", _settings(auth_required=False, allow_insecure_bind=False))

    assert "Set AUTH_REQUIRED=true" in str(exc_info.value)
    assert "SANDCASTLE_ALLOW_INSECURE_BIND=true" in str(exc_info.value)


def test_network_bind_without_auth_allows_explicit_opt_out():
    validate_server_bind("0.0.0.0", _settings(auth_required=False, allow_insecure_bind=True))


def test_network_bind_with_auth_is_allowed():
    validate_server_bind("0.0.0.0", _settings(auth_required=True, allow_insecure_bind=False))


def test_insecure_bind_opt_out_reads_the_documented_environment_variable(monkeypatch):
    monkeypatch.setenv("SANDCASTLE_ALLOW_INSECURE_BIND", "true")

    assert Settings(_env_file=None).allow_insecure_bind is True
