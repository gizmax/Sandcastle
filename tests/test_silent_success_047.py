"""0.47 workstream 2: the silent-success sweep.

The failure this guards against is a run that reports ``completed`` with a step
that reports "1 reply created" when nothing was ever sent. The sweep compares
what a run *claimed* against the evidence three independent stores recorded.

The load-bearing test is :class:`TestHonestVersusSilent`: two runs of the same
real workflow, executed through ``execute_workflow`` against the test database,
one left alone and one tampered with in a single targeted way. The sweep must
find exactly the tampered one and say nothing about the honest one.

Everything else in this file is false-positive discipline. The honest fixture
deliberately contains the three shapes a naive sweep would flag wrongly - a GET
that writes no ledger row by design, a dry-run notify that claims nothing, and
(in ``TestReplayIsNotSilent``) a memoized step that has no audit events at all.

Where a fixture is hand-crafted rather than executed, the test says so.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from sandcastle.engine.dag import build_plan, parse_yaml_string
from sandcastle.engine.executor import execute_workflow
from sandcastle.engine.silent_success import (
    is_accept_approval_claim,
    is_notify_delivery_claim,
    sweep_run,
    sweep_runs,
)
from sandcastle.engine.storage import LocalStorage
from sandcastle.models.db import (
    AuditEvent,
    Run,
    RunStatus,
    RunStep,
    StepEffect,
    WorkflowVersion,
    async_session,
)

# A workflow with one of every shape that matters:
#
#   post_invoice  - POST, so the effect guard claims a ledger row
#   lookup_rate   - GET, so effect_mode_for says "live" and NO row is written.
#                   A sweep that flags this is broken.
#   announce      - a real notify delivery: output claims status=delivered
#   log_only      - a dry-run notify: claims status=logged and asserts nothing.
#                   A sweep that flags this is broken.
#   accept_invoice- a checks-only accept: free, deterministic, and it writes a
#                   step.accept event onto the audit chain
WF_TEMPLATE = """
name: {name}
description: Post an invoice, read a rate, announce it, and accept the result
default_model: sonnet
steps:
  - id: post_invoice
    type: http
    http_config:
      url: https://billing.internal/v1/invoices
      method: POST
      body: '{{"amount": 100}}'
  - id: lookup_rate
    type: http
    http_config:
      url: https://rates.internal/v1/usd
      method: GET
  - id: announce
    type: notify
    depends_on: [post_invoice]
    notify_config:
      service: webhook
      channel: https://hooks.internal/invoices
      message: "invoice created"
  - id: log_only
    type: notify
    notify_config:
      service: log
      message: "this one never leaves the process"
  - id: accept_invoice
    type: accept
    depends_on: [post_invoice]
    accept_config:
      target: post_invoice
      checks:
        - type: not_empty
