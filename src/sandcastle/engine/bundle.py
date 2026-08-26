"""Verified template bundles (.sctpl): a workflow plus its proof-of-execution.

A bundle is a single zip archive that ships a workflow YAML together with one or
more recorded cassettes and a ``manifest.json`` carrying SHA-256 checksums of every
payload file. The cassettes are the proof: ``sandcastle template verify`` replays
them against the bundled workflow in strict replay mode - offline, $0, no provider
calls - and reports PASS/FAIL per cassette. A tampered cassette fails the checksum;
a tampered workflow changes the step cache keys and fails the replay. Either way,
the proof breaks before anything runs live.

Layout inside the archive::

    manifest.json                  # metadata + sha256 checksums
    workflow.yaml                  # the workflow definition
    cassettes/<name>.cassette.json # one or more recorded cassettes

Bundles are untrusted input. Reading one never extracts to disk blindly: member
names are validated (no absolute paths, no ``..`` traversal), counts and sizes are
capped, the workflow YAML goes through the community hub security scanner, and the
replay itself runs with ``admin_trusted=False`` so ``code`` steps cannot execute.
Verification only replays workflows whose steps are all cassette-covered types, so
no step ever touches the network or the host.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sandcastle.engine.cassette import CassetteStore

logger = logging.getLogger(__name__)

BUNDLE_FORMAT = "sctpl"
BUNDLE_FORMAT_VERSION = 1
BUNDLE_SUFFIX = ".sctpl"

MANIFEST_NAME = "manifest.json"
WORKFLOW_NAME = "workflow.yaml"
CASSETTE_DIR = "cassettes"

# Bundles are untrusted input - hard limits before anything is parsed.
MAX_BUNDLE_SIZE = 10 * 1024 * 1024  # 10MB archive on disk
MAX_MEMBER_SIZE = 5 * 1024 * 1024  # 5MB per decompressed member
MAX_MEMBERS = 32  # manifest + workflow + cassettes, with headroom

# Step types whose replay is provably inert *from the bundled file alone*.
#
# Since 0.45 the cassette hook sits in ``execute_step_with_retry``, so every
# hybrid type is looked up before it executes and a strict-replay miss aborts
# the step before any network. That makes far more types technically coverable
# - but coverage is not the bar here. Verification runs untrusted content, so a
# type only earns a place on this list when a cassette *hit* also causes
# nothing: no request, no file, no spend. ``http``, ``notify``, ``code``,
# ``browser``, ``computer-use``, ``tool``, ``composio``, ``openclaw``,
# ``agent``, ``managed-agent``, ``delegate``, ``sub_workflow`` and ``report``
# stay excluded regardless of the ledger, which is installation-local and must
# never be consulted on somebody else's cassette. ``condition``/``classify``/
# ``gate`` are excluded because the guard deliberately never intercepts them
# (they mutate branch selection), so they would execute live.
REPLAY_SAFE_STEP_TYPES = frozenset(
    {"standard", "llm", "parse", "transform", "trajectory-replay"}
)

_REQUIRED_MANIFEST_FIELDS = (
    "format",
    "format_version",
    "name",
    "version",
    "description",
    "author",
    "sandcastle_version",
    "created_at",
    "workflow",
    "cassettes",
)


class BundleError(Exception):
    """A bundle is malformed, oversized, or fails a safety check."""


class CassetteMissError(Exception):
    """Strict replay hit a step that has no recorded output in the cassette."""


class StrictReplayCassette(CassetteStore):
    """A replay store where a key miss is a hard failure instead of a live call.

    The stock ``CassetteStore`` falls through to live execution on a miss so a
    changed workflow still runs. Verification needs the opposite guarantee: a
    miss means the bundled workflow no longer matches its recorded proof, and
    nothing may reach a provider. Raising aborts the step before any network.
    """

    def get(self, cache_key: str) -> dict[str, Any] | None:
        rec = super().get(cache_key)
        if rec is None:
            raise CassetteMissError(
                f"strict replay miss for cache key {cache_key[:12]}... - the workflow "
                "does not match the recorded cassette"
            )
        return rec


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class CassetteVerifyResult:
    """Outcome of replaying one bundled cassette against the bundled workflow."""

    file: str
    passed: bool
    detail: str = ""
    replay_hits: int = 0
    replay_misses: int = 0


@dataclass
class BundleVerifyResult:
    """Aggregated outcome of ``verify_bundle`` - PASS only when everything holds."""

    ok: bool
    manifest: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    cassette_results: list[CassetteVerifyResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_member_name(name: str) -> bool:
    """True when a zip member name cannot escape the extraction root."""
    if not name or name != name.strip():
        return False
    if "\\" in name or name.startswith("/") or ":" in name:
        return False
    parts = name.split("/")
    return all(p not in ("", ".", "..") for p in parts)


def _git_created_at(path: Path) -> str | None:
    """Last git commit date (ISO 8601) of *path*, or None outside a repo."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", path.name],
            cwd=path.parent,
            capture_output=True,
            text=True,
            timeout=5,
        )
        stamp = out.stdout.strip()
        return stamp or None
    except Exception:
        return None


