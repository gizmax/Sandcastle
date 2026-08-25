"""The filesystem memory backend: the markdown vault.

The vault sells one thing - a human can read what the agent remembered, when
it changed, and what it forgot. These tests hold that claim to account:

1. Path safety, assuming a hostile scope got past `_validate_scope` anyway.
2. Tenant isolation.
3. The write policy (confirm / supersede / append) and its thresholds.
4. The hard caps: 1,500-token INDEX, 120 domains, 32 KB per domain file.
5. Forgetting: TTL with a confirmations veto, domain merge, attic purge.
6. Concurrency, **multiprocess** - a threaded version of this test can be
   GIL-serialised into a false pass, so it uses real processes.
7. Git: a commit per run per scope, hooks neutralised, graceful without git.
8. Conformance to the MemoryBackend Protocol and dispatch through _get_backend.
"""

from __future__ import annotations

import inspect
import json
import multiprocessing as mp
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from sandcastle.engine.compaction import estimate_tokens
from sandcastle.engine.memory import (
    MemoryBackend,
    MemoryBackendError,
    _get_backend,
    _reset_client,
    _validate_scope,
)
from sandcastle.engine.memory_fs import (
    ATTIC_MAX_AGE_DAYS,
    CONFIRMATIONS_VETO,
    INDEX_TOKEN_BUDGET,
    MAX_DOMAIN_FILE_BYTES,
    MAX_DOMAINS_PER_SCOPE,
    FilesystemMemoryBackend,
    MemoryVaultFull,
    _RevisionConflict,
    _slug,
    decide_write,
    parse_domain,
    render_domain,
    render_index,
    route_domain,
    split_scope,
)


@pytest.fixture
def vault(tmp_path):
    """A git-less vault - most tests are about the markdown, not the history."""
    backend = FilesystemMemoryBackend(tmp_path / "vault", use_git=False)
    yield backend


@pytest.fixture
def git_vault(tmp_path):
    backend = FilesystemMemoryBackend(tmp_path / "gitvault", use_git=True)
    yield backend


def _scope_dir(backend: FilesystemMemoryBackend, scope_id: str) -> Path:
    return backend._vault.scope_dir(scope_id)


def _domain_files(backend: FilesystemMemoryBackend, scope_id: str) -> list[Path]:
    d = _scope_dir(backend, scope_id)
    return sorted(p for p in d.glob("*.md") if p.name != "INDEX.md")


def _all_entries(backend: FilesystemMemoryBackend, scope_id: str) -> list[dict]:
    out: list[dict] = []
    for path in _domain_files(backend, scope_id):
        _, entries, _ = parse_domain(path.read_text(encoding="utf-8"))
        out.extend(entries)
    return out


# ---------------------------------------------------------------------------
# 1. Path safety
# ---------------------------------------------------------------------------


class TestPathSafety:
    """0.45 closed the regex. This asserts the backend does not depend on it."""

    @pytest.mark.parametrize(
        "hostile",
        [
            "workflow:..",
            "workflow:../../etc",
            "agent:..",
            "tenant:../workflow:x",
            "tenant:../../root/workflow:x",
            "workflow:/etc/passwd",
            "workflow:/absolute",
            "tenant:/abs/workflow:x",
            "workflow:%2e%2e%2f%2e%2e",
            "workflow:..%2F..%2Fetc",
            "workflow:....//....//etc",
            "workflow:\\..\\..\\windows",
            "workflow:.",
            "workflow:...",
            "global/../../..",
        ],
    )
    def test_hostile_scope_stays_inside_the_vault(self, vault, hostile):
        """Bypass _validate_scope entirely: assume the caller is hostile."""
        resolved = _scope_dir(vault, hostile)
        assert resolved.resolve().is_relative_to(vault.root.resolve()), resolved
        # Exactly two components below the root: <tenant>/<scope>.
        rel = resolved.resolve().relative_to(vault.root.resolve())
        assert len(rel.parts) == 2, rel
        assert ".." not in rel.parts

    def test_hostile_tenant_id_cannot_escape(self, vault):
        for tenant in ["..", "../..", "/etc", "....", "%2e%2e", ".git"]:
            resolved = _scope_dir(vault, f"tenant:{tenant}/global")
            assert resolved.resolve().is_relative_to(vault.root.resolve())
            rel = resolved.resolve().relative_to(vault.root.resolve())
            assert rel.parts[0] not in ("..", ".git", ""), rel

    def test_hostile_domain_name_cannot_escape(self, vault):
        scope = _scope_dir(vault, "workflow:demo")
        for name in ["../../escape", "..", "/etc/passwd", ".git"]:
            path = vault._vault.domain_path(scope, name)
            assert path.resolve().is_relative_to(scope.resolve()), path

    @pytest.mark.asyncio
    async def test_hostile_scope_write_lands_in_the_vault(self, vault):
        await vault.save("workflow:..", "a hostile scope wrote this note down", {}, "r1")
        written = list(vault.root.rglob("*.md"))
        assert written
        for path in written:
            assert path.resolve().is_relative_to(vault.root.resolve())
        # And nothing leaked to the vault's parent.
        siblings = {p.name for p in vault.root.parent.iterdir()}
        assert siblings == {vault.root.name}

    def test_slug_never_returns_a_traversal_component(self):
        for raw in ["..", ".", "...", "../..", "/", "//", ".git", "_locks", "_attic", ""]:
            out = _slug(raw)
            assert out not in ("", ".", "..", "/", ".git", "_locks", "_attic")
            assert "/" not in out and "\\" not in out
            assert ".." not in out

    def test_the_regex_already_rejects_these(self):
        """The backend's defence is second; this is the first line still holding."""
        for hostile in ["workflow:..", "agent:..", "tenant:../workflow:x", "workflow:x\n"]:
            with pytest.raises(ValueError):
                _validate_scope(hostile)


