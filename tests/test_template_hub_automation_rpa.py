"""Validation for the Automation & RPA Template Hub category.

This category consolidates the browser / computer-use / trajectory-replay / openclaw
templates that were previously scattered across general_ai, devops, engineering, hr_legal,
sales_crm and data into one coherent flagship home, and adds three novel flagship templates
that lean on the under-used desktop-automation primitives (computer-use, openclaw, delegate,
strategy-race, trajectory-replay). Every automation_rpa template must parse, validate,
build-plan and pass the code-sandbox blocklist; the three novel ones additionally must each
exercise a genuinely under-used RPA step type. A guard asserts the re-home did NOT swallow a
domain template that belongs elsewhere (patient_intake_eligibility_verifier stays healthcare).
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
from sandcastle.templates import list_templates

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "src" / "sandcastle" / "templates"
REGISTRY = ROOT / "site" / "hub" / "registry.json"

# The three net-new flagship templates and the under-used step type each must showcase.
NOVEL = {
    "desktop_thickclient_dataentry_reconciler": "computer-use",
    "vision_vs_dom_strategy_race_extractor": "race",
    "openclaw_agent_fleet_consensus_runner": "openclaw",
}

# Browser/RPA templates that are domain-first and must NOT have been re-homed.
KEEP_DOMAIN = {"patient_intake_eligibility_verifier": "healthcare"}


def _automation_rpa_files() -> list[str]:
    return sorted(
        t.file_name[:-5]  # strip .yaml
        for t in list_templates()
        if t.category == "automation_rpa"
    )


AUTOMATION_RPA = _automation_rpa_files()


def _load(name: str) -> str:
    return (TEMPLATES_DIR / f"{name}.yaml").read_text()


def _header_category(text: str) -> str | None:
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("#"):
            break
        s = s.lstrip("#").strip()
        if s.startswith("category:"):
            return s.split(":", 1)[1].strip()
    return None


def test_category_is_populated() -> None:
    assert len(AUTOMATION_RPA) >= 37, f"automation_rpa has only {len(AUTOMATION_RPA)} templates"


@pytest.mark.parametrize("name", AUTOMATION_RPA)
def test_parses_validates_plans_and_is_sandbox_clean(name: str) -> None:
    text = _load(name)
    assert _header_category(text) == "automation_rpa"
    wf = parse_yaml_string(text)
    assert wf.name and len(wf.steps) > 0
    assert validate(wf) == [], f"{name} did not validate"
    planned = [sid for stage in build_plan(wf).stages for sid in stage]
    assert len(planned) == len(set(planned)) and set(planned) == {s.id for s in wf.steps}
    for step in wf.steps:
        assert step.type in VALID_STEP_TYPES, f"{name}/{step.id} bad type {step.type!r}"
        if step.type == "code" and step.code_config and step.code_config.code:
            code = step.code_config.code
            m = _CODE_STEP_BLOCKED_PATTERNS.search(code)
            assert m is None, f"{name}/{step.id} sandbox-blocked {m.group(0)!r}"
            ast.parse(code)


@pytest.mark.parametrize("name,required_type", NOVEL.items())
def test_novel_templates_showcase_underused_step_types(name: str, required_type: str) -> None:
    assert name in AUTOMATION_RPA, f"{name} not discovered as automation_rpa"
    wf = parse_yaml_string(_load(name))
    used = {s.type for s in wf.steps}
    assert required_type in used, f"{name} should showcase {required_type!r}; uses {sorted(used)}"


@pytest.mark.parametrize("name,expected_cat", KEEP_DOMAIN.items())
def test_domain_first_templates_were_not_rehomed(name: str, expected_cat: str) -> None:
    by_file = {t.file_name: t for t in list_templates()}
    info = by_file.get(f"{name}.yaml")
    assert info is not None and info.category == expected_cat, (
        f"{name} should stay {expected_cat}, found {info.category if info else None}"
    )


def test_web_hub_registry_surfaces_automation_rpa() -> None:
    reg = json.loads(REGISTRY.read_text())
    template_counts = Counter(t.get("category", "general_ai") for t in reg["templates"])
    bar = {c["id"]: c["count"] for c in reg["categories"]}
    assert bar == dict(template_counts), (
        "registry categories[] out of sync with templates[] - run scripts/update_hub_registry.py"
    )
    assert bar.get("automation_rpa", 0) >= len(AUTOMATION_RPA)
    assert reg["stats"]["total_templates"] == len(reg["templates"])
