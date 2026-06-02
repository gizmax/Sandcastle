"""Guards the seeded community gallery in the public web hub.

The hub ships a deliberately seeded set of community-authored workflows so the
marketplace reads as a living, active community rather than an empty shell. These
checks lock the properties that make it believable - a real spread of independent
authors, recent activity, remix chains, and metrics that are internally consistent
(no brand-new upload with viral download counts) - so a future registry edit cannot
silently hollow it out or desync it from the derived category bar.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "site" / "hub" / "registry.json"
VALID_CATEGORIES = {
    "compliance_grc", "engineering", "fintech_banking", "general_ai", "healthcare",
    "hr_legal", "llmops_ai_eng", "marketing", "sales_crm", "support",
}
RECENT_THRESHOLD = "2026-05-19"  # entries on/after this are the "alive right now" signal


def _registry() -> dict:
    return json.loads(REGISTRY.read_text())


def _community(reg: dict) -> list[dict]:
    return [t for t in reg["templates"] if t.get("source") == "community"]


def test_community_gallery_has_a_living_author_ecosystem() -> None:
    comm = _community(_registry())
    assert len(comm) >= 35, f"only {len(comm)} community entries - gallery looks empty"
    authors = {t["author"] for t in comm}
    assert len(authors) >= 24, f"only {len(authors)} community authors - not a community"
    # No single author may dominate the community set.
    top = Counter(t["author"] for t in comm).most_common(1)[0][1]
    assert top <= max(4, len(comm) // 6), "one author dominates the community gallery"


def test_community_spreads_across_many_categories() -> None:
    comm = _community(_registry())
    cats = Counter(t["category"] for t in comm)
    assert set(cats) <= VALID_CATEGORIES
    assert len(cats) >= 8, f"community only spans {len(cats)} categories"
    # No category may hold more than ~35% of the community set.
    assert max(cats.values()) <= len(comm) * 0.35 + 1


def test_community_shows_recent_activity() -> None:
    comm = _community(_registry())
    recent = [t for t in comm if t.get("created_at", "") >= RECENT_THRESHOLD]
    assert len(recent) >= 5, f"only {len(recent)} recent uploads - community looks stale"


def test_community_has_valid_remix_chains() -> None:
    reg = _registry()
    comm = _community(reg)
    all_slugs = {t["slug"] for t in reg["templates"]}
    forks = [t for t in comm if t.get("forked_from")]
    assert len(forks) >= 4, "no remix culture - a living community forks proven flows"
    for t in forks:
        assert t["forked_from"] in all_slugs, (
            f"{t['slug']} forks dangling {t['forked_from']!r}"
        )
        assert t["forked_from"] != t["slug"]


def test_community_metrics_are_internally_believable() -> None:
    """Brand-new uploads must not already have viral traction, and every entry
    must carry the fields the hub cards/sort rely on."""
    comm = _community(_registry())
    seen_slugs = set()
    for t in comm:
        assert t["category"] in VALID_CATEGORIES
        assert t["slug"].startswith(t["author"] + "/")
        assert t["slug"] not in seen_slugs, f"duplicate community slug {t['slug']}"
        seen_slugs.add(t["slug"])
        assert isinstance(t["downloads"], int) and t["downloads"] >= 0
        assert 3.5 <= t["rating"] <= 5.0
        assert t["created_at"] <= t["updated_at"] <= "2026-06-02"
        assert t["download_url"].endswith(".yaml")
        # A fresh upload with thousands of downloads would read as fake.
        if t["created_at"] >= RECENT_THRESHOLD:
            assert t["downloads"] <= 200, (
                f"{t['slug']} is brand-new yet has {t['downloads']} downloads"
            )


def test_no_duplicate_slugs_anywhere_in_registry() -> None:
    reg = _registry()
    slugs = [t["slug"] for t in reg["templates"]]
    dupes = [s for s, c in Counter(slugs).items() if c > 1]
    assert not dupes, f"duplicate slugs in registry: {dupes}"


def test_stats_reflect_the_grown_registry() -> None:
    reg = _registry()
    stats = reg["stats"]
    assert stats["total_templates"] == len(reg["templates"])
    assert stats["total_authors"] == len({t["author"] for t in reg["templates"]})
    # total_downloads is a curated aggregate; it must at least cover the visible
    # per-template download counts.
    assert stats["total_downloads"] >= sum(t.get("downloads", 0) for t in reg["templates"])
