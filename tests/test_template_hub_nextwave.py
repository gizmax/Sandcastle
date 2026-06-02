"""Validation for the next-wave Template Hub category expansion.

Adds three new categories to the built-in catalog - personal (Personal & Life
Admin), research_intel (Research & Intelligence) and finance_ops (Finance & FP&A
Operations) - and surfaces them in the public web hub registry. The checks mirror
the v034 hub suite (parse, validate, build-plan coverage, control-flow references,
code-sandbox blocklist, category header, catalog discovery) and add an engine-first
guard: each template must carry the load on a non-LLM step type (deterministic code,
http, sensor, race, gate, classify, condition, loop), not a bare LLM prompt - that is
the whole point of a provider-neutral template.
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
    "personal": [
        "bill_renewal_late_fee_sentinel", "subscription_audit_cancel_warden",
        "warranty_return_window_tracker", "trip_itinerary_race_planner",
        "inbox_to_tasks_triage_router",
    ],
    "research_intel": [
        "competitor_pricing_page_change_sentinel", "funding_and_ma_radar_dedup",
        "literature_source_triage_race", "sec_filing_delta_extractor",
        "topic_share_of_voice_rank_monitor",
    ],
    "finance_ops": [
        "budget_variance_materiality_sentinel", "expense_policy_line_item_auditor",
        "ar_collections_dunning_prioritizer", "mrr_waterfall_board_rollup",
        "vendor_spend_anomaly_zscore_detector",
    ],
}
ALL = [(cat, name) for cat, names in HUB_TEMPLATES.items() for name in names]

# Non-LLM step types that can carry the load-bearing decision in a provider-neutral
# workflow. A template that uses none of these is "just prompt a model" and fails.
ENGINE_STEP_TYPES = {"http", "code", "sensor", "race", "gate", "classify", "condition", "loop"}

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
def test_is_engine_first_not_a_bare_prompt(cat: str, name: str) -> None:
    wf = parse_yaml_string(_load(name))
    used = {s.type for s in wf.steps}
    assert used & ENGINE_STEP_TYPES, (
        f"{name} uses no engine step type {ENGINE_STEP_TYPES} - it is just an LLM prompt"
    )


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


def test_new_categories_span_three() -> None:
    cats = {_header_category(_load(n)) for _, n in ALL}
    assert cats == {"personal", "research_intel", "finance_ops"}


def test_web_hub_registry_surfaces_new_categories() -> None:
    """The generator's invariant: registry categories[] counts equal the actual
    per-category counts in templates[], and each new category surfaces real
    templates in the public hub bar."""
    reg = json.loads(REGISTRY.read_text())
    template_counts = Counter(t.get("category", "general_ai") for t in reg["templates"])
    bar = {c["id"]: c["count"] for c in reg["categories"]}
    assert bar == dict(template_counts), (
        "registry categories[] is out of sync with templates[] - run "
        "scripts/update_hub_registry.py"
    )
    for cat in HUB_TEMPLATES:
        assert bar.get(cat, 0) >= len(HUB_TEMPLATES[cat]), f"{cat} missing from web hub bar"
    assert reg["stats"]["total_templates"] == len(reg["templates"])
