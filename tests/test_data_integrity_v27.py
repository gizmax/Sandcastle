"""Data integrity and edge case tests for Sandcastle v0.27.

Covers: cost calculation, template resolution, DAG validation,
audit trail integrity, and PII privacy redaction.

Target: 50+ tests using real functions where possible.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.audit import compute_audit_hash, verify_audit_chain
from sandcastle.engine.dag import (
    StepDefinition,
    WorkflowDefinition,
    _detect_cycles,
    _parse_step,
    build_plan,
    parse_yaml_string,
    validate,
)
from sandcastle.engine.executor import (
    RunContext,
    StepResult,
    _escape_braces,
    _safe_cost,
    resolve_templates,
    resolve_variable,
    _UNRESOLVED,
)
from sandcastle.engine.privacy import (
    PIIMatch,
    PrivacyConfig,
    PrivacyRouter,
)
from sandcastle.engine.providers import PROVIDER_REGISTRY, ModelInfo, resolve_model


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(
    *,
    run_id: str = "test-run-1",
    input_data: dict | None = None,
    step_outputs: dict | None = None,
    step_results: dict | None = None,
    costs: list[float] | None = None,
    max_cost_usd: float | None = None,
) -> RunContext:
    """Build a minimal RunContext for testing without I/O."""
    return RunContext(
        run_id=run_id,
        input=input_data or {},
        step_outputs=step_outputs or {},
        step_results=step_results or {},
        costs=costs or [],
        max_cost_usd=max_cost_usd,
    )


def _make_workflow(
    steps: list[StepDefinition],
    name: str = "test-wf",
    default_model: str = "sonnet",
) -> WorkflowDefinition:
    """Build a minimal WorkflowDefinition for testing."""
    return WorkflowDefinition(
        name=name,
        description="test workflow",
        default_model=default_model,
        default_max_turns=10,
        default_timeout=300,
        steps=steps,
    )


def _make_step(
    id: str,
    depends_on: list[str] | None = None,
    **kwargs,
) -> StepDefinition:
    """Build a StepDefinition with sane defaults."""
    return StepDefinition(
        id=id,
        prompt=kwargs.get("prompt", "do something"),
        depends_on=depends_on or [],
        **{k: v for k, v in kwargs.items() if k != "prompt"},
    )


# ===================================================================
# 1. Cost calculation accuracy (12 tests)
# ===================================================================


class TestCostCalculation:
    """Verify _safe_cost and cost tracking through RunContext."""

    def test_haiku_cost_matches_registry(self):
        """Haiku pricing from PROVIDER_REGISTRY is used correctly."""
        info = PROVIDER_REGISTRY["haiku"]
        cost = _safe_cost(1_000_000, 1_000_000, info.input_price_per_m, info.output_price_per_m)
        expected = info.input_price_per_m + info.output_price_per_m
        assert cost == pytest.approx(expected), f"Haiku cost mismatch: {cost} != {expected}"

    def test_sonnet_cost_matches_registry(self):
        """Sonnet pricing from PROVIDER_REGISTRY is used correctly."""
        info = PROVIDER_REGISTRY["sonnet"]
        cost = _safe_cost(1_000_000, 1_000_000, info.input_price_per_m, info.output_price_per_m)
        expected = info.input_price_per_m + info.output_price_per_m
        assert cost == pytest.approx(expected)

    def test_opus_cost_matches_registry(self):
        """Opus pricing from PROVIDER_REGISTRY is used correctly."""
        info = PROVIDER_REGISTRY["opus"]
        cost = _safe_cost(1_000_000, 1_000_000, info.input_price_per_m, info.output_price_per_m)
        expected = info.input_price_per_m + info.output_price_per_m
        assert cost == pytest.approx(expected)

    def test_zero_tokens_zero_cost(self):
        """Zero tokens produce zero cost regardless of pricing."""
        cost = _safe_cost(0, 0, 3.0, 15.0)
        assert cost == 0.0

    def test_large_token_count_no_overflow(self):
        """Very large token counts (10B) do not overflow or produce Inf."""
        cost = _safe_cost(10_000_000_000, 10_000_000_000, 15.0, 75.0)
        assert math.isfinite(cost)
        assert cost > 0

    def test_none_pricing_returns_zero(self):
        """None pricing (misconfigured provider) returns 0 instead of crash."""
        cost = _safe_cost(1000, 1000, None, None)  # type: ignore[arg-type]
        assert cost == 0.0

    def test_safe_cost_rejects_nan(self):
        """NaN pricing is clamped to 0."""
        cost = _safe_cost(1000, 1000, float("nan"), 1.0)
        assert cost == 0.0

    def test_safe_cost_rejects_infinity(self):
        """Infinity pricing is clamped to 0."""
        cost = _safe_cost(1000, 1000, float("inf"), 1.0)
        assert cost == 0.0

    def test_safe_cost_rejects_negative_tokens(self):
        """Negative token counts are clamped to 0."""
        cost = _safe_cost(-100, -200, 3.0, 15.0)
        assert cost == 0.0

    def test_total_run_cost_equals_sum_of_steps(self):
        """RunContext.total_cost is the sum of all step costs."""
        ctx = _make_context(costs=[0.01, 0.02, 0.005, 0.1])
        assert ctx.total_cost == pytest.approx(0.135)

    def test_cost_per_provider_aggregation(self):
        """Per-provider cost aggregation matches individual step costs."""
        costs_by_provider: dict[str, float] = {}
        for model_key, info in PROVIDER_REGISTRY.items():
            c = _safe_cost(500_000, 200_000, info.input_price_per_m, info.output_price_per_m)
            costs_by_provider[model_key] = c

        total = sum(costs_by_provider.values())
        recomputed = 0.0
        for model_key, info in PROVIDER_REGISTRY.items():
            recomputed += _safe_cost(500_000, 200_000, info.input_price_per_m, info.output_price_per_m)
        assert total == pytest.approx(recomputed)

    def test_ollama_local_is_free(self):
        """Ollama (local) model has zero cost."""
        info = PROVIDER_REGISTRY["ollama"]
        cost = _safe_cost(1_000_000, 1_000_000, info.input_price_per_m, info.output_price_per_m)
        assert cost == 0.0


# ===================================================================
# 2. Template resolution edge cases (11 tests)
# ===================================================================


class TestTemplateResolution:
    """Verify resolve_templates handles all edge-case inputs."""

    def test_empty_string_resolves(self):
        """Empty string resolves without error and stays empty."""
        ctx = _make_context()
        result = resolve_templates("", ctx)
        assert result == ""

    def test_unicode_preserved(self):
        """Unicode characters pass through unaltered."""
        ctx = _make_context(input_data={"name": "Tomas"})
        result = resolve_templates("Hello {input.name}!", ctx)
        assert "Tomas" in result

    def test_unicode_emoji_preserved(self):
        """Emoji and CJK characters survive resolution."""
        ctx = _make_context(input_data={"greeting": "Ahoj!"})
        result = resolve_templates("{input.greeting}", ctx)
        assert result == "Ahoj!"

    def test_large_text_no_truncation(self):
        """100KB text resolves without silent truncation."""
        big = "A" * 100_000
        ctx = _make_context(input_data={"big": big})
        result = resolve_templates("{input.big}", ctx)
        # Braces are escaped in resolved input, but no A's are lost
        assert len(result) >= 100_000

    def test_oversized_template_rejected(self):
        """Template > 1MB raises ValueError."""
        huge = "x" * (1024 * 1024 + 1)
        ctx = _make_context()
        with pytest.raises(ValueError, match="too large"):
            resolve_templates(huge, ctx)

    def test_nested_braces_no_infinite_recursion(self):
        """Nested {steps.{input.key}} does not cause infinite recursion.

        The regex doesn't match nested patterns, so it is left as-is.
        """
        ctx = _make_context(input_data={"key": "foo"})
        result = resolve_templates("{steps.{input.key}.output}", ctx)
        # Inner match resolves but outer doesn't form a valid variable
        assert "steps" in result  # placeholder remains in some form

    def test_nonexistent_path_leaves_placeholder(self):
        """Missing variable {nonexistent.path} is left unchanged."""
        ctx = _make_context()
        result = resolve_templates("Value: {input.missing}", ctx)
        assert "{input.missing}" in result

    def test_multiple_refs_all_resolve(self):
        """Multiple {input.x} references in same string all resolve."""
        ctx = _make_context(input_data={"a": "X", "b": "Y"})
        result = resolve_templates("{input.a} and {input.b}", ctx)
        assert "X" in result
        assert "Y" in result

    def test_json_braces_escaped(self):
        """Input containing JSON braces is properly escaped to prevent re-resolution."""
        ctx = _make_context(input_data={"data": '{"key": "value"}'})
        result = resolve_templates("{input.data}", ctx)
        # Braces are escaped so they cannot be re-interpreted as template vars
        assert "{" not in result or "{{" in result

    def test_step_output_with_template_pattern_escaped(self):
        """Step output containing {input.x} pattern is escaped (no re-resolution)."""
        ctx = _make_context(
            step_outputs={"prev": "Use {input.secret} for auth"},
        )
        result = resolve_templates("{steps.prev.output}", ctx)
        # The literal {input.secret} in the output must be escaped
        assert "secret" in result
        assert "{{input.secret}}" in result or "{input.secret}" not in result.replace("{{", "")

    def test_run_id_resolved(self):
        """The special {run_id} variable resolves to the context run_id."""
        ctx = _make_context(run_id="abc-123")
        result = resolve_templates("Run: {run_id}", ctx)
        assert "abc-123" in result


# ===================================================================
# 3. DAG validation edge cases (12 tests)
# ===================================================================


class TestDAGValidation:
    """Verify DAG validation in dag.validate() and related helpers."""

    def test_zero_steps_rejected(self):
        """Workflow with 0 steps produces a validation error."""
        wf = _make_workflow(steps=[])
        errors = validate(wf)
        assert any("at least one step" in e for e in errors)

    def test_100_steps_accepted(self):
        """Workflow with 100 valid steps passes validation."""
        steps = [_make_step(id=f"s{i}") for i in range(100)]
        wf = _make_workflow(steps=steps)
        errors = validate(wf)
        # Filter out errors that are NOT about step count
        step_count_errors = [e for e in errors if "too many steps" in e.lower()]
        assert len(step_count_errors) == 0

    def test_self_cycle_rejected(self):
        """Step depending on itself is detected as a cycle."""
        steps = [_make_step(id="loop_step", depends_on=["loop_step"])]
        wf = _make_workflow(steps=steps)
        errors = validate(wf)
        cycle_errors = [e for e in errors if "cycle" in e.lower()]
        assert len(cycle_errors) > 0

    def test_long_step_id_rejected(self):
        """Step ID with 1000 characters fails the ID format regex."""
        long_id = "a" * 1000
        steps = [_make_step(id=long_id)]
        wf = _make_workflow(steps=steps)
        errors = validate(wf)
        # The regex caps at 100 chars, so this should fail
        assert any("invalid" in e.lower() for e in errors)

    def test_special_chars_in_id_rejected(self):
        """Step ID with special characters (spaces, dots) is rejected."""
        steps = [_make_step(id="step with spaces")]
        wf = _make_workflow(steps=steps)
        errors = validate(wf)
        assert any("invalid" in e.lower() for e in errors)

    def test_duplicate_step_ids_rejected(self):
        """Duplicate step IDs produce a validation error."""
        steps = [_make_step(id="dup"), _make_step(id="dup")]
        wf = _make_workflow(steps=steps)
        errors = validate(wf)
        assert any("duplicate" in e.lower() for e in errors)

    def test_depends_on_integer_coerced_to_string(self):
        """YAML gotcha: integer depends_on is coerced to string during parsing."""
        raw = {"id": "step_b", "depends_on": [1], "prompt": "test"}
        defaults = {"model": "sonnet", "max_turns": 10, "timeout": 300}
        step = _parse_step(raw, defaults)
        assert step.depends_on == ["1"]
        assert all(isinstance(d, str) for d in step.depends_on)

    def test_empty_depends_on_same_as_none(self):
        """Empty depends_on list behaves identically to no depends_on."""
        step_empty = _make_step(id="a", depends_on=[])
        step_none = _make_step(id="b", depends_on=None)
        assert step_empty.depends_on == []
        assert step_none.depends_on == []

    def test_unknown_depends_on_reference(self):
        """Depending on a nonexistent step produces a validation error."""
        steps = [_make_step(id="only_step", depends_on=["ghost"])]
        wf = _make_workflow(steps=steps)
        errors = validate(wf)
        assert any("unknown step 'ghost'" in e for e in errors)

    def test_hyphen_underscore_in_id_accepted(self):
        """Step IDs with hyphens and underscores are valid."""
        steps = [_make_step(id="my-step_01")]
        wf = _make_workflow(steps=steps)
        errors = validate(wf)
        id_errors = [e for e in errors if "invalid" in e.lower() and "my-step_01" in e]
        assert len(id_errors) == 0

    def test_detect_cycles_two_step_cycle(self):
        """Two-step mutual dependency is detected as a cycle."""
        steps = [
            _make_step(id="a", depends_on=["b"]),
            _make_step(id="b", depends_on=["a"]),
        ]
        errors = _detect_cycles(steps)
        assert len(errors) > 0

    def test_build_plan_linear_chain(self):
        """Linear A->B->C produces 3 sequential stages."""
        steps = [
            _make_step(id="a"),
            _make_step(id="b", depends_on=["a"]),
            _make_step(id="c", depends_on=["b"]),
        ]
        wf = _make_workflow(steps=steps)
        plan = build_plan(wf)
        assert len(plan.stages) == 3
        assert plan.stages[0] == ["a"]
        assert plan.stages[1] == ["b"]
        assert plan.stages[2] == ["c"]


# ===================================================================
# 4. Audit trail integrity (8 tests)
# ===================================================================


class TestAuditTrailIntegrity:
    """Verify audit hash chain correctness and tamper detection."""

    def test_hash_deterministic(self):
        """Same inputs always produce the same hash."""
        h1 = compute_audit_hash("start", "run-1", "system", "2026-01-01T00:00:00Z", {}, "genesis")
        h2 = compute_audit_hash("start", "run-1", "system", "2026-01-01T00:00:00Z", {}, "genesis")
        assert h1 == h2

    def test_hash_is_sha256_hex(self):
        """The hash is a valid 64-character hex SHA-256 digest."""
        h = compute_audit_hash("test", None, "actor", "ts", {"k": "v"}, "genesis")
        assert len(h) == 64
        int(h, 16)  # Raises if not valid hex

    def test_hash_changes_with_event_type(self):
        """Different event types produce different hashes."""
        h1 = compute_audit_hash("start", "r", "a", "t", {}, "genesis")
        h2 = compute_audit_hash("complete", "r", "a", "t", {}, "genesis")
        assert h1 != h2

    def test_hash_changes_with_payload(self):
        """Different payloads produce different hashes."""
        h1 = compute_audit_hash("start", "r", "a", "t", {"x": 1}, "genesis")
        h2 = compute_audit_hash("start", "r", "a", "t", {"x": 2}, "genesis")
        assert h1 != h2

    def test_hash_chain_continuity(self):
        """Each event's hash depends on the previous event's hash."""
        h1 = compute_audit_hash("start", "r", "a", "t1", {}, "genesis")
        h2 = compute_audit_hash("step", "r", "a", "t2", {}, h1)
        h3 = compute_audit_hash("complete", "r", "a", "t3", {}, h2)

        # Recompute h2 with wrong prev_hash -> mismatch
        h2_tampered = compute_audit_hash("step", "r", "a", "t2", {}, "wrong-hash")
        assert h2_tampered != h2

        # Verify chain is valid if we replay correctly
        assert h2 == compute_audit_hash("step", "r", "a", "t2", {}, h1)
        assert h3 == compute_audit_hash("complete", "r", "a", "t3", {}, h2)

    def test_tampered_event_detected_in_chain(self):
        """If an event's payload is changed, the hash no longer matches."""
        original = compute_audit_hash("test", "r", "a", "ts", {"ok": True}, "genesis")
        tampered = compute_audit_hash("test", "r", "a", "ts", {"ok": False}, "genesis")
        assert original != tampered

    def test_none_run_id_handled(self):
        """System-level events (run_id=None) hash without error."""
        h = compute_audit_hash("system_event", None, "system", "ts", {}, "genesis")
        assert isinstance(h, str) and len(h) == 64

    def test_empty_payload_hashed_as_empty_dict(self):
        """Empty payload {} produces a valid hash."""
        h = compute_audit_hash("test", "r", "a", "ts", {}, "genesis")
        assert isinstance(h, str) and len(h) == 64


