"""Filesystem memory backend - a git-diffable markdown vault.

The mem0+Qdrant backend stores memory as vectors. Vectors retrieve well and
audit badly: nobody can read a 384-float embedding and say what the agent
remembered, when it changed its mind, or what it quietly forgot. Under the EU
AI Act's record-keeping duties, and in any Black Box review, that is the
question actually being asked.

This backend answers it by writing memory as markdown a human can read and git
can diff:

    <vault root>/                       # a dedicated git repo
      <tenant>/
        <scope>/
          INDEX.md                      # <= 1,500 tokens, regenerated
          <domain>.md                   # one file per subject
          _attic/<domain>.md            # superseded text, kept for the diff
          _locks/scope.lock             # flock target

Every write lands in exactly one domain file, chosen by deterministic keyword
routing. Every entry carries its own metadata comment (id, timestamps,
confirmation count, originating run). Every forgotten entry leaves a
tombstone. One commit per run per scope pins the whole thing to a SHA, and that
SHA is pinned in turn into the audit hash chain, because git history on its own
is not tamper-evident.

Retrieval cost is explicitly the thing being traded away. See
``docs/memory-filesystem-vault.md`` for the six places this is worse than
vectors, named.

Zero dependencies beyond the core install: stdlib plus pyyaml.
"""

from __future__ import annotations

import atexit
import errno
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import weakref
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from sandcastle.engine.compaction import estimate_tokens
from sandcastle.engine.memory import (
    MemoryBackendError,
    detect_conflicts,
    enrich_memory,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Limits. These are policy, not tuning knobs - each one is the price of a
# promise made elsewhere in this file.
# ---------------------------------------------------------------------------

#: INDEX.md must stay cheap enough to paste into a prompt or read in one sitting.
INDEX_TOKEN_BUDGET = 1500

#: A scope may hold at most this many domain files. This is a hard error, not a
#: warning: past ~120 files an auditor stops reading a directory and starts
#: grepping it, which is the failure mode the vault exists to avoid.
MAX_DOMAINS_PER_SCOPE = 120

#: A single domain file is capped so it stays reviewable in one diff.
MAX_DOMAIN_FILE_BYTES = 32 * 1024

#: Overlap coefficient thresholds for the write policy (see _decide_write).
CONFIRM_OVERLAP = 0.85
SUPERSEDE_OVERLAP = 0.40

#: Minimum share of a new entry's keywords that must already be present in a
#: domain for the entry to be routed there rather than opening a new domain.
ROUTE_MIN_SCORE = 0.25

#: Keywords tracked per domain. Bounded so routing does not drift into
#: "every domain matches everything" as files grow.
MAX_DOMAIN_KEYWORDS = 24

#: An entry confirmed at least this many times survives TTL expiry. Repetition
#: across runs is the only evidence this backend has that a fact still holds.
CONFIRMATIONS_VETO = 2

#: Superseded text is kept this long before the attic is purged.
ATTIC_MAX_AGE_DAYS = 180

#: Domains whose keyword sets overlap at least this much are merge candidates
#: during consolidation.
DOMAIN_MERGE_OVERLAP = 0.60

#: Directory holding scopes that carry no tenant prefix.
SHARED_TENANT = "_shared"

#: Names the vault reserves for its own bookkeeping. A real tenant or domain
#: that slugs onto one of these is pushed aside rather than colliding with it.
_RESERVED_NAMES = frozenset(
    {"index", "_attic", "_locks", "_health", SHARED_TENANT, ".git", ""},
)

#: Filesystem-safe component charset. Anything else becomes "-".
_UNSAFE_CHARS_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
_DOT_RUN_RE = re.compile(r"\.\.+")
_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")

_META_RE = re.compile(r"<!--\s*meta:\s*(\{.*?\})\s*-->", re.DOTALL)
_TOMB_RE = re.compile(r"<!--\s*forgotten:\s*(\{.*?\})\s*-->", re.DOTALL)


class _RevisionConflict(RuntimeError):
    """A domain file changed underneath us between read and write."""


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def _slug(text: str, *, max_len: int = 64, fallback: str = "unnamed") -> str:
    """Reduce arbitrary text to one safe path component.

    Defence in depth. 0.45 closed the scope regex so ``workflow:..`` can no
    longer reach a backend, but this backend assumes it did anyway: every dot
    run collapses, every character outside ``[A-Za-z0-9_.-]`` becomes a dash,
    leading dots are stripped, and the reserved names are pushed aside. The
    result cannot be ``.``, ``..``, absolute, or a bookkeeping directory, no
    matter what came in.
    """
    cleaned = _UNSAFE_CHARS_RE.sub("-", text.strip())
    cleaned = _DOT_RUN_RE.sub(".", cleaned)
    cleaned = cleaned.strip(".-")
    cleaned = cleaned[:max_len].strip(".-")
    if not cleaned or cleaned.lower() in _RESERVED_NAMES:
        cleaned = f"{cleaned}-d" if cleaned else fallback
    return cleaned


def split_scope(scope_id: str) -> tuple[str, str]:
    """Split a scope id into (tenant component, scope component).

    ``tenant:acme/workflow:deploy`` -> ``("acme", "workflow-deploy")``.
    ``global`` -> ``("_shared", "global")``.

    Both halves go through :func:`_slug`, so the result is always exactly two
    safe path components regardless of the input.
    """
    tenant = SHARED_TENANT
    rest = scope_id
    if scope_id.startswith("tenant:"):
        head, sep, tail = scope_id.partition("/")
        if sep:
            tenant = _slug(head[len("tenant:"):], max_len=64, fallback="_tenant")
            rest = tail
    return tenant, _slug(rest.replace(":", "-"), max_len=128, fallback="scope")


def _contained(child: Path, parent: Path) -> bool:
    """True when *child* resolves inside *parent*."""
    try:
        return child.resolve(strict=False).is_relative_to(parent.resolve(strict=False))
    except (OSError, ValueError):  # pragma: no cover - resolve is defensive here
        return False


# ---------------------------------------------------------------------------
# Small text helpers (shared with engine.memory's heuristics)
# ---------------------------------------------------------------------------


def _word_set(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text) if len(w) > 2}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


def _as_list(value: Any) -> list[str]:
    """Accept the comma-joined strings engine.memory puts in metadata."""
    if value is None:
        return []
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(p).strip() for p in value if str(p).strip()]
    return []


def _keywords_for(content: str, metadata: dict[str, Any] | None) -> list[str]:
    """Keywords for routing: whatever enrichment produced, else derive them."""
    kws = _as_list((metadata or {}).get("keywords"))
    if kws:
        return kws[:8]
    return list(enrich_memory(content).get("keywords") or [])[:8]


