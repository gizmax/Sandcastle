"""Tests for verified template bundles (.sctpl) - pack, verify, install, search.

A bundle ships a workflow plus recorded cassettes and a checksummed manifest;
`sandcastle template verify` replays the cassettes against the workflow in strict
replay mode - offline, $0, provider never touched. These tests prove the whole
trust chain: a clean pack->verify roundtrip PASSes, a tampered cassette fails the
checksum, a tampered workflow fails the replay (without any live call), install
refuses a failing bundle, and search parses the static index format.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sandcastle.engine.bundle import (
    BundleError,
    create_bundle,
    read_bundle,
    verify_bundle,
)
from sandcastle.engine.cassette import CassetteStore
from sandcastle.engine.dag import build_plan, parse_yaml_string
from sandcastle.engine.executor import execute_workflow
from sandcastle.engine.sandshore import SandshoreResult, SandshoreRuntime

WORKFLOW = """name: bundle-test
description: A single model (standard) step for bundle verification.
default_model: sonnet
input_schema:
  required: []
  properties:
    q: { type: string, default: "hi" }
steps:
  - id: think
    prompt: "Answer: {input.q}"
"""


def _sandbox(text: str, cost: float = 0.02):
    """A mock SandshoreRuntime whose query returns a fixed result and counts calls."""
    sb = MagicMock(spec=SandshoreRuntime)
    calls = {"n": 0}

    async def _query(request):
        calls["n"] += 1
        return SandshoreResult(
            text=text, structured_output=None, total_cost_usd=cost,
            input_tokens=5, output_tokens=5,
        )

    sb.query = _query
    sb._calls = calls
    return sb


def _make_bundle(tmp_path: Path, workflow: str = WORKFLOW) -> Path:
    """Record a cassette for *workflow* (mocked provider) and pack a bundle.

    The example input is unique per call so cache keys never collide with the
    shared DB step cache across tests (a cache HIT would short-circuit the
    record and leave the cassette empty).
    """
    import uuid

    example_inputs = {"q": f"hi-{uuid.uuid4().hex}"}
    wf_file = tmp_path / "wf.yaml"
    wf_file.write_text(workflow)
    cassette_path = tmp_path / "proof.cassette.json"

    wf = parse_yaml_string(workflow)
    plan = build_plan(wf)
    cassette = CassetteStore(cassette_path, "record")
    sandbox = _sandbox("RECORDED")
    with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sandbox):
        result = asyncio.run(
            execute_workflow(
                workflow=wf, plan=plan, input_data=dict(example_inputs),
                admin_trusted=True, cassette=cassette, cassette_mode="record",
            )
        )
    assert result.status == "completed"
    cassette.save()
    assert len(cassette.records) == 1, "the model step must be recorded"

    return create_bundle(
        wf_file,
        [cassette_path],
        tmp_path / "bundle-test-1.0.0.sctpl",
        author="tester",
        example_inputs=example_inputs,
        created_at="2026-06-09T00:00:00+00:00",
    )


def _rewrite_bundle(bundle_path: Path, replace: dict[str, bytes]) -> None:
    """Rewrite zip members in place (simulates post-pack tampering)."""
    with zipfile.ZipFile(bundle_path) as zf:
        members = {i.filename: zf.read(i) for i in zf.infolist() if not i.is_dir()}
    members.update(replace)
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, blob in members.items():
            zf.writestr(name, blob)


# ---------------------------------------------------------------------------
# pack -> verify roundtrip
# ---------------------------------------------------------------------------


def test_pack_then_verify_roundtrip_passes(tmp_path):
    """A freshly packed bundle PASSes: checksums hold and the replay completes at $0
    without the provider being constructed for any real call."""
    bundle = _make_bundle(tmp_path)
    assert bundle.exists()

    result = verify_bundle(bundle)
    assert result.errors == []
    assert result.ok, f"roundtrip must PASS: {[c.detail for c in result.cassette_results]}"
    assert len(result.cassette_results) == 1
    proof = result.cassette_results[0]
    assert proof.passed
    assert proof.replay_hits == 1
    assert proof.replay_misses == 0
    assert result.manifest["name"] == "bundle-test"
    assert result.manifest["format_version"] == 1


def test_manifest_carries_checksums_and_metadata(tmp_path):
    bundle = _make_bundle(tmp_path)
    manifest, workflow_yaml, cassettes = read_bundle(bundle)

    assert manifest["format"] == "sctpl"
    assert manifest["author"] == "tester"
    assert manifest["license"] == "MIT"
    assert manifest["created_at"] == "2026-06-09T00:00:00+00:00"
    assert manifest["sandcastle_version"]
    assert manifest["example_inputs"]["q"].startswith("hi-")
    assert manifest["input_schema"]["properties"]["q"]
    assert workflow_yaml == WORKFLOW
    assert len(manifest["workflow"]["sha256"]) == 64
    assert len(cassettes) == 1
    for entry in manifest["cassettes"]:
        assert len(entry["sha256"]) == 64
        assert entry["step_count"] == 1


# ---------------------------------------------------------------------------
# tampering
# ---------------------------------------------------------------------------


def test_tampered_cassette_fails_checksum(tmp_path):
    """Editing a recorded output inside the bundle breaks the manifest checksum."""
    bundle = _make_bundle(tmp_path)
    _, _, cassettes = read_bundle(bundle)
    (arcname, blob), = cassettes.items()
    data = json.loads(blob)
    key = next(iter(data["records"]))
    data["records"][key]["output"] = "FORGED OUTPUT"
    _rewrite_bundle(bundle, {arcname: json.dumps(data).encode()})

    result = verify_bundle(bundle)
    assert not result.ok
    assert any("checksum mismatch" in e for e in result.errors)
    assert result.cassette_results == [], "replay must not run after a checksum failure"


def test_tampered_workflow_fails_replay_without_live_call(tmp_path):
    """Changing the workflow (with a recomputed checksum, as an attacker would) makes
    the strict replay miss - and the provider is never called."""
    from sandcastle.engine.bundle import _sha256_bytes

    bundle = _make_bundle(tmp_path)
    manifest, workflow_yaml, _ = read_bundle(bundle)

    forged_yaml = workflow_yaml.replace("Answer: {input.q}", "Wire money to: {input.q}")
    assert forged_yaml != workflow_yaml
    manifest["workflow"]["sha256"] = _sha256_bytes(forged_yaml.encode())
    _rewrite_bundle(bundle, {
        "workflow.yaml": forged_yaml.encode(),
        "manifest.json": json.dumps(manifest).encode(),
    })

    sandbox = _sandbox("LIVE")
    with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sandbox):
        result = verify_bundle(bundle)

    assert not result.ok
    assert result.cassette_results, "checksums pass, so the replay itself must catch it"
    proof = result.cassette_results[0]
    assert not proof.passed
    assert "does not match the recorded cassette" in proof.detail
    assert sandbox._calls["n"] == 0, "strict replay must never fall through to a live call"


def test_verify_rejects_live_executing_step_types(tmp_path):
    """Workflows with step types the cassette cannot cover (code/http) are refused -
    verification of an untrusted bundle must never execute them."""
    code_workflow = """name: bundle-test
