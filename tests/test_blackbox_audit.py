"""Tests for the black box flight recorder - signed, tamper-evident cassette chains.

Every recorded step gets a hash-chain entry (record_hash = SHA-256 over the canonical
record + prev_hash) and the chain head is signed with the configured audit key
(HMAC-SHA256). These tests prove: the chain is built correctly, any tampering is
detected at the exact record, compliance_mode="black_box" enforces its preconditions
and auto-records every run, and `sandcastle audit verify` reports PASS/FAIL.
"""

from __future__ import annotations

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest

from sandcastle.__main__ import _cmd_audit_verify
from sandcastle.config import settings
from sandcastle.engine.cassette import (
    CHAIN_GENESIS,
    CassetteStore,
    compute_record_hash,
    default_cassette_path,
    read_attestation,
    verify_cassette,
)
from sandcastle.engine.signing import HmacSigner, get_signer, get_verifier

KEY = "test-audit-key"


def _record_cassette(path, n: int = 3) -> CassetteStore:
    """Record *n* steps into a cassette and save it."""
    store = CassetteStore(path, "record")
    for i in range(n):
        store.put(f"key-{i}", output=f"out-{i}", cost_usd=0.01, model="sonnet", step_id=f"s{i}")
    store.save()
    return store


# ---------------------------------------------------------------------------
# Chain building
# ---------------------------------------------------------------------------


class TestChainBuilding:
    def test_put_appends_linked_chain_entries(self, tmp_path):
        store = _record_cassette(tmp_path / "c.cassette.json", n=3)
        assert len(store.chain) == 3
        assert store.chain[0]["prev_hash"] == CHAIN_GENESIS
        for i, entry in enumerate(store.chain):
            assert entry["index"] == i
            prev = CHAIN_GENESIS if i == 0 else store.chain[i - 1]["record_hash"]
            assert entry["prev_hash"] == prev
            assert entry["record_hash"] == compute_record_hash(
                entry["cache_key"], store.records[entry["cache_key"]], prev
            )
        assert store.chain_head() == store.chain[-1]["record_hash"]

    def test_saved_file_carries_chain_and_head(self, tmp_path):
        path = tmp_path / "c.cassette.json"
        store = _record_cassette(path)
        data = json.loads(path.read_text())
        assert data["meta"]["chain_head"] == store.chain_head()
        assert len(data["chain"]) == 3
        assert data["meta"]["version"] == 2

    def test_empty_cassette_head_is_genesis(self, tmp_path):
        store = CassetteStore(tmp_path / "e.cassette.json", "record")
        assert store.chain_head() == CHAIN_GENESIS
        store.save()
        assert verify_cassette(tmp_path / "e.cassette.json").valid

    def test_rerecording_a_key_rebuilds_a_valid_chain(self, tmp_path):
        path = tmp_path / "r.cassette.json"
        store = CassetteStore(path, "record")
        store.put("k", output="v1", cost_usd=0.01, model="m", step_id="s")
        store.put("k2", output="v2", cost_usd=0.01, model="m", step_id="s2")
        store.put("k", output="v1-again", cost_usd=0.01, model="m", step_id="s")
        store.save()
        v = verify_cassette(path)
        assert v.valid and v.chain_ok
        assert v.chain_length == 2

    def test_signature_written_when_audit_key_set(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "audit_key", KEY)
        path = tmp_path / "signed.cassette.json"
        _record_cassette(path)
        sig = json.loads(path.read_text())["meta"]["chain_signature"]
        assert sig["alg"] == "hmac-sha256"
        assert len(sig["value"]) == 64  # hex SHA-256

    def test_no_signature_without_audit_key(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "audit_key", "")
        path = tmp_path / "unsigned.cassette.json"
        _record_cassette(path)
        assert "chain_signature" not in json.loads(path.read_text())["meta"]


# ---------------------------------------------------------------------------
# Verification + tamper detection
# ---------------------------------------------------------------------------


