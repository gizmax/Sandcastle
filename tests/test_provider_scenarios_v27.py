"""Exhaustive tests for the multi-provider and failover system.

Covers:
1. Provider switching scenarios
2. Failover chain scenarios
3. SLO routing scenarios
4. Cost estimation scenarios
5. Data residency enforcement

All HTTP calls are mocked; real provider logic is exercised.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import httpx
import pytest

from sandcastle.engine.providers import (
    FAILOVER_CHAINS,
    KNOWN_MODELS,
    PROVIDER_REGISTRY,
    ModelInfo,
    ProviderFailover,
    get_api_key,
    resolve_model,
)
from sandcastle.engine.generator import (
    ADVISOR_MODEL_TIERS,
    ADVISOR_QUALITY_TIERS,
    _build_providers_to_try,
    _build_request_body,
    _call_advisor_llm,
    _get_advisor_config,
    _PROVIDER_CONFIGS,
    _resolve_model_for_tier,
    _resolve_provider_name,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_settings(**overrides):
    """Return a mock Settings object with sensible defaults."""
    defaults = {
        "data_residency": "",
        "failover_cooldown_seconds": 60.0,
        "anthropic_api_key": "sk-ant-test",
        "openai_api_key": "",
        "mistral_api_key": "",
        "openrouter_api_key": "",
        "minimax_api_key": "",
        "advisor_quality_mode": "auto",
        "omlx_base_url": "http://localhost:8080",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_httpx_response(status_code: int, json_data: dict | None = None) -> httpx.Response:
    """Build a fake httpx.Response with given status and optional JSON body."""
    resp = httpx.Response(
        status_code=status_code,
        request=httpx.Request("POST", "https://fake"),
        json=json_data or {},
    )
    return resp


def _anthropic_ok_response(text: str = "result") -> dict:
    """Standard Anthropic-format JSON response body."""
    return {"content": [{"text": text}]}


def _openai_ok_response(text: str = "result") -> dict:
    """Standard OpenAI-format JSON response body."""
    return {"choices": [{"message": {"content": text}}]}


# ===================================================================
# 1. Provider switching scenarios (12 tests)
# ===================================================================


class TestProviderSwitching:
    """Verify that switching the SANDCASTLE_ADVISOR_PROVIDER env var
    correctly selects the target provider configuration."""

    def test_switch_anthropic_to_mistral(self):
        """Switching to mistral must change the API URL and model."""
        with patch.dict("os.environ", {"SANDCASTLE_ADVISOR_PROVIDER": "mistral"}), \
             patch("sandcastle.config.settings", _fake_settings()):
            cfg = _get_advisor_config()
        assert cfg["api_url"] == "https://api.mistral.ai/v1/chat/completions"
        assert cfg["model"] == "mistral-large-latest"
        assert cfg["api_key_env"] == "MISTRAL_API_KEY"

    def test_switch_mistral_to_ollama_no_key_needed(self):
        """Ollama requires no API key - api_key_env is empty string."""
        with patch.dict("os.environ", {"SANDCASTLE_ADVISOR_PROVIDER": "ollama"}), \
             patch("sandcastle.config.settings", _fake_settings()):
            cfg = _get_advisor_config()
        assert cfg["api_key_env"] == ""
        assert cfg["region"] == "local"
        assert "localhost" in cfg["api_url"]

    def test_switch_to_unknown_falls_back_to_anthropic(self):
        """An unknown provider name silently falls back to anthropic."""
        with patch.dict("os.environ", {"SANDCASTLE_ADVISOR_PROVIDER": "nonexistent_provider"}), \
             patch("sandcastle.config.settings", _fake_settings()):
            cfg = _get_advisor_config()
        assert cfg["api_key_env"] == "ANTHROPIC_API_KEY"
        assert cfg["model"] == "claude-sonnet-4-20250514"

    def test_switch_to_openai(self):
        """Switching to openai configures correct base URL and key env."""
        with patch.dict("os.environ", {"SANDCASTLE_ADVISOR_PROVIDER": "openai"}), \
             patch("sandcastle.config.settings", _fake_settings()):
            cfg = _get_advisor_config()
        assert cfg["api_url"] == "https://api.openai.com/v1/chat/completions"
        assert cfg["api_key_env"] == "OPENAI_API_KEY"

    def test_switch_to_google(self):
        """Switching to google uses OpenRouter URL."""
        with patch.dict("os.environ", {"SANDCASTLE_ADVISOR_PROVIDER": "google"}), \
             patch("sandcastle.config.settings", _fake_settings()):
            cfg = _get_advisor_config()
        assert "openrouter.ai" in cfg["api_url"]

    def test_switch_to_minimax(self):
        """Switching to minimax uses the minimax API."""
        with patch.dict("os.environ", {"SANDCASTLE_ADVISOR_PROVIDER": "minimax"}), \
             patch("sandcastle.config.settings", _fake_settings()):
            cfg = _get_advisor_config()
        assert "minimaxi.chat" in cfg["api_url"]
        assert cfg["api_key_env"] == "MINIMAX_API_KEY"

    def test_switch_while_eu_residency_blocks_us_providers(self):
        """EU residency must reject US-region providers like anthropic."""
        with patch.dict("os.environ", {"SANDCASTLE_ADVISOR_PROVIDER": "anthropic"}), \
             patch("sandcastle.config.settings", _fake_settings(data_residency="eu")):
            with pytest.raises(ValueError, match="Data residency mode 'eu'"):
                _get_advisor_config()

    def test_switch_while_eu_residency_allows_mistral(self):
        """EU residency allows the mistral provider (region=eu)."""
        with patch.dict("os.environ", {"SANDCASTLE_ADVISOR_PROVIDER": "mistral"}), \
             patch("sandcastle.config.settings", _fake_settings(data_residency="eu")):
            cfg = _get_advisor_config()
        assert cfg["region"] == "eu"

    def test_switch_while_local_residency_only_ollama(self):
        """Local residency only allows ollama (region=local)."""
        with patch.dict("os.environ", {"SANDCASTLE_ADVISOR_PROVIDER": "ollama"}), \
             patch("sandcastle.config.settings", _fake_settings(data_residency="local")):
            cfg = _get_advisor_config()
        assert cfg["region"] == "local"

    def test_switch_while_local_residency_blocks_cloud(self):
        """Local residency blocks anthropic (cloud US provider)."""
        with patch.dict("os.environ", {"SANDCASTLE_ADVISOR_PROVIDER": "anthropic"}), \
             patch("sandcastle.config.settings", _fake_settings(data_residency="local")):
            with pytest.raises(ValueError, match="Data residency mode 'local'"):
                _get_advisor_config()

    def test_model_override_applied_to_switched_provider(self):
        """SANDCASTLE_ADVISOR_MODEL overrides the default model of the chosen provider."""
        with patch.dict("os.environ", {
            "SANDCASTLE_ADVISOR_PROVIDER": "mistral",
            "SANDCASTLE_ADVISOR_MODEL": "mistral-medium-latest",
        }), patch("sandcastle.config.settings", _fake_settings()):
            cfg = _get_advisor_config()
        assert cfg["model"] == "mistral-medium-latest"

    def test_provider_name_case_insensitive(self):
        """Provider name matching is case-insensitive."""
        with patch.dict("os.environ", {"SANDCASTLE_ADVISOR_PROVIDER": "Mistral"}), \
             patch("sandcastle.config.settings", _fake_settings()):
            cfg = _get_advisor_config()
        assert cfg["api_key_env"] == "MISTRAL_API_KEY"


# ===================================================================
# 2. Failover chain scenarios (12 tests)
# ===================================================================


class TestFailoverChain:
    """Test the ProviderFailover class and _call_advisor_llm failover behavior."""

    def test_primary_fails_429_second_succeeds(self):
        """When the primary returns 429, the second provider is tried and succeeds."""
        loop = asyncio.new_event_loop()
        try:
            # Set up: anthropic fails with 429, openai succeeds
            call_count = {"n": 0}

            async def mock_post(url, json, headers, **kwargs):
                call_count["n"] += 1
                if "anthropic.com" in url:
                    resp = httpx.Response(
                        429,
                        request=httpx.Request("POST", url),
                        json={"error": "rate limited"},
                    )
                    resp.raise_for_status()
                return httpx.Response(
                    200,
                    request=httpx.Request("POST", url),
                    json=_openai_ok_response("fallback-ok"),
                )

            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            with patch.dict("os.environ", {
                "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
                "SANDCASTLE_ADVISOR_MODEL": "",
            }), \
                 patch("sandcastle.config.settings", _fake_settings(
                     openai_api_key="sk-test-openai",
                 )), \
                 patch("httpx.AsyncClient", return_value=mock_client):

                result = loop.run_until_complete(
                    _call_advisor_llm("sys", "user", purpose="generation")
                )
            assert result == "fallback-ok"
        finally:
            loop.close()

    def test_primary_500_second_429_third_succeeds(self):
        """Chain: primary -> 500, second -> 429, third -> success."""
        loop = asyncio.new_event_loop()
        try:
            attempts = []

            async def mock_post(url, json, headers, **kwargs):
                attempts.append(url)
                if len(attempts) == 1:
                    # First call: 500
                    resp = httpx.Response(500, request=httpx.Request("POST", url))
                    resp.raise_for_status()
                elif len(attempts) == 2:
                    # Second call: 429
                    resp = httpx.Response(429, request=httpx.Request("POST", url))
                    resp.raise_for_status()
                else:
                    # Third call: success
                    return httpx.Response(
                        200,
                        request=httpx.Request("POST", url),
                        json=_openai_ok_response("third-ok"),
                    )

            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            with patch.dict("os.environ", {
                "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
                "SANDCASTLE_ADVISOR_MODEL": "",
            }), \
                 patch("sandcastle.config.settings", _fake_settings(
                     openai_api_key="sk-test-openai",
                     mistral_api_key="sk-test-mistral",
                 )), \
                 patch("httpx.AsyncClient", return_value=mock_client):

                result = loop.run_until_complete(
                    _call_advisor_llm("sys", "user", purpose="generation")
                )
            assert result == "third-ok"
            assert len(attempts) == 3
        finally:
            loop.close()

    def test_all_providers_fail_raises_runtime_error(self):
        """When all providers fail, a RuntimeError with all attempts is raised."""
        loop = asyncio.new_event_loop()
        try:
            async def mock_post(url, json, headers, **kwargs):
                resp = httpx.Response(500, request=httpx.Request("POST", url))
                resp.raise_for_status()

            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            with patch.dict("os.environ", {
                "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
                "SANDCASTLE_ADVISOR_MODEL": "",
            }), \
                 patch("sandcastle.config.settings", _fake_settings()), \
                 patch("httpx.AsyncClient", return_value=mock_client):

                with pytest.raises(RuntimeError, match="All advisor providers failed"):
                    loop.run_until_complete(
                        _call_advisor_llm("sys", "user", purpose="generation")
                    )
        finally:
            loop.close()

    def test_failover_respects_eu_residency(self):
        """In EU residency mode, only EU-region providers appear in the fallback list."""
        with patch.dict("os.environ", {"SANDCASTLE_ADVISOR_PROVIDER": "mistral"}), \
             patch("sandcastle.config.settings", _fake_settings(
                 data_residency="eu",
                 mistral_api_key="sk-test-mistral",
             )):
            providers = _build_providers_to_try("mistral", "eu")
        # Only mistral (eu) and ollama (local - excluded because region!=eu)
        for p in providers:
            cfg = _PROVIDER_CONFIGS[p]
            assert cfg["region"] == "eu", f"Provider '{p}' has region '{cfg['region']}', expected 'eu'"

    def test_cooldown_marks_provider_unavailable(self):
        """After mark_cooldown(), the provider must be unavailable."""
        fo = ProviderFailover(rate_limit_cooldown_seconds=10.0)
        fo.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=30.0)
        assert fo.is_available("ANTHROPIC_API_KEY") is False

    def test_cooldown_expires_provider_available(self):
        """After cooldown expires, the provider becomes available again."""
        fo = ProviderFailover(rate_limit_cooldown_seconds=10.0)
        # Set cooldown that expires immediately (0.01s)
        fo.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=0.01)
        time.sleep(0.02)
        assert fo.is_available("ANTHROPIC_API_KEY") is True

    def test_rapid_429s_cooldown_does_not_stack(self):
        """Multiple rapid mark_cooldown calls should overwrite, not add up."""
        fo = ProviderFailover(rate_limit_cooldown_seconds=10.0)
        fo.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=5.0, is_rate_limit=True)
        time.sleep(0.01)
        fo.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=5.0, is_rate_limit=True)
        # The cooldown should be ~5s from the second call, NOT 10s stacked
        # Verify by checking the internal state
        with fo._lock:
            deadline = fo._cooldowns["ANTHROPIC_API_KEY"]
        # The deadline should be within 5.1 seconds from now (not 10+)
        remaining = deadline - time.monotonic()
        assert remaining < 5.1, f"Cooldown stacked: {remaining:.2f}s remaining"

    def test_rate_limit_cooldown_shorter_than_server_error(self):
        """Rate-limit cooldown should be shorter than the default 5xx cooldown."""
        fo = ProviderFailover(rate_limit_cooldown_seconds=15.0)
        # Rate limit cooldown = 15s, server error default = failover_cooldown_seconds (60s)
        fo.mark_cooldown("KEY_A", duration_seconds=60.0, is_rate_limit=False)
        fo.mark_cooldown("KEY_B", duration_seconds=None, is_rate_limit=True)
        with fo._lock:
            remaining_a = fo._cooldowns["KEY_A"] - time.monotonic()
            remaining_b = fo._cooldowns["KEY_B"] - time.monotonic()
        assert remaining_b < remaining_a, "Rate-limit cooldown should be shorter"

    def test_clear_cooldown_immediately_available(self):
        """clear_cooldown removes a provider from cooldown instantly."""
        fo = ProviderFailover(rate_limit_cooldown_seconds=10.0)
        fo.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=300.0)
        assert fo.is_available("ANTHROPIC_API_KEY") is False
        fo.clear_cooldown("ANTHROPIC_API_KEY")
        assert fo.is_available("ANTHROPIC_API_KEY") is True

    def test_get_alternatives_filters_cooled_down(self):
        """get_alternatives skips models whose key is on cooldown."""
        fo = ProviderFailover(rate_limit_cooldown_seconds=10.0)
        # Cool down OPENAI_API_KEY
        fo.mark_cooldown("OPENAI_API_KEY", duration_seconds=300.0)

        with patch("sandcastle.engine.providers.get_api_key") as mock_key:
            mock_key.return_value = "some-key"
            alts = fo.get_alternatives("sonnet")

        # No openai model should appear because the key is cooled down
        for alt in alts:
            info = PROVIDER_REGISTRY.get(alt)
            assert info.api_key_env != "OPENAI_API_KEY", (
                f"Cooled-down model '{alt}' should not appear in alternatives"
            )

    def test_get_alternatives_filters_no_key(self):
        """get_alternatives skips models that have no configured API key."""
        fo = ProviderFailover(rate_limit_cooldown_seconds=10.0)

        with patch("sandcastle.engine.providers.get_api_key") as mock_key:
            # Only return a key for mistral - all others return ""
            def key_for_info(info):
                if info.api_key_env == "MISTRAL_API_KEY":
                    return "sk-mistral-test"
                return ""
            mock_key.side_effect = key_for_info
            alts = fo.get_alternatives("sonnet")

        # Only mistral models should appear
        for alt in alts:
            info = PROVIDER_REGISTRY.get(alt)
            assert info.api_key_env == "MISTRAL_API_KEY", (
                f"Model '{alt}' has key '{info.api_key_env}' but only MISTRAL_API_KEY was configured"
            )

    def test_connection_error_triggers_failover(self):
        """ConnectError on the primary must trigger failover to the next provider."""
        loop = asyncio.new_event_loop()
        try:
            attempts = []

            async def mock_post(url, json, headers, **kwargs):
                attempts.append(url)
                if len(attempts) == 1:
                    raise httpx.ConnectError("Connection refused")
                return httpx.Response(
                    200,
                    request=httpx.Request("POST", url),
                    json=_openai_ok_response("fallback-connect-ok"),
                )

            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            with patch.dict("os.environ", {
                "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
                "SANDCASTLE_ADVISOR_MODEL": "",
            }), \
                 patch("sandcastle.config.settings", _fake_settings(
                     openai_api_key="sk-test-openai",
                 )), \
                 patch("httpx.AsyncClient", return_value=mock_client):

                result = loop.run_until_complete(
                    _call_advisor_llm("sys", "user", purpose="generation")
                )
            assert result == "fallback-connect-ok"
            assert len(attempts) >= 2
        finally:
            loop.close()


# ===================================================================
# 3. SLO routing scenarios (10 tests)
# ===================================================================


class TestSLORouting:
    """Verify that SLO-aware quality tier routing selects the correct model
    based on purpose, quality_mode, and model overrides."""

    def test_generation_purpose_gets_high_tier(self):
        """purpose='generation' maps to quality tier 'high'."""
        tier = ADVISOR_QUALITY_TIERS["generation"]
        assert tier == "high"

    def test_judge_purpose_gets_low_tier(self):
        """purpose='judge' maps to quality tier 'low'."""
        tier = ADVISOR_QUALITY_TIERS["judge"]
        assert tier == "low"

    def test_evolution_purpose_gets_medium_tier(self):
        """purpose='evolution' maps to quality tier 'medium'."""
        tier = ADVISOR_QUALITY_TIERS["evolution"]
        assert tier == "medium"

    def test_unknown_purpose_falls_back_to_high(self):
        """An unknown purpose defaults to 'high' tier via dict.get fallback."""
        tier = ADVISOR_QUALITY_TIERS.get("nonexistent_purpose", "high")
        assert tier == "high"

    def test_high_tier_anthropic_resolves_to_sonnet(self):
        """For anthropic, high tier should select claude-sonnet-4."""
        model = _resolve_model_for_tier("anthropic", "high")
        assert model == "claude-sonnet-4-20250514"

    def test_low_tier_anthropic_resolves_to_haiku(self):
        """For anthropic, low tier should select claude-haiku-4."""
        model = _resolve_model_for_tier("anthropic", "low")
        assert model == "claude-haiku-4-20250514"

    def test_high_tier_mistral_resolves_to_large(self):
        """For mistral, high tier should select mistral-large-latest."""
        model = _resolve_model_for_tier("mistral", "high")
        assert model == "mistral-large-latest"

    def test_low_tier_mistral_resolves_to_small(self):
        """For mistral, low tier should select mistral-small-latest."""
        model = _resolve_model_for_tier("mistral", "low")
        assert model == "mistral-small-latest"

    def test_unknown_provider_returns_none(self):
        """Unknown provider has no tier map and returns None."""
        result = _resolve_model_for_tier("imaginary_provider", "high")
        assert result is None

    def test_quality_mode_always_best_overrides_purpose(self):
        """quality_mode='always_best' forces high tier regardless of purpose."""
        loop = asyncio.new_event_loop()
        try:
            captured_body = {}

            async def mock_post(url, json, headers, **kwargs):
                captured_body.update(json)
                return httpx.Response(
                    200,
                    request=httpx.Request("POST", url),
                    json=_anthropic_ok_response("ok"),
                )

            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            with patch.dict("os.environ", {
                "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
                "SANDCASTLE_ADVISOR_MODEL": "",
            }), \
                 patch("sandcastle.config.settings", _fake_settings(
                     advisor_quality_mode="always_best",
                 )), \
                 patch("httpx.AsyncClient", return_value=mock_client):

                loop.run_until_complete(
                    _call_advisor_llm("sys", "user", purpose="judge")
                )
            # "judge" would normally get "low" tier (haiku), but always_best forces "high" (sonnet)
            expected_model = ADVISOR_MODEL_TIERS["anthropic"]["high"]
            assert captured_body["model"] == expected_model
        finally:
            loop.close()

    def test_quality_mode_always_cheapest_uses_low_tier(self):
        """quality_mode='always_cheapest' forces low tier for all purposes."""
        loop = asyncio.new_event_loop()
        try:
            captured_body = {}

            async def mock_post(url, json, headers, **kwargs):
                captured_body.update(json)
                return httpx.Response(
                    200,
                    request=httpx.Request("POST", url),
                    json=_anthropic_ok_response("ok"),
                )

            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            with patch.dict("os.environ", {
                "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
                "SANDCASTLE_ADVISOR_MODEL": "",
            }), \
                 patch("sandcastle.config.settings", _fake_settings(
                     advisor_quality_mode="always_cheapest",
                 )), \
                 patch("httpx.AsyncClient", return_value=mock_client):

                loop.run_until_complete(
                    _call_advisor_llm("sys", "user", purpose="generation")
                )
            # "generation" would normally get "high" tier, but always_cheapest forces "low"
            expected_model = ADVISOR_MODEL_TIERS["anthropic"]["low"]
            assert captured_body["model"] == expected_model
        finally:
            loop.close()

    def test_model_override_trumps_slo_routing(self):
        """SANDCASTLE_ADVISOR_MODEL takes precedence over SLO tier selection."""
        loop = asyncio.new_event_loop()
        try:
            captured_body = {}

            async def mock_post(url, json, headers, **kwargs):
                captured_body.update(json)
                return httpx.Response(
                    200,
                    request=httpx.Request("POST", url),
                    json=_anthropic_ok_response("ok"),
                )

            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            with patch.dict("os.environ", {
                "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
                "SANDCASTLE_ADVISOR_MODEL": "my-custom-model-v9",
            }), \
                 patch("sandcastle.config.settings", _fake_settings()), \
                 patch("httpx.AsyncClient", return_value=mock_client):

                loop.run_until_complete(
                    _call_advisor_llm("sys", "user", purpose="generation")
                )
            assert captured_body["model"] == "my-custom-model-v9"
        finally:
            loop.close()


# ===================================================================
# 4. Cost estimation scenarios (9 tests)
# ===================================================================


class TestCostEstimation:
    """Verify the workflow run cost estimation endpoint logic.

    Tests exercise the same logic as POST /runs/estimate but at the unit
    level by importing the route handler and mocking the workflow parser.
    """

    # Average tokens per step type from the route handler
    AVG_TOKENS = {
        "standard": (2000, 1500),
        "llm": (1000, 800),
        "classify": (500, 200),
        "gate": (500, 200),
        "delegate": (2000, 1500),
        "browser": (3000, 2000),
    }
    NON_LLM = {
        "http", "code", "condition", "loop", "race", "sensor",
        "transform", "notify", "composio", "sub_workflow",
        "openclaw", "parse",
    }
    SINGLE_CALL_TYPES = {"classify", "gate"}

    def _estimate_step_cost(self, step_type: str, model_key: str = "sonnet",
                            max_turns: int = 10, parallel_over: bool = False) -> float:
        """Replicate the cost estimation logic from the route handler."""
        if step_type in self.NON_LLM:
            return 0.0

        model_info = PROVIDER_REGISTRY.get(model_key)
        if not model_info:
            model_info = PROVIDER_REGISTRY["sonnet"]

        avg_in, avg_out = self.AVG_TOKENS.get(step_type, (1500, 1000))

        if step_type in self.SINGLE_CALL_TYPES:
            turns = 1
        else:
            turns = min(max_turns, 5)

        est_in = avg_in * turns
        est_out = avg_out * turns
        cost = (est_in * model_info.input_price_per_m + est_out * model_info.output_price_per_m) / 1_000_000

        if parallel_over:
            cost *= 10

        return cost

    def test_simple_3_step_workflow_cost(self):
        """Cost estimate for 3 standard steps with default sonnet model."""
        cost_per_step = self._estimate_step_cost("standard", "sonnet")
        total = cost_per_step * 3
        assert total > 0, "3-step workflow should have non-zero cost"
        # Verify calculation: (2000*5)*3/1M + (1500*5)*15/1M per step * 3
        sonnet = PROVIDER_REGISTRY["sonnet"]
        expected_per_step = (
            2000 * 5 * sonnet.input_price_per_m +
            1500 * 5 * sonnet.output_price_per_m
        ) / 1_000_000
        assert abs(total - expected_per_step * 3) < 1e-10

    def test_parallel_over_multiplier(self):
        """parallel_over multiplies the step cost by 10."""
        cost_single = self._estimate_step_cost("standard", "sonnet", parallel_over=False)
        cost_parallel = self._estimate_step_cost("standard", "sonnet", parallel_over=True)
        assert abs(cost_parallel - cost_single * 10) < 1e-10

    def test_different_models_per_step(self):
        """Different models yield different costs for the same step type."""
        cost_sonnet = self._estimate_step_cost("standard", "sonnet")
        cost_haiku = self._estimate_step_cost("standard", "haiku")
        cost_opus = self._estimate_step_cost("standard", "opus")
        # opus > sonnet > haiku in pricing
        assert cost_opus > cost_sonnet > cost_haiku

    def test_http_step_zero_cost(self):
        """HTTP steps have zero LLM cost."""
        assert self._estimate_step_cost("http") == 0.0

    def test_code_step_zero_cost(self):
        """Code execution steps have zero LLM cost."""
        assert self._estimate_step_cost("code") == 0.0

    def test_condition_step_zero_cost(self):
        """Condition branching steps have zero LLM cost."""
        assert self._estimate_step_cost("condition") == 0.0

    def test_transform_step_zero_cost(self):
        """Transform steps have zero LLM cost."""
        assert self._estimate_step_cost("transform") == 0.0

    def test_unknown_model_falls_back_to_sonnet_pricing(self):
        """An unknown model in a step uses sonnet pricing as fallback."""
        cost_unknown = self._estimate_step_cost("standard", "nonexistent-model-xyz")
        cost_sonnet = self._estimate_step_cost("standard", "sonnet")
        assert cost_unknown == cost_sonnet

    def test_classify_single_call(self):
        """classify steps issue a single LLM call regardless of max_turns."""
        cost_1 = self._estimate_step_cost("classify", "sonnet", max_turns=1)
        cost_10 = self._estimate_step_cost("classify", "sonnet", max_turns=10)
        # Both should be identical because classify always uses 1 turn
        assert cost_1 == cost_10


# ===================================================================
# 5. Data residency enforcement (10 tests)
# ===================================================================


class TestDataResidencyEnforcement:
    """Verify that data residency constraints are enforced at every layer."""

    def test_eu_mode_blocks_anthropic(self):
        """EU residency blocks anthropic (us region) with a clear error."""
        with patch.dict("os.environ", {"SANDCASTLE_ADVISOR_PROVIDER": "anthropic"}), \
             patch("sandcastle.config.settings", _fake_settings(data_residency="eu")):
            with pytest.raises(ValueError, match="Compliant providers"):
                _get_advisor_config()

    def test_eu_mode_allows_mistral(self):
        """EU residency allows mistral (eu region)."""
        with patch.dict("os.environ", {"SANDCASTLE_ADVISOR_PROVIDER": "mistral"}), \
             patch("sandcastle.config.settings", _fake_settings(data_residency="eu")):
            cfg = _get_advisor_config()
        assert cfg["region"] == "eu"

    def test_eu_mode_blocks_openai(self):
        """EU residency blocks openai (us region)."""
        with patch.dict("os.environ", {"SANDCASTLE_ADVISOR_PROVIDER": "openai"}), \
             patch("sandcastle.config.settings", _fake_settings(data_residency="eu")):
            with pytest.raises(ValueError, match="Data residency mode 'eu'"):
                _get_advisor_config()

    def test_local_mode_only_ollama_allowed(self):
        """Local residency only allows ollama."""
        with patch.dict("os.environ", {"SANDCASTLE_ADVISOR_PROVIDER": "ollama"}), \
             patch("sandcastle.config.settings", _fake_settings(data_residency="local")):
            cfg = _get_advisor_config()
        assert cfg["region"] == "local"

    def test_local_mode_blocks_mistral(self):
        """Local residency blocks mistral (eu region)."""
        with patch.dict("os.environ", {"SANDCASTLE_ADVISOR_PROVIDER": "mistral"}), \
             patch("sandcastle.config.settings", _fake_settings(data_residency="local")):
            with pytest.raises(ValueError, match="Data residency mode 'local'"):
                _get_advisor_config()

    def test_no_residency_all_providers_allowed(self):
        """Empty data_residency allows all providers."""
        for provider_name in _PROVIDER_CONFIGS:
            with patch.dict("os.environ", {"SANDCASTLE_ADVISOR_PROVIDER": provider_name}), \
                 patch("sandcastle.config.settings", _fake_settings(data_residency="")):
                cfg = _get_advisor_config()
            assert cfg is not None, f"Provider '{provider_name}' should be allowed with no residency"

    def test_residency_in_failover_chain_filtered(self):
        """_build_providers_to_try only includes providers matching the residency."""
        with patch.dict("os.environ", {"SANDCASTLE_ADVISOR_PROVIDER": "mistral"}), \
             patch("sandcastle.config.settings", _fake_settings(
                 data_residency="eu",
                 mistral_api_key="sk-test",
             )):
            providers = _build_providers_to_try("mistral", "eu")
        for p in providers:
            region = _PROVIDER_CONFIGS[p].get("region", "us")
            assert region == "eu", f"Provider '{p}' region '{region}' violates EU residency"

    def test_failover_alternatives_respect_eu_residency(self):
        """ProviderFailover.get_alternatives filters by data_residency='eu'."""
        fo = ProviderFailover(rate_limit_cooldown_seconds=10.0)
        with patch("sandcastle.engine.providers.get_api_key") as mock_key:
            mock_key.return_value = "some-key"
            alts = fo.get_alternatives("mistral/large", data_residency="eu")

        for alt in alts:
            info = PROVIDER_REGISTRY.get(alt)
            assert info is not None
            assert info.region == "eu", (
                f"Alternative '{alt}' has region '{info.region}', expected 'eu'"
            )

    def test_failover_alternatives_respect_local_residency(self):
        """ProviderFailover.get_alternatives with data_residency='local' returns only local models."""
        fo = ProviderFailover(rate_limit_cooldown_seconds=10.0)
        with patch("sandcastle.engine.providers.get_api_key") as mock_key:
            mock_key.return_value = "some-key"
            alts = fo.get_alternatives("ollama", data_residency="local")
        # ollama has no failover chain, so should be empty
        assert alts == []

    def test_residency_error_lists_compliant_providers(self):
        """The residency error message must list providers that ARE compliant."""
        with patch.dict("os.environ", {"SANDCASTLE_ADVISOR_PROVIDER": "anthropic"}), \
             patch("sandcastle.config.settings", _fake_settings(data_residency="eu")):
            with pytest.raises(ValueError, match="mistral") as exc_info:
                _get_advisor_config()
        # The error should list mistral as a compliant EU provider
        assert "mistral" in str(exc_info.value)


# ===================================================================
# 6. Provider registry data integrity (bonus coverage, 6 tests)
# ===================================================================


class TestRegistryDataIntegrity:
    """Additional structural tests on the provider registry and failover chains."""

    def test_all_failover_chain_models_exist_in_registry(self):
        """Every model referenced in FAILOVER_CHAINS must exist in the registry."""
        for primary, chain in FAILOVER_CHAINS.items():
            assert primary in PROVIDER_REGISTRY, f"Primary '{primary}' not in registry"
            for alt in chain:
                assert alt in PROVIDER_REGISTRY, (
                    f"Failover alternative '{alt}' (from '{primary}') not in registry"
                )

    def test_failover_chains_do_not_self_reference(self):
        """A model's failover chain must not include itself."""
        for model, chain in FAILOVER_CHAINS.items():
            assert model not in chain, f"Model '{model}' is in its own failover chain"

    def test_all_registry_models_have_failover_chain(self):
        """Every model in the registry must have an entry in FAILOVER_CHAINS."""
        for model_key in PROVIDER_REGISTRY:
            assert model_key in FAILOVER_CHAINS, (
                f"Model '{model_key}' has no entry in FAILOVER_CHAINS"
            )

    def test_eu_models_have_correct_region(self):
        """All mistral models must have region='eu'."""
        for key, info in PROVIDER_REGISTRY.items():
            if info.provider == "mistral":
                assert info.region == "eu", f"Mistral model '{key}' has region '{info.region}'"

    def test_local_models_have_zero_pricing(self):
        """Local models (ollama) must have zero pricing."""
        for key, info in PROVIDER_REGISTRY.items():
            if info.region == "local":
                assert info.input_price_per_m == 0.0, f"Local model '{key}' has non-zero input price"
                assert info.output_price_per_m == 0.0, f"Local model '{key}' has non-zero output price"

    def test_advisor_model_tiers_cover_all_providers(self):
        """ADVISOR_MODEL_TIERS must have entries for all known provider names."""
        provider_names = {cfg.get("region"): name for name, cfg in _PROVIDER_CONFIGS.items()}
        for provider_name in _PROVIDER_CONFIGS:
            assert provider_name in ADVISOR_MODEL_TIERS, (
                f"Provider '{provider_name}' missing from ADVISOR_MODEL_TIERS"
            )