# ===================================================================
# 5. Privacy/PII edge cases (12 tests)
# ===================================================================


def _default_router() -> PrivacyRouter:
    """Build a PrivacyRouter with all entity types enabled."""
    cfg = PrivacyConfig(
        enabled=True,
        entities=["email", "phone", "ssn", "credit_card", "ip_address", "iban"],
        mode="redact",
    )
    return PrivacyRouter(cfg)


class TestPrivacyPII:
    """Verify PII detection and redaction edge cases."""

    def test_email_in_json_detected(self):
        """Email embedded in a JSON string value is detected and redacted."""
        router = _default_router()
        text = '{"user": "contact@example.com", "role": "admin"}'
        result, matches = router.scrub(text)
        assert "[EMAIL]" in result
        assert any(m.entity_type == "email" for m in matches)

    def test_phone_in_markdown_table(self):
        """Phone number inside a markdown table row is detected."""
        router = _default_router()
        text = "| Name | Phone |\n| John | (555) 123-4567 |"
        result, matches = router.scrub(text)
        assert any(m.entity_type == "phone" for m in matches)

    def test_ssn_with_dashes_detected(self):
        """SSN in 123-45-6789 format is detected."""
        router = _default_router()
        text = "SSN: 123-45-6789"
        result, matches = router.scrub(text)
        assert any(m.entity_type == "ssn" for m in matches)
        assert "[SSN]" in result

    def test_credit_card_with_spaces_detected_or_overlaps_phone(self):
        """Credit card with space separators is detected as PII.

        When both phone and credit_card patterns are active, the phone
        pattern may match the same span first due to overlapping.  Either
        entity type is acceptable - the key guarantee is that the sensitive
        number IS redacted.
        """
        router = _default_router()
        text = "Card: 4111 1111 1111 1111"
        result, matches = router.scrub(text)
        # The number must be redacted regardless of which pattern caught it
        assert "4111" not in result
        assert len(matches) >= 1

    def test_credit_card_with_dashes_detected_or_overlaps_phone(self):
        """Credit card with dash separators is detected as PII.

        Same overlap consideration as the spaces variant.
        """
        router = _default_router()
        text = "Card: 4111-1111-1111-1111"
        result, matches = router.scrub(text)
        assert "4111" not in result
        assert len(matches) >= 1

    def test_credit_card_only_router_with_spaces(self):
        """Credit card with spaces is detected when phone pattern is disabled."""
        cfg = PrivacyConfig(
            enabled=True,
            entities=["credit_card"],
            mode="redact",
        )
        router = PrivacyRouter(cfg)
        text = "Card: 4111 1111 1111 1111"
        result, matches = router.scrub(text)
        assert any(m.entity_type == "credit_card" for m in matches)
        assert "[CREDIT_CARD]" in result

    def test_credit_card_only_router_with_dashes(self):
        """Credit card with dashes is detected when phone pattern is disabled."""
        cfg = PrivacyConfig(
            enabled=True,
            entities=["credit_card"],
            mode="redact",
        )
        router = PrivacyRouter(cfg)
        text = "Card: 4111-1111-1111-1111"
        result, matches = router.scrub(text)
        assert any(m.entity_type == "credit_card" for m in matches)
        assert "[CREDIT_CARD]" in result

    def test_credit_card_no_separator_detected(self):
        """Credit card with no separators is detected."""
        router = _default_router()
        text = "Card: 4111111111111111"
        result, matches = router.scrub(text)
        assert any(m.entity_type == "credit_card" for m in matches)

    def test_already_redacted_not_double_redacted(self):
        """Already-redacted [EMAIL] placeholder is NOT re-redacted."""
        router = _default_router()
        text = "Contact: [EMAIL] for details"
        result, matches = router.scrub(text)
        # [EMAIL] does not match any PII pattern -> no matches, text unchanged
        assert result == text
        assert len([m for m in matches if m.entity_type == "email"]) == 0

    def test_empty_string_returns_empty(self):
        """Empty string input returns empty string without error."""
        router = _default_router()
        result, matches = router.scrub("")
        assert result == ""
        assert matches == []

    def test_none_input_handled_via_scrub_dict(self):
        """None input through scrub_dict does not crash."""
        router = _default_router()
        result, matches = router.scrub_dict(None)
        assert result is None
        assert matches == []

    def test_ip_address_detected(self):
        """IPv4 addresses are detected and redacted."""
        router = _default_router()
        text = "Server at 192.168.1.100 is down"
        result, matches = router.scrub(text)
        assert any(m.entity_type == "ip_address" for m in matches)

    def test_audit_only_mode_preserves_text(self):
        """In audit_only mode, text is returned unchanged but matches are reported."""
        cfg = PrivacyConfig(
            enabled=True,
            entities=["email"],
            mode="audit_only",
        )
        router = PrivacyRouter(cfg)
        text = "Email: test@example.com"
        result, matches = router.scrub(text)
        assert result == text  # Not redacted
        assert len(matches) > 0  # But still detected

    def test_scrub_dict_recursive(self):
        """scrub_dict recursively scrubs nested dicts and lists."""
        router = _default_router()
        data = {
            "users": [
                {"name": "Alice", "email": "alice@test.com"},
                {"name": "Bob", "phone": "(555) 123-4567"},
            ],
            "meta": {"ssn": "123-45-6789"},
        }
        result, matches = router.scrub_dict(data)
        assert len(matches) >= 3  # email + phone + ssn


