"""
Lint-level checks for the Cloudflare cookbook deployments.

These tests do NOT spin up wrangler. They only validate the shape of the
two cookbook directories:

  - deploy/cookbooks/cloudflare/   (Cloudflare Containers via Workers)
  - deploy/cookbooks/cf-worker/    (Durable Object isolate, no container)

The sibling deploy/cookbooks/docker/ is shipped by a different agent and is
explicitly out of scope.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CF_DIR = ROOT / "deploy" / "cookbooks" / "cloudflare"
CFW_DIR = ROOT / "deploy" / "cookbooks" / "cf-worker"


# ---------------------------------------------------------------------------
# wrangler.toml shape
# ---------------------------------------------------------------------------


def _load_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_both_wrangler_toml_parse() -> None:
    """Both wrangler.toml files must be valid TOML."""
    cf = _load_toml(CF_DIR / "wrangler.toml")
    cfw = _load_toml(CFW_DIR / "wrangler.toml")
    assert cf["name"]
    assert cfw["name"]
    assert cf["main"].endswith(".ts")
    assert cfw["main"].endswith(".ts")


def test_cf_wrangler_declares_containers_block() -> None:
    """cloudflare/ cookbook must declare a [[containers]] block + DO binding."""
    cf = _load_toml(CF_DIR / "wrangler.toml")
    containers = cf.get("containers")
    assert isinstance(containers, list) and len(containers) >= 1, (
        "cloudflare/wrangler.toml must declare a [[containers]] block"
    )
    block = containers[0]
    assert block["class_name"] == "SandboxContainer"
    assert block["image"].endswith("Dockerfile")
    do = cf["durable_objects"]["bindings"]
    assert any(b["class_name"] == "SandboxContainer" for b in do)


def test_cf_wrangler_documents_secret_bindings() -> None:
    """Both required secrets must be documented for the cf cookbook."""
    raw = (CF_DIR / "wrangler.toml").read_text(encoding="utf-8")
    assert "ANTHROPIC_ENVIRONMENT_KEY" in raw
    assert "ANTHROPIC_ENVIRONMENT_ID" in raw
    assert "ANTHROPIC_WEBHOOK_SECRET" in raw


def test_cfw_wrangler_has_no_containers_block() -> None:
    """cf-worker/ cookbook is the isolate variant and must NOT declare containers."""
    cfw = _load_toml(CFW_DIR / "wrangler.toml")
    assert "containers" not in cfw, "cf-worker/ must not declare [[containers]]"
    do = cfw["durable_objects"]["bindings"]
    assert any(b["class_name"] == "SessionToolRunner" for b in do)


# ---------------------------------------------------------------------------
# src/index.ts contracts
# ---------------------------------------------------------------------------


WEBHOOK_EVENT = "session.status_run_started"
BETA_HEADER_RX = re.compile(r"(mcp-client-2025-11-20|managed-agents-2026-04-01)")


def test_both_index_ts_reference_webhook_event() -> None:
    cf = (CF_DIR / "src" / "index.ts").read_text(encoding="utf-8")
    cfw = (CFW_DIR / "src" / "index.ts").read_text(encoding="utf-8")
    assert WEBHOOK_EVENT in cf, "cf cookbook must consume session.status_run_started"
    assert WEBHOOK_EVENT in cfw, "cf-worker cookbook must consume session.status_run_started"


def test_both_index_ts_reference_documented_beta_header() -> None:
    cf = (CF_DIR / "src" / "index.ts").read_text(encoding="utf-8")
    cfw = (CFW_DIR / "src" / "index.ts").read_text(encoding="utf-8")
    assert BETA_HEADER_RX.search(cf), "cf cookbook must reference a documented beta header"
    assert BETA_HEADER_RX.search(cfw), "cf-worker cookbook must reference a documented beta header"


# ---------------------------------------------------------------------------
# README walkthrough patterns
# ---------------------------------------------------------------------------


def test_cf_readme_has_four_step_walkthrough() -> None:
    raw = (CF_DIR / "README.md").read_text(encoding="utf-8")
    for n in (1, 2, 3, 4):
        assert re.search(rf"^##\s+{n}\.\s", raw, flags=re.MULTILINE), (
            f"cloudflare/README.md is missing step {n}"
        )


def test_cfw_readme_has_four_step_walkthrough() -> None:
    raw = (CFW_DIR / "README.md").read_text(encoding="utf-8")
    for n in (1, 2, 3, 4):
        assert re.search(rf"^##\s+{n}\.\s", raw, flags=re.MULTILINE), (
            f"cf-worker/README.md is missing step {n}"
        )


# ---------------------------------------------------------------------------
# Dockerfile (cf only) + package.json shape
# ---------------------------------------------------------------------------


def test_cf_dockerfile_runs_as_uid_10001_in_workspace() -> None:
    """Match the docker/ cookbook contract: USER 10001 + WORKDIR /workspace."""
    df = (CF_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^USER\s+10001\b", df, flags=re.MULTILINE)
    assert re.search(r"^WORKDIR\s+/workspace\b", df, flags=re.MULTILINE)


def test_both_package_json_declare_workers_types() -> None:
    for d in (CF_DIR, CFW_DIR):
        pkg = json.loads((d / "package.json").read_text(encoding="utf-8"))
        dev = pkg.get("devDependencies", {})
        assert "@cloudflare/workers-types" in dev, (
            f"{d.name}/package.json must declare @cloudflare/workers-types"
        )
        deps = pkg.get("dependencies", {})
        assert "@anthropic-ai/sdk" in deps, (
            f"{d.name}/package.json must declare @anthropic-ai/sdk"
        )


# ---------------------------------------------------------------------------
# Secret hygiene
# ---------------------------------------------------------------------------


SECRET_SHAPES = (
    re.compile(r"sk-ant-(?!\[REDACTED\])[A-Za-z0-9._-]{8,}"),
    re.compile(r"whsec_(?!\[REDACTED\])[A-Za-z0-9+/=_-]{8,}"),
)


@pytest.mark.parametrize(
    "path",
    [
        CF_DIR / "wrangler.toml",
        CF_DIR / "Dockerfile",
        CF_DIR / "README.md",
        CF_DIR / "package.json",
        CF_DIR / "tsconfig.json",
        CF_DIR / "src" / "index.ts",
        CF_DIR / "src" / "container.ts",
        CF_DIR / "src" / "types.ts",
        CFW_DIR / "wrangler.toml",
        CFW_DIR / "README.md",
        CFW_DIR / "package.json",
        CFW_DIR / "tsconfig.json",
        CFW_DIR / "src" / "index.ts",
        CFW_DIR / "src" / "SessionToolRunner.ts",
        CFW_DIR / "src" / "fakefs.ts",
    ],
)
def test_no_hardcoded_secrets(path: Path) -> None:
    """No real-looking sk-ant-* or whsec_* tokens may appear in any cookbook file."""
    raw = path.read_text(encoding="utf-8")
    for rx in SECRET_SHAPES:
        match = rx.search(raw)
        assert match is None, f"{path}: hardcoded secret-like string: {match.group(0)[:24]}..."