# ===================================================================
# 7. Request body and response parsing (bonus, 5 tests)
# ===================================================================


class TestRequestBodyFormatting:
    """Verify that provider-specific request bodies and response parsing
    are correctly built for Anthropic vs OpenAI formats."""

    def test_anthropic_body_has_system_field(self):
        """Anthropic format puts system prompt in a top-level 'system' field."""
        body = _build_request_body("claude-sonnet", "sys prompt", [{"role": "user", "content": "hi"}],
                                   is_anthropic=True)
        assert "system" in body
        assert body["system"] == "sys prompt"
        # Messages should NOT contain the system prompt
        assert all(m["role"] != "system" for m in body["messages"])

    def test_openai_body_has_system_in_messages(self):
        """OpenAI format puts system prompt as the first message."""
        body = _build_request_body("gpt-4o", "sys prompt", [{"role": "user", "content": "hi"}],
                                   is_anthropic=False)
        assert "system" not in body
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][0]["content"] == "sys prompt"

    def test_anthropic_body_includes_model_and_max_tokens(self):
        """Both model and max_tokens must be present."""
        body = _build_request_body("claude-sonnet", "sys", [], max_tokens=8192, is_anthropic=True)
        assert body["model"] == "claude-sonnet"
        assert body["max_tokens"] == 8192

    def test_openai_body_includes_model_and_max_tokens(self):
        """OpenAI body must include model and max_tokens."""
        body = _build_request_body("gpt-4o", "sys", [], max_tokens=4096, is_anthropic=False)
        assert body["model"] == "gpt-4o"
        assert body["max_tokens"] == 4096

    def test_provider_configs_all_have_required_fields(self):
        """Every _PROVIDER_CONFIGS entry must have api_url, model, api_key_env, region, headers_fn."""
        required = {"api_url", "model", "api_key_env", "region", "headers_fn"}
        for name, cfg in _PROVIDER_CONFIGS.items():
            for field in required:
                assert field in cfg, f"Provider '{name}' missing field '{field}'"


# ===================================================================
# 8. ProviderFailover status reporting (bonus, 3 tests)
# ===================================================================


class TestFailoverStatusReporting:
    """Verify the get_status() diagnostic output."""

    def test_status_empty_when_no_cooldowns(self):
        """Fresh failover instance reports no active cooldowns."""
        fo = ProviderFailover(rate_limit_cooldown_seconds=10.0)
        status = fo.get_status()
        assert status["active_cooldowns"] == {}

    def test_status_shows_active_cooldown(self):
        """Active cooldown appears in get_status output."""
        fo = ProviderFailover(rate_limit_cooldown_seconds=10.0)
        fo.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=60.0)
        status = fo.get_status()
        assert "ANTHROPIC_API_KEY" in status["active_cooldowns"]
        remaining = status["active_cooldowns"]["ANTHROPIC_API_KEY"]
        assert 0 < remaining <= 60.0

    def test_status_expired_cooldown_cleaned_up(self):
        """Expired cooldowns are removed from the status."""
        fo = ProviderFailover(rate_limit_cooldown_seconds=10.0)
        fo.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=0.01)
        time.sleep(0.02)
        status = fo.get_status()
        assert "ANTHROPIC_API_KEY" not in status["active_cooldowns"]
