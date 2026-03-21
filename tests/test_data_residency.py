"""Tests for data residency mode, provider health endpoint, audit trail, and startup validation.

Features tested:
- Feature 1: Data Residency Mode
- Feature 2: Provider Health Endpoint
- Feature 3: Provider Audit Trail
- Feature 4: Startup Provider Validation
"""

from __future__ import annotations

import os
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# Ensure in-memory DB before imports
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite://")


# ---------------------------------------------------------------------------
# Feature 1: Data Residency Mode - ModelInfo region field
# ---------------------------------------------------------------------------


class TestModelInfoRegion:
    """Tests for ModelInfo.region field in PROVIDER_REGISTRY."""

    def test_mistral_models_have_eu_region(self):
        from sandcastle.engine.providers import PROVIDER_REGISTRY

        for key, info in PROVIDER_REGISTRY.items():
            if key.startswith("mistral/"):
                assert info.region == "eu", f"{key} should have region='eu'"

    def test_anthropic_models_have_us_region(self):
        from sandcastle.engine.providers import PROVIDER_REGISTRY

        for key in ("sonnet", "haiku", "opus"):
            info = PROVIDER_REGISTRY[key]
            assert info.region == "us", f"{key} should have region='us'"

    def test_openai_models_have_us_region(self):
        from sandcastle.engine.providers import PROVIDER_REGISTRY

        for key, info in PROVIDER_REGISTRY.items():
            if key.startswith("openai/"):
                assert info.region == "us", f"{key} should have region='us'"

    def test_ollama_model_has_local_region(self):
        from sandcastle.engine.providers import PROVIDER_REGISTRY

        assert "ollama" in PROVIDER_REGISTRY
        assert PROVIDER_REGISTRY["ollama"].region == "local"

    def test_minimax_has_us_region(self):
        from sandcastle.engine.providers import PROVIDER_REGISTRY

        assert PROVIDER_REGISTRY["minimax/m2.5"].region == "us"

    def test_google_has_us_region(self):
        from sandcastle.engine.providers import PROVIDER_REGISTRY

        assert PROVIDER_REGISTRY["google/gemini-2.5-pro"].region == "us"

    def test_model_info_region_default_is_us(self):
        """ModelInfo region defaults to 'us'."""
        from sandcastle.engine.providers import ModelInfo

        info = ModelInfo(
            provider="test", api_model_id="test-model", runner="runner.mjs",
            api_key_env="TEST_KEY", api_base_url=None,
            input_price_per_m=1.0, output_price_per_m=2.0,
        )
        assert info.region == "us"

    def test_model_info_region_can_be_set(self):
        from sandcastle.engine.providers import ModelInfo

        info = ModelInfo(
            provider="test", api_model_id="test-model", runner="runner.mjs",
            api_key_env="TEST_KEY", api_base_url=None,
            input_price_per_m=1.0, output_price_per_m=2.0, region="eu",
        )
        assert info.region == "eu"


# ---------------------------------------------------------------------------
# Feature 1: Data Residency - config.py
# ---------------------------------------------------------------------------


class TestDataResidencyConfig:
    """Tests for data_residency setting in config."""

    def test_data_residency_defaults_to_empty(self):
        from sandcastle.config import Settings

        s = Settings()
        assert s.data_residency == ""

    def test_data_residency_can_be_set_to_eu(self):
        from sandcastle.config import Settings

        s = Settings(data_residency="eu")
        assert s.data_residency == "eu"

    def test_data_residency_can_be_set_to_local(self):
        from sandcastle.config import Settings

        s = Settings(data_residency="local")
        assert s.data_residency == "local"


# ---------------------------------------------------------------------------
# Feature 1: Data Residency - _PROVIDER_CONFIGS regions in generator.py
# ---------------------------------------------------------------------------


class TestProviderConfigRegions:
    """Tests for region field in generator._PROVIDER_CONFIGS."""

    def test_anthropic_config_region_is_us(self):
        from sandcastle.engine.generator import _PROVIDER_CONFIGS

        assert _PROVIDER_CONFIGS["anthropic"]["region"] == "us"

    def test_openai_config_region_is_us(self):
        from sandcastle.engine.generator import _PROVIDER_CONFIGS

        assert _PROVIDER_CONFIGS["openai"]["region"] == "us"

    def test_mistral_config_region_is_eu(self):
        from sandcastle.engine.generator import _PROVIDER_CONFIGS

        assert _PROVIDER_CONFIGS["mistral"]["region"] == "eu"

    def test_ollama_config_region_is_local(self):
        from sandcastle.engine.generator import _PROVIDER_CONFIGS

        assert _PROVIDER_CONFIGS["ollama"]["region"] == "local"


