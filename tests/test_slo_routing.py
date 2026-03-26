"""Tests for SLO-aware advisor routing (quality tiers per purpose).

Covers:
1. purpose="generation" selects high-tier model
2. purpose="judge" selects low-tier model
3. purpose="evolution" selects medium-tier model
4. SANDCASTLE_ADVISOR_MODEL override beats SLO routing
5. advisor_quality_mode="always_best" always picks high
6. advisor_quality_mode="always_cheapest" always picks low
7. Failover preserves quality tier (uses correct tier on each provider)
8. Unknown purpose defaults to "high" tier
9. Audit event includes quality_tier and model_selected_by
10. Provider without tier config uses provider default model
11. "auto" mode is the default quality mode
12. chat purpose maps to high tier
13. explain purpose maps to medium tier
14. ADVISOR_QUALITY_TIERS covers all expected purposes
15. ADVISOR_MODEL_TIERS covers all known providers
16. _resolve_model_for_tier returns None for unknown provider
17. _resolve_model_for_tier returns correct model for known provider/tier
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.generator import (
    ADVISOR_MODEL_TIERS,
    ADVISOR_QUALITY_TIERS,
    _PROVIDER_CONFIGS,
    _resolve_model_for_tier,
)


# ---------------------------------------------------------------------------
# 1. ADVISOR_QUALITY_TIERS mapping tests
# ---------------------------------------------------------------------------


class TestAdvisorQualityTiers:
    """Verify the quality tier mapping per purpose."""

    def test_generation_is_high(self):
        assert ADVISOR_QUALITY_TIERS["generation"] == "high"

    def test_chat_is_high(self):
        assert ADVISOR_QUALITY_TIERS["chat"] == "high"

    def test_explain_is_medium(self):
        assert ADVISOR_QUALITY_TIERS["explain"] == "medium"

    def test_evolution_is_medium(self):
        assert ADVISOR_QUALITY_TIERS["evolution"] == "medium"

    def test_judge_is_low(self):
        assert ADVISOR_QUALITY_TIERS["judge"] == "low"

    def test_all_known_purposes_covered(self):
        """All purposes that callers use must have a tier defined."""
        expected_purposes = {"generation", "chat", "explain", "evolution", "judge"}
        assert expected_purposes.issubset(set(ADVISOR_QUALITY_TIERS.keys()))


# ---------------------------------------------------------------------------
# 2. ADVISOR_MODEL_TIERS mapping tests
# ---------------------------------------------------------------------------


class TestAdvisorModelTiers:
    """Verify the model tier map for each provider."""

    def test_all_known_providers_covered(self):
        """Every provider in _PROVIDER_CONFIGS should have a tier map."""
        for provider_name in _PROVIDER_CONFIGS:
            assert provider_name in ADVISOR_MODEL_TIERS, (
                f"Provider '{provider_name}' is missing from ADVISOR_MODEL_TIERS"
            )

    def test_anthropic_high_is_sonnet(self):
        assert "sonnet" in ADVISOR_MODEL_TIERS["anthropic"]["high"]

    def test_anthropic_medium_is_haiku(self):
        assert "haiku" in ADVISOR_MODEL_TIERS["anthropic"]["medium"]

    def test_anthropic_low_is_haiku(self):
        assert "haiku" in ADVISOR_MODEL_TIERS["anthropic"]["low"]

    def test_mistral_high_is_large(self):
        assert "large" in ADVISOR_MODEL_TIERS["mistral"]["high"]

    def test_mistral_low_is_small(self):
        assert "small" in ADVISOR_MODEL_TIERS["mistral"]["low"]

    def test_openai_high_is_gpt4o(self):
        assert "gpt-4o" in ADVISOR_MODEL_TIERS["openai"]["high"]

    def test_openai_low_is_mini(self):
        assert "mini" in ADVISOR_MODEL_TIERS["openai"]["low"]

    def test_each_provider_has_all_tiers(self):
        required_tiers = {"high", "medium", "low"}
        for provider, tier_map in ADVISOR_MODEL_TIERS.items():
            assert required_tiers.issubset(set(tier_map.keys())), (
                f"Provider '{provider}' is missing tiers: {required_tiers - set(tier_map.keys())}"
            )


# ---------------------------------------------------------------------------
# 3. _resolve_model_for_tier helper
# ---------------------------------------------------------------------------


class TestResolveModelForTier:
    """Unit tests for the _resolve_model_for_tier helper."""

    def test_known_provider_high(self):
        model = _resolve_model_for_tier("anthropic", "high")
        assert model is not None
        assert "sonnet" in model

    def test_known_provider_low(self):
        model = _resolve_model_for_tier("anthropic", "low")
        assert model is not None
        assert "haiku" in model

    def test_unknown_provider_returns_none(self):
        model = _resolve_model_for_tier("nonexistent-provider", "high")
        assert model is None

    def test_mistral_medium_is_small(self):
        model = _resolve_model_for_tier("mistral", "medium")
        assert model is not None
        assert "small" in model

    def test_openai_medium_is_mini(self):
        model = _resolve_model_for_tier("openai", "medium")
        assert model is not None
        assert "mini" in model


# ---------------------------------------------------------------------------
# 4. _call_advisor_llm - SLO routing integration tests
# ---------------------------------------------------------------------------


def _make_http_response(text: str) -> MagicMock:
    """Build a mock httpx response for an Anthropic-format API call."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "content": [{"text": text}]
    }
    return mock_resp