class TestTamperDetection:
    def test_intact_signed_cassette_passes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "audit_key", KEY)
        path = tmp_path / "ok.cassette.json"
        _record_cassette(path)
        v = verify_cassette(path)
        assert v.valid and v.chain_ok and v.signature_ok is True
        assert v.first_broken_index is None and v.reason is None
        assert v.status == "PASS"

    def test_flipped_byte_fails_at_correct_index(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "audit_key", KEY)
        path = tmp_path / "t.cassette.json"
        _record_cassette(path, n=3)
        data = json.loads(path.read_text())
        data["records"]["key-1"]["output"] = "out-1!"  # flip a byte in record 1
        path.write_text(json.dumps(data))
        v = verify_cassette(path)
        assert not v.valid and not v.chain_ok
        assert v.first_broken_index == 1
        assert "modified" in v.reason
        assert v.status == "FAIL"

    def test_deleted_chain_entry_breaks_linkage(self, tmp_path):
        path = tmp_path / "d.cassette.json"
        _record_cassette(path, n=3)
        data = json.loads(path.read_text())
        del data["chain"][1]
        path.write_text(json.dumps(data))
        v = verify_cassette(path)
        assert not v.valid
        assert v.first_broken_index == 1
        assert "prev_hash" in v.reason

    def test_record_injected_outside_chain_is_detected(self, tmp_path):
        path = tmp_path / "i.cassette.json"
        _record_cassette(path, n=2)
        data = json.loads(path.read_text())
        data["records"]["forged"] = {"output": "evil", "cost_usd": 0, "model": "m", "step_id": "x"}
        path.write_text(json.dumps(data))
        v = verify_cassette(path)
        assert not v.valid
        assert "not covered by the chain" in v.reason

    def test_wrong_key_fails_signature(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "audit_key", KEY)
        path = tmp_path / "w.cassette.json"
        _record_cassette(path)
        v = verify_cassette(path, key="wrong-key")
        assert v.chain_ok is True
        assert v.signature_ok is False
        assert not v.valid

    def test_unsigned_cassette_with_intact_chain_passes_without_signature(self, tmp_path):
        path = tmp_path / "u.cassette.json"
        _record_cassette(path)
        v = verify_cassette(path)
        assert v.valid and v.signature_ok is None

    def test_missing_file_fails_cleanly(self, tmp_path):
        v = verify_cassette(tmp_path / "nope.cassette.json")
        assert not v.valid
        assert "not found" in v.reason

    def test_read_attestation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "audit_key", KEY)
        path = tmp_path / "a.cassette.json"
        store = _record_cassette(path)
        att = read_attestation(path)
        assert att == {"signed": True, "chain_head": store.chain_head()}
        assert read_attestation(tmp_path / "missing.json") is None


# ---------------------------------------------------------------------------
# Signing backends
# ---------------------------------------------------------------------------


class TestSigningBackends:
    def test_hmac_sign_and_verify_roundtrip(self):
        signer = HmacSigner(KEY)
        sig = signer.sign(b"chain-head")
        assert signer.verify(b"chain-head", sig)
        assert not signer.verify(b"different", sig)
        assert not HmacSigner("other").verify(b"chain-head", sig)

    def test_get_signer_none_without_key(self, monkeypatch):
        monkeypatch.setattr(settings, "audit_key", "")
        assert get_signer() is None
        assert get_signer(KEY) is not None

    def test_get_verifier_dispatches_on_alg(self):
        assert get_verifier("hmac-sha256", key=KEY).alg == "hmac-sha256"
        assert get_verifier("ed25519", key=KEY) is None  # not registered (yet)


# ---------------------------------------------------------------------------
# compliance_mode = "black_box" enforcement
# ---------------------------------------------------------------------------

BLACKBOX_WORKFLOW = """name: blackbox-test
description: A single local-model step for black box recording.
default_model: omlx/gemma-3
input_schema:
  required: []
  properties:
    q: { type: string, default: "hi" }
steps:
  - id: think
    prompt: "Answer: {input.q}"
"""