def _overlap(a: set[str], b: set[str]) -> float:
    """Szymkiewicz-Simpson overlap coefficient - same measure engine.memory uses."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


# ---------------------------------------------------------------------------
# Domain file format
#
# YAML frontmatter (same shape agent_skills.py parses) plus one markdown
# section per entry. Each section ends in a `meta:` comment holding the
# structured fields, so the prose above it stays clean for a human reader and
# the machine still has ids and counters. Tombstones are comments too, so a
# forgotten entry leaves a visible hole in the diff instead of vanishing.
# ---------------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split ``---`` frontmatter from a body. Mirrors agent_skills._split_frontmatter."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        fm = yaml.safe_load(parts[1].strip()) or {}
    except yaml.YAMLError:
        return {}, parts[2].lstrip("\n")
    if not isinstance(fm, dict):
        fm = {}
    return fm, parts[2].lstrip("\n")


def parse_domain(text: str) -> tuple[dict[str, Any], list[dict], list[dict]]:
    """Parse a domain file into (frontmatter, entries, tombstones).

    Tolerant by design: a hand-edited file with a mangled entry loses that
    entry, not the file. The vault invites human edits; it must survive them.
    """
    fm, body = _split_frontmatter(text)

    tombstones: list[dict] = []
    for match in _TOMB_RE.finditer(body):
        try:
            tombstones.append(json.loads(match.group(1)))
        except (ValueError, TypeError):
            continue
    body = _TOMB_RE.sub("", body)
    # The tombstone section is rendered after the entries; cut it off so its
    # heading and bullets do not get absorbed into the last entry's prose.
    body = re.split(r"^## Forgotten\s*$", body, maxsplit=1, flags=re.MULTILINE)[0]

    entries: list[dict] = []
    chunks = re.split(r"^### ", body, flags=re.MULTILINE)
    for chunk in chunks:
        if not chunk.strip():
            continue
        meta_match = _META_RE.search(chunk)
        if not meta_match:
            continue
        try:
            meta = json.loads(meta_match.group(1))
        except (ValueError, TypeError):
            continue
        if not isinstance(meta, dict) or not meta.get("id"):
            continue
        prose = _META_RE.sub("", chunk)
        # Drop the heading line - it is a rendering of the metadata, not data.
        prose = prose.split("\n", 1)[1] if "\n" in prose else ""
        entry = {
            "id": str(meta.get("id")),
            "text": prose.strip(),
            "created": meta.get("created") or "",
            "updated": meta.get("updated") or meta.get("created") or "",
            "confirmations": int(meta.get("confirmations") or 0),
            "run_id": meta.get("run_id") or "",
            "keywords": _as_list(meta.get("keywords")),
            "tags": _as_list(meta.get("tags")),
        }
        entries.append(entry)
    return fm, entries, tombstones


def render_domain(
    *,
    domain: str,
    scope_id: str,
    revision: int,
    entries: list[dict],
    tombstones: list[dict],
) -> str:
    """Render a domain file. Deterministic - the same state renders byte-identically."""
    keywords: list[str] = []
    seen: set[str] = set()
    for entry in reversed(entries):  # newest entries dominate the routing set
        for kw in entry.get("keywords") or []:
            low = kw.lower()
            if low not in seen:
                seen.add(low)
                keywords.append(low)
    keywords = sorted(keywords[:MAX_DOMAIN_KEYWORDS])

    updated = max(
        (e.get("updated") or e.get("created") or "" for e in entries),
        default="",
    )
    fm = {
        "domain": domain,
        "scope": scope_id,
        "revision": revision,
        "entries": len(entries),
        "updated": updated or _now(),
        "keywords": keywords,
        "forgotten": len(tombstones),
    }
    out = [
        "---",
        yaml.safe_dump(fm, sort_keys=True, default_flow_style=False).rstrip("\n"),
        "---",
        "",
        f"# {domain}",
        "",
    ]
    for entry in entries:
        stamp = (entry.get("updated") or entry.get("created") or "")[:10]
        confirmations = entry.get("confirmations", 0)
        badge = f" (confirmed {confirmations}x)" if confirmations else ""
        out.append(f"### {stamp} - {entry['id']}{badge}")
        out.append("")
        out.append(entry.get("text", "").strip())
        out.append("")
        meta = {
            "id": entry["id"],
            "created": entry.get("created") or "",
            "updated": entry.get("updated") or "",
            "confirmations": confirmations,
            "run_id": entry.get("run_id") or "",
            "keywords": entry.get("keywords") or [],
            "tags": entry.get("tags") or [],
        }
        out.append(
            "<!-- meta: "
            + json.dumps(meta, sort_keys=True, separators=(",", ":"))
            + " -->"
        )
        out.append("")

    if tombstones:
        out.append("## Forgotten")
        out.append("")
        for tomb in tombstones:
            out.append(
                f"- `{tomb.get('id', '?')}` {tomb.get('reason', 'forgotten')} "
                f"at {tomb.get('at', '?')}: {tomb.get('excerpt', '')}"
            )
            out.append(
                "<!-- forgotten: "
                + json.dumps(tomb, sort_keys=True, separators=(",", ":"))
                + " -->"
            )
        out.append("")
    return "\n".join(out)


def render_index(scope_id: str, rows: list[dict], *, dropped: int = 0) -> str:
    """Render INDEX.md for a scope, capped at :data:`INDEX_TOKEN_BUDGET`.

    Rows are dropped least-recently-updated first until the rendered text fits
    the budget measured with the repo's own ``estimate_tokens``. Whatever was
    dropped is stated in an overflow line - the index may be incomplete, but it
    never lies about being complete.
    """
    ordered = sorted(
        rows,
        key=lambda r: (r.get("updated") or "", r.get("domain") or ""),
        reverse=True,
    )
    kept = list(ordered)
    while True:
        text = _render_index_text(
            scope_id, kept, dropped + (len(ordered) - len(kept)),
        )
        if estimate_tokens(text) <= INDEX_TOKEN_BUDGET or not kept:
            return text
        kept.pop()


def _render_index_text(scope_id: str, rows: list[dict], dropped: int) -> str:
    lines = [
        "---",
        yaml.safe_dump(
            {
                "scope": scope_id,
                "domains": len(rows) + dropped,
                "listed": len(rows),
                "generated": _now(),
            },
            sort_keys=True,
            default_flow_style=False,
        ).rstrip("\n"),
        "---",
        "",
        f"# Memory index - {scope_id}",
        "",
        "| domain | entries | updated | keywords |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        kws = ", ".join((row.get("keywords") or [])[:6])
        lines.append(
            f"| [{row['domain']}]({row['domain']}.md) | {row.get('entries', 0)} "
            f"| {(row.get('updated') or '')[:10]} | {kws} |"
        )
    if dropped:
        lines.append("")
        lines.append(
            f"_{dropped} more domain file(s) exist in this directory but did not "
            f"fit the {INDEX_TOKEN_BUDGET}-token index budget. This index is a "
            f"summary, not the record: the directory listing is._"
        )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Atomic writes and locking
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, text: str) -> None:
    """Write-to-temp-then-rename, the same pattern LocalStorage.write uses."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class _FileLock:
    """Advisory ``flock`` on a lock file, with a timeout.

    This is the real mutual exclusion. ``fcntl`` is POSIX-only; on a platform
    without it the lock degrades to a no-op, and the revision CAS is **not** a
    replacement. Measured on this machine with the multiprocess test in
    ``tests/test_memory_fs.py`` (5 processes x 8 writes to one domain file):

        flock + CAS      40/40 writes survive
        CAS only         17/40
        neither          12/40

    The CAS narrows the read-modify-write window; it does not close it, because
    the revision check is itself a read followed by a write. Losing ``fcntl``
    means losing the guarantee, so it is a warning, not a debug line.
    """

    _warned = False

    def __init__(self, path: Path, timeout: float = 30.0) -> None:
        self.path = path
        self.timeout = timeout
        self._handle: Any = None

    def __enter__(self) -> _FileLock:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows
            if not _FileLock._warned:
                _FileLock._warned = True
                logger.warning(
                    "fcntl is unavailable on this platform: the memory vault "
                    "cannot lock a scope. Concurrent writers may lose entries; "
                    "the revision CAS narrows the window but does not close it.",
                )
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "a+")  # noqa: SIM115 - released in __exit__
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    self._handle.close()
                    self._handle = None
                    raise TimeoutError(f"Timed out locking {self.path}") from exc
                time.sleep(0.01)

    def __exit__(self, *exc_info: Any) -> None:
        if self._handle is None:
            return
        try:
            import fcntl

            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except Exception:  # pragma: no cover - best effort unlock
            pass
        finally:
            self._handle.close()
            self._handle = None


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------


