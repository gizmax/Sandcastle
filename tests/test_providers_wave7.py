"""Wave 7 deep audit tests for the provider system.

Covers: failover logic, circuit breaker, model registry integrity,
cost calculation correctness, and edge cases.
"""

from __future__ import annotations

import asyncio
import math
import time
import threading
from unittest.mock import patch, MagicMock

import pytest

from sandcastle.engine.providers import (
    FAILOVER_CHAINS,
    KNOWN_MODELS,
    PROVIDER_REGISTRY,
    ModelInfo,
    ProviderFailover,
    get_api_key,
    get_failover,
    is_claude_model,
    resolve_model,
)
from sandcastle.engine.sandshore import (
    CircuitBreaker,
    RuntimeMetrics,
    SandshoreError,
    SandshoreResult,
    SandshoreRuntime,
)


# ===================================================================
# 1. Model Registry integrity
# ===================================================================


class TestModelRegistryIntegrity:
    """Deep validation of the model registry structure."""

    def test_all_models_have_string_keys(self):
        """Registry keys must be non-empty strings."""
        for key in PROVIDER_REGISTRY:
            assert isinstance(key, str)
            assert len(key) > 0

    def test_all_model_ids_are_strings(self):
        """api_model_id must be a non-empty string for every model."""
        for key, info in PROVIDER_REGISTRY.items():
            assert isinstance(info.api_model_id, str), f"{key}: api_model_id not str"
            assert len(info.api_model_id) > 0, f"{key}: empty api_model_id"

    def test_api_key_env_naming_convention(self):
        """API key env vars should follow UPPER_SNAKE_CASE convention."""
        import re
        for key, info in PROVIDER_REGISTRY.items():
            assert re.match(r"^[A-Z][A-Z0-9_]+$", info.api_key_env), (
                f"{key}: api_key_env '{info.api_key_env}' not UPPER_SNAKE_CASE"
            )

    def test_input_price_less_than_output_price(self):
        """For all models, input tokens should be cheaper than output tokens."""
        for key, info in PROVIDER_REGISTRY.items():
            assert info.input_price_per_m < info.output_price_per_m, (
                f"{key}: input price ({info.input_price_per_m}) >= "
                f"output price ({info.output_price_per_m})"
            )

    def test_claude_models_use_anthropic_key(self):
        """All Claude models must use ANTHROPIC_API_KEY."""
        for key, info in PROVIDER_REGISTRY.items():
            if info.provider == "claude":
                assert info.api_key_env == "ANTHROPIC_API_KEY", (
                    f"Claude model '{key}' uses wrong key env: {info.api_key_env}"
                )

    def test_openai_models_use_openai_key(self):
        """All OpenAI models must use OPENAI_API_KEY."""
        for key, info in PROVIDER_REGISTRY.items():
            if info.provider == "openai":
                assert info.api_key_env == "OPENAI_API_KEY"

    def test_non_claude_models_have_base_url(self):
        """Non-Claude models need a base URL for the OpenAI-compatible runner."""
        for key, info in PROVIDER_REGISTRY.items():
            if info.provider != "claude":
                assert info.api_base_url is not None, (
                    f"Non-Claude model '{key}' missing api_base_url"
                )
                assert info.api_base_url.startswith("https://"), (
                    f"Non-Claude model '{key}' base URL not HTTPS"
                )

    def test_claude_models_have_no_base_url(self):
        """Claude models should have None base URL (uses SDK default)."""
        for key, info in PROVIDER_REGISTRY.items():
            if info.provider == "claude":
                assert info.api_base_url is None, (
                    f"Claude model '{key}' has unexpected base URL"
                )

    def test_model_info_is_frozen(self):
        """ModelInfo instances must be frozen (immutable)."""
        info = resolve_model("sonnet")
        with pytest.raises(AttributeError):
            info.provider = "modified"  # type: ignore[misc]

    def test_known_models_is_frozenset(self):
        """KNOWN_MODELS should be immutable."""
        assert isinstance(KNOWN_MODELS, frozenset)

    def test_resolve_model_returns_same_object(self):
        """Resolving the same model twice returns the identical object."""
        a = resolve_model("sonnet")
        b = resolve_model("sonnet")
        assert a is b

    def test_resolve_unknown_model_error_message(self):
        """Error message for unknown model should list all available models."""
        with pytest.raises(KeyError) as exc_info:
            resolve_model("totally-fake-model")
        error_msg = str(exc_info.value)
        for model in PROVIDER_REGISTRY:
            assert model in error_msg

    def test_no_duplicate_api_model_ids_within_provider(self):
        """Within the same provider, api_model_ids should be unique."""
        by_provider: dict[str, list[str]] = {}
        for key, info in PROVIDER_REGISTRY.items():
            by_provider.setdefault(info.provider, []).append(info.api_model_id)
        for provider, ids in by_provider.items():
            assert len(ids) == len(set(ids)), (
                f"Provider '{provider}' has duplicate api_model_ids: {ids}"
            )

    def test_pricing_values_are_reasonable(self):
        """Pricing should be within reasonable bounds (0.01 to 200 per 1M tokens)."""
        for key, info in PROVIDER_REGISTRY.items():
            assert 0.01 <= info.input_price_per_m <= 200.0, (
                f"{key}: input price {info.input_price_per_m} out of range"
            )
            assert 0.01 <= info.output_price_per_m <= 200.0, (
                f"{key}: output price {info.output_price_per_m} out of range"
            )


