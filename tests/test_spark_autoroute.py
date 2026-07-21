"""Tests for Spark Mode -> local NIM auto-route (is_nim_reachable + maybe_spark_nim_route)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import sandcastle.engine.providers as providers
from sandcastle.config import settings
from sandcastle.engine.providers import is_nim_reachable, maybe_spark_nim_route


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    providers._NIM_REACHABLE_CACHE.clear()
    yield
    providers._NIM_REACHABLE_CACHE.clear()


# ---- is_nim_reachable -------------------------------------------------------


@pytest.mark.parametrize("status,expected", [(200, True), (401, True), (404, True), (503, False)])
def test_is_nim_reachable_by_status(status, expected):
    with patch("httpx.get", return_value=MagicMock(status_code=status)):
        assert is_nim_reachable("http://nim:8000") is expected


def test_is_nim_reachable_false_on_connection_error():
    with patch("httpx.get", side_effect=OSError("refused")):
        assert is_nim_reachable("http://nim:8000") is False


def test_is_nim_reachable_caches(monkeypatch):
    calls = {"n": 0}

    def _get(*a, **k):
        calls["n"] += 1
        return MagicMock(status_code=200)

    with patch("httpx.get", side_effect=_get):
        assert is_nim_reachable("http://nim:8000") is True
        assert is_nim_reachable("http://nim:8000") is True
    assert calls["n"] == 1  # second call served from cache


# ---- maybe_spark_nim_route --------------------------------------------------


def _spark(monkeypatch, *, spark=True, autoroute=True, residency="", reachable=True):
    # spark_mode is a computed_field driven by SANDCASTLE_SPARK_MODE; set the env.
    monkeypatch.setenv("SANDCASTLE_SPARK_MODE", "on" if spark else "off")
    monkeypatch.setattr(settings, "spark_nim_autoroute", autoroute)
    monkeypatch.setattr(settings, "data_residency", residency)
    monkeypatch.setattr(providers, "is_nim_reachable", lambda *a, **k: reachable)


def test_routes_default_to_nim_on_spark(monkeypatch):
    _spark(monkeypatch)
    assert maybe_spark_nim_route("sonnet") == "nim/llama-3.1-70b"


def test_explicit_non_default_model_is_respected(monkeypatch):
    _spark(monkeypatch)
    assert maybe_spark_nim_route("opus") == "opus"
    assert maybe_spark_nim_route("mistral/large") == "mistral/large"


def test_noop_when_spark_off(monkeypatch):
    _spark(monkeypatch, spark=False)
    assert maybe_spark_nim_route("sonnet") == "sonnet"


def test_noop_when_autoroute_disabled(monkeypatch):
    _spark(monkeypatch, autoroute=False)
    assert maybe_spark_nim_route("sonnet") == "sonnet"


def test_noop_when_unreachable(monkeypatch):
    _spark(monkeypatch, reachable=False)
    assert maybe_spark_nim_route("sonnet") == "sonnet"


def test_noop_under_eu_residency(monkeypatch):
    _spark(monkeypatch, residency="eu")
    assert maybe_spark_nim_route("sonnet") == "sonnet"


def test_configurable_default_model(monkeypatch):
    _spark(monkeypatch)
    monkeypatch.setattr(settings, "spark_nim_default_model", "nim/ornith")
    assert maybe_spark_nim_route("sonnet") == "nim/ornith"


def test_empty_default_model_falls_back(monkeypatch):
    _spark(monkeypatch)
    monkeypatch.setattr(settings, "spark_nim_default_model", "")
    assert maybe_spark_nim_route("sonnet") == "nim/llama-3.1-70b"


# ---- ollama_base_url --------------------------------------------------------


def test_ollama_base_url_env_wins(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://host.docker.internal:11434/")
    assert providers.ollama_base_url() == "http://host.docker.internal:11434"


def test_ollama_base_url_settings_fallback(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setattr(settings, "ollama_host", "http://myhost:11434")
    assert providers.ollama_base_url() == "http://myhost:11434"


def test_ollama_resolve_base_url_respects_host(monkeypatch):
    from sandcastle.engine.providers import resolve_base_url, resolve_model

    monkeypatch.setenv("OLLAMA_HOST", "http://host.docker.internal:11434")
    info = resolve_model("ollama")
    assert resolve_base_url(info) == "http://host.docker.internal:11434/v1"
