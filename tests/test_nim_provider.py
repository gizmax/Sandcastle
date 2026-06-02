"""Tests for the NVIDIA NIM provider backend (local OpenAI-compatible inference)."""

from __future__ import annotations

import pytest

from sandcastle.config import settings
from sandcastle.engine.providers import (
    FAILOVER_CHAINS,
    PROVIDER_REGISTRY,
    get_api_key,
    is_known_model,
    resolve_base_url,
    resolve_model,
)


def test_static_nim_entry_is_local_and_free():
    m = resolve_model("nim/llama-3.1-70b")
    assert m.provider == "nim"
    assert m.api_model_id == "meta/llama-3.1-70b-instruct"
    assert m.region == "local"
    assert m.input_price_per_m == 0.0 and m.output_price_per_m == 0.0
    assert m.runner == "runner-openai.mjs"


def test_dynamic_nim_model_resolves():
    # NIM serves any model; "nim/<id>" (id may contain slashes) resolves dynamically.
    m = resolve_model("nim/meta/llama-3.3-70b-instruct")
    assert m.provider == "nim"
    assert m.api_model_id == "meta/llama-3.3-70b-instruct"
    assert m.region == "local"
    assert m.api_key_env == "NIM_API_KEY"


def test_empty_nim_id_raises():
    with pytest.raises(KeyError):
        resolve_model("nim/")


def test_unknown_non_nim_model_raises():
    with pytest.raises(KeyError):
        resolve_model("totally-bogus-model")


def test_resolve_base_url_reads_settings(monkeypatch):
    monkeypatch.setattr(settings, "nim_base_url", "http://spark.local:8000")
    assert resolve_base_url(resolve_model("nim/llama-3.1-70b")) == "http://spark.local:8000/v1"
    # dynamic models use the same base URL
    assert resolve_base_url(resolve_model("nim/custom/model")) == "http://spark.local:8000/v1"


def test_get_api_key_prefers_env_then_settings(monkeypatch):
    m = resolve_model("nim/llama-3.1-8b")
    # env wins
    monkeypatch.setenv("NIM_API_KEY", "nvapi-from-env")
    assert get_api_key(m) == "nvapi-from-env"
    # settings fallback when env empty (local NIMs may leave it blank)
    monkeypatch.delenv("NIM_API_KEY", raising=False)
    monkeypatch.setattr(settings, "nim_api_key", "nvapi-from-settings")
    assert get_api_key(m) == "nvapi-from-settings"


@pytest.mark.parametrize(
    "model,expected",
    [
        ("nim/llama-3.1-70b", True),  # static
        ("nim/anything/at-all", True),  # dynamic
        ("nim/", False),  # empty id
        ("sonnet", True),  # registry
        ("bogus", False),
    ],
)
def test_is_known_model(model, expected):
    assert is_known_model(model) is expected


def test_nim_failover_chains_present():
    assert "nim/llama-3.1-70b" in FAILOVER_CHAINS
    assert "ollama" in FAILOVER_CHAINS["nim/llama-3.1-70b"]
    assert sum(1 for k in PROVIDER_REGISTRY if k.startswith("nim/")) >= 4


def test_dag_validate_accepts_dynamic_nim_model():
    from sandcastle.engine.dag import parse_yaml_string, validate

    wf = parse_yaml_string(
        "name: nim-wf\n"
        "default_model: nim/meta/llama-3.3-70b-instruct\n"
        "steps:\n"
        "  - id: a\n"
        "    prompt: hello\n"
    )
    errors = validate(wf)
    assert not any("Unknown model" in e for e in errors), errors