# ---------------------------------------------------------------------------
# 2. Tenant isolation
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    @pytest.mark.asyncio
    async def test_tenants_do_not_see_each_other(self, vault):
        await vault.save(
            "tenant:acme/workflow:deploy",
            "Acme rotates its signing key every ninety days without fail.",
            {}, "r1",
        )
        await vault.save(
            "tenant:globex/workflow:deploy",
            "Globex keeps a permanent signing key and audits it quarterly.",
            {}, "r1",
        )
        acme = await vault.load("tenant:acme/workflow:deploy", "", 50)
        globex = await vault.load("tenant:globex/workflow:deploy", "", 50)
        assert len(acme) == 1 and len(globex) == 1
        assert "Acme" in acme[0]["memory"]
        assert "Globex" in globex[0]["memory"]

    @pytest.mark.asyncio
    async def test_tenant_scope_is_a_separate_directory(self, vault):
        await vault.save("tenant:acme/global", "Acme prefers invoices in euros.", {}, "r1")
        await vault.save("global", "The shared default currency is dollars.", {}, "r1")
        assert (vault.root / "acme" / "global").is_dir()
        assert (vault.root / "_shared" / "global").is_dir()
        shared = await vault.load("global", "", 50)
        assert len(shared) == 1
        assert "dollars" in shared[0]["memory"]

    @pytest.mark.asyncio
    async def test_delete_all_stops_at_the_tenant_boundary(self, vault):
        await vault.save("tenant:acme/global", "Acme prefers invoices in euros.", {}, "r1")
        await vault.save("tenant:globex/global", "Globex settles in pounds sterling.", {}, "r1")
        await vault.delete_all("tenant:acme/global")
        assert await vault.load("tenant:acme/global", "", 50) == []
        assert len(await vault.load("tenant:globex/global", "", 50)) == 1


# ---------------------------------------------------------------------------
# 3. Write policy
# ---------------------------------------------------------------------------


class TestWritePolicy:
    def test_thresholds_are_the_designed_ones(self):
        base = "the deploy pipeline pushes images to the internal registry nightly"
        # Identical text - overlap 1.0.
        entries = [{"id": "a", "text": base}]
        action, target, ratio = decide_write(base, entries)
        assert action == "confirm" and ratio >= 0.85 and target["id"] == "a"

        # Partly rewritten - between the two thresholds.
        changed = "the deploy pipeline pushes images to an external registry weekly"
        action, target, ratio = decide_write(changed, entries)
        assert action == "supersede", ratio
        assert 0.40 <= ratio < 0.85, ratio

        # Unrelated - below the floor.
        other = "quarterly headcount planning happens in the finance spreadsheet"
        action, target, ratio = decide_write(other, entries)
        assert action == "append" and target is None

    @pytest.mark.asyncio
    async def test_confirm_bumps_the_counter_and_adds_no_entry(self, vault):
        text = "The nightly export writes a parquet file to the reporting bucket."
        for _ in range(3):
            await vault.save("workflow:demo", text, {}, "r1")
        entries = _all_entries(vault, "workflow:demo")
        assert len(entries) == 1
        assert entries[0]["confirmations"] == 2
        # And it is legible: the count is in the heading a human reads.
        body = _domain_files(vault, "workflow:demo")[0].read_text()
        assert "confirmed 2x" in body

    @pytest.mark.asyncio
    async def test_supersede_keeps_the_id_and_files_the_old_text(self, vault):
        old = "The deploy pipeline pushes images to the internal registry nightly."
        new = "The deploy pipeline pushes images to an external registry weekly."
        first = await vault.save("workflow:demo", old, {}, "r1")
        second = await vault.save("workflow:demo", new, {}, "r1")
        assert second[0]["event"] == "supersede"
        assert second[0]["id"] == first[0]["id"]

        entries = _all_entries(vault, "workflow:demo")
        assert len(entries) == 1
        assert "external registry" in entries[0]["text"]

        attic = list((_scope_dir(vault, "workflow:demo") / "_attic").glob("*.md"))
        assert attic, "superseded text must survive in the attic"
        assert "internal registry" in attic[0].read_text()

    @pytest.mark.asyncio
    async def test_append_when_unrelated(self, vault):
        await vault.save("workflow:demo", "Invoices from Acme arrive on the fifteenth.", {}, "r1")
        await vault.save("workflow:demo", "Kubernetes nodes drain before a rolling upgrade.", {}, "r1")
        assert len(_all_entries(vault, "workflow:demo")) == 2

    def test_routing_is_deterministic(self):
        rows = [
            {"domain": "deploy", "keywords": ["deploy", "registry", "docker"]},
            {"domain": "billing", "keywords": ["invoice", "acme", "ledger"]},
        ]
        for _ in range(20):
            assert route_domain(["deploy", "docker", "cache"], "x", rows) == ("deploy", False)
            assert route_domain(["invoice", "ledger"], "x", rows) == ("billing", False)
        # An unrelated entry opens its own file rather than being forced into one.
        name, is_new = route_domain(["kubernetes", "drain"], "x", rows)
        assert is_new and name == "kubernetes"

    def test_routing_breaks_ties_on_name(self):
        rows = [
            {"domain": "zeta", "keywords": ["shared", "word"]},
            {"domain": "alpha", "keywords": ["shared", "word"]},
        ]
        # Equal scores: the first in name order wins, every time.
        assert {route_domain(["shared", "word"], "x", rows)[0] for _ in range(20)} == {"alpha"}