class TestResolveModelEdgeCases:
    """Edge cases for resolve_model."""

    def test_empty_string_raises(self):
        with pytest.raises(KeyError):
            resolve_model("")

    def test_none_raises(self):
        with pytest.raises((KeyError, TypeError)):
            resolve_model(None)  # type: ignore[arg-type]

    def test_case_sensitive(self):
        """Model names are case-sensitive."""
        with pytest.raises(KeyError):
            resolve_model("Sonnet")
        with pytest.raises(KeyError):
            resolve_model("SONNET")

    def test_whitespace_not_stripped(self):
        """Leading/trailing whitespace is not tolerated."""
        with pytest.raises(KeyError):
            resolve_model(" sonnet")
        with pytest.raises(KeyError):
            resolve_model("sonnet ")


class TestGetApiKey:
    """Edge cases for get_api_key."""

    def test_unknown_key_env_returns_empty(self):
        """If api_key_env is not in the attr_map, returns empty string."""
        info = ModelInfo(
            provider="test",
            api_model_id="test-model",
            runner="runner.mjs",
            api_key_env="UNKNOWN_KEY_VAR",
            api_base_url=None,
            input_price_per_m=1.0,
            output_price_per_m=2.0,
        )
        # UNKNOWN_KEY_VAR is not in attr_map and not in env
        with patch.dict("os.environ", {}, clear=False):
            if "UNKNOWN_KEY_VAR" in __import__("os").environ:
                del __import__("os").environ["UNKNOWN_KEY_VAR"]
            result = get_api_key(info)
            assert result == ""

    def test_env_var_takes_priority_over_settings(self, monkeypatch):
        """Direct env var should take priority over settings attribute."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key-123")
        info = resolve_model("sonnet")
        assert get_api_key(info) == "env-key-123"


class TestIsClaude:
    """Edge cases for is_claude_model."""

    def test_empty_string(self):
        assert is_claude_model("") is False

    def test_partial_match(self):
        """Partial model names should not match."""
        assert is_claude_model("son") is False
        assert is_claude_model("claude") is False


# ===================================================================
# 2. Failover chain structure
# ===================================================================


class TestFailoverChainStructure:
    """Deep validation of failover chain correctness."""

    def test_all_models_have_failover_chains(self):
        """Every registered model must have a failover chain."""
        for model in PROVIDER_REGISTRY:
            assert model in FAILOVER_CHAINS, f"No failover chain for '{model}'"

    def test_no_orphan_chains(self):
        """Failover chains should not reference non-existent models."""
        for model, chain in FAILOVER_CHAINS.items():
            assert model in PROVIDER_REGISTRY, f"Chain key '{model}' not in registry"
            for alt in chain:
                assert alt in PROVIDER_REGISTRY, (
                    f"Chain for '{model}' has unknown alt '{alt}'"
                )

    def test_no_self_reference_in_chains(self):
        """A model must never appear in its own failover chain."""
        for model, chain in FAILOVER_CHAINS.items():
            assert model not in chain, f"'{model}' references itself in chain"

    def test_no_duplicate_entries_in_chain(self):
        """Each failover chain should have unique entries."""
        for model, chain in FAILOVER_CHAINS.items():
            assert len(chain) == len(set(chain)), (
                f"Duplicate entries in chain for '{model}': {chain}"
            )

    def test_cross_provider_coverage(self):
        """Each model should have at least one cross-provider alternative."""
        for model, chain in FAILOVER_CHAINS.items():
            primary_provider = PROVIDER_REGISTRY[model].provider
            cross_provider = [
                alt for alt in chain
                if PROVIDER_REGISTRY[alt].provider != primary_provider
            ]
            assert len(cross_provider) >= 1, (
                f"Model '{model}' has no cross-provider alternatives"
            )

    def test_bidirectional_failover(self):
        """If A is in B's chain, B should be in A's chain (bidirectional)."""
        for model, chain in FAILOVER_CHAINS.items():
            for alt in chain:
                alt_chain = FAILOVER_CHAINS.get(alt, [])
                assert model in alt_chain, (
                    f"'{alt}' is in '{model}' chain but '{model}' "
                    f"is not in '{alt}' chain (asymmetric failover)"
                )

    def test_chain_includes_at_least_2_alternatives(self):
        """Each model should have at least 2 alternatives for resilience."""
        for model, chain in FAILOVER_CHAINS.items():
            assert len(chain) >= 2, (
                f"Model '{model}' has only {len(chain)} alternative(s), need >= 2"
            )


# ===================================================================
# 3. ProviderFailover
# ===================================================================


class TestProviderFailoverCooldown:
    """Detailed tests for ProviderFailover cooldown mechanics."""

    def test_initial_all_available(self):
        pf = ProviderFailover()
        for info in PROVIDER_REGISTRY.values():
            assert pf.is_available(info.api_key_env)

    def test_mark_cooldown_makes_unavailable(self):
        pf = ProviderFailover()
        pf.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=60.0)
        assert not pf.is_available("ANTHROPIC_API_KEY")

    def test_cooldown_expiry(self):
        pf = ProviderFailover()
        pf.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=0.01)
        time.sleep(0.02)
        assert pf.is_available("ANTHROPIC_API_KEY")

    def test_cooldown_cleanup_on_check(self):
        """Expired cooldowns should be cleaned from internal dict."""
        pf = ProviderFailover()
        pf.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=0.01)
        time.sleep(0.02)
        pf.is_available("ANTHROPIC_API_KEY")
        with pf._lock:
            assert "ANTHROPIC_API_KEY" not in pf._cooldowns

    def test_independent_cooldowns(self):
        """Cooldown on one key does not affect others."""
        pf = ProviderFailover()
        pf.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=60.0)
        assert pf.is_available("OPENAI_API_KEY")
        assert pf.is_available("MINIMAX_API_KEY")

    def test_clear_cooldown(self):
        """clear_cooldown removes cooldown immediately."""
        pf = ProviderFailover()
        pf.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=60.0)
        assert not pf.is_available("ANTHROPIC_API_KEY")
        pf.clear_cooldown("ANTHROPIC_API_KEY")
        assert pf.is_available("ANTHROPIC_API_KEY")

    def test_clear_cooldown_nonexistent_key(self):
        """Clearing a non-existent cooldown should not raise."""
        pf = ProviderFailover()
        pf.clear_cooldown("NONEXISTENT_KEY")  # should not raise

    def test_overwrite_cooldown_extends(self):
        """Marking cooldown again should overwrite the deadline."""
        pf = ProviderFailover()
        pf.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=0.01)
        # Overwrite with longer cooldown
        pf.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=60.0)
        time.sleep(0.02)
        assert not pf.is_available("ANTHROPIC_API_KEY")

    def test_rate_limit_cooldown_shorter(self):
        """Rate limit cooldown should be shorter than default."""
        pf = ProviderFailover()
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.failover_cooldown_seconds = 60.0
            pf.mark_cooldown("ANTHROPIC_API_KEY", is_rate_limit=True)
            pf.mark_cooldown("OPENAI_API_KEY", is_rate_limit=False)
            # Rate limit cooldown should be min(10, 60/4) = 15s
            # Normal cooldown should be 60s
            with pf._lock:
                now = time.monotonic()
                rl_remaining = pf._cooldowns["ANTHROPIC_API_KEY"] - now
                normal_remaining = pf._cooldowns["OPENAI_API_KEY"] - now
            assert rl_remaining < normal_remaining

    def test_rate_limit_cooldown_minimum_10s(self):
        """Rate limit cooldown should be at least 10 seconds."""
        pf = ProviderFailover()
        assert pf.rate_limit_cooldown_seconds >= 10.0

    def test_custom_rate_limit_cooldown(self):
        """Custom rate_limit_cooldown_seconds overrides the computed default."""
        pf = ProviderFailover(rate_limit_cooldown_seconds=5.0)
        assert pf.rate_limit_cooldown_seconds == 5.0


class TestProviderFailoverAlternatives:
    """Tests for get_alternatives filtering."""

    @patch.dict("os.environ", {
        "ANTHROPIC_API_KEY": "sk-ant",
        "OPENAI_API_KEY": "sk-oai",
        "MINIMAX_API_KEY": "sk-mm",
        "OPENROUTER_API_KEY": "sk-or",
    })
    def test_returns_alternatives_with_all_keys(self):
        """With all keys configured, all alternatives are returned."""
        pf = ProviderFailover()
        alts = pf.get_alternatives("sonnet")
        assert len(alts) > 0

    @patch.dict("os.environ", {
        "ANTHROPIC_API_KEY": "sk-ant",
        "OPENAI_API_KEY": "sk-oai",
        "MINIMAX_API_KEY": "sk-mm",
        "OPENROUTER_API_KEY": "sk-or",
    })
    def test_cooldown_filters_alternatives(self):
        """Alternatives with cooled-down keys are excluded."""
        pf = ProviderFailover()
        pf.mark_cooldown("OPENAI_API_KEY", duration_seconds=60.0)
        alts = pf.get_alternatives("sonnet")
        for alt in alts:
            info = PROVIDER_REGISTRY[alt]
            assert info.api_key_env != "OPENAI_API_KEY"

    @patch.dict("os.environ", {}, clear=True)
    def test_no_keys_returns_empty(self):
        """With no API keys configured, no alternatives are returned."""
        pf = ProviderFailover()
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.anthropic_api_key = ""
            mock_settings.e2b_api_key = ""
            mock_settings.minimax_api_key = ""
            mock_settings.openai_api_key = ""
            mock_settings.openrouter_api_key = ""
            alts = pf.get_alternatives("sonnet")
            assert alts == []

    def test_unknown_model_returns_empty(self):
        """Unknown model string returns empty alternatives list."""
        pf = ProviderFailover()
        alts = pf.get_alternatives("nonexistent-model")
        assert alts == []

    @patch.dict("os.environ", {
        "ANTHROPIC_API_KEY": "sk-ant",
        "OPENAI_API_KEY": "sk-oai",
        "MINIMAX_API_KEY": "sk-mm",
        "OPENROUTER_API_KEY": "sk-or",
    })
    def test_all_cooled_down_returns_empty(self):
        """When all provider keys are on cooldown, no alternatives remain."""
        pf = ProviderFailover()
        # Cool down every possible key
        for info in PROVIDER_REGISTRY.values():
            pf.mark_cooldown(info.api_key_env, duration_seconds=60.0)
        alts = pf.get_alternatives("sonnet")
        assert alts == []

    @patch.dict("os.environ", {
        "ANTHROPIC_API_KEY": "sk-ant",
        "OPENAI_API_KEY": "sk-oai",
        "MINIMAX_API_KEY": "sk-mm",
        "OPENROUTER_API_KEY": "sk-or",
    })
    def test_alternatives_preserve_chain_order(self):
        """Alternatives should maintain the order from FAILOVER_CHAINS."""
        pf = ProviderFailover()
        alts = pf.get_alternatives("sonnet")
        chain = FAILOVER_CHAINS["sonnet"]
        # Verify order is preserved (alts is a subset of chain in same order)
        chain_idx = 0
        for alt in alts:
            while chain_idx < len(chain) and chain[chain_idx] != alt:
                chain_idx += 1
            assert chain_idx < len(chain), (
                f"Alternative '{alt}' appears out of order"
            )
            chain_idx += 1


class TestProviderFailoverGetStatus:
    """Tests for get_status diagnostics."""

    def test_status_structure(self):
        pf = ProviderFailover()
        status = pf.get_status()
        assert "active_cooldowns" in status
        assert "available_models" in status
        assert "unavailable_models" in status

    def test_status_reflects_cooldowns(self):
        pf = ProviderFailover()
        pf.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=30.0)
        status = pf.get_status()
        assert "ANTHROPIC_API_KEY" in status["active_cooldowns"]
        remaining = status["active_cooldowns"]["ANTHROPIC_API_KEY"]
        assert 0 < remaining <= 30.0

    def test_status_cleans_expired(self):
        pf = ProviderFailover()
        pf.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=0.01)
        time.sleep(0.02)
        status = pf.get_status()
        assert "ANTHROPIC_API_KEY" not in status["active_cooldowns"]


class TestProviderFailoverThreadSafety:
    """Verify thread safety of ProviderFailover."""

    def test_concurrent_mark_and_check(self):
        """Concurrent mark_cooldown and is_available should not crash."""
        pf = ProviderFailover()
        errors: list[Exception] = []

        def mark_loop():
            try:
                for _ in range(100):
                    pf.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=0.001)
            except Exception as e:
                errors.append(e)

        def check_loop():
            try:
                for _ in range(100):
                    pf.is_available("ANTHROPIC_API_KEY")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=mark_loop),
            threading.Thread(target=check_loop),
            threading.Thread(target=mark_loop),
            threading.Thread(target=check_loop),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread safety violations: {errors}"


class TestGetFailoverSingleton:
    """Test singleton behavior."""

    def test_returns_same_instance(self):
        f1 = get_failover()
        f2 = get_failover()
        assert f1 is f2

    def test_is_provider_failover(self):
        assert isinstance(get_failover(), ProviderFailover)


# ===================================================================
# 4. Circuit breaker
# ===================================================================


class TestCircuitBreakerStates:
    """Detailed tests for circuit breaker state transitions."""

    @pytest.mark.asyncio
    async def test_initial_state_closed(self):
        cb = CircuitBreaker()
        assert cb.state == CircuitBreaker.CLOSED
        assert await cb.allow_request() is True

    @pytest.mark.asyncio
    async def test_stays_closed_under_threshold(self):
        cb = CircuitBreaker(failure_threshold=5)
        for _ in range(4):
            await cb.record_failure()
        assert cb.state == CircuitBreaker.CLOSED
        assert await cb.allow_request() is True

    @pytest.mark.asyncio
    async def test_trips_open_at_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
        for _ in range(3):
            await cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        assert await cb.allow_request() is False

    @pytest.mark.asyncio
    async def test_trips_open_above_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
        for _ in range(10):
            await cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN

    @pytest.mark.asyncio
    async def test_success_resets_to_closed(self):
        cb = CircuitBreaker(failure_threshold=2)
        await cb.record_failure()
        await cb.record_failure()
        assert cb._state == CircuitBreaker.OPEN
        await cb.record_success()
        assert cb.state == CircuitBreaker.CLOSED
        assert cb._failure_count == 0

    @pytest.mark.asyncio
    async def test_success_between_failures_resets_count(self):
        cb = CircuitBreaker(failure_threshold=3)
        await cb.record_failure()
        await cb.record_failure()
        await cb.record_success()
        # Count should be reset
        assert cb._failure_count == 0
        await cb.record_failure()
        assert cb.state == CircuitBreaker.CLOSED

    @pytest.mark.asyncio
    async def test_half_open_after_recovery_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.02)
        await cb.record_failure()
        assert cb._state == CircuitBreaker.OPEN
        await asyncio.sleep(0.03)
        assert cb.state == CircuitBreaker.HALF_OPEN

    @pytest.mark.asyncio
    async def test_half_open_allows_one_probe(self):
        """In HALF_OPEN, only one request should pass (the probe)."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.02)
        await cb.record_failure()
        await asyncio.sleep(0.03)

        # First request should be allowed (the probe)
        allowed_first = await cb.allow_request()
        assert allowed_first is True

        # Second concurrent request should be rejected
        allowed_second = await cb.allow_request()
        assert allowed_second is False

    @pytest.mark.asyncio
    async def test_half_open_probe_success_resets(self):
        """Successful probe in HALF_OPEN resets to CLOSED."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.02)
        await cb.record_failure()
        await asyncio.sleep(0.03)

        assert await cb.allow_request() is True  # probe allowed
        await cb.record_success()  # probe succeeded
        assert cb.state == CircuitBreaker.CLOSED
        # All requests should now pass
        assert await cb.allow_request() is True
        assert await cb.allow_request() is True

    @pytest.mark.asyncio
    async def test_half_open_probe_failure_trips_open(self):
        """Failed probe in HALF_OPEN trips back to OPEN immediately."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.02)
        await cb.record_failure()
        await asyncio.sleep(0.03)

        assert await cb.allow_request() is True  # probe allowed
        await cb.record_failure()  # probe failed
        assert cb._state == CircuitBreaker.OPEN
        # Should NOT allow another request until recovery_timeout
        assert await cb.allow_request() is False

    @pytest.mark.asyncio
    async def test_multiple_half_open_cycles(self):
        """Circuit breaker can cycle through OPEN -> HALF_OPEN -> OPEN
        multiple times before finally recovering."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.02)

        # First trip
        await cb.record_failure()
        assert cb._state == CircuitBreaker.OPEN

        # Wait for half-open
        await asyncio.sleep(0.03)
        assert await cb.allow_request() is True  # probe allowed
        await cb.record_failure()  # probe fails
        assert cb._state == CircuitBreaker.OPEN

        # Wait again
        await asyncio.sleep(0.03)
        assert await cb.allow_request() is True  # probe allowed
        await cb.record_success()  # probe succeeds
        assert cb.state == CircuitBreaker.CLOSED

    @pytest.mark.asyncio
    async def test_default_thresholds(self):
        cb = CircuitBreaker()
        assert cb.failure_threshold == 5
        assert cb.recovery_timeout == 30.0

    @pytest.mark.asyncio
    async def test_custom_thresholds(self):
        cb = CircuitBreaker(failure_threshold=10, recovery_timeout=120.0)
        assert cb.failure_threshold == 10
        assert cb.recovery_timeout == 120.0


class TestCircuitBreakerConcurrency:
    """Test circuit breaker under concurrent async access."""

    @pytest.mark.asyncio
    async def test_concurrent_failures(self):
        """Many concurrent failures should correctly trip the breaker."""
        cb = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)
        tasks = [cb.record_failure() for _ in range(20)]
        await asyncio.gather(*tasks)
        assert cb._state == CircuitBreaker.OPEN
        assert cb._failure_count >= 5

    @pytest.mark.asyncio
    async def test_concurrent_allow_request_half_open(self):
        """Only one of many concurrent allow_request calls should pass
        when in HALF_OPEN state."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.02)
        await cb.record_failure()
        await asyncio.sleep(0.03)

        results = await asyncio.gather(
            *[cb.allow_request() for _ in range(10)]
        )
        # Exactly one should be True (the probe)
        assert results.count(True) == 1