def _validate_manifest(manifest: Any) -> list[str]:
    """Return a list of manifest schema problems (empty = valid)."""
    if not isinstance(manifest, dict):
        return ["manifest.json must be a JSON object"]
    problems = [f"manifest missing required field '{f}'" for f in _REQUIRED_MANIFEST_FIELDS
                if f not in manifest]
    if problems:
        return problems
    if manifest["format"] != BUNDLE_FORMAT:
        problems.append(f"unknown bundle format '{manifest['format']}'")
    if manifest["format_version"] != BUNDLE_FORMAT_VERSION:
        problems.append(
            f"unsupported format_version {manifest['format_version']} "
            f"(this Sandcastle supports {BUNDLE_FORMAT_VERSION})"
        )
    wf = manifest["workflow"]
    if not isinstance(wf, dict) or "file" not in wf or "sha256" not in wf:
        problems.append("manifest 'workflow' must be an object with 'file' and 'sha256'")
    cassettes = manifest["cassettes"]
    if not isinstance(cassettes, list) or not cassettes:
        problems.append("manifest 'cassettes' must be a non-empty list")
    else:
        for i, c in enumerate(cassettes):
            if not isinstance(c, dict) or "file" not in c or "sha256" not in c:
                problems.append(f"manifest cassettes[{i}] must have 'file' and 'sha256'")
    return problems


# ---------------------------------------------------------------------------
# Pack
# ---------------------------------------------------------------------------


def create_bundle(
    workflow_path: str | Path,
    cassette_paths: list[str | Path],
    output_path: str | Path | None = None,
    *,
    name: str | None = None,
    version: str = "1.0.0",
    description: str | None = None,
    author: str = "",
    license_id: str = "MIT",
    example_inputs: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> Path:
    """Pack a workflow plus recorded cassettes into a .sctpl bundle.

    *name* / *description* default to the workflow YAML's own fields. *created_at*
    defaults to the workflow file's last git commit date, falling back to now.
    *output_path* defaults to ``<name>-<version>.sctpl`` in the working directory.
    Returns the written bundle path.
    """
    import re

    import yaml

    workflow_path = Path(workflow_path)
    if not cassette_paths:
        raise BundleError("a bundle needs at least one recorded cassette")

    workflow_yaml = workflow_path.read_text()
    data = yaml.safe_load(workflow_yaml)
    if not isinstance(data, dict) or "name" not in data:
        raise BundleError(f"{workflow_path} is not a workflow YAML (missing 'name')")

    bundle_name = name or str(data["name"])
    if output_path is None:
        safe_stem = re.sub(r"[^a-zA-Z0-9_\-]", "_", bundle_name.split("/")[-1]).lstrip(".")
        output_path = Path(f"{safe_stem or 'template'}-{version}{BUNDLE_SUFFIX}")
    output_path = Path(output_path)

    cassette_entries: list[dict[str, Any]] = []
    cassette_blobs: dict[str, bytes] = {}
    for cp in cassette_paths:
        cp = Path(cp)
        blob = cp.read_bytes()
        try:
            cassette_data = json.loads(blob)
        except json.JSONDecodeError as exc:
            raise BundleError(f"{cp} is not a valid cassette JSON file: {exc}") from exc
        meta = cassette_data.get("meta", {})
        arcname = f"{CASSETTE_DIR}/{cp.name}"
        cassette_blobs[arcname] = blob
        cassette_entries.append(
            {
                "file": arcname,
                "sha256": _sha256_bytes(blob),
                "step_count": meta.get("step_count", len(cassette_data.get("records", {}))),
                "recorded_cost_usd": meta.get("total_cost_usd", 0.0),
            }
        )

    from sandcastle import __version__

    manifest = {
        "format": BUNDLE_FORMAT,
        "format_version": BUNDLE_FORMAT_VERSION,
        "name": bundle_name,
        "version": version,
        "description": description or str(data.get("description", "")),
        "author": author,
        "license": license_id,
        "sandcastle_version": __version__,
        "created_at": created_at
        or _git_created_at(workflow_path)
        or datetime.now(timezone.utc).isoformat(),
        "workflow": {"file": WORKFLOW_NAME, "sha256": _sha256_bytes(workflow_yaml.encode())},
        "cassettes": cassette_entries,
        "input_schema": data.get("input_schema"),
        "example_inputs": example_inputs or {},
    }

    # Fixed member timestamps (from created_at) make the archive byte-for-byte
    # reproducible: packing the same payload twice yields the same sha256.
    try:
        ts = datetime.fromisoformat(str(manifest["created_at"]))
        date_time = (ts.year, ts.month, ts.day, ts.hour, ts.minute, ts.second)
    except ValueError:
        date_time = (1980, 1, 1, 0, 0, 0)

    def _zinfo(arcname: str) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo(arcname, date_time=date_time)
        info.compress_type = zipfile.ZIP_DEFLATED
        return info

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_zinfo(MANIFEST_NAME), json.dumps(manifest, indent=2, default=str))
        zf.writestr(_zinfo(WORKFLOW_NAME), workflow_yaml)
        for arcname, blob in cassette_blobs.items():
            zf.writestr(_zinfo(arcname), blob)
    logger.info("Packed bundle %s (%d cassette(s))", output_path, len(cassette_entries))
    return output_path


