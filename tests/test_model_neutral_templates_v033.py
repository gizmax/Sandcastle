"""Structural + thesis validation for the v0.33 model-independent workflow templates.

These five templates exist to prove Sandcastle is a real workflow engine, not a
prompt wrapper: each one puts its load-bearing decision in deterministic ``code`` or
in a cross-provider ``race``/``gate`` pattern, drives real action through ``http`` and
``notify``, waits on external state with ``sensor``, and escalates to a human via
``approval``. The checks below mirror the top-20 e2e suite (parse, step types,
depends_on, no cycles, build-plan coverage) and add thesis-specific assertions: every
template fans a ``race`` across at least two distinct providers and gates the result.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sandcastle.engine.dag import (
    VALID_STEP_TYPES,
    build_plan,
    parse_yaml_string,
    validate,
)
from sandcastle.engine.tools.registry import KNOWN_TOOLS
from sandcastle.templates import list_templates

MODEL_NEUTRAL_TEMPLATES = [
    "closed_loop_autoremediator",
    "quote_to_cash_orchestrator",
    "refund_credit_approval_workflow",
    "access_review_campaign_orchestrator",
    "cost_tiered_escalation_router",
]

# Control-flow step types whose presence proves the templates exercise the engine
# rather than chaining plain prompts. The inventory found these unused across the
# original 130 templates.
ENGINE_STEP_TYPES = {"http", "code", "race", "gate", "sensor", "loop", "classify", "condition"}

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "src" / "sandcastle" / "templates"


def _load(name: str) -> str:
    path = TEMPLATES_DIR / f"{name}.yaml"
    assert path.exists(), f"Template file not found: {path}"
    return path.read_text()


@pytest.mark.parametrize("template_name", MODEL_NEUTRAL_TEMPLATES)
def test_parses_with_steps(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    assert wf is not None
    assert wf.name, f"{template_name} has no name"
    assert len(wf.steps) > 0, f"{template_name} has no steps"


@pytest.mark.parametrize("template_name", MODEL_NEUTRAL_TEMPLATES)
def test_step_types_valid(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    for step in wf.steps:
        assert step.type in VALID_STEP_TYPES, (
            f"{template_name}/{step.id} invalid type {step.type!r}"
        )


@pytest.mark.parametrize("template_name", MODEL_NEUTRAL_TEMPLATES)
def test_depends_on_resolve(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    ids = {s.id for s in wf.steps}
    for step in wf.steps:
        for dep in step.depends_on:
            assert dep in ids, f"{template_name}/{step.id} depends on unknown {dep!r}"


@pytest.mark.parametrize("template_name", MODEL_NEUTRAL_TEMPLATES)
def test_validates_clean(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    errors = validate(wf)
    assert errors == [], f"{template_name} validate() errors: {errors}"


@pytest.mark.parametrize("template_name", MODEL_NEUTRAL_TEMPLATES)
def test_build_plan_covers_every_step_once(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    plan = build_plan(wf)
    assert plan is not None and len(plan.stages) > 0
    planned: list[str] = [sid for stage in plan.stages for sid in stage]
    assert len(planned) == len(set(planned)), f"{template_name}: a step is planned twice"
    assert set(planned) == {s.id for s in wf.steps}


@pytest.mark.parametrize("template_name", MODEL_NEUTRAL_TEMPLATES)
def test_tool_references_known(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    for step in wf.steps:
        if step.tool_config and step.tool_config.tool:
            base = step.tool_config.tool.split(":")[0]
            assert base in KNOWN_TOOLS, f"{template_name}/{step.id} unknown tool {base!r}"


@pytest.mark.parametrize("template_name", MODEL_NEUTRAL_TEMPLATES)
def test_control_flow_branches_reference_real_steps(template_name: str) -> None:
    """Every condition/classify/loop/race child reference must point to a real step."""
    wf = parse_yaml_string(_load(template_name))
    ids = {s.id for s in wf.steps}
    for step in wf.steps:
        if step.condition_config:
            for sid in step.condition_config.then_steps + step.condition_config.else_steps:
                assert sid in ids, f"{template_name}/{step.id} condition -> unknown {sid!r}"
        if step.classify_config:
            for branch in step.classify_config.branches.values():
                for sid in branch:
                    assert sid in ids, f"{template_name}/{step.id} classify -> unknown {sid!r}"
        if step.loop_config:
            for sid in step.loop_config.step_ids:
                assert sid in ids, f"{template_name}/{step.id} loop -> unknown {sid!r}"
        if step.race_config:
            for branch in step.race_config.branches:
                for sid in branch:
                    assert sid in ids, f"{template_name}/{step.id} race -> unknown {sid!r}"


@pytest.mark.parametrize("template_name", MODEL_NEUTRAL_TEMPLATES)
def test_exercises_engine_step_types(template_name: str) -> None:
    """Each template must use several of the control-flow step types the inventory
    found unused, not collapse into a plain prompt chain."""
    wf = parse_yaml_string(_load(template_name))
    used = {s.type for s in wf.steps}
    overlap = used & ENGINE_STEP_TYPES
    assert len(overlap) >= 4, (
        f"{template_name} only exercises {sorted(overlap)} of the engine step types"
    )


@pytest.mark.parametrize("template_name", MODEL_NEUTRAL_TEMPLATES)
def test_race_fans_across_distinct_providers(template_name: str) -> None:
    """The model-independence thesis: every template has a race whose branches are
    pinned to at least two DISTINCT providers (cross-provider failover), with a
    validator (first-valid-wins, not a vote tally)."""
    wf = parse_yaml_string(_load(template_name))
    by_id = {s.id: s for s in wf.steps}
    races = [s for s in wf.steps if s.type == "race" and s.race_config]
    assert races, f"{template_name} has no race step (model-independence not demonstrated)"
    found_cross_provider = False
    for race in races:
        models = {
            by_id[sid].model
            for branch in race.race_config.branches
            for sid in branch
            if sid in by_id and by_id[sid].model
        }
        # provider is the part before '/', or the bare alias for anthropic tiers
        providers = {m.split("/")[0] for m in models}
        if len(providers) >= 2:
            found_cross_provider = True
            assert race.race_config.validator, (
                f"{template_name}/{race.id} cross-provider race must have a validator"
            )
    assert found_cross_provider, (
        f"{template_name} race branches are not pinned to >= 2 distinct providers"
    )


@pytest.mark.parametrize("template_name", MODEL_NEUTRAL_TEMPLATES)
def test_has_gate_with_llm_and_human(template_name: str) -> None:
    """Decisions are gated by a measured llm_eval plus human oversight, not a single
    model's unilateral say."""
    wf = parse_yaml_string(_load(template_name))
    gates = [s for s in wf.steps if s.type == "gate" and s.gate_config]
    assert gates, f"{template_name} has no gate step"
    strat_types = {
        st.get("type")
        for gate in gates
        for st in gate.gate_config.strategies
    }
    assert "llm_eval" in strat_types, f"{template_name} gate has no llm_eval strategy"
    assert "human" in strat_types, f"{template_name} gate has no human strategy"


def test_templates_are_discovered() -> None:
    by_file = {t.file_name: t for t in list_templates()}
    for name in MODEL_NEUTRAL_TEMPLATES:
        info = by_file.get(f"{name}.yaml")
        assert info is not None, f"{name} not discovered"
        assert info.source == "built-in"
        assert info.step_count > 0
