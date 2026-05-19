"""Tests for the self-hosted sandbox runtime (v0.32.1).

Covers:
- Config dataclass + provider enum
- Org-key leak guard (the most important safety contract)
- Memory Stores incompatibility hard-error
- AWS region warning (soft)
- Environment key format validation
- Session-create payload augmentation
"""

from __future__ import annotations

import logging

import pytest

from sandcastle.engine.self_hosted_sandbox import (
    EnvironmentKeyFormatError,
    MemoryStoresIncompatibleError,
    OrgKeyLeakError,
    SandboxProvider,
    SelfHostedSandboxConfig,
    SelfHostedSandboxError,
    assert_env_key_format,
    assert_memory_compatible,
    assert_org_key_not_set,
    build_environment_block,
    prepare_session_payload,
    warn_on_aws_region,
)


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


class TestSandboxConfig:
    def test_minimal_config(self):
        cfg = SelfHostedSandboxConfig(environment_id="env_abc123")
        assert cfg.environment_id == "env_abc123"
        assert cfg.provider == SandboxProvider.DOCKER
        assert cfg.environment_key_env == "ANTHROPIC_ENVIRONMENT_KEY"
        assert cfg.max_concurrent_sessions == 8

    def test_provider_enum_values(self):
        # The four blog-named providers + docker fallback for local dev.
        values = {p.value for p in SandboxProvider}
        assert {"cloudflare", "daytona", "modal", "vercel", "docker"} == values

    def test_custom_metadata_round_trips(self):
        cfg = SelfHostedSandboxConfig(
            environment_id="env_xyz",
            provider=SandboxProvider.MODAL,
            metadata={"region": "eu-west-1", "billing_tag": "research"},
        )
        block = build_environment_block(cfg)
        assert block["metadata"]["region"] == "eu-west-1"
        assert block["metadata"]["billing_tag"] == "research"


# ---------------------------------------------------------------------------
# Org-key leak guard
# ---------------------------------------------------------------------------


