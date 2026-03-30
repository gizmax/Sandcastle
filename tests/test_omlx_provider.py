"""Tests for the oMLX provider integration (local inference on Apple Silicon)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from sandcastle.engine.providers import (
    FAILOVER_CHAINS,
    KNOWN_MODELS,
    PROVIDER_REGISTRY,
    ModelInfo,
    ProviderFailover,
    resolve_base_url,
    resolve_model,
)


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

OMLX_MODELS = [
    "omlx/llama-4-scout",
    "omlx/mistral-small",
    "omlx/gemma-3",
    "omlx/qwen-3",
]


class TestOmlxProviderRegistry:
    """Verify all 4 oMLX models are registered correctly."""

    def test_all_omlx_models_in_registry(self):
        for model in OMLX_MODELS:
            assert model in PROVIDER_REGISTRY, f"{model} missing from PROVIDER_REGISTRY"

    def test_all_omlx_models_in_known_models(self):
        for model in OMLX_MODELS:
            assert model in KNOWN_MODELS, f"{model} missing from KNOWN_MODELS"

    def test_omlx_models_are_model_info(self):
        for model in OMLX_MODELS:
            assert isinstance(PROVIDER_REGISTRY[model], ModelInfo)

    def test_omlx_region_is_local(self):
        for model in OMLX_MODELS:
            info = PROVIDER_REGISTRY[model]
            assert info.region == "local", f"{model} region should be 'local'"

    def test_omlx_pricing_is_zero(self):
        for model in OMLX_MODELS:
            info = PROVIDER_REGISTRY[model]
            assert info.input_price_per_m == 0.0, f"{model} input price should be 0.0"
            assert info.output_price_per_m == 0.0, f"{model} output price should be 0.0"

    def test_omlx_no_api_key_required(self):
        for model in OMLX_MODELS:
            info = PROVIDER_REGISTRY[model]
            assert info.api_key_env == "", f"{model} should not require an API key"

    def test_omlx_provider_is_omlx(self):
        for model in OMLX_MODELS:
            info = PROVIDER_REGISTRY[model]
            assert info.provider == "omlx"

    def test_omlx_uses_openai_runner(self):
        for model in OMLX_MODELS:
            info = PROVIDER_REGISTRY[model]
            assert info.runner == "runner-openai.mjs"

    def test_omlx_model_ids(self):
        expected = {
            "omlx/llama-4-scout": "mlx-community/Llama-4-Scout-17B-16E-Instruct-4bit",
            "omlx/mistral-small": "mlx-community/Mistral-Small-24B-Instruct-2501-4bit",
            "omlx/gemma-3": "mlx-community/gemma-3-27b-it-qat-4bit",
            "omlx/qwen-3": "mlx-community/Qwen3-30B-A3B-4bit",
        }
        for model, expected_id in expected.items():
            info = PROVIDER_REGISTRY[model]
            assert info.api_model_id == expected_id

    def test_omlx_default_base_url(self):
        for model in OMLX_MODELS:
            info = PROVIDER_REGISTRY[model]
            assert info.api_base_url == "http://localhost:8080/v1"


# ---------------------------------------------------------------------------
# Failover chain tests
# ---------------------------------------------------------------------------

class TestOmlxFailoverChains:
    """Verify failover chains exist and are well-formed."""

    def test_all_omlx_models_have_failover_chains(self):
        for model in OMLX_MODELS:
            assert model in FAILOVER_CHAINS, f"{model} missing from FAILOVER_CHAINS"

    def test_failover_chains_not_empty(self):
        for model in OMLX_MODELS:
            chain = FAILOVER_CHAINS[model]
            assert len(chain) > 0, f"{model} has empty failover chain"

    def test_failover_chains_include_ollama(self):
        for model in OMLX_MODELS:
            chain = FAILOVER_CHAINS[model]
            assert "ollama" in chain, f"{model} failover should include ollama"

    def test_failover_chains_include_other_omlx(self):
        for model in OMLX_MODELS:
            chain = FAILOVER_CHAINS[model]
            other_omlx = [m for m in OMLX_MODELS if m != model]
            for alt in other_omlx:
                assert alt in chain, f"{model} failover should include {alt}"

    def test_failover_chain_no_self_reference(self):
        for model in OMLX_MODELS:
            chain = FAILOVER_CHAINS[model]
            assert model not in chain, f"{model} should not appear in its own failover chain"

    def test_failover_all_targets_exist_in_registry(self):
        for model in OMLX_MODELS:
            for alt in FAILOVER_CHAINS[model]:
                assert alt in PROVIDER_REGISTRY, f"Failover target {alt} not in registry"


# ---------------------------------------------------------------------------
# Dynamic URL resolution tests
# ---------------------------------------------------------------------------

class TestOmlxDynamicUrl:
    """Verify the base URL is resolved dynamically from settings."""

    def test_resolve_base_url_default(self):
        info = PROVIDER_REGISTRY["omlx/llama-4-scout"]
        url = resolve_base_url(info)
        assert url == "http://localhost:8080/v1"

    def test_resolve_base_url_custom(self):
        info = PROVIDER_REGISTRY["omlx/llama-4-scout"]
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.omlx_base_url = "http://192.168.1.100:9090"
            url = resolve_base_url(info)
            assert url == "http://192.168.1.100:9090/v1"

    def test_resolve_base_url_strips_trailing_slash(self):
        info = PROVIDER_REGISTRY["omlx/llama-4-scout"]
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.omlx_base_url = "http://localhost:8080/"
            url = resolve_base_url(info)
            assert url == "http://localhost:8080/v1"

    def test_resolve_base_url_non_omlx_unchanged(self):
        info = PROVIDER_REGISTRY["ollama"]
        url = resolve_base_url(info)
        assert url == "http://localhost:11434/v1"


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

class TestOmlxConfig:
    """Verify omlx_base_url in Settings."""

    def test_default_omlx_base_url(self):
        from sandcastle.config import Settings
        s = Settings(anthropic_api_key="test")
        assert s.omlx_base_url == "http://localhost:8080"

    def test_custom_omlx_base_url_from_env(self):
        from sandcastle.config import Settings
        env = {"OMLX_BASE_URL": "http://myserver:9999", "ANTHROPIC_API_KEY": "test"}
        with patch.dict(os.environ, env, clear=False):
            s = Settings()
            assert s.omlx_base_url == "http://myserver:9999"


# ---------------------------------------------------------------------------
# Generator config tests
# ---------------------------------------------------------------------------

class TestOmlxGeneratorConfig:
    """Verify oMLX in _PROVIDER_CONFIGS and ADVISOR_MODEL_TIERS."""

    def test_provider_configs_has_omlx(self):
        from sandcastle.engine.generator import _PROVIDER_CONFIGS
        assert "omlx" in _PROVIDER_CONFIGS

    def test_provider_configs_omlx_region(self):
        from sandcastle.engine.generator import _PROVIDER_CONFIGS
        assert _PROVIDER_CONFIGS["omlx"]["region"] == "local"

    def test_provider_configs_omlx_no_key(self):
        from sandcastle.engine.generator import _PROVIDER_CONFIGS
        assert _PROVIDER_CONFIGS["omlx"]["api_key_env"] == ""

    def test_provider_configs_omlx_dynamic_url(self):
        from sandcastle.engine.generator import _PROVIDER_CONFIGS
        url = _PROVIDER_CONFIGS["omlx"]["api_url"]
        assert "{omlx_base_url}" in url

    def test_advisor_model_tiers_has_omlx(self):
        from sandcastle.engine.generator import ADVISOR_MODEL_TIERS
        assert "omlx" in ADVISOR_MODEL_TIERS

    def test_advisor_model_tiers_omlx_structure(self):
        from sandcastle.engine.generator import ADVISOR_MODEL_TIERS
        tiers = ADVISOR_MODEL_TIERS["omlx"]
        assert "high" in tiers
        assert "medium" in tiers
        assert "low" in tiers

    def test_advisor_model_tiers_omlx_models(self):
        from sandcastle.engine.generator import ADVISOR_MODEL_TIERS
        tiers = ADVISOR_MODEL_TIERS["omlx"]
        assert "Llama-4-Scout" in tiers["high"]
        assert "Mistral-Small" in tiers["medium"]
        assert "gemma-3" in tiers["low"]


# ---------------------------------------------------------------------------
# Template tests
# ---------------------------------------------------------------------------

class TestOmlxTemplate:
    """Verify the omlx_research_tweet template is valid."""

    @pytest.fixture
    def template_path(self) -> Path:
        return Path(__file__).parent.parent / "src" / "sandcastle" / "templates" / "omlx_research_tweet.yaml"

    def test_template_exists(self, template_path: Path):
        assert template_path.exists(), f"Template not found at {template_path}"

    def test_template_parses_as_yaml(self, template_path: Path):
        data = yaml.safe_load(template_path.read_text())
        assert isinstance(data, dict)

    def test_template_has_required_fields(self, template_path: Path):
        data = yaml.safe_load(template_path.read_text())
        assert "name" in data
        assert "steps" in data
        assert "description" in data

    def test_template_uses_omlx_models(self, template_path: Path):
        data = yaml.safe_load(template_path.read_text())
        assert data["default_model"] == "omlx/llama-4-scout"
        models_used = {step.get("model") for step in data["steps"]}
        assert "omlx/llama-4-scout" in models_used
        assert "omlx/mistral-small" in models_used
        assert "omlx/gemma-3" in models_used

    def test_template_all_models_in_registry(self, template_path: Path):
        data = yaml.safe_load(template_path.read_text())
        all_models = {data.get("default_model")}
        for step in data["steps"]:
            if "model" in step:
                all_models.add(step["model"])
        for model in all_models:
            if model:
                assert model in PROVIDER_REGISTRY, f"Template model {model} not in registry"

    def test_template_has_input_schema(self, template_path: Path):
        data = yaml.safe_load(template_path.read_text())
        assert "input_schema" in data
        assert "topic" in data["input_schema"]["properties"]

    def test_template_steps_have_dependencies(self, template_path: Path):
        data = yaml.safe_load(template_path.read_text())
        steps = data["steps"]
        assert len(steps) == 3
        # First step has no dependencies
        assert "depends_on" not in steps[0]
        # Second depends on first
        assert "research" in steps[1]["depends_on"]
        # Third depends on second
        assert "analyze" in steps[2]["depends_on"]


# ---------------------------------------------------------------------------
# Provider health check (mock)
# ---------------------------------------------------------------------------

class TestOmlxHealthCheck:
    """Verify oMLX provider health check works with mock HTTP."""

    @pytest.mark.asyncio
    async def test_omlx_health_check_success(self):
        import httpx
        info = PROVIDER_REGISTRY["omlx/llama-4-scout"]
        base_url = resolve_base_url(info)
        mock_response = httpx.Response(200, json={"status": "ok"})
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{base_url}/models")
                assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_omlx_health_check_failure(self):
        import httpx
        info = PROVIDER_REGISTRY["omlx/llama-4-scout"]
        base_url = resolve_base_url(info)
        mock_response = httpx.Response(503, json={"error": "server down"})
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{base_url}/models")
                assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Resolve model tests
# ---------------------------------------------------------------------------

class TestOmlxResolveModel:
    """Verify resolve_model works for oMLX models."""

    def test_resolve_omlx_llama(self):
        info = resolve_model("omlx/llama-4-scout")
        assert info.provider == "omlx"
        assert "Llama-4-Scout" in info.api_model_id

    def test_resolve_omlx_mistral_small(self):
        info = resolve_model("omlx/mistral-small")
        assert info.provider == "omlx"
        assert "Mistral-Small" in info.api_model_id

    def test_resolve_omlx_gemma(self):
        info = resolve_model("omlx/gemma-3")
        assert info.provider == "omlx"
        assert "gemma-3" in info.api_model_id

    def test_resolve_omlx_qwen(self):
        info = resolve_model("omlx/qwen-3")
        assert info.provider == "omlx"
        assert "Qwen3" in info.api_model_id


# ---------------------------------------------------------------------------
# Failover manager with local providers
# ---------------------------------------------------------------------------

class TestOmlxFailoverManager:
    """Verify ProviderFailover handles local providers correctly."""

    def test_local_providers_available_without_keys(self):
        fo = ProviderFailover()
        status = fo.get_status()
        # oMLX and Ollama should appear in available (no key needed)
        for model in OMLX_MODELS + ["ollama"]:
            assert model in status["available_models"], (
                f"{model} should be available without API key"
            )

    def test_omlx_failover_returns_alternatives(self):
        fo = ProviderFailover()
        alts = fo.get_alternatives("omlx/llama-4-scout", data_residency="local")
        # Should include other oMLX models and ollama
        assert len(alts) > 0
        for alt in alts:
            info = PROVIDER_REGISTRY[alt]
            assert info.region == "local"
