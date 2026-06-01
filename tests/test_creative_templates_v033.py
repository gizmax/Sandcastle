"""Structural validation for the v0.33 creative use-case templates.

These three templates fork the proven UGC Studio skeleton
(image-gen -> vision-judge -> self-heal) into demoable, user-attracting
verticals. Each is checked the same way the top-20 e2e suite checks core
templates: it parses, every step type is recognized, depends_on references
resolve, the DAG is acyclic, every tool reference is a real registry entry,
and the build plan covers every step exactly once. A few template-specific
assertions guard the backend router and the self-healing chain.
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

CREATIVE_TEMPLATES = [
    "action_figure_me",
    "glow_up_restore",
    "ad_creative_factory",
]

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "src" / "sandcastle" / "templates"


def _load(name: str) -> str:
    path = TEMPLATES_DIR / f"{name}.yaml"
    assert path.exists(), f"Template file not found: {path}"
    return path.read_text()


@pytest.mark.parametrize("template_name", CREATIVE_TEMPLATES)
def test_parses_with_steps(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    assert wf is not None
    assert wf.name, f"{template_name} has no name"
    assert len(wf.steps) > 0, f"{template_name} has no steps"


@pytest.mark.parametrize("template_name", CREATIVE_TEMPLATES)
def test_step_types_valid(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    for step in wf.steps:
        assert step.type in VALID_STEP_TYPES, (
            f"{template_name}/{step.id} invalid type {step.type!r}"
        )


@pytest.mark.parametrize("template_name", CREATIVE_TEMPLATES)
def test_depends_on_resolve(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    ids = {s.id for s in wf.steps}
    for step in wf.steps:
        for dep in step.depends_on:
            assert dep in ids, f"{template_name}/{step.id} depends on unknown {dep!r}"


@pytest.mark.parametrize("template_name", CREATIVE_TEMPLATES)
def test_no_cycles_and_validates(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    errors = validate(wf)
    assert errors == [], f"{template_name} validate() errors: {errors}"


@pytest.mark.parametrize("template_name", CREATIVE_TEMPLATES)
def test_tool_references_known(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    for step in wf.steps:
        if step.tool_config and step.tool_config.tool:
            base = step.tool_config.tool.split(":")[0]
            assert base in KNOWN_TOOLS, (
                f"{template_name}/{step.id} unknown tool {base!r}"
            )


@pytest.mark.parametrize("template_name", CREATIVE_TEMPLATES)
def test_condition_branches_reference_real_steps(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    ids = {s.id for s in wf.steps}
    for step in wf.steps:
        if step.type == "condition" and step.condition_config:
            for sid in step.condition_config.then_steps + step.condition_config.else_steps:
                assert sid in ids, (
                    f"{template_name}/{step.id} condition references unknown {sid!r}"
                )


@pytest.mark.parametrize("template_name", CREATIVE_TEMPLATES)
def test_build_plan_covers_every_step_once(template_name: str) -> None:
    wf = parse_yaml_string(_load(template_name))
    plan = build_plan(wf)
    assert plan is not None and len(plan.stages) > 0
    planned: set[str] = set()
    for stage in plan.stages:
        for sid in stage:
            assert sid not in planned, f"{template_name}: {sid} planned twice"
            planned.add(sid)
    assert planned == {s.id for s in wf.steps}


@pytest.mark.parametrize("template_name", CREATIVE_TEMPLATES)
def test_has_backend_router_and_self_heal(template_name: str) -> None:
    """Each creative template forks the UGC skeleton: a backend router that
    branches between the two image generators, and a bounded self-heal chain."""
    wf = parse_yaml_string(_load(template_name))
    ids = {s.id for s in wf.steps}
    assert {"backend-router", "generate-nano", "generate-openai"} <= ids
    assert {"quality-judge", "quality-aggregate", "quality-check", "regenerate"} <= ids


def test_templates_are_discovered() -> None:
    """All three appear in the built-in template catalog."""
    by_file = {t.file_name: t for t in list_templates()}
    for name in CREATIVE_TEMPLATES:
        info = by_file.get(f"{name}.yaml")
        assert info is not None, f"{name} not discovered"
        assert info.source == "built-in"
        assert info.step_count > 0
        assert info.category in {"creative", "marketing"}
