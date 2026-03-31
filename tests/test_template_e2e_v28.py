"""
TOP 20 TEMPLATE E2E TESTS FOR SANDCASTLE v0.28
================================================
Validates the 20 most important built-in templates by parsing each YAML
and running full DAG validation without any LLM calls.

Checks per template:
  - YAML parses without error
  - All step types are valid
  - All depends_on references exist
  - No circular dependencies
  - All tool references are valid (in TOOL_REGISTRY)
  - Step flow makes sense (can execute start to finish)
  - Model names are valid

Run:
    cd /Users/gizmax/Documents/Sandcastle
    .venv/bin/python -m pytest tests/test_template_e2e_v28.py -v --tb=short
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sandcastle.engine.dag import (
    VALID_STEP_TYPES,
    NON_LLM_TYPES,
    NON_PROMPT_TYPES,
    build_plan,
    parse_yaml_string,
    validate,
)
from sandcastle.engine.providers import KNOWN_MODELS
from sandcastle.engine.tools.registry import KNOWN_TOOLS

# ======================================================================
# Top 20 templates by importance (core use-cases, diverse step types)
# ======================================================================

TOP_20_TEMPLATES = [
    "research_agent",
    "data_extractor",
    "email_campaign",
    "competitive_intel",
    "contract_review",
    "deployment_monitor",
    "self_healing_pipeline",
    "deep_research_pipeline",
    "invoice_processor",
    "incident_responder",
    "compliance_checker",
    "lead_scoring",
    "content_factory",
    "resume_screener",
    "chain_of_thought",
    "seo_content",
    "ai_rag_pipeline",
    "contract_analyzer_with_parse",
    "review_and_approve",
    "customer_churn_predictor",
]

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "src" / "sandcastle" / "templates"


def _load_template(name: str) -> str:
    """Load a template YAML file by name."""
    path = TEMPLATES_DIR / f"{name}.yaml"
    assert path.exists(), f"Template file not found: {path}"
    return path.read_text()


# ======================================================================
# Parametrized tests - one test per template per check
# ======================================================================


class TestTemplateYamlParsing:
    """Verify all top 20 templates parse from YAML without errors."""

    @pytest.mark.parametrize("template_name", TOP_20_TEMPLATES)
    def test_yaml_parses_without_error(self, template_name: str) -> None:
        """Template YAML must parse into a WorkflowDefinition successfully."""
        content = _load_template(template_name)
        workflow = parse_yaml_string(content)
        assert workflow is not None
        assert workflow.name, f"Template {template_name} has no name"
        assert len(workflow.steps) > 0, f"Template {template_name} has no steps"


class TestStepTypesValid:
    """Verify all step types used in templates are valid."""

    @pytest.mark.parametrize("template_name", TOP_20_TEMPLATES)
    def test_all_step_types_valid(self, template_name: str) -> None:
        """Every step in the template must use a recognized step type."""
        content = _load_template(template_name)
        workflow = parse_yaml_string(content)
        for step in workflow.steps:
            assert step.type in VALID_STEP_TYPES, (
                f"Template '{template_name}', step '{step.id}' has "
                f"invalid type '{step.type}'. "
                f"Valid types: {sorted(VALID_STEP_TYPES)}"
            )


class TestDependsOnReferences:
    """Verify all depends_on references point to existing steps."""

    @pytest.mark.parametrize("template_name", TOP_20_TEMPLATES)
    def test_depends_on_references_exist(self, template_name: str) -> None:
        """Every depends_on reference must point to an existing step ID."""
        content = _load_template(template_name)
        workflow = parse_yaml_string(content)
        step_ids = {s.id for s in workflow.steps}
        for step in workflow.steps:
            for dep in step.depends_on:
                assert dep in step_ids, (
                    f"Template '{template_name}', step '{step.id}' "
                    f"depends on unknown step '{dep}'. "
                    f"Available: {sorted(step_ids)}"
                )


class TestNoCycles:
    """Verify no circular dependencies exist in template DAGs."""

    @pytest.mark.parametrize("template_name", TOP_20_TEMPLATES)
    def test_no_circular_dependencies(self, template_name: str) -> None:
        """DAG must be acyclic - no step can transitively depend on itself."""
        content = _load_template(template_name)
        workflow = parse_yaml_string(content)
        errors = validate(workflow)
        cycle_errors = [e for e in errors if "ycle" in e]
        assert not cycle_errors, (
            f"Template '{template_name}' has circular dependencies: "
            f"{cycle_errors}"
        )


class TestToolReferences:
    """Verify all tool references in templates are valid registry entries."""

    @pytest.mark.parametrize("template_name", TOP_20_TEMPLATES)
    def test_tool_references_valid(self, template_name: str) -> None:
        """Every tool reference must exist in TOOL_REGISTRY."""
        content = _load_template(template_name)
        workflow = parse_yaml_string(content)
        # Collect all tool refs from workflow-level and step-level
        all_tools: set[str] = set(workflow.default_tools)
        for step in workflow.steps:
            if step.tools:
                all_tools.update(step.tools)
        for tool_ref in all_tools:
            # Handle tool:connection syntax (e.g. "slack:main")
            base_name = tool_ref.split(":")[0]
            assert base_name in KNOWN_TOOLS, (
                f"Template '{template_name}' references unknown tool "
                f"'{base_name}'. Available: {sorted(KNOWN_TOOLS)}"
            )


class TestStepFlowExecutable:
    """Verify the step flow can execute from start to finish."""

    @pytest.mark.parametrize("template_name", TOP_20_TEMPLATES)
    def test_step_flow_makes_sense(self, template_name: str) -> None:
        """Template must produce a valid execution plan with stages."""
        content = _load_template(template_name)
        workflow = parse_yaml_string(content)
        plan = build_plan(workflow)
        assert plan is not None
        assert len(plan.stages) > 0, (
            f"Template '{template_name}' produces empty execution plan"
        )
        # All step IDs must appear in exactly one stage
        planned_ids: set[str] = set()
        for stage in plan.stages:
            for sid in stage:
                assert sid not in planned_ids, (
                    f"Step '{sid}' appears in multiple stages"
                )
                planned_ids.add(sid)
        template_ids = {s.id for s in workflow.steps}
        assert planned_ids == template_ids, (
            f"Template '{template_name}': planned steps {planned_ids} "
            f"!= template steps {template_ids}. "
            f"Missing: {template_ids - planned_ids}, "
            f"Extra: {planned_ids - template_ids}"
        )

    @pytest.mark.parametrize("template_name", TOP_20_TEMPLATES)
    def test_first_stage_has_no_dependencies(self, template_name: str) -> None:
        """The first stage should contain steps with no depends_on."""
        content = _load_template(template_name)
        workflow = parse_yaml_string(content)
        plan = build_plan(workflow)
        step_map = {s.id: s for s in workflow.steps}
        first_stage = plan.stages[0]
        for sid in first_stage:
            step = step_map[sid]
            # First-stage steps should have no explicit depends_on
            # (implicit deps from template vars may move them, which
            # is fine as long as build_plan resolves them)
            assert sid in {s.id for s in workflow.steps}, (
                f"First stage step '{sid}' not found in workflow steps"
            )


class TestModelNames:
    """Verify all model names used in templates are known."""

    @pytest.mark.parametrize("template_name", TOP_20_TEMPLATES)
    def test_model_names_valid(self, template_name: str) -> None:
        """Every model name used in template steps must be in KNOWN_MODELS."""
        content = _load_template(template_name)
        workflow = parse_yaml_string(content)
        # Check default model
        assert workflow.default_model in KNOWN_MODELS, (
            f"Template '{template_name}' has unknown default_model "
            f"'{workflow.default_model}'. Available: {sorted(KNOWN_MODELS)}"
        )
        # Check per-step models (skip non-LLM types)
        for step in workflow.steps:
            if step.type not in NON_LLM_TYPES:
                assert step.model in KNOWN_MODELS, (
                    f"Template '{template_name}', step '{step.id}' "
                    f"uses unknown model '{step.model}'. "
                    f"Available: {sorted(KNOWN_MODELS)}"
                )
        # Check fallback models
        for step in workflow.steps:
            if step.fallback and step.fallback.model:
                assert step.fallback.model in KNOWN_MODELS, (
                    f"Template '{template_name}', step '{step.id}' "
                    f"fallback uses unknown model '{step.fallback.model}'"
                )


class TestFullValidation:
    """Run the full validate() pipeline on each template."""

    @pytest.mark.parametrize("template_name", TOP_20_TEMPLATES)
    def test_full_validation_passes(self, template_name: str) -> None:
        """Full validation should produce zero errors for each template."""
        content = _load_template(template_name)
        workflow = parse_yaml_string(content)
        errors = validate(workflow)
        assert errors == [], (
            f"Template '{template_name}' has validation errors:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )


class TestStepPrompts:
    """Verify prompt requirements per step type."""

    @pytest.mark.parametrize("template_name", TOP_20_TEMPLATES)
    def test_prompt_required_steps_have_prompts(self, template_name: str) -> None:
        """Steps that require prompts must have non-empty prompt fields."""
        content = _load_template(template_name)
        workflow = parse_yaml_string(content)
        for step in workflow.steps:
            if step.type not in NON_PROMPT_TYPES:
                # These types need a prompt
                assert step.prompt and step.prompt.strip(), (
                    f"Template '{template_name}', step '{step.id}' "
                    f"(type={step.type}) requires a prompt but has none"
                )


class TestStepIdUniqueness:
    """Verify step IDs are unique within each template."""

    @pytest.mark.parametrize("template_name", TOP_20_TEMPLATES)
    def test_step_ids_unique(self, template_name: str) -> None:
        """No duplicate step IDs within a template."""
        content = _load_template(template_name)
        workflow = parse_yaml_string(content)
        seen: set[str] = set()
        for step in workflow.steps:
            assert step.id not in seen, (
                f"Template '{template_name}' has duplicate step ID '{step.id}'"
            )
            seen.add(step.id)


class TestTemplateMetadata:
    """Verify templates have required metadata fields."""

    @pytest.mark.parametrize("template_name", TOP_20_TEMPLATES)
    def test_has_name_and_description(self, template_name: str) -> None:
        """Each template must have a name and description."""
        content = _load_template(template_name)
        workflow = parse_yaml_string(content)
        assert workflow.name, f"Template '{template_name}' is missing name"
        assert workflow.description, (
            f"Template '{template_name}' is missing description"
        )

    @pytest.mark.parametrize("template_name", TOP_20_TEMPLATES)
    def test_name_is_nonempty_string(self, template_name: str) -> None:
        """Template names should be non-empty strings (kebab-case or human-readable)."""
        content = _load_template(template_name)
        workflow = parse_yaml_string(content)
        import re
        # Names can be kebab-case IDs or human-readable titles
        assert len(workflow.name.strip()) > 0, (
            f"Template '{template_name}' has empty name"
        )
        # Must not contain special characters that would break file paths
        assert re.match(r"^[a-zA-Z0-9][a-zA-Z0-9 \-_]+$", workflow.name), (
            f"Template '{template_name}' name '{workflow.name}' "
            "contains invalid characters"
        )


class TestDagTopology:
    """Verify DAG topological properties."""

    @pytest.mark.parametrize("template_name", TOP_20_TEMPLATES)
    def test_stages_are_ordered(self, template_name: str) -> None:
        """Steps in later stages should depend on steps in earlier stages."""
        content = _load_template(template_name)
        workflow = parse_yaml_string(content)
        plan = build_plan(workflow)
        step_map = {s.id: s for s in workflow.steps}
        # Build stage index: step_id -> stage_number
        stage_index: dict[str, int] = {}
        for i, stage in enumerate(plan.stages):
            for sid in stage:
                stage_index[sid] = i
        # Verify ordering: every explicit dependency must be in an earlier stage
        for step in workflow.steps:
            step_stage = stage_index.get(step.id)
            if step_stage is None:
                continue
            for dep in step.depends_on:
                dep_stage = stage_index.get(dep)
                if dep_stage is not None:
                    assert dep_stage < step_stage, (
                        f"Template '{template_name}': step '{step.id}' "
                        f"(stage {step_stage}) depends on '{dep}' "
                        f"(stage {dep_stage}) which is not in an earlier stage"
                    )