# ===================================================================
# 5. Cost calculation
# ===================================================================


class TestCostCalculation:
    """Tests for cost calculation correctness."""

    def test_basic_cost_formula(self):
        """Verify the standard cost formula: (in * price_in + out * price_out) / 1M."""
        info = resolve_model("sonnet")
        in_tokens = 1000
        out_tokens = 500
        expected = (
            in_tokens * info.input_price_per_m / 1_000_000
            + out_tokens * info.output_price_per_m / 1_000_000
        )
        # Sonnet: 1000 * 3.0 / 1M + 500 * 15.0 / 1M = 0.003 + 0.0075 = 0.0105
        assert math.isclose(expected, 0.0105, rel_tol=1e-9)

    def test_zero_tokens_zero_cost(self):
        """Zero tokens should result in zero cost."""
        info = resolve_model("sonnet")
        cost = 0 * info.input_price_per_m / 1_000_000 + 0 * info.output_price_per_m / 1_000_000
        assert cost == 0.0

    def test_large_token_count_precision(self):
        """Cost calculation should maintain precision for large token counts."""
        info = resolve_model("haiku")
        # 10M input tokens, 5M output tokens
        in_tokens = 10_000_000
        out_tokens = 5_000_000
        cost = (
            in_tokens * info.input_price_per_m / 1_000_000
            + out_tokens * info.output_price_per_m / 1_000_000
        )
        expected = 10 * info.input_price_per_m + 5 * info.output_price_per_m
        assert math.isclose(cost, expected, rel_tol=1e-9)

    def test_all_models_cost_positive_for_nonzero_tokens(self):
        """Every model should produce a positive cost for any non-zero token count."""
        for key, info in PROVIDER_REGISTRY.items():
            cost = (
                1 * info.input_price_per_m / 1_000_000
                + 1 * info.output_price_per_m / 1_000_000
            )
            assert cost > 0, f"Model '{key}' produced zero cost for 1 token"

    def test_cost_ordering_cheap_to_expensive(self):
        """Haiku should be cheapest, opus most expensive (for same tokens)."""
        in_t, out_t = 1000, 1000
        costs = {}
        for name in ("haiku", "sonnet", "opus"):
            info = resolve_model(name)
            costs[name] = (
                in_t * info.input_price_per_m / 1_000_000
                + out_t * info.output_price_per_m / 1_000_000
            )
        assert costs["haiku"] < costs["sonnet"] < costs["opus"]

    def test_cost_no_negative_values(self):
        """Cost must never be negative."""
        for key, info in PROVIDER_REGISTRY.items():
            cost = (
                100 * info.input_price_per_m / 1_000_000
                + 100 * info.output_price_per_m / 1_000_000
            )
            assert cost >= 0

    def test_float_precision_sum(self):
        """Summing many small costs should not accumulate excessive float error."""
        info = resolve_model("haiku")
        single_cost = (
            100 * info.input_price_per_m / 1_000_000
            + 50 * info.output_price_per_m / 1_000_000
        )
        total = sum(single_cost for _ in range(10000))
        expected = single_cost * 10000
        # Allow up to 0.01% relative error
        assert math.isclose(total, expected, rel_tol=1e-4)