class _VaultGit:
    """The vault's own git repo.

    Sandcastle owns this repo: it is created by ``git init`` at the vault root
    and is never expected to have a remote, a user config, or hooks. Hooks are
    neutralised on every single invocation (``-c core.hooksPath=<empty dir>``)
    rather than only at init, so a hook dropped into the repo later still never
    runs. Global and system config are routed to ``os.devnull`` for the same
    reason.

    Every method degrades to a no-op when git is missing or unhappy. A vault
    without git is still a readable vault; it just loses the history.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.available = shutil.which("git") is not None
        self._initialised = False
        self._lock = threading.Lock()

    def _hooks_dir(self) -> Path:
        path = self.root / "_locks" / "no-hooks"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _run(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.update(
            {
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": "",
                "GIT_AUTHOR_NAME": "Sandcastle",
                "GIT_AUTHOR_EMAIL": "memory@sandcastle.local",
                "GIT_COMMITTER_NAME": "Sandcastle",
                "GIT_COMMITTER_EMAIL": "memory@sandcastle.local",
            },
        )
        cmd = [
            "git",
            "-C",
            str(self.root),
            "-c",
            f"core.hooksPath={self._hooks_dir()}",
            "-c",
            "gc.auto=0",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "commit.gpgsign=false",
            *args,
        ]
        return subprocess.run(
            cmd,
            cwd=str(self.root),
            env=env,
            capture_output=True,
            text=True,
            check=check,
            timeout=60,
        )

    def ensure_repo(self) -> bool:
        if not self.available:
            return False
        if self._initialised:
            return True
        with self._lock:
            if self._initialised:
                return True
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                if not (self.root / ".git").exists():
                    self._run(["init", "-q", "-b", "main"])
                    self._run(["config", "core.hooksPath", str(self._hooks_dir())])
                    self._run(["config", "user.name", "Sandcastle"])
                    self._run(["config", "user.email", "memory@sandcastle.local"])
                    gitignore = self.root / ".gitignore"
                    if not gitignore.exists():
                        _atomic_write(gitignore, "_locks/\n*.tmp\n")
                self._initialised = True
                return True
            except Exception as exc:
                logger.warning("Memory vault git unavailable at %s: %s", self.root, exc)
                self.available = False
                return False

    def commit(self, message: str, paths: list[Path]) -> str | None:
        """Stage *paths* and commit. Returns the SHA, or None if nothing happened."""
        if not self.ensure_repo():
            return None
        try:
            rel = [str(p.relative_to(self.root)) for p in paths if _contained(p, self.root)]
            if not rel:
                return None
            self._run(["add", "--", *rel], check=False)
            status = self._run(["status", "--porcelain", "--", *rel], check=False)
            if not status.stdout.strip():
                return None
            self._run(["commit", "-q", "-m", message, "--", *rel], check=False)
            head = self._run(["rev-parse", "HEAD"], check=False)
            sha = head.stdout.strip()
            return sha or None
        except Exception as exc:
            logger.warning("Memory vault commit failed: %s", exc)
            return None


# ---------------------------------------------------------------------------
# The vault
# ---------------------------------------------------------------------------


class _FilesystemVault:
    """All the on-disk work, synchronous. The async backend wraps it in threads."""

    def __init__(self, root: Path, *, use_git: bool = True) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.git = _VaultGit(self.root) if use_git else None

    # -- paths ------------------------------------------------------------

    def scope_dir(self, scope_id: str) -> Path:
        """Resolve a scope to its directory, refusing anything outside the vault.

        Belt and braces. ``engine.memory._validate_scope`` already rejects dot
        runs, and :func:`split_scope` slugs both components, but this method
        assumes a hostile scope reached it anyway and re-checks containment on
        the resolved path before any I/O happens.
        """
        tenant, scope = split_scope(scope_id)
        tenant_root = (self.root / tenant).resolve(strict=False)
        if not _contained(tenant_root, self.root):
            raise ValueError(f"Memory scope escapes the vault root: {scope_id!r}")
        target = (tenant_root / scope).resolve(strict=False)
        if not _contained(target, tenant_root):
            raise ValueError(f"Memory scope escapes its tenant root: {scope_id!r}")
        return target

    def domain_path(self, scope_dir: Path, domain: str) -> Path:
        safe = _slug(domain, max_len=64, fallback="misc")
        target = (scope_dir / f"{safe}.md").resolve(strict=False)
        if not _contained(target, scope_dir):
            raise ValueError(f"Domain name escapes its scope: {domain!r}")
        return target

    def attic_path(self, scope_dir: Path, domain: str) -> Path:
        safe = _slug(domain, max_len=64, fallback="misc")
        target = (scope_dir / "_attic" / f"{safe}.md").resolve(strict=False)
        if not _contained(target, scope_dir):
            raise ValueError(f"Domain name escapes its scope: {domain!r}")
        return target

    def lock_for(self, scope_dir: Path) -> _FileLock:
        return _FileLock(scope_dir / "_locks" / "scope.lock")

    def list_domains(self, scope_dir: Path) -> list[str]:
        if not scope_dir.is_dir():
            return []
        names = []
        for path in scope_dir.iterdir():
            if path.suffix != ".md" or path.name == "INDEX.md":
                continue
            if path.is_symlink() or not path.is_file():
                continue
            names.append(path.stem)
        return sorted(names)

    # -- domain read/write ------------------------------------------------

    def read_domain(self, path: Path) -> tuple[dict, list[dict], list[dict]]:
        if not path.exists() or path.is_symlink():
            return {}, [], []
        try:
            return parse_domain(path.read_text(encoding="utf-8"))
        except OSError as exc:
            logger.warning("Unreadable memory domain %s: %s", path, exc)
            return {}, [], []

    def write_domain(
        self,
        path: Path,
        *,
        domain: str,
        scope_id: str,
        entries: list[dict],
        tombstones: list[dict],
        expected_revision: int | None,
    ) -> int:
        """Write a domain file under a revision compare-and-swap.

        The scope lock is what actually serialises writers. This CAS is a
        second, weaker net: it catches the cases where the file changed between
        our read and our write *outside* the window it cannot see - a hand edit,
        an interrupted retry, a writer on a filesystem that ignored the flock -
        and turns a silent overwrite into a retry. It is a detector, not a
        mutex: see the measurements in :class:`_FileLock`.
        """
        if expected_revision is not None and path.exists():
            current_fm, _, _ = self.read_domain(path)
            current = int(current_fm.get("revision") or 0)
            if current != expected_revision:
                raise _RevisionConflict(
                    f"{path.name}: expected revision {expected_revision}, found {current}"
                )
        revision = (expected_revision or 0) + 1
        text = render_domain(
            domain=domain,
            scope_id=scope_id,
            revision=revision,
            entries=entries,
            tombstones=tombstones,
        )
        _atomic_write(path, text)
        return revision

    def append_attic(self, path: Path, records: list[dict]) -> None:
        """Append superseded / forgotten text to the attic file for a domain."""
        if not records:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = ""
        if path.exists() and not path.is_symlink():
            try:
                existing = path.read_text(encoding="utf-8")
            except OSError:
                existing = ""
        if not existing:
            existing = (
                "---\nkind: attic\n---\n\n"
                "# Attic\n\n"
                "Superseded and forgotten text, kept so the diff shows what "
                "changed rather than only what is current.\n\n"
            )
        chunks = [existing.rstrip("\n"), ""]
        for record in records:
            chunks.append(f"### {record.get('at', '')[:19]} - {record.get('id', '?')}")
            chunks.append("")
            chunks.append(record.get("text", "").strip())
            chunks.append("")
            chunks.append(
                "<!-- attic: "
                + json.dumps(record, sort_keys=True, separators=(",", ":"))
                + " -->"
            )
            chunks.append("")
        _atomic_write(path, "\n".join(chunks))

    # -- index ------------------------------------------------------------

    def rebuild_index(self, scope_id: str, scope_dir: Path) -> Path:
        rows = []
        for domain in self.list_domains(scope_dir):
            fm, entries, _ = self.read_domain(self.domain_path(scope_dir, domain))
            rows.append(
                {
                    "domain": domain,
                    "entries": len(entries),
                    "updated": fm.get("updated") or "",
                    "keywords": _as_list(fm.get("keywords")),
                },
            )
        index_path = scope_dir / "INDEX.md"
        _atomic_write(index_path, render_index(scope_id, rows))
        return index_path

    def read_index(self, scope_dir: Path) -> list[dict]:
        """Parse INDEX.md back into rows, falling back to a directory scan."""
        index_path = scope_dir / "INDEX.md"
        rows: list[dict] = []
        if index_path.exists() and not index_path.is_symlink():
            try:
                _, body = _split_frontmatter(index_path.read_text(encoding="utf-8"))
            except OSError:
                body = ""
            for line in body.splitlines():
                match = re.match(
                    r"^\|\s*\[([^\]]+)\]\([^)]+\)\s*\|\s*(\d+)\s*\|\s*([^|]*)\|\s*([^|]*)\|$",
                    line.strip(),
                )
                if match:
                    rows.append(
                        {
                            "domain": match.group(1),
                            "entries": int(match.group(2)),
                            "updated": match.group(3).strip(),
                            "keywords": [
                                k.strip() for k in match.group(4).split(",") if k.strip()
                            ],
                        },
                    )
        listed = {r["domain"] for r in rows}
        for domain in self.list_domains(scope_dir):
            if domain not in listed:
                rows.append({"domain": domain, "entries": 0, "updated": "", "keywords": []})
        return rows


# ---------------------------------------------------------------------------
# Write policy
# ---------------------------------------------------------------------------


def route_domain(
    keywords: list[str],
    content: str,
    candidates: list[dict],
) -> tuple[str, bool]:
    """Pick the domain file a new entry belongs in.

    Deterministic: score every candidate by the share of the new entry's
    keywords it already carries, take the best, break ties on domain name. No
    model call, no embedding, no randomness - the same content always lands in
    the same file, which is what makes the diff meaningful.

    Returns ``(domain, is_new)``.
    """
    new_kw = {k.lower() for k in keywords} or _word_set(content)
    best_name = ""
    best_score = 0.0
    for row in sorted(candidates, key=lambda r: r.get("domain", "")):
        domain_kw = {k.lower() for k in (row.get("keywords") or [])}
        if not domain_kw or not new_kw:
            continue
        score = len(new_kw & domain_kw) / len(new_kw)
        if score > best_score:
            best_score = score
            best_name = row["domain"]
    if best_name and best_score >= ROUTE_MIN_SCORE:
        return best_name, False

    seed = (keywords[0] if keywords else "") or (sorted(_word_set(content))[:1] or ["misc"])[0]
    return _slug(seed, max_len=48, fallback="misc"), True


def decide_write(content: str, entries: list[dict]) -> tuple[str, dict | None, float]:
    """Classify a write against the entries already in its domain.

    Reuses ``engine.memory.detect_conflicts`` - the overlap heuristic that
    shipped in 0.44 with no caller. It flags anything over 0.40; this backend
    reads the ratio it reports and turns it into a decision:

      >= 0.85   confirm     the same fact again. Bump the counter, write no
                            new entry. Repetition is the only signal this
                            backend has that a fact still holds.
      0.40-0.85 supersede   a changed version of a known fact. Same id, new
                            text, old text to the attic.
      < 0.40    append      a new fact.
    """
    shaped = [{**e, "memory": e.get("text", "")} for e in entries]
    conflicts = detect_conflicts(content, shaped)
    if not conflicts:
        return "append", None, 0.0
    best = max(conflicts, key=lambda c: c.get("_overlap_ratio", 0.0))
    ratio = float(best.get("_overlap_ratio", 0.0))
    target = next((e for e in entries if e["id"] == best.get("id")), None)
    if target is None:
        return "append", None, ratio
    if ratio >= CONFIRM_OVERLAP:
        return "confirm", target, ratio
    if ratio >= SUPERSEDE_OVERLAP:
        return "supersede", target, ratio
    return "append", None, ratio


# ---------------------------------------------------------------------------
# The backend
# ---------------------------------------------------------------------------


def default_vault_root() -> Path:
    """Vault location: ``MEMORY_FS_ROOT`` if set, else ``<data_dir>/memory-vault``."""
    try:
        from sandcastle.config import settings

        configured = (settings.memory_fs_root or "").strip()
        base = configured or str(Path(settings.data_dir) / "memory-vault")
    except Exception:  # pragma: no cover - settings should always import
        base = str(Path.home() / ".sandcastle" / "data" / "memory-vault")
    return Path(base).expanduser()


class FilesystemMemoryBackend:
    """``MemoryBackend`` over a markdown vault. Registered as ``"filesystem"``.

    Conforms to the Protocol in ``engine.memory``: five async methods, nothing
    else. Everything backend-agnostic (scope validation, size clamping,
    admission control, enrichment, decay) stays in ``engine.memory`` and is not
    duplicated here.
    """

    name = "filesystem"

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        use_git: bool | None = None,
        max_age_days: int | None = None,
    ) -> None:
        if use_git is None:
            try:
                from sandcastle.config import settings

                use_git = bool(settings.memory_fs_git)
            except Exception:  # pragma: no cover
                use_git = True
        self._vault = _FilesystemVault(
            Path(root) if root else default_vault_root(),
            use_git=bool(use_git),
        )
        self._max_age_days = max_age_days
        # (scope_id -> run_id) currently open, and the files each touched.
        self._open_runs: dict[str, str] = {}
        self._dirty: dict[str, set[Path]] = {}
        self._state_lock = threading.Lock()
        _register_for_flush(self)

    # -- properties -------------------------------------------------------

    @property
    def root(self) -> Path:
        return self._vault.root

    # -- MemoryBackend ----------------------------------------------------

    async def load(self, scope_id: str, query: str, limit: int) -> list[dict]:
        import asyncio

        return await asyncio.to_thread(self._load_sync, scope_id, query, limit)

    async def save(
        self,
        scope_id: str,
        content: str,
        metadata: dict[str, Any],
        run_id: str,
    ) -> list[dict]:
        import asyncio

        commits, records = await asyncio.to_thread(
            self._save_sync, scope_id, content, metadata, run_id,
        )
        await _pin_commits(commits)
        return records

    async def delete(self, memory_id: str, scope_id: str | None = None) -> bool:
        import asyncio

        commits, ok = await asyncio.to_thread(self._delete_sync, memory_id, scope_id)
        await _pin_commits(commits)
        return ok

    async def delete_all(self, scope_id: str) -> bool:
        import asyncio

        commits, ok = await asyncio.to_thread(self._delete_all_sync, scope_id)
        await _pin_commits(commits)
        return ok

    async def health(self) -> None:
        import asyncio

        await asyncio.to_thread(self._health_sync)

    # -- extra public surface (not part of the Protocol) ------------------

    async def end_run(self, run_id: str = "", scope_id: str | None = None) -> list[dict]:
        """Consolidate and commit everything this run touched.

        One pass, at the end. Forgetting is expensive and destructive, so it
        happens once per run rather than on every write.
        """
        import asyncio

        commits = await asyncio.to_thread(self._end_run_sync, run_id, scope_id)
        await _pin_commits(commits)
        return commits

    async def consolidate(self, scope_id: str, max_age_days: int | None = None) -> dict:
        import asyncio

        return await asyncio.to_thread(self._consolidate_sync, scope_id, max_age_days)

    # -- sync implementations --------------------------------------------

    def _load_sync(self, scope_id: str, query: str, limit: int) -> list[dict]:
        scope_dir = self._vault.scope_dir(scope_id)
        if not scope_dir.is_dir():
            return []

        rows = self._vault.read_index(scope_dir)
        query_kw = _word_set(query) if query else set()

        if query_kw:
            ranked = sorted(
                rows,
                key=lambda r: (
                    -_overlap(query_kw, {k.lower() for k in (r.get("keywords") or [])}),
                    r.get("domain", ""),
                ),
            )
            # Linear scan, capped. Beyond this the index is doing no work and a
            # vector store would be doing all of it - see the honesty docs.
            ranked = ranked[: max(8, limit)]
        else:
            ranked = rows

        collected: list[tuple[float, str, dict]] = []
        for row in ranked:
            domain = row["domain"]
            _, entries, _ = self._vault.read_domain(
                self._vault.domain_path(scope_dir, domain),
            )
            for entry in entries:
                if query_kw:
                    score = _overlap(query_kw, _word_set(entry.get("text", "")))
                    kw_hit = _overlap(query_kw, {k.lower() for k in entry.get("keywords", [])})
                    score = max(score, kw_hit)
                    if score <= 0.0:
                        continue
                else:
                    score = 0.0
                collected.append((score, entry.get("updated") or "", {**entry, "domain": domain}))

        collected.sort(key=lambda t: (t[0], t[1]), reverse=True)
        out: list[dict] = []
        for score, _, entry in collected[:limit]:
            out.append(
                {
                    "id": entry["id"],
                    "memory": entry.get("text", ""),
                    "metadata": {
                        "domain": entry.get("domain", ""),
                        "confirmations": entry.get("confirmations", 0),
                        "run_id": entry.get("run_id", ""),
                        "keywords": ",".join(entry.get("keywords") or []),
                        "tags": ",".join(entry.get("tags") or []),
                        "match_score": round(score, 3),
                    },
                    "created_at": entry.get("created", ""),
                    "updated_at": entry.get("updated", ""),
                },
            )
        return out

    def _save_sync(
        self,
        scope_id: str,
        content: str,
        metadata: dict[str, Any],
        run_id: str,
    ) -> tuple[list[dict], list[dict]]:
        content = (content or "").strip()
        if not content:
            return [], []

        commits = self._rotate_run(scope_id, run_id)
        scope_dir = self._vault.scope_dir(scope_id)
        scope_dir.mkdir(parents=True, exist_ok=True)

        keywords = _keywords_for(content, metadata)
        tags = _as_list((metadata or {}).get("tags"))

        # Fixed lock ordering: scope lock first, git repo lock second (taken
        # inside _VaultGit). Never the reverse - that is the whole deadlock
        # avoidance strategy, and it is only safe because it is the only order
        # anything in this module ever uses.
        with self._vault.lock_for(scope_dir):
            record = self._write_entry_locked(
                scope_id, scope_dir, content, keywords, tags, run_id,
            )
            index_path = self._vault.rebuild_index(scope_id, scope_dir)

        self._mark_dirty(scope_id, [record["_path"], index_path])
        record.pop("_path", None)
        return commits, [record]

    def _write_entry_locked(
        self,
        scope_id: str,
        scope_dir: Path,
        content: str,
        keywords: list[str],
        tags: list[str],
        run_id: str,
    ) -> dict:
        rows = self._vault.read_index(scope_dir)
        # Re-read keyword sets from the files themselves: INDEX.md is a
        # summary and may have dropped rows to fit its token budget.
        for row in rows:
            fm, _, _ = self._vault.read_domain(
                self._vault.domain_path(scope_dir, row["domain"]),
            )
            row["keywords"] = _as_list(fm.get("keywords")) or row.get("keywords") or []

        domain, is_new = route_domain(keywords, content, rows)
        if is_new and len({r["domain"] for r in rows}) >= MAX_DOMAINS_PER_SCOPE:
            raise MemoryVaultFull(
                f"Memory scope {scope_id!r} already holds "
                f"{MAX_DOMAINS_PER_SCOPE} domain files. That cap is the price of "
                f"auditability: past it, nobody reads the directory. Consolidate "
                f"or narrow the scope before writing more."
            )

        path = self._vault.domain_path(scope_dir, domain)
        for attempt in range(4):
            fm, entries, tombstones = self._vault.read_domain(path)
            revision = int(fm.get("revision") or 0)
            action, target, ratio = decide_write(content, entries)
            now = _now()
            attic: list[dict] = []

            if action == "confirm" and target is not None:
                target["confirmations"] = int(target.get("confirmations", 0)) + 1
                target["updated"] = now
                if run_id:
                    target["run_id"] = run_id
                entry = target
            elif action == "supersede" and target is not None:
                attic.append(
                    {
                        "id": target["id"],
                        "text": target.get("text", ""),
                        "at": now,
                        "reason": "superseded",
                        "overlap": round(ratio, 3),
                        "run_id": run_id,
                    },
                )
                target["text"] = content
                target["updated"] = now
                target["keywords"] = keywords
                target["tags"] = tags
                if run_id:
                    target["run_id"] = run_id
                entry = target
            else:
                entry = {
                    "id": uuid.uuid4().hex[:12],
                    "text": content,
                    "created": now,
                    "updated": now,
                    "confirmations": 0,
                    "run_id": run_id,
                    "keywords": keywords,
                    "tags": tags,
                }
                entries.append(entry)

            evicted = self._enforce_size_cap(domain, scope_id, entries, tombstones, entry["id"])
            attic.extend(evicted)

            try:
                self._vault.write_domain(
                    path,
                    domain=domain,
                    scope_id=scope_id,
                    entries=entries,
                    tombstones=tombstones,
                    expected_revision=revision,
                )
            except _RevisionConflict as exc:
                # Merge, do not fail: re-read and re-apply the same decision
                # against the newer state. A concurrent writer's entry stays.
                logger.debug("Vault CAS retry %d on %s: %s", attempt + 1, path.name, exc)
                continue

            if attic:
                self._vault.append_attic(self._vault.attic_path(scope_dir, domain), attic)
            return {
                "id": entry["id"],
                "memory": entry["text"],
                "event": action,
                "domain": domain,
                "overlap": round(ratio, 3),
                "metadata": {"domain": domain, "confirmations": entry.get("confirmations", 0)},
                "created_at": entry.get("created", ""),
                "updated_at": entry.get("updated", ""),
                "_path": path,
            }

        raise MemoryVaultConflict(
            f"Could not write memory domain {domain!r} in {scope_id!r}: "
            f"revision changed under us 4 times running."
        )

    def _enforce_size_cap(
        self,
        domain: str,
        scope_id: str,
        entries: list[dict],
        tombstones: list[dict],
        keep_id: str,
    ) -> list[dict]:
        """Evict oldest entries until the rendered file fits the size cap.

        Eviction is forgetting, so it is recorded: each evicted entry leaves a
        tombstone in the file and its text in the attic.
        """
        evicted: list[dict] = []
        while len(entries) > 1:
            text = render_domain(
                domain=domain,
                scope_id=scope_id,
                revision=0,
                entries=entries,
                tombstones=tombstones,
            )
            if len(text.encode("utf-8")) <= MAX_DOMAIN_FILE_BYTES:
                return evicted
            victim = min(
                (e for e in entries if e["id"] != keep_id),
                key=lambda e: (e.get("confirmations", 0), e.get("updated") or ""),
                default=None,
            )
            if victim is None:
                break
            entries.remove(victim)
            now = _now()
            tombstones.append(
                {
                    "id": victim["id"],
                    "reason": "size-cap",
                    "at": now,
                    "excerpt": victim.get("text", "")[:80],
                },
            )
            evicted.append({**victim, "at": now, "reason": "size-cap"})

        single = render_domain(
            domain=domain, scope_id=scope_id, revision=0,
            entries=entries, tombstones=tombstones,
        )
        if len(single.encode("utf-8")) > MAX_DOMAIN_FILE_BYTES:
            raise MemoryVaultFull(
                f"A single memory entry does not fit the "
                f"{MAX_DOMAIN_FILE_BYTES}-byte domain file cap. Shorten it, or "
                f"store it as an artifact and remember the reference."
            )
        return evicted

    def _delete_sync(
        self, memory_id: str, scope_id: str | None,
    ) -> tuple[list[dict], bool]:
        scopes = [scope_id] if scope_id else self._all_scope_ids()
        for candidate in scopes:
            try:
                scope_dir = self._vault.scope_dir(candidate)
            except ValueError:
                continue
            if not scope_dir.is_dir():
                continue
            with self._vault.lock_for(scope_dir):
                for domain in self._vault.list_domains(scope_dir):
                    path = self._vault.domain_path(scope_dir, domain)
                    fm, entries, tombstones = self._vault.read_domain(path)
                    match = next((e for e in entries if e["id"] == memory_id), None)
                    if match is None:
                        continue
                    entries.remove(match)
                    now = _now()
                    tombstones.append(
                        {
                            "id": memory_id,
                            "reason": "deleted",
                            "at": now,
                            "excerpt": match.get("text", "")[:80],
                        },
                    )
                    self._vault.write_domain(
                        path,
                        domain=domain,
                        scope_id=candidate,
                        entries=entries,
                        tombstones=tombstones,
                        expected_revision=int(fm.get("revision") or 0),
                    )
                    self._vault.append_attic(
                        self._vault.attic_path(scope_dir, domain),
                        [{**match, "at": now, "reason": "deleted"}],
                    )
                    index_path = self._vault.rebuild_index(candidate, scope_dir)
                    commits = self._commit(
                        candidate, "", [path, index_path],
                        f"memory({candidate}): forget {memory_id}",
                    )
                    return commits, True
        return [], False

    def _delete_all_sync(self, scope_id: str) -> tuple[list[dict], bool]:
        scope_dir = self._vault.scope_dir(scope_id)
        if not scope_dir.is_dir():
            return [], True
        with self._vault.lock_for(scope_dir):
            removed = []
            for domain in self._vault.list_domains(scope_dir):
                path = self._vault.domain_path(scope_dir, domain)
                try:
                    path.unlink()
                    removed.append(path)
                except OSError as exc:  # pragma: no cover - unlink is defensive
                    logger.warning("Could not remove %s: %s", path, exc)
            attic = scope_dir / "_attic"
            if attic.is_dir() and _contained(attic, scope_dir):
                shutil.rmtree(attic, ignore_errors=True)
            index_path = self._vault.rebuild_index(scope_id, scope_dir)
        with self._state_lock:
            self._open_runs.pop(scope_id, None)
            self._dirty.pop(scope_id, None)
        commits = self._commit(
            scope_id, "", [*removed, index_path, scope_dir],
            f"memory({scope_id}): erase scope",
        )
        return commits, True

    def _health_sync(self) -> None:
        self._vault.root.mkdir(parents=True, exist_ok=True)
        probe = self._vault.root / "_locks" / "health.probe"
        _atomic_write(probe, _now())
        try:
            probe.unlink()
        except OSError:  # pragma: no cover
            pass

    # -- consolidation ----------------------------------------------------

    def _consolidate_sync(self, scope_id: str, max_age_days: int | None) -> dict:
        """One pass of forgetting: TTL, domain merge, attic purge."""
        if max_age_days is None:
            max_age_days = self._resolve_max_age()
        scope_dir = self._vault.scope_dir(scope_id)
        if not scope_dir.is_dir():
            return {"expired": 0, "merged": 0, "attic_purged": 0, "paths": []}

        expired = merged = purged = 0
        touched: list[Path] = []
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max_age_days)
            if max_age_days and max_age_days > 0
            else None
        )

        with self._vault.lock_for(scope_dir):
            # 1. TTL, with the confirmations veto.
            for domain in self._vault.list_domains(scope_dir):
                path = self._vault.domain_path(scope_dir, domain)
                fm, entries, tombstones = self._vault.read_domain(path)
                if not entries:
                    continue
                keep, drop = [], []
                for entry in entries:
                    updated = _parse_ts(entry.get("updated") or entry.get("created"))
                    stale = cutoff is not None and updated is not None and updated < cutoff
                    veto = int(entry.get("confirmations", 0)) >= CONFIRMATIONS_VETO
                    (drop if (stale and not veto) else keep).append(entry)
                if not drop:
                    continue
                now = _now()
                for entry in drop:
                    tombstones.append(
                        {
                            "id": entry["id"],
                            "reason": "ttl",
                            "at": now,
                            "excerpt": entry.get("text", "")[:80],
                        },
                    )
                self._vault.append_attic(
                    self._vault.attic_path(scope_dir, domain),
                    [{**e, "at": now, "reason": "ttl"} for e in drop],
                )
                expired += len(drop)
                self._vault.write_domain(
                    path,
                    domain=domain,
                    scope_id=scope_id,
                    entries=keep,
                    tombstones=tombstones,
                    expected_revision=int(fm.get("revision") or 0),
                )
                touched.append(path)

            # 2. Domain merge. Two files about the same subject are two files
            #    an auditor has to correlate; merging them costs nothing and
            #    buys headroom under the 120 cap.
            merged, merge_paths = self._merge_domains_locked(scope_id, scope_dir)
            touched.extend(merge_paths)

            # 3. Attic purge. The attic exists for the diff, not forever.
            purged, purge_paths = self._purge_attic_locked(scope_dir)
            touched.extend(purge_paths)

            touched.append(self._vault.rebuild_index(scope_id, scope_dir))

        return {
            "expired": expired,
            "merged": merged,
            "attic_purged": purged,
            "paths": touched,
        }

    def _merge_domains_locked(self, scope_id: str, scope_dir: Path) -> tuple[int, list[Path]]:
        domains = self._vault.list_domains(scope_dir)
        keywords: dict[str, set[str]] = {}
        for domain in domains:
            fm, _, _ = self._vault.read_domain(self._vault.domain_path(scope_dir, domain))
            keywords[domain] = {k.lower() for k in _as_list(fm.get("keywords"))}

        merged = 0
        touched: list[Path] = []
        gone: set[str] = set()
        for i, left in enumerate(domains):
            if left in gone:
                continue
            for right in domains[i + 1:]:
                if right in gone or not keywords[left] or not keywords[right]:
                    continue
                if _overlap(keywords[left], keywords[right]) < DOMAIN_MERGE_OVERLAP:
                    continue
                # The lexicographically-first name wins, so the merge is
                # deterministic and reruns are no-ops.
                target, source = sorted((left, right))
                t_path = self._vault.domain_path(scope_dir, target)
                s_path = self._vault.domain_path(scope_dir, source)
                t_fm, t_entries, t_tombs = self._vault.read_domain(t_path)
                _, s_entries, s_tombs = self._vault.read_domain(s_path)
                known = {e["id"] for e in t_entries}
                t_entries.extend(e for e in s_entries if e["id"] not in known)
                t_tombs.extend(s_tombs)
                t_tombs.append(
                    {
                        "id": source,
                        "reason": "merged-into-" + target,
                        "at": _now(),
                        "excerpt": f"{len(s_entries)} entries moved",
                    },
                )
                self._vault.write_domain(
                    t_path,
                    domain=target,
                    scope_id=scope_id,
                    entries=t_entries,
                    tombstones=t_tombs,
                    expected_revision=int(t_fm.get("revision") or 0),
                )
                try:
                    s_path.unlink()
                except OSError:  # pragma: no cover
                    pass
                gone.add(source)
                keywords[target] = keywords[left] | keywords[right]
                merged += 1
                touched.extend([t_path, s_path])
                if source == left:
                    break
        return merged, touched

    def _purge_attic_locked(self, scope_dir: Path) -> tuple[int, list[Path]]:
        attic = scope_dir / "_attic"
        if not attic.is_dir() or not _contained(attic, scope_dir):
            return 0, []
        cutoff = datetime.now(timezone.utc) - timedelta(days=ATTIC_MAX_AGE_DAYS)
        purged = 0
        touched: list[Path] = []
        for path in sorted(attic.glob("*.md")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            parts = text.split("\n### ")
            kept_chunks = [parts[0]]
            dropped = 0
            for chunk in parts[1:]:
                match = re.search(
                    r"<!--\s*attic:\s*(\{.*?\})\s*-->", chunk, re.DOTALL,
                )
                stamp = None
                if match:
                    try:
                        stamp = _parse_ts(json.loads(match.group(1)).get("at"))
                    except (ValueError, TypeError):
                        stamp = None
                if stamp is not None and stamp < cutoff:
                    dropped += 1
                    continue
                kept_chunks.append(chunk)
            if dropped:
                _atomic_write(
                    path, "\n### ".join(kept_chunks).rstrip("\n") + "\n",
                )
                purged += dropped
                touched.append(path)
        return purged, touched

    # -- run bookkeeping / git -------------------------------------------

    def _resolve_max_age(self) -> int:
        if self._max_age_days is not None:
            return self._max_age_days
        try:
            from sandcastle.config import settings

            return int(settings.memory_max_age_days)
        except Exception:  # pragma: no cover
            return 90

    def _all_scope_ids(self) -> list[str]:
        """Best-effort reverse lookup for a scope-less delete.

        Slugging is lossy, so the scope id recovered here is the *directory*
        name, which round-trips through split_scope to the same directory. That
        is all _delete_sync needs.
        """
        out: list[str] = []
        if not self._vault.root.is_dir():
            return out
        skip = {"_locks", "_attic", ".git"}
        for tenant_dir in sorted(self._vault.root.iterdir()):
            if not tenant_dir.is_dir() or tenant_dir.name in skip:
                continue
            if tenant_dir.name.startswith("."):
                continue
            for scope_dir in sorted(tenant_dir.iterdir()):
                if not scope_dir.is_dir() or scope_dir.name in skip:
                    continue
                if scope_dir.name.startswith("."):
                    continue
                prefix = (
                    "" if tenant_dir.name == SHARED_TENANT
                    else f"tenant:{tenant_dir.name}/"
                )
                out.append(prefix + scope_dir.name)
        return out

    def _mark_dirty(self, scope_id: str, paths: list[Path]) -> None:
        with self._state_lock:
            self._dirty.setdefault(scope_id, set()).update(paths)

    def _rotate_run(self, scope_id: str, run_id: str) -> list[dict]:
        """Close the previous run for this scope when a new one starts writing.

        One commit per run per scope. Without an executor hook to tell us a run
        ended, the next run's first write is the signal - plus an atexit flush,
        so a process that stops mid-run still commits. Nothing is ever lost by
        waiting: the files are already on disk, only the attribution is coarse.
        """
        with self._state_lock:
            previous = self._open_runs.get(scope_id)
            if previous is None or previous == run_id:
                self._open_runs[scope_id] = run_id
                return []
        commits = self._end_run_sync(previous, scope_id)
        with self._state_lock:
            self._open_runs[scope_id] = run_id
        return commits

    def _end_run_sync(self, run_id: str, scope_id: str | None) -> list[dict]:
        with self._state_lock:
            scopes = [scope_id] if scope_id else list(self._dirty.keys())
        commits: list[dict] = []
        for scope in scopes:
            with self._state_lock:
                paths = self._dirty.pop(scope, set())
                open_run = self._open_runs.pop(scope, "")
            if not paths:
                continue
            effective_run = run_id or open_run
            try:
                result = self._consolidate_sync(scope, None)
                paths.update(p for p in result.get("paths", []))
            except Exception as exc:
                logger.warning("Vault consolidation failed for %s: %s", scope, exc)
            commits.extend(
                self._commit(
                    scope,
                    effective_run,
                    sorted(paths),
                    f"memory({scope}): run {effective_run or 'adhoc'}",
                ),
            )
        return commits

    def _commit(
        self, scope_id: str, run_id: str, paths: list[Path], message: str,
    ) -> list[dict]:
        if self._vault.git is None:
            return []
        sha = self._vault.git.commit(message, paths)
        if not sha:
            return []
        logger.info("Memory vault commit %s for %s", sha[:12], scope_id)
        return [
            {
                "commit": sha,
                "scope_id": scope_id,
                "run_id": run_id,
                "root": str(self._vault.root),
                "files": [
                    str(p.relative_to(self._vault.root))
                    for p in paths
                    if _contained(p, self._vault.root)
                ][:50],
            },
        ]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MemoryVaultFull(MemoryBackendError):
    """A hard cap was hit. Raised, not warned - see the cap docstrings."""


class MemoryVaultConflict(MemoryBackendError):
    """Concurrent writers could not be reconciled after repeated retries."""


# ---------------------------------------------------------------------------
# Audit-chain pinning
#
# git history is not tamper-evident: anyone with write access to the vault can
# rewrite it, and `git commit --amend` leaves no trace. Nor is it
# GDPR-erasable in place - deleting a file removes it from HEAD, not from
# history. The mitigation is to pin each commit SHA into the audit hash chain,
# which *is* tamper-evident: a rewritten vault no longer matches the SHA the
# chain recorded, and the mismatch is detectable. Erasure still needs a
# history rewrite plus a fresh pin; the vault does not pretend otherwise.
# ---------------------------------------------------------------------------


async def _pin_commits(commits: list[dict]) -> None:
    """Append one audit event per vault commit. Best effort, never fatal."""
    for commit in commits:
        try:
            from sandcastle.engine.audit import append_audit_event
            from sandcastle.models.db import async_session

            async with async_session() as session:
                await append_audit_event(
                    session=session,
                    event_type="memory.vault.commit",
                    run_id=commit.get("run_id") or None,
                    actor_id="memory-vault",
                    payload={
                        "commit": commit.get("commit", ""),
                        "scope_id": commit.get("scope_id", ""),
                        "root": commit.get("root", ""),
                        "files": commit.get("files", []),
                    },
                )
                await session.commit()
        except Exception as exc:
            logger.debug("Could not pin vault commit to the audit chain: %s", exc)


# ---------------------------------------------------------------------------
# Process-exit flush
# ---------------------------------------------------------------------------

# Weak references: a backend the caller has dropped must not be kept alive (or
# have its tmpdir committed to) just because it once registered here. The one
# the engine actually uses lives in engine.memory._backends and survives to
# exit, which is the case this hook is for.
_live_backends: weakref.WeakSet = weakref.WeakSet()
_live_lock = threading.Lock()


def _register_for_flush(backend: FilesystemMemoryBackend) -> None:
    with _live_lock:
        _live_backends.add(backend)


@atexit.register
def _flush_all() -> None:  # pragma: no cover - exercised by process exit
    """Commit any run still open when the process stops.

    Without an executor hook telling the vault a run ended, a process that
    exits mid-run would leave its last writes uncommitted. The files are
    already on disk either way; this is about attribution, not durability.
    """
    with _live_lock:
        backends = list(_live_backends)
    for backend in backends:
        try:
            backend._end_run_sync("", None)
        except Exception:
            pass