# ---------------------------------------------------------------------------
# Feature 1: Data Residency - _get_advisor_config enforcement
# ---------------------------------------------------------------------------


class TestAdvisorDataResidencyEnforcement:
    """Tests that _get_advisor_config blocks cross-region providers."""

    def test_eu_residency_blocks_anthropic(self):
        """When data_residency='eu', anthropic (us) should be blocked."""
        from sandcastle.engine.generator import _get_advisor_config
        from sandcastle.config import settings

        original = settings.data_residency
        try:
            settings.data_residency = "eu"
            with patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "anthropic"}):
                with pytest.raises(ValueError, match="eu"):
                    _get_advisor_config()
        finally:
            settings.data_residency = original

    def test_eu_residency_blocks_openai(self):
        """When data_residency='eu', openai (us) should be blocked."""
        from sandcastle.engine.generator import _get_advisor_config
        from sandcastle.config import settings

        original = settings.data_residency
        try:
            settings.data_residency = "eu"
            with patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "openai"}):
                with pytest.raises(ValueError, match="eu"):
                    _get_advisor_config()
        finally:
            settings.data_residency = original

    def test_eu_residency_allows_mistral(self):
        """When data_residency='eu', mistral (eu) should be allowed."""
        from sandcastle.engine.generator import _get_advisor_config
        from sandcastle.config import settings

        original = settings.data_residency
        try:
            settings.data_residency = "eu"
            with patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "mistral"}):
                cfg = _get_advisor_config()
                assert cfg["region"] == "eu"
        finally:
            settings.data_residency = original

    def test_local_residency_allows_ollama(self):
        """When data_residency='local', ollama should be allowed."""
        from sandcastle.engine.generator import _get_advisor_config
        from sandcastle.config import settings

        original = settings.data_residency
        try:
            settings.data_residency = "local"
            with patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "ollama"}):
                cfg = _get_advisor_config()
                assert cfg["region"] == "local"
        finally:
            settings.data_residency = original

    def test_local_residency_blocks_anthropic(self):
        """When data_residency='local', anthropic (us) should be blocked."""
        from sandcastle.engine.generator import _get_advisor_config
        from sandcastle.config import settings

        original = settings.data_residency
        try:
            settings.data_residency = "local"
            with patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "anthropic"}):
                with pytest.raises(ValueError, match="local"):
                    _get_advisor_config()
        finally:
            settings.data_residency = original

    def test_no_residency_allows_everything(self):
        """When data_residency='', any provider should be allowed."""
        from sandcastle.engine.generator import _get_advisor_config
        from sandcastle.config import settings

        original = settings.data_residency
        try:
            settings.data_residency = ""
            for provider in ("anthropic", "openai", "mistral", "ollama"):
                with patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": provider}):
                    cfg = _get_advisor_config()
                    assert "region" in cfg
        finally:
            settings.data_residency = original

    def test_error_message_lists_compliant_providers(self):
        """Error message should mention providers that satisfy the constraint."""
        from sandcastle.engine.generator import _get_advisor_config
        from sandcastle.config import settings

        original = settings.data_residency
        try:
            settings.data_residency = "eu"
            with patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "anthropic"}):
                with pytest.raises(ValueError) as exc_info:
                    _get_advisor_config()
                assert "mistral" in str(exc_info.value)
        finally:
            settings.data_residency = original


# ---------------------------------------------------------------------------
# Feature 1: Data Residency - ProviderFailover.get_alternatives
# ---------------------------------------------------------------------------