# ===================================================================
# 6. Budget checking
# ===================================================================


class TestBudgetCheck:
    """Tests for the _check_budget function."""

    def test_no_budget_returns_none(self):
        """With no budget set, always returns None."""
        from sandcastle.engine.executor import RunContext, _check_budget
        ctx = RunContext(run_id="test", input={}, max_cost_usd=None)
        assert _check_budget(ctx) is None

    def test_zero_budget_returns_none(self):
        """Zero budget is treated as no budget."""
        from sandcastle.engine.executor import RunContext, _check_budget
        ctx = RunContext(run_id="test", input={}, max_cost_usd=0.0)
        assert _check_budget(ctx) is None

    def test_negative_budget_returns_none(self):
        """Negative budget is treated as no budget."""
        from sandcastle.engine.executor import RunContext, _check_budget
        ctx = RunContext(run_id="test", input={}, max_cost_usd=-1.0)
        assert _check_budget(ctx) is None

    def test_under_budget(self):
        """Under 80% of budget returns None."""
        from sandcastle.engine.executor import RunContext, _check_budget
        ctx = RunContext(run_id="test", input={}, max_cost_usd=1.0, costs=[0.5])
        assert _check_budget(ctx) is None

    def test_warning_at_80_percent(self):
        """At 80% of budget returns 'warning'."""
        from sandcastle.engine.executor import RunContext, _check_budget
        ctx = RunContext(run_id="test", input={}, max_cost_usd=1.0, costs=[0.8])
        assert _check_budget(ctx) == "warning"

    def test_warning_at_90_percent(self):
        """At 90% of budget returns 'warning'."""
        from sandcastle.engine.executor import RunContext, _check_budget
        ctx = RunContext(run_id="test", input={}, max_cost_usd=1.0, costs=[0.9])
        assert _check_budget(ctx) == "warning"

    def test_exceeded_at_100_percent(self):
        """At exactly 100% returns 'exceeded'."""
        from sandcastle.engine.executor import RunContext, _check_budget
        ctx = RunContext(run_id="test", input={}, max_cost_usd=1.0, costs=[1.0])
        assert _check_budget(ctx) == "exceeded"

    def test_exceeded_over_100_percent(self):
        """Over 100% returns 'exceeded'."""
        from sandcastle.engine.executor import RunContext, _check_budget
        ctx = RunContext(run_id="test", input={}, max_cost_usd=1.0, costs=[1.5])
        assert _check_budget(ctx) == "exceeded"

    def test_multiple_costs_sum(self):
        """Budget check sums all costs in the list."""
        from sandcastle.engine.executor import RunContext, _check_budget
        ctx = RunContext(
            run_id="test", input={}, max_cost_usd=1.0,
            costs=[0.3, 0.3, 0.25],
        )
        # 0.85 -> warning
        assert _check_budget(ctx) == "warning"

    def test_empty_costs_under_budget(self):
        """Empty costs list = 0 cost = under budget."""
        from sandcastle.engine.executor import RunContext, _check_budget
        ctx = RunContext(run_id="test", input={}, max_cost_usd=1.0, costs=[])
        assert _check_budget(ctx) is None

    def test_very_small_budget_precision(self):
        """Very small budget should not cause float comparison issues."""
        from sandcastle.engine.executor import RunContext, _check_budget
        ctx = RunContext(
            run_id="test", input={}, max_cost_usd=0.0001,
            costs=[0.00008],
        )
        assert _check_budget(ctx) == "warning"  # 80%

    def test_boundary_just_under_80(self):
        """Just under 80% should return None."""
        from sandcastle.engine.executor import RunContext, _check_budget
        ctx = RunContext(
            run_id="test", input={}, max_cost_usd=1.0,
            costs=[0.799],
        )
        assert _check_budget(ctx) is None

    def test_boundary_just_under_100(self):
        """Just under 100% should return 'warning', not 'exceeded'."""
        from sandcastle.engine.executor import RunContext, _check_budget
        ctx = RunContext(
            run_id="test", input={}, max_cost_usd=1.0,
            costs=[0.999],
        )
        assert _check_budget(ctx) == "warning"


