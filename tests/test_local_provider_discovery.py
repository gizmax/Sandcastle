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


class TestWorkflowDefaultModel:
    """0.42: workflow_default_model setting + effective_model + dynamic ollama/<tag>."""

    def test_effective_model_replaces_bare_default(self, monkeypatch):
        from sandcastle.engine.providers import effective_model

        monkeypatch.setattr(settings, "workflow_default_model", "nim/ornith")
        assert effective_model("sonnet") == "nim/ornith"

    def test_effective_model_respects_explicit(self, monkeypatch):
        from sandcastle.engine.providers import effective_model

        monkeypatch.setattr(settings, "workflow_default_model", "nim/ornith")
        assert effective_model("opus") == "opus"
        assert effective_model("mistral/large") == "mistral/large"

    def test_effective_model_empty_falls_through_to_autoroute(self, monkeypatch):
        import sandcastle.engine.providers as providers

        monkeypatch.setattr(settings, "workflow_default_model", "")
        monkeypatch.setattr(
            providers, "maybe_spark_nim_route", lambda m: "AUTOROUTED" if m == "sonnet" else m
        )
        assert providers.effective_model("sonnet") == "AUTOROUTED"

    def test_dynamic_ollama_model_resolves(self):
        from sandcastle.engine.providers import resolve_model

        info = resolve_model("ollama/qwen3:8b")
        assert info.provider == "ollama"
        assert info.api_model_id == "qwen3:8b"
        assert info.region == "local"
        assert info.input_price_per_m == 0.0

    def test_dynamic_ollama_rejects_traversal(self):
        from sandcastle.engine.providers import resolve_model

        with pytest.raises(KeyError):
            resolve_model("ollama/../etc/passwd")

    def test_settings_patch_rejects_unknown_model(self):
        client = _make_client()
        resp = client.patch(
            "/settings", json={"workflow_default_model": "definitely-not-a-model"}
        )
        assert resp.status_code == 400
        assert "UNKNOWN_MODEL" in resp.text

    def test_settings_patch_accepts_dynamic_models(self, monkeypatch):
        client = _make_client()
        for model in ("nim/ornith", "ollama/qwen3:8b", "opus"):
            resp = client.patch("/settings", json={"workflow_default_model": model})
            assert resp.status_code == 200, f"{model}: {resp.text}"
            assert resp.json()["data"]["workflow_default_model"] == model
        # empty string clears it
        resp = client.patch("/settings", json={"workflow_default_model": ""})
        assert resp.status_code == 200

    def test_health_providers_includes_models_list(self, monkeypatch):
        """Local provider entries carry a models list when the probe returns one."""
        client = _make_client()
        _clear_health_cache()
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = MagicMock(
                return_value={
                    "models": [{"name": "qwen3:8b"}, {"name": "gpt-oss:120b"}],
                    "data": [{"id": "ornith"}, {"id": "aeon"}],
                }
            )
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_instance.post = AsyncMock(return_value=mock_resp)
            mock_instance.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_instance
            resp = client.get("/health/providers")
        data = resp.json()["data"]
        assert data["ollama"]["models"] == ["qwen3:8b", "gpt-oss:120b"]
        assert data["nim"]["models"] == ["ornith", "aeon"]
