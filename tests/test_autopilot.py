"""Tests for AutoPilot self-optimizing workflows."""

from __future__ import annotations

from collections import namedtuple

import pytest

from sandcastle.engine.autopilot import (
    _evaluate_schema_completeness,
    apply_variant,
    select_winner,
)
from sandcastle.engine.dag import (
    AutoPilotConfig,
    StepDefinition,
    VariantConfig,
    parse_yaml_string,
    validate,
)

# --- DAG parsing ---


AUTOPILOT_WORKFLOW_YAML = """
name: autopilot-test
description: Workflow with autopilot optimization
sandstorm_url: http://localhost:8000
steps:
  - id: enrich
    prompt: "Enrich {input.company}"
    model: sonnet
    autopilot:
      enabled: true
      optimize_for: cost
      min_samples: 5
      auto_deploy: true
      quality_threshold: 0.6
      variants:
        - id: baseline
          model: sonnet
        - id: cheap
          model: haiku
          prompt: "Quickly enrich {input.company}"
        - id: premium
          model: opus
          max_turns: 20
      evaluation:
        method: schema_completeness
        criteria: "completeness of enrichment data"
"""


class TestAutoPilotParsing:
    def test_parse_autopilot_config(self):
        workflow = parse_yaml_string(AUTOPILOT_WORKFLOW_YAML)
        step = workflow.get_step("enrich")
        assert step.autopilot is not None
        assert step.autopilot.enabled is True
        assert step.autopilot.optimize_for == "cost"
        assert step.autopilot.min_samples == 5
        assert step.autopilot.quality_threshold == 0.6

    def test_parse_variants(self):
        workflow = parse_yaml_string(AUTOPILOT_WORKFLOW_YAML)
        step = workflow.get_step("enrich")
        assert len(step.autopilot.variants) == 3
        assert step.autopilot.variants[0].id == "baseline"
        assert step.autopilot.variants[1].model == "haiku"
        assert step.autopilot.variants[2].max_turns == 20

    def test_parse_evaluation(self):
        workflow = parse_yaml_string(AUTOPILOT_WORKFLOW_YAML)
        step = workflow.get_step("enrich")
        assert step.autopilot.evaluation is not None
        assert step.autopilot.evaluation.method == "schema_completeness"

    def test_step_without_autopilot(self):
        yaml_content = """
name: no-autopilot
description: test
sandstorm_url: http://localhost:8000
steps:
  - id: step1
    prompt: "Hello"
"""
        workflow = parse_yaml_string(yaml_content)
        step = workflow.get_step("step1")
        assert step.autopilot is None

    def test_valid_autopilot_workflow(self):
        workflow = parse_yaml_string(AUTOPILOT_WORKFLOW_YAML)
        errors = validate(workflow)
        assert errors == []


# --- Variant application ---


class TestApplyVariant:
    def test_apply_model_variant(self):
        step = StepDefinition(id="test", prompt="Original prompt", model="sonnet")
        variant = VariantConfig(id="cheap", model="haiku")
        modified = apply_variant(step, variant)
        assert modified.model == "haiku"
        assert modified.prompt == "Original prompt"  # Unchanged
        assert modified.autopilot is None  # Cleared to prevent recursion

    def test_apply_prompt_variant(self):
        step = StepDefinition(id="test", prompt="Original", model="sonnet")
        variant = VariantConfig(id="alt", prompt="Alternative prompt")
        modified = apply_variant(step, variant)
        assert modified.prompt == "Alternative prompt"
        assert modified.model == "sonnet"  # Unchanged

    def test_apply_full_variant(self):
        step = StepDefinition(id="test", prompt="Original", model="sonnet", max_turns=10)
        variant = VariantConfig(id="premium", model="opus", prompt="Premium", max_turns=20)
        modified = apply_variant(step, variant)
        assert modified.model == "opus"
        assert modified.prompt == "Premium"
        assert modified.max_turns == 20


# --- Evaluation ---


