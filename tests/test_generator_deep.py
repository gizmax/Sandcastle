"""Deep tests for the AI Workflow Generator (generator.py).

Covers: _call_advisor_llm, _get_advisor_config, _build_providers_to_try,
_resolve_api_key_for_provider, generate_workflow, generate_chat, explain_error,
provider configs, SLO routing, request/response building, and secret scrubbing.
"""

from __future__ import annotations

import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from sandcastle.engine.generator import (
    ADVISOR_MODEL_TIERS,
    ADVISOR_QUALITY_TIERS,
    GenerateResult,
    _PROVIDER_CONFIGS,
    _build_providers_to_try,
    _build_request_body,
    _get_advisor_config,
    _is_anthropic_provider,
    _parse_response_text,
    _resolve_api_key,
    _resolve_api_key_for_provider,
    _resolve_model_for_tier,
    _resolve_provider_name,
    _scrub_secrets,
    _strip_fencing,
    explain_error,
    generate_chat,
    generate_workflow,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CLEAR_ENV = {
    "SANDCASTLE_ADVISOR_PROVIDER": "",
    "SANDCASTLE_ADVISOR_MODEL": "",
    "ANTHROPIC_API_KEY": "",
    "OPENAI_API_KEY": "",
    "MISTRAL_API_KEY": "",
    "OPENROUTER_API_KEY": "",
    "MINIMAX_API_KEY": "",
}

VALID_YAML = """\
name: test-workflow
description: A test workflow

default_model: sonnet
default_max_turns: 10
default_timeout: 300

input_schema:
  required: ["topic"]
  properties:
    topic:
      type: string
      description: "Test topic"

steps:
  - id: step-one
    prompt: "Do something with {input.topic}"
    model: sonnet
    max_turns: 5
"""

SETTINGS_NO_RESIDENCY = MagicMock(
    data_residency="",
    anthropic_api_key="",
    openai_api_key="",
    mistral_api_key="",
    openrouter_api_key="",
    minimax_api_key="",
    advisor_quality_mode="auto",
)


def _mock_httpx_response(data: dict, status_code: int = 200):
    """Build a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _anthropic_ok(text: str):
    """Anthropic-format successful response data."""
    return {"content": [{"text": text}]}


def _openai_ok(text: str):
    """OpenAI-format successful response data."""
    return {"choices": [{"message": {"content": text}}]}


# ===================================================================
# _PROVIDER_CONFIGS completeness
# ===================================================================


class TestProviderConfigs:
    """Verify every provider in _PROVIDER_CONFIGS has required fields."""

    def test_all_providers_have_api_url(self):
        for name, cfg in _PROVIDER_CONFIGS.items():
            assert "api_url" in cfg, f"{name} missing api_url"
            url = cfg["api_url"]
            # Local providers use runtime-resolved placeholders ({omlx_base_url},
            # {ollama_host}) so they don't start with http. Resolve them the same
            # way production does before validating the URL shape.
            if "{" in url:
                from sandcastle.engine.generator import resolve_provider_api_url
                url = resolve_provider_api_url(cfg)
            assert url.startswith("http"), f"{name} api_url invalid"

    def test_all_providers_have_model(self):
        for name, cfg in _PROVIDER_CONFIGS.items():
            assert "model" in cfg, f"{name} missing default model"
            assert isinstance(cfg["model"], str) and cfg["model"], f"{name} model empty"

    def test_all_providers_have_headers_fn(self):
        for name, cfg in _PROVIDER_CONFIGS.items():
            assert callable(cfg["headers_fn"]), f"{name} headers_fn not callable"

    def test_all_providers_have_region(self):
        for name, cfg in _PROVIDER_CONFIGS.items():
            assert "region" in cfg, f"{name} missing region"
            assert cfg["region"] in ("us", "eu", "local"), f"{name} unknown region"

    def test_anthropic_headers_contain_version(self):
        headers = _PROVIDER_CONFIGS["anthropic"]["headers_fn"]("test-key")
        assert headers["x-api-key"] == "test-key"
        assert "anthropic-version" in headers

    def test_openai_headers_contain_bearer(self):
        headers = _PROVIDER_CONFIGS["openai"]["headers_fn"]("test-key")
        assert headers["Authorization"] == "Bearer test-key"

    def test_mistral_headers_contain_bearer(self):
        headers = _PROVIDER_CONFIGS["mistral"]["headers_fn"]("test-key")
        assert headers["Authorization"] == "Bearer test-key"

    def test_ollama_headers_no_auth(self):
        headers = _PROVIDER_CONFIGS["ollama"]["headers_fn"]("unused")
        assert "Authorization" not in headers
        assert headers["Content-Type"] == "application/json"

    def test_ollama_needs_no_api_key(self):
        assert _PROVIDER_CONFIGS["ollama"]["api_key_env"] == ""

    def test_mistral_region_is_eu(self):
        assert _PROVIDER_CONFIGS["mistral"]["region"] == "eu"

    def test_ollama_region_is_local(self):
        assert _PROVIDER_CONFIGS["ollama"]["region"] == "local"

    def test_google_uses_openrouter(self):
        assert "openrouter" in _PROVIDER_CONFIGS["google"]["api_url"]

    def test_minimax_config_present(self):
        assert "minimax" in _PROVIDER_CONFIGS
        assert "MiniMax" in _PROVIDER_CONFIGS["minimax"]["model"]


# ===================================================================
# _resolve_provider_name
# ===================================================================


class TestResolveProviderName:
    def test_defaults_to_anthropic(self):
        with patch.dict("os.environ", {**_CLEAR_ENV}):
            assert _resolve_provider_name() == "anthropic"

    def test_respects_env_var(self):
        with patch.dict("os.environ", {**_CLEAR_ENV, "SANDCASTLE_ADVISOR_PROVIDER": "mistral"}):
            assert _resolve_provider_name() == "mistral"

    def test_unknown_provider_falls_back_to_anthropic(self):
        with patch.dict("os.environ", {**_CLEAR_ENV, "SANDCASTLE_ADVISOR_PROVIDER": "nonexistent"}):
            assert _resolve_provider_name() == "anthropic"

    def test_case_insensitive(self):
        with patch.dict("os.environ", {**_CLEAR_ENV, "SANDCASTLE_ADVISOR_PROVIDER": "OPENAI"}):
            assert _resolve_provider_name() == "openai"


# ===================================================================
# _get_advisor_config
# ===================================================================


class TestGetAdvisorConfig:
    def test_default_config_is_anthropic(self):
        with patch.dict("os.environ", {**_CLEAR_ENV}), \
             patch("sandcastle.config.settings", SETTINGS_NO_RESIDENCY):
            config = _get_advisor_config()
            assert config["api_url"] == "https://api.anthropic.com/v1/messages"

    def test_openai_config(self):
        with patch.dict("os.environ", {**_CLEAR_ENV, "SANDCASTLE_ADVISOR_PROVIDER": "openai"}), \
             patch("sandcastle.config.settings", SETTINGS_NO_RESIDENCY):
            config = _get_advisor_config()
            assert "openai.com" in config["api_url"]
            assert config["model"] == "gpt-5.2"

    def test_model_override(self):
        with patch.dict("os.environ", {
            **_CLEAR_ENV,
            "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
            "SANDCASTLE_ADVISOR_MODEL": "claude-opus-99",
        }), patch("sandcastle.config.settings", SETTINGS_NO_RESIDENCY):
            config = _get_advisor_config()
            assert config["model"] == "claude-opus-99"

    def test_unknown_provider_defaults_to_anthropic(self):
        with patch.dict("os.environ", {
            **_CLEAR_ENV, "SANDCASTLE_ADVISOR_PROVIDER": "totally-fake",
        }), patch("sandcastle.config.settings", SETTINGS_NO_RESIDENCY):
            config = _get_advisor_config()
            assert config["api_url"] == "https://api.anthropic.com/v1/messages"

    def test_data_residency_eu_allows_mistral(self):
        settings = MagicMock(data_residency="eu")
        with patch.dict("os.environ", {
            **_CLEAR_ENV, "SANDCASTLE_ADVISOR_PROVIDER": "mistral",
        }), patch("sandcastle.config.settings", settings):
            config = _get_advisor_config()
            assert config["region"] == "eu"

    def test_data_residency_eu_blocks_anthropic(self):
        settings = MagicMock(data_residency="eu")
        with patch.dict("os.environ", {
            **_CLEAR_ENV, "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
        }), patch("sandcastle.config.settings", settings):
            with pytest.raises(ValueError, match="residency"):
                _get_advisor_config()

    def test_data_residency_eu_error_lists_compliant_providers(self):
        settings = MagicMock(data_residency="eu")
        with patch.dict("os.environ", {
            **_CLEAR_ENV, "SANDCASTLE_ADVISOR_PROVIDER": "openai",
        }), patch("sandcastle.config.settings", settings):
            with pytest.raises(ValueError, match="mistral"):
                _get_advisor_config()

    def test_data_residency_local_allows_ollama(self):
        settings = MagicMock(data_residency="local")
        with patch.dict("os.environ", {
            **_CLEAR_ENV, "SANDCASTLE_ADVISOR_PROVIDER": "ollama",
        }), patch("sandcastle.config.settings", settings):
            config = _get_advisor_config()
            assert config["region"] == "local"

    def test_data_residency_local_blocks_openai(self):
        settings = MagicMock(data_residency="local")
        with patch.dict("os.environ", {
            **_CLEAR_ENV, "SANDCASTLE_ADVISOR_PROVIDER": "openai",
        }), patch("sandcastle.config.settings", settings):
            with pytest.raises(ValueError, match="residency"):
                _get_advisor_config()


# ===================================================================
# _resolve_api_key_for_provider
# ===================================================================


class TestResolveApiKeyForProvider:
    def test_anthropic_key_from_env(self):
        with patch.dict("os.environ", {**_CLEAR_ENV, "ANTHROPIC_API_KEY": "sk-ant-xxx"}):
            assert _resolve_api_key_for_provider("anthropic") == "sk-ant-xxx"

    def test_openai_key_from_env(self):
        with patch.dict("os.environ", {**_CLEAR_ENV, "OPENAI_API_KEY": "sk-oai-xxx"}):
            assert _resolve_api_key_for_provider("openai") == "sk-oai-xxx"

    def test_mistral_key_from_env(self):
        with patch.dict("os.environ", {**_CLEAR_ENV, "MISTRAL_API_KEY": "mist-xxx"}):
            assert _resolve_api_key_for_provider("mistral") == "mist-xxx"

    def test_ollama_returns_no_key_required(self):
        with patch.dict("os.environ", _CLEAR_ENV):
            assert _resolve_api_key_for_provider("ollama") == "no-key-required"

    def test_missing_key_returns_empty(self):
        settings = MagicMock(anthropic_api_key="", openai_api_key="", mistral_api_key="")
        with patch.dict("os.environ", _CLEAR_ENV), \
             patch("sandcastle.config.settings", settings):
            assert _resolve_api_key_for_provider("anthropic") == ""

    def test_key_falls_back_to_settings(self):
        settings = MagicMock(anthropic_api_key="from-settings")
        with patch.dict("os.environ", _CLEAR_ENV), \
             patch("sandcastle.config.settings", settings):
            assert _resolve_api_key_for_provider("anthropic") == "from-settings"

    def test_unknown_provider_returns_empty(self):
        with patch.dict("os.environ", _CLEAR_ENV):
            # Unknown provider has no config, cfg.get("api_key_env") returns ""
            result = _resolve_api_key_for_provider("nonexistent")
            assert result == "no-key-required"  # empty key_env -> no-key-required

    def test_openrouter_key_from_env(self):
        with patch.dict("os.environ", {**_CLEAR_ENV, "OPENROUTER_API_KEY": "or-xxx"}):
            assert _resolve_api_key_for_provider("google") == "or-xxx"

    def test_minimax_key_from_env(self):
        with patch.dict("os.environ", {**_CLEAR_ENV, "MINIMAX_API_KEY": "mm-xxx"}):
            assert _resolve_api_key_for_provider("minimax") == "mm-xxx"


# ===================================================================
# _build_providers_to_try
# ===================================================================


class TestBuildProvidersToTry:
    def test_primary_always_first(self):
        with patch.dict("os.environ", {**_CLEAR_ENV, "ANTHROPIC_API_KEY": "k"}):
            providers = _build_providers_to_try("anthropic", "")
            assert providers[0] == "anthropic"

    def test_includes_providers_with_keys(self):
        with patch.dict("os.environ", {
            **_CLEAR_ENV,
            "ANTHROPIC_API_KEY": "k1",
            "OPENAI_API_KEY": "k2",
        }):
            providers = _build_providers_to_try("anthropic", "")
            assert "openai" in providers

    def test_excludes_providers_without_keys(self):
        with patch.dict("os.environ", {**_CLEAR_ENV, "ANTHROPIC_API_KEY": "k"}), \
             patch("sandcastle.config.settings", SETTINGS_NO_RESIDENCY):
            providers = _build_providers_to_try("anthropic", "")
            # OpenAI has no key, should not be in list (except ollama which needs no key)
            assert "openai" not in providers

    def test_ollama_included_without_key(self):
        with patch.dict("os.environ", {**_CLEAR_ENV, "ANTHROPIC_API_KEY": "k"}), \
             patch("sandcastle.config.settings", SETTINGS_NO_RESIDENCY):
            providers = _build_providers_to_try("anthropic", "")
            # Ollama returns "no-key-required" which is truthy
            assert "ollama" in providers

    def test_residency_eu_filters_non_eu(self):
        with patch.dict("os.environ", {
            **_CLEAR_ENV,
            "ANTHROPIC_API_KEY": "k1",
            "MISTRAL_API_KEY": "k2",
            "OPENAI_API_KEY": "k3",
        }):
            providers = _build_providers_to_try("mistral", "eu")
            # Primary (mistral, eu) is always first. Non-eu providers excluded.
            assert providers[0] == "mistral"
            assert "openai" not in providers
            assert "anthropic" not in providers

    def test_residency_local_only_ollama(self):
        with patch.dict("os.environ", {
            **_CLEAR_ENV,
            "ANTHROPIC_API_KEY": "k1",
        }):
            providers = _build_providers_to_try("ollama", "local")
            assert "ollama" in providers
            assert "anthropic" not in providers

    def test_no_residency_allows_all_with_keys(self):
        with patch.dict("os.environ", {
            **_CLEAR_ENV,
            "ANTHROPIC_API_KEY": "k1",
            "OPENAI_API_KEY": "k2",
            "MISTRAL_API_KEY": "k3",
        }):
            providers = _build_providers_to_try("anthropic", "")
            assert len(providers) >= 3


# ===================================================================
# _resolve_model_for_tier / SLO routing
# ===================================================================


class TestSloRouting:
    def test_anthropic_high_tier(self):
        model = _resolve_model_for_tier("anthropic", "high")
        assert "sonnet" in model or "opus" in model

    def test_anthropic_low_tier(self):
        model = _resolve_model_for_tier("anthropic", "low")
        assert "haiku" in model

    def test_mistral_high_tier(self):
        model = _resolve_model_for_tier("mistral", "high")
        assert "large" in model

    def test_openai_medium_tier(self):
        model = _resolve_model_for_tier("openai", "medium")
        assert "mini" in model

    def test_unknown_provider_returns_none(self):
        assert _resolve_model_for_tier("nonexistent", "high") is None

    def test_all_tiers_defined_for_known_providers(self):
        for provider_name, tiers in ADVISOR_MODEL_TIERS.items():
            for tier in ("high", "medium", "low"):
                assert tier in tiers, f"{provider_name} missing tier {tier}"

    def test_quality_tiers_cover_all_purposes(self):
        expected = {"generation", "chat", "explain", "evolution", "judge"}
        assert set(ADVISOR_QUALITY_TIERS.keys()) == expected

    def test_judge_gets_low_tier(self):
        assert ADVISOR_QUALITY_TIERS["judge"] == "low"

    def test_generation_gets_high_tier(self):
        assert ADVISOR_QUALITY_TIERS["generation"] == "high"


# ===================================================================
# _build_request_body / _parse_response_text
# ===================================================================


class TestRequestResponseBuilding:
    def test_anthropic_body_has_system_field(self):
        body = _build_request_body("model-x", "sys prompt", [{"role": "user", "content": "hi"}],
                                   is_anthropic=True)
        assert body["system"] == "sys prompt"
        assert body["model"] == "model-x"
        assert body["messages"] == [{"role": "user", "content": "hi"}]

    def test_openai_body_prepends_system_message(self):
        body = _build_request_body("gpt-4o", "sys prompt", [{"role": "user", "content": "hi"}],
                                   is_anthropic=False)
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][0]["content"] == "sys prompt"
        assert body["messages"][1]["role"] == "user"
        assert "system" not in body  # no top-level system field

    def test_max_tokens_passed(self):
        body = _build_request_body("m", "s", [], max_tokens=8192, is_anthropic=True)
        assert body["max_tokens"] == 8192

    def test_parse_anthropic_response(self):
        data = {"content": [{"text": "hello world"}]}
        assert _parse_response_text(data, is_anthropic=True) == "hello world"

    def test_parse_openai_response(self):
        data = {"choices": [{"message": {"content": "hello world"}}]}
        assert _parse_response_text(data, is_anthropic=False) == "hello world"


# ===================================================================
# _is_anthropic_provider
# ===================================================================


class TestIsAnthropicProvider:
    def test_anthropic_detected(self):
        with patch.dict("os.environ", {**_CLEAR_ENV}), \
             patch("sandcastle.config.settings", SETTINGS_NO_RESIDENCY):
            assert _is_anthropic_provider() is True

    def test_openai_not_anthropic(self):
        with patch.dict("os.environ", {
            **_CLEAR_ENV, "SANDCASTLE_ADVISOR_PROVIDER": "openai",
        }), patch("sandcastle.config.settings", SETTINGS_NO_RESIDENCY):
            assert _is_anthropic_provider() is False


# ===================================================================
# _call_advisor_llm - failover
# ===================================================================


class TestCallAdvisorLlm:
    @pytest.mark.asyncio
    async def test_success_on_first_provider(self):
        """Primary provider succeeds immediately."""
        from sandcastle.engine.generator import _call_advisor_llm

        mock_resp = _mock_httpx_response(_anthropic_ok("result-text"))
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("sandcastle.engine.generator.httpx.AsyncClient", return_value=mock_client), \
             patch.dict("os.environ", {**_CLEAR_ENV, "ANTHROPIC_API_KEY": "k"}), \
             patch("sandcastle.config.settings", SETTINGS_NO_RESIDENCY), \
             patch("sandcastle.engine.audit.append_audit_event", new_callable=AsyncMock):
            result = await _call_advisor_llm("system", "user")
            assert result == "result-text"

    @pytest.mark.asyncio
    async def test_failover_on_429(self):
        """Rate-limited primary -> falls over to next provider."""
        from sandcastle.engine.generator import _call_advisor_llm

        # First call: 429 from anthropic
        resp_429 = _mock_httpx_response({}, status_code=429)
        # Second call: success from openai
        resp_ok = _mock_httpx_response(_openai_ok("fallback-result"))

        call_count = 0

        async def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return resp_429
            return resp_ok

        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("sandcastle.engine.generator.httpx.AsyncClient", return_value=mock_client), \
             patch.dict("os.environ", {
                 **_CLEAR_ENV,
                 "ANTHROPIC_API_KEY": "k1",
                 "OPENAI_API_KEY": "k2",
             }), \
             patch("sandcastle.config.settings", SETTINGS_NO_RESIDENCY), \
             patch("sandcastle.engine.audit.append_audit_event", new_callable=AsyncMock):
            result = await _call_advisor_llm("system", "user")
            assert result == "fallback-result"

    @pytest.mark.asyncio
    async def test_failover_on_500(self):
        """Server error -> falls over to next provider."""
        from sandcastle.engine.generator import _call_advisor_llm

        resp_500 = _mock_httpx_response({}, status_code=500)
        resp_ok = _mock_httpx_response(_openai_ok("recovered"))

        call_count = 0

        async def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return resp_500
            return resp_ok

        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("sandcastle.engine.generator.httpx.AsyncClient", return_value=mock_client), \
             patch.dict("os.environ", {
                 **_CLEAR_ENV,
                 "ANTHROPIC_API_KEY": "k1",
                 "OPENAI_API_KEY": "k2",
             }), \
             patch("sandcastle.config.settings", SETTINGS_NO_RESIDENCY), \
             patch("sandcastle.engine.audit.append_audit_event", new_callable=AsyncMock):
            result = await _call_advisor_llm("system", "user")
            assert result == "recovered"

    @pytest.mark.asyncio
    async def test_non_retryable_4xx_raises(self):
        """403 should NOT be retried - raise immediately."""
        from sandcastle.engine.generator import _call_advisor_llm

        resp_403 = _mock_httpx_response({}, status_code=403)

        mock_client = AsyncMock()
        mock_client.post.return_value = resp_403
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("sandcastle.engine.generator.httpx.AsyncClient", return_value=mock_client), \
             patch.dict("os.environ", {
                 **_CLEAR_ENV,
                 "ANTHROPIC_API_KEY": "k1",
                 "OPENAI_API_KEY": "k2",
             }), \
             patch("sandcastle.config.settings", SETTINGS_NO_RESIDENCY):
            with pytest.raises(httpx.HTTPStatusError):
                await _call_advisor_llm("system", "user")

    @pytest.mark.asyncio
    async def test_all_providers_fail_raises_runtime_error(self):
        """When every provider fails, raise RuntimeError."""
        from sandcastle.engine.generator import _call_advisor_llm

        resp_500 = _mock_httpx_response({}, status_code=500)

        mock_client = AsyncMock()
        mock_client.post.return_value = resp_500
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("sandcastle.engine.generator.httpx.AsyncClient", return_value=mock_client), \
             patch.dict("os.environ", {**_CLEAR_ENV, "ANTHROPIC_API_KEY": "k1"}), \
             patch("sandcastle.config.settings", SETTINGS_NO_RESIDENCY):
            with pytest.raises(RuntimeError, match="All advisor providers failed"):
                await _call_advisor_llm("system", "user")

    @pytest.mark.asyncio
    async def test_failover_on_connect_error(self):
        """Connection error -> falls over to next provider."""
        from sandcastle.engine.generator import _call_advisor_llm

        call_count = 0

        async def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("Connection refused")
            return _mock_httpx_response(_openai_ok("recovered"))

        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("sandcastle.engine.generator.httpx.AsyncClient", return_value=mock_client), \
             patch.dict("os.environ", {
                 **_CLEAR_ENV,
                 "ANTHROPIC_API_KEY": "k1",
                 "OPENAI_API_KEY": "k2",
             }), \
             patch("sandcastle.config.settings", SETTINGS_NO_RESIDENCY), \
             patch("sandcastle.engine.audit.append_audit_event", new_callable=AsyncMock):
            result = await _call_advisor_llm("system", "user")
            assert result == "recovered"

    @pytest.mark.asyncio
    async def test_failover_on_timeout(self):
        """Timeout -> falls over to next provider."""
        from sandcastle.engine.generator import _call_advisor_llm

        call_count = 0

        async def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.TimeoutException("Read timed out")
            return _mock_httpx_response(_openai_ok("ok"))

        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("sandcastle.engine.generator.httpx.AsyncClient", return_value=mock_client), \
             patch.dict("os.environ", {
                 **_CLEAR_ENV,
                 "ANTHROPIC_API_KEY": "k1",
                 "OPENAI_API_KEY": "k2",
             }), \
             patch("sandcastle.config.settings", SETTINGS_NO_RESIDENCY), \
             patch("sandcastle.engine.audit.append_audit_event", new_callable=AsyncMock):
            result = await _call_advisor_llm("system", "user")
            assert result == "ok"

    @pytest.mark.asyncio
    async def test_slo_model_selection_auto(self):
        """Purpose 'judge' with auto quality_mode should select low-tier model."""
        from sandcastle.engine.generator import _call_advisor_llm

        captured_body = {}

        async def mock_post(url, **kwargs):
            captured_body.update(kwargs.get("json", {}))
            return _mock_httpx_response(_anthropic_ok("0.8"))

        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        settings = MagicMock(
            data_residency="", advisor_quality_mode="auto",
            anthropic_api_key="", openai_api_key="", mistral_api_key="",
            openrouter_api_key="", minimax_api_key="",
        )
        with patch("sandcastle.engine.generator.httpx.AsyncClient", return_value=mock_client), \
             patch.dict("os.environ", {**_CLEAR_ENV, "ANTHROPIC_API_KEY": "k"}), \
             patch("sandcastle.config.settings", settings), \
             patch("sandcastle.engine.audit.append_audit_event", new_callable=AsyncMock):
            await _call_advisor_llm("system", "rate this", purpose="judge")
            # Judge purpose -> low tier -> haiku
            assert "haiku" in captured_body.get("model", "")

    @pytest.mark.asyncio
    async def test_slo_always_best_overrides(self):
        """advisor_quality_mode='always_best' should select high-tier regardless of purpose."""
        from sandcastle.engine.generator import _call_advisor_llm

        captured_body = {}

        async def mock_post(url, **kwargs):
            captured_body.update(kwargs.get("json", {}))
            return _mock_httpx_response(_anthropic_ok("0.8"))

        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        settings = MagicMock(
            data_residency="", advisor_quality_mode="always_best",
            anthropic_api_key="", openai_api_key="", mistral_api_key="",
            openrouter_api_key="", minimax_api_key="",
        )
        with patch("sandcastle.engine.generator.httpx.AsyncClient", return_value=mock_client), \
             patch.dict("os.environ", {**_CLEAR_ENV, "ANTHROPIC_API_KEY": "k"}), \
             patch("sandcastle.config.settings", settings), \
             patch("sandcastle.engine.audit.append_audit_event", new_callable=AsyncMock):
            await _call_advisor_llm("system", "rate this", purpose="judge")
            # always_best -> high tier -> sonnet
            assert "sonnet" in captured_body.get("model", "")

    @pytest.mark.asyncio
    async def test_model_override_wins_over_slo(self):
        """Explicit SANDCASTLE_ADVISOR_MODEL overrides SLO tier."""
        from sandcastle.engine.generator import _call_advisor_llm

        captured_body = {}

        async def mock_post(url, **kwargs):
            captured_body.update(kwargs.get("json", {}))
            return _mock_httpx_response(_anthropic_ok("0.8"))

        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("sandcastle.engine.generator.httpx.AsyncClient", return_value=mock_client), \
             patch.dict("os.environ", {
                 **_CLEAR_ENV,
                 "ANTHROPIC_API_KEY": "k",
                 "SANDCASTLE_ADVISOR_MODEL": "custom-model-v1",
             }), \
             patch("sandcastle.config.settings", SETTINGS_NO_RESIDENCY), \
             patch("sandcastle.engine.audit.append_audit_event", new_callable=AsyncMock):
            await _call_advisor_llm("system", "user", purpose="judge")
            assert captured_body["model"] == "custom-model-v1"


# ===================================================================
# generate_workflow
# ===================================================================


class TestGenerateWorkflow:
    @pytest.mark.asyncio
    async def test_no_api_key_raises_value_error(self):
        with patch.dict("os.environ", _CLEAR_ENV), \
             patch("sandcastle.config.settings", SETTINGS_NO_RESIDENCY):
            with pytest.raises(ValueError, match="API key"):
                await generate_workflow("test")

    @pytest.mark.asyncio
    async def test_valid_yaml_produces_result(self):
        with patch("sandcastle.engine.generator._call_advisor_llm",
                    new_callable=AsyncMock, return_value=VALID_YAML), \
             patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key"):
            result = await generate_workflow("Create a test workflow")
            assert isinstance(result, GenerateResult)
            assert result.name == "test-workflow"
            assert result.steps_count == 1
            assert result.validation_errors == []

    @pytest.mark.asyncio
    async def test_fenced_yaml_stripped(self):
        fenced = f"```yaml\n{VALID_YAML}```"
        with patch("sandcastle.engine.generator._call_advisor_llm",
                    new_callable=AsyncMock, return_value=fenced), \
             patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key"):
            result = await generate_workflow("Create a workflow")
            assert result.name == "test-workflow"
            assert result.validation_errors == []

    @pytest.mark.asyncio
    async def test_invalid_yaml_returns_errors(self):
        bad_yaml = "name: bad\nsteps:\n  - id: s1\n    prompt: x\n    depends_on: [nope]"
        with patch("sandcastle.engine.generator._call_advisor_llm",
                    new_callable=AsyncMock, return_value=bad_yaml), \
             patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key"):
            result = await generate_workflow("test")
            assert len(result.validation_errors) > 0

    @pytest.mark.asyncio
    async def test_unparseable_yaml_returns_parse_error(self):
        garbage = "{{{{not yaml at all"
        with patch("sandcastle.engine.generator._call_advisor_llm",
                    new_callable=AsyncMock, return_value=garbage), \
             patch("sandcastle.engine.generator._resolve_api_key", return_value="test-key"):
            result = await generate_workflow("test")
            assert any("parse error" in e.lower() for e in result.validation_errors)

    @pytest.mark.asyncio
    async def test_refine_includes_existing_yaml(self):
        """Refine mode should pass existing YAML in the user message."""
        captured_user = ""

        async def capture_llm(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return VALID_YAML

        with patch("sandcastle.engine.generator._call_advisor_llm", side_effect=capture_llm), \
             patch("sandcastle.engine.generator._resolve_api_key", return_value="key"):
            await generate_workflow(
                "Original",
                refine_from="name: old\nsteps: []",
                refine_instruction="Add a step",
            )
            assert "name: old" in captured_user
            assert "Add a step" in captured_user

    @pytest.mark.asyncio
    async def test_input_schema_extracted(self):
        with patch("sandcastle.engine.generator._call_advisor_llm",
                    new_callable=AsyncMock, return_value=VALID_YAML), \
             patch("sandcastle.engine.generator._resolve_api_key", return_value="key"):
            result = await generate_workflow("test")
            assert result.input_schema is not None
            assert "topic" in result.input_schema.get("properties", {})

    @pytest.mark.asyncio
    async def test_description_extracted(self):
        with patch("sandcastle.engine.generator._call_advisor_llm",
                    new_callable=AsyncMock, return_value=VALID_YAML), \
             patch("sandcastle.engine.generator._resolve_api_key", return_value="key"):
            result = await generate_workflow("test")
            assert result.description == "A test workflow"


# ===================================================================
# generate_chat
# ===================================================================


class TestGenerateChat:
    @pytest.mark.asyncio
    async def test_questions_mode(self):
        """When LLM returns questions mode, relay them."""
        llm_response = json.dumps({"mode": "questions", "message": "What is your goal?"})
        with patch("sandcastle.engine.generator._call_advisor_llm",
                    new_callable=AsyncMock, return_value=llm_response), \
             patch("sandcastle.engine.generator._resolve_api_key", return_value="key"):
            result = await generate_chat([{"role": "user", "content": "I need a workflow"}])
            assert result["mode"] == "questions"
            assert "goal" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_yaml_mode(self):
        """When LLM returns yaml mode, parse and validate."""
        llm_response = json.dumps({
            "mode": "yaml",
            "message": "Here's your workflow",
            "yaml": VALID_YAML,
        })
        with patch("sandcastle.engine.generator._call_advisor_llm",
                    new_callable=AsyncMock, return_value=llm_response), \
             patch("sandcastle.engine.generator._resolve_api_key", return_value="key"):
            result = await generate_chat([{"role": "user", "content": "Generate it"}])
            assert result["mode"] == "yaml"
            assert result["name"] == "test-workflow"
            assert result["steps_count"] == 1

    @pytest.mark.asyncio
    async def test_invalid_json_falls_back_to_questions(self):
        """Non-JSON response treated as questions."""
        with patch("sandcastle.engine.generator._call_advisor_llm",
                    new_callable=AsyncMock, return_value="Just some plain text response"), \
             patch("sandcastle.engine.generator._resolve_api_key", return_value="key"):
            result = await generate_chat([{"role": "user", "content": "help"}])
            assert result["mode"] == "questions"

    @pytest.mark.asyncio
    async def test_no_api_key_raises(self):
        with patch.dict("os.environ", _CLEAR_ENV), \
             patch("sandcastle.config.settings", SETTINGS_NO_RESIDENCY):
            with pytest.raises(ValueError, match="API key"):
                await generate_chat([{"role": "user", "content": "test"}])

    @pytest.mark.asyncio
    async def test_existing_yaml_injected(self):
        """Existing YAML should be injected into user message."""
        captured_user = ""

        async def capture_llm(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return json.dumps({"mode": "questions", "message": "ok"})

        with patch("sandcastle.engine.generator._call_advisor_llm", side_effect=capture_llm), \
             patch("sandcastle.engine.generator._resolve_api_key", return_value="key"):
            await generate_chat(
                [{"role": "user", "content": "Add a step"}],
                existing_yaml="name: existing\nsteps: []",
            )
            assert "name: existing" in captured_user


# ===================================================================
# explain_error
# ===================================================================


class TestExplainError:
    @pytest.mark.asyncio
    async def test_returns_explanation_dict(self):
        llm_response = json.dumps({
            "summary": "Timeout exceeded",
            "cause": "Step took too long",
            "fix": "Increase timeout",
            "severity": "medium",
        })
        with patch("sandcastle.engine.generator._call_advisor_llm",
                    new_callable=AsyncMock, return_value=llm_response), \
             patch("sandcastle.engine.generator._resolve_api_key", return_value="key"):
            result = await explain_error("step-1", "llm", "TimeoutError")
            assert result["severity"] == "medium"
            assert "timeout" in result["summary"].lower()

    @pytest.mark.asyncio
    async def test_no_api_key_returns_basic_explanation(self):
        with patch("sandcastle.engine.generator._resolve_api_key", return_value=""):
            result = await explain_error("step-1", "llm", "Something broke")
            assert result["cause"] == "Unable to generate AI explanation (API key not set)"

    @pytest.mark.asyncio
    async def test_llm_failure_returns_fallback(self):
        with patch("sandcastle.engine.generator._call_advisor_llm",
                    new_callable=AsyncMock, side_effect=RuntimeError("all failed")), \
             patch("sandcastle.engine.generator._resolve_api_key", return_value="key"):
            result = await explain_error("step-1", "llm", "error msg")
            assert result["cause"] == "AI explanation unavailable"

    @pytest.mark.asyncio
    async def test_secrets_scrubbed_before_sending(self):
        captured_user = ""

        async def capture_llm(system, user, **kwargs):
            nonlocal captured_user
            captured_user = user
            return json.dumps({
                "summary": "x", "cause": "x", "fix": "x", "severity": "low",
            })

        with patch("sandcastle.engine.generator._call_advisor_llm", side_effect=capture_llm), \
             patch("sandcastle.engine.generator._resolve_api_key", return_value="key"):
            await explain_error(
                "step-1", "http", "Bearer sk-abc123456789012345 failed",
                prompt="api_key=supersecretvalue1234567890",
            )
            assert "sk-abc123456789012345" not in captured_user
            assert "supersecretvalue1234567890" not in captured_user


# ===================================================================
# _scrub_secrets
# ===================================================================


class TestScrubSecrets:
    def test_scrubs_bearer_token(self):
        text = "Authorization: Bearer sk-abc123456789012345"
        assert "sk-abc123456789012345" not in _scrub_secrets(text)

    def test_scrubs_api_key_assignment(self):
        text = 'api_key=mysupersecretkey12345678'
        result = _scrub_secrets(text)
        assert "mysupersecretkey12345678" not in result

    def test_scrubs_aws_access_key(self):
        text = "AKIAIOSFODNN7EXAMPLE1"
        result = _scrub_secrets(text)
        assert "AKIAIOSFODNN7EXAMPLE1" not in result

    def test_scrubs_pem_key(self):
        text = "-----BEGIN PRIVATE KEY-----\nMIIEvgIB...\n-----END PRIVATE KEY-----"
        result = _scrub_secrets(text)
        assert "MIIEvgIB" not in result

    def test_preserves_normal_text(self):
        text = "The step failed because the URL was not reachable"
        assert _scrub_secrets(text) == text

    def test_scrubs_github_pat(self):
        text = "ghp_1234567890abcdef1234567890abcdef12"
        result = _scrub_secrets(text)
        assert "ghp_1234567890abcdef" not in result

    def test_scrubs_credential_url(self):
        text = "postgres://admin:s3cret@db.example.com:5432/app"
        result = _scrub_secrets(text)
        assert "s3cret" not in result


# ===================================================================
# _strip_fencing (additional edge cases)
# ===================================================================


class TestStripFencingExtended:
    def test_strips_yml_fence(self):
        text = "```yml\ncontent\n```"
        assert _strip_fencing(text) == "content"

    def test_preserves_unfenced_text(self):
        text = "name: foo\nsteps: []"
        assert _strip_fencing(text) == text

    def test_strips_with_extra_whitespace(self):
        text = "  ```yaml\n  content\n  ```  "
        result = _strip_fencing(text)
        assert "```" not in result

    def test_strips_multiline_yaml(self):
        text = "```yaml\nname: test\nsteps:\n  - id: a\n    prompt: x\n```"
        result = _strip_fencing(text)
        assert result.startswith("name: test")
        assert "```" not in result


# ===================================================================
# _resolve_api_key (top-level resolver)
# ===================================================================


class TestResolveApiKey:
    def test_returns_key_from_env(self):
        with patch.dict("os.environ", {**_CLEAR_ENV, "ANTHROPIC_API_KEY": "sk-test"}), \
             patch("sandcastle.config.settings", SETTINGS_NO_RESIDENCY):
            assert _resolve_api_key() == "sk-test"

    def test_ollama_returns_sentinel(self):
        with patch.dict("os.environ", {
            **_CLEAR_ENV, "SANDCASTLE_ADVISOR_PROVIDER": "ollama",
        }), patch("sandcastle.config.settings", SETTINGS_NO_RESIDENCY):
            key = _resolve_api_key()
            assert key == "ollama-no-key"

    def test_empty_when_no_key_configured(self):
        with patch.dict("os.environ", _CLEAR_ENV), \
             patch("sandcastle.config.settings", SETTINGS_NO_RESIDENCY):
            key = _resolve_api_key()
            assert key == ""