class TestRunContextCosts:
    """Tests for cost tracking in RunContext."""

    def test_total_cost_empty(self):
        from sandcastle.engine.executor import RunContext
        ctx = RunContext(run_id="test", input={})
        assert ctx.total_cost == 0.0

    def test_total_cost_sum(self):
        from sandcastle.engine.executor import RunContext
        ctx = RunContext(run_id="test", input={}, costs=[0.1, 0.2, 0.3])
        assert math.isclose(ctx.total_cost, 0.6, rel_tol=1e-9)

    def test_child_context_isolated_costs(self):
        """Child contexts from with_item should have independent cost lists."""
        from sandcastle.engine.executor import RunContext
        parent = RunContext(
            run_id="test", input={}, costs=[0.5], max_cost_usd=10.0
        )
        child = parent.with_item("item1", 0)
        assert child.costs == []  # Independent
        child.costs.append(0.3)
        assert parent.total_cost == 0.5  # Parent unaffected

    def test_child_context_inherits_budget(self):
        """Child context should inherit max_cost_usd from parent."""
        from sandcastle.engine.executor import RunContext
        parent = RunContext(
            run_id="test", input={}, max_cost_usd=5.0
        )
        child = parent.with_item("item1", 0)
        assert child.max_cost_usd == 5.0

    def test_snapshot_includes_costs(self):
        from sandcastle.engine.executor import RunContext
        ctx = RunContext(run_id="test", input={}, costs=[0.1, 0.2])
        snap = ctx.snapshot()
        assert snap["costs"] == [0.1, 0.2]
        assert math.isclose(snap["total_cost"], 0.3, rel_tol=1e-9)


