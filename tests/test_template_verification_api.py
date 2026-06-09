"""Tests for the template verification API behind the dashboard's Proven badge.

A template installed from a verified .sctpl bundle keeps the bundle next to the
workflow YAML. The API surfaces that proof: the templates list flags such
templates as ``proven``, ``GET /templates/{name}/verification`` returns the
manifest plus checksum validity, and ``POST /templates/{name}/verify`` replays
the bundled cassettes strictly offline and reports PASS/FAIL per cassette.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sandcastle.main import app
from tests.test_template_bundle import WORKFLOW, _install_args, _make_bundle

client = TestClient(app)

PLAIN_TEMPLATE = """# name: plain-local
# description: A hand-written template with no bundle proof.
name: plain-local
description: A hand-written template with no bundle proof.
steps:
  - id: think
    prompt: "Say hi"
"""


@pytest.fixture
def templates_dir(tmp_path, monkeypatch) -> Path:
    """Patch the templates dir to an isolated tmp tree with a community subdir."""
    root = tmp_path / "templates"
    (root / "community").mkdir(parents=True)
    monkeypatch.setattr("sandcastle.templates._TEMPLATES_DIR", root)
    return root


@pytest.fixture
def installed_bundle(templates_dir, tmp_path, capsys) -> Path:
    """Install a freshly packed, verified bundle into the community dir."""
    from sandcastle.__main__ import _cmd_template_install

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    bundle = _make_bundle(src_dir)
    _cmd_template_install(_install_args(str(bundle), templates_dir / "community"))
    capsys.readouterr()  # swallow the CLI verify report
    return templates_dir / "community"


def _tamper_cassette(bundle_path: Path) -> None:
    """Flip one byte of the bundled cassette so its manifest checksum breaks."""
    with zipfile.ZipFile(bundle_path) as zf:
        members = {i.filename: zf.read(i) for i in zf.infolist() if not i.is_dir()}
    cassette_name = next(n for n in members if n.startswith("cassettes/"))
    members[cassette_name] += b" "
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, blob in members.items():
            zf.writestr(name, blob)


# ---------------------------------------------------------------------------
# install keeps the bundle alongside the workflow
# ---------------------------------------------------------------------------


def test_install_keeps_bundle_next_to_workflow(installed_bundle):
    assert (installed_bundle / "bundle-test.yaml").exists()
    assert (installed_bundle / "bundle-test.sctpl").exists()


def test_bundle_for_template_resolves_sibling(installed_bundle):
    from sandcastle.engine.bundle import bundle_for_template

    assert bundle_for_template("bundle-test.yaml") == installed_bundle / "bundle-test.sctpl"
    assert bundle_for_template("no-such-template.yaml") is None


# ---------------------------------------------------------------------------
# templates list flags proven templates
# ---------------------------------------------------------------------------


def test_list_templates_marks_bundle_installed_as_proven(installed_bundle):
    (installed_bundle / "plain-local.yaml").write_text(PLAIN_TEMPLATE)

    resp = client.get("/api/templates")
    assert resp.status_code == 200
    by_name = {t["name"]: t for t in resp.json()["data"]}

    assert by_name["bundle-test"]["proven"] is True
    assert by_name["plain-local"]["proven"] is False


def test_get_template_includes_proven_flag(installed_bundle):
    resp = client.get("/api/templates/bundle-test")
    assert resp.status_code == 200
    assert resp.json()["data"]["proven"] is True


# ---------------------------------------------------------------------------
# GET /templates/{name}/verification
# ---------------------------------------------------------------------------


def test_verification_returns_manifest_and_valid_checksums(installed_bundle):
    resp = client.get("/api/templates/bundle-test/verification")
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert data["proven"] is True
    assert data["manifest"]["name"] == "bundle-test"
    assert data["manifest"]["author"] == "tester"
    assert data["manifest"]["version"] == "1.0.0"
    assert data["workflow"]["valid"] is True
    assert len(data["workflow"]["sha256"]) == 64
    assert len(data["cassettes"]) == 1
    assert data["cassettes"][0]["valid"] is True
    assert len(data["cassettes"][0]["sha256"]) == 64
    assert data["checksums_valid"] is True
    assert data["installed_workflow_matches"] is True


def test_verification_detects_locally_edited_workflow(installed_bundle):
    installed = installed_bundle / "bundle-test.yaml"
    installed.write_text(WORKFLOW + "\n# locally edited after install\n")

    resp = client.get("/api/templates/bundle-test/verification")
    data = resp.json()["data"]

    assert data["proven"] is True
    assert data["checksums_valid"] is True  # the bundle itself is intact
    assert data["installed_workflow_matches"] is False


def test_verification_detects_tampered_bundle_cassette(installed_bundle):
    _tamper_cassette(installed_bundle / "bundle-test.sctpl")

    resp = client.get("/api/templates/bundle-test/verification")
    data = resp.json()["data"]

    assert data["proven"] is True
    assert data["cassettes"][0]["valid"] is False
    assert data["checksums_valid"] is False


def test_verification_plain_template_is_not_proven(templates_dir):
    (templates_dir / "community" / "plain-local.yaml").write_text(PLAIN_TEMPLATE)

    resp = client.get("/api/templates/plain-local/verification")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"proven": False}


def test_verification_unknown_template_404(templates_dir):
    resp = client.get("/api/templates/no-such-template/verification")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /templates/{name}/verify - the replay proof
# ---------------------------------------------------------------------------


def test_verify_replays_cassettes_and_passes(installed_bundle):
    resp = client.post("/api/templates/bundle-test/verify")
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert data["ok"] is True
    assert data["errors"] == []
    assert len(data["cassettes"]) == 1
    proof = data["cassettes"][0]
    assert proof["passed"] is True
    assert proof["replay_hits"] == 1
    assert proof["replay_misses"] == 0


def test_verify_fails_on_tampered_bundle(installed_bundle):
    _tamper_cassette(installed_bundle / "bundle-test.sctpl")

    resp = client.post("/api/templates/bundle-test/verify")
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert data["ok"] is False
    assert any("checksum mismatch" in e for e in data["errors"])
    assert data["cassettes"] == []  # replay must not run after a checksum failure


def test_verify_404_for_template_without_bundle(templates_dir):
    (templates_dir / "community" / "plain-local.yaml").write_text(PLAIN_TEMPLATE)

    resp = client.post("/api/templates/plain-local/verify")
    assert resp.status_code == 404


def test_verify_404_for_unknown_template(templates_dir):
    resp = client.post("/api/templates/no-such-template/verify")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# bundle_status unit coverage
# ---------------------------------------------------------------------------


def test_bundle_status_reports_manifest_and_checksums(tmp_path):
    from sandcastle.engine.bundle import bundle_status

    bundle = _make_bundle(tmp_path)
    status = bundle_status(bundle)

    assert status["manifest"]["name"] == "bundle-test"
    assert status["manifest"]["license"] == "MIT"
    assert status["workflow"]["valid"] is True
    assert status["cassettes"][0]["step_count"] == 1
    assert status["checksums_valid"] is True


def test_bundle_status_flags_invalid_checksum(tmp_path):
    from sandcastle.engine.bundle import bundle_status

    bundle = _make_bundle(tmp_path)
    _tamper_cassette(bundle)
    status = bundle_status(bundle)

    assert status["cassettes"][0]["valid"] is False
    assert status["checksums_valid"] is False


def test_bundle_status_raises_on_garbage(tmp_path):
    from sandcastle.engine.bundle import BundleError, bundle_status

    garbage = tmp_path / "garbage.sctpl"
    garbage.write_bytes(b"not a zip at all")
    with pytest.raises(BundleError):
        bundle_status(garbage)


def test_verify_response_shape_is_json_safe(installed_bundle):
    """The verify payload round-trips through JSON without loss."""
    resp = client.post("/api/templates/bundle-test/verify")
    data = resp.json()["data"]
    assert json.loads(json.dumps(data)) == data