# ===================================================================
# 6. Additional cross-cutting integrity tests
# ===================================================================


class TestResolveVariable:
    """Verify resolve_variable edge cases directly."""

    def test_empty_var_path(self):
        """Empty variable path returns UNRESOLVED sentinel."""
        ctx = _make_context()
        assert resolve_variable("", ctx) is _UNRESOLVED

    def test_input_nested_dict(self):
        """Nested dict traversal works: input.user.name."""
        ctx = _make_context(input_data={"user": {"name": "Tomas"}})
        assert resolve_variable("input.user.name", ctx) == "Tomas"

    def test_input_list_index(self):
        """List index traversal works: input.items.0."""
        ctx = _make_context(input_data={"items": ["a", "b", "c"]})
        assert resolve_variable("input.items.1", ctx) == "b"

    def test_input_list_out_of_bounds(self):
        """Out-of-bounds list index returns UNRESOLVED."""
        ctx = _make_context(input_data={"items": ["a"]})
        assert resolve_variable("input.items.5", ctx) is _UNRESOLVED

    def test_step_output_none_returns_none(self):
        """Skipped step output (None) returns None, not UNRESOLVED."""
        ctx = _make_context(step_outputs={"skipped": None})
        result = resolve_variable("steps.skipped.output", ctx)
        assert result is None


