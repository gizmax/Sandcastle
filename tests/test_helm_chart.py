"""Lint-style tests for the memory-mcp Helm chart + docker-compose.

These tests deliberately avoid touching a real Kubernetes API; they only
parse the static YAML files and assert the shape callers depend on. That
keeps them fast and runnable in any CI environment.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parent.parent / "deploy" / "mcp-tunnel" / "memory-mcp"
TEMPLATES_DIR = CHART_DIR / "templates"


def _read_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# Chart.yaml
# ---------------------------------------------------------------------------

def test_chart_yaml_has_mandatory_fields() -> None:
    chart = _read_yaml(CHART_DIR / "Chart.yaml")
    assert chart["apiVersion"] == "v2"
    assert chart["name"] == "memory-mcp"
    assert re.match(r"^\d+\.\d+\.\d+$", str(chart["version"]))
    assert str(chart["appVersion"]) == "0.32.2"


# ---------------------------------------------------------------------------
# values.yaml
# ---------------------------------------------------------------------------

def test_values_yaml_parses() -> None:
    values = _read_yaml(CHART_DIR / "values.yaml")
    assert isinstance(values, dict)


def test_values_has_keys_templates_reference() -> None:
    values = _read_yaml(CHART_DIR / "values.yaml")

    # mcpServer block
    assert "image" in values["mcpServer"]
    assert "repository" in values["mcpServer"]["image"]
    assert "tag" in values["mcpServer"]["image"]
    assert "envFrom" in values["mcpServer"]
    assert "resources" in values["mcpServer"]
    assert "requests" in values["mcpServer"]["resources"]
    assert "limits" in values["mcpServer"]["resources"]

    # cloudflared block
    assert values["cloudflared"]["image"]["repository"] == "cloudflare/cloudflared"
    assert "tunnelId" in values["cloudflared"]
    assert values["cloudflared"]["auth"]["mode"] in {"wif", "manual"}
    assert "audience" in values["cloudflared"]["auth"]["wif"]
    assert "tokenSecret" in values["cloudflared"]["auth"]["manual"]

    # qdrant block
    assert values["qdrant"]["persistence"]["enabled"] is True
    assert values["qdrant"]["persistence"]["size"] == "10Gi"
    assert values["qdrant"]["snapshotInterval"] == "1h"


def test_values_security_hardening_defaults() -> None:
    values = _read_yaml(CHART_DIR / "values.yaml")
    assert values["securityContext"]["runAsNonRoot"] is True
    assert values["containerSecurityContext"]["readOnlyRootFilesystem"] is True
    assert values["containerSecurityContext"]["allowPrivilegeEscalation"] is False
    assert "ALL" in values["containerSecurityContext"]["capabilities"]["drop"]


def test_values_ingress_disabled_by_default() -> None:
    values = _read_yaml(CHART_DIR / "values.yaml")
    # MCP tunnels are outbound-only; Ingress must default to off.
    assert values["ingress"]["enabled"] is False


# ---------------------------------------------------------------------------
# NetworkPolicy egress targets (Anthropic docs, 2026-05-19)
# ---------------------------------------------------------------------------

def test_networkpolicy_targets_cloudflare_edges() -> None:
    values = _read_yaml(CHART_DIR / "values.yaml")
    np = values["networkPolicy"]
    assert np["enabled"] is True
    assert "198.41.192.0/19" in np["cloudflareEdges"]["ipv4"]
    assert "2606:4700:a0::/44" in np["cloudflareEdges"]["ipv6"]

    ports = {(p["protocol"], p["port"]) for p in np["cloudflareTunnelPorts"]}
    assert ("TCP", 7844) in ports
    assert ("UDP", 7844) in ports
    assert np["anthropicApi"]["port"] == 443


# ---------------------------------------------------------------------------
# Templates: each YAML file (other than the helper) is structurally valid.
# We cannot fully render Go templates without helm itself, but we can verify
# the static YAML scaffolding and look for required kinds.
# ---------------------------------------------------------------------------

def test_all_templates_have_expected_kinds() -> None:
    expected = {
        "deployment-mcp-server.yaml": "Deployment",
        "statefulset-qdrant.yaml": "StatefulSet",
        "service-mcp-server.yaml": "Service",
        "configmap.yaml": "ConfigMap",
        "serviceaccount.yaml": "ServiceAccount",
        "networkpolicy.yaml": "NetworkPolicy",
    }
    for filename, kind in expected.items():
        text = (TEMPLATES_DIR / filename).read_text(encoding="utf-8")
        assert f"kind: {kind}" in text, f"{filename} missing kind: {kind}"


def test_templates_have_balanced_go_template_directives() -> None:
    # Each `{{` must have a matching `}}` and conditional blocks must close.
    for path in TEMPLATES_DIR.glob("*.yaml"):
        raw = path.read_text(encoding="utf-8")
        opens = len(re.findall(r"\{\{", raw))
        closes = len(re.findall(r"\}\}", raw))
        assert opens == closes, f"{path.name}: unbalanced {{{{ }}}} ({opens} vs {closes})"
        if_count = len(re.findall(r"\{\{-?\s*if\b", raw))
        end_count = len(re.findall(r"\{\{-?\s*end\b", raw))
        range_count = len(re.findall(r"\{\{-?\s*range\b", raw))
        with_count = len(re.findall(r"\{\{-?\s*with\b", raw))
        assert end_count == if_count + range_count + with_count, (
            f"{path.name}: if/range/with={if_count+range_count+with_count} but end={end_count}"
        )


# ---------------------------------------------------------------------------
# docker-compose.yml
# ---------------------------------------------------------------------------

def test_docker_compose_has_three_services_with_healthchecks() -> None:
    compose = _read_yaml(CHART_DIR / "docker-compose.yml")
    services = compose["services"]
    assert set(services.keys()) == {"qdrant", "memory-mcp-server", "cloudflared"}
    for name, spec in services.items():
        assert "healthcheck" in spec, f"{name} is missing a healthcheck"


def test_docker_compose_reads_required_env_vars() -> None:
    raw = (CHART_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    assert "SANDCASTLE_ENV_KEY" in raw
    assert "ANTHROPIC_TUNNEL_TOKEN" in raw
    assert "cloudflare/cloudflared:latest" in raw


# ---------------------------------------------------------------------------
# README is non-empty and references the request URL.
# ---------------------------------------------------------------------------

def test_readme_references_access_request_and_helm_install() -> None:
    text = (CHART_DIR / "README.md").read_text(encoding="utf-8")
    assert "claude.com/form/claude-managed-agents" in text
    assert "helm install memory-mcp" in text
    assert "/healthz" in text
    assert "mcp_tunnel" in text