# ===================================================================
# 7. Retriable error detection
# ===================================================================


class TestRetriableErrorDetection:
    """Tests for _is_retriable_provider_error and _is_rate_limit_error."""

    @pytest.mark.parametrize("msg", [
        "Error 429: Too many requests",
        "rate limit exceeded",
        "too many requests",
        "HTTP 429 Too Many Requests",
        "429",
    ])
    def test_rate_limit_errors_retriable(self, msg):
        assert SandshoreRuntime._is_retriable_provider_error(msg)

    @pytest.mark.parametrize("msg", [
        "HTTP 500 Internal Server Error",
        "HTTP 502 Bad Gateway",
        "HTTP 503 Service Unavailable",
        "HTTP 504 Gateway Timeout",
        "server error occurred",
        "model is overloaded",
        "at capacity, try again later",
    ])
    def test_server_errors_retriable(self, msg):
        assert SandshoreRuntime._is_retriable_provider_error(msg)

    @pytest.mark.parametrize("msg", [
        "invalid api key",
        "authentication failed",
        "model not found",
        "bad request",
        "context length exceeded",
        "HTTP 400 Bad Request",
        "HTTP 401 Unauthorized",
        "HTTP 403 Forbidden",
        "HTTP 404 Not Found",
    ])
    def test_non_retriable_errors(self, msg):
        assert not SandshoreRuntime._is_retriable_provider_error(msg)

    @pytest.mark.parametrize("msg", [
        "429 rate limit",
        "rate limit exceeded",
        "too many requests",
    ])
    def test_is_rate_limit_error(self, msg):
        assert SandshoreRuntime._is_rate_limit_error(msg)

    @pytest.mark.parametrize("msg", [
        "HTTP 500 Internal Server Error",
        "server error",
        "overloaded",
    ])
    def test_server_error_not_rate_limit(self, msg):
        assert not SandshoreRuntime._is_rate_limit_error(msg)

    def test_505_not_retriable(self):
        """505 and above should not be retriable (only 500-504)."""
        assert not SandshoreRuntime._is_retriable_provider_error("HTTP 505")

    def test_empty_string_not_retriable(self):
        assert not SandshoreRuntime._is_retriable_provider_error("")