# ---------------------------------------------------------------------------
# Read (safe)
# ---------------------------------------------------------------------------


def read_bundle(path: str | Path) -> tuple[dict[str, Any], str, dict[str, bytes]]:
    """Safely read a bundle: (manifest, workflow_yaml, {arcname: cassette_bytes}).

    Treats the archive as hostile: caps the archive size, member count, and each
    member's declared decompressed size, and rejects member names that could
    escape an extraction root (zip-slip). Nothing is written to disk.

    Raises:
        BundleError: On any malformed, oversized, or unsafe content.
    """
    path = Path(path)
    if not path.exists():
        raise BundleError(f"bundle not found: {path}")
    if path.stat().st_size > MAX_BUNDLE_SIZE:
        raise BundleError(f"bundle exceeds size limit ({MAX_BUNDLE_SIZE} bytes)")
    if not zipfile.is_zipfile(path):
        raise BundleError(f"{path} is not a valid .sctpl bundle (not a zip archive)")

    members: dict[str, bytes] = {}
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise BundleError(f"bundle has too many entries ({len(infos)} > {MAX_MEMBERS})")
        for info in infos:
            if info.is_dir():
                continue
            if not _safe_member_name(info.filename):
                raise BundleError(f"unsafe member name in bundle: {info.filename!r}")
            if info.file_size > MAX_MEMBER_SIZE:
                raise BundleError(
                    f"bundle member '{info.filename}' exceeds size limit "
                    f"({MAX_MEMBER_SIZE} bytes)"
                )
            with zf.open(info) as fh:
                blob = fh.read(MAX_MEMBER_SIZE + 1)
            if len(blob) > MAX_MEMBER_SIZE:
                raise BundleError(
                    f"bundle member '{info.filename}' decompresses past the size limit"
                )
            members[info.filename] = blob

    if MANIFEST_NAME not in members:
        raise BundleError("bundle is missing manifest.json")
    try:
        manifest = json.loads(members[MANIFEST_NAME])
    except json.JSONDecodeError as exc:
        raise BundleError(f"manifest.json is not valid JSON: {exc}") from exc

    problems = _validate_manifest(manifest)
    if problems:
        raise BundleError("; ".join(problems))

    wf_file = manifest["workflow"]["file"]
    if wf_file not in members:
        raise BundleError(f"bundle is missing workflow file '{wf_file}'")
    workflow_yaml = members[wf_file].decode("utf-8")

    cassettes: dict[str, bytes] = {}
    for entry in manifest["cassettes"]:
        cfile = entry["file"]
        if cfile not in members:
            raise BundleError(f"bundle is missing cassette file '{cfile}'")
        cassettes[cfile] = members[cfile]

    return manifest, workflow_yaml, cassettes


# ---------------------------------------------------------------------------
# Installed-bundle status
# ---------------------------------------------------------------------------