# ---------------------------------------------------------------------------
# 4. Hard caps
# ---------------------------------------------------------------------------


class TestCaps:
    def test_index_stays_inside_the_token_budget(self):
        rows = [
            {
                "domain": f"domain-with-a-fairly-long-name-{i:03d}",
                "entries": i,
                "updated": f"2026-08-{(i % 28) + 1:02d}T00:00:00+00:00",
                "keywords": [f"keyword{i}{j}" for j in range(6)],
            }
            for i in range(MAX_DOMAINS_PER_SCOPE)
        ]
        text = render_index("workflow:demo", rows)
        assert estimate_tokens(text) <= INDEX_TOKEN_BUDGET
        # It dropped rows, and it says so rather than pretending to be complete.
        listed = text.count("](")
        assert listed < len(rows)
        assert "did not fit" in text
        assert f"{len(rows) - listed} more domain file(s)" in text

    def test_index_without_overflow_has_no_overflow_line(self):
        rows = [{"domain": "a", "entries": 1, "updated": "2026-08-01", "keywords": ["x"]}]
        text = render_index("workflow:demo", rows)
        assert "did not fit" not in text
        assert estimate_tokens(text) <= INDEX_TOKEN_BUDGET

    @pytest.mark.asyncio
    async def test_index_is_regenerated_on_every_write(self, vault):
        await vault.save("workflow:demo", "Invoices from Acme arrive on the fifteenth.", {}, "r1")
        index = (_scope_dir(vault, "workflow:demo") / "INDEX.md").read_text()
        assert "invoices" in index
        await vault.save("workflow:demo", "Kubernetes nodes drain before a rolling upgrade.", {}, "r1")
        index = (_scope_dir(vault, "workflow:demo") / "INDEX.md").read_text()
        assert "invoices" in index and "kubernetes" in index
        assert estimate_tokens(index) <= INDEX_TOKEN_BUDGET

    @pytest.mark.asyncio
    async def test_domain_cap_is_an_error_not_a_warning(self, vault):
        scope = "workflow:demo"
        for i in range(MAX_DOMAINS_PER_SCOPE):
            await vault.save(scope, f"zzq{i:04d}aa zzq{i:04d}bb zzq{i:04d}cc zzq{i:04d}dd", {}, "r1")
        assert len(_domain_files(vault, scope)) == MAX_DOMAINS_PER_SCOPE
        with pytest.raises(MemoryVaultFull) as exc:
            await vault.save(scope, "yyw0000aa yyw0000bb yyw0000cc yyw0000dd", {}, "r1")
        assert "price of" in str(exc.value)
        # It is a MemoryBackendError, so engine.memory's error contract holds.
        assert isinstance(exc.value, MemoryBackendError)

    @pytest.mark.asyncio
    async def test_domain_file_size_cap_evicts_oldest_with_a_tombstone(self, vault):
        scope = "workflow:demo"
        # "sharedtopic logbook" is the routing prefix (2 of 5 keywords, above
        # ROUTE_MIN_SCORE) so every entry lands in one file. The rest of each
        # entry is its own vocabulary, so the overlap coefficient stays far
        # below 0.40 and every write appends instead of superseding.
        for i in range(30):
            body = " ".join(f"e{i:03d}w{k:03d}" for k in range(180))
            await vault.save(scope, f"sharedtopic logbook {body}", {}, "r1")
        assert len(_domain_files(vault, scope)) == 1
        files = _domain_files(vault, scope)
        for path in files:
            assert len(path.read_bytes()) <= MAX_DOMAIN_FILE_BYTES, path
        body = files[0].read_text()
        assert "size-cap" in body, "eviction must leave a visible tombstone"

    @pytest.mark.asyncio
    async def test_single_oversized_entry_is_refused(self, vault):
        with pytest.raises(MemoryVaultFull):
            await vault.save("workflow:demo", "x" * (MAX_DOMAIN_FILE_BYTES + 1000), {}, "r1")


# ---------------------------------------------------------------------------
# 5. Forgetting
# ---------------------------------------------------------------------------


def _age_entries(path: Path, days: int) -> None:
    """Rewrite every entry's timestamps *days* into the past."""
    text = path.read_text(encoding="utf-8")
    old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    def sub(match: re.Match) -> str:
        meta = json.loads(match.group(1))
        meta["created"] = old
        meta["updated"] = old
        return "<!-- meta: " + json.dumps(meta, sort_keys=True, separators=(",", ":")) + " -->"

    path.write_text(
        re.sub(r"<!--\s*meta:\s*(\{.*?\})\s*-->", sub, text, flags=re.DOTALL),
        encoding="utf-8",
    )