# ===================================================================
# 8. Runtime metrics
# ===================================================================


class TestRuntimeMetricsDetailed:
    """Detailed tests for RuntimeMetrics."""

    @pytest.mark.asyncio
    async def test_snapshot_cost_rounding(self):
        """Snapshot should round total_cost_usd to 6 decimal places."""
        m = RuntimeMetrics()
        await m.record_query_success(cost_usd=0.123456789)
        snap = m.snapshot()
        assert snap["total_cost_usd"] == round(0.123456789, 6)

    @pytest.mark.asyncio
    async def test_zero_cost_success(self):
        """Recording success with zero cost should work."""
        m = RuntimeMetrics()
        await m.record_query_success(cost_usd=0.0)
        snap = m.snapshot()
        assert snap["total_cost_usd"] == 0.0
        assert snap["successful_queries"] == 1

    @pytest.mark.asyncio
    async def test_cost_accumulation(self):
        """Cost should accumulate across multiple successes."""
        m = RuntimeMetrics()
        for _ in range(100):
            await m.record_query_success(cost_usd=0.01)
        snap = m.snapshot()
        assert math.isclose(snap["total_cost_usd"], 1.0, rel_tol=1e-3)

    @pytest.mark.asyncio
    async def test_concurrent_recording_stress(self):
        """Many concurrent recordings should not lose data."""
        m = RuntimeMetrics()
        tasks = []
        for _ in range(50):
            tasks.append(m.record_query_success(0.001))
            tasks.append(m.record_query_failure())
            tasks.append(m.record_failover())
        await asyncio.gather(*tasks)
        snap = m.snapshot()
        assert snap["total_queries"] == 100  # 50 success + 50 failure
        assert snap["successful_queries"] == 50
        assert snap["failed_queries"] == 50
        assert snap["failover_attempts"] == 50


# ===================================================================
# 9. Integration: failover flow
# ===================================================================