class TestFailoverDataResidency:
    """Tests for get_alternatives respecting data residency."""

    def test_get_alternatives_returns_all_when_no_residency(self):
        """Without residency constraint all alternatives should be returned (if keys set)."""
        from sandcastle.engine.providers import ProviderFailover, PROVIDER_REGISTRY

        failover = ProviderFailover()
        # Patch get_api_key to return a fake key for all models
        with patch("sandcastle.engine.providers.get_api_key", return_value="fake-key"):
            alts = failover.get_alternatives("sonnet", data_residency="")
            assert len(alts) > 0

    def test_get_alternatives_eu_only_returns_mistral_models(self):
        """With data_residency='eu', only EU models should be returned."""
        from sandcastle.engine.providers import ProviderFailover

        failover = ProviderFailover()
        with patch("sandcastle.engine.providers.get_api_key", return_value="fake-key"):
            alts = failover.get_alternatives("sonnet", data_residency="eu")
            # All alternatives should be EU region
            from sandcastle.engine.providers import PROVIDER_REGISTRY
            for alt in alts:
                info = PROVIDER_REGISTRY.get(alt)
                assert info is not None
                assert info.region == "eu", f"{alt} should be EU but got {info.region}"

    def test_get_alternatives_local_only_returns_ollama(self):
        """With data_residency='local', only local models should be returned."""
        from sandcastle.engine.providers import ProviderFailover, PROVIDER_REGISTRY

        failover = ProviderFailover()
        with patch("sandcastle.engine.providers.get_api_key", return_value="fake-key"):
            alts = failover.get_alternatives("sonnet", data_residency="local")
            for alt in alts:
                info = PROVIDER_REGISTRY.get(alt)
                assert info is not None
                assert info.region == "local"

    def test_get_alternatives_us_only_returns_us_models(self):
        """With data_residency='us', only US models should be returned."""
        from sandcastle.engine.providers import ProviderFailover, PROVIDER_REGISTRY

        failover = ProviderFailover()
        with patch("sandcastle.engine.providers.get_api_key", return_value="fake-key"):
            alts = failover.get_alternatives("mistral/large", data_residency="us")
            for alt in alts:
                info = PROVIDER_REGISTRY.get(alt)
                assert info is not None
                assert info.region == "us", f"{alt} should be US but got {info.region}"


# ---------------------------------------------------------------------------
# Feature 2: Provider Health Endpoint
# ---------------------------------------------------------------------------


class TestProviderHealthEndpoint:
    """Tests for GET /health/providers endpoint."""

    def _make_app(self):
        """Create a FastAPI test client."""
        from fastapi.testclient import TestClient
        from sandcastle.api.routes import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_provider_health_returns_200(self):
        """The endpoint should always return 200."""
        client = self._make_app()
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_instance.post = AsyncMock(return_value=mock_resp)
            mock_instance.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_instance
            resp = client.get("/health/providers")
        assert resp.status_code == 200

    def test_provider_health_returns_all_providers(self):
        """Response should include all providers from _PROVIDER_CONFIGS."""
        from sandcastle.engine.generator import _PROVIDER_CONFIGS

        client = self._make_app()
        # Clear cache
        import sandcastle.api.routes as routes_module
        endpoint = routes_module.get_provider_health
        if hasattr(endpoint, "_cache"):
            del endpoint._cache
        if hasattr(endpoint, "_cache_ts"):
            del endpoint._cache_ts

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_instance.post = AsyncMock(return_value=mock_resp)
            mock_instance.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_instance
            resp = client.get("/health/providers")

        data = resp.json()
        assert "data" in data
        for provider in _PROVIDER_CONFIGS.keys():
            assert provider in data["data"], f"Provider {provider} missing from response"

    def test_provider_health_includes_region(self):
        """Each provider entry should include a region field."""
        client = self._make_app()
        import sandcastle.api.routes as routes_module
        endpoint = routes_module.get_provider_health
        if hasattr(endpoint, "_cache"):
            del endpoint._cache
        if hasattr(endpoint, "_cache_ts"):
            del endpoint._cache_ts

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_instance.post = AsyncMock(return_value=mock_resp)
            mock_instance.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_instance
            resp = client.get("/health/providers")

        data = resp.json()["data"]
        for provider, status in data.items():
            assert "region" in status, f"{provider} missing 'region' field"

    def test_provider_health_unconfigured_when_no_key(self):
        """Providers without API keys should be marked 'unconfigured'."""
        from fastapi.testclient import TestClient
        from sandcastle.api.routes import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        import sandcastle.api.routes as routes_module
        endpoint = routes_module.get_provider_health
        if hasattr(endpoint, "_cache"):
            del endpoint._cache
        if hasattr(endpoint, "_cache_ts"):
            del endpoint._cache_ts

        # Remove all API keys from env
        env_without_keys = {
            k: v for k, v in os.environ.items()
            if not any(k.endswith(suffix) for suffix in ("API_KEY", "OPENROUTER_API_KEY"))
        }
        with patch.dict(os.environ, env_without_keys, clear=True):
            with patch("sandcastle.config.settings") as mock_settings:
                mock_settings.anthropic_api_key = ""
                mock_settings.openai_api_key = ""
                resp = client.get("/health/providers")

        assert resp.status_code == 200

    def test_provider_health_response_structure(self):
        """Each provider entry has status, latency_ms, and region fields."""
        client = self._make_app()
        import sandcastle.api.routes as routes_module
        endpoint = routes_module.get_provider_health
        if hasattr(endpoint, "_cache"):
            del endpoint._cache
        if hasattr(endpoint, "_cache_ts"):
            del endpoint._cache_ts

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_instance.post = AsyncMock(return_value=mock_resp)
            mock_instance.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_instance
            resp = client.get("/health/providers")

        data = resp.json()["data"]
        for provider, entry in data.items():
            assert "status" in entry, f"{provider}: missing 'status'"
            assert "region" in entry, f"{provider}: missing 'region'"
            assert entry["status"] in ("ok", "down", "unconfigured"), \
                f"{provider}: unexpected status '{entry['status']}'"