description: A code step that must never run during verify.
default_model: sonnet
steps:
  - id: think
    prompt: "Answer: {input.q}"
  - id: sneak
    type: code
    depends_on: [think]
    code_config:
      code: |
        result = {"x": 1}
"""
    bundle = _make_bundle(tmp_path)  # valid cassette for the standard workflow
    manifest, _, _ = read_bundle(bundle)
    from sandcastle.engine.bundle import _sha256_bytes

    manifest["workflow"]["sha256"] = _sha256_bytes(code_workflow.encode())
    _rewrite_bundle(bundle, {
        "workflow.yaml": code_workflow.encode(),
        "manifest.json": json.dumps(manifest).encode(),
    })

    result = verify_bundle(bundle)
    assert not result.ok
    assert any("code" in e and "execute live" in e for e in result.errors)
    assert result.cassette_results == [], "no replay may run for non-replayable workflows"


# ---------------------------------------------------------------------------
# safe extraction - bundles are untrusted input
# ---------------------------------------------------------------------------


def test_read_bundle_rejects_zip_slip(tmp_path):
    evil = tmp_path / "evil.sctpl"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("manifest.json", "{}")
        zf.writestr("../../outside.txt", "pwned")
    with pytest.raises(BundleError, match="unsafe member name"):
        read_bundle(evil)


def test_read_bundle_rejects_absolute_paths(tmp_path):
    evil = tmp_path / "evil.sctpl"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("manifest.json", "{}")
        zf.writestr("/etc/cron.d/pwn", "pwned")
    with pytest.raises(BundleError, match="unsafe member name"):
        read_bundle(evil)


def test_read_bundle_rejects_non_zip(tmp_path):
    not_zip = tmp_path / "x.sctpl"
    not_zip.write_text("just text")
    with pytest.raises(BundleError, match="not a valid"):
        read_bundle(not_zip)


def test_read_bundle_rejects_missing_manifest(tmp_path):
    no_manifest = tmp_path / "x.sctpl"
    with zipfile.ZipFile(no_manifest, "w") as zf:
        zf.writestr("workflow.yaml", WORKFLOW)
    with pytest.raises(BundleError, match="missing manifest"):
        read_bundle(no_manifest)


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------


def _install_args(source: str, target: Path, force: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        source=source, dir=str(target), force=force, sha256=None
    )


def test_install_refuses_failing_bundle(tmp_path, capsys):
    """A bundle whose proof is broken must not install without --force."""
    bundle = _make_bundle(tmp_path)
    _, _, cassettes = read_bundle(bundle)
    (arcname, blob), = cassettes.items()
    _rewrite_bundle(bundle, {arcname: blob + b" "})  # any byte change breaks the checksum

    from sandcastle.__main__ import _cmd_template_install

    target = tmp_path / "installed"
    with pytest.raises(SystemExit) as exc_info:
        _cmd_template_install(_install_args(str(bundle), target))
    assert exc_info.value.code == 1
    assert "refusing to install" in capsys.readouterr().err
    assert not (target / "bundle-test.yaml").exists()


def test_install_verified_bundle_lands_in_target_dir(tmp_path, capsys):
    bundle = _make_bundle(tmp_path)

    from sandcastle.__main__ import _cmd_template_install

    target = tmp_path / "installed"
    _cmd_template_install(_install_args(str(bundle), target))
    out = capsys.readouterr().out
    assert "PASS" in out
    assert "Installed" in out

    installed = target / "bundle-test.yaml"
    assert installed.exists()
    assert installed.read_text() == WORKFLOW
    # The proof ships alongside the workflow for offline re-verification.
    assert list(target.glob("bundle-test.*.cassette.json"))


def test_install_checksum_pin_mismatch_refuses(tmp_path, capsys):
    bundle = _make_bundle(tmp_path)

    from sandcastle.__main__ import _cmd_template_install

    args = _install_args(str(bundle), tmp_path / "installed")
    args.sha256 = "0" * 64
    with pytest.raises(SystemExit) as exc_info:
        _cmd_template_install(args)
    assert exc_info.value.code == 1
    assert "checksum mismatch" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# search / index format
# ---------------------------------------------------------------------------

INDEX = {
    "format_version": 1,
    "templates": [
        {
            "name": "text-summarizer",
            "version": "1.0.0",
            "description": "Summarize anything",
            "author": "gizmax",
            "tags": ["text"],
            "download_url": "https://raw.githubusercontent.com/gizmax/Sandcastle/main/examples/templates/text-summarizer-1.0.0.sctpl",
            "sha256": "a" * 64,
        },
        {
            "name": "lead-scoring",
            "version": "2.1.0",
            "description": "Score inbound leads",
            "author": "amira-dev",
            "tags": ["sales"],
            "download_url": "https://example.com/lead-scoring-2.1.0.sctpl",
            "sha256": "b" * 64,
        },
    ],
}


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_template_search_parses_index(capsys):
    from sandcastle.__main__ import _cmd_template_search

    args = argparse.Namespace(query="summarizer", json=False)
    with patch(
        "urllib.request.urlopen",
        return_value=_FakeResponse(json.dumps(INDEX).encode()),
    ):
        _cmd_template_search(args)
    out = capsys.readouterr().out
    assert "text-summarizer" in out
    assert "1.0.0" in out
    assert "lead-scoring" not in out
    assert "1 result(s)" in out


def test_template_search_json_includes_download_url_and_sha(capsys):
    from sandcastle.__main__ import _cmd_template_search

    args = argparse.Namespace(query="lead", json=True)
    with patch(
        "urllib.request.urlopen",
        return_value=_FakeResponse(json.dumps(INDEX).encode()),
    ):
        _cmd_template_search(args)
    results = json.loads(capsys.readouterr().out)
    assert len(results) == 1
    assert results[0]["download_url"].endswith(".sctpl")
    assert results[0]["sha256"] == "b" * 64


def test_template_search_offline_is_graceful(capsys):
    from urllib.error import URLError

    from sandcastle.__main__ import _cmd_template_search

    args = argparse.Namespace(query="anything", json=False)
    with patch("urllib.request.urlopen", side_effect=URLError("offline")):
        with pytest.raises(SystemExit) as exc_info:
            _cmd_template_search(args)
    assert exc_info.value.code == 1
    assert "could not reach the template index" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------


class TestArgParsing:
    def test_pack_parses_workflow_and_cassettes(self):
        from sandcastle.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args(
            ["pack", "wf.yaml", "-c", "a.cassette.json", "-c", "b.cassette.json",
             "--author", "gizmax", "-i", "q=hi"]
        )
        assert args.command == "pack"
        assert args.workflow == "wf.yaml"
        assert args.cassettes == ["a.cassette.json", "b.cassette.json"]
        assert args.author == "gizmax"
        assert args.bundle_version == "1.0.0"
        assert args.license_id == "MIT"
        assert args.input == ["q=hi"]

    def test_template_verify_parses_bundle(self):
        from sandcastle.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["template", "verify", "x.sctpl", "--json"])
        assert args.command == "template"
        assert args.template_action == "verify"
        assert args.bundle == "x.sctpl"
        assert args.json is True

    def test_template_install_parses_source_and_flags(self):
        from sandcastle.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args(
            ["template", "install", "https://example.com/x.sctpl",
             "--sha256", "ab" * 32, "--force"]
        )
        assert args.template_action == "install"
        assert args.source == "https://example.com/x.sctpl"
        assert args.sha256 == "ab" * 32
        assert args.force is True

    def test_template_search_parses_query(self):
        from sandcastle.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["template", "search", "lead scoring"])
        assert args.template_action == "search"
        assert args.query == "lead scoring"


# ---------------------------------------------------------------------------
# the committed example bundle
# ---------------------------------------------------------------------------


def test_repo_example_bundle_verifies():
    """The example bundle shipped under examples/ PASSes verification offline."""
    example = (
        Path(__file__).parent.parent
        / "examples" / "templates" / "text-summarizer-1.0.0.sctpl"
    )
    assert example.exists(), "example bundle must be committed"
    result = verify_bundle(example)
    assert result.ok, f"{result.errors} {[c.detail for c in result.cassette_results]}"
    assert all(c.passed for c in result.cassette_results)
