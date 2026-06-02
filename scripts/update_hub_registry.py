#!/usr/bin/env python3
"""Keep site/hub/registry.json in sync with the built-in template catalog.

The web Template Hub registry is hand-maintained and drifts: its categories[] bar and
counts fall out of step with the templates that actually exist. This script makes the
category bar and stats DERIVED, not hand-typed, so they never drift again, and ingests
built-in templates from new categories as gizmax/ entries so a new category shows real
templates in the public gallery.

Usage:
    python scripts/update_hub_registry.py            # refresh categories[] + stats only
    python scripts/update_hub_registry.py --ingest   # also add missing built-ins for NEW_CATEGORIES
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "src" / "sandcastle" / "templates"
REGISTRY = ROOT / "site" / "hub" / "registry.json"
RAW_BASE = "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates"

# Categories that should be ingested into the public hub as gizmax/ built-ins so the new
# category chips show real templates. Extend this as new categories are added.
NEW_CATEGORIES = {
    "compliance_grc", "healthcare", "fintech_banking", "llmops_ai_eng", "personal",
    "research_intel", "finance_ops", "automation_rpa",
}

# Human labels for category ids (derived bar uses these; falls back to a title-cased slug).
CATEGORY_LABELS = {
    "sales_crm": "Sales & CRM", "marketing": "Marketing", "support": "Support",
    "engineering": "Engineering", "hr_legal": "HR & Legal", "general_ai": "General AI",
    "devops": "DevOps & SRE", "data": "Data", "creative": "Creative",
    "compliance_grc": "Compliance & GRC", "healthcare": "Healthcare & Life Sciences",
    "fintech_banking": "Fintech & Banking", "llmops_ai_eng": "LLMOps & AI Engineering",
    "personal": "Personal & Life Admin", "research_intel": "Research & Intelligence",
    "finance_ops": "Finance & FP&A Operations", "automation_rpa": "Automation & RPA",
}


def _parse_header(text: str) -> dict:
    meta: dict = {}
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("#"):
            break
        s = s.lstrip("#").strip()
        if ":" not in s:
            continue
        k, _, v = s.partition(":")
        k, v = k.strip(), v.strip()
        if k == "tags":
            m = re.match(r"\[(.+)]", v)
            meta[k] = [t.strip() for t in m.group(1).split(",")] if m else [v]
        else:
            meta[k] = v
    return meta


def _builtin_entry(path: Path) -> dict:
    text = path.read_text()
    meta = _parse_header(text)
    try:
        data = yaml.safe_load(text) or {}
    except Exception:
        data = {}
    steps = data.get("steps", []) if isinstance(data, dict) else []
    models, tools = set(), set()
    default_model = data.get("default_model") if isinstance(data, dict) else None
    if default_model:
        models.add(default_model)
    for st in steps:
        if not isinstance(st, dict):
            continue
        if st.get("model"):
            models.add(st["model"])
        tc = st.get("tool_config") or {}
        if tc.get("tool"):
            tools.add(tc["tool"])
    slug = f"gizmax/{path.stem.replace('_', '-')}"
    return {
        "slug": slug,
        "name": str(meta.get("name") or data.get("name") or path.stem),
        "description": str(meta.get("description") or data.get("description") or ""),
        "author": "gizmax",
        "author_url": "https://github.com/gizmax",
        "version": "1.0.0",
        "category": str(meta.get("category") or data.get("category") or "general_ai"),
        "tags": list(meta.get("tags") or data.get("tags") or []),
        "models_used": sorted(models) or ["sonnet"],
        "tools_used": sorted(tools),
        "step_count": len(steps),
        "download_url": f"{RAW_BASE}/{path.name}",
        "source": "built-in",
        "created_at": "2026-06-02T00:00:00Z",
        "updated_at": "2026-06-02T00:00:00Z",
        "estimated_cost_per_run": round(0.02 * max(1, len(steps)), 2),
        "avg_execution_time": 30 * max(1, len(steps)),
        "downloads": 0,
        "remix_count": 0,
        "forked_from": None,
        "license": "BSL-1.1",
        "rating": 0.0,
        "review_count": 0,
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "input_schema": data.get("input_schema") if isinstance(data, dict) else None,
    }


def main() -> None:
    ingest = "--ingest" in sys.argv
    reg = json.loads(REGISTRY.read_text())
    templates = reg.get("templates", [])
    have = {t.get("slug") for t in templates}

    added = 0
    if ingest:
        for path in sorted(TEMPLATES_DIR.glob("*.yaml")):
            meta = _parse_header(path.read_text())
            cat = meta.get("category") or "general_ai"
            slug = f"gizmax/{path.stem.replace('_', '-')}"
            if cat in NEW_CATEGORIES and slug not in have:
                templates.append(_builtin_entry(path))
                have.add(slug)
                added += 1

    # Derive categories[] from the actual templates - never hand-typed, never drifts.
    counts: dict[str, int] = {}
    for t in templates:
        c = t.get("category") or "general_ai"
        counts[c] = counts.get(c, 0) + 1
    reg["categories"] = [
        {"id": cid, "name": CATEGORY_LABELS.get(cid, cid.replace("_", " ").title()), "count": n}
        for cid, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    stats = reg.setdefault("stats", {})
    stats["total_templates"] = len(templates)
    stats["total_authors"] = len({t.get("author") for t in templates})
    stats["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")
    reg["generated_at"] = stats["last_updated"]

    REGISTRY.write_text(json.dumps(reg, indent=1))
    print(
        f"registry updated: {len(templates)} templates ({added} new built-ins ingested), "
        f"{len(reg['categories'])} categories: "
        + ", ".join(f"{c['id']}({c['count']})" for c in reg["categories"])
    )


if __name__ == "__main__":
    main()
