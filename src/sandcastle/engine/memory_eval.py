"""Grade a memory scope - staleness, forgetting, contradiction, retrieval.

Retrieval quality answers "can I find what I stored". It cannot answer
"should that still be there". A store can score perfectly on recall@k and
still be three-tenths stale with nothing ever deleted, which is a log with
good manners rather than a memory.

The 0.46 vault already records what the second question needs: a
confirmation counter, timestamps, a supersede chain with an attic,
tombstones with reasons. This module reads it back and scores it.

Every number here is a division of two counted things, and both counts ship
in the report next to it. There is no learned weight and no composite whose
parts are hidden - a reader can recompute any score by hand from the
integers printed beside it. Where a threshold was invented rather than
derived from the vault's own policy, it is a module constant with the
reasoning attached.

Scoring is **read-only and takes no lock**. See ``docs/design/047-memory-eval.md``
section 8 for why, and for what that costs when a scope is written mid-scan.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sandcastle.engine.memory_fs import (
    ATTIC_MAX_AGE_DAYS,
    CONFIRMATIONS_VETO,
    MAX_DOMAIN_FILE_BYTES,
    MAX_DOMAINS_PER_SCOPE,
    SUPERSEDE_OVERLAP,
    FilesystemMemoryBackend,
    _overlap,
    _parse_ts,
    _word_set,
    render_domain,
    split_scope,
)

logger = logging.getLogger(__name__)

# The private helpers above are imported rather than reimplemented on purpose:
# `_overlap` and `_word_set` are the exact measure the vault uses to decide a
# supersede, and a second copy here would drift from the policy it is grading.
# See design doc section 9 follow-up 1 - a public read-only facade on the
# backend would remove the need.

# ---------------------------------------------------------------------------
# Invented constants. Each one is a judgement call, not a measurement; the
# design doc lists them together so a reviewer can argue with each.
# ---------------------------------------------------------------------------

#: Forget rate at which the forgetting score saturates at 1.0. This is a floor
#: test - "does this store forget at all, at a token rate" - not a claim that
#: one in ten is the right number. Changing it moves every forgetting score.
TARGET_FORGET_RATE = 0.10

#: Share of a hard cap at which a domain or scope is called "near cap". Chosen
#: to warn with roughly a fifth of the headroom left.
NEAR_CAP = 0.80

#: A fact rewritten this many times or more is contested rather than merely
#: maintained. One rewrite is ordinary; the line has to go somewhere.
CONTESTED_DEPTH = 2

#: Retrieval sanity: how many results each probe asks for, and how many probes
#: run at most. Each probe is a full linear scan of the scope.
DEFAULT_PROBE_LIMIT = 10
DEFAULT_MAX_PROBES = 50

#: Cap on entries pulled from a non-filesystem backend for the limited report.
DEFAULT_MAX_ENTRIES = 1000

#: Tombstone reasons that mean an entry was removed. `merged-into-*` is a
#: domain file moving, not a fact being forgotten, and is counted separately.
REMOVAL_REASONS = frozenset({"ttl", "size-cap", "deleted"})

_ATTIC_RE = re.compile(r"<!--\s*attic:\s*(\{.*?\})\s*-->", re.DOTALL)


# ---------------------------------------------------------------------------
# Attic parsing
#
# memory_fs writes attic records but never reads them back as records: its own
# purge (`_purge_attic_locked`) only needs the timestamp. Supersede history
# lives nowhere else, so the parser lives here.
# ---------------------------------------------------------------------------


def parse_attic(text: str) -> list[dict]:
    """Parse an ``_attic/<domain>.md`` file into its records.

    Tolerant in the same way :func:`memory_fs.parse_domain` is: a mangled
    record is skipped, not fatal. The attic invites hand edits as much as the
    domain files do.
    """
    records: list[dict] = []
    for match in _ATTIC_RE.finditer(text or ""):
        try:
            record = json.loads(match.group(1))
        except (ValueError, TypeError):
            continue
        if isinstance(record, dict) and record.get("id"):
            records.append(record)
    return records


# ---------------------------------------------------------------------------
# Typed report
# ---------------------------------------------------------------------------


@dataclass
class EntryGrade:
    """One live entry, with everything the metrics counted about it."""

    id: str
    domain: str
    created: str
    updated: str
    age_days: float
    confirmations: int
    expired: bool
    vetoed: bool
    staleness_ratio: float | None
    supersede_depth: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Metric:
    """A score, the counts it divides, and one sentence of plain English."""

    name: str
    score: float | None
    verdict: str
    parts: dict[str, Any] = field(default_factory=dict)
    worst: list[dict] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DomainGrade:
    """The same metrics, restricted to one domain file."""

    domain: str
    live_entries: int
    fill: float
    near_cap: bool
    staleness: float | None
    forgetting: float | None
    contradiction: float | None
    retrieval: float | None
    parts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MemoryEvalReport:
    """The graded report for one scope on one backend."""

    scope_id: str
    backend: str
    tenant: str = ""
    scope_path: str = ""
    generated_at: str = ""
    ttl_days: int = 0
    totals: dict[str, Any] = field(default_factory=dict)
    staleness: Metric | None = None
    forgetting: Metric | None = None
    contradiction: Metric | None = None
    retrieval: Metric | None = None
    domains: list[DomainGrade] = field(default_factory=list)
    entries: list[EntryGrade] = field(default_factory=list)
    overall: float | None = None
    components: dict[str, float | None] = field(default_factory=dict)
    not_measurable: list[dict] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    @property
    def metrics(self) -> list[Metric]:
        return [m for m in (self.staleness, self.forgetting,
                            self.contradiction, self.retrieval) if m is not None]

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "scope_id": self.scope_id,
            "backend": self.backend,
            "tenant": self.tenant,
            "scope_path": self.scope_path,
            "generated_at": self.generated_at,
            "ttl_days": self.ttl_days,
            "totals": self.totals,
            "overall": self.overall,
            "components": self.components,
            "metrics": {m.name: m.to_dict() for m in self.metrics},
            "domains": [d.to_dict() for d in self.domains],
            "entries": [e.to_dict() for e in self.entries],
            "not_measurable": self.not_measurable,
            "caveats": self.caveats,
        }
        return out


# ---------------------------------------------------------------------------
# Scope scan (filesystem vault)
# ---------------------------------------------------------------------------


@dataclass
class _Scan:
    scope_id: str
    tenant: str
    scope_dir: Path
    domains: list[str]
    entries: list[dict]        # entry dicts with an extra "domain" key
    tombstones: list[dict]     # tombstone dicts with an extra "domain" key
    attic: list[dict]          # attic records with an extra "domain" key
    fill: dict[str, float]     # domain -> rendered bytes / MAX_DOMAIN_FILE_BYTES


def _scan_vault(backend: FilesystemMemoryBackend, scope_id: str) -> _Scan:
    """Read every domain file, tombstone and attic record in a scope.

    Deliberately not through ``load()``: that returns a ranked and truncated
    view, and a count that depends on the ranker is not a count.
    """
    vault = backend._vault  # read-only; see design doc follow-up 1
    tenant, _ = split_scope(scope_id)
    scope_dir = vault.scope_dir(scope_id)

    entries: list[dict] = []
    tombstones: list[dict] = []
    attic: list[dict] = []
    fill: dict[str, float] = {}
    domains: list[str] = []

    if not scope_dir.is_dir():
        return _Scan(scope_id, tenant, scope_dir, [], [], [], [], {})

    domains = vault.list_domains(scope_dir)
    for domain in domains:
        path = vault.domain_path(scope_dir, domain)
        fm, domain_entries, domain_tombs = vault.read_domain(path)
        for entry in domain_entries:
            entries.append({**entry, "domain": domain})
        for tomb in domain_tombs:
            tombstones.append({**tomb, "domain": domain})
        # Measured the way _enforce_size_cap measures it (memory_fs.py:1240),
        # so "near cap" means near the thing that actually raises.
        rendered = render_domain(
            domain=domain,
            scope_id=scope_id,
            revision=int(fm.get("revision") or 0),
            entries=domain_entries,
            tombstones=domain_tombs,
        )
        fill[domain] = len(rendered.encode("utf-8")) / MAX_DOMAIN_FILE_BYTES

        attic_path = vault.attic_path(scope_dir, domain)
        if attic_path.is_file() and not attic_path.is_symlink():
            try:
                text = attic_path.read_text(encoding="utf-8")
            except OSError as exc:  # pragma: no cover - defensive
                logger.warning("Unreadable attic %s: %s", attic_path, exc)
                text = ""
            for record in parse_attic(text):
                attic.append({**record, "domain": domain})

    return _Scan(scope_id, tenant, scope_dir, domains, entries, tombstones, attic, fill)


# ---------------------------------------------------------------------------
# Metric 1 - staleness
# ---------------------------------------------------------------------------


def _grade_entries(
    entries: list[dict],
    depth_by_id: dict[str, int],
    ttl_days: int,
    now: datetime,
) -> list[EntryGrade]:
    """Age every entry against the TTL and the confirmations veto.

    Age is measured from ``updated`` falling back to ``created`` - the same
    field ``_consolidate_sync`` uses (memory_fs.py:1394), so this never calls
    an entry stale that the forgetter considers fresh.
    """
    graded: list[EntryGrade] = []
    for entry in entries:
        stamp = _parse_ts(entry.get("updated") or entry.get("created"))
        age_days = max(0.0, (now - stamp).total_seconds() / 86400.0) if stamp else 0.0
        confirmations = int(entry.get("confirmations") or 0)
        expired = bool(ttl_days > 0 and stamp is not None and age_days > ttl_days)
        graded.append(
            EntryGrade(
                id=str(entry.get("id") or ""),
                domain=str(entry.get("domain") or ""),
                created=str(entry.get("created") or ""),
                updated=str(entry.get("updated") or ""),
                age_days=round(age_days, 2),
                confirmations=confirmations,
                expired=expired,
                vetoed=confirmations >= CONFIRMATIONS_VETO,
                staleness_ratio=(round(age_days / ttl_days, 3) if ttl_days > 0 else None),
                supersede_depth=depth_by_id.get(str(entry.get("id") or ""), 0),
            ),
        )
    return graded


def _staleness_parts(graded: list[EntryGrade], ttl_days: int) -> dict[str, Any]:
    live = len(graded)
    immortal = sum(1 for g in graded if g.expired and g.vetoed)
    pending = sum(1 for g in graded if g.expired and not g.vetoed)
    return {
        "live_entries": live,
        "ttl_days": ttl_days,
        "confirmations_veto": CONFIRMATIONS_VETO,
        "immortal": immortal,
        "expired_pending": pending,
        "stale": immortal + pending,
        "stale_share": round((immortal + pending) / live, 4) if live else 0.0,
    }


def _staleness_score(parts: dict[str, Any], ttl_days: int) -> float | None:
    if ttl_days <= 0:
        return None
    if not parts["live_entries"]:
        return None
    return round(1.0 - parts["stale_share"], 4)


def _staleness_metric(graded: list[EntryGrade], ttl_days: int) -> Metric:
    parts = _staleness_parts(graded, ttl_days)
    score = _staleness_score(parts, ttl_days)
    live = parts["live_entries"]

    if ttl_days <= 0:
        verdict = (
            "TTL is disabled (MEMORY_MAX_AGE_DAYS=0): nothing in this scope can "
            "expire, so staleness is unmeasurable and forgetting is manual."
        )
    elif not live:
        verdict = "This scope holds no live entries; there is nothing to age."
    elif parts["stale"] == 0:
        verdict = f"All {live} entries are inside the {ttl_days}-day TTL."
    else:
        verdict = (
            f"{parts['stale']} of {live} entries are past the {ttl_days}-day TTL"
            f" - {parts['immortal']} held there permanently by the "
            f"{CONFIRMATIONS_VETO}-confirmation veto, {parts['expired_pending']} "
            f"awaiting the next consolidation."
        )
        if parts["immortal"]:
            verdict += (
                f" The {parts['immortal']} vetoed one(s) will never be "
                f"re-examined by the vault."
            )

    worst = sorted(
        (g for g in graded if g.expired),
        key=lambda g: (-(g.staleness_ratio or 0.0), g.id),
    )[:10]
    return Metric(
        name="staleness",
        score=score,
        verdict=verdict,
        parts=parts,
        worst=[
            {
                "id": g.id,
                "domain": g.domain,
                "age_days": g.age_days,
                "ttl_ratio": g.staleness_ratio,
                "confirmations": g.confirmations,
                "immortal": g.vetoed,
            }
            for g in worst
        ],
    )


# ---------------------------------------------------------------------------
# Metric 2 - forgetting health
# ---------------------------------------------------------------------------


def _forgetting_parts(
    live: int, tombstones: list[dict], attic: list[dict], pending: int,
) -> dict[str, Any]:
    by_reason: dict[str, int] = {}
    merges = 0
    removed = 0
    for tomb in tombstones:
        reason = str(tomb.get("reason") or "")
        key = "merged-into" if reason.startswith("merged-into") else reason
        by_reason[key] = by_reason.get(key, 0) + 1
        if reason.startswith("merged-into"):
            merges += 1
        elif reason in REMOVAL_REASONS:
            removed += 1
    superseded = sum(1 for a in attic if a.get("reason") == "superseded")
    written = live + removed
    return {
        "live_entries": live,
        "removed": removed,
        "written": written,
        "forget_rate": round(removed / written, 4) if written else 0.0,
        "target_forget_rate": TARGET_FORGET_RATE,
        "merges": merges,
        "superseded": superseded,
        "tombstones_by_reason": by_reason,
        "attic_records": len(attic),
        "expired_pending": pending,
        "consolidation_lag": pending > 0,
    }


def _forgetting_score(parts: dict[str, Any]) -> float | None:
    if not parts["written"]:
        return None
    if parts["removed"] == 0 and parts["live_entries"] > 0:
        return 0.0
    return round(min(1.0, parts["forget_rate"] / TARGET_FORGET_RATE), 4)


def _forgetting_metric(
    live: int, tombstones: list[dict], attic: list[dict], pending: int,
) -> Metric:
    parts = _forgetting_parts(live, tombstones, attic, pending)
    score = _forgetting_score(parts)

    if not parts["written"]:
        verdict = "Nothing has ever been written to this scope."
    elif parts["removed"] == 0:
        verdict = (
            f"This store has never forgotten anything: {parts['written']} entries "
            f"written, 0 removed. It is an append-only log."
        )
        if parts["superseded"]:
            verdict += (
                f" It does update itself - {parts['superseded']} supersede(s) in "
                f"the attic - but updating is not forgetting."
            )
    else:
        verdict = (
            f"{parts['removed']} of {parts['written']} entries ever written have "
            f"been removed ({parts['forget_rate'] * 100:.1f}%), against a "
            f"{TARGET_FORGET_RATE * 100:.0f}% target."
        )
    if parts["consolidation_lag"]:
        verdict += (
            f" {parts['expired_pending']} entry(ies) are sitting past TTL with no "
            f"veto, which means consolidation has not run since they expired."
        )

    caveats = [
        "Tombstones are the durable removal record; the attic is purged at "
        f"{ATTIC_MAX_AGE_DAYS} days, so attic counts under-report old removals.",
        "delete_all() unlinks whole domain files and takes their tombstones with "
        "them (memory_fs.py:1332): a scope that was erased and refilled is "
        "indistinguishable from one that never forgot.",
    ]
    return Metric(
        name="forgetting",
        score=score,
        verdict=verdict,
        parts=parts,
        caveats=caveats,
    )


# ---------------------------------------------------------------------------
# Metric 3 - contradiction pressure
# ---------------------------------------------------------------------------


def _supersede_depths(attic: list[dict]) -> dict[str, int]:
    """How many times each id has been rewritten, per the attic."""
    depths: dict[str, int] = {}
    for record in attic:
        if record.get("reason") != "superseded":
            continue
        key = str(record.get("id") or "")
        if key:
            depths[key] = depths.get(key, 0) + 1
    return depths


def _contradiction_parts(
    graded: list[EntryGrade],
    fill: dict[str, float],
    domain_count: int,
) -> dict[str, Any]:
    live = len(graded)
    contested = sum(1 for g in graded if g.supersede_depth >= CONTESTED_DEPTH)
    rewritten = sum(1 for g in graded if g.supersede_depth >= 1)
    supersedes = sum(g.supersede_depth for g in graded)
    confirmations = sum(g.confirmations for g in graded)
    near = sorted(d for d, f in fill.items() if f >= NEAR_CAP)
    scope_fill = domain_count / MAX_DOMAINS_PER_SCOPE if MAX_DOMAINS_PER_SCOPE else 0.0
    return {
        "live_entries": live,
        "rewritten_once_or_more": rewritten,
        "contested": contested,
        "contested_depth": CONTESTED_DEPTH,
        "churn_share": round(contested / live, 4) if live else 0.0,
        "supersedes_total": supersedes,
        "confirmations_total": confirmations,
        "confirm_supersede_ratio": round(confirmations / max(1, supersedes), 3),
        "domains": domain_count,
        "scope_fill": round(scope_fill, 4),
        "scope_near_cap": scope_fill >= NEAR_CAP,
        "domains_near_cap": [{"domain": d, "fill": round(fill[d], 4)} for d in near],
        "near_cap_threshold": NEAR_CAP,
        "max_domains_per_scope": MAX_DOMAINS_PER_SCOPE,
        "max_domain_file_bytes": MAX_DOMAIN_FILE_BYTES,
    }


def _contradiction_score(parts: dict[str, Any]) -> float | None:
    if not parts["live_entries"]:
        return None
    return round(1.0 - parts["churn_share"], 4)


def _contradiction_metric(
    graded: list[EntryGrade], fill: dict[str, float], domain_count: int,
) -> Metric:
    parts = _contradiction_parts(graded, fill, domain_count)
    score = _contradiction_score(parts)
    live = parts["live_entries"]

    if not live:
        verdict = "This scope holds no live entries; there is nothing to contradict."
    elif parts["contested"] == 0:
        verdict = (
            f"No fact in this scope has been rewritten {CONTESTED_DEPTH}+ times "
            f"({parts['rewritten_once_or_more']} of {live} rewritten once); "
            f"{parts['confirmations_total']} confirmation(s) against "
            f"{parts['supersedes_total']} supersede(s)."
        )
    else:
        verdict = (
            f"{parts['contested']} of {live} entries have been rewritten "
            f"{CONTESTED_DEPTH}+ times; {parts['confirmations_total']} "
            f"confirmation(s) against {parts['supersedes_total']} supersede(s) "
            f"(ratio {parts['confirm_supersede_ratio']})."
        )
    if parts["domains_near_cap"] or parts["scope_near_cap"]:
        names = ", ".join(d["domain"] for d in parts["domains_near_cap"]) or "-"
        verdict += (
            f" Capacity: {parts['domains']}/{MAX_DOMAINS_PER_SCOPE} domains "
            f"({parts['scope_fill'] * 100:.0f}% of the scope cap), near-cap "
            f"files: {names}. Hitting either cap raises, it does not warn."
        )

    worst = sorted(
        (g for g in graded if g.supersede_depth >= 1),
        key=lambda g: (-g.supersede_depth, g.id),
    )[:10]
    return Metric(
        name="contradiction",
        score=score,
        verdict=verdict,
        parts=parts,
        worst=[
            {
                "id": g.id,
                "domain": g.domain,
                "supersede_depth": g.supersede_depth,
                "confirmations": g.confirmations,
            }
            for g in worst
        ],
        caveats=[
            f"Supersede depth is read from the attic, which is purged at "
            f"{ATTIC_MAX_AGE_DAYS} days: this measures recent churn, not "
            f"lifetime churn.",
        ],
    )


# ---------------------------------------------------------------------------
# Metric 4 - retrieval sanity
#
# The only metric that goes through the backend's own public retrieval. If
# load() is broken, this score must show it broken - so no bespoke ranker and
# no reaching into _load_sync.
#
# The probe is the *superseded* text, not the current text. Querying an entry
# with its own current text is the friendliest possible question and is very
# nearly guaranteed to rank it first - the overlap measure scores an identical
# word set 1.0 and nothing can outrank that, so it would produce a metric that
# passes by construction. Querying with what the entry used to say is the
# question where a stale answer is actually dangerous: "the thing we used to
# believe" is exactly the shape of a query that should return the revision.
# ---------------------------------------------------------------------------


async def _retrieval_metric(
    backend: Any,
    scope_id: str,
    graded: list[EntryGrade],
    entries_by_id: dict[str, dict],
    attic: list[dict],
    *,
    probe_limit: int,
    max_probes: int,
) -> tuple[Metric, dict[tuple[str, str], bool]]:
    """Returns the metric plus a ``(domain, id) -> clean`` map for per-domain rollup."""
    # Most recent superseded text per id: the belief immediately before this one.
    latest_old: dict[str, tuple[str, str]] = {}
    for record in attic:
        if record.get("reason") != "superseded":
            continue
        key = str(record.get("id") or "")
        text = str(record.get("text") or "").strip()
        stamp = str(record.get("at") or "")
        if not key or not text:
            continue
        if key not in latest_old or stamp >= latest_old[key][0]:
            latest_old[key] = (stamp, text)

    candidates = sorted(
        (g for g in graded if g.supersede_depth >= 1 and g.id in latest_old),
        key=lambda g: g.id,
    )
    probes_total = len(candidates)
    sample = candidates[:max_probes]

    parts: dict[str, Any] = {
        "probes_total": probes_total,
        "probes_run": len(sample),
        "probe_limit": probe_limit,
        "clean": 0,
        "unreachable": 0,
        "stale_ahead": 0,
    }
    failures: list[dict] = []
    probe_results: dict[tuple[str, str], bool] = {}

    for grade in sample:
        old_text = latest_old[grade.id][1]
        current = str(entries_by_id.get(grade.id, {}).get("text") or "")
        old_words = _word_set(old_text)
        current_words = _word_set(current)

        results = await backend.load(scope_id, old_text, probe_limit)
        ranked_ids = [str(r.get("id") or "") for r in results]

        if grade.id not in ranked_ids:
            parts["unreachable"] += 1
            probe_results[(grade.domain, grade.id)] = False
            failures.append(
                {
                    "id": grade.id,
                    "domain": grade.domain,
                    "failure": "unreachable",
                    "detail": (
                        f"querying what this entry used to say does not retrieve "
                        f"it within limit {probe_limit}: the rewrite put the fact "
                        f"out of reach of its own history"
                    ),
                },
            )
            continue

        position = ranked_ids.index(grade.id)
        twins: list[str] = []
        for ahead in results[:position]:
            ahead_id = str(ahead.get("id") or "")
            if ahead_id == grade.id:
                continue
            ahead_words = _word_set(str(ahead.get("memory") or ""))
            to_old = _overlap(ahead_words, old_words)
            to_new = _overlap(ahead_words, current_words)
            if to_old >= SUPERSEDE_OVERLAP and to_old > to_new:
                twins.append(ahead_id)

        if twins:
            parts["stale_ahead"] += 1
            probe_results[(grade.domain, grade.id)] = False
            failures.append(
                {
                    "id": grade.id,
                    "domain": grade.domain,
                    "failure": "stale_ahead",
                    "detail": (
                        f"outranked by {len(twins)} live entry(ies) that still say "
                        f"the superseded thing: {', '.join(sorted(set(twins)))}"
                    ),
                },
            )
        else:
            parts["clean"] += 1
            probe_results[(grade.domain, grade.id)] = True

    score = (
        round(parts["clean"] / parts["probes_run"], 4)
        if parts["probes_run"] else None
    )

    if probes_total == 0:
        verdict = (
            "No entry in this scope has supersede history, so there is nothing to "
            "probe: retrieval sanity is unmeasurable here, not passing."
        )
    elif parts["clean"] == parts["probes_run"]:
        verdict = (
            f"All {parts['probes_run']} rewritten fact(s) answer a query for what "
            f"they used to say with their current text."
        )
    else:
        verdict = (
            f"{parts['clean']} of {parts['probes_run']} rewritten fact(s) survive "
            f"a query for what they used to say: {parts['unreachable']} are no "
            f"longer retrievable from their own history, {parts['stale_ahead']} "
            f"are outranked by a live entry that still says the superseded thing."
        )
    if probes_total > parts["probes_run"]:
        verdict += (
            f" Sampled {parts['probes_run']} of {probes_total} candidates "
            f"(max_probes)."
        )

    metric = Metric(
        name="retrieval",
        score=score,
        verdict=verdict,
        parts=parts,
        worst=failures[:10],
        caveats=[
            "The vault's load() reads live domain files only and never opens "
            "_attic/, so an atticked text can only ever come back under some "
            "*other* live entry's id - which is the stale_ahead count. It cannot "
            "come back under the superseded entry's own id, and that half of the "
            "check is a regression guard rather than a discriminator.",
        ],
    )
    return metric, probe_results

# ---------------------------------------------------------------------------
# Per-domain grades
# ---------------------------------------------------------------------------


def _domain_grades(
    scan: _Scan,
    graded: list[EntryGrade],
    ttl_days: int,
    probe_results: dict[tuple[str, str], bool],
) -> list[DomainGrade]:
    retrieval_by_domain: dict[str, list[bool]] = {}
    for (domain, _entry_id), clean in probe_results.items():
        retrieval_by_domain.setdefault(domain, []).append(clean)

    out: list[DomainGrade] = []
    for domain in scan.domains:
        entries = [g for g in graded if g.domain == domain]
        tombs = [t for t in scan.tombstones if t.get("domain") == domain]
        attic = [a for a in scan.attic if a.get("domain") == domain]
        pending = sum(1 for g in entries if g.expired and not g.vetoed)

        s_parts = _staleness_parts(entries, ttl_days)
        f_parts = _forgetting_parts(len(entries), tombs, attic, pending)
        c_parts = _contradiction_parts(entries, {domain: scan.fill.get(domain, 0.0)}, 1)

        probes = retrieval_by_domain.get(domain, [])
        out.append(
            DomainGrade(
                domain=domain,
                live_entries=len(entries),
                fill=round(scan.fill.get(domain, 0.0), 4),
                near_cap=scan.fill.get(domain, 0.0) >= NEAR_CAP,
                staleness=_staleness_score(s_parts, ttl_days),
                forgetting=_forgetting_score(f_parts),
                contradiction=_contradiction_score(c_parts),
                retrieval=(
                    round(sum(1 for ok in probes if ok) / len(probes), 4)
                    if probes else None
                ),
                parts={
                    "staleness": s_parts,
                    "forgetting": f_parts,
                    "contradiction": {
                        k: c_parts[k]
                        for k in (
                            "contested", "churn_share", "supersedes_total",
                            "confirmations_total", "confirm_supersede_ratio",
                        )
                    },
                    "retrieval": {"probes": len(probes), "clean": sum(1 for ok in probes if ok)},
                },
            ),
        )
    return out


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def _resolve_ttl_days(explicit: int | None) -> int:
    if explicit is not None:
        return int(explicit)
    try:
        from sandcastle.config import settings

        return int(settings.memory_max_age_days)
    except Exception:  # pragma: no cover - settings should always import
        return 90


def _combine(metrics: list[Metric]) -> tuple[float | None, dict[str, float | None]]:
    components = {m.name: m.score for m in metrics}
    scored = [s for s in components.values() if s is not None]
    overall = round(sum(scored) / len(scored), 4) if scored else None
    return overall, components


async def evaluate_scope(
    scope_id: str,
    *,
    backend: Any | None = None,
    backend_name: str = "",
    ttl_days: int | None = None,
    probe_limit: int = DEFAULT_PROBE_LIMIT,
    max_probes: int = DEFAULT_MAX_PROBES,
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> MemoryEvalReport:
    """Grade one memory scope.

    On the filesystem vault this computes all four metrics. On any other
    backend it computes what the ``MemoryBackend`` Protocol actually exposes
    and fills :attr:`MemoryEvalReport.not_measurable` with the rest, named -
    the module never bypasses the Protocol to fake a metric.
    """
    from sandcastle.engine.memory import _get_backend, _validate_scope

    _validate_scope(scope_id)
    if backend is None:
        backend = _get_backend(backend_name)

    ttl = _resolve_ttl_days(ttl_days)
    if isinstance(backend, FilesystemMemoryBackend) or getattr(backend, "_vault", None):
        return await _evaluate_vault(
            backend, scope_id, ttl,
            probe_limit=probe_limit, max_probes=max_probes,
        )
    return await _evaluate_limited(backend, scope_id, ttl, max_entries=max_entries)


async def _evaluate_vault(
    backend: FilesystemMemoryBackend,
    scope_id: str,
    ttl_days: int,
    *,
    probe_limit: int,
    max_probes: int,
) -> MemoryEvalReport:
    import asyncio

    scan = await asyncio.to_thread(_scan_vault, backend, scope_id)
    depths = _supersede_depths(scan.attic)
    now = datetime.now(timezone.utc)
    graded = _grade_entries(scan.entries, depths, ttl_days, now)
    entries_by_id = {str(e.get("id") or ""): e for e in scan.entries}
    pending = sum(1 for g in graded if g.expired and not g.vetoed)

    staleness = _staleness_metric(graded, ttl_days)
    forgetting = _forgetting_metric(len(graded), scan.tombstones, scan.attic, pending)
    contradiction = _contradiction_metric(graded, scan.fill, len(scan.domains))
    retrieval, probe_results = await _retrieval_metric(
        backend, scope_id, graded, entries_by_id, scan.attic,
        probe_limit=probe_limit, max_probes=max_probes,
    )

    overall, components = _combine([staleness, forgetting, contradiction, retrieval])
    report = MemoryEvalReport(
        scope_id=scope_id,
        backend=getattr(backend, "name", "filesystem"),
        tenant=scan.tenant,
        scope_path=str(scan.scope_dir),
        generated_at=now.isoformat(),
        ttl_days=ttl_days,
        totals={
            "live_entries": len(graded),
            "domains": len(scan.domains),
            "tombstones": len(scan.tombstones),
            "attic_records": len(scan.attic),
            "superseded": sum(depths.values()),
            "confirmations": sum(g.confirmations for g in graded),
        },
        staleness=staleness,
        forgetting=forgetting,
        contradiction=contradiction,
        retrieval=retrieval,
        domains=_domain_grades(scan, graded, ttl_days, probe_results),
        entries=graded,
        overall=overall,
        components=components,
        caveats=[
            "Scoring is read-only and takes no scope lock: a scope written "
            "during the scan can mix revisions between files (never within "
            "one). Score a quiesced scope when exactness matters.",
        ],
    )
    if not scan.scope_dir.is_dir():
        report.caveats.insert(
            0,
            f"No directory exists for this scope at {scan.scope_dir} - it has "
            f"never been written to on this vault.",
        )
    return report


#: What cannot be answered through a non-vault backend, and why.
#:
#: Written against the installed mem0 (2.0.18) rather than from memory. The
#: distinction that matters: for two of these, mem0 *holds* the data and the
#: Sandcastle adapter simply does not surface it. `Memory.history(memory_id)`
#: returns a memory's ADD/UPDATE/DELETE history, and `add()`/`update()` take an
#: `expiration_date` that `get_all(show_expired=...)` can filter on. Neither
#: reaches `_Mem0Backend`, and this module will not bypass the Protocol to get
#: at them - a metric that needs a hole punched through the backend seam is not
#: a property of the seam. Closing that gap is a follow-up on the adapter, and
#: the wording below says so rather than blaming mem0 for a gap that is ours.
_MEM0_NOT_MEASURABLE: list[dict[str, str]] = [
    {
        "metric": "staleness (immortal vs expired-pending split)",
        "reason": (
            "no confirmations counter anywhere in the stack. The vault's TTL "
            "veto (memory_fs.py:1396) has no analogue, and mem0 metadata "
            "carries only what save_memory put there (memory.py:936). mem0 2.x "
            "does have its own expiration_date field, which Sandcastle never "
            "sets and the adapter never reads."
        ),
    },
    {
        "metric": "forgetting health",
        "reason": (
            "no tombstones, no Forgotten ledger, no attic. A mem0 delete leaves "
            "nothing behind that this seam can see, so a store that deleted "
            "half its contents is indistinguishable from one that never wrote "
            "them."
        ),
    },
    {
        "metric": "contradiction pressure",
        "reason": (
            "no supersede chain through the MemoryBackend Protocol. mem0 2.0.18 "
            "does expose Memory.history(memory_id), but _Mem0Backend does not "
            "surface it (its save() returns only the last result, "
            "memory.py:731) - so the chain exists and is unreachable from here."
        ),
    },
    {
        "metric": "domain and cap pressure",
        "reason": (
            "mem0 has no domain files and no per-file or per-scope cap, so "
            "there is nothing to be near the cap of. This one is genuinely "
            "absent rather than merely unexposed."
        ),
    },
    {
        "metric": "retrieval sanity",
        "reason": (
            "the probe set is 'entries known to have been superseded'. Without "
            "supersede history through the Protocol there is no probe set - a "
            "query would still run, but nothing would make its answer a check."
        ),
    },
]


async def _evaluate_limited(
    backend: Any,
    scope_id: str,
    ttl_days: int,
    *,
    max_entries: int,
) -> MemoryEvalReport:
    """The honest partial report for a backend without vault metadata.

    One metric survives - age against the TTL - and it means something weaker
    than it does on the vault, which the verdict says rather than reusing the
    vault's wording.
    """
    now = datetime.now(timezone.utc)
    name = getattr(backend, "name", "unknown")
    try:
        raw = await backend.load(scope_id, "", max_entries)
    except Exception as exc:
        logger.warning("memory eval could not read %s on %s: %s", scope_id, name, exc)
        return MemoryEvalReport(
            scope_id=scope_id,
            backend=name,
            generated_at=now.isoformat(),
            ttl_days=ttl_days,
            totals={"live_entries": 0},
            overall=None,
            not_measurable=[
                {"metric": "everything", "reason": f"backend read failed: {exc}"},
            ],
            caveats=[f"Could not enumerate scope {scope_id!r} on backend {name!r}."],
        )

    shaped = [
        {
            "id": str(item.get("id") or ""),
            "domain": "",
            "created": item.get("created_at") or "",
            "updated": item.get("updated_at") or item.get("created_at") or "",
            "confirmations": 0,
        }
        for item in (raw or [])
    ]
    graded = _grade_entries(shaped, {}, ttl_days, now)
    parts = _staleness_parts(graded, ttl_days)
    # The veto cannot fire without a counter, so the split is meaningless here:
    # report the single number this backend can support and nothing more.
    parts.pop("immortal", None)
    parts.pop("confirmations_veto", None)
    parts["expired"] = parts.pop("expired_pending", 0)
    live = parts["live_entries"]
    score = _staleness_score(parts, ttl_days)

    if ttl_days <= 0:
        verdict = (
            "TTL is disabled (MEMORY_MAX_AGE_DAYS=0): nothing is ever hidden and "
            "nothing is ever removed."
        )
    elif not live:
        verdict = f"Scope {scope_id!r} holds no entries on this backend."
    else:
        verdict = (
            f"{parts['expired']} of {live} entries are older than the "
            f"{ttl_days}-day TTL. On this backend that TTL is a read filter "
            f"(apply_decay, memory.py:485), not a forgetter: those entries stay "
            f"in storage and are merely hidden from a decay-aware read."
        )

    staleness = Metric(
        name="staleness",
        score=score,
        verdict=verdict,
        parts=parts,
        worst=[
            {
                "id": g.id,
                "age_days": g.age_days,
                "ttl_ratio": g.staleness_ratio,
            }
            for g in sorted(
                (g for g in graded if g.expired),
                key=lambda g: (-(g.staleness_ratio or 0.0), g.id),
            )[:10]
        ],
        caveats=[
            "'Expired' here means 'a decay-aware read would hide it', never "
            "'the store will remove it'.",
        ],
    )
    overall, components = _combine([staleness])
    return MemoryEvalReport(
        scope_id=scope_id,
        backend=name,
        generated_at=now.isoformat(),
        ttl_days=ttl_days,
        totals={"live_entries": live},
        staleness=staleness,
        overall=overall,
        components=components,
        not_measurable=list(_MEM0_NOT_MEASURABLE),
        caveats=[
            f"Entries were enumerated with load(scope, '', {max_entries}); a "
            f"scope larger than that is truncated.",
        ],
    )