class TestOrgKeyLeakGuard:
    def test_org_key_set_raises(self):
        env = {"ANTHROPIC_API_KEY": "sk-ant-api03-xxxxxxxx"}
        with pytest.raises(OrgKeyLeakError) as exc:
            assert_org_key_not_set(env)
        assert "sk-ant-oat01-" in str(exc.value)

    def test_org_key_absent_passes(self):
        # Worker is correctly configured with only the env-scoped key.
        env = {"ANTHROPIC_ENVIRONMENT_KEY": "sk-ant-oat01-abc"}
        assert_org_key_not_set(env)  # Must not raise.

    def test_org_key_empty_string_passes(self):
        # Empty env var should not trip the guard - common in CI matrices.
        env = {"ANTHROPIC_API_KEY": ""}
        assert_org_key_not_set(env)

    def test_org_key_warn_only_mode(self, caplog):
        env = {"ANTHROPIC_API_KEY": "sk-ant-api03-leaky"}
        with caplog.at_level(logging.WARNING):
            assert_org_key_not_set(env, raise_on_violation=False)
        assert any("ANTHROPIC_API_KEY" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Memory Stores incompatibility
# ---------------------------------------------------------------------------


class TestMemoryIncompat:
    def test_memory_plus_self_hosted_raises(self):
        cfg = SelfHostedSandboxConfig(environment_id="env_x")
        with pytest.raises(MemoryStoresIncompatibleError) as exc:
            assert_memory_compatible(cfg, memory_stores=["ms_abc"])
        assert "Memory Stores are not supported" in str(exc.value)
        assert "self-hosted-sandboxes#limitations" in str(exc.value)

    def test_no_sandbox_config_no_op(self):
        # No self-hosted sandbox in play -> memory_stores is fine.
        assert_memory_compatible(None, memory_stores=["ms_abc"])

    def test_self_hosted_without_memory_passes(self):
        cfg = SelfHostedSandboxConfig(environment_id="env_x")
        assert_memory_compatible(cfg, memory_stores=None)
        assert_memory_compatible(cfg, memory_stores=[])


# ---------------------------------------------------------------------------
# Environment key format
# ---------------------------------------------------------------------------


class TestEnvKeyFormat:
    def test_valid_env_key_passes(self):
        assert_env_key_format("sk-ant-oat01-AAAAAAAAAAAAAAAAAAAAAAAA")

    def test_org_key_in_env_key_slot_rejected(self):
        # Catches the "pasted my org key by mistake" foot-gun.
        with pytest.raises(EnvironmentKeyFormatError) as exc:
            assert_env_key_format("sk-ant-api03-BBBBBBBBBBBBBBBBBBBBBBBB")
        assert "sk-ant-oat01-" in str(exc.value)

    def test_empty_key_rejected(self):
        with pytest.raises(EnvironmentKeyFormatError):
            assert_env_key_format("")

    def test_random_string_rejected(self):
        with pytest.raises(EnvironmentKeyFormatError):
            assert_env_key_format("hunter2")


# ---------------------------------------------------------------------------
# AWS region warning
# ---------------------------------------------------------------------------


class TestAWSWarning:
    def test_aws_region_triggers_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            offender = warn_on_aws_region({"ANTHROPIC_REGION": "aws-us-east-1"})
        assert offender == "aws-us-east-1"
        assert any("NOT supported" in r.message for r in caplog.records)

    def test_us_prefix_triggers_warning(self, caplog):
        # AWS-style region naming (us-east-1) should also trip the guard.
        with caplog.at_level(logging.WARNING):
            offender = warn_on_aws_region({"AWS_REGION": "us-west-2"})
        assert offender == "us-west-2"

    def test_clean_env_no_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            offender = warn_on_aws_region({})
        assert offender is None

    def test_non_aws_region_passes(self):
        # Anthropic EU residency endpoint would set this; should NOT warn.
        offender = warn_on_aws_region({"ANTHROPIC_REGION": "eu-de"})
        assert offender is None


# ---------------------------------------------------------------------------
# Environment block builder
# ---------------------------------------------------------------------------


class TestEnvironmentBlock:
    def test_block_contains_required_keys(self):
        cfg = SelfHostedSandboxConfig(environment_id="env_42")
        block = build_environment_block(cfg)
        assert block["environment_id"] == "env_42"
        assert "metadata" in block

    def test_provider_lands_in_metadata(self):
        cfg = SelfHostedSandboxConfig(
            environment_id="env_42",
            provider=SandboxProvider.VERCEL,
        )
        block = build_environment_block(cfg)
        assert block["metadata"]["provider"] == "vercel"

    def test_user_metadata_takes_precedence_when_keys_overlap(self):
        cfg = SelfHostedSandboxConfig(
            environment_id="env_x",
            provider=SandboxProvider.MODAL,
            metadata={"provider": "override-by-user"},
        )
        block = build_environment_block(cfg)
        # User-provided metadata overrides the auto-injected provider key.
        assert block["metadata"]["provider"] == "override-by-user"


# ---------------------------------------------------------------------------
# prepare_session_payload integration
# ---------------------------------------------------------------------------


class TestPrepareSessionPayload:
    def test_happy_path(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_REGION", raising=False)
        monkeypatch.delenv("AWS_REGION", raising=False)
        cfg = SelfHostedSandboxConfig(environment_id="env_x")
        out = prepare_session_payload({"agent_id": "agent_42"}, cfg)
        assert out["agent_id"] == "agent_42"
        assert out["environment"]["environment_id"] == "env_x"

    def test_org_key_blocks(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-leak")
        cfg = SelfHostedSandboxConfig(environment_id="env_x")
        with pytest.raises(OrgKeyLeakError):
            prepare_session_payload({}, cfg)

    def test_memory_combo_blocks(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        cfg = SelfHostedSandboxConfig(environment_id="env_x")
        with pytest.raises(MemoryStoresIncompatibleError):
            prepare_session_payload({}, cfg, memory_stores=["ms_abc"])

    def test_base_payload_not_mutated(self, monkeypatch):
        # Defensive: callers can re-use the base payload.
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        base = {"agent_id": "agent_x"}
        cfg = SelfHostedSandboxConfig(environment_id="env_x")
        out = prepare_session_payload(base, cfg)
        assert "environment" not in base  # Untouched.
        assert "environment" in out


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


def test_all_errors_subclass_base():
    assert issubclass(OrgKeyLeakError, SelfHostedSandboxError)
    assert issubclass(MemoryStoresIncompatibleError, SelfHostedSandboxError)
    assert issubclass(EnvironmentKeyFormatError, SelfHostedSandboxError)