def bundle_for_template(file_name: str) -> Path | None:
    """The sibling .sctpl bundle for an installed community template, if any.

    ``sandcastle template install`` keeps the original bundle next to the
    extracted workflow (``<stem>.yaml`` + ``<stem>.sctpl``), so the proof can be
    replayed at any time. Returns the bundle path or None for plain templates.
    """
    from sandcastle.templates import _TEMPLATES_DIR

    candidate = (_TEMPLATES_DIR / "community" / file_name).with_suffix(BUNDLE_SUFFIX)
    return candidate if candidate.is_file() else None


def bundle_status(path: str | Path) -> dict[str, Any]:
    """Manifest info plus checksum validity for a bundle, without replaying.

    Recomputes the SHA-256 of every payload member and compares it against the
    manifest. This is the cheap half of verification - the replay proof itself
    lives in :func:`verify_bundle`.

    Raises:
        BundleError: When the bundle is malformed or unsafe.
    """
    manifest, workflow_yaml, cassettes = read_bundle(path)

    workflow_entry = {
        "file": manifest["workflow"]["file"],
        "sha256": manifest["workflow"]["sha256"],
        "valid": _sha256_bytes(workflow_yaml.encode()) == manifest["workflow"]["sha256"],
    }
    cassette_entries = [
        {
            "file": entry["file"],
            "sha256": entry["sha256"],
            "valid": _sha256_bytes(cassettes[entry["file"]]) == entry["sha256"],
            "step_count": entry.get("step_count"),
            "recorded_cost_usd": entry.get("recorded_cost_usd"),
        }
        for entry in manifest["cassettes"]
    ]

    return {
        "manifest": {
            key: manifest.get(key)
            for key in (
                "name",
                "version",
                "description",
                "author",
                "license",
                "sandcastle_version",
                "created_at",
            )
        },
        "workflow": workflow_entry,
        "cassettes": cassette_entries,
        "checksums_valid": workflow_entry["valid"]
        and all(c["valid"] for c in cassette_entries),
    }


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


def verify_bundle(path: str | Path) -> BundleVerifyResult:
    """Verify a bundle end to end: manifest, checksums, security scan, replay.

    The replay is the proof-of-execution: each bundled cassette is replayed
    against the bundled workflow in strict mode - every model step must resolve
    from the cassette, nothing may call a provider, and the run must complete at
    $0. Checksums catch a tampered cassette; the strict replay catches a
    tampered workflow (its step cache keys no longer match the recording).
    """
    result = BundleVerifyResult(ok=False)
    try:
        manifest, workflow_yaml, cassettes = read_bundle(path)
    except BundleError as exc:
        result.errors.append(str(exc))
        return result
    result.manifest = manifest

    # 1. Checksums - tampering with any payload file is detectable here.
    wf_expected = manifest["workflow"]["sha256"]
    if _sha256_bytes(workflow_yaml.encode()) != wf_expected:
        result.errors.append("workflow.yaml checksum mismatch - file was modified after packing")
    for entry in manifest["cassettes"]:
        if _sha256_bytes(cassettes[entry["file"]]) != entry["sha256"]:
            result.errors.append(
                f"cassette '{entry['file']}' checksum mismatch - file was modified after packing"
            )
    if result.errors:
        return result

    # 2. Security scan - same scanner that gates community hub installs.
    from sandcastle.engine.hub_scanner import scan_template

    scan = scan_template(workflow_yaml)
    for err in scan.errors:
        step_info = f" (step: {err.step})" if err.step else ""
        result.errors.append(f"security scan: [{err.code}] {err.message}{step_info}")
    if result.errors:
        return result

    # 3. Parse the workflow and refuse step types the cassette cannot cover -
    # those would execute live (code/http/browser) during what must be an
    # offline, no-side-effect verification of untrusted content.
    from sandcastle.engine.dag import build_plan, parse_yaml_string

    try:
        wf = parse_yaml_string(workflow_yaml)
        plan = build_plan(wf)
    except Exception as exc:
        result.errors.append(f"workflow does not parse: {exc}")
        return result

    unsafe = sorted({s.type for s in wf.steps if s.type not in REPLAY_SAFE_STEP_TYPES})
    if unsafe:
        result.errors.append(
            f"workflow contains step type(s) not covered by cassette replay: "
            f"{', '.join(unsafe)} - these would execute live during verify"
        )
        return result

    # 4. Strict replay - the proof itself.
    example_inputs = manifest.get("example_inputs") or {}
    for entry in manifest["cassettes"]:
        result.cassette_results.append(
            _replay_one(wf, plan, cassettes[entry["file"]], entry["file"], example_inputs)
        )

    result.ok = bool(result.cassette_results) and all(
        c.passed for c in result.cassette_results
    )
    return result