"""


def _wf_yaml() -> str:
    """A uniquely named copy, so WorkflowVersion rows never collide."""
    return WF_TEMPLATE.format(name=f"silent-success-{uuid.uuid4().hex[:8]}")


@pytest.fixture
def no_network():
    """Keep every outbound call inside the process.

    ``httpx.AsyncClient`` covers the two http steps; ``dispatch_webhook`` is
    patched where ``_execute_notify_step`` imports it from, so the notify step
    takes its real delivery branch and produces the real ``status: delivered``
    output shape rather than the dry-run one.
    """
    response = MagicMock()
    response.json.return_value = {"invoice": "INV-1001"}
    response.status_code = 200
    response.text = '{"invoice": "INV-1001"}'
    response.reason_phrase = "OK"
    response.headers = {"content-type": "application/json"}

    async def _request(**kwargs):
        return response

    with patch("httpx.AsyncClient") as client_cls, patch(
        "sandcastle.webhooks.dispatcher.dispatch_webhook",
        new_callable=AsyncMock,
        return_value=True,
    ):
        client = AsyncMock()
        client.request = AsyncMock(side_effect=_request)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client_cls.return_value = client
        yield


# ---------------------------------------------------------------------------
# Fixture builders - real execution, then the worker's post-run bookkeeping
# ---------------------------------------------------------------------------


async def _seed_run(
    tmp_path,
    *,
    tenant_id: str | None = None,
    yaml_text: str | None = None,
    effect_scope_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, str, object]:
    """Execute the workflow for real and leave the DB as a finished run.

    The ``WorkflowVersion`` row is what a server deployment pins a run to, and
    it is what lets the sweep recover step types later - without it the sweep
    would fall back to disk, find nothing, and run at reduced coverage.

    The status/completed_at update after ``execute_workflow`` mirrors
    ``queue/worker.py``: the executor never writes the run row itself.
    """
    text = yaml_text or _wf_yaml()
    wf = parse_yaml_string(text)
    run_id = uuid.uuid4()

    async with async_session() as session:
        # A second run of the same workflow reuses the pinned version, exactly
        # as a real deployment would.
        existing = await session.scalar(
            select(WorkflowVersion).where(
                WorkflowVersion.workflow_name == wf.name,
                WorkflowVersion.version == 1,
            )
        )
        if existing is None:
            session.add(
                WorkflowVersion(
                    workflow_name=wf.name,
                    version=1,
                    yaml_content=text,
                    checksum=hashlib.sha256(text.encode()).hexdigest(),
                    steps_count=len(wf.steps),
                )
            )
        session.add(
            Run(
                id=run_id,
                workflow_name=wf.name,
                status=RunStatus.RUNNING,
                input_data={},
                workflow_version=1,
                tenant_id=tenant_id,
                effect_scope_id=effect_scope_id,
                started_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    result = await execute_workflow(
        workflow=wf,
        plan=build_plan(wf),
        input_data={},
        run_id=str(run_id),
        storage=LocalStorage(str(tmp_path)),
        tenant_id=tenant_id,
        effect_scope_id=str(effect_scope_id) if effect_scope_id else None,
    )

    async with async_session() as session:
        run = await session.get(Run, run_id)
        run.status = (
            RunStatus.COMPLETED if result.status == "completed" else RunStatus.FAILED
        )
        run.completed_at = datetime.now(timezone.utc)
        run.total_cost_usd = result.total_cost_usd
        await session.commit()

    return run_id, text, result


async def _age(run_id: uuid.UUID, hours: float) -> None:
    """Move a run and its step rows *hours* into the past.

    A sweep runs over runs that have already finished, so the fixtures are aged
    past the evidence-lag window rather than the window being switched off. Both
    fixtures get the same treatment, so it cannot tilt the comparison.
    """
    delta = timedelta(hours=hours)
    async with async_session() as session:
        run = await session.get(Run, run_id)
        for attr in ("created_at", "started_at", "completed_at"):
            value = getattr(run, attr)
            if value is not None:
                setattr(run, attr, value - delta)
        rows = (
            (await session.execute(select(RunStep).where(RunStep.run_id == run_id)))
            .scalars()
            .all()
        )
        for row in rows:
            if row.completed_at is not None:
                row.completed_at = row.completed_at - delta
        await session.commit()


async def _delete_effect(scope_id: uuid.UUID, step_id: str) -> int:
    """Targeted tampering: erase the ledger's record of one committed effect.

    This is the hand-crafted half of the definition-of-done. The run really
    executed and really claims delivery; only the durable proof is removed,
    which is the shape of a side effect that was reported but never recorded.
    """
    async with async_session() as session:
        result = await session.execute(
            sa_delete(StepEffect).where(
                StepEffect.effect_scope_id == str(scope_id),
                StepEffect.step_id == step_id,
            )
        )
        await session.commit()
        return result.rowcount or 0


async def _delete_audit(run_id: uuid.UUID, event_type: str, step_id: str) -> int:
    """Targeted tampering: erase one audit event naming *step_id*."""
    removed = 0
    async with async_session() as session:
        rows = (
            (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.run_id == run_id,
                        AuditEvent.event_type == event_type,
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            payload = row.payload if isinstance(row.payload, dict) else {}
            if payload.get("step_id") == step_id:
                await session.delete(row)
                removed += 1
        await session.commit()
    return removed


def _types(report) -> list[str]:
    return sorted(f.finding_type for f in report.findings)


# ---------------------------------------------------------------------------
# The headline guarantee
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestHonestVersusSilent:
    """Two runs, one tamper, exactly one finding."""

    async def test_sweep_finds_only_the_silent_run(self, tmp_path, no_network):
        honest_id, _, honest = await _seed_run(tmp_path)
        silent_id, _, silent = await _seed_run(tmp_path)
        assert honest.status == "completed", honest.error
        assert silent.status == "completed", silent.error

        # The notify step really claimed delivery in both runs.
        assert honest.outputs["announce"]["status"] == "delivered"
        assert silent.outputs["announce"]["status"] == "delivered"

        # ...and only the silent one loses its proof.
        assert await _delete_effect(silent_id, "announce") == 1

        await _age(honest_id, hours=3)
        await _age(silent_id, hours=3)

        report = await sweep_runs(run_ids=[honest_id, silent_id])

        assert report.runs_checked == 2
        assert report.steps_checked > 0
        # Both definitions resolved, so all four pairs actually ran.
        assert report.definition_unresolved == []

        assert len(report.findings) == 1, [f.to_dict() for f in report.findings]
        finding = report.findings[0]
        assert finding.run_id == str(silent_id)
        assert finding.step_id == "announce"
        assert finding.finding_type == "notify_not_in_ledger"
        assert finding.severity == "high"

    async def test_honest_run_alone_is_clean(self, tmp_path, no_network):
        """Anti-false-pass guard: the sweep is not simply silent on everything."""
        honest_id, _, result = await _seed_run(tmp_path)
        assert result.status == "completed", result.error
        await _age(honest_id, hours=3)

        report = await sweep_runs(run_ids=[honest_id])

        assert report.findings == []
        assert report.runs_checked == 1

    async def test_wording_is_a_report_not_a_verdict(self, tmp_path, no_network):
        """A finding must never claim the step failed - only that proof is absent."""
        run_id, _, _ = await _seed_run(tmp_path)
        await _delete_effect(run_id, "announce")
        await _age(run_id, hours=3)

        finding = (await sweep_runs(run_ids=[run_id])).findings[0]

        text = f"{finding.claim} {finding.found} {finding.detail}".lower()
        assert "no committed row" in finding.found
        assert "lacks evidence" in text
        assert "failed" not in finding.found


# ---------------------------------------------------------------------------
# The shapes a naive sweep gets wrong
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFalsePositiveDiscipline:
    async def test_get_step_without_ledger_row_is_not_flagged(
        self, tmp_path, no_network
    ):
        """A GET writes no ledger row by design (effect_mode_for -> "live")."""
        run_id, _, _ = await _seed_run(tmp_path)
        await _age(run_id, hours=3)

        async with async_session() as session:
            rows = (
                (
                    await session.execute(
                        select(StepEffect).where(
                            StepEffect.effect_scope_id == str(run_id)
                        )
                    )
                )
                .scalars()
                .all()
            )
        step_ids = {r.step_id for r in rows}
        # Pin the premise: the GET genuinely has no row, and the POST does.
        assert "lookup_rate" not in step_ids
        assert "post_invoice" in step_ids

        report = await sweep_runs(run_ids=[run_id])
        assert [f for f in report.findings if f.step_id == "lookup_rate"] == []

    async def test_dry_run_notify_claims_nothing(self, tmp_path, no_network):
        """service: log returns status=logged - it asserts nothing about the world."""
        run_id, _, result = await _seed_run(tmp_path)
        assert result.outputs["log_only"]["status"] == "logged"
        assert result.outputs["log_only"]["dry_run"] is True

        # Even with its ledger row erased, it is not a claim, so not a finding.
        await _delete_effect(run_id, "log_only")
        await _age(run_id, hours=3)

        report = await sweep_runs(run_ids=[run_id])
        assert [f for f in report.findings if f.step_id == "log_only"] == []

    async def test_claim_inside_the_lag_window_is_counted_not_flagged(
        self, tmp_path, no_network
    ):
        """Evidence may still be in flight for a claim this fresh."""
        run_id, _, _ = await _seed_run(tmp_path)
        assert await _delete_effect(run_id, "announce") == 1
        # Deliberately NOT aged: the run finished seconds ago.

        report = await sweep_runs(run_ids=[run_id], lag_hours=1.0)

        assert report.findings == []
        assert report.within_lag_window > 0

        # ...and with the window switched off, the same claim is flagged.
        strict = await sweep_runs(run_ids=[run_id], lag_hours=0.0)
        assert _types(strict) == ["notify_not_in_ledger"]

    async def test_claim_beyond_the_ledger_ttl_is_counted_not_flagged(
        self, tmp_path, no_network
    ):
        """Past the TTL the row is gone because it expired, not because it never was."""
        from sandcastle.config import settings

        run_id, _, _ = await _seed_run(tmp_path)
        assert await _delete_effect(run_id, "announce") == 1
        await _age(run_id, hours=24 * (settings.effect_ledger_ttl_days + 2))

        report = await sweep_runs(run_ids=[run_id])

        assert [
            f
            for f in report.findings
            if f.finding_type in ("notify_not_in_ledger", "effect_missing_from_ledger")
        ] == []
        assert report.beyond_effect_ttl > 0

    async def test_ledger_kill_switch_skips_the_ledger_pairs(
        self, tmp_path, no_network
    ):
        """With the ledger off there is no evidence side, so there is no check."""
        run_id, _, _ = await _seed_run(tmp_path)
        assert await _delete_effect(run_id, "announce") == 1
        await _age(run_id, hours=3)

        with patch("sandcastle.config.settings.effect_ledger_enabled", False):
            report = await sweep_runs(run_ids=[run_id])

        assert report.ledger_enabled is False
        assert report.findings == []

    async def test_index_mismatch_is_suppressed_not_flagged(
        self, tmp_path, no_network
    ):
        """Hand-crafted: a committed row whose parallel_index we did not expect.

        The strict match misses, the loose match hits, and the sweep declines to
        invent a finding out of an index shape it did not foresee - counting the
        case instead so the trade stays visible.
        """
        run_id, _, _ = await _seed_run(tmp_path)
        async with async_session() as session:
            row = await session.scalar(
                select(StepEffect).where(
                    StepEffect.effect_scope_id == str(run_id),
                    StepEffect.step_id == "announce",
                )
            )
            row.parallel_index = 7
            await session.commit()
        await _age(run_id, hours=3)

        report = await sweep_runs(run_ids=[run_id])

        assert report.findings == []
        assert report.suppressed_loose_match == 1

    async def test_non_completed_runs_are_not_swept(self, tmp_path, no_network):
        """A failed run is not claiming success, so it has nothing to cross-check."""
        run_id, _, _ = await _seed_run(tmp_path)
        await _delete_effect(run_id, "announce")
        await _age(run_id, hours=3)
        async with async_session() as session:
            run = await session.get(Run, run_id)
            run.status = RunStatus.FAILED
            await session.commit()

        report = await sweep_runs(run_ids=[run_id])
        assert report.runs_checked == 0
        assert report.findings == []


@pytest.mark.asyncio
class TestReplayIsNotSilent:
    """The biggest trap: a memoized step has no audit events at all.

    ``_begin_effect_guard`` returns before ``_execute_step_with_retry_inner``,
    which is where ``step.started`` / ``step.completed`` are emitted. A sweep
    that did not exclude ``replayed`` rows would flag every replay, fork and
    crash-resume in the system.
    """

    async def test_second_run_in_the_same_scope_produces_no_findings(
        self, tmp_path, no_network
    ):
        scope = uuid.uuid4()
        first_id, yaml_text, first = await _seed_run(
            tmp_path, effect_scope_id=scope
        )
        assert first.status == "completed", first.error

        second_id, _, second = await _seed_run(
            tmp_path, yaml_text=yaml_text, effect_scope_id=scope
        )
        assert second.status == "completed", second.error

        # Pin the premise: the replay really did memoize, and really has no
        # step.completed event for the memoized step.
        async with async_session() as session:
            replayed = (
                (
                    await session.execute(
                        select(RunStep).where(
                            RunStep.run_id == second_id, RunStep.replayed.is_(True)
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert {r.step_id for r in replayed} >= {"post_invoice", "announce"}

        await _age(first_id, hours=3)
        await _age(second_id, hours=3)

        report = await sweep_runs(run_ids=[first_id, second_id])

        assert report.findings == []
        assert report.replayed_skipped >= 2


@pytest.mark.asyncio
class TestOtherPairs:
    async def test_missing_step_accept_event_is_flagged(self, tmp_path, no_network):
        """Hand-crafted: the accept pack survives, its audit record does not."""
        run_id, _, result = await _seed_run(tmp_path)
        assert result.outputs["accept_invoice"]["decision"] == "approved"
        assert await _delete_audit(run_id, "step.accept", "accept_invoice") == 1
        await _age(run_id, hours=3)

        report = await sweep_runs(run_ids=[run_id])

        assert _types(report) == ["accept_verdict_not_on_chain"]
        finding = report.findings[0]
        assert finding.step_id == "accept_invoice"
        assert finding.severity == "medium"

    async def test_missing_step_completed_event_is_flagged(
        self, tmp_path, no_network
    ):
        """Hand-crafted: a POST completes with nothing on the audit chain."""
        run_id, _, _ = await _seed_run(tmp_path)
        assert await _delete_audit(run_id, "step.completed", "post_invoice") == 1
        await _age(run_id, hours=3)

        report = await sweep_runs(run_ids=[run_id])

        assert _types(report) == ["step_completed_not_on_chain"]
        assert report.findings[0].step_id == "post_invoice"

    async def test_missing_http_effect_is_flagged_as_effect_missing(
        self, tmp_path, no_network
    ):
        """Pair 2: an unsafe-method http step with no committed row.

        Distinguished from pair 1 by finding type - this one needs the workflow
        definition to know the step is a POST.
        """
        run_id, _, _ = await _seed_run(tmp_path)
        assert await _delete_effect(run_id, "post_invoice") == 1
        await _age(run_id, hours=3)

        report = await sweep_runs(run_ids=[run_id])

        assert _types(report) == ["effect_missing_from_ledger"]
        assert report.findings[0].step_id == "post_invoice"

    async def test_unsettled_ledger_row_is_reported_with_what_was_found(
        self, tmp_path, no_network
    ):
        """Hand-crafted: the claim row settled, the ledger row never did."""
        run_id, _, _ = await _seed_run(tmp_path)
        async with async_session() as session:
            row = await session.scalar(
                select(StepEffect).where(
                    StepEffect.effect_scope_id == str(run_id),
                    StepEffect.step_id == "announce",
                )
            )
            row.status = "in_flight"
            await session.commit()
        await _age(run_id, hours=3)

        report = await sweep_runs(run_ids=[run_id])

        assert _types(report) == ["effect_unsettled"]
        assert "in_flight" in report.findings[0].found

    async def test_zero_ledger_coverage_downgrades_severity(
        self, tmp_path, no_network
    ):
        """A run with no committed effects at all reads as infrastructure, not silence."""
        run_id, _, _ = await _seed_run(tmp_path)
        async with async_session() as session:
            await session.execute(
                sa_delete(StepEffect).where(
                    StepEffect.effect_scope_id == str(run_id)
                )
            )
            await session.commit()
        await _age(run_id, hours=3)

        report = await sweep_runs(run_ids=[run_id])

        assert report.findings, "the findings should still be reported, just quieter"
        assert all(f.severity == "medium" for f in report.findings)
        assert all("ledger_coverage=none" in f.detail for f in report.findings)


@pytest.mark.asyncio
class TestScopeAndEntryPoints:
    async def test_window_and_tenant_scoping(self, tmp_path, no_network):
        tenant = f"tenant-{uuid.uuid4().hex[:8]}"
        mine_id, _, _ = await _seed_run(tmp_path, tenant_id=tenant)
        theirs_id, _, _ = await _seed_run(tmp_path, tenant_id="somebody-else")
        await _delete_effect(mine_id, "announce")
        await _delete_effect(theirs_id, "announce")
        await _age(mine_id, hours=3)
        await _age(theirs_id, hours=3)

        report = await sweep_runs(
            since=datetime.now(timezone.utc) - timedelta(days=1),
            tenant_id=tenant,
        )

        assert {f.run_id for f in report.findings} == {str(mine_id)}

    async def test_since_excludes_older_runs(self, tmp_path, no_network):
        tenant = f"tenant-{uuid.uuid4().hex[:8]}"
        run_id, _, _ = await _seed_run(tmp_path, tenant_id=tenant)
        await _delete_effect(run_id, "announce")
        await _age(run_id, hours=48)

        inside = await sweep_runs(
            since=datetime.now(timezone.utc) - timedelta(days=7), tenant_id=tenant
        )
        outside = await sweep_runs(
            since=datetime.now(timezone.utc) - timedelta(hours=6), tenant_id=tenant
        )

        assert len(inside.findings) == 1
        assert outside.runs_checked == 0

    async def test_sweep_run_single_entry_point(self, tmp_path, no_network):
        run_id, _, _ = await _seed_run(tmp_path)
        await _delete_effect(run_id, "announce")
        await _age(run_id, hours=3)

        report = await sweep_run(str(run_id))
        assert _types(report) == ["notify_not_in_ledger"]

    async def test_sweep_run_tolerates_a_bad_id(self):
        report = await sweep_run("not-a-uuid")
        assert report.findings == []
        assert report.runs_checked == 0

    async def test_report_meta_is_json_serialisable(self, tmp_path, no_network):
        import json

        run_id, _, _ = await _seed_run(tmp_path)
        await _delete_effect(run_id, "announce")
        await _age(run_id, hours=3)

        payload = json.loads(json.dumps((await sweep_run(str(run_id))).to_dict()))
        assert payload["meta"]["findings"] == 1
        assert payload["findings"][0]["finding_type"] == "notify_not_in_ledger"


class TestClaimDetectors:
    """The output shapes, pinned against the executor's real return values."""

    def test_notify_delivery_shape(self):
        assert is_notify_delivery_claim(
            {
                "service": "webhook",
                "channel": "https://hooks.internal/x",
                "message": "hi",
                "status": "delivered",
                "delivery": {"ok": True},
            }
        )

    def test_notify_dry_run_shape_is_not_a_claim(self):
        assert not is_notify_delivery_claim(
            {
                "service": "log",
                "channel": "",
                "message": "hi",
                "status": "logged",
                "dry_run": True,
            }
        )

    def test_fan_out_aggregate_list_is_not_a_claim(self):
        """The fan-out aggregate row's output is a list, never a claim."""
        assert not is_notify_delivery_claim(
            [{"status": "delivered", "delivery": {"ok": True}}]
        )

    def test_accept_pack_needs_every_field(self):
        pack = {
            "decision": "approved",
            "reason": "all deterministic checks passed",
            "targets": ["write"],
            "rounds_used": 1,
            "max_rounds": 1,
            "rounds": [],
        }
        assert is_accept_approval_claim(pack)
        assert not is_accept_approval_claim({**pack, "decision": "rejected"})
        # "decision" alone is common workflow data, not an accept pack.
        assert not is_accept_approval_claim({"decision": "approved"})


