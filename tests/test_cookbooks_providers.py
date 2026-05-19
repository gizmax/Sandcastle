"""Static contract tests for the Daytona / Modal / Vercel cookbooks.

These tests pin the shape of the three provider deployments without
executing any vendor SDK. They guard against accidental drift away from
the canonical Anthropic cookbook patterns linked from each README.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

COOKBOOKS = Path(__file__).resolve().parents[1] / "deploy" / "cookbooks"
DAYTONA = COOKBOOKS / "daytona"
MODAL = COOKBOOKS / "modal"
VERCEL = COOKBOOKS / "vercel"


# ---------------------------------------------------------------------------
# README + walkthrough shape
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider_dir", [DAYTONA, MODAL, VERCEL])
def test_readme_has_five_step_walkthrough(provider_dir: Path) -> None:
    readme = provider_dir / "README.md"
    assert readme.exists(), f"missing README.md in {provider_dir}"
    text = readme.read_text()
    assert "5-step walkthrough" in text, f"missing 5-step walkthrough header in {readme}"
    # All five numbered steps must be present.
    for n in range(1, 6):
        assert f"### Step {n}" in text, f"missing Step {n} in {readme}"


@pytest.mark.parametrize("provider_dir", [DAYTONA, MODAL, VERCEL])
def test_readme_references_session_status_run_started(provider_dir: Path) -> None:
    """Each cookbook must explain it triggers off the canonical event."""
    text = (provider_dir / "README.md").read_text()
    runner_text = _runner_source(provider_dir)
    combined = text + "\n" + runner_text
    assert "session.status_run_started" in combined, (
        f"{provider_dir.name} cookbook never references session.status_run_started"
    )


# ---------------------------------------------------------------------------
# Daytona
# ---------------------------------------------------------------------------

def test_daytona_runner_uses_snapshot_api_and_session_label() -> None:
    src = (DAYTONA / "sandbox_runner.py").read_text()
    assert "import" in src and "daytona_sdk" in src, (
        "daytona runner must import daytona_sdk"
    )
    assert "CreateSandboxFromSnapshotParams" in src
    # Per the cookbook, sessions are routed back via byoc.session_id labels.
    assert '"byoc.session_id"' in src


def test_daytona_runner_sets_auto_pause_interval() -> None:
    src = (DAYTONA / "sandbox_runner.py").read_text()
    assert "auto_stop_interval" in src, (
        "daytona runner must configure auto_stop_interval for auto-pause"
    )


def test_daytona_dockerfile_installs_ant_cli() -> None:
    df = (DAYTONA / "byoc_env_default.dockerfile").read_text()
    assert re.search(r"npm\s+install\s+-g\s+@anthropic-ai/ant", df), (
        "Daytona snapshot Dockerfile must install the ant CLI globally"
    )


# ---------------------------------------------------------------------------
# Modal
# ---------------------------------------------------------------------------

def test_modal_runner_uses_sandbox_create_with_volumes_and_gpu() -> None:
    src = (MODAL / "sandbox_runner.py").read_text()
    assert "modal.Sandbox.create" in src
    # All three keyword args must be wired up.
    assert "volumes=" in src
    assert "gpu=" in src
    assert "idle_timeout=" in src


def test_modal_image_pins_python_312_and_ant() -> None:
    img = (MODAL / "image.py").read_text()
    assert 'python_version="3.12"' in img, "Modal image must pin python 3.12"
    assert "@anthropic-ai/ant" in img, "Modal image must install ant CLI"


# ---------------------------------------------------------------------------
# Vercel
# ---------------------------------------------------------------------------

def test_vercel_runner_uses_vercel_sandbox_spawn_with_network_policy() -> None:
    src = (VERCEL / "api" / "runner.mjs").read_text()
    assert "@vercel/sandbox" in src
    assert ".spawn(" in src
    assert "networkPolicy" in src and "allow" in src


def test_vercel_runner_keeps_anthropic_key_outside_sandbox() -> None:
    """Credential brokering: the spawn call must NOT inject the env key."""
    src = (VERCEL / "api" / "runner.mjs").read_text()

    # The function itself reads the key (for unwrap + work.poll) - that's OK.
    assert "process.env.ANTHROPIC_ENVIRONMENT_KEY" in src, (
        "function must read the env key to verify webhooks"
    )

    # But the spawn block must not forward it. Find the spawn(...) call and
    # verify its env map omits ANTHROPIC_ENVIRONMENT_KEY.
    spawn_match = re.search(
        r"sandbox\.spawn\([^)]*?env:\s*\{(?P<env>[^}]*)\}",
        src,
        flags=re.DOTALL,
    )
    assert spawn_match, "could not locate sandbox.spawn env map"
    spawn_env = spawn_match.group("env")
    assert "ANTHROPIC_ENVIRONMENT_KEY" not in spawn_env, (
        "credential brokering violated: ANTHROPIC_ENVIRONMENT_KEY leaked into sandbox env"
    )


def test_vercel_runner_uses_ms_one_hour_timeout() -> None:
    src = (VERCEL / "api" / "runner.mjs").read_text()
    assert 'ms("1h")' in src or "ms('1h')" in src, (
        "Vercel runner must use ms('1h') sandbox timeout per cookbook"
    )


# ---------------------------------------------------------------------------
# Manifest / packaging integrity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("toml_path", [DAYTONA / "pyproject.toml", MODAL / "pyproject.toml"])
def test_pyproject_parses_as_toml(toml_path: Path) -> None:
    data = tomllib.loads(toml_path.read_text())
    assert "project" in data
    assert data["project"]["name"]
    assert data["project"]["dependencies"]


def test_vercel_package_json_parses_and_declares_sandbox_dep() -> None:
    pkg = json.loads((VERCEL / "package.json").read_text())
    assert pkg["name"]
    assert "@vercel/sandbox" in pkg["dependencies"]
    assert "@anthropic-ai/sdk" in pkg["dependencies"]


def test_vercel_json_parses_and_caps_durations() -> None:
    cfg = json.loads((VERCEL / "vercel.json").read_text())
    fns = cfg["functions"]
    assert fns["api/runner.mjs"]["maxDuration"] == 60
    # Spawn helper gets the full hour.
    spawn_helper = next(
        (v for k, v in fns.items() if "spawn" in k.lower() or v.get("maxDuration") == 3600),
        None,
    )
    assert spawn_helper is not None, "vercel.json must declare a long-running helper"
    assert spawn_helper["maxDuration"] == 3600


# ---------------------------------------------------------------------------
# Secret hygiene
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"REPLACE_ME|placeholder", re.IGNORECASE)
_REAL_SECRET_RE = re.compile(
    r"sk-ant-(?!oat-REPLACE)[A-Za-z0-9_-]{20,}"
    r"|whsec_(?!REPLACE)[A-Za-z0-9_-]{20,}"
    r"|dtn_(?!REPLACE)[A-Za-z0-9_-]{20,}"
)


@pytest.mark.parametrize(
    "env_file",
    [
        DAYTONA / ".env.example",
        MODAL / ".env.example",
        VERCEL / ".env.example",
    ],
)
def test_env_example_has_no_real_secrets(env_file: Path) -> None:
    text = env_file.read_text()
    # Every line that assigns a sensitive var must use a placeholder.
    for line in text.splitlines():
        if "=" not in line or line.strip().startswith("#"):
            continue
        key, _, value = line.partition("=")
        if any(token in key for token in ("KEY", "SECRET", "TOKEN", "API_KEY")):
            assert (
                _PLACEHOLDER_RE.search(value) or value.strip() == ""
            ), f"{env_file}: {key} looks like a real secret"
    assert not _REAL_SECRET_RE.search(text), (
        f"{env_file}: contains a string matching a real secret pattern"
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _runner_source(provider_dir: Path) -> str:
    """Return concatenated runner source for a provider directory."""
    parts: list[str] = []
    for name in ("sandbox_runner.py", "image.py", "sandbox-runner.mjs"):
        candidate = provider_dir / name
        if candidate.exists():
            parts.append(candidate.read_text())
    # Vercel keeps the webhook handler under api/.
    api_runner = provider_dir / "api" / "runner.mjs"
    if api_runner.exists():
        parts.append(api_runner.read_text())
    return "\n".join(parts)
