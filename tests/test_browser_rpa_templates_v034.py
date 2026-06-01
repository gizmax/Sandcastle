"""Structural + safety validation for the v0.34 browser/computer-use RPA templates.

This wave moves Sandcastle past API-based workflows into work that only exists inside a
logged-in browser. The checks below add RPA-specific guarantees on top of the usual
structural ones: every template drives a real browser step; the reasoning stays
provider-neutral (playwright/dom modes, no computer_use lock-in in this build-first
set); browser steps load secrets via credentials_env, never inline; and any template
with a human approval gate positions it so a real browser/loop action runs after it
(the irreversible click is gated), including loop-orchestrated writes.
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
    "synthetic_signup_journey_sentinel",
    "act_then_sensor_verify_submission_skeleton",
    "supplier_portal_invoice_status_reconciler",
    "saas_seat_deactivation_offboarder",
    "browser_flow_trajectory_replay_harness",
    "web_portal_supplier_onboarding_filer",
]

NEUTRAL_BROWSER_MODES = {"playwright", "dom", "lightpanda", "browserbase"}
LIGHTWEIGHT_READ_MODES = {"dom", "lightpanda"}

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "src" / "sandcastle" / "templates"


def _load(name: str) -> str:
    path = TEMPLATES_DIR / f"{name}.yaml"
    assert path.exists(), f"Template file not found: {path}"
    return path.read_text()


def _downstream_of(target_ids: set[str], wf) -> set[str]:
    """All step ids that run after any step in target_ids, following both depends_on
    edges and loop orchestration (a loop's step_ids run when the loop runs)."""
    by_id = {s.id: s for s in wf.steps}
    # forward edges: dep -> step (depends_on), and loop -> its child step_ids
    succ: dict[str, set[str]] = {s.id: set() for s in wf.steps}
    for s in wf.steps:
        for d in s.depends_on:
            if d in succ:
                succ[d].add(s.id)
        if s.loop_config:
            for child in s.loop_config.step_ids:
                if child in by_id:
                    succ[s.id].add(child)
    seen: set[str] = set()
    stack = [t for t in target_ids]
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
        assert step.type in VALID_STEP_TYPES, (
            f"{template_name}/{step.id} invalid type {step.type!r}"
        )


@pytest.mark.parametrize("template_name", RPA_TEMPLATES)
def test_depends_on_resolve(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    ids = {s.id for s in wf.steps}
    for step in wf.steps:
        for dep in step.depends_on:
            assert dep in ids, f"{template_name}/{step.id} depends on unknown {dep!r}"


@pytest.mark.parametrize("template_name", RPA_TEMPLATES)
def test_validates_clean(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    assert validate(wf) == [], f"{template_name} did not validate"


@pytest.mark.parametrize("template_name", RPA_TEMPLATES)
def test_build_plan_covers_every_step_once(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    plan = build_plan(wf)
    planned = [sid for stage in plan.stages for sid in stage]
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
    assert any(s.type == "browser" for s in wf.steps), (
        f"{template_name} is an RPA template but has no browser step"
    )


@pytest.mark.parametrize("template_name", RPA_TEMPLATES)
def test_browser_modes_are_provider_neutral(template_name: str) -> None:
    """This build-first set must avoid computer_use (the one vendor-coupled mode): the
    reasoning model only authors a Playwright script / DOM read, so it stays swappable."""
    wf = parse_yaml_string(_load(template_name))
    for step in wf.steps:
        if step.type == "browser" and step.browser_config:
            assert step.browser_config.mode in NEUTRAL_BROWSER_MODES, (
                f"{template_name}/{step.id} uses non-neutral browser mode "
                f"{step.browser_config.mode!r}"
            )


@pytest.mark.parametrize("template_name", RPA_TEMPLATES)
def test_browser_steps_handle_secrets_safely(template_name: str) -> None:
    """A browser step either authenticates via credentials_env (never inline secrets) or
    is a lightweight unauthenticated read (dom/lightpanda)."""
    wf = parse_yaml_string(_load(template_name))
    for step in wf.steps:
        if step.type == "browser" and step.browser_config:
            bc = step.browser_config
            assert bc.credentials_env or bc.mode in LIGHTWEIGHT_READ_MODES, (
                f"{template_name}/{step.id} browser step has no credentials_env and is "
                f"not a lightweight read mode"
            )


@pytest.mark.parametrize("template_name", RPA_TEMPLATES)
def test_approval_gates_a_downstream_action(template_name: str) -> None:
    """If a template has a human approval, it must actually gate downstream work: a
    browser step or a loop runs after the approval (the irreversible click is gated),
    including loop-orchestrated writes. Read-only templates legitimately have none."""
    wf = parse_yaml_string(_load(template_name))
    approval_ids = {s.id for s in wf.steps if s.type == "approval"}
    if not approval_ids:
        pytest.skip("read-only template - no approval gate required")
    downstream = _downstream_of(approval_ids, wf)
    gated_action = any(
        s.id in downstream and (s.type in ("browser", "loop", "http"))
        for s in wf.steps
    )
    assert gated_action, (
        f"{template_name} has an approval that gates no downstream browser/loop/http action"
    )


@pytest.mark.parametrize("template_name", RPA_TEMPLATES)
def test_code_steps_pass_engine_sandbox_blocklist(template_name: str) -> None:
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


def test_wave_exercises_last_unused_step_types() -> None:
    """The wave as a whole must finally exercise the browser-family step types the engine
    shipped but no template used end to end: browser, trajectory-replay, delegate."""
    types_seen: set[str] = set()
    for name in RPA_TEMPLATES:
        wf = parse_yaml_string(_load(name))
        types_seen |= {s.type for s in wf.steps}
    for needed in ("browser", "trajectory-replay", "delegate"):
        assert needed in types_seen, f"wave should exercise the {needed} step type"


@pytest.mark.parametrize("template_name", RPA_TEMPLATES)
def test_templates_are_discovered(template_name: str) -> None:
    by_file = {t.file_name: t for t in list_templates()}
    info = by_file.get(f"{template_name}.yaml")
    assert info is not None and info.source == "built-in" and info.step_count > 0
