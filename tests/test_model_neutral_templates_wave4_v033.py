"""Structural + thesis + code-safety validation for the v0.33 model-independent
templates, wave 4 (the final, lower-ranked ideas from the ideation sweep).

Adds a sixth model-independence pattern over wave 3: deterministic-dominant, where a
deterministic ``code`` step computes the decision and a ``condition`` routes the action
on that computed value (e.g. revenue_leakage_detector gates ticket filing on a
code-computed recoverable total). The code-sandbox regression guard now spans every
model-neutral template across all four waves.
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

WAVE4_TEMPLATES = [
    "residency_routed_inference",
    "eu_ai_act_risk_register",
    "policy_change_propagation",
    "incident_rca_pr_closer",
    "schema_drift_early_warning",
    "fastest_of_n_failover_responder",
    "revenue_leakage_detector",
    "pr_review_polyglot_router",
]

WAVE1 = [
    "closed_loop_autoremediator", "quote_to_cash_orchestrator",
    "refund_credit_approval_workflow", "access_review_campaign_orchestrator",
    "cost_tiered_escalation_router",
]
WAVE2 = [
    "three_way_match_pay_run", "provider_consensus_decision_engine",
    "warehouse_data_quality_sentinel", "vuln_triage_reachability_gate",
    "sla_sensor_escalation_ladder", "map_reduce_over_list",
]
WAVE3 = [
    "ticket_resolution_autopilot", "soc2_evidence_collector_continuous",
    "inbound_lead_sla_router", "deal_desk_discount_approval_gate",
    "nda_autopilot_intake_to_countersign", "lead_dedup_merge_warden",
    "bank_reconciliation_closer", "dependency_upgrade_pilot", "flaky_test_hunter",
    "churn_save_play_orchestrator", "vendor_security_questionnaire_autoresponder",
    "dsar_fulfillment_deadline_engine", "dependency_outage_failover_router",
    "canary_promotion_sentinel", "warehouse_to_narrative_briefing",
    "records_retention_disposition_orchestrator", "csat_coaching_loop",
    "proactive_outage_comms_commander", "dunning_escalation_orchestrator",
    "cloud_cost_guardrail_enforcer", "error_budget_freeze_controller",
    "reverse_etl_validation_gate", "cross_store_reconciliation_auditor",
    "model_promotion_gate",
]
ALL_MODEL_NEUTRAL_TEMPLATES = [*WAVE1, *WAVE2, *WAVE3, *WAVE4_TEMPLATES]

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


@pytest.mark.parametrize("template_name", WAVE4_TEMPLATES)
def test_parses_with_steps(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    assert wf is not None and wf.name and len(wf.steps) > 0


@pytest.mark.parametrize("template_name", WAVE4_TEMPLATES)
def test_step_types_valid(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    for step in wf.steps:
        assert step.type in VALID_STEP_TYPES, (
            f"{template_name}/{step.id} invalid type {step.type!r}"
        )


@pytest.mark.parametrize("template_name", WAVE4_TEMPLATES)
def test_depends_on_resolve(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    ids = {s.id for s in wf.steps}
    for step in wf.steps:
        for dep in step.depends_on:
            assert dep in ids, f"{template_name}/{step.id} depends on unknown {dep!r}"


@pytest.mark.parametrize("template_name", WAVE4_TEMPLATES)
def test_validates_clean(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    assert validate(wf) == [], f"{template_name} did not validate"


@pytest.mark.parametrize("template_name", WAVE4_TEMPLATES)
def test_build_plan_covers_every_step_once(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    plan = build_plan(wf)
    planned = [sid for stage in plan.stages for sid in stage]
    assert len(planned) == len(set(planned)) and set(planned) == {s.id for s in wf.steps}


@pytest.mark.parametrize("template_name", WAVE4_TEMPLATES)
def test_tool_references_known(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    for step in wf.steps:
        if step.tool_config and step.tool_config.tool:
            assert step.tool_config.tool.split(":")[0] in KNOWN_TOOLS


@pytest.mark.parametrize("template_name", WAVE4_TEMPLATES)
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


@pytest.mark.parametrize("template_name", WAVE4_TEMPLATES)
def test_exercises_engine_step_types(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    overlap = {s.type for s in wf.steps} & ENGINE_STEP_TYPES
    assert len(overlap) >= 4, f"{template_name} exercises only {sorted(overlap)}"


@pytest.mark.parametrize("template_name", WAVE4_TEMPLATES)
def test_demonstrates_model_independence(template_name: str) -> None:
    """Six accepted patterns: cross-provider race, multi-provider consensus, two-judge
    gate, deterministic code guarded by a human, minimal-model, or deterministic-dominant
    (a code step computes the decision and a condition routes the action on it)."""
    wf = parse_yaml_string(_load(template_name))
    by_id = {s.id: s for s in wf.steps}

    cross_provider_race = any(
        s.type == "race" and s.race_config and s.race_config.validator
        and len({
            _provider(by_id[sid].model)
            for branch in s.race_config.branches for sid in branch
            if sid in by_id and by_id[sid].model
        } - {None}) >= 2
        for s in wf.steps
    )
    vote_provs = {
        _provider(s.model) for s in wf.steps
        if s.type in ("standard", "llm") and getattr(s, "model", None)
    } - {None}
    consensus = len(vote_provs) >= 2
    two_judge_gate = any(
        s.type == "gate" and s.gate_config
        and len({
            _provider(st.get("config", {}).get("model"))
            for st in s.gate_config.strategies if st.get("type") == "llm_eval"
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
    minimal_model = (
        not any(s.type in ("standard", "llm") for s in wf.steps)
        and has_code and any(s.type == "http" for s in wf.steps)
    )
    # deterministic-dominant: a code step computes the decision and a condition routes
    # the action on that computed value, so the model is never load-bearing.
    code_ids = {s.id for s in wf.steps if s.type == "code"}
    deterministic_dominant = bool(code_ids) and any(
        s.condition_config and any(cid in s.condition_config.expression for cid in code_ids)
        for s in wf.steps
    )

    assert (
        cross_provider_race or consensus or two_judge_gate
        or deterministic_guarded or minimal_model or deterministic_dominant
    ), (
        f"{template_name} shows no model-independence pattern "
        f"(race={cross_provider_race}, consensus={consensus}, two_judge={two_judge_gate}, "
        f"det_guarded={deterministic_guarded}, minimal={minimal_model}, "
        f"det_dominant={deterministic_dominant})"
    )


@pytest.mark.parametrize("template_name", ALL_MODEL_NEUTRAL_TEMPLATES)
def test_code_steps_pass_engine_sandbox_blocklist(template_name: str) -> None:
    """Regression guard across all four waves: no code step may contain a pattern the
    engine sandbox rejects at runtime (re.compile(, subprocess, eval(, open(, ...)."""
    wf = parse_yaml_string(_load(template_name))
    for step in wf.steps:
        if step.type == "code" and step.code_config and step.code_config.code:
            code = step.code_config.code
            match = _CODE_STEP_BLOCKED_PATTERNS.search(code)
            assert match is None, (
                f"{template_name}/{step.id} contains sandbox-blocked pattern "
                f"{match.group(0)!r} (would fail at runtime)"
            )
            ast.parse(code)


@pytest.mark.parametrize("template_name", WAVE4_TEMPLATES)
def test_templates_are_discovered(template_name: str) -> None:
    by_file = {t.file_name: t for t in list_templates()}
    info = by_file.get(f"{template_name}.yaml")
    assert info is not None and info.source == "built-in" and info.step_count > 0