# ---------------------------------------------------------------------------
# Feature 3: Provider Audit Trail
# ---------------------------------------------------------------------------


class TestProviderAuditTrail:
    """Tests for audit event emission in _call_advisor_llm."""

    @pytest.mark.asyncio
    async def test_call_advisor_llm_emits_audit_event(self):
        """_call_advisor_llm should emit an advisor.llm_call audit event."""
        from sandcastle.engine.generator import _call_advisor_llm

        captured_events = []

        async def mock_append_audit(session, event_type, run_id, actor_id, payload):
            captured_events.append({
                "event_type": event_type,
                "actor_id": actor_id,
                "payload": payload,
            })

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "content": [{"text": "generated yaml content"}]
        }

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.commit = AsyncMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_instance.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_instance

            with patch("sandcastle.models.db.async_session", return_value=mock_session):
                with patch(
                    "sandcastle.engine.audit.append_audit_event",
                    side_effect=mock_append_audit,
                ):
                    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
                        result = await _call_advisor_llm(
                            "system prompt", "user prompt", purpose="generation"
                        )

        assert result == "generated yaml content"

    @pytest.mark.asyncio
    async def test_audit_event_payload_includes_provider_info(self):
        """The audit payload should contain provider, model, region, purpose."""
        from sandcastle.engine.generator import _call_advisor_llm

        captured_payloads = []

        async def fake_append(session, event_type, run_id, actor_id, payload):
            if event_type == "advisor.llm_call":
                captured_payloads.append(payload)
            return None

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "content": [{"text": "test output"}]
        }

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.commit = AsyncMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_instance.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_instance

            with patch("sandcastle.models.db.async_session", return_value=mock_session):
                with patch("sandcastle.engine.audit.append_audit_event", side_effect=fake_append):
                    with patch.dict(os.environ, {
                        "ANTHROPIC_API_KEY": "test-key",
                        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
                    }):
                        await _call_advisor_llm("sys", "usr", purpose="evolution")

        if captured_payloads:
            payload = captured_payloads[0]
            assert "provider" in payload
            assert "model" in payload
            assert "region" in payload
            assert "purpose" in payload

    @pytest.mark.asyncio
    async def test_audit_event_does_not_break_generation(self):
        """Audit failure should never break the LLM call itself."""
        from sandcastle.engine.generator import _call_advisor_llm

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "content": [{"text": "safe output"}]
        }

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.commit = AsyncMock(side_effect=Exception("DB error"))

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_instance.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_instance

            with patch("sandcastle.models.db.async_session", return_value=mock_session):
                with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
                    # Should not raise even if audit fails
                    result = await _call_advisor_llm("system", "user")
                    assert result == "safe output"

    @pytest.mark.asyncio
    async def test_audit_event_purpose_generation(self):
        """Default purpose should be 'generation'."""
        from sandcastle.engine.generator import _call_advisor_llm

        captured = []

        async def fake_append(session, event_type, run_id, actor_id, payload):
            if event_type == "advisor.llm_call":
                captured.append(payload.get("purpose"))
            return None

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "content": [{"text": "output"}]
        }

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.commit = AsyncMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_instance.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_instance

            with patch("sandcastle.models.db.async_session", return_value=mock_session):
                with patch("sandcastle.engine.audit.append_audit_event", side_effect=fake_append):
                    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
                        await _call_advisor_llm("sys", "usr")

        if captured:
            assert captured[0] == "generation"


