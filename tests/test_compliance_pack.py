"""Tests for the EU AI Act compliance-pack workflow templates.

Verifies every YAML in workflows/compliance-pack/ parses via parse_yaml_string,
passes validate() with zero errors, and declares an explicit risk_level field.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sandcastle.engine.dag import (
    VALID_RISK_LEVELS,
    parse_yaml_string,
    validate,
)

PACK_DIR = Path(__file__).resolve().parents[1] / "workflows" / "compliance-pack"

EXPECTED_FILES = [
    "dpia.yaml",
    "vendor-risk-assessment.yaml",
    "incident-report.yaml",
    "transparency-report.yaml",
    "bias-audit.yaml",
    "human-oversight-log.yaml",
    "model-card-generator.yaml",
    "risk-register.yaml",
    "gdpr-data-subject-request.yaml",
    "ai-inventory.yaml",
]


def _load_text(name: str) -> str:
    path = PACK_DIR / name
    assert path.exists(), f"Expected compliance-pack file missing: {path}"
    return path.read_text(encoding="utf-8")


def test_pack_directory_exists():
    """The compliance-pack directory exists in the workflows tree."""
    assert PACK_DIR.is_dir(), f"Compliance pack dir missing: {PACK_DIR}"


def test_pack_contains_ten_workflows():
    """Exactly the 10 expected YAML files ship in the pack."""
    yaml_files = sorted(p.name for p in PACK_DIR.glob("*.yaml"))
    assert yaml_files == sorted(EXPECTED_FILES), (
        f"Compliance pack file list mismatch. Found: {yaml_files}"
    )


@pytest.mark.parametrize("filename", EXPECTED_FILES)
def test_workflow_parses(filename: str):
    """Every compliance-pack YAML parses through parse_yaml_string."""
    wf = parse_yaml_string(_load_text(filename))
    assert wf.name, f"{filename}: workflow name is empty"
    assert wf.steps, f"{filename}: no steps defined"


@pytest.mark.parametrize("filename", EXPECTED_FILES)
def test_workflow_validates_clean(filename: str):
    """Every compliance-pack YAML validates with zero errors."""
    wf = parse_yaml_string(_load_text(filename))
    errors = validate(wf)
    assert errors == [], f"{filename}: validation errors: {errors}"


@pytest.mark.parametrize("filename", EXPECTED_FILES)
def test_workflow_declares_risk_level(filename: str):
    """Every compliance-pack YAML declares an explicit risk_level at the root."""
    raw = yaml.safe_load(_load_text(filename))
    assert isinstance(raw, dict), f"{filename}: top-level must be a mapping"
    assert "risk_level" in raw, (
        f"{filename}: must declare risk_level at the root (EU AI Act Article 6)"
    )
    assert raw["risk_level"] in VALID_RISK_LEVELS, (
        f"{filename}: risk_level '{raw['risk_level']}' not in {VALID_RISK_LEVELS}"
    )


@pytest.mark.parametrize("filename", EXPECTED_FILES)
def test_high_risk_has_approval_step(filename: str):
    """High-risk workflows must include a human-oversight approval step (Article 14)."""
    raw = yaml.safe_load(_load_text(filename))
    if raw.get("risk_level") != "high":
        pytest.skip(f"{filename} is not high-risk")
    wf = parse_yaml_string(_load_text(filename))
    has_approval = any(step.type == "approval" for step in wf.steps)
    assert has_approval, (
        f"{filename}: high-risk workflow must have at least one approval step"
    )


def test_pack_covers_required_compliance_scenarios():
    """The pack covers the ten compliance scenarios advertised on the landing page."""
    required_keywords = {
        "dpia": False,
        "vendor": False,
        "incident": False,
        "transparency": False,
        "bias": False,
        "oversight": False,
        "model-card": False,
        "risk-register": False,
        "gdpr": False,
        "inventory": False,
    }
    for fname in EXPECTED_FILES:
        for key in required_keywords:
            if key in fname:
                required_keywords[key] = True
    missing = [k for k, v in required_keywords.items() if not v]
    assert not missing, f"Compliance pack missing scenario coverage for: {missing}"