def _replay_one(
    wf: Any,
    plan: Any,
    cassette_blob: bytes,
    arcname: str,
    example_inputs: dict[str, Any],
) -> CassetteVerifyResult:
    """Replay a single cassette against the parsed workflow, strictly offline."""
    import uuid

    from sandcastle.engine.executor import execute_workflow

    async def _replay_async() -> Any:
        # Best-effort parent run row (same pattern as `sandcastle run --local`):
        # step/audit persistence works when a DB is around and is silently
        # skipped when it is not. The replay itself never needs the DB.
        run_id = str(uuid.uuid4())
        persisted = False
        try:
            from sandcastle.models.db import Run, RunStatus, async_session, init_db

            await init_db()
            async with async_session() as session:
                session.add(
                    Run(
                        id=uuid.UUID(run_id),
                        workflow_name=wf.name,
                        status=RunStatus.RUNNING,
                        input_data=dict(example_inputs),
                    )
                )
                await session.commit()
            persisted = True
        except Exception:
            persisted = False

        wf_result = await execute_workflow(
            workflow=wf,
            plan=plan,
            input_data=dict(example_inputs),
            run_id=run_id if persisted else None,
            admin_trusted=False,  # bundles are untrusted - never run code steps
            cassette=store,
            cassette_mode="replay",
        )

        if persisted:
            try:
                from sandcastle.models.db import Run, RunStatus, async_session

                status_map = {
                    "completed": RunStatus.COMPLETED,
                    "failed": RunStatus.FAILED,
                    "error": RunStatus.FAILED,
                    "partial": RunStatus.PARTIAL,
                    "cancelled": RunStatus.CANCELLED,
                    "budget_exceeded": RunStatus.BUDGET_EXCEEDED,
                }
                async with async_session() as session:
                    db_run = await session.get(Run, uuid.UUID(run_id))
                    if db_run is not None:
                        db_run.status = status_map.get(
                            str(getattr(wf_result, "status", "")), RunStatus.FAILED
                        )
                        db_run.total_cost_usd = getattr(wf_result, "total_cost_usd", 0.0)
                        from sandcastle.engine.json_utils import json_safe

                        db_run.output_data = json_safe(getattr(wf_result, "outputs", {}))
                        db_run.error = getattr(wf_result, "error", None)
                        await session.commit()
            except Exception:
                pass

        return wf_result

    with tempfile.TemporaryDirectory(prefix="sctpl-verify-") as tmp:
        cassette_path = Path(tmp) / "proof.cassette.json"
        cassette_path.write_bytes(cassette_blob)
        try:
            store = StrictReplayCassette(cassette_path, "replay")
        except Exception as exc:
            return CassetteVerifyResult(file=arcname, passed=False, detail=str(exc))
        if not store.verify():
            return CassetteVerifyResult(
                file=arcname, passed=False,
                detail="cassette signature mismatch - records were modified after recording",
            )
        try:
            wf_result = asyncio.run(_replay_async())
        except Exception as exc:
            return CassetteVerifyResult(
                file=arcname, passed=False, detail=f"replay error: {exc}",
                replay_hits=store.replay_hits, replay_misses=store.replay_misses,
            )

    status = str(getattr(wf_result, "status", "unknown"))
    cost = float(getattr(wf_result, "total_cost_usd", 0.0) or 0.0)
    if status != "completed":
        detail = getattr(wf_result, "error", None) or f"replay finished with status '{status}'"
        return CassetteVerifyResult(
            file=arcname, passed=False, detail=str(detail),
            replay_hits=store.replay_hits, replay_misses=store.replay_misses,
        )
    if store.replay_misses:
        return CassetteVerifyResult(
            file=arcname, passed=False,
            detail=f"{store.replay_misses} step(s) missing from the cassette",
            replay_hits=store.replay_hits, replay_misses=store.replay_misses,
        )
    if cost > 0:
        return CassetteVerifyResult(
            file=arcname, passed=False,
            detail=f"replay incurred cost ${cost:.4f} - it must be free",
            replay_hits=store.replay_hits, replay_misses=store.replay_misses,
        )
    return CassetteVerifyResult(
        file=arcname, passed=True,
        detail=f"{store.replay_hits} step(s) replayed at $0",
        replay_hits=store.replay_hits, replay_misses=store.replay_misses,
    )
