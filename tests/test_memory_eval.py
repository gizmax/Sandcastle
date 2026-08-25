"""Grading a memory scope: `sandcastle memory eval`.

The point of the module under test is that every score is a division of two
counted things. These tests are the proof: the fixture vault has a known
composition, and every expected number below is worked out by hand in a
comment next to the assertion. **The test is the formula's documentation** -
if an assertion here has to change, the formula changed, and the arithmetic
in the comment says what it changed from.

The fixture (`graded_vault`, scope `workflow:graded`, TTL 90 days):

    billing.md    4 live   1 fresh, 1 expired-no-veto, 1 expired-vetoed,
                           1 rewritten twice (depth 2, confirmed once)
                  1 tombstone   reason ttl            -> a real removal
                  3 attic       2 superseded + 1 ttl
    deploy.md     2 live   1 fresh, 1 rewritten once (depth 1, confirmed 2x)
                  1 tombstone   reason merged-into-*  -> NOT a removal
                  1 attic       superseded
    notes.md     13 live   1 stale twin (verbatim copy of deploy's old text)
                           + 12 fillers, all fresh
    bulky.md      1 live   ~27 KB of text -> 83% of the 32 KB file cap

    live = 4 + 2 + 13 + 1 = 20      removals = 1      merges = 1
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sandcastle.engine.memory_eval import (
    CONTESTED_DEPTH,
    NEAR_CAP,
    TARGET_FORGET_RATE,
    MemoryEvalReport,
    evaluate_scope,
    parse_attic,
)
from sandcastle.engine.memory_fs import (
    MAX_DOMAIN_FILE_BYTES,
    FilesystemMemoryBackend,
    render_domain,
)

SCOPE = "workflow:graded"
TTL_DAYS = 90


# ---------------------------------------------------------------------------
# Fixture construction - the markdown is written directly so the composition
# is exact. Going through save() would let the write policy (confirm /
# supersede / append) decide the numbers, and then the test would document
# that policy instead of this formula.
# ---------------------------------------------------------------------------


def _ts(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _entry(entry_id: str, text: str, *, days_ago: float = 1, confirmations: int = 0) -> dict:
    stamp = _ts(days_ago)
    return {
        "id": entry_id,
        "text": text,
        "created": stamp,
        "updated": stamp,
        "confirmations": confirmations,
        "run_id": "run-fixture",
        # Deliberately empty: _load_sync scores max(text overlap, keyword
        # overlap), and empty keywords keep the retrieval arithmetic below
        # dependent on the text alone.
        "keywords": [],
        "tags": [],
    }


CHURN_CURRENT = "Payment terms are now net forty five for enterprise accounts."
CHURN_OLD_RECENT = "Payment terms are net thirty five for enterprise accounts."
CHURN_OLD_FIRST = "Payment terms are net thirty for every account we invoice."

DEPLOY_CURRENT = "Deployment now happens Tuesdays using the canary pipeline."
DEPLOY_OLD = "Deployment happens on Fridays via the release pipeline."

_FILLER_TAIL = [
    "commanders", "supervisors", "coordinators", "specialists", "analysts",
    "engineers", "reviewers", "operators", "responders", "auditors",
    "planners", "schedulers",
]


def _write_domain(
    backend: FilesystemMemoryBackend,
    domain: str,
    entries: list[dict],
    tombstones: list[dict] | None = None,
) -> None:
    vault = backend._vault
    scope_dir = vault.scope_dir(SCOPE)
    scope_dir.mkdir(parents=True, exist_ok=True)
    path = vault.domain_path(scope_dir, domain)
    path.write_text(
        render_domain(
            domain=domain,
            scope_id=SCOPE,
            revision=1,
            entries=entries,
            tombstones=list(tombstones or []),
        ),
        encoding="utf-8",
    )


def _write_attic(backend: FilesystemMemoryBackend, domain: str, records: list[dict]) -> None:
    vault = backend._vault
    scope_dir = vault.scope_dir(SCOPE)
    vault.append_attic(vault.attic_path(scope_dir, domain), records)


@pytest.fixture
def graded_vault(tmp_path) -> FilesystemMemoryBackend:
    """A vault whose composition is stated in the module docstring."""
    backend = FilesystemMemoryBackend(tmp_path / "vault", use_git=False)

    _write_domain(
        backend,
        "billing",
        [
            _entry("fresh0001", "Acme invoices arrive on fifteenth and settle net thirty."),
            # 200 days old, never confirmed -> consolidation will drop it.
            _entry(
                "stale0001",
                "Legacy ledger export ran nightly from mainframe batch jobs.",
                days_ago=200,
            ),
            # 200 days old, confirmed 3x -> the veto makes it immortal.
            _entry(
                "immortal1",
                "Quarterly close signed off by controller before audit.",
                days_ago=200,
                confirmations=3,
            ),
            _entry("churn0001", CHURN_CURRENT, days_ago=2, confirmations=1),
        ],
        [{"id": "gone00001", "reason": "ttl", "at": _ts(30), "excerpt": "an expired note"}],
    )
    _write_attic(
        backend,
        "billing",
        [
            {"id": "churn0001", "text": CHURN_OLD_FIRST, "at": _ts(10),
             "reason": "superseded", "overlap": 0.62},
            {"id": "churn0001", "text": CHURN_OLD_RECENT, "at": _ts(3),
             "reason": "superseded", "overlap": 0.71},
            {"id": "gone00001", "text": "an expired note", "at": _ts(30), "reason": "ttl"},
        ],
    )

    _write_domain(
        backend,
        "deploy",
        [
            _entry("deploy0001", "Builds are published from main branch nightly by automation."),
            _entry("deploy0002", DEPLOY_CURRENT, confirmations=2),
        ],
        # A merge moves a file, not a fact: this must not count as forgetting.
        [{"id": "old-deploy", "reason": "merged-into-deploy", "at": _ts(5),
          "excerpt": "2 entries moved"}],
    )
    _write_attic(
        backend,
        "deploy",
        [{"id": "deploy0002", "text": DEPLOY_OLD, "at": _ts(4),
          "reason": "superseded", "overlap": 0.55}],
    )

    notes = [
        # The stale twin: a verbatim survival of what deploy0002 used to say,
        # updated more recently so it wins the rank tie-break.
        _entry("twin00001", DEPLOY_OLD, days_ago=0),
    ]
    notes.extend(
        _entry(
            f"filler{i:04d}",
            f"Runbook chapter documents escalation ladders for regional incident {tail}.",
        )
        for i, tail in enumerate(_FILLER_TAIL)
    )
    _write_domain(backend, "notes", notes)

    # ~27 KB of a three-word vocabulary: big enough to sit at 83% of the
    # 32 KB file cap, distinctive enough not to disturb any probe above.
    _write_domain(
        backend,
        "bulky",
        [_entry("bulky0001", "aggregate telemetry sample " * 1000)],
    )

    backend._vault.rebuild_index(SCOPE, backend._vault.scope_dir(SCOPE))
    return backend


async def _report(backend: FilesystemMemoryBackend, **kwargs) -> MemoryEvalReport:
    kwargs.setdefault("ttl_days", TTL_DAYS)
    return await evaluate_scope(SCOPE, backend=backend, **kwargs)


# ---------------------------------------------------------------------------


class TestScopeTotals:
    @pytest.mark.asyncio
    async def test_totals_match_the_fixture(self, graded_vault):
        report = await _report(graded_vault)
        assert report.backend == "filesystem"
        assert report.tenant == "_shared"
        assert report.ttl_days == TTL_DAYS
        # 4 billing + 2 deploy + 13 notes + 1 bulky
        assert report.totals["live_entries"] == 20
        assert report.totals["domains"] == 4
        # 1 ttl (billing) + 1 merged-into (deploy)
        assert report.totals["tombstones"] == 2
        # 3 billing + 1 deploy
        assert report.totals["attic_records"] == 4
        # churn0001 twice + deploy0002 once
        assert report.totals["superseded"] == 3
        # immortal1 3 + churn0001 1 + deploy0002 2
        assert report.totals["confirmations"] == 6


class TestStaleness:
    @pytest.mark.asyncio
    async def test_score_and_parts(self, graded_vault):
        report = await _report(graded_vault)
        parts = report.staleness.parts

        assert parts["live_entries"] == 20
        assert parts["immortal"] == 1          # immortal1: 200d old, 3 confirmations
        assert parts["expired_pending"] == 1   # stale0001: 200d old, 0 confirmations
        assert parts["stale"] == 2
        # 2 / 20 = 0.10
        assert parts["stale_share"] == 0.1
        # 1 - 0.10 = 0.90
        assert report.staleness.score == 0.9

    @pytest.mark.asyncio
    async def test_verdict_names_both_populations(self, graded_vault):
        report = await _report(graded_vault)
        verdict = report.staleness.verdict
        assert "2 of 20" in verdict
        assert "90-day TTL" in verdict
        assert "never be" in verdict  # the immortal population is called out

    @pytest.mark.asyncio
    async def test_worst_offenders_are_named_by_id(self, graded_vault):
        report = await _report(graded_vault)
        worst = {w["id"] for w in report.staleness.worst}
        assert worst == {"stale0001", "immortal1"}
        by_id = {w["id"]: w for w in report.staleness.worst}
        # 200 days against a 90-day TTL
        assert by_id["immortal1"]["ttl_ratio"] == pytest.approx(200 / 90, abs=0.02)
        assert by_id["immortal1"]["immortal"] is True
        assert by_id["stale0001"]["immortal"] is False

    @pytest.mark.asyncio
    async def test_ttl_disabled_is_unmeasurable_not_zero(self, graded_vault):
        report = await _report(graded_vault, ttl_days=0)
        assert report.staleness.score is None
        assert "TTL is disabled" in report.staleness.verdict
        # and it drops out of the mean rather than dragging it to zero
        assert report.components["staleness"] is None
        assert report.overall == pytest.approx(
            (0.476 + 0.95 + 0.5) / 3, abs=1e-4,
        )


class TestForgettingHealth:
    @pytest.mark.asyncio
    async def test_score_counts_removals_not_merges(self, graded_vault):
        report = await _report(graded_vault)
        parts = report.forgetting.parts

        assert parts["removed"] == 1   # the one `ttl` tombstone
        assert parts["merges"] == 1    # `merged-into-deploy` is not forgetting
        # written = live + removed = 20 + 1 = 21
        assert parts["written"] == 21
        # 1 / 21 = 0.047619 -> 0.0476
        assert parts["forget_rate"] == 0.0476
        # 0.0476 / 0.10 = 0.476
        assert report.forgetting.score == 0.476
        assert parts["target_forget_rate"] == TARGET_FORGET_RATE
        assert parts["superseded"] == 3
        assert parts["tombstones_by_reason"] == {"ttl": 1, "merged-into": 1}

    @pytest.mark.asyncio
    async def test_consolidation_lag_is_flagged(self, graded_vault):
        report = await _report(graded_vault)
        # stale0001 is past TTL with no veto and still present
        assert report.forgetting.parts["consolidation_lag"] is True
        assert "consolidation has not run" in report.forgetting.verdict

    @pytest.mark.asyncio
    async def test_a_growing_only_store_scores_worse_than_one_with_throughput(
        self, tmp_path,
    ):
        """The critique's central claim, as an assertion.

        Two stores with identical live contents. One has forgotten a couple of
        entries and has the attic and tombstones to show for it; the other has
        only ever appended. The second must score strictly worse - and zero,
        because 'never removed anything' is the floor, not a low fraction.
        """
        live = [_entry(f"e{i:04d}", f"Fact number {i} about regional supply routing.")
                for i in range(8)]

        growing = FilesystemMemoryBackend(tmp_path / "growing", use_git=False)
        _write_domain(growing, "facts", live, [])

        forgetting = FilesystemMemoryBackend(tmp_path / "forgetting", use_git=False)
        _write_domain(
            forgetting,
            "facts",
            live,
            [
                {"id": "d0", "reason": "ttl", "at": _ts(20), "excerpt": "gone"},
                {"id": "d1", "reason": "size-cap", "at": _ts(15), "excerpt": "evicted"},
            ],
        )
        _write_attic(
            forgetting,
            "facts",
            [
                {"id": "d0", "text": "an old fact", "at": _ts(20), "reason": "ttl"},
                {"id": "d1", "text": "another old fact", "at": _ts(15), "reason": "size-cap"},
            ],
        )

        grown = await _report(growing)
        forgot = await _report(forgetting)

        # growing: 8 written, 0 removed -> the append-only floor
        assert grown.forgetting.parts["written"] == 8
        assert grown.forgetting.parts["removed"] == 0
        assert grown.forgetting.score == 0.0
        assert "never forgotten anything" in grown.forgetting.verdict
        assert "append-only log" in grown.forgetting.verdict

        # forgetting: 10 written, 2 removed -> 0.2, well past the 0.10 target
        assert forgot.forgetting.parts["written"] == 10
        assert forgot.forgetting.parts["removed"] == 2
        assert forgot.forgetting.parts["forget_rate"] == 0.2
        assert forgot.forgetting.score == 1.0

        assert forgot.forgetting.score > grown.forgetting.score

    @pytest.mark.asyncio
    async def test_superseding_without_removing_is_still_append_only(self, tmp_path):
        """Updating is not forgetting, and the verdict has to say so."""
        backend = FilesystemMemoryBackend(tmp_path / "v", use_git=False)
        _write_domain(backend, "facts", [_entry("only00001", CHURN_CURRENT)], [])
        _write_attic(
            backend, "facts",
            [{"id": "only00001", "text": CHURN_OLD_RECENT, "at": _ts(2),
              "reason": "superseded", "overlap": 0.7}],
        )
        report = await _report(backend)
        assert report.forgetting.score == 0.0
        assert report.forgetting.parts["superseded"] == 1
        assert "updating is not forgetting" in report.forgetting.verdict


class TestContradictionPressure:
    @pytest.mark.asyncio
    async def test_score_counts_only_repeatedly_rewritten_facts(self, graded_vault):
        report = await _report(graded_vault)
        parts = report.contradiction.parts

        # churn0001 (depth 2) and deploy0002 (depth 1) were both rewritten...
        assert parts["rewritten_once_or_more"] == 2
        # ...but only churn0001 crosses the contested line
        assert parts["contested"] == 1
        assert parts["contested_depth"] == CONTESTED_DEPTH
        # 1 / 20 = 0.05
        assert parts["churn_share"] == 0.05
        assert report.contradiction.score == 0.95

    @pytest.mark.asyncio
    async def test_confirm_versus_supersede_ratio(self, graded_vault):
        report = await _report(graded_vault)
        parts = report.contradiction.parts
        assert parts["confirmations_total"] == 6   # 3 + 1 + 2
        assert parts["supersedes_total"] == 3      # 2 + 1
        assert parts["confirm_supersede_ratio"] == 2.0

    @pytest.mark.asyncio
    async def test_deepest_chains_are_named(self, graded_vault):
        report = await _report(graded_vault)
        assert report.contradiction.worst[0]["id"] == "churn0001"
        assert report.contradiction.worst[0]["supersede_depth"] == 2
        assert report.contradiction.worst[1]["id"] == "deploy0002"

    @pytest.mark.asyncio
    async def test_domain_near_its_file_cap_is_named(self, graded_vault):
        report = await _report(graded_vault)
        parts = report.contradiction.parts
        near = {d["domain"] for d in parts["domains_near_cap"]}
        assert near == {"bulky"}
        fill = next(d["fill"] for d in parts["domains_near_cap"] if d["domain"] == "bulky")
        assert NEAR_CAP <= fill < 1.0
        assert "near-cap files: bulky" in report.contradiction.verdict
        # 4 domains against the 120 cap
        assert parts["scope_fill"] == round(4 / 120, 4)
        assert parts["scope_near_cap"] is False
        assert parts["max_domain_file_bytes"] == MAX_DOMAIN_FILE_BYTES


class TestRetrievalSanity:
    @pytest.mark.asyncio
    async def test_stale_twin_outranks_the_current_fact(self, graded_vault):
        """The probe is the *superseded* text, so a surviving paraphrase wins.

        `twin00001` holds a verbatim copy of what `deploy0002` used to say.
        Querying with that old text scores the twin 1.0 (identical word set)
        and `deploy0002` 4/7 = 0.571, so the twin ranks first and retrieval
        hands back yesterday's answer.
        """
        report = await _report(graded_vault)
        parts = report.retrieval.parts

        assert parts["probes_total"] == 2       # churn0001, deploy0002
        assert parts["probes_run"] == 2
        assert parts["clean"] == 1              # churn0001 survives its probe
        assert parts["stale_ahead"] == 1        # deploy0002 does not
        assert parts["unreachable"] == 0
        assert report.retrieval.score == 0.5

        failure = next(f for f in report.retrieval.worst if f["id"] == "deploy0002")
        assert failure["failure"] == "stale_ahead"
        assert "twin00001" in failure["detail"]

    @pytest.mark.asyncio
    async def test_a_rewrite_sharing_no_words_becomes_unreachable(self, tmp_path):
        backend = FilesystemMemoryBackend(tmp_path / "v", use_git=False)
        _write_domain(
            backend,
            "facts",
            [_entry("rewrite01", "Beta corporation ships industrial hardware every quarter.")],
            [],
        )
        _write_attic(
            backend, "facts",
            [{"id": "rewrite01", "text": "Acme invoices settle within thirty calendar days.",
              "at": _ts(2), "reason": "superseded", "overlap": 0.45}],
        )
        backend._vault.rebuild_index(SCOPE, backend._vault.scope_dir(SCOPE))

        report = await _report(backend)
        assert report.retrieval.parts["unreachable"] == 1
        assert report.retrieval.score == 0.0
        assert report.retrieval.worst[0]["failure"] == "unreachable"
        assert "out of reach of its own history" in report.retrieval.worst[0]["detail"]

    @pytest.mark.asyncio
    async def test_no_supersede_history_is_unmeasurable_not_a_pass(self, tmp_path):
        backend = FilesystemMemoryBackend(tmp_path / "v", use_git=False)
        _write_domain(backend, "facts", [_entry("plain0001", "A fact nobody has revised.")], [])
        report = await _report(backend)
        assert report.retrieval.score is None
        assert "unmeasurable here, not passing" in report.retrieval.verdict
        assert report.components["retrieval"] is None

    @pytest.mark.asyncio
    async def test_probe_sampling_is_reported_not_hidden(self, graded_vault):
        report = await _report(graded_vault, max_probes=1)
        parts = report.retrieval.parts
        assert parts["probes_total"] == 2
        assert parts["probes_run"] == 1
        assert "Sampled 1 of 2 candidates" in report.retrieval.verdict

    @pytest.mark.asyncio
    async def test_probes_go_through_the_backend_load_api(self, graded_vault):
        """No bespoke retrieval: every probe is a public `load()` call."""
        calls: list[tuple] = []
        real_load = graded_vault.load

        async def _spy(scope_id, query, limit):
            calls.append((scope_id, query, limit))
            return await real_load(scope_id, query, limit)

        with patch.object(graded_vault, "load", _spy):
            await _report(graded_vault, probe_limit=7)

        assert len(calls) == 2
        assert {c[0] for c in calls} == {SCOPE}
        assert {c[2] for c in calls} == {7}
        # each probe queries with an atticked text, never the current one
        assert DEPLOY_OLD in [c[1] for c in calls]
        assert CHURN_OLD_RECENT in [c[1] for c in calls]
        assert CHURN_CURRENT not in [c[1] for c in calls]


class TestPerDomainAndOverall:
    @pytest.mark.asyncio
    async def test_per_domain_scores(self, graded_vault):
        report = await _report(graded_vault)
        by_domain = {d.domain: d for d in report.domains}
        assert set(by_domain) == {"billing", "bulky", "deploy", "notes"}

        billing = by_domain["billing"]
        assert billing.live_entries == 4
        # 2 of 4 past TTL -> 1 - 0.5
        assert billing.staleness == 0.5
        # 1 removed of (4 + 1) written = 0.2 -> saturates the 0.10 target
        assert billing.forgetting == 1.0
        # churn0001 of 4 -> 1 - 0.25
        assert billing.contradiction == 0.75
        assert billing.retrieval == 1.0

        deploy = by_domain["deploy"]
        assert deploy.live_entries == 2
        assert deploy.staleness == 1.0
        # its only tombstone is a merge, so nothing was removed
        assert deploy.forgetting == 0.0
        assert deploy.contradiction == 1.0
        assert deploy.retrieval == 0.0

        notes = by_domain["notes"]
        assert notes.live_entries == 13
        assert notes.forgetting == 0.0
        assert notes.retrieval is None   # nothing in notes was ever superseded

        assert by_domain["bulky"].near_cap is True
        assert by_domain["notes"].near_cap is False

    @pytest.mark.asyncio
    async def test_overall_is_the_mean_of_the_printed_components(self, graded_vault):
        report = await _report(graded_vault)
        assert report.components == {
            "staleness": 0.9,
            "forgetting": 0.476,
            "contradiction": 0.95,
            "retrieval": 0.5,
        }
        # (0.9 + 0.476 + 0.95 + 0.5) / 4 = 0.7065
        assert report.overall == pytest.approx(0.7065, abs=1e-4)

    @pytest.mark.asyncio
    async def test_every_score_is_recomputable_from_its_parts(self, graded_vault):
        """The module's central promise, asserted rather than asserted-in-prose."""
        report = await _report(graded_vault)

        s = report.staleness.parts
        assert report.staleness.score == pytest.approx(
            1 - (s["immortal"] + s["expired_pending"]) / s["live_entries"], abs=1e-4,
        )
        f = report.forgetting.parts
        assert report.forgetting.score == pytest.approx(
            min(1.0, (f["removed"] / f["written"]) / TARGET_FORGET_RATE), abs=1e-3,
        )
        c = report.contradiction.parts
        assert report.contradiction.score == pytest.approx(
            1 - c["contested"] / c["live_entries"], abs=1e-4,
        )
        r = report.retrieval.parts
        assert report.retrieval.score == pytest.approx(
            r["clean"] / r["probes_run"], abs=1e-4,
        )


