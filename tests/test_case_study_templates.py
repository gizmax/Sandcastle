"""Tests for customer case-study workflow templates.

These templates mirror Anthropic's 2026-05-19 Managed Agents blog
case studies (Amplitude, Clay, Rogo). The tests assert that:

- Each YAML parses via ``parse_yaml_string`` and validates clean.
- Each template exercises the right v0.32 features (managed-agent,
  multiagent, computer-use, mcp_tunnel, self_hosted_sandbox, outcomes).
- The provider mappings match what the blog cites (Cloudflare for
  Amplitude, Daytona for Clay, Vercel for Rogo).
- No template ever combines ``memory_stores`` with self_hosted (that
  combination is hard-errored at session-create time per
  ``self_hosted_sandbox.assert_memory_compatible``).
- The README documents the pre-requisites and production gotchas
  EU enterprises hit when productionizing these patterns.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sandcastle.engine.dag import parse_yaml_string, validate

CASE_STUDIES_DIR = Path(__file__).resolve().parent.parent / "workflows" / "case-studies"

AMPLITUDE = CASE_STUDIES_DIR / "amplitude-design-agent.yaml"
CLAY = CASE_STUDIES_DIR / "clay-sculptor-gtm.yaml"
ROGO = CASE_STUDIES_DIR / "rogo-analyst-on-private-data.yaml"
README = CASE_STUDIES_DIR / "README.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(path: Path) -> dict:
    """Return the raw YAML dict (for structural assertions)."""
    return yaml.safe_load(path.read_text())


def _managed_agent_step(raw: dict) -> dict:
    """Return the first managed-agent step from a parsed YAML dict."""
    for step in raw.get("steps", []):
        if step.get("type") == "managed-agent":
            return step
    raise AssertionError("no managed-agent step in workflow")


# ---------------------------------------------------------------------------
# Parse + validate the three templates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [AMPLITUDE, CLAY, ROGO])
def test_template_parses_and_validates(path: Path) -> None:
    """All three case-study YAMLs parse and validate clean."""
    workflow = parse_yaml_string(path.read_text())
    errors = validate(workflow)
    assert errors == [], f"{path.name} failed validation: {errors}"


# ---------------------------------------------------------------------------
# Amplitude: Cloudflare + computer-use + accessibility specialist
# ---------------------------------------------------------------------------


def test_amplitude_has_computer_use_step_and_cloudflare_provider() -> None:
    raw = _load(AMPLITUDE)
    types = [s.get("type") for s in raw["steps"]]
    assert "computer-use" in types, "Amplitude template must include a computer-use step"
    mgmt = _managed_agent_step(raw)
    cfg = mgmt["managed_agent_config"]
    assert cfg["self_hosted_sandbox"]["provider"] == "cloudflare"
    nicknames = {a.get("nickname") for a in cfg["multiagent"]["agents"]}
    assert "accessibility-review" in nicknames


# ---------------------------------------------------------------------------
# Clay: multiagent with 3 specialists + Daytona
# ---------------------------------------------------------------------------


def test_clay_has_three_specialists_and_daytona() -> None:
    raw = _load(CLAY)
    mgmt = _managed_agent_step(raw)
    cfg = mgmt["managed_agent_config"]
    assert cfg["self_hosted_sandbox"]["provider"] == "daytona"
    roster = cfg["multiagent"]["agents"]
    assert cfg["multiagent"]["type"] == "coordinator"
    nicknames = {a.get("nickname") for a in roster}
    assert {"researcher", "writer", "qualifier"} <= nicknames, (
        f"Clay template must have researcher/writer/qualifier roster, got {nicknames}"
    )
    assert len([a for a in roster if a.get("type") == "agent"]) == 3


# ---------------------------------------------------------------------------
# Rogo: WIF auth + high risk + Vercel
# ---------------------------------------------------------------------------


def test_rogo_uses_workload_identity_federation_and_is_high_risk() -> None:
    raw = _load(ROGO)
    assert raw["risk_level"] == "high"
    assert raw["mcp_tunnel"]["auth_mode"] == "workload_identity_federation"
    mgmt = _managed_agent_step(raw)
    assert mgmt["managed_agent_config"]["self_hosted_sandbox"]["provider"] == "vercel"
    # High-risk requires an approval step - validator enforces, double-check explicitly.
    assert any(s.get("type") == "approval" for s in raw["steps"])


# ---------------------------------------------------------------------------
# Cross-template invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [AMPLITUDE, CLAY, ROGO])
def test_each_template_references_self_hosted_sandbox(path: Path) -> None:
    """Every case-study workflow runs on a customer-owned sandbox."""
    raw = _load(path)
    mgmt = _managed_agent_step(raw)
    shs = mgmt["managed_agent_config"].get("self_hosted_sandbox")
    assert isinstance(shs, dict), f"{path.name} missing self_hosted_sandbox block"
    assert shs.get("environment_id"), f"{path.name} self_hosted_sandbox needs environment_id"
    assert shs.get("provider") in {"cloudflare", "daytona", "vercel"}


@pytest.mark.parametrize("path", [AMPLITUDE, CLAY, ROGO])
def test_no_template_uses_memory_stores_on_self_hosted(path: Path) -> None:
    """memory_stores + self_hosted is hard-errored at session create."""
    raw = _load(path)
    mgmt = _managed_agent_step(raw)
    cfg = mgmt["managed_agent_config"]
    assert "memory_stores" not in cfg, (
        f"{path.name} declares memory_stores; this is incompatible with "
        "self_hosted_sandbox (see assert_memory_compatible)."
    )


@pytest.mark.parametrize("path", [AMPLITUDE, CLAY, ROGO])
def test_every_step_has_a_description(path: Path) -> None:
    """Self-describing metadata - every step has a responsibility line."""
    workflow = parse_yaml_string(path.read_text())
    for step in workflow.steps:
        assert step.responsibility, (
            f"{path.name} step {step.id!r} missing responsibility"
        )


@pytest.mark.parametrize("path", [AMPLITUDE, CLAY, ROGO])
def test_each_managed_agent_declares_outcomes(path: Path) -> None:
    """Every case-study managed-agent step captures a typed outcome."""
    raw = _load(path)
    mgmt = _managed_agent_step(raw)
    outcomes = mgmt["managed_agent_config"].get("outcomes")
    assert isinstance(outcomes, list) and outcomes, (
        f"{path.name} managed-agent step must declare outcomes"
    )
    for outcome in outcomes:
        assert "id" in outcome and "range" in outcome


# ---------------------------------------------------------------------------
# README assertions
# ---------------------------------------------------------------------------


def test_readme_documents_prerequisites_and_gotchas() -> None:
    text = README.read_text()
    assert "## Pre-requisites" in text, "README must have Pre-requisites section"
    assert "## Production gotchas" in text, "README must have Production gotchas section"
    # The three sharp-edged limitations EU prospects need to know up front.
    assert "Memory Stores are not compatible" in text
    assert "AWS is unsupported" in text
    assert "MCP tunnels are a gated preview" in text