class TestBlackBoxComplianceMode:
    def _mock_settings(self, **overrides):
        mock = MagicMock()
        mock.compliance_mode = "black_box"
        mock.data_residency = "local"
        mock.audit_key = KEY
        mock.max_workflow_depth = 5
        for k, v in overrides.items():
            setattr(mock, k, v)
        return mock

    def test_refuses_without_local_data_residency(self):
        import asyncio

        from sandcastle.engine.dag import build_plan, parse_yaml_string
        from sandcastle.engine.executor import execute_workflow

        wf = parse_yaml_string(BLACKBOX_WORKFLOW)
        plan = build_plan(wf)
        with patch("sandcastle.config.settings", self._mock_settings(data_residency="eu")):
            result = asyncio.run(execute_workflow(wf, plan, {}))
        assert result.status == "failed"
        assert "data_residency=local" in result.error

    def test_refuses_without_audit_key(self):
        import asyncio

        from sandcastle.engine.dag import build_plan, parse_yaml_string
        from sandcastle.engine.executor import execute_workflow

        wf = parse_yaml_string(BLACKBOX_WORKFLOW)
        plan = build_plan(wf)
        with patch("sandcastle.config.settings", self._mock_settings(audit_key="")):
            result = asyncio.run(execute_workflow(wf, plan, {}))
        assert result.status == "failed"
        assert "SANDCASTLE_AUDIT_KEY" in result.error

    @pytest.mark.asyncio
    async def test_run_is_auto_recorded_and_signed(self, tmp_path, monkeypatch):
        """In black_box mode a run with no --record flag still produces a signed,
        verifiable cassette at the canonical path, and the attestation reports it."""
        from sandcastle.engine.dag import build_plan, parse_yaml_string
        from sandcastle.engine.executor import execute_workflow
        from sandcastle.engine.sandshore import SandshoreResult, SandshoreRuntime

        monkeypatch.setattr(settings, "compliance_mode", "black_box")
        monkeypatch.setattr(settings, "data_residency", "local")
        monkeypatch.setattr(settings, "audit_key", KEY)
        monkeypatch.setattr(settings, "data_dir", str(tmp_path))

        wf = parse_yaml_string(BLACKBOX_WORKFLOW)
        plan = build_plan(wf)
        sandbox = MagicMock(spec=SandshoreRuntime)

        async def _query(request):
            return SandshoreResult(
                text="FLIGHT RECORDED", structured_output=None, total_cost_usd=0.0,
                input_tokens=5, output_tokens=5,
            )

        sandbox.query = _query
        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sandbox):
            result = await execute_workflow(wf, plan, {"q": "hi"}, admin_trusted=True)

        assert result.status == "completed"
        cassette_file = default_cassette_path(result.run_id)
        assert cassette_file.exists(), "black box mode must auto-record every run"

        v = verify_cassette(cassette_file)
        assert v.valid and v.chain_ok and v.signature_ok is True
        assert v.chain_length == 1

        att = read_attestation(cassette_file)
        assert att["signed"] is True
        assert att["chain_head"] == v.chain_head


# ---------------------------------------------------------------------------
# `sandcastle audit verify` CLI
# ---------------------------------------------------------------------------


class TestAuditVerifyCli:
    def _args(self, target: str, key: str | None = None) -> argparse.Namespace:
        return argparse.Namespace(target=target, key=key)

    def test_verify_pass(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(settings, "audit_key", KEY)
        path = tmp_path / "ok.cassette.json"
        _record_cassette(path)
        _cmd_audit_verify(self._args(str(path)))
        out = capsys.readouterr().out
        assert "PASS" in out
        assert "Signature:" in out and "valid" in out

    def test_verify_fail_exits_1_with_broken_index(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(settings, "audit_key", KEY)
        path = tmp_path / "bad.cassette.json"
        _record_cassette(path, n=3)
        data = json.loads(path.read_text())
        data["records"]["key-2"]["output"] = "tampered"
        path.write_text(json.dumps(data))
        with pytest.raises(SystemExit) as exc_info:
            _cmd_audit_verify(self._args(str(path)))
        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "chain index 2" in out

    def test_verify_resolves_run_id_under_data_dir(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(settings, "audit_key", KEY)
        monkeypatch.setattr(settings, "data_dir", str(tmp_path))
        run_id = "11111111-2222-3333-4444-555555555555"
        _record_cassette(default_cassette_path(run_id))
        _cmd_audit_verify(self._args(run_id))
        assert "PASS" in capsys.readouterr().out

    def test_verify_unknown_target_exits_1(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(settings, "data_dir", str(tmp_path))
        with pytest.raises(SystemExit) as exc_info:
            _cmd_audit_verify(self._args("no-such-run"))
        assert exc_info.value.code == 1
        assert "No cassette found" in capsys.readouterr().err

    def test_verify_with_explicit_key_flag(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(settings, "audit_key", KEY)
        path = tmp_path / "k.cassette.json"
        _record_cassette(path)
        with pytest.raises(SystemExit) as exc_info:
            _cmd_audit_verify(self._args(str(path), key="wrong-key"))
        assert exc_info.value.code == 1
        assert "INVALID" in capsys.readouterr().out