class TestForgetting:
    @pytest.mark.asyncio
    async def test_ttl_expires_and_leaves_a_tombstone(self, vault):
        scope = "workflow:demo"
        await vault.save(scope, "A one-off note nobody ever repeated again.", {}, "r1")
        path = _domain_files(vault, scope)[0]
        _age_entries(path, 400)
        result = await vault.consolidate(scope, max_age_days=90)
        assert result["expired"] == 1
        assert _all_entries(vault, scope) == []
        assert "ttl" in path.read_text()
        attic = list((_scope_dir(vault, scope) / "_attic").glob("*.md"))
        assert attic and "one-off note" in attic[0].read_text()

    @pytest.mark.asyncio
    async def test_confirmations_veto_the_ttl(self, vault):
        scope = "workflow:demo"
        text = "The release train leaves every second Thursday without exception."
        for _ in range(CONFIRMATIONS_VETO + 1):
            await vault.save(scope, text, {}, "r1")
        path = _domain_files(vault, scope)[0]
        _age_entries(path, 400)
        result = await vault.consolidate(scope, max_age_days=90)
        assert result["expired"] == 0
        entries = _all_entries(vault, scope)
        assert len(entries) == 1
        assert entries[0]["confirmations"] >= CONFIRMATIONS_VETO

    @pytest.mark.asyncio
    async def test_ttl_zero_keeps_everything(self, vault):
        scope = "workflow:demo"
        await vault.save(scope, "A one-off note nobody ever repeated again.", {}, "r1")
        _age_entries(_domain_files(vault, scope)[0], 4000)
        assert (await vault.consolidate(scope, max_age_days=0))["expired"] == 0
        assert len(_all_entries(vault, scope)) == 1

    @pytest.mark.asyncio
    async def test_domain_merge_leaves_a_tombstone(self, vault):
        scope = "workflow:demo"
        scope_dir = _scope_dir(vault, scope)
        scope_dir.mkdir(parents=True, exist_ok=True)
        shared = ["alpha", "beta", "gamma", "delta"]
        for name in ("aaa", "bbb"):
            (scope_dir / f"{name}.md").write_text(
                render_domain(
                    domain=name,
                    scope_id=scope,
                    revision=1,
                    entries=[
                        {
                            "id": f"{name}-1",
                            "text": f"a note filed under {name}",
                            "created": datetime.now(timezone.utc).isoformat(),
                            "updated": datetime.now(timezone.utc).isoformat(),
                            "confirmations": 0,
                            "run_id": "r1",
                            "keywords": shared,
                            "tags": [],
                        },
                    ],
                    tombstones=[],
                ),
            )
        result = await vault.consolidate(scope, max_age_days=0)
        assert result["merged"] == 1
        remaining = {p.stem for p in _domain_files(vault, scope)}
        assert remaining == {"aaa"}
        assert "merged-into-aaa" in (scope_dir / "aaa.md").read_text()
        assert len(_all_entries(vault, scope)) == 2

    @pytest.mark.asyncio
    async def test_attic_purge_drops_only_stale_records(self, vault):
        scope = "workflow:demo"
        scope_dir = _scope_dir(vault, scope)
        old = (datetime.now(timezone.utc) - timedelta(days=ATTIC_MAX_AGE_DAYS + 5)).isoformat()
        fresh = datetime.now(timezone.utc).isoformat()
        vault._vault.append_attic(
            scope_dir / "_attic" / "notes.md",
            [
                {"id": "old1", "text": "ancient superseded text", "at": old, "reason": "superseded"},
                {"id": "new1", "text": "recent superseded text", "at": fresh, "reason": "superseded"},
            ],
        )
        result = await vault.consolidate(scope, max_age_days=0)
        assert result["attic_purged"] == 1
        body = (scope_dir / "_attic" / "notes.md").read_text()
        assert "ancient superseded" not in body
        assert "recent superseded" in body

    @pytest.mark.asyncio
    async def test_consolidation_runs_once_per_run(self, vault):
        scope = "workflow:demo"
        calls: list[str] = []
        real = vault._consolidate_sync

        def counting(scope_id, max_age):
            calls.append(scope_id)
            return real(scope_id, max_age)

        with patch.object(vault, "_consolidate_sync", counting):
            for i in range(4):
                await vault.save(scope, f"note number {i} about wholly separate matters {i}", {}, "run-a")
            assert calls == []          # nothing during the run
            await vault.end_run("run-a")
            assert calls == [scope]     # exactly one pass, at the end


# ---------------------------------------------------------------------------
# 6. Concurrency - MULTIPROCESS
# ---------------------------------------------------------------------------


def _writer_process(root: str, worker: int, count: int) -> int:
    """Run in a *separate process*: no GIL to accidentally serialise us."""
    import asyncio

    from sandcastle.engine.memory_fs import FilesystemMemoryBackend

    backend = FilesystemMemoryBackend(root, use_git=False)

    async def go() -> int:
        written = 0
        for i in range(count):
            # "sharedtopic logbook" is the shared routing prefix, so every
            # writer contends on ONE domain file. The rest is per-writer
            # vocabulary, so overlap stays below 0.40 and each write appends.
            unique = " ".join(f"p{worker}i{i}t{k}" for k in range(7))
            await backend.save(
                "workflow:race", f"sharedtopic logbook {unique}", {}, "run-x",
            )
            written += 1
        return written

    return asyncio.run(go())