class TestReadOnly:
    @pytest.mark.asyncio
    async def test_scoring_never_writes_to_the_vault(self, graded_vault):
        """Read-only is why no scope lock is taken; this is the evidence."""
        root = Path(graded_vault.root)

        def snapshot() -> dict[str, tuple[int, bytes]]:
            out = {}
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    out[str(path.relative_to(root))] = (
                        path.stat().st_mtime_ns, path.read_bytes(),
                    )
            return out

        before = snapshot()
        await _report(graded_vault)
        assert snapshot() == before


class TestEmptyAndMalformed:
    @pytest.mark.asyncio
    async def test_scope_that_was_never_written(self, tmp_path):
        backend = FilesystemMemoryBackend(tmp_path / "empty", use_git=False)
        report = await evaluate_scope(
            "workflow:nothing-here", backend=backend, ttl_days=TTL_DAYS,
        )
        assert report.totals["live_entries"] == 0
        assert report.staleness.score is None
        assert report.forgetting.score is None
        assert report.contradiction.score is None
        assert report.retrieval.score is None
        assert report.overall is None
        assert any("never been written" in c for c in report.caveats)

    @pytest.mark.asyncio
    async def test_invalid_scope_is_rejected(self, graded_vault):
        with pytest.raises(ValueError):
            await evaluate_scope("not a scope at all", backend=graded_vault)

    def test_parse_attic_skips_mangled_records(self):
        text = (
            "### x\n<!-- attic: {\"id\":\"good0001\",\"reason\":\"superseded\"} -->\n"
            "### y\n<!-- attic: {not json} -->\n"
            "### z\n<!-- attic: {\"reason\":\"superseded\"} -->\n"   # no id
        )
        records = parse_attic(text)
        assert [r["id"] for r in records] == ["good0001"]

    def test_parse_attic_on_empty_input(self):
        assert parse_attic("") == []


