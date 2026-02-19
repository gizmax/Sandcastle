"""Tests for model failover chains and ProviderFailover class."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from sandcastle.engine.providers import (
    FAILOVER_CHAINS,
    KNOWN_MODELS,
    PROVIDER_REGISTRY,
    ProviderFailover,
    get_failover,
)
from sandcastle.engine.sandshore import SandshoreError, SandshoreRuntime


# ---------------------------------------------------------------------------
# TestFailoverChains
# ---------------------------------------------------------------------------


class TestFailoverChains:
    """Validate structure and correctness of FAILOVER_CHAINS."""

    def test_all_models_have_chains(self) -> None:
        """Every known model must have a failover chain."""
        for model in KNOWN_MODELS:
            assert model in FAILOVER_CHAINS, f"Missing chain for {model}"

    def test_chains_reference_valid_models(self) -> None:
        """All models in chains must exist in PROVIDER_REGISTRY."""
        for model, chain in FAILOVER_CHAINS.items():
            for alt in chain:
                assert alt in PROVIDER_REGISTRY, (
                    f"Chain for '{model}' references unknown model '{alt}'"
                )

    def test_no_self_reference(self) -> None:
        """A model must not appear in its own chain."""
        for model, chain in FAILOVER_CHAINS.items():
            assert model not in chain, f"'{model}' references itself"

    def test_same_provider_first(self) -> None:
        """First alternative should be from the same provider (where possible)."""
        for model, chain in FAILOVER_CHAINS.items():
            if not chain:
                continue
            primary_provider = PROVIDER_REGISTRY[model].provider
            first_alt_provider = PROVIDER_REGISTRY[chain[0]].provider
            # Only check when there are same-provider alternatives
            same_provider_alts = [
                a for a in chain if PROVIDER_REGISTRY[a].provider == primary_provider
            ]
            if same_provider_alts:
                assert first_alt_provider == primary_provider, (
                    f"Chain for '{model}': first alt '{chain[0]}' is "
                    f"'{first_alt_provider}', expected '{primary_provider}'"
                )

    def test_chains_are_non_empty(self) -> None:
        """Every chain must have at least one alternative."""
        for model, chain in FAILOVER_CHAINS.items():
            assert len(chain) >= 1, f"Chain for '{model}' is empty"


# ---------------------------------------------------------------------------
# TestProviderFailover
# ---------------------------------------------------------------------------


class TestProviderFailover:
    """Test ProviderFailover cooldown tracking."""

    def test_initial_state_all_available(self) -> None:
        pf = ProviderFailover()
        for info in PROVIDER_REGISTRY.values():
            assert pf.is_available(info.api_key_env)

    def test_mark_cooldown(self) -> None:
        pf = ProviderFailover()
        pf.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=10.0)
        assert not pf.is_available("ANTHROPIC_API_KEY")

    def test_cooldown_expires(self) -> None:
        pf = ProviderFailover()
        pf.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=0.01)
        time.sleep(0.02)
        assert pf.is_available("ANTHROPIC_API_KEY")

    def test_custom_duration(self) -> None:
        pf = ProviderFailover()
        pf.mark_cooldown("OPENAI_API_KEY", duration_seconds=5.0)
        assert not pf.is_available("OPENAI_API_KEY")
        # Other keys unaffected
        assert pf.is_available("ANTHROPIC_API_KEY")

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test", "OPENAI_API_KEY": "sk-oa"})
    def test_filters_cooldown_in_alternatives(self) -> None:
        pf = ProviderFailover()
        pf.mark_cooldown("OPENAI_API_KEY", duration_seconds=60.0)
        alts = pf.get_alternatives("sonnet")
        # OpenAI models should be excluded
        for alt in alts:
            info = PROVIDER_REGISTRY[alt]
            assert info.api_key_env != "OPENAI_API_KEY"

    def test_filters_unconfigured_keys(self) -> None:
        """Models with no API key configured are excluded from alternatives."""
        pf = ProviderFailover()
        with patch.dict("os.environ", {}, clear=True):
            with patch("sandcastle.config.settings") as mock_settings:
                # No keys configured anywhere
                mock_settings.anthropic_api_key = ""
                mock_settings.e2b_api_key = ""
                mock_settings.minimax_api_key = ""
                mock_settings.openai_api_key = ""
                mock_settings.openrouter_api_key = ""
                alts = pf.get_alternatives("sonnet")
                assert alts == []

    def test_get_status(self) -> None:
        pf = ProviderFailover()
        pf.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=30.0)
        status = pf.get_status()
        assert "active_cooldowns" in status
        assert "available_models" in status
        assert "unavailable_models" in status
        assert "ANTHROPIC_API_KEY" in status["active_cooldowns"]

    def test_get_status_expired_cleaned(self) -> None:
        pf = ProviderFailover()
        pf.mark_cooldown("ANTHROPIC_API_KEY", duration_seconds=0.01)
        time.sleep(0.02)
        status = pf.get_status()
        assert "ANTHROPIC_API_KEY" not in status["active_cooldowns"]


# ---------------------------------------------------------------------------
# TestGetFailoverSingleton
# ---------------------------------------------------------------------------


class TestGetFailoverSingleton:
    """Test that get_failover returns the same instance."""

    def test_singleton(self) -> None:
        f1 = get_failover()
        f2 = get_failover()
        assert f1 is f2


# ---------------------------------------------------------------------------
# TestSandshoreFailoverIntegration
# ---------------------------------------------------------------------------


class TestSandshoreFailoverIntegration:
    """Test _is_retriable_provider_error and async failover logic."""

    @pytest.mark.parametrize(
        "msg",
        [
            "Error 429: Too many requests",
            "rate limit exceeded",
            "too many requests",
            "HTTP 500 Internal Server Error",
            "HTTP 502 Bad Gateway",
            "HTTP 503 Service Unavailable",
            "HTTP 504 Gateway Timeout",
            "server error occurred",
            "model is overloaded",
            "at capacity, try again later",
        ],
    )
    def test_retriable_patterns(self, msg: str) -> None:
        assert SandshoreRuntime._is_retriable_provider_error(msg)

    @pytest.mark.parametrize(
        "msg",
        [
            "invalid api key",
            "authentication failed",
            "model not found",
            "bad request",
            "context length exceeded",
        ],
    )
    def test_non_retriable_patterns(self, msg: str) -> None:
        assert not SandshoreRuntime._is_retriable_provider_error(msg)

    @pytest.mark.asyncio
    async def test_failover_on_retriable_error(self) -> None:
        """Primary model fails with 429, failover to alternative succeeds."""
        from sandcastle.engine.backends import SSEEvent

        call_count = 0

        async def mock_stream_once(request, cancel_event=None):
            nonlocal call_count
            call_count += 1
            model = request.get("model", "sonnet")
            if model == "sonnet":
                raise SandshoreError("429 rate limit exceeded")
                yield  # makes this an async generator
            # Alternative succeeds
            yield SSEEvent(event="result", data={"type": "result", "result": "ok"})

        runtime = SandshoreRuntime.__new__(SandshoreRuntime)
        runtime._sandbox_backend_type = "mock"
        runtime._stream_backend_once = mock_stream_once

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test", "OPENAI_API_KEY": "sk-oa"}):
            events = []
            async for event in runtime._stream_backend({"model": "sonnet", "prompt": "test"}):
                events.append(event)

            assert len(events) == 1
            assert events[0].data["result"] == "ok"
            assert call_count == 2  # primary + 1 alternative

    @pytest.mark.asyncio
    async def test_no_failover_on_non_retriable_error(self) -> None:
        """Non-retriable errors are raised immediately without failover."""

        async def mock_stream_once(request, cancel_event=None):
            raise SandshoreError("invalid api key")
            yield  # makes this an async generator

        runtime = SandshoreRuntime.__new__(SandshoreRuntime)
        runtime._sandbox_backend_type = "mock"
        runtime._stream_backend_once = mock_stream_once

        with pytest.raises(SandshoreError, match="invalid api key"):
            async for _ in runtime._stream_backend({"model": "sonnet", "prompt": "test"}):
                pass

    @pytest.mark.asyncio
    async def test_all_alternatives_exhausted(self) -> None:
        """When all alternatives fail, raises SandshoreError."""

        async def mock_stream_once(request, cancel_event=None):
            raise SandshoreError("429 rate limit exceeded")
            yield  # makes this an async generator

        runtime = SandshoreRuntime.__new__(SandshoreRuntime)
        runtime._sandbox_backend_type = "mock"
        runtime._stream_backend_once = mock_stream_once

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test", "OPENAI_API_KEY": "sk-oa"}):
            with pytest.raises(SandshoreError, match="exhausted|no alternatives"):
                async for _ in runtime._stream_backend({"model": "sonnet", "prompt": "test"}):
                    pass
