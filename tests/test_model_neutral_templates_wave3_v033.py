"""Structural + thesis + code-safety validation for the v0.33 model-independent
templates, wave 3 (the remaining strong ideas from the ideation sweep).

In addition to the wave-1/2 structural and thesis checks, this module adds a
code-safety regression guard across ALL model-neutral templates: the engine's code
sandbox rejects a set of patterns (re.compile(, subprocess, eval(, open(, getattr(,
chr(, ...) anywhere in a code step - even in comments - so a template that parses and
validates can still fail at runtime. Every ``code`` step is scanned against the real
engine blocklist and ast-parsed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from sandcastle.engine.dag import (
    VALID_STEP_TYPES,
    build_plan,
    parse_yaml_string,
    validate,
)
from sandcastle.engine.executor import _CODE_STEP_BLOCKED_PATTERNS
from sandcastle.engine.tools.registry import KNOWN_TOOLS
from sandcastle.templates import list_templates

WAVE3_TEMPLATES = [
    "ticket_resolution_autopilot",
    "soc2_evidence_collector_continuous",
    "inbound_lead_sla_router",
    "deal_desk_discount_approval_gate",
    "nda_autopilot_intake_to_countersign",
    "lead_dedup_merge_warden",
    "bank_reconciliation_closer",
    "dependency_upgrade_pilot",
    "flaky_test_hunter",
    "churn_save_play_orchestrator",
    "vendor_security_questionnaire_autoresponder",
    "dsar_fulfillment_deadline_engine",
    "dependency_outage_failover_router",
    "canary_promotion_sentinel",
    "warehouse_to_narrative_briefing",
    "records_retention_disposition_orchestrator",
    "csat_coaching_loop",
    "proactive_outage_comms_commander",
    "dunning_escalation_orchestrator",
    "cloud_cost_guardrail_enforcer",
    "error_budget_freeze_controller",
    "reverse_etl_validation_gate",
    "cross_store_reconciliation_auditor",
    "model_promotion_gate",
]

# Every model-neutral template shipped across the three waves - the code-safety guard
# runs over all of them so a future edit cannot reintroduce a sandbox-blocked pattern.
ALL_MODEL_NEUTRAL_TEMPLATES = [
    "closed_loop_autoremediator", "quote_to_cash_orchestrator",
    "refund_credit_approval_workflow", "access_review_campaign_orchestrator",
    "cost_tiered_escalation_router", "three_way_match_pay_run",
    "provider_consensus_decision_engine", "warehouse_data_quality_sentinel",
    "vuln_triage_reachability_gate", "sla_sensor_escalation_ladder",
    "map_reduce_over_list", *WAVE3_TEMPLATES,
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
    return model.split("/")[0] if model else None


@pytest.mark.parametrize("template_name", WAVE3_TEMPLATES)
def test_parses_with_steps(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    assert wf is not None and wf.name and len(wf.steps) > 0


@pytest.mark.parametrize("template_name", WAVE3_TEMPLATES)
def test_step_types_valid(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    for step in wf.steps:
        assert step.type in VALID_STEP_TYPES, (
            f"{template_name}/{step.id} invalid type {step.type!r}"
        )


@pytest.mark.parametrize("template_name", WAVE3_TEMPLATES)
def test_depends_on_resolve(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    ids = {s.id for s in wf.steps}
    for step in wf.steps:
        for dep in step.depends_on:
            assert dep in ids, f"{template_name}/{step.id} depends on unknown {dep!r}"


@pytest.mark.parametrize("template_name", WAVE3_TEMPLATES)
def test_validates_clean(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    errors = validate(wf)
    assert errors == [], f"{template_name} validate() errors: {errors}"


@pytest.mark.parametrize("template_name", WAVE3_TEMPLATES)
def test_build_plan_covers_every_step_once(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    plan = build_plan(wf)
    assert plan is not None and len(plan.stages) > 0
    planned = [sid for stage in plan.stages for sid in stage]
    assert len(planned) == len(set(planned)), f"{template_name}: a step is planned twice"
    assert set(planned) == {s.id for s in wf.steps}


@pytest.mark.parametrize("template_name", WAVE3_TEMPLATES)
def test_tool_references_known(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    for step in wf.steps:
        if step.tool_config and step.tool_config.tool:
            base = step.tool_config.tool.split(":")[0]
            assert base in KNOWN_TOOLS, f"{template_name}/{step.id} unknown tool {base!r}"


@pytest.mark.parametrize("template_name", WAVE3_TEMPLATES)
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


@pytest.mark.parametrize("template_name", WAVE3_TEMPLATES)
def test_exercises_engine_step_types(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    overlap = {s.type for s in wf.steps} & ENGINE_STEP_TYPES
    assert len(overlap) >= 4, (
        f"{template_name} only exercises {sorted(overlap)} engine step types"
    )


@pytest.mark.parametrize("template_name", WAVE3_TEMPLATES)
def test_demonstrates_model_independence(template_name: str) -> None:
    """Each template proves model-independence in at least one legitimate way:
    cross-provider race, multi-provider consensus, a two-judge gate, a deterministic
    code decision guarded by a human, or minimal-model (the only generative step is a
    swappable classifier over a deterministic http/code spine).
    """
    wf = parse_yaml_string(_load(template_name))
    by_id = {s.id: s for s in wf.steps}

    cross_provider_race = any(
        s.type == "race"
        and s.race_config
        and s.race_config.validator
        and len({
            _provider(by_id[sid].model)
            for branch in s.race_config.branches
            for sid in branch
            if sid in by_id and by_id[sid].model
        } - {None}) >= 2
        for s in wf.steps
    )

    vote_provs = {
        _provider(s.model)
        for s in wf.steps
        if s.type in ("standard", "llm") and getattr(s, "model", None)
    } - {None}
    consensus = len(vote_provs) >= 2

    two_judge_gate = any(
        s.type == "gate"
        and s.gate_config
        and len({
            _provider(st.get("config", {}).get("model"))
            for st in s.gate_config.strategies
            if st.get("type") == "llm_eval"
        } - {None}) >= 2
        for s in wf.steps
    )

    has_code = any(s.type == "code" for s in wf.steps)
    has_human = any(s.type == "approval" for s in wf.steps) or any(
        s.type == "gate" and s.gate_config
        and any(st.get("type") == "human" for st in s.gate_config.strategies)
        for s in wf.steps
    )
    deterministic_guarded = has_code and has_human

    # minimal-model: no free-form standard/llm decision step; the model is confined to
    # swappable classify/judge steps over a deterministic http+code spine.
    has_freeform_llm = any(s.type in ("standard", "llm") for s in wf.steps)
    has_http = any(s.type == "http" for s in wf.steps)
    minimal_model = (not has_freeform_llm) and has_code and has_http

    assert (
        cross_provider_race or consensus or two_judge_gate
        or deterministic_guarded or minimal_model
    ), (
        f"{template_name} demonstrates no model-independence pattern "
        f"(race={cross_provider_race}, consensus={consensus}, two_judge={two_judge_gate}, "
        f"det_guarded={deterministic_guarded}, minimal_model={minimal_model})"
    )


@pytest.mark.parametrize("template_name", ALL_MODEL_NEUTRAL_TEMPLATES)
def test_code_steps_pass_engine_sandbox_blocklist(template_name: str) -> None:
    """Regression guard: the engine code sandbox rejects re.compile(, subprocess,
    eval(, open(, getattr(, chr(, ... anywhere in a code step (comments included), so
    a template that parses can still fail at runtime. Every code step must be free of
    blocked patterns and be syntactically valid Python."""
    wf = parse_yaml_string(_load(template_name))
    for step in wf.steps:
        if step.type == "code" and step.code_config and step.code_config.code:
            code = step.code_config.code
            match = _CODE_STEP_BLOCKED_PATTERNS.search(code)
            assert match is None, (
                f"{template_name}/{step.id} code step contains the sandbox-blocked "
                f"pattern {match.group(0)!r} (would be rejected at runtime)"
            )
            try:
                ast.parse(code)
            except SyntaxError as exc:  # pragma: no cover - defensive
                pytest.fail(f"{template_name}/{step.id} code is not valid Python: {exc}")


def test_wave_introduces_breadth() -> None:
    """The wave as a whole should span many domains and exercise diverse patterns."""
    # Resolve the catalog once - calling list_templates() per template parses the whole
    # 170+ template catalog each time and times out the test.
    by_file = {t.file_name: t for t in list_templates()}
    categories: set[str] = set()
    types_seen: set[str] = set()
    for name in WAVE3_TEMPLATES:
        wf = parse_yaml_string(_load(name))
        types_seen |= {s.type for s in wf.steps}
        info = by_file.get(f"{name}.yaml")
        if info and info.category:
            categories.add(info.category)
    assert len(categories) >= 5, f"wave 3 should span >= 5 categories, got {sorted(categories)}"
    assert {"sensor", "loop", "race", "gate", "classify"} <= types_seen


@pytest.mark.parametrize("template_name", WAVE3_TEMPLATES)
def test_templates_are_discovered(template_name: str) -> None:
    by_file = {t.file_name: t for t in list_templates()}
    info = by_file.get(f"{template_name}.yaml")
    assert info is not None and info.source == "built-in" and info.step_count > 0