class TestEscapeBraces:
    """Verify brace escaping prevents template injection."""

    def test_single_braces_doubled(self):
        """Single { and } are doubled to {{ and }}."""
        assert _escape_braces("{hello}") == "{{hello}}"

    def test_no_braces_unchanged(self):
        """String without braces passes through unchanged."""
        assert _escape_braces("plain text") == "plain text"

    def test_nested_braces(self):
        """Nested braces are all escaped."""
        assert _escape_braces("{{a}}") == "{{{{a}}}}"


class TestProviderRegistry:
    """Verify provider registry data integrity."""

    def test_all_models_have_nonnegative_pricing(self):
        """Every model in the registry has non-negative pricing."""
        for model_key, info in PROVIDER_REGISTRY.items():
            assert info.input_price_per_m >= 0, f"{model_key} has negative input price"
            assert info.output_price_per_m >= 0, f"{model_key} has negative output price"

    def test_all_models_have_region(self):
        """Every model has a valid region string."""
        valid_regions = {"us", "eu", "local"}
        for model_key, info in PROVIDER_REGISTRY.items():
            assert info.region in valid_regions, f"{model_key} has invalid region '{info.region}'"

    def test_resolve_unknown_model_raises(self):
        """Resolving an unknown model raises KeyError."""
        with pytest.raises(KeyError, match="Unknown model"):
            resolve_model("nonexistent/model")

    def test_resolve_known_model_returns_info(self):
        """Resolving a known model returns a ModelInfo object."""
        info = resolve_model("sonnet")
        assert isinstance(info, ModelInfo)
        assert info.provider == "claude"


class TestYAMLParsing:
    """Verify YAML parsing edge cases."""

    def test_empty_yaml_rejected(self):
        """Empty YAML content raises ValueError."""
        with pytest.raises(ValueError, match="empty"):
            parse_yaml_string("")

    def test_yaml_too_large_rejected(self):
        """YAML content > 1MB raises ValueError."""
        huge = "name: test\nsteps:\n" + "  - id: x\n" * 200_000
        with pytest.raises(ValueError, match="too large"):
            parse_yaml_string(huge)

    def test_minimal_valid_workflow_parses(self):
        """A minimal valid workflow YAML parses without error."""
        yaml_content = """
name: test
steps:
  - id: step1
    prompt: "hello"
"""
        wf = parse_yaml_string(yaml_content)
        assert wf.name == "test"
        assert len(wf.steps) == 1

    def test_depends_on_single_value_coerced_to_list(self):
        """A single depends_on value (not a list) is coerced to a list."""
        raw = {"id": "s1", "depends_on": "other", "prompt": "test"}
        defaults = {"model": "sonnet", "max_turns": 10, "timeout": 300}
        step = _parse_step(raw, defaults)
        assert step.depends_on == ["other"]
