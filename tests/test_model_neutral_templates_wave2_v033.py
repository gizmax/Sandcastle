"""Structural + thesis validation for the v0.33 model-independent templates, wave 2.

This wave broadens the patterns beyond the first five: it adds ``parse`` (invoice
extraction), ``sub_workflow`` composition (map-reduce), a true multi-provider
*consensus* (N parallel votes on distinct providers plus a deterministic code tally,
as opposed to race failover), a two-judge cross-provider ``gate``, and fills the
previously near-empty ``data`` category. Each template proves model-independence in
one of several legitimate ways, so the thesis check accepts any of: a cross-provider
race, multi-provider consensus votes, a two-judge gate, or a deterministic code
decision guarded by human oversight.
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

WAVE2_TEMPLATES = [
    "three_way_match_pay_run",
    "provider_consensus_decision_engine",
    "warehouse_data_quality_sentinel",
    "vuln_triage_reachability_gate",
    "sla_sensor_escalation_ladder",
    "map_reduce_over_list",
]

ENGINE_STEP_TYPES = {
    "http", "code", "race", "gate", "sensor", "loop", "classify",
    "condition", "parse", "sub_workflow", "transform",
}

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "src" / "sandcastle" / "templates"


def _load(name: str) -> str:
    path = TEMPLATES_DIR / f"{name}.yaml"
    assert path.exists(), f"Template file not found: {path}"
    return path.read_text()


def _provider(model: str | None) -> str | None:
    if not model:
        return None
    return model.split("/")[0]


@pytest.mark.parametrize("template_name", WAVE2_TEMPLATES)
def test_parses_with_steps(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    assert wf is not None and wf.name and len(wf.steps) > 0


@pytest.mark.parametrize("template_name", WAVE2_TEMPLATES)
def test_step_types_valid(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    for step in wf.steps:
        assert step.type in VALID_STEP_TYPES, (
            f"{template_name}/{step.id} invalid type {step.type!r}"
        )


@pytest.mark.parametrize("template_name", WAVE2_TEMPLATES)
def test_depends_on_resolve(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    ids = {s.id for s in wf.steps}
    for step in wf.steps:
        for dep in step.depends_on:
            assert dep in ids, f"{template_name}/{step.id} depends on unknown {dep!r}"


@pytest.mark.parametrize("template_name", WAVE2_TEMPLATES)
def test_validates_clean(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    errors = validate(wf)
    assert errors == [], f"{template_name} validate() errors: {errors}"


@pytest.mark.parametrize("template_name", WAVE2_TEMPLATES)
def test_build_plan_covers_every_step_once(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    plan = build_plan(wf)
    assert plan is not None and len(plan.stages) > 0
    planned: list[str] = [sid for stage in plan.stages for sid in stage]
    assert len(planned) == len(set(planned)), f"{template_name}: a step is planned twice"
    assert set(planned) == {s.id for s in wf.steps}


@pytest.mark.parametrize("template_name", WAVE2_TEMPLATES)
def test_tool_references_known(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    for step in wf.steps:
        if step.tool_config and step.tool_config.tool:
            base = step.tool_config.tool.split(":")[0]
            assert base in KNOWN_TOOLS, f"{template_name}/{step.id} unknown tool {base!r}"


@pytest.mark.parametrize("template_name", WAVE2_TEMPLATES)
def test_control_flow_branches_reference_real_steps(template_name: str) -> None:
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


@pytest.mark.parametrize("template_name", WAVE2_TEMPLATES)
def test_exercises_engine_step_types(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    overlap = {s.type for s in wf.steps} & ENGINE_STEP_TYPES
    assert len(overlap) >= 4, (
        f"{template_name} only exercises {sorted(overlap)} engine step types"
    )


@pytest.mark.parametrize("template_name", WAVE2_TEMPLATES)
def test_demonstrates_model_independence(template_name: str) -> None:
    """Each template must prove model-independence in at least one legitimate way:
    a cross-provider race, multi-provider consensus votes, a two-judge gate across
    distinct providers, or a deterministic code decision guarded by human oversight.
    """
    wf = parse_yaml_string(_load(template_name))
    by_id = {s.id: s for s in wf.steps}

    # (a) cross-provider race
    cross_provider_race = False
    for s in wf.steps:
        if s.type == "race" and s.race_config:
            provs = {
                _provider(by_id[sid].model)
                for branch in s.race_config.branches
                for sid in branch
                if sid in by_id and by_id[sid].model
            }
            if len({p for p in provs if p}) >= 2 and s.race_config.validator:
                cross_provider_race = True

    # (b) multi-provider consensus: >= 2 model-pinned llm/standard steps on distinct providers
    vote_provs = {
        _provider(s.model)
        for s in wf.steps
        if s.type in ("standard", "llm") and getattr(s, "model", None)
    }
    consensus = len({p for p in vote_provs if p}) >= 2

    # (c) two-judge gate: a gate with >= 2 llm_eval strategies on distinct providers
    two_judge_gate = False
    for s in wf.steps:
        if s.type == "gate" and s.gate_config:
            judge_provs = {
                _provider(st.get("config", {}).get("model"))
                for st in s.gate_config.strategies
                if st.get("type") == "llm_eval"
            }
            if len({p for p in judge_provs if p}) >= 2:
                two_judge_gate = True

    # (d) deterministic code decision guarded by a human (gate-human or approval)
    has_code = any(s.type == "code" for s in wf.steps)
    has_human = any(s.type == "approval" for s in wf.steps) or any(
        s.type == "gate"
        and s.gate_config
        and any(st.get("type") == "human" for st in s.gate_config.strategies)
        for s in wf.steps
    )
    deterministic_guarded = has_code and has_human

    assert cross_provider_race or consensus or two_judge_gate or deterministic_guarded, (
        f"{template_name} demonstrates no model-independence pattern "
        f"(race={cross_provider_race}, consensus={consensus}, "
        f"two_judge={two_judge_gate}, det_guarded={deterministic_guarded})"
    )


def test_new_step_patterns_present_across_wave() -> None:
    """The wave as a whole must introduce the parse and sub_workflow step types and a
    genuine multi-provider consensus (vote steps on >= 3 distinct providers)."""
    types_seen: set[str] = set()
    max_distinct_vote_providers = 0
    for name in WAVE2_TEMPLATES:
        wf = parse_yaml_string(_load(name))
        types_seen |= {s.type for s in wf.steps}
        provs = {
            _provider(s.model)
            for s in wf.steps
            if s.type in ("standard", "llm") and getattr(s, "model", None)
        }
        max_distinct_vote_providers = max(
            max_distinct_vote_providers, len({p for p in provs if p})
        )
    assert "parse" in types_seen, "wave 2 should introduce the parse step type"
    assert "sub_workflow" in types_seen, "wave 2 should introduce sub_workflow composition"
    assert max_distinct_vote_providers >= 3, (
        "wave 2 should include a 3-provider consensus"
    )


def test_templates_are_discovered() -> None:
    by_file = {t.file_name: t for t in list_templates()}
    for name in WAVE2_TEMPLATES:
        info = by_file.get(f"{name}.yaml")
        assert info is not None, f"{name} not discovered"
        assert info.source == "built-in"
        assert info.step_count > 0
