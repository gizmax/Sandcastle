"""Structural + safety validation for the v0.34 browser-RPA templates, batch 2.

Same RPA guarantees as the first browser wave, over the remaining strong ideas: every
template drives a real browser; modes are provider-neutral (playwright/dom, no
computer_use lock-in); secrets load via credentials_env; and the irreversible write is
gated. The write-gate check here is precise: a template only needs an approval when it
actually writes (2+ browser steps, i.e. a login/fill followed by a submit, or a browser
inside a loop), and the final write browser - taking a loop child's effective stage as
its loop's stage - must run downstream of an approval (following depends_on and loop
orchestration). Read-only templates (a single browser read) legitimately have no gate.
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

RPA_TEMPLATES = [
    "state_sales_tax_efile_filer", "gov_portal_filing_status_puller",
    "saas_admin_console_seat_audit", "saas_role_permission_remediator",
    "vendor_invoice_portal_harvester", "bank_statement_vault_reconciler",
    "saas_admin_seat_deprovisioning_offboard", "insurance_claim_fnol_portal_filer",
    "partner_portal_journey_monitor", "synthetic_checkout_revenue_canary",
    "golden_journey_regression_gate", "portal_login_2fa_extract_skeleton",
    "workers_comp_fnol_insurance_filer", "saas_config_setting_propagator",
    "tax_form_1099_w2_collector", "utility_municipal_bill_filer",
    "saas_api_key_rotation_runner", "government_procurement_bid_submitter",
    "kyc_onboarding_portal_filer", "permit_eportal_application_filer",
    "consent_banner_cmp_compliance_auditor", "legacy_erp_order_status_sync",
    "legacy_admin_report_export_to_warehouse", "wcag_accessibility_audit_evidence_pack",
    "race_two_models_author_scrape_skeleton", "ats_job_application_autosubmit",
    "onpage_claim_and_price_verifier_vs_source_of_truth",
    "osha_environmental_compliance_report_filer",
]

# Templates that perform an irreversible external write (submit / pay / deactivate /
# rotate). Each was verified to gate that write behind a human approval. Read-only
# templates (status pulls, harvests, synthetic monitors, scrape/login skeletons) are not
# listed - they have no irreversible action to gate.
WRITE_TEMPLATES = [
    "state_sales_tax_efile_filer", "saas_admin_console_seat_audit",
    "saas_role_permission_remediator", "vendor_invoice_portal_harvester",
    "insurance_claim_fnol_portal_filer", "saas_admin_seat_deprovisioning_offboard",
    "workers_comp_fnol_insurance_filer", "saas_config_setting_propagator",
    "utility_municipal_bill_filer", "saas_api_key_rotation_runner",
    "government_procurement_bid_submitter", "kyc_onboarding_portal_filer",
    "permit_eportal_application_filer", "ats_job_application_autosubmit",
    "osha_environmental_compliance_report_filer", "legacy_erp_order_status_sync",
]

NEUTRAL_BROWSER_MODES = {"playwright", "dom", "lightpanda", "browserbase"}
LIGHTWEIGHT_READ_MODES = {"dom", "lightpanda"}

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "src" / "sandcastle" / "templates"


def _load(name: str) -> str:
    path = TEMPLATES_DIR / f"{name}.yaml"
    assert path.exists(), f"Template file not found: {path}"
    return path.read_text()


def _forward_reaches(seed: set[str], wf) -> set[str]:
    """Step ids reachable forward from seed via depends_on and loop orchestration."""
    succ: dict[str, set[str]] = {s.id: set() for s in wf.steps}
    for s in wf.steps:
        for d in s.depends_on:
            if d in succ:
                succ[d].add(s.id)
        if s.loop_config:
            for child in s.loop_config.step_ids:
                if child in succ:
                    succ[s.id].add(child)
    seen, stack = set(), list(seed)
    while stack:
        cur = stack.pop()
        for nxt in succ.get(cur, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


@pytest.mark.parametrize("template_name", RPA_TEMPLATES)
def test_parses_with_steps(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    assert wf is not None and wf.name and len(wf.steps) > 0


@pytest.mark.parametrize("template_name", RPA_TEMPLATES)
def test_step_types_valid(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    for step in wf.steps:
        assert step.type in VALID_STEP_TYPES, f"{template_name}/{step.id} bad type {step.type!r}"


@pytest.mark.parametrize("template_name", RPA_TEMPLATES)
def test_depends_on_resolve(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    ids = {s.id for s in wf.steps}
    for step in wf.steps:
        for dep in step.depends_on:
            assert dep in ids, f"{template_name}/{step.id} depends on unknown {dep!r}"


@pytest.mark.parametrize("template_name", RPA_TEMPLATES)
def test_validates_clean(template_name: str) -> None:
    assert validate(parse_yaml_string(_load(template_name))) == []


@pytest.mark.parametrize("template_name", RPA_TEMPLATES)
def test_build_plan_covers_every_step_once(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    planned = [sid for stage in build_plan(wf).stages for sid in stage]
    assert len(planned) == len(set(planned)) and set(planned) == {s.id for s in wf.steps}


@pytest.mark.parametrize("template_name", RPA_TEMPLATES)
def test_tool_references_known(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    for step in wf.steps:
        if step.tool_config and step.tool_config.tool:
            assert step.tool_config.tool.split(":")[0] in KNOWN_TOOLS


@pytest.mark.parametrize("template_name", RPA_TEMPLATES)
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


@pytest.mark.parametrize("template_name", RPA_TEMPLATES)
def test_drives_a_browser(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    assert any(s.type == "browser" for s in wf.steps), f"{template_name} has no browser step"


@pytest.mark.parametrize("template_name", RPA_TEMPLATES)
def test_browser_modes_are_provider_neutral(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    for step in wf.steps:
        if step.type == "browser" and step.browser_config:
            assert step.browser_config.mode in NEUTRAL_BROWSER_MODES, (
                f"{template_name}/{step.id} non-neutral browser mode "
                f"{step.browser_config.mode!r} (no computer_use in this set)"
            )


@pytest.mark.parametrize("template_name", RPA_TEMPLATES)
def test_browser_steps_handle_secrets_safely(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    for step in wf.steps:
        if step.type == "browser" and step.browser_config:
            bc = step.browser_config
            assert bc.credentials_env or bc.mode in LIGHTWEIGHT_READ_MODES, (
                f"{template_name}/{step.id} browser has no credentials_env and is not a read mode"
            )


@pytest.mark.parametrize("template_name", WRITE_TEMPLATES)
def test_irreversible_write_is_gated(template_name: str) -> None:
    """For every template that performs an irreversible external write, the final write
    browser (a loop child counted at its loop's stage) must run downstream of a human
    approval, following depends_on and loop orchestration. This is the core RPA safety
    property: no submit/pay/deactivate without a human in the loop."""
    wf = parse_yaml_string(_load(template_name))
    plan = build_plan(wf)
    stage_of = {sid: i for i, stage in enumerate(plan.stages) for sid in stage}
    loop_parent = {
        child: s.id
        for s in wf.steps if s.loop_config
        for child in s.loop_config.step_ids
    }
    browsers = [s for s in wf.steps if s.type == "browser"]
    assert browsers, f"{template_name} is a write template with no browser step"

    def eff_stage(sid: str) -> int:
        return stage_of.get(loop_parent.get(sid, sid), stage_of.get(sid, -1))

    write_browser = max(browsers, key=lambda b: eff_stage(b.id))
    approvals = {s.id for s in wf.steps if s.type == "approval"}
    assert approvals, f"{template_name} writes via browser but has no approval gate"
    assert write_browser.id in _forward_reaches(approvals, wf), (
        f"{template_name}: write browser {write_browser.id!r} is not downstream of an approval"
    )


@pytest.mark.parametrize("template_name", RPA_TEMPLATES)
def test_code_steps_pass_engine_sandbox_blocklist(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    for step in wf.steps:
        if step.type == "code" and step.code_config and step.code_config.code:
            code = step.code_config.code
            match = _CODE_STEP_BLOCKED_PATTERNS.search(code)
            assert match is None, (
                f"{template_name}/{step.id} sandbox-blocked pattern {match.group(0)!r}"
            )
            ast.parse(code)


@pytest.mark.parametrize("template_name", RPA_TEMPLATES)
def test_templates_are_discovered(template_name: str) -> None:
    by_file = {t.file_name: t for t in list_templates()}
    info = by_file.get(f"{template_name}.yaml")
    assert info is not None and info.source == "built-in" and info.step_count > 0
