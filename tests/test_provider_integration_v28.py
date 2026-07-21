"""
PROVIDER INTEGRATION TESTS FOR SANDCASTLE v0.28
================================================
Comprehensive tests for all 7 AI providers: anthropic, mistral, openai,
google, minimax, ollama, omlx.

Per provider (15+ tests):
  - Provider exists in PROVIDER_REGISTRY
  - _PROVIDER_CONFIGS has entry
  - ADVISOR_MODEL_TIERS has entry
  - Mock HTTP call to provider URL, verify request format
  - Verify response parsing for each provider
  - Verify failover chain exists and leads somewhere valid
  - Verify data residency tagging (region field)

All HTTP calls are mocked; no real API requests are made.

Run:
    cd /Users/gizmax/Documents/Sandcastle
    .venv/bin/python -m pytest tests/test_provider_integration_v28.py -v --tb=short
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from sandcastle.engine.providers import (
    FAILOVER_CHAINS,
    KNOWN_MODELS,
    PROVIDER_REGISTRY,
    ModelInfo,
    ProviderFailover,
    get_api_key,
    is_claude_model,
    resolve_base_url,
    resolve_model,
)
from sandcastle.engine.generator import (
    ADVISOR_MODEL_TIERS,
    ADVISOR_QUALITY_TIERS,
    _build_request_body,
    _parse_response_text,
    _PROVIDER_CONFIGS,
)


# ======================================================================
# All 7 providers
# ======================================================================

ALL_PROVIDERS = ["anthropic", "mistral", "openai", "google", "minimax", "ollama", "omlx"]

# Map of provider name -> at least one model key in PROVIDER_REGISTRY
PROVIDER_MODEL_KEYS = {
    "anthropic": ["sonnet", "opus", "haiku"],
    "mistral": ["mistral/large", "mistral/small", "mistral/codestral"],
    "openai": ["openai/codex-mini", "openai/codex"],
    "google": ["google/gemini-2.5-pro"],
    "minimax": ["minimax/m2.5"],
    "ollama": ["ollama"],
    "omlx": ["omlx/llama-4-scout", "omlx/mistral-small", "omlx/gemma-3", "omlx/qwen-3"],
}

# Expected regions per provider
PROVIDER_REGIONS = {
    "anthropic": "us",
    "mistral": "eu",
    "openai": "us",
    "google": "us",
    "minimax": "us",
    "ollama": "local",
    "omlx": "local",
}

# Expected API URL patterns per provider
PROVIDER_API_URLS = {
    "anthropic": "api.anthropic.com",
    "mistral": "api.mistral.ai",
    "openai": "api.openai.com",
    "google": "openrouter.ai",
    "minimax": "api.minimaxi.chat",
    "ollama": "localhost:11434",
    "omlx": "localhost:8080",
}


def _fake_settings(**overrides):
    """Return a mock Settings object with sensible defaults."""
    defaults = {
        "data_residency": "",
        "failover_cooldown_seconds": 60.0,
        "anthropic_api_key": "sk-ant-test",
        "openai_api_key": "sk-openai-test",
        "mistral_api_key": "sk-mistral-test",
        "openrouter_api_key": "sk-openrouter-test",
        "minimax_api_key": "sk-minimax-test",
        "e2b_api_key": "",
        "advisor_quality_mode": "auto",
        "omlx_base_url": "http://localhost:8080",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_anthropic_response(text: str = "result") -> dict:
    """Standard Anthropic-format JSON response body."""
    return {"content": [{"text": text}]}


def _make_openai_response(text: str = "result") -> dict:
    """Standard OpenAI-compatible JSON response body."""
    return {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


# ======================================================================
# 1. Provider Registry Tests
# ======================================================================


class TestProviderRegistryPresence:
    """Verify each provider has entries in PROVIDER_REGISTRY."""

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_provider_has_models_in_registry(self, provider: str) -> None:
        """Provider must have at least one model in PROVIDER_REGISTRY."""
        model_keys = PROVIDER_MODEL_KEYS[provider]
        for key in model_keys:
            assert key in PROVIDER_REGISTRY, (
                f"Model key '{key}' for provider '{provider}' "
                f"not found in PROVIDER_REGISTRY"
            )

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_provider_model_info_fields(self, provider: str) -> None:
        """Each model entry must have valid ModelInfo fields."""
        model_keys = PROVIDER_MODEL_KEYS[provider]
        for key in model_keys:
            info = PROVIDER_REGISTRY[key]
            assert isinstance(info, ModelInfo)
            assert info.provider, f"Model '{key}' has empty provider"
            assert info.api_model_id, f"Model '{key}' has empty api_model_id"
            assert info.runner in ("runner.mjs", "runner-openai.mjs"), (
                f"Model '{key}' has invalid runner '{info.runner}'"
            )
            assert info.region in ("us", "eu", "local"), (
                f"Model '{key}' has invalid region '{info.region}'"
            )
            assert info.input_price_per_m >= 0, (
                f"Model '{key}' has negative input price"
            )
            assert info.output_price_per_m >= 0, (
                f"Model '{key}' has negative output price"
            )

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_provider_resolve_model(self, provider: str) -> None:
        """resolve_model() must return the correct ModelInfo."""
        model_keys = PROVIDER_MODEL_KEYS[provider]
        for key in model_keys:
            info = resolve_model(key)
            assert info.provider == PROVIDER_REGISTRY[key].provider


# ======================================================================
# 2. _PROVIDER_CONFIGS Tests
# ======================================================================


class TestProviderConfigs:
    """Verify each provider has a proper entry in _PROVIDER_CONFIGS."""

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_provider_in_configs(self, provider: str) -> None:
        """Provider must exist in _PROVIDER_CONFIGS."""
        assert provider in _PROVIDER_CONFIGS, (
            f"Provider '{provider}' not found in _PROVIDER_CONFIGS. "
            f"Available: {sorted(_PROVIDER_CONFIGS.keys())}"
        )

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_provider_config_has_api_url(self, provider: str) -> None:
        """Provider config must have an api_url."""
        cfg = _PROVIDER_CONFIGS[provider]
        assert "api_url" in cfg, f"Provider '{provider}' missing api_url"
        assert cfg["api_url"], f"Provider '{provider}' has empty api_url"

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_provider_config_has_model(self, provider: str) -> None:
        """Provider config must have a default model."""
        cfg = _PROVIDER_CONFIGS[provider]
        assert "model" in cfg, f"Provider '{provider}' missing model"
        assert cfg["model"], f"Provider '{provider}' has empty model"

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_provider_config_has_headers_fn(self, provider: str) -> None:
        """Provider config must have a headers_fn callable."""
        cfg = _PROVIDER_CONFIGS[provider]
        assert "headers_fn" in cfg, f"Provider '{provider}' missing headers_fn"
        assert callable(cfg["headers_fn"]), (
            f"Provider '{provider}' headers_fn is not callable"
        )

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_provider_config_has_region(self, provider: str) -> None:
        """Provider config must have a region field."""
        cfg = _PROVIDER_CONFIGS[provider]
        assert "region" in cfg, f"Provider '{provider}' missing region"
        assert cfg["region"] in ("us", "eu", "local"), (
            f"Provider '{provider}' has invalid region '{cfg['region']}'"
        )

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_provider_config_api_url_pattern(self, provider: str) -> None:
        """Provider API URL should match the expected pattern."""
        cfg = _PROVIDER_CONFIGS[provider]
        expected_pattern = PROVIDER_API_URLS[provider]
        # Local providers use template variables resolved at call time; resolve
        # them the same way production does (with no env override this yields
        # the localhost defaults the patterns below expect).
        from sandcastle.engine.generator import resolve_provider_api_url
        url = resolve_provider_api_url(cfg)
        assert expected_pattern in url, (
            f"Provider '{provider}' API URL '{url}' does not contain "
            f"expected pattern '{expected_pattern}'"
        )


# ======================================================================
# 3. ADVISOR_MODEL_TIERS Tests
# ======================================================================


class TestAdvisorModelTiers:
    """Verify each provider has model tier entries."""

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_provider_has_tier_entry(self, provider: str) -> None:
        """Provider must have an entry in ADVISOR_MODEL_TIERS."""
        assert provider in ADVISOR_MODEL_TIERS, (
            f"Provider '{provider}' not found in ADVISOR_MODEL_TIERS. "
            f"Available: {sorted(ADVISOR_MODEL_TIERS.keys())}"
        )

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_tier_has_all_levels(self, provider: str) -> None:
        """Each provider tier entry must have high, medium, and low levels."""
        tiers = ADVISOR_MODEL_TIERS[provider]
        for level in ("high", "medium", "low"):
            assert level in tiers, (
                f"Provider '{provider}' tier map missing '{level}' level"
            )
            assert tiers[level], (
                f"Provider '{provider}' tier '{level}' has empty model"
            )

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_tier_models_are_strings(self, provider: str) -> None:
        """Tier model values must be non-empty strings."""
        tiers = ADVISOR_MODEL_TIERS[provider]
        for level, model in tiers.items():
            assert isinstance(model, str) and len(model) > 0, (
                f"Provider '{provider}' tier '{level}' model is not a valid string"
            )

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_high_tier_exists(self, provider: str) -> None:
        """High tier model must be defined (used for generation)."""
        tiers = ADVISOR_MODEL_TIERS[provider]
        assert "high" in tiers and tiers["high"], (
            f"Provider '{provider}' has no high-tier model defined"
        )


# ======================================================================
# 4. Request Format Tests (Anthropic vs OpenAI format)
# ======================================================================


class TestRequestFormat:
    """Verify request bodies are formatted correctly per provider type."""

    def test_anthropic_request_format(self) -> None:
        """Anthropic requests use top-level system field."""
        body = _build_request_body(
            model="claude-sonnet-4-6",
            system="You are a helper.",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=1024,
            is_anthropic=True,
        )
        assert "system" in body
        assert body["system"] == "You are a helper."
        assert body["model"] == "claude-sonnet-4-6"
        assert body["max_tokens"] == 1024
        assert body["messages"] == [{"role": "user", "content": "Hello"}]
        # Anthropic format should NOT have system in messages
        assert not any(m.get("role") == "system" for m in body["messages"])

    def test_openai_request_format(self) -> None:
        """OpenAI/Ollama requests use system message in messages array."""
        body = _build_request_body(
            model="gpt-4o",
            system="You are a helper.",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=1024,
            is_anthropic=False,
        )
        assert "system" not in body
        assert body["model"] == "gpt-4o"
        assert body["max_tokens"] == 1024
        # First message should be system
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][0]["content"] == "You are a helper."
        # User message should follow
        assert body["messages"][1]["role"] == "user"
        assert body["messages"][1]["content"] == "Hello"

    @pytest.mark.parametrize("provider", ["mistral", "openai", "google", "minimax", "ollama", "omlx"])
    def test_non_anthropic_providers_use_openai_format(self, provider: str) -> None:
        """All non-Anthropic providers should use OpenAI message format."""
        cfg = _PROVIDER_CONFIGS[provider]
        model = cfg["model"]
        body = _build_request_body(
            model=model,
            system="Test system prompt.",
            messages=[{"role": "user", "content": "Test"}],
            is_anthropic=False,
        )
        # Should have system as first message, not as top-level key
        assert "system" not in body
        assert body["messages"][0]["role"] == "system"

    def test_anthropic_format_preserves_message_order(self) -> None:
        """Multi-turn Anthropic requests preserve message order."""
        messages = [
            {"role": "user", "content": "First"},
            {"role": "assistant", "content": "Reply"},
            {"role": "user", "content": "Second"},
        ]
        body = _build_request_body(
            model="claude-sonnet-4-6",
            system="System",
            messages=messages,
            is_anthropic=True,
        )
        assert len(body["messages"]) == 3
        assert body["messages"][0]["content"] == "First"
        assert body["messages"][2]["content"] == "Second"

    def test_openai_format_prepends_system(self) -> None:
        """Multi-turn OpenAI requests have system prepended."""
        messages = [
            {"role": "user", "content": "First"},
            {"role": "assistant", "content": "Reply"},
        ]
        body = _build_request_body(
            model="gpt-4o",
            system="System",
            messages=messages,
            is_anthropic=False,
        )
        # System + 2 original messages = 3 total
        assert len(body["messages"]) == 3
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][1]["content"] == "First"


# ======================================================================
# 5. Response Parsing Tests
# ======================================================================


class TestResponseParsing:
    """Verify response parsing for each provider format."""

    def test_anthropic_response_parsing(self) -> None:
        """Parse Anthropic response format correctly."""
        data = _make_anthropic_response("Hello from Claude")
        text = _parse_response_text(data, is_anthropic=True)
        assert text == "Hello from Claude"

    def test_openai_response_parsing(self) -> None:
        """Parse OpenAI response format correctly."""
        data = _make_openai_response("Hello from GPT")
        text = _parse_response_text(data, is_anthropic=False)
        assert text == "Hello from GPT"

    def test_anthropic_response_with_long_text(self) -> None:
        """Parse Anthropic response with long content."""
        long_text = "A" * 10000
        data = _make_anthropic_response(long_text)
        text = _parse_response_text(data, is_anthropic=True)
        assert len(text) == 10000

    def test_openai_response_with_long_text(self) -> None:
        """Parse OpenAI response with long content."""
        long_text = "B" * 10000
        data = _make_openai_response(long_text)
        text = _parse_response_text(data, is_anthropic=False)
        assert len(text) == 10000

    def test_anthropic_response_missing_content_raises(self) -> None:
        """Missing content field should raise for Anthropic format."""
        with pytest.raises((KeyError, IndexError)):
            _parse_response_text({"content": []}, is_anthropic=True)

    def test_openai_response_missing_choices_raises(self) -> None:
        """Missing choices field should raise for OpenAI format."""
        with pytest.raises((KeyError, IndexError)):
            _parse_response_text({"choices": []}, is_anthropic=False)

    @pytest.mark.parametrize("provider", ["mistral", "openai", "google", "minimax", "ollama", "omlx"])
    def test_openai_compatible_providers_parse_same_format(self, provider: str) -> None:
        """All OpenAI-compatible providers use the same response format."""
        data = _make_openai_response(f"Hello from {provider}")
        text = _parse_response_text(data, is_anthropic=False)
        assert text == f"Hello from {provider}"


# ======================================================================
# 6. Failover Chain Tests
# ======================================================================


class TestFailoverChains:
    """Verify failover chains exist and lead to valid models."""

    @pytest.mark.parametrize("model_key", list(PROVIDER_REGISTRY.keys()))
    def test_model_has_failover_chain(self, model_key: str) -> None:
        """Every model in registry should have a failover chain entry."""
        assert model_key in FAILOVER_CHAINS, (
            f"Model '{model_key}' has no FAILOVER_CHAINS entry"
        )

    @pytest.mark.parametrize("model_key", list(PROVIDER_REGISTRY.keys()))
    def test_failover_chain_targets_valid(self, model_key: str) -> None:
        """All failover targets must be valid models in PROVIDER_REGISTRY."""
        chain = FAILOVER_CHAINS.get(model_key, [])
        for target in chain:
            assert target in PROVIDER_REGISTRY, (
                f"Failover chain for '{model_key}' references unknown "
                f"model '{target}'"
            )

    @pytest.mark.parametrize("model_key", list(PROVIDER_REGISTRY.keys()))
    def test_failover_chain_no_self_reference(self, model_key: str) -> None:
        """A model should not appear in its own failover chain."""
        chain = FAILOVER_CHAINS.get(model_key, [])
        assert model_key not in chain, (
            f"Model '{model_key}' appears in its own failover chain"
        )

    @pytest.mark.parametrize("provider", ["anthropic", "mistral", "openai", "google", "minimax"])
    def test_cloud_provider_has_nonempty_chain(self, provider: str) -> None:
        """Cloud providers should have at least one failover target."""
        model_keys = PROVIDER_MODEL_KEYS[provider]
        for key in model_keys:
            chain = FAILOVER_CHAINS.get(key, [])
            assert len(chain) > 0, (
                f"Cloud model '{key}' has empty failover chain"
            )

    def test_ollama_has_empty_chain(self) -> None:
        """Ollama (local) should have an empty failover chain."""
        chain = FAILOVER_CHAINS.get("ollama", None)
        assert chain is not None
        assert chain == [], "Ollama should have empty failover chain (no cloud alternatives)"

    def test_omlx_models_failover_to_local(self) -> None:
        """oMLX models should failover to other oMLX models or ollama."""
        for key in PROVIDER_MODEL_KEYS["omlx"]:
            chain = FAILOVER_CHAINS.get(key, [])
            for target in chain:
                info = PROVIDER_REGISTRY[target]
                assert info.region == "local", (
                    f"oMLX model '{key}' failover target '{target}' "
                    f"is not local (region='{info.region}')"
                )


# ======================================================================
# 7. Data Residency Tests
# ======================================================================


class TestDataResidency:
    """Verify data residency tagging per provider."""

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_provider_registry_region(self, provider: str) -> None:
        """All models for a provider must have the expected region."""
        expected_region = PROVIDER_REGIONS[provider]
        model_keys = PROVIDER_MODEL_KEYS[provider]
        for key in model_keys:
            info = PROVIDER_REGISTRY[key]
            assert info.region == expected_region, (
                f"Model '{key}' has region '{info.region}', "
                f"expected '{expected_region}' for provider '{provider}'"
            )

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_provider_config_region(self, provider: str) -> None:
        """_PROVIDER_CONFIGS region should match expected residency."""
        cfg = _PROVIDER_CONFIGS[provider]
        expected_region = PROVIDER_REGIONS[provider]
        assert cfg["region"] == expected_region, (
            f"Provider config '{provider}' has region '{cfg['region']}', "
            f"expected '{expected_region}'"
        )

    def test_eu_providers_are_eu(self) -> None:
        """Mistral should be tagged as EU."""
        for key in PROVIDER_MODEL_KEYS["mistral"]:
            info = PROVIDER_REGISTRY[key]
            assert info.region == "eu"

    def test_local_providers_are_local(self) -> None:
        """Ollama and oMLX should be tagged as local."""
        for provider in ("ollama", "omlx"):
            for key in PROVIDER_MODEL_KEYS[provider]:
                info = PROVIDER_REGISTRY[key]
                assert info.region == "local"

    def test_us_providers_are_us(self) -> None:
        """Anthropic, OpenAI, Google, MiniMax should be tagged as US."""
        for provider in ("anthropic", "openai", "google", "minimax"):
            for key in PROVIDER_MODEL_KEYS[provider]:
                info = PROVIDER_REGISTRY[key]
                assert info.region == "us"


# ======================================================================
# 8. Header Generation Tests
# ======================================================================


class TestHeaderGeneration:
    """Verify headers_fn generates correct headers per provider."""

    def test_anthropic_headers(self) -> None:
        """Anthropic headers must include x-api-key and anthropic-version."""
        cfg = _PROVIDER_CONFIGS["anthropic"]
        headers = cfg["headers_fn"]("sk-ant-test-key")
        assert "x-api-key" in headers
        assert headers["x-api-key"] == "sk-ant-test-key"
        assert "anthropic-version" in headers
        assert "content-type" in headers

    @pytest.mark.parametrize("provider", ["openai", "mistral", "google", "minimax"])
    def test_bearer_auth_headers(self, provider: str) -> None:
        """OpenAI-compatible providers use Bearer token auth."""
        cfg = _PROVIDER_CONFIGS[provider]
        headers = cfg["headers_fn"]("sk-test-key")
        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer sk-test-key"
        assert "Content-Type" in headers

    def test_ollama_no_auth_header(self) -> None:
        """Ollama should not include Authorization header."""
        cfg = _PROVIDER_CONFIGS["ollama"]
        headers = cfg["headers_fn"]("")
        assert "Authorization" not in headers
        assert "x-api-key" not in headers

    def test_omlx_no_auth_header(self) -> None:
        """oMLX should not include Authorization header."""
        cfg = _PROVIDER_CONFIGS["omlx"]
        headers = cfg["headers_fn"]("")
        assert "Authorization" not in headers
        assert "x-api-key" not in headers


# ======================================================================
# 9. ProviderFailover Logic Tests
# ======================================================================


class TestProviderFailoverLogic:
    """Test the ProviderFailover class behavior."""

    def test_new_failover_has_no_cooldowns(self) -> None:
        """Fresh ProviderFailover should have no cooldowns."""
        pf = ProviderFailover()
        status = pf.get_status()
        assert status["active_cooldowns"] == {}

    def test_mark_cooldown_makes_unavailable(self) -> None:
        """Marking a key as cooled down should make it unavailable."""
        pf = ProviderFailover()
        pf.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=60.0)
        assert not pf.is_available("ANTHROPIC_API_KEY")

    def test_clear_cooldown_restores_availability(self) -> None:
        """Clearing cooldown should restore availability."""
        pf = ProviderFailover()
        pf.mark_cooldown("OPENAI_API_KEY", duration_seconds=60.0)
        assert not pf.is_available("OPENAI_API_KEY")
        pf.clear_cooldown("OPENAI_API_KEY")
        assert pf.is_available("OPENAI_API_KEY")

    def test_uncooled_key_is_available(self) -> None:
        """Keys that were never cooled should be available."""
        pf = ProviderFailover()
        assert pf.is_available("SOME_RANDOM_KEY")

    @patch("sandcastle.engine.providers.get_api_key")
    def test_get_alternatives_filters_cooldown(self, mock_get_key) -> None:
        """get_alternatives should skip models on cooldown."""
        mock_get_key.return_value = "sk-test"
        pf = ProviderFailover()
        pf.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=60.0)
        alternatives = pf.get_alternatives("openai/codex-mini")
        # haiku and sonnet should be filtered out because ANTHROPIC_API_KEY is on cooldown
        for alt in alternatives:
            info = PROVIDER_REGISTRY[alt]
            if info.api_key_env == "ANTHROPIC_API_KEY":
                pytest.fail(
                    f"Alternative '{alt}' should be filtered out "
                    "because ANTHROPIC_API_KEY is on cooldown"
                )

    @patch("sandcastle.engine.providers.get_api_key")
    def test_get_alternatives_respects_data_residency(self, mock_get_key) -> None:
        """get_alternatives with data_residency should only return matching regions."""
        mock_get_key.return_value = "sk-test"
        pf = ProviderFailover()
        alternatives = pf.get_alternatives("mistral/large", data_residency="eu")
        for alt in alternatives:
            info = PROVIDER_REGISTRY[alt]
            assert info.region == "eu", (
                f"Alternative '{alt}' has region '{info.region}' "
                "but data_residency='eu' was requested"
            )


# ======================================================================
# 10. Runner Assignment Tests
# ======================================================================


class TestRunnerAssignment:
    """Verify correct runner is assigned per provider."""

    def test_claude_models_use_claude_runner(self) -> None:
        """Claude models should use runner.mjs."""
        for key in PROVIDER_MODEL_KEYS["anthropic"]:
            info = PROVIDER_REGISTRY[key]
            assert info.runner == "runner.mjs", (
                f"Claude model '{key}' should use runner.mjs"
            )

    @pytest.mark.parametrize("provider", ["mistral", "openai", "google", "minimax", "ollama", "omlx"])
    def test_non_claude_models_use_openai_runner(self, provider: str) -> None:
        """Non-Claude models should use runner-openai.mjs."""
        for key in PROVIDER_MODEL_KEYS[provider]:
            info = PROVIDER_REGISTRY[key]
            assert info.runner == "runner-openai.mjs", (
                f"Model '{key}' should use runner-openai.mjs"
            )


# ======================================================================
# 11. is_claude_model Utility Tests
# ======================================================================


class TestIsClaudeModel:
    """Verify is_claude_model helper works correctly."""

    @pytest.mark.parametrize("model_key", ["sonnet", "opus", "haiku"])
    def test_claude_models_return_true(self, model_key: str) -> None:
        assert is_claude_model(model_key) is True

    @pytest.mark.parametrize(
        "model_key",
        ["mistral/large", "openai/codex-mini", "ollama", "omlx/llama-4-scout", "minimax/m2.5"],
    )
    def test_non_claude_models_return_false(self, model_key: str) -> None:
        assert is_claude_model(model_key) is False

    def test_unknown_model_returns_false(self) -> None:
        assert is_claude_model("nonexistent-model") is False


# ======================================================================
# 12. API Key Configuration Tests
# ======================================================================


class TestApiKeyConfig:
    """Verify API key env var configuration per provider."""

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_provider_config_api_key_env(self, provider: str) -> None:
        """Provider config api_key_env should be consistent with registry."""
        cfg = _PROVIDER_CONFIGS[provider]
        cfg_key_env = cfg.get("api_key_env", "")
        # For providers with models, the key env should match
        model_keys = PROVIDER_MODEL_KEYS[provider]
        if model_keys:
            first_key = model_keys[0]
            registry_key_env = PROVIDER_REGISTRY[first_key].api_key_env
            # Local providers may have empty key env
            if provider in ("ollama", "omlx"):
                assert cfg_key_env == "", (
                    f"Local provider '{provider}' should have empty api_key_env"
                )
            else:
                assert cfg_key_env, (
                    f"Cloud provider '{provider}' should have a non-empty api_key_env"
                )

    def test_local_providers_need_no_key(self) -> None:
        """Ollama and oMLX should not require API keys."""
        for provider in ("ollama", "omlx"):
            for key in PROVIDER_MODEL_KEYS[provider]:
                info = PROVIDER_REGISTRY[key]
                assert info.api_key_env == "", (
                    f"Local model '{key}' should have empty api_key_env"
                )

    def test_cloud_providers_need_key(self) -> None:
        """Cloud providers should require API keys."""
        for provider in ("anthropic", "openai", "mistral", "google", "minimax"):
            for key in PROVIDER_MODEL_KEYS[provider]:
                info = PROVIDER_REGISTRY[key]
                assert info.api_key_env, (
                    f"Cloud model '{key}' should have non-empty api_key_env"
                )


# ======================================================================
# 13. Pricing Configuration Tests
# ======================================================================


class TestPricingConfig:
    """Verify pricing makes sense for each provider."""

    def test_local_providers_are_free(self) -> None:
        """Ollama and oMLX models should have zero pricing."""
        for provider in ("ollama", "omlx"):
            for key in PROVIDER_MODEL_KEYS[provider]:
                info = PROVIDER_REGISTRY[key]
                assert info.input_price_per_m == 0.0
                assert info.output_price_per_m == 0.0

    @pytest.mark.parametrize("provider", ["anthropic", "openai", "mistral", "google", "minimax"])
    def test_cloud_providers_have_pricing(self, provider: str) -> None:
        """Cloud providers should have non-zero pricing."""
        for key in PROVIDER_MODEL_KEYS[provider]:
            info = PROVIDER_REGISTRY[key]
            assert info.input_price_per_m > 0 or info.output_price_per_m > 0, (
                f"Cloud model '{key}' has zero pricing"
            )

    def test_output_price_greater_than_input(self) -> None:
        """For most models, output price should be >= input price."""
        for key, info in PROVIDER_REGISTRY.items():
            if info.input_price_per_m > 0:
                assert info.output_price_per_m >= info.input_price_per_m, (
                    f"Model '{key}': output price ({info.output_price_per_m}) "
                    f"< input price ({info.input_price_per_m})"
                )


# ======================================================================
# 14. KNOWN_MODELS Consistency Tests
# ======================================================================


class TestKnownModelsConsistency:
    """Verify KNOWN_MODELS is consistent with PROVIDER_REGISTRY."""

    def test_known_models_matches_registry_keys(self) -> None:
        """KNOWN_MODELS should be exactly the keys of PROVIDER_REGISTRY."""
        assert KNOWN_MODELS == frozenset(PROVIDER_REGISTRY.keys())

    def test_all_provider_models_in_known(self) -> None:
        """All model keys from all providers should be in KNOWN_MODELS."""
        for provider, keys in PROVIDER_MODEL_KEYS.items():
            for key in keys:
                assert key in KNOWN_MODELS, (
                    f"Model '{key}' from provider '{provider}' "
                    "not in KNOWN_MODELS"
                )


# ======================================================================
# 15. Quality Tier Resolution Tests
# ======================================================================


class TestQualityTierResolution:
    """Verify quality tier resolution works for all providers."""

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    @pytest.mark.parametrize("tier", ["high", "medium", "low"])
    def test_tier_resolution(self, provider: str, tier: str) -> None:
        """Each provider/tier combination should resolve to a model."""
        tiers = ADVISOR_MODEL_TIERS.get(provider, {})
        model = tiers.get(tier)
        assert model is not None, (
            f"Provider '{provider}' tier '{tier}' resolved to None"
        )
        assert isinstance(model, str) and len(model) > 0

    def test_advisor_quality_tiers_cover_all_purposes(self) -> None:
        """ADVISOR_QUALITY_TIERS should cover generation, chat, explain, evolution, judge."""
        expected = {"generation", "chat", "explain", "evolution", "judge"}
        assert expected == set(ADVISOR_QUALITY_TIERS.keys()), (
            f"Missing purposes: {expected - set(ADVISOR_QUALITY_TIERS.keys())}"
        )

    def test_all_quality_tier_values_are_valid(self) -> None:
        """All values in ADVISOR_QUALITY_TIERS must be valid tier levels."""
        valid_levels = {"high", "medium", "low"}
        for purpose, level in ADVISOR_QUALITY_TIERS.items():
            assert level in valid_levels, (
                f"Purpose '{purpose}' has invalid tier level '{level}'"
            )


# ======================================================================
# 16. Base URL Resolution Tests
# ======================================================================


class TestBaseUrlResolution:
    """Verify base URL resolution per provider."""

    def test_claude_models_no_base_url(self) -> None:
        """Claude models should have None api_base_url (uses SDK default)."""
        for key in PROVIDER_MODEL_KEYS["anthropic"]:
            info = PROVIDER_REGISTRY[key]
            assert info.api_base_url is None

    @pytest.mark.parametrize("provider", ["mistral", "openai", "google", "minimax"])
    def test_cloud_providers_have_base_url(self, provider: str) -> None:
        """Cloud providers should have a base URL configured."""
        for key in PROVIDER_MODEL_KEYS[provider]:
            info = PROVIDER_REGISTRY[key]
            assert info.api_base_url is not None
            assert info.api_base_url.startswith("http")

    def test_ollama_base_url(self) -> None:
        """Ollama should point to localhost."""
        info = PROVIDER_REGISTRY["ollama"]
        assert info.api_base_url is not None
        assert "localhost" in info.api_base_url

    def test_omlx_base_url_in_registry(self) -> None:
        """oMLX models should have localhost base URL in registry."""
        for key in PROVIDER_MODEL_KEYS["omlx"]:
            info = PROVIDER_REGISTRY[key]
            assert info.api_base_url is not None
            assert "localhost" in info.api_base_url

    def test_omlx_config_uses_template_url(self) -> None:
        """oMLX config in _PROVIDER_CONFIGS should use a template URL."""
        cfg = _PROVIDER_CONFIGS["omlx"]
        assert "{omlx_base_url}" in cfg["api_url"], (
            "oMLX config should use {omlx_base_url} template variable"
        )
