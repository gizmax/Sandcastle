"""Validation for the v0.34 Template Hub category expansion.

Adds four NEW categories to the built-in catalog (compliance_grc, healthcare,
fintech_banking, llmops_ai_eng) and keeps the public web hub registry in sync. The
template checks mirror the model-neutral suite (parse, validate, build-plan coverage,
control-flow references, code-sandbox blocklist) and assert each template carries its new
category. A registry check guards the drift invariant the generator was built to fix:
site/hub/registry.json categories[] counts must equal the actual per-category counts in
templates[], so the public category bar can never silently fall out of step again.
"""

from __future__ import annotations

import ast
import json
from collections import Counter
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

HUB_TEMPLATES = {
    "compliance_grc": [
        "control_continuous_drift_sentinel", "ropa_data_mapping_builder",
        "policy_attestation_campaign_runner", "vendor_risk_continuous_monitor",
        "audit_evidence_request_responder",
    ],
    "healthcare": [
        "prior_authorization_packet_builder", "clinical_denial_appeal_generator",
        "hipaa_phi_leak_auditor", "patient_intake_eligibility_verifier",
    ],
    "fintech_banking": [
        "kyc_aml_sanctions_screener", "adverse_action_credit_decision",
        "transaction_fraud_triage_sar_router", "reg_e_dispute_provisional_credit_engine",
    ],
    "llmops_ai_eng": [
        "llm_golden_set_regression_gate", "prompt_optimization_loop",
        "rag_retrieval_eval_harness", "groundedness_guardrail_gate",
        "agent_trajectory_eval_harness", "model_routing_policy_compiler",
    ],
}
ALL = [(cat, name) for cat, names in HUB_TEMPLATES.items() for name in names]

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "src" / "sandcastle" / "templates"
REGISTRY = ROOT / "site" / "hub" / "registry.json"


def _load(name: str) -> str:
    path = TEMPLATES_DIR / f"{name}.yaml"
    assert path.exists(), f"Template file not found: {path}"
    return path.read_text()


def _header_category(text: str) -> str | None:
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("#"):
            break
        s = s.lstrip("#").strip()
        if s.startswith("category:"):
            return s.split(":", 1)[1].strip()
    return None


@pytest.mark.parametrize("cat,name", ALL)
def test_parses_validates_and_plans(cat: str, name: str) -> None:
    wf = parse_yaml_string(_load(name))
    assert wf.name and len(wf.steps) > 0
    assert validate(wf) == [], f"{name} did not validate"
    planned = [sid for stage in build_plan(wf).stages for sid in stage]
    assert len(planned) == len(set(planned)) and set(planned) == {s.id for s in wf.steps}


@pytest.mark.parametrize("cat,name", ALL)
def test_step_types_and_refs(cat: str, name: str) -> None:
    wf = parse_yaml_string(_load(name))
    ids = {s.id for s in wf.steps}
    for step in wf.steps:
        assert step.type in VALID_STEP_TYPES, f"{name}/{step.id} bad type {step.type!r}"
        for dep in step.depends_on:
            assert dep in ids, f"{name}/{step.id} depends on unknown {dep!r}"
        if step.tool_config and step.tool_config.tool:
            assert step.tool_config.tool.split(":")[0] in KNOWN_TOOLS
        if step.condition_config:
            for sid in step.condition_config.then_steps + step.condition_config.else_steps:
                assert sid in ids
        if step.loop_config:
            for sid in step.loop_config.step_ids:
                assert sid in ids
        if step.race_config:
            for branch in step.race_config.branches:
                for sid in branch:
                    assert sid in ids


@pytest.mark.parametrize("cat,name", ALL)
def test_carries_new_category(cat: str, name: str) -> None:
    assert _header_category(_load(name)) == cat, f"{name} should be category {cat}"


@pytest.mark.parametrize("cat,name", ALL)
def test_code_steps_pass_sandbox_blocklist(cat: str, name: str) -> None:
    wf = parse_yaml_string(_load(name))
    for step in wf.steps:
        if step.type == "code" and step.code_config and step.code_config.code:
            code = step.code_config.code
            match = _CODE_STEP_BLOCKED_PATTERNS.search(code)
            assert match is None, f"{name}/{step.id} sandbox-blocked {match.group(0)!r}"
            ast.parse(code)


@pytest.mark.parametrize("cat,name", ALL)
def test_discovered_in_catalog(cat: str, name: str) -> None:
    by_file = {t.file_name: t for t in list_templates()}
    info = by_file.get(f"{name}.yaml")
    assert info is not None and info.category == cat and info.step_count > 0


def test_new_categories_span_four() -> None:
    cats = {_header_category(_load(n)) for _, n in ALL}
    assert cats == {"compliance_grc", "healthcare", "fintech_banking", "llmops_ai_eng"}


def test_web_hub_registry_categories_are_derived_not_drifted() -> None:
    """The generator's invariant: registry categories[] counts must equal the actual
    per-category counts in templates[], so the public category bar never drifts."""
    reg = json.loads(REGISTRY.read_text())
    template_counts = Counter(t.get("category", "general_ai") for t in reg["templates"])
    bar = {c["id"]: c["count"] for c in reg["categories"]}
    assert bar == dict(template_counts), (
        "registry categories[] is out of sync with templates[] - run "
        "scripts/update_hub_registry.py"
    )
    # The four new categories surface in the public hub with real templates.
    for cat in HUB_TEMPLATES:
        assert bar.get(cat, 0) >= len(HUB_TEMPLATES[cat]), f"{cat} missing from web hub bar"
    assert reg["stats"]["total_templates"] == len(reg["templates"])
