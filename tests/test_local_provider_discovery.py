"""Tests for 0.41.2 local provider discovery (OLLAMA_HOST-aware probes + NIM detection).

Docker deployments run Ollama / vLLM on the host, not in the container, so every
discovery probe must honour OLLAMA_HOST / NIM_BASE_URL instead of localhost.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sandcastle.config import settings


def _make_client() -> TestClient:
    from sandcastle.api.routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _clear_health_cache() -> None:
    import sandcastle.api.routes as routes_module

    endpoint = routes_module.get_provider_health
    for attr in ("_cache", "_cache_ts"):
        if hasattr(endpoint, attr):
            delattr(endpoint, attr)


def _mock_async_client(mock_client_cls, status_code: int = 200):
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.raise_for_status = MagicMock()
    mock_instance = AsyncMock()
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=False)
    mock_instance.post = AsyncMock(return_value=mock_resp)
    mock_instance.get = AsyncMock(return_value=mock_resp)
    mock_client_cls.return_value = mock_instance
    return mock_instance


class TestHealthProvidersLocalDiscovery:
    def test_nim_included_in_health_providers(self):
        """/health/providers must report the local NIM/vLLM endpoint."""
        client = _make_client()
        _clear_health_cache()
        with patch("httpx.AsyncClient") as mock_client_cls:
            _mock_async_client(mock_client_cls)
            resp = client.get("/health/providers")
        data = resp.json()["data"]
        assert "nim" in data
        assert data["nim"]["region"] == "local"
        assert data["nim"]["status"] == "ok"

    def test_ollama_probe_uses_ollama_host_env(self, monkeypatch):
        """The Ollama probe must target OLLAMA_HOST, not hardcoded localhost."""
        monkeypatch.setenv("OLLAMA_HOST", "http://host.docker.internal:11434")
        client = _make_client()
        _clear_health_cache()
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_instance = _mock_async_client(mock_client_cls)
            client.get("/health/providers")
        get_urls = [c.args[0] for c in mock_instance.get.await_args_list if c.args]
        assert "http://host.docker.internal:11434/api/tags" in get_urls

    def test_nim_probe_uses_nim_base_url(self, monkeypatch):
        """The NIM probe must target settings.nim_base_url."""
        monkeypatch.setattr(settings, "nim_base_url", "http://host.docker.internal:18000")
        client = _make_client()
        _clear_health_cache()
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_instance = _mock_async_client(mock_client_cls)
            client.get("/health/providers")
        get_urls = [c.args[0] for c in mock_instance.get.await_args_list if c.args]
        assert "http://host.docker.internal:18000/v1/models" in get_urls


class TestAdvisorStatusLocalDiscovery:
    def test_advisor_status_lists_nim(self):
        """/advisor/status must include a nim entry with local region."""
        client = _make_client()
        with patch("httpx.AsyncClient") as mock_client_cls:
            _mock_async_client(mock_client_cls)
            resp = client.get("/advisor/status")
        providers = resp.json()["data"]["available_providers"]
        by_id = {p["id"]: p for p in providers}
        assert "nim" in by_id
        assert by_id["nim"]["region"] == "local"
        assert by_id["nim"]["status"] == "running"

    def test_advisor_status_ollama_uses_env_host(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_HOST", "http://host.docker.internal:11434")
        client = _make_client()
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_instance = _mock_async_client(mock_client_cls)
            client.get("/advisor/status")
        get_urls = [c.args[0] for c in mock_instance.get.await_args_list if c.args]
        assert "http://host.docker.internal:11434/api/tags" in get_urls


class TestGeneratorApiUrlResolution:
    def test_ollama_api_url_resolves_from_env(self, monkeypatch):
        from sandcastle.engine.generator import _PROVIDER_CONFIGS, resolve_provider_api_url

        monkeypatch.setenv("OLLAMA_HOST", "http://host.docker.internal:11434")
        url = resolve_provider_api_url(_PROVIDER_CONFIGS["ollama"])
        assert url == "http://host.docker.internal:11434/v1/chat/completions"

    def test_static_urls_pass_through(self):
        from sandcastle.engine.generator import _PROVIDER_CONFIGS, resolve_provider_api_url

        url = resolve_provider_api_url(_PROVIDER_CONFIGS["anthropic"])
        assert url == "https://api.anthropic.com/v1/messages"