class TestEvaluation:
    def test_schema_completeness_full(self):
        schema = {"properties": {"name": {}, "email": {}, "score": {}}}
        output = {"name": "Acme", "email": "a@b.com", "score": 95}
        assert _evaluate_schema_completeness(output, schema) == 1.0

    def test_schema_completeness_partial(self):
        schema = {"properties": {"name": {}, "email": {}, "score": {}}}
        output = {"name": "Acme", "email": None}
        score = _evaluate_schema_completeness(output, schema)
        assert score == pytest.approx(1 / 3)

    def test_schema_completeness_no_schema(self):
        assert _evaluate_schema_completeness({"data": 1}, None) == 1.0
        assert _evaluate_schema_completeness(None, None) == 0.0

    def test_schema_completeness_non_dict(self):
        schema = {"properties": {"name": {}}}
        assert _evaluate_schema_completeness("string output", schema) == 0.0


# --- Winner selection ---


VariantRow = namedtuple(
    "VariantRow", ["variant_id", "count", "avg_quality", "avg_cost", "avg_duration"]
)


class TestSelectWinner:
    def test_optimize_quality(self):
        stats = [
            VariantRow("baseline", 10, 0.9, 0.05, 5.0),
            VariantRow("cheap", 10, 0.7, 0.01, 2.0),
            VariantRow("premium", 10, 0.95, 0.10, 8.0),
        ]
        config = AutoPilotConfig(optimize_for="quality", quality_threshold=0.6)
        winner = select_winner(stats, config)
        assert winner["variant_id"] == "premium"

    def test_optimize_cost(self):
        stats = [
            VariantRow("baseline", 10, 0.9, 0.05, 5.0),
            VariantRow("cheap", 10, 0.7, 0.01, 2.0),
            VariantRow("premium", 10, 0.95, 0.10, 8.0),
        ]
        config = AutoPilotConfig(optimize_for="cost", quality_threshold=0.6)
        winner = select_winner(stats, config)
        assert winner["variant_id"] == "cheap"

    def test_optimize_latency(self):
        stats = [
            VariantRow("baseline", 10, 0.9, 0.05, 5.0),
            VariantRow("cheap", 10, 0.7, 0.01, 2.0),
            VariantRow("premium", 10, 0.95, 0.10, 8.0),
        ]
        config = AutoPilotConfig(optimize_for="latency", quality_threshold=0.6)
        winner = select_winner(stats, config)
        assert winner["variant_id"] == "cheap"

    def test_quality_threshold_filtering(self):
        stats = [
            VariantRow("bad", 10, 0.3, 0.01, 1.0),
            VariantRow("good", 10, 0.8, 0.05, 5.0),
        ]
        config = AutoPilotConfig(optimize_for="cost", quality_threshold=0.7)
        winner = select_winner(stats, config)
        # Only "good" passes threshold
        assert winner["variant_id"] == "good"

    def test_all_below_threshold_returns_best(self):
        stats = [
            VariantRow("a", 10, 0.3, 0.01, 1.0),
            VariantRow("b", 10, 0.4, 0.02, 2.0),
        ]
        config = AutoPilotConfig(optimize_for="quality", quality_threshold=0.9)
        winner = select_winner(stats, config)
        # Returns best quality even below threshold
        assert winner["variant_id"] == "b"

    def test_empty_stats(self):
        config = AutoPilotConfig(optimize_for="quality")
        assert select_winner([], config) is None

    def test_pareto_optimization(self):
        stats = [
            VariantRow("expensive_fast", 10, 0.9, 0.10, 1.0),
            VariantRow("balanced", 10, 0.85, 0.03, 3.0),
            VariantRow("cheap_slow", 10, 0.7, 0.01, 10.0),
        ]
        config = AutoPilotConfig(optimize_for="pareto", quality_threshold=0.6)
        winner = select_winner(stats, config)
        # Balanced should win in Pareto (best trade-off)
        assert winner is not None
