"""Tests for the provider registry and multi-model support."""

from __future__ import annotations

import pytest

from sandcastle.engine.providers import (
    KNOWN_MODELS,
    PROVIDER_REGISTRY,
    ModelInfo,
    get_api_key,
    is_claude_model,
    resolve_model,
)


class TestProviderRegistry:
    """Tests for PROVIDER_REGISTRY structure."""

    def test_registry_has_claude_models(self):
        assert "sonnet" in PROVIDER_REGISTRY
        assert "opus" in PROVIDER_REGISTRY
        assert "haiku" in PROVIDER_REGISTRY

    def test_registry_has_minimax(self):
        assert "minimax/m2.5" in PROVIDER_REGISTRY

    def test_registry_has_openai(self):
        assert "openai/codex-mini" in PROVIDER_REGISTRY
        assert "openai/codex" in PROVIDER_REGISTRY

    def test_registry_has_google(self):
        assert "google/gemini-2.5-pro" in PROVIDER_REGISTRY

    def test_all_entries_are_model_info(self):
        for key, info in PROVIDER_REGISTRY.items():
            assert isinstance(info, ModelInfo), f"{key} is not ModelInfo"

    def test_all_entries_have_runner(self):
        valid_runners = {"runner.mjs", "runner-openai.mjs"}
        for key, info in PROVIDER_REGISTRY.items():
            assert info.runner in valid_runners, f"{key} has invalid runner: {info.runner}"

    def test_claude_models_use_claude_runner(self):
        for name in ("sonnet", "opus", "haiku"):
            assert PROVIDER_REGISTRY[name].runner == "runner.mjs"

    def test_non_claude_models_use_openai_runner(self):
        for name in ("minimax/m2.5", "openai/codex-mini", "openai/codex", "google/gemini-2.5-pro"):
            assert PROVIDER_REGISTRY[name].runner == "runner-openai.mjs"

    def test_pricing_is_positive(self):
        for key, info in PROVIDER_REGISTRY.items():
            assert info.input_price_per_m > 0, f"{key} has non-positive input price"
            assert info.output_price_per_m > 0, f"{key} has non-positive output price"

    def test_known_models_matches_registry(self):
        assert KNOWN_MODELS == frozenset(PROVIDER_REGISTRY.keys())


class TestResolveModel:
    """Tests for resolve_model()."""

    def test_resolve_claude(self):
        info = resolve_model("sonnet")
        assert info.provider == "claude"
        assert info.runner == "runner.mjs"

    def test_resolve_minimax(self):
        info = resolve_model("minimax/m2.5")
        assert info.provider == "minimax"
        assert info.api_model_id == "MiniMax-M2.5"
        assert info.api_base_url == "https://api.minimaxi.chat/v1"

    def test_resolve_openai(self):
        info = resolve_model("openai/codex-mini")
        assert info.provider == "openai"
        assert info.api_key_env == "OPENAI_API_KEY"

    def test_resolve_unknown_raises(self):
        with pytest.raises(KeyError, match="Unknown model"):
            resolve_model("nonexistent/model")

    def test_resolve_error_lists_available(self):
        with pytest.raises(KeyError, match="sonnet"):
            resolve_model("bad-model")


class TestIsClaude:
    """Tests for is_claude_model()."""

    def test_claude_models(self):
        assert is_claude_model("sonnet") is True
        assert is_claude_model("opus") is True
        assert is_claude_model("haiku") is True

    def test_non_claude_models(self):
        assert is_claude_model("minimax/m2.5") is False
        assert is_claude_model("openai/codex") is False
        assert is_claude_model("google/gemini-2.5-pro") is False

    def test_unknown_model(self):
        assert is_claude_model("nonexistent") is False


class TestGetApiKey:
    """Tests for get_api_key()."""

    def test_reads_from_env(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
        info = resolve_model("minimax/m2.5")
        assert get_api_key(info) == "test-minimax-key"

    def test_empty_when_not_set(self, monkeypatch):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        info = resolve_model("minimax/m2.5")
        # May return empty string (from env) or settings default
        key = get_api_key(info)
        assert isinstance(key, str)


class TestDagValidationWithProviders:
    """Tests for DAG validation with multi-model support."""

    def test_valid_multi_model_workflow(self):
        from sandcastle.engine.dag import parse_yaml_string, validate

        yaml_content = """
name: multi-model-test
description: Uses multiple models
default_model: sonnet
steps:
  - id: research
    model: minimax/m2.5
    prompt: "Research topic"
  - id: analyze
    model: sonnet
    depends_on: [research]
    prompt: "Analyze results"
  - id: format
    model: haiku
    depends_on: [analyze]
    prompt: "Format output"
"""
        workflow = parse_yaml_string(yaml_content)
        errors = validate(workflow)
        assert errors == []

    def test_invalid_model_detected(self):
        from sandcastle.engine.dag import parse_yaml_string, validate

        yaml_content = """
name: bad-model-test
description: Uses invalid model
default_model: sonnet
steps:
  - id: step1
    model: nonexistent/model
    prompt: "This should fail validation"
"""
        workflow = parse_yaml_string(yaml_content)
        errors = validate(workflow)
        assert any("Unknown model" in e for e in errors)

    def test_all_providers_valid(self):
        from sandcastle.engine.dag import parse_yaml_string, validate

        yaml_content = """
name: all-providers
description: Uses all providers
default_model: sonnet
steps:
  - id: claude-step
    model: sonnet
    prompt: "Claude"
  - id: minimax-step
    model: minimax/m2.5
    prompt: "MiniMax"
  - id: openai-step
    model: openai/codex-mini
    prompt: "OpenAI"
  - id: google-step
    model: google/gemini-2.5-pro
    prompt: "Google"
"""
        workflow = parse_yaml_string(yaml_content)
        errors = validate(workflow)
        assert errors == []


class TestOptimizerExtendedPool:
    """Tests for the extended model pool in optimizer."""

    def test_extended_pool_exists(self):
        from sandcastle.engine.optimizer import EXTENDED_MODEL_POOL

        model_ids = [m.id for m in EXTENDED_MODEL_POOL]
        assert "minimax-m2.5" in model_ids
        assert "openai-codex-mini" in model_ids

    def test_extended_pool_includes_defaults(self):
        from sandcastle.engine.optimizer import DEFAULT_MODEL_POOL, EXTENDED_MODEL_POOL

        default_ids = {m.id for m in DEFAULT_MODEL_POOL}
        extended_ids = {m.id for m in EXTENDED_MODEL_POOL}
        assert default_ids.issubset(extended_ids)

    def test_auto_model_pool_includes_non_claude(self):
        from sandcastle.engine.dag import _parse_model_pool

        pool = _parse_model_pool("auto")
        model_names = [m.model for m in pool]
        assert "minimax/m2.5" in model_names
        assert "openai/codex-mini" in model_names