@pytest.mark.skipif(
    sys.platform.startswith("win"), reason="flock is POSIX-only",
)
class TestConcurrencyMultiprocess:
    """Real processes, not threads.

    A threaded version of this test can pass for the wrong reason: the GIL
    serialises the read-modify-write window and the lock is never exercised.
    Separate processes have no shared interpreter, so the only thing keeping
    two writers from clobbering each other is the flock plus the revision CAS.
    """

    def test_parallel_processes_lose_no_writes(self, tmp_path):
        root = str(tmp_path / "race")
        workers, per_worker = 5, 8

        ctx = mp.get_context("spawn")
        env_src = str(Path(__file__).resolve().parents[1] / "src")
        os.environ["PYTHONPATH"] = env_src + os.pathsep + os.environ.get("PYTHONPATH", "")

        with ctx.Pool(workers) as pool:
            results = pool.starmap(
                _writer_process,
                [(root, w, per_worker) for w in range(workers)],
            )
        assert results == [per_worker] * workers

        backend = FilesystemMemoryBackend(root, use_git=False)
        assert len(_domain_files(backend, "workflow:race")) == 1, (
            "the fixture must put every writer on one file, or nothing contends"
        )
        entries = _all_entries(backend, "workflow:race")
        assert len(entries) == workers * per_worker, (
            f"expected {workers * per_worker} entries, found {len(entries)} - "
            "a write was lost to a race"
        )
        assert len({e["id"] for e in entries}) == len(entries)
        # Every writer's work survived, not just the last one home.
        texts = " ".join(e["text"] for e in entries)
        for w in range(workers):
            for i in range(per_worker):
                assert f"p{w}i{i}t0" in texts

    def test_index_survives_the_race(self, tmp_path):
        root = str(tmp_path / "race2")
        ctx = mp.get_context("spawn")
        env_src = str(Path(__file__).resolve().parents[1] / "src")
        os.environ["PYTHONPATH"] = env_src + os.pathsep + os.environ.get("PYTHONPATH", "")
        with ctx.Pool(4) as pool:
            pool.starmap(_writer_process, [(root, w, 4) for w in range(4)])

        backend = FilesystemMemoryBackend(root, use_git=False)
        index = _scope_dir(backend, "workflow:race") / "INDEX.md"
        assert index.exists()
        text = index.read_text()
        assert estimate_tokens(text) <= INDEX_TOKEN_BUDGET
        # Not truncated mid-write: the frontmatter is still parseable YAML.
        assert text.startswith("---")
        rows = backend._vault.read_index(_scope_dir(backend, "workflow:race"))
        assert rows


class TestRevisionCAS:
    """The CAS is what survives a lost lock. Test it without the lock."""

    def test_stale_revision_is_refused(self, vault):
        scope = "workflow:demo"
        scope_dir = _scope_dir(vault, scope)
        scope_dir.mkdir(parents=True, exist_ok=True)
        path = vault._vault.domain_path(scope_dir, "notes")
        entry = {
            "id": "aaa", "text": "first", "created": "2026-01-01T00:00:00+00:00",
            "updated": "2026-01-01T00:00:00+00:00", "confirmations": 0,
            "run_id": "r", "keywords": ["notes"], "tags": [],
        }
        rev = vault._vault.write_domain(
            path, domain="notes", scope_id=scope, entries=[entry],
            tombstones=[], expected_revision=0,
        )
        assert rev == 1
        # Somebody else writes; our revision is now stale.
        vault._vault.write_domain(
            path, domain="notes", scope_id=scope, entries=[entry],
            tombstones=[], expected_revision=1,
        )
        with pytest.raises(_RevisionConflict):
            vault._vault.write_domain(
                path, domain="notes", scope_id=scope, entries=[entry],
                tombstones=[], expected_revision=1,
            )

    @pytest.mark.asyncio
    async def test_save_merges_rather_than_failing_on_conflict(self, vault):
        """A concurrent writer's entry must survive our retry, not be overwritten."""
        scope = "workflow:demo"
        await vault.save(scope, "sharedtopic aaa bbb ccc ddd eee", {}, "r1")
        path = _domain_files(vault, scope)[0]
        original = vault._vault.write_domain
        state = {"fired": False}

        def sabotage(p, **kwargs):
            if not state["fired"] and p == path:
                state["fired"] = True
                # An intruder lands an entry between our read and our write.
                fm, entries, tombs = vault._vault.read_domain(p)
                entries.append(
                    {
                        "id": "intruder", "text": "sharedtopic zzz yyy xxx www vvv",
                        "created": "2026-01-01T00:00:00+00:00",
                        "updated": "2026-01-01T00:00:00+00:00",
                        "confirmations": 0, "run_id": "other",
                        "keywords": ["sharedtopic"], "tags": [],
                    },
                )
                original(
                    p, domain=kwargs["domain"], scope_id=kwargs["scope_id"],
                    entries=entries, tombstones=tombs,
                    expected_revision=int(fm.get("revision") or 0),
                )
            return original(p, **kwargs)

        with patch.object(vault._vault, "write_domain", sabotage):
            await vault.save(scope, "sharedtopic fff ggg hhh iii jjj", {}, "r1")

        texts = " ".join(e["text"] for e in _all_entries(vault, scope))
        assert "zzz" in texts, "the concurrent writer's entry was clobbered"
        assert "fff" in texts, "our own entry was lost"
        assert "aaa" in texts


# ---------------------------------------------------------------------------
# 7. Git
# ---------------------------------------------------------------------------


def _git(root: Path, *args: str) -> str:
    env = dict(os.environ)
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, env=env, check=False,
    ).stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
