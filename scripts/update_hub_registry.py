#!/usr/bin/env python3
"""Generate the public site registry from the canonical hub registry.

Usage:
    python scripts/update_hub_registry.py
    python scripts/update_hub_registry.py --check
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_registry import validate_community_inputs

ROOT = Path(__file__).resolve().parent.parent
CANONICAL_REGISTRY = ROOT / "hub" / "registry.json"
SITE_REGISTRY = ROOT / "site" / "hub" / "registry.json"


def _render_canonical_registry() -> str:
    """Return the canonical registry in the site artifact's stable JSON format."""
    registry = json.loads(CANONICAL_REGISTRY.read_text(encoding="utf-8"))
    return json.dumps(registry, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the site registry is stale")
    args = parser.parse_args()

    registry = json.loads(CANONICAL_REGISTRY.read_text(encoding="utf-8"))
    try:
        validate_community_inputs(registry)
    except ValueError as exc:
        print(f"community hub inputs are invalid: {exc}")
        return 1

    rendered = json.dumps(registry, indent=2) + "\n"
    current = SITE_REGISTRY.read_text(encoding="utf-8") if SITE_REGISTRY.exists() else ""
    if args.check:
        if rendered != current:
            print("site/hub/registry.json is stale; run python scripts/update_hub_registry.py")
            return 1
        print("site hub registry is current")
        return 0

    SITE_REGISTRY.write_text(rendered, encoding="utf-8")
    registry = json.loads(rendered)
    print(
        f"site hub registry generated from canonical registry ({len(registry['templates'])} templates)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