@pytest.fixture()
def mock_http_client():
    """Patch httpx.AsyncClient so no real HTTP calls are made."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=_make_http_response("test-response"))
    with patch("sandcastle.engine.generator.httpx.AsyncClient", return_value=mock_client):
        yield mock_client


@pytest.fixture()
def mock_audit():
    """Suppress audit DB writes by patching at the source module level."""
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=ctx)
    ctx.__aexit__ = AsyncMock(return_value=False)
    ctx.commit = AsyncMock()
    with (
        patch("sandcastle.models.db.async_session", return_value=ctx),
        patch("sandcastle.engine.audit.append_audit_event", new=AsyncMock()),
    ):
        yield ctx


@pytest.mark.asyncio
class TestCallAdvisorLlmRouting:
    """Tests that _call_advisor_llm selects the right model based on purpose."""

    @patch.dict(os.environ, {
        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
        "ANTHROPIC_API_KEY": "sk-test-key",
        "SANDCASTLE_ADVISOR_MODEL": "",
    })
    async def test_generation_uses_high_tier_model(self, mock_http_client, mock_audit):
        from sandcastle.engine.generator import _call_advisor_llm

        await _call_advisor_llm(system="sys", user="user", purpose="generation")
        call_args = mock_http_client.post.call_args
        body = call_args[1]["json"]
        assert "sonnet" in body["model"]

    @patch.dict(os.environ, {
        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
        "ANTHROPIC_API_KEY": "sk-test-key",
        "SANDCASTLE_ADVISOR_MODEL": "",
    })
    async def test_judge_uses_low_tier_model(self, mock_http_client, mock_audit):
        from sandcastle.engine.generator import _call_advisor_llm

        await _call_advisor_llm(system="sys", user="user", purpose="judge")
        call_args = mock_http_client.post.call_args
        body = call_args[1]["json"]
        assert "haiku" in body["model"]

    @patch.dict(os.environ, {
        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
        "ANTHROPIC_API_KEY": "sk-test-key",
        "SANDCASTLE_ADVISOR_MODEL": "",
    })
    async def test_evolution_uses_medium_tier_model(self, mock_http_client, mock_audit):
        from sandcastle.engine.generator import _call_advisor_llm

        await _call_advisor_llm(system="sys", user="user", purpose="evolution")
        call_args = mock_http_client.post.call_args
        body = call_args[1]["json"]
        # medium tier for anthropic is haiku
        assert "haiku" in body["model"]

    @patch.dict(os.environ, {
        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
        "ANTHROPIC_API_KEY": "sk-test-key",
        "SANDCASTLE_ADVISOR_MODEL": "my-custom-model-v99",
    })
    async def test_model_override_beats_slo_routing(self, mock_http_client, mock_audit):
        """SANDCASTLE_ADVISOR_MODEL overrides SLO tier selection."""
        from sandcastle.engine.generator import _call_advisor_llm

        await _call_advisor_llm(system="sys", user="user", purpose="judge")
        call_args = mock_http_client.post.call_args
        body = call_args[1]["json"]
        assert body["model"] == "my-custom-model-v99"

    @patch.dict(os.environ, {
        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
        "ANTHROPIC_API_KEY": "sk-test-key",
        "SANDCASTLE_ADVISOR_MODEL": "",
    })
    async def test_unknown_purpose_defaults_to_high(self, mock_http_client, mock_audit):
        """An unknown purpose string should default to 'high' tier."""
        from sandcastle.engine.generator import _call_advisor_llm

        await _call_advisor_llm(system="sys", user="user", purpose="unknown-purpose-xyz")
        call_args = mock_http_client.post.call_args
        body = call_args[1]["json"]
        assert "sonnet" in body["model"]

    @patch.dict(os.environ, {
        "SANDCASTLE_ADVISOR_PROVIDER": "mistral",
        "MISTRAL_API_KEY": "test-mistral-key",
        "SANDCASTLE_ADVISOR_MODEL": "",
    })
    async def test_mistral_judge_uses_small_model(self, mock_http_client, mock_audit):
        """Mistral + purpose=judge should use mistral-small (low tier)."""
        mock_http_client.post.return_value = MagicMock(
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={
                "choices": [{"message": {"content": "0.7"}}]
            })
        )
        from sandcastle.engine.generator import _call_advisor_llm

        await _call_advisor_llm(system="sys", user="user", purpose="judge")
        call_args = mock_http_client.post.call_args
        body = call_args[1]["json"]
        assert "small" in body["model"]

    @patch.dict(os.environ, {
        "SANDCASTLE_ADVISOR_PROVIDER": "mistral",
        "MISTRAL_API_KEY": "test-mistral-key",
        "SANDCASTLE_ADVISOR_MODEL": "",
    })
    async def test_mistral_generation_uses_large_model(self, mock_http_client, mock_audit):
        """Mistral + purpose=generation should use mistral-large (high tier)."""
        mock_http_client.post.return_value = MagicMock(
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={
                "choices": [{"message": {"content": "result"}}]
            })
        )
        from sandcastle.engine.generator import _call_advisor_llm

        await _call_advisor_llm(system="sys", user="user", purpose="generation")
        call_args = mock_http_client.post.call_args
        body = call_args[1]["json"]
        assert "large" in body["model"]


# ---------------------------------------------------------------------------
# 5. advisor_quality_mode setting tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestQualityModeSettings:
    """Tests for advisor_quality_mode in Settings."""

    @patch.dict(os.environ, {
        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
        "ANTHROPIC_API_KEY": "sk-test",
        "SANDCASTLE_ADVISOR_MODEL": "",
    })
    async def test_always_best_mode_picks_high_for_judge(self, mock_http_client, mock_audit):
        """always_best forces high tier even for judge purpose."""
        from sandcastle.config import Settings
        from sandcastle.engine.generator import _call_advisor_llm

        # settings is imported lazily inside _call_advisor_llm - patch at source
        with patch("sandcastle.config.settings", Settings(
            anthropic_api_key="sk-test",
            advisor_quality_mode="always_best",
        )):
            await _call_advisor_llm(system="sys", user="user", purpose="judge")
            call_args = mock_http_client.post.call_args
            body = call_args[1]["json"]
            assert "sonnet" in body["model"]

    @patch.dict(os.environ, {
        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
        "ANTHROPIC_API_KEY": "sk-test",
        "SANDCASTLE_ADVISOR_MODEL": "",
    })
    async def test_always_cheapest_mode_picks_low_for_generation(self, mock_http_client, mock_audit):
        """always_cheapest forces low tier even for generation purpose."""
        from sandcastle.config import Settings
        from sandcastle.engine.generator import _call_advisor_llm

        # settings is imported lazily inside _call_advisor_llm - patch at source
        with patch("sandcastle.config.settings", Settings(
            anthropic_api_key="sk-test",
            advisor_quality_mode="always_cheapest",
        )):
            await _call_advisor_llm(system="sys", user="user", purpose="generation")
            call_args = mock_http_client.post.call_args
            body = call_args[1]["json"]
            assert "haiku" in body["model"]

    async def test_default_quality_mode_is_auto(self):
        """advisor_quality_mode should default to 'auto'."""
        from sandcastle.config import Settings

        s = Settings()
        assert s.advisor_quality_mode == "auto"


# ---------------------------------------------------------------------------
# 6. Audit payload tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAuditPayloadEnhancement:
    """Tests that audit events include quality_tier and model_selected_by."""

    @patch.dict(os.environ, {
        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
        "ANTHROPIC_API_KEY": "sk-test-key",
        "SANDCASTLE_ADVISOR_MODEL": "",
    })
    async def test_audit_event_contains_quality_tier(self):
        """Audit payload must include quality_tier key."""
        captured_payloads: list[dict] = []

        async def mock_append_audit(session, *, event_type, run_id=None, actor_id=None, payload=None):
            if payload:
                captured_payloads.append(dict(payload))

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=_make_http_response("response"))

        mock_sess_ctx = AsyncMock()
        mock_sess_ctx.__aenter__ = AsyncMock(return_value=mock_sess_ctx)
        mock_sess_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_sess_ctx.commit = AsyncMock()

        with (
            patch("sandcastle.engine.generator.httpx.AsyncClient", return_value=mock_client),
            patch("sandcastle.models.db.async_session", return_value=mock_sess_ctx),
            patch("sandcastle.engine.audit.append_audit_event", side_effect=mock_append_audit),
        ):
            from sandcastle.engine.generator import _call_advisor_llm

            await _call_advisor_llm(system="sys", user="user", purpose="judge")

        assert len(captured_payloads) == 1
        payload = captured_payloads[0]
        assert "quality_tier" in payload
        assert payload["quality_tier"] == "low"

    @patch.dict(os.environ, {
        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
        "ANTHROPIC_API_KEY": "sk-test-key",
        "SANDCASTLE_ADVISOR_MODEL": "",
    })
    async def test_audit_event_contains_model_selected_by_slo(self):
        """Audit payload must include model_selected_by='slo_routing' for normal calls."""
        captured_payloads: list[dict] = []

        async def mock_append_audit(session, *, event_type, run_id=None, actor_id=None, payload=None):
            if payload:
                captured_payloads.append(dict(payload))

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=_make_http_response("response"))

        mock_sess_ctx = AsyncMock()
        mock_sess_ctx.__aenter__ = AsyncMock(return_value=mock_sess_ctx)
        mock_sess_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_sess_ctx.commit = AsyncMock()

        with (
            patch("sandcastle.engine.generator.httpx.AsyncClient", return_value=mock_client),
            patch("sandcastle.models.db.async_session", return_value=mock_sess_ctx),
            patch("sandcastle.engine.audit.append_audit_event", side_effect=mock_append_audit),
        ):
            from sandcastle.engine.generator import _call_advisor_llm

            await _call_advisor_llm(system="sys", user="user", purpose="generation")

        assert len(captured_payloads) == 1
        payload = captured_payloads[0]
        assert "model_selected_by" in payload
        assert payload["model_selected_by"] == "slo_routing"

    @patch.dict(os.environ, {
        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
        "ANTHROPIC_API_KEY": "sk-test-key",
        "SANDCASTLE_ADVISOR_MODEL": "my-override-model",
    })
    async def test_audit_event_model_selected_by_user_override(self):
        """When SANDCASTLE_ADVISOR_MODEL is set, model_selected_by='user_override'."""
        captured_payloads: list[dict] = []

        async def mock_append_audit(session, *, event_type, run_id=None, actor_id=None, payload=None):
            if payload:
                captured_payloads.append(dict(payload))

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=_make_http_response("response"))

        mock_sess_ctx = AsyncMock()
        mock_sess_ctx.__aenter__ = AsyncMock(return_value=mock_sess_ctx)
        mock_sess_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_sess_ctx.commit = AsyncMock()

        with (
            patch("sandcastle.engine.generator.httpx.AsyncClient", return_value=mock_client),
            patch("sandcastle.models.db.async_session", return_value=mock_sess_ctx),
            patch("sandcastle.engine.audit.append_audit_event", side_effect=mock_append_audit),
        ):
            from sandcastle.engine.generator import _call_advisor_llm

            await _call_advisor_llm(system="sys", user="user", purpose="judge")

        assert len(captured_payloads) == 1
        payload = captured_payloads[0]
        assert payload["model_selected_by"] == "user_override"


# ---------------------------------------------------------------------------
# 7. Failover quality tier preservation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFailoverQualityTierPreservation:
    """Tests that failover to another provider also uses the quality tier."""

    @patch.dict(os.environ, {
        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
        "ANTHROPIC_API_KEY": "sk-test-key",
        "MISTRAL_API_KEY": "mk-test-key",
        "SANDCASTLE_ADVISOR_MODEL": "",
    })
    async def test_failover_preserves_quality_tier(self):
        """When primary fails, the failover provider uses the same quality tier."""
        import httpx as _httpx

        call_models: list[str] = []

        async def mock_post(url, *, json=None, headers=None, **kwargs):
            model = json.get("model", "")
            call_models.append(model)
            # Fail the first call with 429 to trigger failover
            if len(call_models) == 1:
                mock_resp = MagicMock()
                mock_resp.status_code = 429
                raise _httpx.HTTPStatusError(
                    "Rate limited", request=MagicMock(), response=mock_resp
                )
            # Second call succeeds (mistral-compatible response format)
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": "fallback-response"}}]
            }
            return mock_resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=mock_post)

        mock_sess_ctx = AsyncMock()
        mock_sess_ctx.__aenter__ = AsyncMock(return_value=mock_sess_ctx)
        mock_sess_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_sess_ctx.commit = AsyncMock()

        with (
            patch("sandcastle.engine.generator.httpx.AsyncClient", return_value=mock_client),
            patch("sandcastle.models.db.async_session", return_value=mock_sess_ctx),
            patch("sandcastle.engine.audit.append_audit_event", AsyncMock()),
            patch("sandcastle.engine.generator._build_providers_to_try",
                  return_value=["anthropic", "mistral"]),
        ):
            from sandcastle.engine.generator import _call_advisor_llm

            await _call_advisor_llm(system="sys", user="user", purpose="generation")

        # Two calls were made - primary failed, failover succeeded
        assert len(call_models) == 2
        # The failover (mistral) call should use the high-tier mistral model
        failover_model = call_models[1]
        assert "large" in failover_model  # mistral high = mistral-large-latest