class TestGit:
    @pytest.mark.asyncio
    async def test_one_commit_per_run_per_scope(self, git_vault):
        scope = "workflow:demo"
        for i in range(4):
            await git_vault.save(scope, f"note {i} covering entirely separate ground {i}", {}, "run-a")
        assert _git(git_vault.root, "log", "--oneline").strip() == "", "no commit mid-run"
        await git_vault.end_run("run-a")
        log = [ln for ln in _git(git_vault.root, "log", "--oneline").splitlines() if ln]
        assert len(log) == 1, log
        assert "run-a" in log[0]

        for i in range(2):
            await git_vault.save(scope, f"later note {i} on unrelated business {i}", {}, "run-b")
        await git_vault.end_run("run-b")
        log = [ln for ln in _git(git_vault.root, "log", "--oneline").splitlines() if ln]
        assert len(log) == 2, log
        assert "run-b" in log[0]

    @pytest.mark.asyncio
    async def test_the_next_run_closes_the_previous_one(self, git_vault):
        scope = "workflow:demo"
        await git_vault.save(scope, "first run wrote a note about invoicing rules", {}, "run-a")
        await git_vault.save(scope, "second run wrote a note about kubernetes drains", {}, "run-b")
        log = [ln for ln in _git(git_vault.root, "log", "--oneline").splitlines() if ln]
        assert len(log) == 1 and "run-a" in log[0]

    @pytest.mark.asyncio
    async def test_history_shows_what_changed(self, git_vault):
        scope = "workflow:demo"
        await git_vault.save(
            scope, "The deploy pipeline pushes images to the internal registry nightly.", {}, "run-a",
        )
        await git_vault.end_run("run-a")
        await git_vault.save(
            scope, "The deploy pipeline pushes images to an external registry weekly.", {}, "run-b",
        )
        await git_vault.end_run("run-b")
        diff = _git(git_vault.root, "diff", "HEAD~1", "HEAD")
        assert "-The deploy pipeline pushes images to the internal registry nightly." in diff
        assert "+The deploy pipeline pushes images to an external registry weekly." in diff

    @pytest.mark.asyncio
    async def test_hooks_are_neutralised(self, git_vault):
        scope = "workflow:demo"
        await git_vault.save(scope, "a first note so the repo exists at all", {}, "run-a")
        await git_vault.end_run("run-a")

        # Plant a hook that would fail the commit if it ever ran.
        hooks = git_vault.root / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        marker = git_vault.root.parent / "hook-ran"
        hook = hooks / "pre-commit"
        hook.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
        hook.chmod(0o755)
        # And point the repo config at them, as a hostile repo would.
        subprocess.run(
            ["git", "-C", str(git_vault.root), "config", "core.hooksPath", str(hooks)],
            check=True, capture_output=True,
        )

        await git_vault.save(scope, "a second note on a completely different subject", {}, "run-b")
        await git_vault.end_run("run-b")
        assert not marker.exists(), "the pre-commit hook ran"
        log = [ln for ln in _git(git_vault.root, "log", "--oneline").splitlines() if ln]
        assert len(log) == 2, "the commit was blocked by the hook"

    @pytest.mark.asyncio
    async def test_degrades_gracefully_without_git(self, tmp_path):
        backend = FilesystemMemoryBackend(tmp_path / "nogit", use_git=True)
        with patch("shutil.which", return_value=None):
            backend._vault.git.available = False
            backend._vault.git._initialised = False
            await backend.save("workflow:demo", "a note written with no git on the box", {}, "run-a")
            commits = await backend.end_run("run-a")
        assert commits == []
        assert not (backend.root / ".git").exists()
        assert len(await backend.load("workflow:demo", "", 10)) == 1

    @pytest.mark.asyncio
    async def test_commit_sha_is_pinned_into_the_audit_chain(self, git_vault):
        seen: list[dict] = []

        async def fake_pin(commits):
            seen.extend(commits)

        with patch("sandcastle.engine.memory_fs._pin_commits", fake_pin):
            await git_vault.save("workflow:demo", "a note worth pinning to a hash chain", {}, "run-a")
            await git_vault.end_run("run-a")

        assert len(seen) == 1
        record = seen[0]
        assert re.fullmatch(r"[0-9a-f]{40}", record["commit"])
        assert record["scope_id"] == "workflow:demo"
        assert record["run_id"] == "run-a"
        head = _git(git_vault.root, "rev-parse", "HEAD").strip()
        assert record["commit"] == head

    @pytest.mark.asyncio
    async def test_audit_pin_failure_never_breaks_a_save(self, git_vault):
        from sandcastle.engine import memory_fs

        def boom(*_a, **_k):
            raise RuntimeError("no database here")

        with patch("sandcastle.models.db.async_session", boom):
            await git_vault.save("workflow:demo", "a note written with no database at all", {}, "run-a")
            await git_vault.end_run("run-a")
        assert len(await git_vault.load("workflow:demo", "", 10)) == 1
        assert memory_fs is not None


# ---------------------------------------------------------------------------
# 8. Protocol conformance, dispatch, retrieval
# ---------------------------------------------------------------------------


