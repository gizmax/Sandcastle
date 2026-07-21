#!/usr/bin/env python3
"""Maintain the canonical ``hub/registry.json``.

``hub/registry.json`` is the runtime source of truth.  ``site/hub/registry.json``
is generated from it by ``update_hub_registry.py`` and must never be edited as an
independent catalog.

Use ``--migrate-site-builtins`` only to import already-published built-in entries
from the legacy site registry.  Community entries are built from the checked-in
``hub/community`` workflows and their curated ``seed.json`` manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "hub" / "registry.json"
LEGACY_SITE_REGISTRY = ROOT / "site" / "hub" / "registry.json"
COMMUNITY_ROOT = ROOT / "hub" / "community"
SEED_MANIFEST = COMMUNITY_ROOT / "seed.json"
RAW_PREFIX = "https://raw.githubusercontent.com/gizmax/Sandcastle/main/"

# These deliberately conservative estimates are derived from YAML step count,
# rather than telemetry. They make catalogue cards comparable until runtime
# benchmarks are available.
COST_PER_STEP_USD = 0.0125
SECONDS_PER_STEP = 4
MAX_DESCRIPTION_LENGTH = 2000

CATEGORY_LABELS = {
    "sales_crm": "Sales & CRM",
    "marketing": "Marketing",
    "support": "Support",
    "engineering": "Engineering",
    "hr_legal": "HR & Legal",
    "general_ai": "General AI",
    "devops": "DevOps & SRE",
    "data": "Data",
    "creative": "Creative",
    "compliance_grc": "Compliance & GRC",
    "healthcare": "Healthcare & Life Sciences",
    "fintech_banking": "Fintech & Banking",
    "llmops_ai_eng": "LLMOps & AI Engineering",
    "personal": "Personal & Life Admin",
    "research_intel": "Research & Intelligence",
    "finance_ops": "Finance & FP&A Operations",
    "automation_rpa": "Automation & RPA",
}


def _source_path(entry: dict) -> Path | None:
    """Return a repository-confined path for a raw GitHub download URL."""
    url = entry.get("download_url", "")
    if not url.startswith(RAW_PREFIX):
        return None
    candidate = (ROOT / url.removeprefix(RAW_PREFIX)).resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _timestamp(path: Path) -> str:
    return (
        datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _workflow_fields(content: str, source_path: Path) -> dict:
    """Extract the catalogue fields that are directly represented in YAML."""
    try:
        workflow = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid workflow YAML: {source_path.relative_to(ROOT)}") from exc
    if not isinstance(workflow, dict):
        raise ValueError(f"Workflow YAML must be a mapping: {source_path.relative_to(ROOT)}")

    steps = workflow.get("steps", [])
    if not isinstance(steps, list):
        raise ValueError(f"Workflow steps must be a list: {source_path.relative_to(ROOT)}")
    models = {workflow.get("default_model", "sonnet")}
    for step in steps:
        if isinstance(step, dict) and step.get("model"):
            models.add(step["model"])

    description = str(workflow.get("description", ""))
    if len(description) > MAX_DESCRIPTION_LENGTH:
        # The full description remains in the source workflow. The registry is
        # a card catalogue and its JSON schema deliberately bounds summaries.
        description = description[: MAX_DESCRIPTION_LENGTH - 3].rsplit(" ", 1)[0] + "..."

    return {
        "name": workflow.get("name", ""),
        "description": description,
        "version": workflow.get("version", "1.0.0"),
        "input_schema": workflow.get("input_schema"),
        "models_used": sorted(models),
        "step_count": len(steps),
    }


def _normalise_entry(entry: dict, source_path: Path, *, preserve_curated_metadata: bool) -> dict:
    """Refresh content-derived fields and retain only manifest-backed telemetry."""
    normalised = dict(entry)
    content = source_path.read_text(encoding="utf-8")
    timestamp = _timestamp(source_path)
    normalised.update(_workflow_fields(content, source_path))
    normalised["sha256"] = hashlib.sha256(content.encode()).hexdigest()
    if not preserve_curated_metadata:
        normalised["created_at"] = timestamp
        normalised["updated_at"] = timestamp
        # Built-in workflows do not have a telemetry source. Zero is an honest
        # placeholder and satisfies the catalogue's required counter contract.
        normalised["downloads"] = 0
    normalised["remix_count"] = 0
    step_count = max(1, normalised["step_count"])
    normalised["estimated_cost_per_run"] = round(step_count * COST_PER_STEP_USD, 4)
    normalised["avg_execution_time"] = f"~{step_count * SECONDS_PER_STEP}s"
    return normalised


def _community_workflow_paths() -> dict[str, Path]:
    """Map each checked-in community workflow to its canonical author/slug."""
    if not COMMUNITY_ROOT.exists():
        return {}
    paths: dict[str, Path] = {}
    for path in sorted(COMMUNITY_ROOT.rglob("*.yaml")):
        relative = path.relative_to(COMMUNITY_ROOT).with_suffix("")
        if len(relative.parts) != 2:
            raise ValueError(
                "Community workflows must use hub/community/<author>/<slug>.yaml: "
                f"{path.relative_to(ROOT)}"
            )
        slug = relative.as_posix()
        if slug in paths:
            raise ValueError(f"Duplicate community workflow slug: {slug}")
        paths[slug] = path
    return paths


def _load_seed_manifest() -> dict[str, dict]:
    """Load the human-curated metadata which workflow source cannot express."""
    if not SEED_MANIFEST.is_file():
        raise ValueError(f"Missing community seed manifest: {SEED_MANIFEST.relative_to(ROOT)}")
    try:
        manifest = json.loads(SEED_MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid community seed manifest: {SEED_MANIFEST.relative_to(ROOT)}"
        ) from exc
    if not isinstance(manifest, dict) or not all(
        isinstance(value, dict) for value in manifest.values()
    ):
        raise ValueError("Community seed manifest must be an object keyed by slug")
    return manifest


def _validate_community_source_inputs(
    workflows: dict[str, Path], manifest: dict[str, dict]
) -> None:
    """Reject a stale manifest or a workflow that cannot represent its slug."""
    errors: list[str] = []
    workflow_slugs = set(workflows)
    manifest_slugs = set(manifest)
    missing_files = sorted(manifest_slugs - workflow_slugs)
    missing_metadata = sorted(workflow_slugs - manifest_slugs)
    if missing_files:
        errors.append(f"manifest entries without workflow files: {', '.join(missing_files)}")
    if missing_metadata:
        errors.append(f"workflow files without manifest entries: {', '.join(missing_metadata)}")

    required_fields = {
        "author",
        "author_url",
        "category",
        "tags",
        "license",
        "rating",
        "review_count",
        "created_at",
        "updated_at",
        "downloads",
        "forked_from",
    }
    for slug, metadata in sorted(manifest.items()):
        author = slug.split("/", 1)[0]
        missing = sorted(required_fields - set(metadata))
        if missing:
            errors.append(f"{slug} missing manifest fields: {', '.join(missing)}")
        if metadata.get("author") != author:
            errors.append(f"{slug} author must match its path author '{author}'")
    if errors:
        raise ValueError("Invalid community inputs: " + "; ".join(errors))


def _community_entries() -> list[dict]:
    """Build community registry entries from real workflow files and curated data."""
    workflows = _community_workflow_paths()
    manifest = _load_seed_manifest()
    _validate_community_source_inputs(workflows, manifest)

    entries = []
    for slug, path in sorted(workflows.items()):
        entry = dict(manifest[slug])
        entry.update(
            {
                "slug": slug,
                "download_url": RAW_PREFIX + path.relative_to(ROOT).as_posix(),
                "source": "community",
            }
        )
        entries.append(_normalise_entry(entry, path, preserve_curated_metadata=True))
    return entries


def validate_community_inputs(registry: dict) -> None:
    """Validate the generated community catalogue and its remix references.

    This is intentionally public so the site-mirror updater can fail its
    ``--check`` mode before declaring an otherwise byte-identical mirror current.
    """
    workflows = _community_workflow_paths()
    manifest = _load_seed_manifest()
    _validate_community_source_inputs(workflows, manifest)

    entries = registry.get("templates", [])
    if not isinstance(entries, list):
        raise ValueError("Registry templates must be a list")
    community = [entry for entry in entries if entry.get("source") == "community"]
    expected_slugs = set(workflows)
    actual_slugs = {entry.get("slug") for entry in community}
    if actual_slugs != expected_slugs:
        raise ValueError("Registry community entries do not match the seed manifest workflows")
    for entry in community:
        slug = entry["slug"]
        expected_url = RAW_PREFIX + workflows[slug].relative_to(ROOT).as_posix()
        if entry.get("download_url") != expected_url:
            raise ValueError(f"Community download URL is stale for {slug}")

    all_slugs = {entry.get("slug") for entry in entries}
    dangling = sorted(
        entry["slug"]
        for entry in entries
        if entry.get("forked_from") and entry["forked_from"] not in all_slugs
    )
    if dangling:
        raise ValueError("Registry has dangling forked_from entries: " + ", ".join(dangling))


def _migrate_site_builtins(registry: dict) -> int:
    """Import valid built-ins from the pre-canonical site registry once."""
    legacy = json.loads(LEGACY_SITE_REGISTRY.read_text(encoding="utf-8"))
    have = {entry["slug"] for entry in registry["templates"]}
    added = 0
    for entry in legacy.get("templates", []):
        if entry.get("source") != "built-in" or entry.get("slug") in have:
            continue
        if _source_path(entry) is None:
            continue
        registry["templates"].append(entry)
        have.add(entry["slug"])
        added += 1
    return added


def _build_registry(registry: dict, migrate_site_builtins: bool) -> tuple[dict, int, int]:
    registry = dict(registry)
    registry["templates"] = [
        entry for entry in registry.get("templates", []) if entry.get("source") != "community"
    ]
    migrated = _migrate_site_builtins(registry) if migrate_site_builtins else 0

    valid_entries: list[dict] = []
    dropped = 0
    timestamps: list[str] = []
    for entry in registry["templates"]:
        source_path = _source_path(entry)
        if source_path is None:
            dropped += 1
            continue
        valid_entries.append(_normalise_entry(entry, source_path, preserve_curated_metadata=False))
        timestamps.append(_timestamp(source_path))
    valid_entries.extend(_community_entries())
    timestamps.extend(_timestamp(path) for path in _community_workflow_paths().values())
    registry["templates"] = valid_entries

    remix_counts = Counter(
        entry["forked_from"] for entry in valid_entries if entry.get("forked_from")
    )
    for entry in valid_entries:
        entry["remix_count"] = remix_counts[entry["slug"]]

    counts = Counter(entry["category"] for entry in valid_entries)
    registry["categories"] = [
        {
            "id": category,
            "name": CATEGORY_LABELS.get(category, category.replace("_", " ").title()),
            "count": count,
        }
        for category, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]

    slugs = {entry["slug"] for entry in valid_entries}
    collections = []
    for collection in registry.get("collections", []):
        updated = dict(collection)
        updated["template_slugs"] = [slug for slug in updated["template_slugs"] if slug in slugs]
        # Collection download counts are telemetry too; no telemetry source is configured.
        updated["downloads"] = 0
        collections.append(updated)
    registry["collections"] = collections

    generated_at = max(timestamps, default="1970-01-01T00:00:00Z")
    stats = dict(registry.get("stats", {}))
    stats["total_templates"] = len(valid_entries)
    stats["total_authors"] = len({entry["author"] for entry in valid_entries})
    stats["total_downloads"] = sum(entry["downloads"] for entry in valid_entries)
    stats["last_updated"] = generated_at
    registry["stats"] = stats
    registry["generated_at"] = generated_at
    validate_community_inputs(registry)
    return registry, migrated, dropped


def _render(registry: dict) -> str:
    return json.dumps(registry, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--migrate-site-builtins",
        action="store_true",
        help="import valid built-in entries from the pre-canonical site registry",
    )
    parser.add_argument(
        "--check", action="store_true", help="fail if regeneration changes the file"
    )
    args = parser.parse_args()

    current = json.loads(REGISTRY.read_text(encoding="utf-8"))
    updated, migrated, dropped = _build_registry(current, args.migrate_site_builtins)
    rendered = _render(updated)
    current_text = REGISTRY.read_text(encoding="utf-8")
    if args.check:
        if rendered != current_text:
            print("hub/registry.json is stale; run python scripts/build_registry.py")
            return 1
        print(f"hub registry is current ({len(updated['templates'])} templates)")
        return 0

    REGISTRY.write_text(rendered, encoding="utf-8")
    print(
        f"hub registry updated: {len(updated['templates'])} templates "
        f"({migrated} site built-ins migrated, {dropped} invalid entries dropped)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