# ---------------------------------------------------------------------------
# The honesty section: a backend without vault metadata
# ---------------------------------------------------------------------------


class _FakeMem0Backend:
    """A MemoryBackend that exposes exactly what _Mem0Backend exposes.

    Deliberately not a mem0 mock: the point is the *Protocol* surface, which
    is all the scorer is allowed to use. Nothing here has confirmations,
    tombstones, an attic or a domain, because mem0 does not offer them
    through this seam either.
    """

    name = "local"

    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.calls: list[tuple] = []

    async def load(self, scope_id: str, query: str, limit: int) -> list[dict]:
        self.calls.append((scope_id, query, limit))
        return [
            {
                "id": item["id"],
                "memory": item["memory"],
                "metadata": {},
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
            }
            for item in self.items[:limit]
        ]

    async def save(self, scope_id, content, metadata, run_id):  # pragma: no cover
        raise AssertionError("eval must not write")

    async def delete(self, memory_id, scope_id=None):  # pragma: no cover
        raise AssertionError("eval must not write")

    async def delete_all(self, scope_id):  # pragma: no cover
        raise AssertionError("eval must not write")

    async def health(self) -> None:  # pragma: no cover
        return None


def _mem0_items() -> list[dict]:
    return [
        {"id": "m1", "memory": "fresh", "created_at": _ts(1), "updated_at": _ts(1)},
        {"id": "m2", "memory": "fresh too", "created_at": _ts(3), "updated_at": _ts(3)},
        {"id": "m3", "memory": "ancient", "created_at": _ts(400), "updated_at": _ts(400)},
        {"id": "m4", "memory": "also ancient", "created_at": _ts(300), "updated_at": _ts(300)},
    ]