class TestApiEndpoint:
    """Shape check on the HTTP surface; the logic is covered above."""

    @staticmethod
    def _client():
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from sandcastle.api.routes import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app, raise_server_exceptions=False)

    def test_returns_findings_and_meta(self):
        resp = self._client().get("/audit/silent-success?since=1h")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "findings" in data
        assert "ledger_enabled" in data["meta"]

    def test_rejects_a_bad_run_id(self):
        resp = self._client().get("/audit/silent-success?run_id=not-a-uuid")
        assert resp.status_code == 400

    def test_rejects_a_bad_since(self):
        resp = self._client().get("/audit/silent-success?since=next%20tuesday")
        assert resp.status_code == 400


class TestCli:
    """``sandcastle audit silent-success`` - wiring, wording and exit code."""

    @staticmethod
    def _args(**overrides):
        import argparse

        base = {
            "since": "48h",
            "run": None,
            "lag_hours": None,
            "json": False,
            "api_url": None,
            "api_key": None,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    @staticmethod
    def _body(findings, **meta):
        base_meta = {
            "runs_checked": 2,
            "steps_checked": 9,
            "findings": len(findings),
            "ledger_enabled": True,
            "within_lag_window": 0,
            "beyond_effect_ttl": 0,
            "replayed_skipped": 0,
            "unresolved_steps": 0,
            "suppressed_loose_match": 0,
            "definition_unresolved": [],
        }
        base_meta.update(meta)
        return {"data": {"findings": findings, "meta": base_meta}}

    def test_parser_accepts_the_subcommand(self):
        from sandcastle.__main__ import _build_parser

        args = _build_parser().parse_args(
            ["audit", "silent-success", "--since", "48h", "--lag-hours", "0"]
        )
        assert args.audit_action == "silent-success"
        assert args.since == "48h"
        assert args.lag_hours == 0.0

    def test_clean_sweep_prints_pass_and_exits_zero(self, capsys):
        from sandcastle.__main__ import _cmd_audit_silent_success

        with patch("sandcastle.__main__._api_get", return_value=self._body([])):
            _cmd_audit_silent_success(self._args())
        out = capsys.readouterr().out
        assert "PASS" in out
        assert "Swept 2 completed run(s)" in out

    def test_findings_exit_nonzero_and_read_as_a_report(self, capsys):
        from sandcastle.__main__ import _cmd_audit_silent_success

        body = self._body(
            [
                {
                    "run_id": str(uuid.uuid4()),
                    "workflow_name": "wf",
                    "step_id": "announce",
                    "parallel_index": None,
                    "finding_type": "notify_not_in_ledger",
                    "claim": "notify step output claims delivery",
                    "expected_evidence": "a committed run_step_effects row",
                    "found": "no committed row in run_step_effects for this effect scope",
                    "severity": "high",
                    "detail": "the claim lacks evidence",
                    "occurred_at": None,
                }
            ],
            within_lag_window=3,
        )
        with patch("sandcastle.__main__._api_get", return_value=body):
            with pytest.raises(SystemExit) as exc:
                _cmd_audit_silent_success(self._args())
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "announce" in out
        assert "claim(s) lack evidence" in out
        assert "not the same as the step having failed" in out
        assert "Not checked: 3 step(s) inside the evidence-lag window" in out

    def test_single_run_form_sends_run_id_not_since(self):
        from sandcastle.__main__ import _cmd_audit_silent_success

        run_id = str(uuid.uuid4())
        mock_get = MagicMock(return_value=self._body([]))
        with patch("sandcastle.__main__._api_get", mock_get):
            _cmd_audit_silent_success(self._args(run=run_id))
        params = mock_get.call_args.kwargs["params"]
        assert params == {"run_id": run_id}