class TestProtocolAndDispatch:
    def test_conforms_to_the_memory_backend_protocol(self):
        for name in ("load", "save", "delete", "delete_all", "health"):
            assert hasattr(FilesystemMemoryBackend, name)
            assert inspect.iscoroutinefunction(getattr(FilesystemMemoryBackend, name))
        proto_sig = inspect.signature(MemoryBackend.save)
        impl_sig = inspect.signature(FilesystemMemoryBackend.save)
        assert list(proto_sig.parameters) == list(impl_sig.parameters)

    def test_get_backend_resolves_filesystem(self, tmp_path):
        _reset_client()
        try:
            with patch("sandcastle.config.settings.memory_fs_root", str(tmp_path / "v")):
                backend = _get_backend("filesystem")
                assert isinstance(backend, FilesystemMemoryBackend)
                assert _get_backend("filesystem") is backend  # cached singleton
        finally:
            _reset_client()

    def test_settings_accepts_filesystem(self):
        from sandcastle.config import Settings

        assert Settings(memory_backend="filesystem").memory_backend == "filesystem"
        assert Settings(memory_backend="FILESYSTEM").memory_backend == "filesystem"
        assert Settings(memory_backend="nonsense").memory_backend == "local"

    def test_unknown_backend_message_lists_filesystem(self):
        _reset_client()
        try:
            from sandcastle.engine.memory import _get_client

            with pytest.raises(MemoryBackendError) as exc:
                _get_client("nonsense")
            assert "filesystem" in str(exc.value)
        finally:
            _reset_client()

    @pytest.mark.asyncio
    async def test_health_reports_ok_and_leaves_no_litter(self, vault):
        await vault.health()
        assert vault.root.is_dir()
        assert not (vault.root / "_locks" / "health.probe").exists()

    @pytest.mark.asyncio
    async def test_public_api_round_trip(self, tmp_path):
        """The whole engine.memory surface over the vault, no mem0 anywhere."""
        from sandcastle.engine import memory as mem

        _reset_client()
        try:
            with patch("sandcastle.config.settings.memory_fs_root", str(tmp_path / "v")), \
                 patch("sandcastle.config.settings.memory_backend", "filesystem"):
                await mem.save_memory(
                    "workflow:demo",
                    "The quarterly compliance export must be signed before upload.",
                    run_id="run-a", skip_admission=True,
                )
                loaded = await mem.load_memories("workflow:demo", "compliance export")
                assert len(loaded) == 1
                assert "compliance export" in loaded[0]["memory"]

                block = mem.format_memories_for_prompt(loaded)
                assert "[Agent Memory]" in block

                health = await mem.memory_health_check()
                assert health["status"] == "ok"
                assert health["backend"] == "filesystem"

                assert await mem.delete_memory(loaded[0]["id"], scope_id="workflow:demo")
                assert await mem.load_memories("workflow:demo") == []
        finally:
            _reset_client()

    @pytest.mark.asyncio
    async def test_delete_by_id_without_a_scope(self, vault):
        record = await vault.save("workflow:demo", "a note to be forgotten by id alone", {}, "r1")
        assert await vault.delete(record[0]["id"]) is True
        assert await vault.load("workflow:demo", "", 10) == []

    @pytest.mark.asyncio
    async def test_delete_of_an_unknown_id_is_false(self, vault):
        await vault.save("workflow:demo", "a note that stays exactly where it is", {}, "r1")
        assert await vault.delete("does-not-exist", scope_id="workflow:demo") is False
        assert len(await vault.load("workflow:demo", "", 10)) == 1


class TestRetrieval:
    @pytest.mark.asyncio
    async def test_query_ranks_by_keyword_overlap(self, vault):
        scope = "workflow:demo"
        await vault.save(scope, "Kubernetes nodes drain before a rolling upgrade.", {}, "r1")
        await vault.save(scope, "Invoices from Acme arrive on the fifteenth of the month.", {}, "r1")
        hits = await vault.load(scope, "invoice acme fifteenth", 5)
        assert hits and "Acme" in hits[0]["memory"]

    @pytest.mark.asyncio
    async def test_empty_query_returns_newest_first(self, vault):
        scope = "workflow:demo"
        await vault.save(scope, "The first note, about wholly separate ground.", {}, "r1")
        await vault.save(scope, "A later remark concerning different business entirely.", {}, "r1")
        hits = await vault.load(scope, "", 10)
        assert len(hits) == 2
        assert hits[0]["updated_at"] >= hits[1]["updated_at"]

    @pytest.mark.asyncio
    async def test_limit_is_respected(self, vault):
        scope = "workflow:demo"
        for i in range(6):
            await vault.save(scope, f"qqz{i}aa qqz{i}bb qqz{i}cc qqz{i}dd qqz{i}ee", {}, "r1")
        assert len(await vault.load(scope, "", 3)) == 3

    @pytest.mark.asyncio
    async def test_missing_scope_reads_empty(self, vault):
        assert await vault.load("workflow:never-written", "anything", 10) == []

    @pytest.mark.asyncio
    async def test_a_hand_edited_file_still_parses(self, vault):
        """The vault invites human edits. It must survive a sloppy one."""
        scope = "workflow:demo"
        await vault.save(scope, "A note somebody is about to edit by hand.", {}, "r1")
        path = _domain_files(vault, scope)[0]
        text = path.read_text()
        path.write_text(
            text.replace(
                "A note somebody is about to edit by hand.",
                "A note somebody edited by hand, adding a whole paragraph\n\nand a second one.",
            ),
        )
        hits = await vault.load(scope, "", 10)
        assert len(hits) == 1
        assert "second one" in hits[0]["memory"]

    @pytest.mark.asyncio
    async def test_a_corrupt_entry_loses_the_entry_not_the_file(self, vault):
        scope = "workflow:demo"
        await vault.save(scope, "Invoices from Acme arrive on the fifteenth.", {}, "r1")
        await vault.save(scope, "Kubernetes nodes drain before a rolling upgrade.", {}, "r1")
        path = _domain_files(vault, scope)[0]
        path.write_text(path.read_text().replace('"id":', '"id"', 1))
        # The mangled file yields nothing; the other file is untouched.
        remaining = await vault.load(scope, "", 10)
        assert len(remaining) == 1