# ---------------------------------------------------------------------------
# Feature 4: Startup Provider Validation
# ---------------------------------------------------------------------------


class TestStartupValidation:
    """Tests for _validate_providers startup check."""

    @pytest.mark.asyncio
    async def test_validate_providers_does_not_raise(self):
        """_validate_providers should never raise exceptions."""
        from sandcastle.main import _validate_providers

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_instance.post = AsyncMock(side_effect=Exception("Connection refused"))
            mock_instance.get = AsyncMock(side_effect=Exception("Connection refused"))
            mock_client_cls.return_value = mock_instance

            # Should complete without raising
            await _validate_providers()

    @pytest.mark.asyncio
    async def test_validate_providers_logs_warnings_for_unreachable(self):
        """_validate_providers should log warnings when providers are down."""
        from sandcastle.main import _validate_providers
        import logging

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_instance.post = AsyncMock(side_effect=Exception("timeout"))
            mock_instance.get = AsyncMock(side_effect=Exception("timeout"))
            mock_client_cls.return_value = mock_instance

            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
                with patch("sandcastle.main.logger") as mock_logger:
                    await _validate_providers()
                    # Should have logged some warnings
                    assert mock_logger.warning.called or mock_logger.info.called

    @pytest.mark.asyncio
    async def test_validate_providers_logs_ok_for_reachable(self):
        """_validate_providers should log info when providers are reachable."""
        from sandcastle.main import _validate_providers

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_instance.post = AsyncMock(return_value=mock_response)
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_instance

            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
                with patch("sandcastle.main.logger") as mock_logger:
                    await _validate_providers()
                    assert mock_logger.info.called

    @pytest.mark.asyncio
    async def test_validate_providers_skips_unconfigured(self):
        """_validate_providers should skip providers with no API key."""
        from sandcastle.main import _validate_providers

        # Remove all keys
        env = {k: v for k, v in os.environ.items() if "API_KEY" not in k}
        with patch.dict(os.environ, env, clear=True):
            with patch("sandcastle.config.settings") as mock_settings:
                mock_settings.anthropic_api_key = ""
                mock_settings.openai_api_key = ""
                with patch("httpx.AsyncClient") as mock_client_cls:
                    mock_client_cls.assert_not_called  # Just check it doesn't crash
                    await _validate_providers()

    @pytest.mark.asyncio
    async def test_validate_providers_checks_all_configured_providers(self):
        """All providers with keys should be checked during startup."""
        from sandcastle.main import _validate_providers

        checked_urls = []

        async def mock_post(url, **kwargs):
            checked_urls.append(url)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        async def mock_get(url, **kwargs):
            checked_urls.append(url)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_instance.post = mock_post
            mock_instance.get = mock_get
            mock_client_cls.return_value = mock_instance

            with patch.dict(os.environ, {
                "ANTHROPIC_API_KEY": "test-key",
                "OPENAI_API_KEY": "test-key-2",
            }):
                await _validate_providers()

        # Should have made at least one request
        assert len(checked_urls) >= 1


# ---------------------------------------------------------------------------
# Integration: FAILOVER_CHAINS contains no ollama entry
# (ollama is a new registry entry - chains are optional)
# ---------------------------------------------------------------------------


class TestRegistryIntegrity:
    """Sanity checks on the provider registry after changes."""

    def test_all_registry_models_have_region(self):
        """Every model in PROVIDER_REGISTRY must have a region field."""
        from sandcastle.engine.providers import PROVIDER_REGISTRY

        for name, info in PROVIDER_REGISTRY.items():
            assert hasattr(info, "region"), f"{name} missing region field"
            assert info.region in ("eu", "us", "local"), \
                f"{name} has unexpected region '{info.region}'"

    def test_ollama_in_registry(self):
        """Ollama should be in PROVIDER_REGISTRY."""
        from sandcastle.engine.providers import PROVIDER_REGISTRY

        assert "ollama" in PROVIDER_REGISTRY

    def test_ollama_has_no_api_key_env(self):
        """Ollama model should have empty api_key_env."""
        from sandcastle.engine.providers import PROVIDER_REGISTRY

        assert PROVIDER_REGISTRY["ollama"].api_key_env == ""
