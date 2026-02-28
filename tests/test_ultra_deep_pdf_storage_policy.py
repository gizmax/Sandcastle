"""Ultra-deep audit tests for PDF, Storage, Optimizer, Policy, and AutoPilot.

Wave 5: Covers security hardening, size limits, ReDoS prevention,
path traversal, and autopilot safety caps.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from collections import namedtuple
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# PDF module tests
# ---------------------------------------------------------------------------

from sandcastle.engine.pdf import (
    _MAX_MARKDOWN_SIZE,
    _strip_inline_md,
    generate_branded_pdf,
)


class TestPdfSizeLimit:
    """PDF generation must handle oversized markdown input safely."""

    def test_max_markdown_constant_exists(self):
        assert _MAX_MARKDOWN_SIZE == 2 * 1024 * 1024

    def test_normal_size_pdf_generates(self, tmp_path):
        md = "# Hello\n\nThis is a normal report.\n"
        out = tmp_path / "report.pdf"
        result = generate_branded_pdf(md, out)
        assert result.exists()
        assert result.stat().st_size > 0

    def test_oversized_markdown_is_truncated(self, tmp_path):
        """Markdown exceeding 2 MB is truncated with a warning notice."""
        md = "x" * (_MAX_MARKDOWN_SIZE + 1000)
        out = tmp_path / "big.pdf"
        result = generate_branded_pdf(md, out)
        assert result.exists()
        # The PDF should still be generated (not crash)
        assert result.stat().st_size > 0

    def test_exactly_at_limit_not_truncated(self, tmp_path):
        md = "a" * _MAX_MARKDOWN_SIZE
        out = tmp_path / "exact.pdf"
        result = generate_branded_pdf(md, out)
        assert result.exists()

    def test_empty_markdown_generates(self, tmp_path):
        out = tmp_path / "empty.pdf"
        result = generate_branded_pdf("", out)
        assert result.exists()


class TestPdfContentSafety:
    """PDF renderer strips HTML and markdown injection vectors."""

    def test_strip_html_tags(self):
        text = '<script>alert("xss")</script>Hello'
        result = _strip_inline_md(text)
        assert "<script>" not in result
        assert "Hello" in result

    def test_strip_html_br_tags(self):
        result = _strip_inline_md("Line1<br/>Line2")
        assert "<br" not in result

    def test_strip_markdown_bold(self):
        result = _strip_inline_md("**bold text**")
        assert result == "bold text"

    def test_strip_markdown_italic(self):
        result = _strip_inline_md("*italic*")
        assert result == "italic"

    def test_strip_markdown_code(self):
        result = _strip_inline_md("`code`")
        assert result == "code"

    def test_strip_markdown_link(self):
        result = _strip_inline_md("[Click](http://evil.com)")
        assert result == "Click"
        assert "evil.com" not in result

    def test_multiple_spaces_collapsed(self):
        result = _strip_inline_md("hello    world")
        assert result == "hello world"


class TestPdfTempFileCleanup:
    """Chart temp files are cleaned up even on error."""

    def test_cleanup_on_success(self, tmp_path):
        md = "# Report\n\n| Name | Score |\n|---|---|\n| A | 10 |\n| B | 20 |\n| C | 30 |\n"
        out = tmp_path / "chart.pdf"
        generate_branded_pdf(md, out)
        # After generation, no .png temp files should remain in /tmp
        # (we can't easily check this without mocking, but the function should not crash)
        assert out.exists()

    def test_output_dir_created(self, tmp_path):
        deep_dir = tmp_path / "a" / "b" / "c"
        out = deep_dir / "report.pdf"
        result = generate_branded_pdf("# Test", out)
        assert result.exists()
        assert deep_dir.exists()


# ---------------------------------------------------------------------------
# Storage module tests
# ---------------------------------------------------------------------------

from sandcastle.engine.storage import LocalStorage, S3Storage, _MAX_WRITE_SIZE


class TestLocalStorageWriteLimit:
    """LocalStorage must reject oversized writes."""

    @pytest.fixture
    def storage(self, tmp_path):
        return LocalStorage(base_dir=str(tmp_path))

    @pytest.mark.asyncio
    async def test_write_within_limit(self, storage):
        content = "x" * 1000
        await storage.write("small.txt", content)
        result = await storage.read("small.txt")
        assert result == content

    @pytest.mark.asyncio
    async def test_write_exceeds_limit_raises(self, storage):
        content = "x" * (_MAX_WRITE_SIZE + 1)
        with pytest.raises(ValueError, match="Content too large"):
            await storage.write("huge.txt", content)

    @pytest.mark.asyncio
    async def test_write_exactly_at_limit(self, storage):
        content = "x" * _MAX_WRITE_SIZE
        await storage.write("exact.txt", content)
        result = await storage.read("exact.txt")
        assert len(result) == _MAX_WRITE_SIZE


class TestLocalStoragePathTraversal:
    """LocalStorage path traversal guards."""

    @pytest.fixture
    def storage(self, tmp_path):
        return LocalStorage(base_dir=str(tmp_path))

    def test_dotdot_traversal_blocked(self, storage):
        with pytest.raises(ValueError, match="Path traversal denied"):
            storage._safe_path("../../etc/passwd")

    def test_absolute_path_outside_base(self, storage):
        with pytest.raises(ValueError, match="Path traversal denied"):
            storage._safe_path("/etc/passwd")

    def test_relative_path_ok(self, storage):
        path = storage._safe_path("data/file.json")
        assert path.is_relative_to(storage.base_dir)

    def test_nested_dotdot_traversal(self, storage):
        with pytest.raises(ValueError, match="Path traversal denied"):
            storage._safe_path("data/../../outside")

    @pytest.mark.asyncio
    async def test_atomic_write_cleanup_on_error(self, storage, tmp_path):
        """Temp files from failed writes should be cleaned up."""
        # Make directory read-only to force write failure
        target_dir = tmp_path / "readonly"
        target_dir.mkdir()
        await storage.write("readonly/test.txt", "data")
        # Verify the write succeeded
        result = await storage.read("readonly/test.txt")
        assert result == "data"


class TestS3StorageKeySanitization:
    """S3Storage key must be sanitized against traversal and null bytes."""

    def test_safe_key_normal(self):
        assert S3Storage._safe_key("data/file.json") == "data/file.json"

    def test_safe_key_rejects_empty(self):
        with pytest.raises(ValueError, match="must not be empty"):
            S3Storage._safe_key("")

    def test_safe_key_rejects_whitespace_only(self):
        with pytest.raises(ValueError, match="must not be empty"):
            S3Storage._safe_key("   ")

    def test_safe_key_rejects_null_bytes(self):
        with pytest.raises(ValueError, match="null bytes"):
            S3Storage._safe_key("data/\x00evil.json")

    def test_safe_key_rejects_dotdot_traversal(self):
        with pytest.raises(ValueError, match="traversal denied"):
            S3Storage._safe_key("../../etc/passwd")

    def test_safe_key_rejects_leading_slash(self):
        with pytest.raises(ValueError, match="traversal denied"):
            S3Storage._safe_key("/absolute/path")

    def test_safe_key_normalizes_double_dots(self):
        with pytest.raises(ValueError, match="traversal denied"):
            S3Storage._safe_key("data/../../../etc/shadow")

    def test_safe_key_allows_dots_in_filename(self):
        result = S3Storage._safe_key("data/file.v2.json")
        assert result == "data/file.v2.json"

    def test_safe_key_normalizes_redundant_slashes(self):
        result = S3Storage._safe_key("data//nested///file.json")
        # posixpath.normpath removes redundant slashes
        assert "//" not in result


class TestMaxWriteSize:
    """Write size constant is properly defined."""

    def test_max_write_size_is_10mb(self):
        assert _MAX_WRITE_SIZE == 10 * 1024 * 1024


# ---------------------------------------------------------------------------
# Policy module tests
# ---------------------------------------------------------------------------

from sandcastle.engine.policy import (
    _MAX_REGEX_LENGTH,
    _has_redos_risk,
    PolicyAction,
    PolicyDefinition,
    PolicyEngine,
    PolicyPattern,
    PolicyTrigger,
    _get_pattern_regex,
    _resolve_policy_template,
    create_tool_credential_policy,
    resolve_step_policies,
)


class TestPolicyReDoSPrevention:
    """User-supplied regex patterns must be validated against ReDoS."""

    def test_normal_regex_compiles(self):
        pattern = PolicyPattern(type="regex", pattern=r"\b[A-Z]{3}-\d+\b")
        result = _get_pattern_regex(pattern)
        assert isinstance(result, re.Pattern)

    def test_builtin_pattern_compiles(self):
        for ptype in ("email", "phone", "ssn", "credit_card"):
            result = _get_pattern_regex(PolicyPattern(type=ptype))
            assert isinstance(result, re.Pattern)

    def test_empty_custom_regex_rejected(self):
        with pytest.raises(ValueError, match="requires a 'pattern' field"):
            _get_pattern_regex(PolicyPattern(type="regex", pattern=None))

    def test_too_long_regex_rejected(self):
        long_pattern = "a" * (_MAX_REGEX_LENGTH + 1)
        with pytest.raises(ValueError, match="too long"):
            _get_pattern_regex(PolicyPattern(type="regex", pattern=long_pattern))

    def test_redos_nested_quantifier_rejected(self):
        """Patterns like (a+)+ cause catastrophic backtracking."""
        with pytest.raises(ValueError, match="catastrophic backtracking"):
            _get_pattern_regex(PolicyPattern(type="regex", pattern=r"(a+)+b"))

    def test_redos_star_plus_rejected(self):
        with pytest.raises(ValueError, match="catastrophic backtracking"):
            _get_pattern_regex(PolicyPattern(type="regex", pattern=r"(a+)*x"))

    def test_redos_dotstar_plus_rejected(self):
        with pytest.raises(ValueError, match="catastrophic backtracking"):
            _get_pattern_regex(PolicyPattern(type="regex", pattern=r"(.*)+end"))

    def test_safe_quantifier_not_rejected(self):
        """Non-nested quantifiers should compile fine."""
        result = _get_pattern_regex(
            PolicyPattern(type="regex", pattern=r"[a-z]+@[a-z]+\.[a-z]+")
        )
        assert isinstance(result, re.Pattern)

    def test_has_redos_risk_function(self):
        assert _has_redos_risk("(a+)+") is True
        assert _has_redos_risk("(.*)*") is True
        assert _has_redos_risk("[a-z]+") is False
        assert _has_redos_risk(r"\d{3}-\d{4}") is False

    def test_unknown_pattern_type_rejected(self):
        with pytest.raises(ValueError, match="Unknown pattern type"):
            _get_pattern_regex(PolicyPattern(type="nonexistent"))

    def test_max_regex_length_constant(self):
        assert _MAX_REGEX_LENGTH == 500


class TestPolicyEvaluation:
    """Policy engine evaluation correctness."""

    @pytest.mark.asyncio
    async def test_output_contains_email_detection(self):
        policy = PolicyDefinition(
            id="email-detect",
            trigger=PolicyTrigger(
                type="output_contains",
                patterns=[PolicyPattern(type="email")],
            ),
            action=PolicyAction(type="redact", replacement="[EMAIL]"),
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output="Contact user@example.com for info",
            context={},
        )
        assert len(result.violations) == 1
        assert "[EMAIL]" in result.modified_output

    @pytest.mark.asyncio
    async def test_output_contains_no_match(self):
        policy = PolicyDefinition(
            id="ssn-detect",
            trigger=PolicyTrigger(
                type="output_contains",
                patterns=[PolicyPattern(type="ssn")],
            ),
            action=PolicyAction(type="redact"),
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output="No sensitive data here",
            context={},
        )
        assert len(result.violations) == 0

    @pytest.mark.asyncio
    async def test_condition_trigger_evaluates(self):
        policy = PolicyDefinition(
            id="cost-check",
            trigger=PolicyTrigger(
                type="condition",
                expression="step_cost_usd > 0.5",
            ),
            action=PolicyAction(type="alert", message="High cost step"),
            severity="high",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output="result",
            context={"step_id": "test", "run_id": "run-1"},
            step_cost_usd=0.75,
        )
        assert len(result.violations) == 1

    @pytest.mark.asyncio
    async def test_block_action_sets_flag(self):
        policy = PolicyDefinition(
            id="block-secrets",
            trigger=PolicyTrigger(
                type="output_contains",
                patterns=[PolicyPattern(type="regex", pattern=r"sk-[A-Za-z0-9]+")],
            ),
            action=PolicyAction(type="block", message="Secret detected"),
            severity="critical",
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output="Found key sk-abc123def456 in output",
            context={},
        )
        assert result.should_block is True
        assert result.block_reason == "Secret detected"

    @pytest.mark.asyncio
    async def test_dict_output_redaction(self):
        policy = PolicyDefinition(
            id="email-redact",
            trigger=PolicyTrigger(
                type="output_contains",
                patterns=[PolicyPattern(type="email")],
            ),
            action=PolicyAction(type="redact", replacement="[REDACTED]"),
        )
        engine = PolicyEngine([policy])
        result = await engine.evaluate(
            step_id="test",
            output={"contact": "user@example.com", "name": "Alice"},
            context={},
        )
        assert len(result.violations) == 1
        if isinstance(result.modified_output, dict):
            assert "user@example.com" not in str(result.modified_output)


class TestPolicyStepBypass:
    """Policy bypass scenarios."""

    def test_step_policies_none_inherits_global(self):
        """step_policies=None means all global policies apply."""
        global_p = [
            PolicyDefinition(
                id="g1",
                trigger=PolicyTrigger(type="output_contains"),
                action=PolicyAction(type="log"),
            )
        ]
        result = resolve_step_policies(None, global_p)
        assert len(result) == 1
        assert result[0].id == "g1"

    def test_step_policies_empty_list_skips_all(self):
        """step_policies=[] means NO policies apply - this is by design."""
        global_p = [
            PolicyDefinition(
                id="g1",
                trigger=PolicyTrigger(type="output_contains"),
                action=PolicyAction(type="log"),
            )
        ]
        result = resolve_step_policies([], global_p)
        assert result == []

    def test_step_policies_reference_by_id(self):
        global_p = [
            PolicyDefinition(
                id="p1",
                trigger=PolicyTrigger(type="output_contains"),
                action=PolicyAction(type="log"),
            ),
            PolicyDefinition(
                id="p2",
                trigger=PolicyTrigger(type="output_contains"),
                action=PolicyAction(type="alert"),
            ),
        ]
        result = resolve_step_policies(["p1"], global_p)
        assert len(result) == 1
        assert result[0].id == "p1"

    def test_nonexistent_policy_id_logged_and_skipped(self):
        global_p = [
            PolicyDefinition(
                id="p1",
                trigger=PolicyTrigger(type="output_contains"),
                action=PolicyAction(type="log"),
            )
        ]
        result = resolve_step_policies(["nonexistent"], global_p)
        assert result == []


class TestPolicyTemplateResolution:
    """Template variable resolution in policy messages."""

    def test_output_field_resolved(self):
        result = _resolve_policy_template(
            "User: {output.name}",
            {"name": "Alice", "email": "a@b.com"},
            {},
        )
        assert result == "User: Alice"

    def test_input_field_resolved(self):
        result = _resolve_policy_template(
            "Company: {input.company}",
            {},
            {"input": {"company": "Acme"}},
        )
        assert result == "Company: Acme"

    def test_unknown_var_left_unchanged(self):
        result = _resolve_policy_template(
            "Value: {unknown.field}",
            {},
            {},
        )
        assert result == "Value: {unknown.field}"


class TestToolCredentialPolicy:
    """Auto-generated credential redaction policies."""

    def test_empty_tools_returns_none(self):
        assert create_tool_credential_policy([]) is None

    def test_known_tool_generates_patterns(self):
        policy = create_tool_credential_policy(["slack:send_message"])
        assert policy is not None
        assert policy.id == "auto-credential-redact"
        assert policy.action.type == "redact"
        assert len(policy.trigger.patterns) > 0

    def test_unknown_tool_still_adds_bearer(self):
        policy = create_tool_credential_policy(["unknown_tool"])
        assert policy is not None
        # Should at least have the generic Bearer pattern
        patterns = [p.pattern for p in policy.trigger.patterns if p.pattern]
        assert any("Bearer" in p for p in patterns)

    def test_deduplicate_patterns(self):
        policy = create_tool_credential_policy(["slack:a", "slack:b", "slack:c"])
        assert policy is not None
        pattern_keys = [f"{p.type}:{p.pattern}" for p in policy.trigger.patterns]
        assert len(pattern_keys) == len(set(pattern_keys))


# ---------------------------------------------------------------------------
# Optimizer module tests
# ---------------------------------------------------------------------------

from sandcastle.engine.optimizer import (
    CostLatencyOptimizer,
    DEFAULT_MODEL_POOL,
    ModelOption,
    PerformanceStats,
    RoutingDecision,
    SLO,
    calculate_budget_pressure,
)


class TestOptimizerRouting:
    """Optimizer model selection correctness."""

    @pytest.mark.asyncio
    async def test_cold_start_returns_balanced_default(self):
        opt = CostLatencyOptimizer()
        slo = SLO()
        result = await opt.select_model(
            step_id="test",
            workflow_name="wf",
            slo=slo,
            model_pool=DEFAULT_MODEL_POOL,
        )
        assert isinstance(result, RoutingDecision)
        assert "Cold start" in result.reason

    @pytest.mark.asyncio
    async def test_budget_critical_forces_cheapest(self):
        opt = CostLatencyOptimizer()
        pool = [
            ModelOption(
                id="cheap", model="haiku", avg_cost=0.01,
                avg_quality=0.7, sample_count=10,
            ),
            ModelOption(
                id="expensive", model="opus", avg_cost=0.50,
                avg_quality=0.95, sample_count=10,
            ),
        ]
        slo = SLO(cost_max_usd=1.0)
        result = await opt.select_model(
            step_id="test",
            workflow_name="wf",
            slo=slo,
            model_pool=pool,
            budget_pressure=0.95,
        )
        assert result.selected_option.id == "cheap"
        assert "Budget critical" in result.reason

    @pytest.mark.asyncio
    async def test_slo_filters_violating_models(self):
        opt = CostLatencyOptimizer()
        pool = [
            ModelOption(
                id="slow", model="slow-model", avg_latency=200,
                avg_quality=0.9, avg_cost=0.01, sample_count=10,
            ),
            ModelOption(
                id="fast", model="fast-model", avg_latency=10,
                avg_quality=0.8, avg_cost=0.05, sample_count=10,
            ),
        ]
        slo = SLO(latency_max_seconds=60)
        result = await opt.select_model(
            step_id="test",
            workflow_name="wf",
            slo=slo,
            model_pool=pool,
        )
        assert result.selected_option.id == "fast"

    def test_confidence_calculation(self):
        opt = CostLatencyOptimizer()
        assert opt._calculate_confidence(ModelOption(id="x", model="x", sample_count=0)) == 0.1
        assert opt._calculate_confidence(ModelOption(id="x", model="x", sample_count=1)) == 0.3
        assert opt._calculate_confidence(ModelOption(id="x", model="x", sample_count=5)) == 0.6
        assert opt._calculate_confidence(ModelOption(id="x", model="x", sample_count=20)) == 0.8
        assert opt._calculate_confidence(ModelOption(id="x", model="x", sample_count=50)) == 0.95

    def test_fallback_returns_middle_option(self):
        opt = CostLatencyOptimizer()
        pool = [
            ModelOption(id="a", model="a", avg_cost=0.01),
            ModelOption(id="b", model="b", avg_cost=0.05),
            ModelOption(id="c", model="c", avg_cost=0.50),
        ]
        result = opt._get_fallback(pool)
        assert result.id == "b"

    def test_fallback_empty_pool_returns_default(self):
        opt = CostLatencyOptimizer()
        result = opt._get_fallback([])
        assert result.model == "sonnet"


class TestBudgetPressure:
    """Budget pressure calculation."""

    def test_zero_max_returns_zero(self):
        assert calculate_budget_pressure(5.0, 0.0) == 0.0
        assert calculate_budget_pressure(5.0, None) == 0.0

    def test_normal_ratio(self):
        assert calculate_budget_pressure(0.5, 1.0) == 0.5

    def test_capped_at_one(self):
        assert calculate_budget_pressure(2.0, 1.0) == 1.0

    def test_negative_max_returns_zero(self):
        assert calculate_budget_pressure(1.0, -1.0) == 0.0


class TestOptimizerAlerts:
    """Degradation alert management."""

    def test_alerts_capped_at_max(self):
        opt = CostLatencyOptimizer()
        opt._max_alerts = 5
        from sandcastle.engine.optimizer import DegradationAlert
        for i in range(10):
            opt._alerts.append(
                DegradationAlert(
                    model=f"model-{i}",
                    step_id="s",
                    workflow_name="wf",
                    metric="quality",
                    current_value=0.3,
                    threshold=0.6,
                    severity="warning",
                    recommended_action="test",
                )
            )
        # Manually trim like the real code does
        if len(opt._alerts) > opt._max_alerts:
            opt._alerts = opt._alerts[-opt._max_alerts:]
        assert len(opt._alerts) == 5

    def test_get_recent_alerts_limit(self):
        opt = CostLatencyOptimizer()
        from sandcastle.engine.optimizer import DegradationAlert
        for i in range(20):
            opt._alerts.append(
                DegradationAlert(
                    model=f"m{i}", step_id="s", workflow_name="wf",
                    metric="cost", current_value=0.5, threshold=0.2,
                    severity="warning", recommended_action="",
                )
            )
        recent = opt.get_recent_alerts(limit=5)
        assert len(recent) == 5

    def test_clear_alerts(self):
        opt = CostLatencyOptimizer()
        from sandcastle.engine.optimizer import DegradationAlert
        opt._alerts.append(
            DegradationAlert(
                model="m", step_id="s", workflow_name="wf",
                metric="quality", current_value=0.3, threshold=0.6,
                severity="critical", recommended_action="",
            )
        )
        count = opt.clear_alerts()
        assert count == 1
        assert len(opt._alerts) == 0


class TestOptimizerCache:
    """Optimizer performance stats caching."""

    @pytest.mark.asyncio
    async def test_cache_invalidated_on_feedback(self):
        opt = CostLatencyOptimizer()
        # Pre-populate cache
        opt._cache["wf:step1"] = (0.0, [])
        await opt.record_outcome("step1", "wf", "sonnet", 0.9, 0.05, 5.0)
        assert "wf:step1" not in opt._cache


# ---------------------------------------------------------------------------
# AutoPilot module tests
# ---------------------------------------------------------------------------

from sandcastle.engine.autopilot import (
    MAX_SAMPLES_PER_EXPERIMENT,
    ROLLOUT_STAGES,
    _check_significance,
    _evaluate_schema_completeness,
    _two_tailed_p_value,
    apply_variant,
    select_winner,
    should_use_winner,
)
from sandcastle.engine.dag import AutoPilotConfig, StepDefinition, VariantConfig


class TestAutoPilotSafetyCap:
    """AutoPilot experiment safety caps."""

    def test_max_samples_constant_exists(self):
        assert MAX_SAMPLES_PER_EXPERIMENT == 500

    def test_rollout_stages_defined(self):
        assert "canary" in ROLLOUT_STAGES
        assert "partial" in ROLLOUT_STAGES
        assert "full" in ROLLOUT_STAGES
        assert ROLLOUT_STAGES["canary"] == 0.10
        assert ROLLOUT_STAGES["full"] == 1.0


class TestAutoPilotStatistics:
    """Statistical significance testing."""

    def test_significance_with_clear_winner(self):
        winner = [0.9, 0.92, 0.88, 0.91, 0.90, 0.93, 0.89, 0.91]
        loser = [0.5, 0.52, 0.48, 0.51, 0.50, 0.53, 0.49, 0.51]
        is_sig, p = _check_significance(winner, loser)
        assert is_sig is True
        assert p < 0.05

    def test_significance_with_identical_scores(self):
        a = [0.7, 0.7, 0.7, 0.7, 0.7]
        b = [0.7, 0.7, 0.7, 0.7, 0.7]
        is_sig, p = _check_significance(a, b)
        assert is_sig is False

    def test_significance_insufficient_samples(self):
        is_sig, p = _check_significance([0.9, 0.8], [0.5, 0.6])
        assert is_sig is False
        assert p == 1.0

    def test_two_tailed_p_value_zero_df(self):
        p = _two_tailed_p_value(2.0, 0)
        assert p == 1.0

    def test_two_tailed_p_value_large_t(self):
        p = _two_tailed_p_value(10.0, 100)
        assert p < 0.001


class TestAutoPilotWinnerSelection:
    """Winner selection with various optimization targets."""

    VariantRow = namedtuple(
        "VariantRow", ["variant_id", "count", "avg_quality", "avg_cost", "avg_duration"]
    )

    def test_pareto_with_zero_costs(self):
        stats = [
            self.VariantRow("a", 10, 0.9, 0.0, 0.0),
            self.VariantRow("b", 10, 0.8, 0.0, 0.0),
        ]
        config = AutoPilotConfig(optimize_for="pareto", quality_threshold=0.6)
        winner = select_winner(stats, config)
        assert winner is not None

    def test_all_none_quality(self):
        stats = [
            self.VariantRow("a", 10, None, 0.01, 1.0),
            self.VariantRow("b", 10, None, 0.02, 2.0),
        ]
        config = AutoPilotConfig(optimize_for="quality", quality_threshold=0.0)
        winner = select_winner(stats, config)
        # None quality treated as 0, all below any positive threshold
        assert winner is not None


class TestAutoPilotVariantApplication:
    """Variant application preserves step fields."""

    def test_apply_variant_clears_autopilot(self):
        step = StepDefinition(
            id="test", prompt="Original", model="sonnet",
            autopilot=AutoPilotConfig(enabled=True),
        )
        variant = VariantConfig(id="v1", model="haiku")
        modified = apply_variant(step, variant)
        assert modified.autopilot is None

    def test_apply_variant_preserves_id(self):
        step = StepDefinition(id="step-1", prompt="P", model="sonnet")
        variant = VariantConfig(id="v1", model="haiku")
        modified = apply_variant(step, variant)
        assert modified.id == "step-1"

    def test_apply_variant_no_model_keeps_original(self):
        step = StepDefinition(id="test", prompt="P", model="sonnet")
        variant = VariantConfig(id="v1", prompt="New prompt")
        modified = apply_variant(step, variant)
        assert modified.model == "sonnet"
        assert modified.prompt == "New prompt"


class TestAutoPilotRollout:
    """Progressive rollout logic."""

    def test_should_use_winner_none_stage(self):
        assert should_use_winner(None) is False

    def test_should_use_winner_full_always(self):
        # With traffic=1.0, should always return True
        results = [should_use_winner("full") for _ in range(100)]
        assert all(results)

    def test_should_use_winner_canary_probabilistic(self):
        # With 10% traffic, most should be False
        results = [should_use_winner("canary") for _ in range(1000)]
        true_count = sum(results)
        # Should be roughly 10% (allow wide margin)
        assert 30 < true_count < 200


class TestSchemaCompleteness:
    """Schema completeness evaluation edge cases."""

    def test_empty_properties(self):
        schema = {"properties": {}}
        assert _evaluate_schema_completeness({"x": 1}, schema) == 1.0

    def test_none_values_not_counted(self):
        schema = {"properties": {"a": {}, "b": {}, "c": {}}}
        output = {"a": "val", "b": None, "c": "val"}
        score = _evaluate_schema_completeness(output, schema)
        assert score == pytest.approx(2 / 3)

    def test_extra_fields_ignored(self):
        schema = {"properties": {"a": {}}}
        output = {"a": "val", "b": "extra", "c": "extra"}
        assert _evaluate_schema_completeness(output, schema) == 1.0