class TestNonVaultBackend:
    @pytest.mark.asyncio
    async def test_age_is_measurable_and_is_the_only_thing_that_is(self):
        backend = _FakeMem0Backend(_mem0_items())
        report = await evaluate_scope(SCOPE, backend=backend, ttl_days=TTL_DAYS)

        assert report.backend == "local"
        assert report.totals["live_entries"] == 4
        # m3 and m4 are past 90 days
        assert report.staleness.parts["expired"] == 2
        assert report.staleness.score == 0.5
        assert report.forgetting is None
        assert report.contradiction is None
        assert report.retrieval is None
        assert report.components == {"staleness": 0.5}
        assert report.overall == 0.5

    @pytest.mark.asyncio
    async def test_expired_is_described_as_hidden_not_removed(self):
        backend = _FakeMem0Backend(_mem0_items())
        report = await evaluate_scope(SCOPE, backend=backend, ttl_days=TTL_DAYS)
        verdict = report.staleness.verdict
        assert "read filter" in verdict
        assert "not a forgetter" in verdict
        assert any("hide it" in c for c in report.staleness.caveats)

    @pytest.mark.asyncio
    async def test_unmeasurable_metrics_are_named_with_reasons(self):
        backend = _FakeMem0Backend(_mem0_items())
        report = await evaluate_scope(SCOPE, backend=backend, ttl_days=TTL_DAYS)

        named = {item["metric"] for item in report.not_measurable}
        assert named == {
            "staleness (immortal vs expired-pending split)",
            "forgetting health",
            "contradiction pressure",
            "domain and cap pressure",
            "retrieval sanity",
        }
        # every entry carries a reason, not just a name
        assert all(len(item["reason"]) > 40 for item in report.not_measurable)

    @pytest.mark.asyncio
    async def test_no_confirmations_means_no_immortal_split(self):
        backend = _FakeMem0Backend(_mem0_items())
        report = await evaluate_scope(SCOPE, backend=backend, ttl_days=TTL_DAYS)
        # the vault's two-way split is absent rather than faked as zero
        assert "immortal" not in report.staleness.parts
        assert "confirmations_veto" not in report.staleness.parts

    @pytest.mark.asyncio
    async def test_a_backend_that_cannot_be_read_says_so(self):
        class _Broken(_FakeMem0Backend):
            async def load(self, scope_id, query, limit):
                raise RuntimeError("qdrant unreachable")

        report = await evaluate_scope(SCOPE, backend=_Broken([]), ttl_days=TTL_DAYS)
        assert report.overall is None
        assert report.not_measurable[0]["metric"] == "everything"
        assert "qdrant unreachable" in report.not_measurable[0]["reason"]

    @pytest.mark.asyncio
    async def test_real_mem0_adapter_routes_to_the_limited_report(self):
        """The real `_Mem0Backend` must take the limited path, not the vault one.

        The stubbed client reproduces mem0 2.0.18's actual behaviour: it
        rejects `user_id=` as a top-level kwarg on `get_all`
        (mem0/memory/main.py:165, called at :1282). `_Mem0Backend.load` still
        passes it, so a read raises today - see docs/design/047-memory-eval.md
        section 6. The scorer's job is to report that honestly rather than to
        emit a zero-entry report that reads like a healthy empty scope.
        """
        from unittest.mock import MagicMock

        from sandcastle.engine.memory import _Mem0Backend

        client = MagicMock()
        client.get_all.side_effect = ValueError(
            "Top-level entity parameters frozenset({'user_id'}) are not "
            "supported in get_all(). Use filters={'user_id': '...'} instead.",
        )
        with patch("sandcastle.engine.memory._get_client", return_value=client):
            report = await evaluate_scope(
                SCOPE, backend=_Mem0Backend("local"), ttl_days=TTL_DAYS,
            )

        assert report.backend == "local"
        assert report.overall is None
        assert report.not_measurable[0]["metric"] == "everything"
        assert "user_id" in report.not_measurable[0]["reason"]
        # and it is not silently reported as an empty, healthy scope
        assert report.totals["live_entries"] == 0
        assert report.staleness is None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCli:
    def test_subcommand_is_registered_with_the_expected_flags(self):
        from sandcastle.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args(
            ["memory", "eval", "workflow:x", "--tenant", "acme", "--ttl-days", "30"],
        )
        assert args.command == "memory"
        assert args.memory_action == "eval"
        assert args.scope == "workflow:x"
        assert args.tenant == "acme"
        assert args.ttl_days == 30
        # defaults documented in the design doc
        assert args.probe_limit == 10
        assert args.max_probes == 50

    def test_tenant_flag_prefixes_the_scope(self, graded_vault, capsys):
        from sandcastle.__main__ import _cmd_memory_eval

        seen: list[str] = []

        async def _fake(scope_id, **kwargs):
            seen.append(scope_id)
            return await evaluate_scope(SCOPE, backend=graded_vault, ttl_days=TTL_DAYS)

        with patch("sandcastle.engine.memory_eval.evaluate_scope", _fake):
            _cmd_memory_eval(
                SimpleNamespace(
                    scope="workflow:graded", tenant="acme", backend=None,
                    ttl_days=None, probe_limit=10, max_probes=50, json=False,
                ),
            )
        assert seen == ["tenant:acme/workflow:graded"]

    def test_human_output_shows_every_metric_and_its_verdict(self, graded_vault, capsys):
        from sandcastle.__main__ import _cmd_memory_eval

        with patch(
            "sandcastle.engine.memory._get_backend", return_value=graded_vault,
        ):
            _cmd_memory_eval(
                SimpleNamespace(
                    scope=SCOPE, tenant=None, backend=None, ttl_days=TTL_DAYS,
                    probe_limit=10, max_probes=50, json=False,
                ),
            )
        out = capsys.readouterr().out
        for name in ("staleness", "forgetting", "contradiction", "retrieval"):
            assert name in out
        assert "0.90" in out          # staleness score
        assert "2 of 20 entries are past the 90-day TTL" in out
        assert "1 of 21 entries ever written have been removed" in out
        assert "removed=1" in out     # the counts the score divides
        assert "written=21" in out
        assert "DOMAIN" in out and "billing" in out and "bulky" in out
        assert "overall" in out

    def test_json_output_is_machine_readable(self, graded_vault, capsys):
        from sandcastle.__main__ import _cmd_memory_eval

        with patch(
            "sandcastle.engine.memory._get_backend", return_value=graded_vault,
        ):
            _cmd_memory_eval(
                SimpleNamespace(
                    scope=SCOPE, tenant=None, backend=None, ttl_days=TTL_DAYS,
                    probe_limit=10, max_probes=50, json=True,
                ),
            )
        payload = json.loads(capsys.readouterr().out)
        assert payload["scope_id"] == SCOPE
        assert payload["metrics"]["staleness"]["score"] == 0.9
        assert payload["metrics"]["forgetting"]["parts"]["removed"] == 1
        assert payload["overall"] == pytest.approx(0.7065, abs=1e-4)
        assert len(payload["domains"]) == 4
        assert len(payload["entries"]) == 20

    def test_honesty_section_is_printed_for_a_non_vault_backend(self, capsys):
        from sandcastle.__main__ import _cmd_memory_eval

        backend = _FakeMem0Backend(_mem0_items())
        with patch("sandcastle.engine.memory._get_backend", return_value=backend):
            _cmd_memory_eval(
                SimpleNamespace(
                    scope=SCOPE, tenant=None, backend="local", ttl_days=TTL_DAYS,
                    probe_limit=10, max_probes=50, json=False,
                ),
            )
        out = capsys.readouterr().out
        assert "not measurable on this backend" in out
        assert "forgetting health" in out
        assert "contradiction pressure" in out
        assert "The filesystem vault records all of the above" in out

    def test_bad_scope_exits_nonzero(self, capsys):
        from sandcastle.__main__ import _cmd_memory_eval

        with pytest.raises(SystemExit) as exc:
            _cmd_memory_eval(
                SimpleNamespace(
                    scope="nonsense scope", tenant=None, backend=None, ttl_days=None,
                    probe_limit=10, max_probes=50, json=False,
                ),
            )
        assert exc.value.code == 1

    def test_memory_without_a_subcommand_prints_usage(self, capsys):
        from sandcastle.__main__ import _cmd_memory

        with pytest.raises(SystemExit) as exc:
            _cmd_memory(SimpleNamespace(memory_action=None))
        assert exc.value.code == 1
        assert "sandcastle memory eval" in capsys.readouterr().err