class TestFailoverIntegration:
    """Integration tests for the full failover flow in SandshoreRuntime."""

    @pytest.fixture(autouse=True)
    def _reset_failover_singleton(self):
        """Reset the global failover singleton to avoid cross-test pollution."""
        import sandcastle.engine.providers as _pmod
        old = _pmod._failover_instance
        _pmod._failover_instance = None
        yield
        _pmod._failover_instance = old

    @pytest.mark.asyncio
    async def test_primary_succeeds_no_failover(self):
        """When primary model succeeds, no failover occurs."""
        from sandcastle.engine.backends import SSEEvent

        call_models: list[str] = []

        async def mock_stream_once(request, cancel_event=None):
            call_models.append(request.get("model", "sonnet"))
            yield SSEEvent(event="result", data={"type": "result", "result": "ok"})

        runtime = SandshoreRuntime.__new__(SandshoreRuntime)
        runtime._sandbox_backend_type = "mock"
        runtime._metrics = RuntimeMetrics()
        runtime._stream_backend_once = mock_stream_once

        events = []
        async for event in runtime._stream_backend({"model": "sonnet", "prompt": "test"}):
            events.append(event)

        assert len(events) == 1
        assert call_models == ["sonnet"]

    @pytest.mark.asyncio
    async def test_failover_records_metrics(self):
        """Failover attempts should be recorded in metrics."""
        from sandcastle.engine.backends import SSEEvent

        call_count = 0

        async def mock_stream_once(request, cancel_event=None):
            nonlocal call_count
            call_count += 1
            if request.get("model") == "sonnet":
                raise SandshoreError("429 rate limit")
                yield  # make it an async generator
            yield SSEEvent(event="result", data={"type": "result", "result": "ok"})

        runtime = SandshoreRuntime.__new__(SandshoreRuntime)
        runtime._sandbox_backend_type = "mock"
        runtime._metrics = RuntimeMetrics()
        runtime._stream_backend_once = mock_stream_once

        with patch.dict("os.environ", {
            "ANTHROPIC_API_KEY": "sk-ant",
            "OPENAI_API_KEY": "sk-oai",
        }):
            async for _ in runtime._stream_backend({"model": "sonnet", "prompt": "test"}):
                pass

        assert runtime._metrics.snapshot()["failover_attempts"] >= 1

    @pytest.mark.asyncio
    async def test_non_retriable_error_no_failover(self):
        """Non-retriable errors should raise immediately, no failover."""
        async def mock_stream_once(request, cancel_event=None):
            raise SandshoreError("invalid api key")
            yield

        runtime = SandshoreRuntime.__new__(SandshoreRuntime)
        runtime._sandbox_backend_type = "mock"
        runtime._metrics = RuntimeMetrics()
        runtime._stream_backend_once = mock_stream_once

        with pytest.raises(SandshoreError, match="invalid api key"):
            async for _ in runtime._stream_backend({"model": "sonnet", "prompt": "test"}):
                pass

    @pytest.mark.asyncio
    async def test_all_alternatives_exhausted_error(self):
        """When all alternatives also fail, raises comprehensive error."""
        async def mock_stream_once(request, cancel_event=None):
            raise SandshoreError("429 rate limit exceeded")
            yield

        runtime = SandshoreRuntime.__new__(SandshoreRuntime)
        runtime._sandbox_backend_type = "mock"
        runtime._metrics = RuntimeMetrics()
        runtime._stream_backend_once = mock_stream_once

        with patch.dict("os.environ", {
            "ANTHROPIC_API_KEY": "sk-ant",
            "OPENAI_API_KEY": "sk-oai",
        }):
            with pytest.raises(SandshoreError, match="exhausted|no alternatives"):
                async for _ in runtime._stream_backend(
                    {"model": "sonnet", "prompt": "test"}
                ):
                    pass

    @pytest.mark.asyncio
    async def test_failover_with_5xx_uses_longer_cooldown(self):
        """5xx errors should put keys on longer cooldown than 429."""
        from sandcastle.engine.backends import SSEEvent

        async def mock_stream_once(request, cancel_event=None):
            model = request.get("model")
            if model == "sonnet":
                raise SandshoreError("HTTP 500 Internal Server Error")
                yield
            yield SSEEvent(event="result", data={"type": "result", "result": "ok"})

        runtime = SandshoreRuntime.__new__(SandshoreRuntime)
        runtime._sandbox_backend_type = "mock"
        runtime._metrics = RuntimeMetrics()
        runtime._stream_backend_once = mock_stream_once

        failover = ProviderFailover(rate_limit_cooldown_seconds=5.0)
        with patch("sandcastle.engine.sandshore.get_failover", return_value=failover):
            with patch.dict("os.environ", {
                "ANTHROPIC_API_KEY": "sk-ant",
                "OPENAI_API_KEY": "sk-oai",
            }):
                with patch("sandcastle.config.settings") as mock_settings:
                    mock_settings.failover_cooldown_seconds = 60.0
                    async for _ in runtime._stream_backend(
                        {"model": "sonnet", "prompt": "test"}
                    ):
                        pass

        # ANTHROPIC_API_KEY should be on the longer 5xx cooldown (60s),
        # not the shorter rate-limit cooldown (5s)
        assert not failover.is_available("ANTHROPIC_API_KEY")
        with failover._lock:
            remaining = failover._cooldowns["ANTHROPIC_API_KEY"] - time.monotonic()
        # Should be close to 60s (the normal cooldown), not 5s
        assert remaining > 30.0


# ===================================================================
# 10. SandshoreResult defaults
# ===================================================================


class TestSandshoreResultDefaults:
    """Verify SandshoreResult default values."""

    def test_all_defaults(self):
        r = SandshoreResult()
        assert r.text == ""
        assert r.structured_output is None
        assert r.total_cost_usd == 0.0
        assert r.num_turns == 0
        assert r.input_tokens == 0
        assert r.output_tokens == 0

    def test_with_cost_data(self):
        r = SandshoreResult(
            total_cost_usd=0.05,
            input_tokens=1000,
            output_tokens=500,
        )
        assert r.total_cost_usd == 0.05
        assert r.input_tokens == 1000
        assert r.output_tokens == 500


# ===================================================================
# 11. Unknown model fallback in _build_env
# ===================================================================


class TestBuildEnvFallback:
    """Test that _build_env falls back gracefully for unknown models."""

    def test_unknown_model_falls_back_to_sonnet(self):
        """_build_env should log a warning and fall back to sonnet."""
        runtime = SandshoreRuntime.__new__(SandshoreRuntime)
        runtime.anthropic_api_key = "sk-test"
        runtime.e2b_api_key = "e2b-test"
        runtime._sandbox_backend_type = "e2b"

        envs, runner_file, use_claude, tool_files = runtime._build_env(
            {"model": "nonexistent-model", "prompt": "test"}
        )
        # Should fall back to sonnet (Claude runner)
        assert use_claude is True
        assert runner_file == "runner.mjs"
        assert "ANTHROPIC_API_KEY" in envs

    def test_known_model_uses_correct_runner(self):
        """Known models should resolve to their correct runner."""
        runtime = SandshoreRuntime.__new__(SandshoreRuntime)
        runtime.anthropic_api_key = "sk-test"
        runtime.e2b_api_key = "e2b-test"
        runtime._sandbox_backend_type = "e2b"

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-oai"}):
            envs, runner_file, use_claude, tool_files = runtime._build_env(
                {"model": "openai/codex-mini", "prompt": "test"}
            )
        assert use_claude is False
        assert runner_file == "runner-openai.mjs"
        assert envs.get("MODEL_ID") == "codex-mini"

    def test_pricing_env_vars_always_set(self):
        """MODEL_INPUT_PRICE and MODEL_OUTPUT_PRICE should always be set."""
        runtime = SandshoreRuntime.__new__(SandshoreRuntime)
        runtime.anthropic_api_key = "sk-test"
        runtime.e2b_api_key = "e2b-test"
        runtime._sandbox_backend_type = "e2b"

        envs, *_ = runtime._build_env({"model": "sonnet", "prompt": "test"})
        assert "MODEL_INPUT_PRICE" in envs
        assert "MODEL_OUTPUT_PRICE" in envs
        assert float(envs["MODEL_INPUT_PRICE"]) > 0
        assert float(envs["MODEL_OUTPUT_PRICE"]) > 0