class TestRendering:
    def test_domain_file_round_trips(self):
        entries = [
            {
                "id": "abc123", "text": "line one\n\nline two",
                "created": "2026-08-01T00:00:00+00:00",
                "updated": "2026-08-02T00:00:00+00:00",
                "confirmations": 3, "run_id": "r1",
                "keywords": ["alpha", "beta"], "tags": ["insight"],
            },
        ]
        tombs = [{"id": "gone", "reason": "ttl", "at": "2026-08-03T00:00:00+00:00", "excerpt": "x"}]
        text = render_domain(
            domain="notes", scope_id="workflow:demo", revision=7,
            entries=entries, tombstones=tombs,
        )
        fm, parsed, parsed_tombs = parse_domain(text)
        assert fm["revision"] == 7 and fm["domain"] == "notes"
        assert parsed == entries
        assert parsed_tombs == tombs

    def test_rendering_is_deterministic(self):
        entries = [
            {
                "id": "abc123", "text": "a note", "created": "2026-08-01T00:00:00+00:00",
                "updated": "2026-08-01T00:00:00+00:00", "confirmations": 0,
                "run_id": "r1", "keywords": ["alpha"], "tags": [],
            },
        ]
        kwargs = dict(domain="notes", scope_id="workflow:demo", revision=1, tombstones=[])
        assert render_domain(entries=entries, **kwargs) == render_domain(entries=entries, **kwargs)

    def test_frontmatter_is_valid_yaml(self):
        import yaml

        text = render_domain(
            domain="notes", scope_id="workflow:demo", revision=1,
            entries=[], tombstones=[],
        )
        block = text.split("---")[1]
        assert isinstance(yaml.safe_load(block), dict)

    def test_split_scope_round_trips_to_two_components(self):
        assert split_scope("global") == ("_shared", "global")
        assert split_scope("workflow:my flow") == ("_shared", "workflow-my-flow")
        assert split_scope("tenant:acme/agent:bot") == ("acme", "agent-bot")


# ---------------------------------------------------------------------------
# 9. The documented weaknesses
#
# docs/memory-filesystem-vault.md names six places keyword retrieval is worse
# than vectors. Several of them are behaviours a future change could quietly
# "fix" or quietly worsen, so they are pinned here. These tests assert the
# limitation, not the ideal: if one starts failing because the vault got
# smarter, update the docs in the same commit.
# ---------------------------------------------------------------------------


class TestDocumentedWeaknesses:
    def test_stopwords_inflate_overlap_into_a_false_supersede(self):
        """Honesty item 1: shared function words can merge unrelated entries.

        `_word_set` keeps every token over two characters, stopwords included -
        `_STOPWORDS` filters keyword *extraction*, not the overlap measure. On
        short entries a couple of shared function words is a large slice of a
        small word set, so two sentences that share no meaning supersede each
        other. The attic keeps the loser, which is why this is a documented
        cost rather than silent data loss.
        """
        existing = [{"id": "a", "text": "The build is broken on main"}]
        action, target, ratio = decide_write("CI is red on the main branch", existing)
        assert action == "supersede", (
            "docs claim shared stopwords cause a false supersede here"
        )
        assert ratio == pytest.approx(0.5, abs=0.01)
        assert target["id"] == "a"

    @pytest.mark.asyncio
    async def test_a_false_supersede_is_recoverable_from_the_attic(self, vault):
        scope = "workflow:demo"
        await vault.save(scope, "The build is broken on main", {}, "r1")
        await vault.save(scope, "CI is red on the main branch", {}, "r1")
        attic = list((_scope_dir(vault, scope) / "_attic").glob("*.md"))
        assert attic and "The build is broken on main" in attic[0].read_text()

    def test_non_latin_scripts_extract_no_keywords(self):
        """Honesty item 2: the tokenizer is [a-zA-Z0-9_]+."""
        from sandcastle.engine.memory import enrich_memory

        assert enrich_memory("請求書は毎月十五日に届きます。")["keywords"] == []
        # Latin fragments inside Cyrillic text are all that survives.
        assert enrich_memory(
            "Счета от Acme приходят пятнадцатого.",
        )["keywords"] == ["acme"]
        # Latin-script non-English does tokenize - it just keeps its stopwords.
        cz = enrich_memory("Faktury od Acme chodi vzdy patnacteho v mesici.")["keywords"]
        assert "faktury" in cz and "acme" in cz

    @pytest.mark.asyncio
    async def test_cjk_memories_collapse_and_queries_are_ignored(self, vault):
        """Honesty item 2, end to end: it degrades silently, it does not error."""
        scope = "workflow:demo"
        for text in [
            "請求書は毎月十五日に届きます。",
            "サーバーは午前三時に再起動します。",
            "Счета приходят пятнадцатого.",
        ]:
            await vault.save(scope, text, {}, "r1")
        assert len(_domain_files(vault, scope)) == 1, (
            "docs claim non-tokenizable text collapses into one fallback domain"
        )
        # The query extracts no terms, so ranking silently becomes "everything".
        hits = await vault.load(scope, "請求書", 10)
        assert len(hits) == 3
        assert all(h["metadata"]["match_score"] == 0.0 for h in hits)

    def test_routing_score_is_a_staircase_not_a_gradient(self):
        """Honesty item 3: five keywords means six possible scores, no gradient."""
        rows = [{"domain": "d", "keywords": ["k0", "k1", "k2", "k3", "k4"]}]
        # 1/5 = 0.20 is below ROUTE_MIN_SCORE; 2/5 = 0.40 clears it. One word
        # decides which file the entry lands in - there is nothing in between.
        assert route_domain(["k0", "x1", "x2", "x3", "x4"], "t", rows)[1] is True
        assert route_domain(["k0", "k1", "x2", "x3", "x4"], "t", rows)[1] is False

    @pytest.mark.asyncio
    async def test_consolidation_merges_domains_but_never_splits_one(self, vault):
        """Honesty item 5: nothing re-files an entry after the fact."""
        scope = "workflow:demo"
        await vault.save(scope, "sharedtopic logbook aaa bbb ccc ddd eee", {}, "r1")
        await vault.save(scope, "sharedtopic logbook fff ggg hhh iii jjj", {}, "r1")
        assert len(_domain_files(vault, scope)) == 1
        await vault.consolidate(scope, max_age_days=0)
        assert len(_domain_files(vault, scope)) == 1, "consolidation must not split"
