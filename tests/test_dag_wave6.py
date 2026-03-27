"""Wave 6 deep audit tests for DAG building, template resolution, and validation.

Covers:
1. Implicit dependency detection in build_plan (condition expressions,
   prompt references, transform templates, etc.)
2. Template variable resolution edge cases (nested vars, missing vars,
   injection prevention, recursion guard, size limits)
3. Workflow YAML validation gaps (llm step prompt, template ref validation,
   step ID format edge cases)
4. Delegate step depth limit enforcement (early check before YAML load)
5. Transform step safety (template size limit, no Jinja2 SSTI)
"""

from __future__ import annotations

import asyncio
import re
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.dag import (
    StepDefinition,
    WorkflowDefinition,
    _collect_step_template_fields,
    _extract_implicit_deps,
    _extract_step_refs,
    build_plan,
    parse_yaml_string,
    validate,
)
from sandcastle.engine.executor import (
    RunContext,
    StepResult,
    _UNRESOLVED,
    _escape_braces,
    resolve_templates,
    resolve_variable,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(
    inputs: dict | None = None,
    step_outputs: dict | None = None,
    run_id: str | None = None,
) -> RunContext:
    """Create a RunContext for testing."""
    return RunContext(
        run_id=run_id or str(uuid.uuid4()),
        input=inputs or {},
        step_outputs=step_outputs or {},
    )


# =========================================================================
# 1. Implicit dependency detection in build_plan
# =========================================================================


class TestExtractStepRefs:
    """Unit tests for _extract_step_refs helper."""

    def test_single_ref(self):
        refs = _extract_step_refs("Use {steps.scrape.output} here")
        assert refs == {"scrape"}

    def test_multiple_refs(self):
        refs = _extract_step_refs(
            "{steps.alpha.output} and {steps.beta.output.field}"
        )
        assert refs == {"alpha", "beta"}

    def test_no_refs(self):
        refs = _extract_step_refs("No step references here")
        assert refs == set()

    def test_input_refs_ignored(self):
        refs = _extract_step_refs("{input.website} and {run_id}")
        assert refs == set()

    def test_empty_string(self):
        refs = _extract_step_refs("")
        assert refs == set()

    def test_none_string(self):
        refs = _extract_step_refs(None)
        assert refs == set()

    def test_hyphenated_step_id(self):
        refs = _extract_step_refs("{steps.my-step.output}")
        assert refs == {"my-step"}

    def test_nested_output_field(self):
        refs = _extract_step_refs("{steps.analyze.output.score.value}")
        assert refs == {"analyze"}


class TestCollectStepTemplateFields:
    """Tests for _collect_step_template_fields - collects all text fields."""

    def test_prompt_always_collected(self):
        step = StepDefinition(id="test", prompt="Use {steps.a.output}")
        fields = _collect_step_template_fields(step)
        assert "Use {steps.a.output}" in fields

    def test_condition_expression(self):
        from sandcastle.engine.dag import ConditionConfig

        step = StepDefinition(
            id="cond",
            type="condition",
            condition_config=ConditionConfig(
                expression="{steps.check.output} == True"
            ),
        )
        fields = _collect_step_template_fields(step)
        assert "{steps.check.output} == True" in fields

    def test_transform_template(self):
        from sandcastle.engine.dag import TransformConfig

        step = StepDefinition(
            id="tx",
            type="transform",
            transform_config=TransformConfig(
                template='{"data": "{steps.fetch.output}"}'
            ),
        )
        fields = _collect_step_template_fields(step)
        assert '{"data": "{steps.fetch.output}"}' in fields

    def test_notify_message_and_channel(self):
        from sandcastle.engine.dag import NotifyConfig

        step = StepDefinition(
            id="notify",
            type="notify",
            notify_config=NotifyConfig(
                service="slack",
                channel="{steps.route.output}",
                message="Result: {steps.analyze.output}",
            ),
        )
        fields = _collect_step_template_fields(step)
        assert "{steps.route.output}" in fields
        assert "Result: {steps.analyze.output}" in fields

    def test_http_url_and_body(self):
        from sandcastle.engine.dag import HttpConfig

        step = StepDefinition(
            id="call",
            type="http",
            http_config=HttpConfig(
                url="https://api.example.com/{steps.setup.output}",
                body='{"key": "{steps.auth.output}"}',
            ),
        )
        fields = _collect_step_template_fields(step)
        assert "https://api.example.com/{steps.setup.output}" in fields
        assert '{"key": "{steps.auth.output}"}' in fields


class TestExtractImplicitDeps:
    """Tests for _extract_implicit_deps."""

    def test_basic_implicit_dep(self):
        step = StepDefinition(
            id="enrich",
            prompt="Enrich {steps.scrape.output}",
            depends_on=[],
        )
        deps = _extract_implicit_deps(step, {"scrape", "enrich"})
        assert deps == {"scrape"}

    def test_explicit_dep_not_duplicated(self):
        step = StepDefinition(
            id="enrich",
            prompt="Enrich {steps.scrape.output}",
            depends_on=["scrape"],
        )
        deps = _extract_implicit_deps(step, {"scrape", "enrich"})
        assert deps == set()

    def test_self_reference_excluded(self):
        step = StepDefinition(
            id="loop_step",
            prompt="Previous: {steps.loop_step.output}",
            depends_on=[],
        )
        deps = _extract_implicit_deps(step, {"loop_step"})
        assert deps == set()

    def test_nonexistent_step_excluded(self):
        step = StepDefinition(
            id="test",
            prompt="Use {steps.nonexistent.output}",
            depends_on=[],
        )
        deps = _extract_implicit_deps(step, {"test", "other"})
        assert deps == set()

    def test_condition_expression_implicit_dep(self):
        from sandcastle.engine.dag import ConditionConfig

        step = StepDefinition(
            id="branch",
            type="condition",
            condition_config=ConditionConfig(
                expression="{steps.check.output} > 0.5",
                then_steps=["good"],
                else_steps=["bad"],
            ),
        )
        deps = _extract_implicit_deps(step, {"check", "branch", "good", "bad"})
        assert deps == {"check"}

    def test_multiple_implicit_deps(self):
        step = StepDefinition(
            id="combine",
            prompt="{steps.alpha.output} + {steps.beta.output}",
            depends_on=[],
        )
        deps = _extract_implicit_deps(step, {"alpha", "beta", "combine"})
        assert deps == {"alpha", "beta"}


class TestBuildPlanImplicitDeps:
    """Integration tests for build_plan with implicit dependencies."""

    def test_condition_expression_creates_dependency(self):
        """A condition step referencing {steps.X.output} must run after X."""
        yaml_content = """
name: implicit-condition-deps
description: test
steps:
  - id: check_score
    prompt: "Calculate score"
  - id: branch
    type: condition
    condition_config:
      expression: "{steps.check_score.output} > 0.5"
      then: [good_path]
      else: [bad_path]
  - id: good_path
    depends_on: [branch]
    prompt: "Good path"
  - id: bad_path
    depends_on: [branch]
    prompt: "Bad path"
"""
        wf = parse_yaml_string(yaml_content)
        plan = build_plan(wf)
        # check_score must be in an earlier stage than branch
        stage_of = {}
        for i, stage in enumerate(plan.stages):
            for sid in stage:
                stage_of[sid] = i
        assert stage_of["check_score"] < stage_of["branch"], (
            "check_score must run before branch due to implicit dep"
        )

    def test_prompt_reference_creates_dependency(self):
        """A step referencing {steps.X.output} in prompt without explicit depends_on."""
        yaml_content = """
name: implicit-prompt-deps
description: test
steps:
  - id: fetch
    prompt: "Fetch data"
  - id: process
    prompt: "Process {steps.fetch.output}"
"""
        wf = parse_yaml_string(yaml_content)
        plan = build_plan(wf)
        stage_of = {}
        for i, stage in enumerate(plan.stages):
            for sid in stage:
                stage_of[sid] = i
        assert stage_of["fetch"] < stage_of["process"], (
            "fetch must run before process due to implicit dep in prompt"
        )

    def test_explicit_deps_still_work(self):
        """Explicit depends_on should still work alongside implicit deps."""
        yaml_content = """
name: mixed-deps
description: test
steps:
  - id: a
    prompt: "Step A"
  - id: b
    prompt: "Step B"
  - id: c
    depends_on: [a]
    prompt: "Process {steps.b.output}"
"""
        wf = parse_yaml_string(yaml_content)
        plan = build_plan(wf)
        stage_of = {}
        for i, stage in enumerate(plan.stages):
            for sid in stage:
                stage_of[sid] = i
        # c depends on both a (explicit) and b (implicit)
        assert stage_of["a"] < stage_of["c"]
        assert stage_of["b"] < stage_of["c"]

    def test_no_implicit_deps_no_change(self):
        """Steps without template references should behave as before."""
        yaml_content = """
name: no-implicit
description: test
steps:
  - id: a
    prompt: "Step A"
  - id: b
    prompt: "Step B"
"""
        wf = parse_yaml_string(yaml_content)
        plan = build_plan(wf)
        # Both should be in the same stage (parallel)
        assert len(plan.stages) == 1
        assert sorted(plan.stages[0]) == ["a", "b"]

    def test_transform_template_creates_dependency(self):
        """Transform step with {steps.X.output} in template creates implicit dep."""
        yaml_content = """
name: transform-implicit
description: test
steps:
  - id: fetch
    prompt: "Fetch data"
  - id: transform
    type: transform
    transform_config:
      template: '{"result": "{steps.fetch.output}"}'
"""
        wf = parse_yaml_string(yaml_content)
        plan = build_plan(wf)
        stage_of = {}
        for i, stage in enumerate(plan.stages):
            for sid in stage:
                stage_of[sid] = i
        assert stage_of["fetch"] < stage_of["transform"]

    def test_notify_message_creates_dependency(self):
        """Notify step referencing {steps.X.output} in message creates implicit dep."""
        yaml_content = """
name: notify-implicit
description: test
steps:
  - id: analyze
    prompt: "Analyze data"
  - id: alert
    type: notify
    notify_config:
      service: slack
      channel: "#alerts"
      message: "Score: {steps.analyze.output}"
"""
        wf = parse_yaml_string(yaml_content)
        plan = build_plan(wf)
        stage_of = {}
        for i, stage in enumerate(plan.stages):
            for sid in stage:
                stage_of[sid] = i
        assert stage_of["analyze"] < stage_of["alert"]


# =========================================================================
# 2. Template variable resolution edge cases
# =========================================================================


class TestResolveVariable:
    """Tests for resolve_variable edge cases."""

    def test_missing_input_key(self):
        ctx = _make_context(inputs={"name": "test"})
        result = resolve_variable("input.nonexistent", ctx)
        assert result is _UNRESOLVED

    def test_missing_step_output(self):
        ctx = _make_context(step_outputs={})
        result = resolve_variable("steps.missing.output", ctx)
        assert result is _UNRESOLVED

    def test_none_step_output(self):
        ctx = _make_context(step_outputs={"skipped": None})
        result = resolve_variable("steps.skipped.output", ctx)
        assert result is None

    def test_nested_dict_access(self):
        ctx = _make_context(step_outputs={"api": {"score": 0.8, "label": "good"}})
        result = resolve_variable("steps.api.output.score", ctx)
        assert result == 0.8

    def test_list_index_access(self):
        ctx = _make_context(step_outputs={"items": ["a", "b", "c"]})
        result = resolve_variable("steps.items.output.1", ctx)
        assert result == "b"

    def test_list_index_out_of_range(self):
        ctx = _make_context(step_outputs={"items": ["a"]})
        result = resolve_variable("steps.items.output.5", ctx)
        assert result is _UNRESOLVED

    def test_empty_var_path(self):
        ctx = _make_context()
        result = resolve_variable("", ctx)
        assert result is _UNRESOLVED

    def test_run_id(self):
        ctx = _make_context(run_id="test-run-123")
        result = resolve_variable("run_id", ctx)
        assert result == "test-run-123"

    def test_date(self):
        ctx = _make_context()
        result = resolve_variable("date", ctx)
        # Should be an ISO date string
        assert re.match(r"\d{4}-\d{2}-\d{2}", result)

    def test_unknown_root(self):
        ctx = _make_context()
        result = resolve_variable("unknown.path", ctx)
        assert result is _UNRESOLVED


class TestResolveTemplates:
    """Tests for resolve_templates edge cases."""

    def test_basic_substitution(self):
        ctx = _make_context(inputs={"name": "Acme"})
        result = resolve_templates("Company: {input.name}", ctx)
        assert result == "Company: Acme"

    def test_missing_variable_left_as_placeholder(self):
        ctx = _make_context(inputs={})
        result = resolve_templates("Hello {input.missing}", ctx)
        assert "{input.missing}" in result

    def test_none_value_becomes_string(self):
        ctx = _make_context(step_outputs={"step1": None})
        result = resolve_templates("{steps.step1.output}", ctx)
        assert result == "None"

    def test_dict_value_json_serialized(self):
        ctx = _make_context(step_outputs={"api": {"key": "value"}})
        result = resolve_templates("{steps.api.output}", ctx)
        assert '"key"' in result
        assert '"value"' in result

    def test_list_value_json_serialized(self):
        ctx = _make_context(step_outputs={"items": [1, 2, 3]})
        result = resolve_templates("{steps.items.output}", ctx)
        assert "[1, 2, 3]" in result

    def test_brace_escape_prevents_reresolution(self):
        """If a step output contains {steps.X.output}, it must not re-resolve."""
        ctx = _make_context(
            step_outputs={"inner": "Use {steps.other.output} here"}
        )
        result = resolve_templates("{steps.inner.output}", ctx)
        # The inner braces should be escaped to double braces
        assert "{{steps.other.output}}" in result
        # Verify the single-brace pattern only appears as part of double-braces
        # (i.e., no unescaped template variable remains)
        unescaped = result.replace("{{", "").replace("}}", "")
        assert "{steps.other.output}" not in unescaped

    def test_nested_template_variable_not_supported(self):
        """Nested variables like {steps.{input.step_name}.output} should NOT resolve."""
        ctx = _make_context(
            inputs={"step_name": "foo"},
            step_outputs={"foo": "result"},
        )
        # The inner {input.step_name} breaks the outer regex match
        result = resolve_templates(
            "{steps.{input.step_name}.output}", ctx
        )
        # Should not resolve to "result" - the nested var pattern isn't supported
        assert result != "result"

    def test_template_size_limit(self):
        """Templates exceeding 1MB should raise ValueError."""
        ctx = _make_context()
        huge_template = "x" * (1024 * 1024 + 1)
        with pytest.raises(ValueError, match="too large"):
            resolve_templates(huge_template, ctx)

    def test_auto_inject_unreferenced_deps(self):
        """Dependency outputs not referenced in template should be auto-injected."""
        ctx = _make_context(
            step_outputs={"scrape": "scraped data"}
        )
        result = resolve_templates(
            "Analyze this",
            ctx,
            depends_on=["scrape"],
        )
        assert "scraped data" in result
        assert "Context from previous steps" in result

    def test_auto_inject_skips_referenced_deps(self):
        """Deps that ARE referenced in the template should NOT be auto-injected."""
        ctx = _make_context(
            step_outputs={"scrape": "scraped data"}
        )
        result = resolve_templates(
            "Analyze {steps.scrape.output}",
            ctx,
            depends_on=["scrape"],
        )
        # Should not have the "Context from previous steps" block
        assert "Context from previous steps" not in result


class TestEscapeBraces:
    """Tests for _escape_braces injection prevention."""

    def test_basic_escape(self):
        assert _escape_braces("{hello}") == "{{hello}}"

    def test_no_braces(self):
        assert _escape_braces("plain text") == "plain text"

    def test_template_pattern_escaped(self):
        result = _escape_braces("{steps.x.output}")
        assert result == "{{steps.x.output}}"


# =========================================================================
# 3. Workflow YAML validation gaps
# =========================================================================


class TestValidationGaps:
    """Tests for validation rules added in wave6."""

    def test_llm_step_requires_prompt(self):
        """LLM step type should require a meaningful prompt."""
        yaml_content = """
name: llm-no-prompt
description: test
steps:
  - id: generate
    type: llm
"""
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        assert any("LLM step" in e and "prompt" in e for e in errors)

    def test_llm_step_with_prompt_valid(self):
        """LLM step with a prompt should pass validation."""
        yaml_content = """
name: llm-with-prompt
description: test
steps:
  - id: generate
    type: llm
    prompt: "Summarize the document"
"""
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        llm_errors = [e for e in errors if "LLM step" in e]
        assert llm_errors == []

    def test_template_ref_to_unknown_step(self):
        """Template references to nonexistent steps should be flagged."""
        yaml_content = """
name: bad-template-ref
description: test
steps:
  - id: step1
    prompt: "Use {steps.nonexistent.output}"
"""
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        assert any("references unknown step 'nonexistent'" in e for e in errors)

    def test_template_ref_to_valid_step_ok(self):
        """Template references to existing steps should not produce errors."""
        yaml_content = """
name: good-template-ref
description: test
steps:
  - id: fetch
    prompt: "Fetch data"
  - id: process
    depends_on: [fetch]
    prompt: "Process {steps.fetch.output}"
"""
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        ref_errors = [e for e in errors if "references unknown step" in e]
        assert ref_errors == []

    def test_step_id_with_spaces_invalid(self):
        """Step IDs with spaces should be rejected."""
        yaml_content = """
name: bad-ids
description: test
steps:
  - id: "step with spaces"
    prompt: "bad"
"""
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        assert any("invalid" in e.lower() for e in errors)

    def test_step_id_with_special_chars_invalid(self):
        """Step IDs with special chars should be rejected."""
        yaml_content = """
name: bad-ids
description: test
steps:
  - id: "step@#$"
    prompt: "bad"
"""
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        assert any("invalid" in e.lower() for e in errors)

    def test_step_id_hyphen_and_underscore_valid(self):
        """Step IDs with hyphens and underscores should be valid."""
        yaml_content = """
name: good-ids
description: test
steps:
  - id: my_step-1
    prompt: "good"
"""
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        id_errors = [e for e in errors if "invalid" in e.lower() and "ID" in e]
        assert id_errors == []

    def test_empty_step_id_rejected(self):
        """Empty step ID should be rejected during parsing."""
        yaml_content = """
name: empty-id
description: test
steps:
  - id: ""
    prompt: "empty id"
"""
        with pytest.raises(ValueError, match="non-empty"):
            parse_yaml_string(yaml_content)

    def test_duplicate_step_ids_detected(self):
        """Duplicate step IDs should produce validation errors."""
        yaml_content = """
name: dupes
description: test
steps:
  - id: alpha
    prompt: "first"
  - id: alpha
    prompt: "second"
"""
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        assert any("Duplicate" in e for e in errors)

    def test_depends_on_nonexistent_step(self):
        """depends_on referencing a nonexistent step should be flagged."""
        yaml_content = """
name: bad-dep
description: test
steps:
  - id: step1
    depends_on: [ghost]
    prompt: "depends on nothing"
"""
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        assert any("unknown step 'ghost'" in e for e in errors)

    def test_condition_then_else_unknown_step(self):
        """Condition then/else referencing unknown steps should be flagged."""
        yaml_content = """
name: bad-condition
description: test
steps:
  - id: cond
    type: condition
    condition_config:
      expression: "True"
      then: [missing_then]
      else: [missing_else]
"""
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        assert any("missing_then" in e for e in errors)
        assert any("missing_else" in e for e in errors)

    def test_delegate_step_requires_workflow(self):
        """Delegate step must have delegate_config with a workflow name."""
        yaml_content = """
name: bad-delegate
description: test
steps:
  - id: sub
    type: delegate
    delegate_config:
      task_description: "Do something"
"""
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        assert any("delegate_config with a workflow" in e for e in errors)

    def test_sensor_requires_url_and_condition(self):
        """Sensor step must have both url and condition."""
        yaml_content = """
name: bad-sensor
description: test
steps:
  - id: poll
    type: sensor
    sensor_config:
      check_interval: 10
      timeout: 60
"""
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        assert any("url" in e for e in errors)
        assert any("condition" in e for e in errors)

    def test_transform_ref_to_unknown_step_in_template(self):
        """Transform template referencing unknown step should be caught."""
        yaml_content = """
name: bad-transform-ref
description: test
steps:
  - id: tx
    type: transform
    transform_config:
      template: '{"data": "{steps.ghost.output}"}'
"""
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        assert any("references unknown step 'ghost'" in e for e in errors)


# =========================================================================
# 4. Delegate step depth limit enforcement
# =========================================================================


class TestDelegateDepthLimit:
    """Tests for early depth check in _execute_delegate_step."""

    @pytest.fixture
    def mock_settings(self):
        """Patch settings with max_workflow_depth=3."""
        with patch("sandcastle.engine.executor.settings", create=True) as mock:
            mock.max_workflow_depth = 3
            yield mock

    def test_depth_exceeded_returns_early(self):
        """Delegate at max depth should fail without loading sub-workflow."""
        from sandcastle.engine.dag import DelegateConfig
        from sandcastle.engine.executor import _execute_delegate_step

        step = StepDefinition(
            id="delegate_step",
            type="delegate",
            delegate_config=DelegateConfig(
                workflow="sub_workflow",
                task_description="Do something",
            ),
        )
        ctx = _make_context()
        storage = MagicMock()

        # Mock settings so max_workflow_depth = 3
        mock_settings = MagicMock()
        mock_settings.max_workflow_depth = 3

        with patch(
            "sandcastle.engine.executor._delegate_settings",
            mock_settings,
            create=True,
        ):
            # Patch at the import location inside the function
            with patch.dict(
                "sys.modules",
                {"sandcastle.config": MagicMock(settings=mock_settings)},
            ):
                result = asyncio.run(
                    _execute_delegate_step(step, ctx, storage, depth=2)
                )

        assert result.status == "failed"
        assert "depth" in result.error.lower()

    def test_depth_ok_proceeds(self):
        """Delegate within depth limit should proceed (may fail for other reasons)."""
        from sandcastle.engine.dag import DelegateConfig
        from sandcastle.engine.executor import _execute_delegate_step

        step = StepDefinition(
            id="delegate_step",
            type="delegate",
            delegate_config=DelegateConfig(
                workflow="nonexistent_workflow",
                task_description="Do something",
            ),
        )
        ctx = _make_context()
        storage = MagicMock()

        mock_settings = MagicMock()
        mock_settings.max_workflow_depth = 5
        mock_settings.workflows_dir = "/nonexistent"

        with patch.dict(
            "sys.modules",
            {"sandcastle.config": MagicMock(settings=mock_settings)},
        ):
            result = asyncio.run(
                _execute_delegate_step(step, ctx, storage, depth=0)
            )

        # Should proceed past depth check (may fail finding the workflow)
        # The error should NOT be about exceeding depth limit
        if result.status == "failed" and result.error:
            assert "exceeds max" not in result.error.lower()
            assert "depth limit" not in result.error.lower()

    def test_missing_delegate_config(self):
        """Delegate step without config should return missing config error."""
        from sandcastle.engine.executor import _execute_delegate_step

        step = StepDefinition(id="bad", type="delegate")
        ctx = _make_context()
        storage = MagicMock()

        result = asyncio.run(
            _execute_delegate_step(step, ctx, storage)
        )
        assert result.status == "failed"
        assert "Missing delegate_config" in result.error


# =========================================================================
# 5. Transform step safety
# =========================================================================


class TestTransformStepSafety:
    """Tests for transform step template safety."""

    def test_transform_template_size_limit(self):
        """Oversized transform templates should fail gracefully."""
        from sandcastle.engine.dag import TransformConfig
        from sandcastle.engine.executor import _execute_transform_step

        huge_template = "x" * (1024 * 1024 + 1)
        step = StepDefinition(
            id="big_transform",
            type="transform",
            transform_config=TransformConfig(template=huge_template),
        )
        ctx = _make_context()

        result = asyncio.run(
            _execute_transform_step(step, ctx)
        )
        assert result.status == "failed"
        assert "too large" in result.error.lower()

    def test_transform_basic_resolution(self):
        """Transform step should resolve {steps.X.output} variables."""
        from sandcastle.engine.dag import TransformConfig
        from sandcastle.engine.executor import _execute_transform_step

        step = StepDefinition(
            id="tx",
            type="transform",
            transform_config=TransformConfig(
                template='{"score": "{steps.calc.output}"}'
            ),
        )
        ctx = _make_context(step_outputs={"calc": "0.95"})

        result = asyncio.run(
            _execute_transform_step(step, ctx)
        )
        assert result.status == "completed"

    def test_transform_jinja_like_syntax(self):
        """Transform step supports {{ var }} Jinja2-like syntax safely."""
        from sandcastle.engine.dag import TransformConfig
        from sandcastle.engine.executor import _execute_transform_step

        step = StepDefinition(
            id="tx",
            type="transform",
            transform_config=TransformConfig(
                template="{{ steps.data.output | tojson }}"
            ),
        )
        ctx = _make_context(step_outputs={"data": {"key": "value"}})

        result = asyncio.run(
            _execute_transform_step(step, ctx)
        )
        assert result.status == "completed"
        # Output should be the JSON-parsed dict
        assert result.output == {"key": "value"}

    def test_transform_no_real_jinja2_execution(self):
        """Transform step should NOT execute real Jinja2 (no {% %} blocks)."""
        from sandcastle.engine.dag import TransformConfig
        from sandcastle.engine.executor import _execute_transform_step

        # Jinja2 for-loop syntax - should be treated as literal text
        step = StepDefinition(
            id="tx",
            type="transform",
            transform_config=TransformConfig(
                template="{% for i in range(10) %}{{ i }}{% endfor %}"
            ),
        )
        ctx = _make_context()

        result = asyncio.run(
            _execute_transform_step(step, ctx)
        )
        assert result.status == "completed"
        # The Jinja2 block syntax should be left as literal text
        assert "{% for" in str(result.output)

    def test_transform_missing_config(self):
        """Transform step without config should return error."""
        from sandcastle.engine.executor import _execute_transform_step

        step = StepDefinition(id="bad_tx", type="transform")
        ctx = _make_context()

        result = asyncio.run(
            _execute_transform_step(step, ctx)
        )
        assert result.status == "failed"
        assert "Missing transform_config" in result.error


# =========================================================================
# Additional edge case tests
# =========================================================================


class TestBuildPlanCycleWithImplicitDeps:
    """Ensure implicit deps don't create false cycles or break topological sort."""

    def test_self_reference_does_not_create_cycle(self):
        """A step referencing itself should not create a cycle."""
        yaml_content = """
name: self-ref
description: test
steps:
  - id: step1
    prompt: "Process {steps.step1.output}"
"""
        wf = parse_yaml_string(yaml_content)
        plan = build_plan(wf)
        assert plan.stages == [["step1"]]

    def test_mutual_reference_in_prompts_is_cycle(self):
        """Mutual implicit references should create a cycle (caught by build_plan)."""
        yaml_content = """
name: mutual-ref
description: test
steps:
  - id: alpha
    prompt: "Use {steps.beta.output}"
  - id: beta
    prompt: "Use {steps.alpha.output}"
"""
        wf = parse_yaml_string(yaml_content)
        with pytest.raises(ValueError, match="unschedulable"):
            build_plan(wf)

    def test_implicit_plus_explicit_no_duplicate_edges(self):
        """When a dep is both explicit and implicit, it should not cause issues."""
        yaml_content = """
name: double-dep
description: test
steps:
  - id: a
    prompt: "A"
  - id: b
    depends_on: [a]
    prompt: "Use {steps.a.output}"
"""
        wf = parse_yaml_string(yaml_content)
        plan = build_plan(wf)
        # Should be two stages: [a] then [b]
        assert plan.stages == [["a"], ["b"]]


class TestParseYamlEdgeCases:
    """Tests for YAML parsing edge cases."""

    def test_empty_yaml(self):
        with pytest.raises(ValueError, match="empty"):
            parse_yaml_string("")

    def test_whitespace_only_yaml(self):
        with pytest.raises(ValueError, match="empty"):
            parse_yaml_string("   \n\n  ")

    def test_yaml_too_large(self):
        huge = "name: test\n" + "x" * (1024 * 1024 + 100)
        with pytest.raises(ValueError, match="too large"):
            parse_yaml_string(huge)

    def test_non_mapping_yaml(self):
        with pytest.raises(ValueError, match="mapping"):
            parse_yaml_string("- item1\n- item2")

    def test_yaml_null_document(self):
        with pytest.raises(ValueError, match="None"):
            parse_yaml_string("---\n")

    def test_missing_name_field(self):
        with pytest.raises(ValueError, match="name"):
            parse_yaml_string("description: no name\nsteps: []")

    def test_steps_not_list(self):
        with pytest.raises(ValueError, match="list"):
            parse_yaml_string("name: test\nsteps: not_a_list")

    def test_step_id_not_string(self):
        with pytest.raises(ValueError, match="string"):
            parse_yaml_string("""
name: test
steps:
  - id: 123
    prompt: "numeric id"
""")


class TestValidateComplexWorkflows:
    """Additional validation tests for complex workflows."""

    def test_race_unknown_step_in_branch(self):
        """Race step referencing unknown step should be caught."""
        yaml_content = """
name: bad-race
description: test
steps:
  - id: race1
    type: race
    race_config:
      branches:
        - [missing_step]
"""
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        assert any("missing_step" in e for e in errors)

    def test_loop_invalid_max_iterations(self):
        """Loop step with invalid max_iterations should be caught."""
        yaml_content = """
name: bad-loop
description: test
steps:
  - id: loop1
    type: loop
    loop_config:
      over: "{input.items}"
      max_iterations: 0
"""
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        assert any("max_iterations" in e for e in errors)

    def test_workflow_name_too_long(self):
        """Workflow name exceeding 200 chars should be caught."""
        yaml_content = f"""
name: {"x" * 201}
description: test
steps:
  - id: step1
    prompt: "hello"
"""
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        assert any("too long" in e for e in errors)

    def test_too_many_steps(self):
        """Workflow with more than 500 steps should be caught."""
        steps = "\n".join(
            [f'  - id: step{i}\n    prompt: "step {i}"' for i in range(501)]
        )
        yaml_content = f"name: huge\ndescription: test\nsteps:\n{steps}"
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        assert any("too many steps" in e for e in errors)

    def test_valid_complex_workflow_no_errors(self):
        """A well-formed complex workflow should pass validation."""
        yaml_content = """
name: complex-valid
description: test
steps:
  - id: fetch
    prompt: "Fetch data from {input.url}"
  - id: analyze
    depends_on: [fetch]
    prompt: "Analyze {steps.fetch.output}"
  - id: check
    depends_on: [analyze]
    type: condition
    condition_config:
      expression: "{steps.analyze.output} > 0.5"
      then: [good]
      else: [bad]
  - id: good
    depends_on: [check]
    prompt: "Handle good case"
  - id: bad
    depends_on: [check]
    prompt: "Handle bad case"
"""
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        # Filter out model/tool warnings which are environment-dependent
        structural_errors = [
            e for e in errors
            if "Unknown model" not in e and "Unknown tool" not in e
        ]
        assert structural_errors == []
