#!/usr/bin/env python3
"""Build the example verified-template bundle committed under examples/templates/.

Records a cassette for the built-in ``summarize`` template fully offline: the
provider layer is replaced with a deterministic stub, so no API key and no network
are needed, and the resulting bundle is byte-for-byte reproducible (fixed inputs,
fixed outputs, fixed created_at). The recorded outputs are clearly labeled demo
text - the point of the artifact is the verifiable mechanics, not the prose:
``sandcastle template verify`` replays it against the bundled workflow at $0 and
any tampering with either file breaks the proof.

Run from the repo root:

    python scripts/build_example_bundle.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

# Isolate from any local Sandcastle database - the build is a pure offline record.
_scratch_db = tempfile.mkstemp(suffix=".sqlite", prefix="sctpl-example-")[1]
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_scratch_db}"
logging.disable(logging.WARNING)

from sandcastle.engine.bundle import create_bundle, verify_bundle  # noqa: E402
from sandcastle.engine.cassette import CassetteStore  # noqa: E402
from sandcastle.engine.dag import build_plan, parse_yaml_string  # noqa: E402
from sandcastle.engine.executor import execute_workflow  # noqa: E402
from sandcastle.engine.sandshore import SandshoreResult, SandshoreRuntime  # noqa: E402

WORKFLOW_PATH = REPO_ROOT / "src" / "sandcastle" / "templates" / "summarize.yaml"
OUTPUT_PATH = REPO_ROOT / "examples" / "templates" / "text-summarizer-1.0.0.sctpl"

EXAMPLE_INPUTS = {
    "text": (
        "Sandcastle is an open-source AI workflow orchestrator in pure Python. "
        "It runs multi-step agent workflows defined in YAML, records every model "
        "step into portable cassettes, and replays them offline at zero cost."
    ),
    "format": "executive",
    "max_length": "200 words",
    "audience": "general",
}


def _demo_runtime() -> MagicMock:
    """A deterministic stand-in for the provider runtime (offline, $0)."""
    sb = MagicMock(spec=SandshoreRuntime)

    async def _query(request: dict):
        # Deterministic per-prompt output so re-running the script reproduces
        # the identical cassette (and therefore the identical bundle payload).
        digest = hashlib.sha256(str(request.get("prompt", "")).encode()).hexdigest()[:12]
        return SandshoreResult(
            text=(
                "[demo output - recorded offline by scripts/build_example_bundle.py] "
                f"Deterministic step output {digest}."
            ),
            structured_output=None,
            total_cost_usd=0.0,
            input_tokens=0,
            output_tokens=0,
        )

    sb.query = _query
    return sb


def main() -> None:
    wf = parse_yaml_string(WORKFLOW_PATH.read_text())
    plan = build_plan(wf)

    with tempfile.TemporaryDirectory(prefix="sctpl-example-") as tmp:
        cassette_path = Path(tmp) / "proof.cassette.json"
        cassette = CassetteStore(cassette_path, "record")
        with patch(
            "sandcastle.engine.executor.get_sandshore_runtime",
            return_value=_demo_runtime(),
        ):
            result = asyncio.run(
                execute_workflow(
                    workflow=wf,
                    plan=plan,
                    input_data=dict(EXAMPLE_INPUTS),
                    admin_trusted=False,
                    cassette=cassette,
                    cassette_mode="record",
                )
            )
        if str(result.status) != "completed":
            raise SystemExit(f"record run failed: {result.status} - {result.error}")
        cassette.save()

        create_bundle(
            WORKFLOW_PATH,
            [cassette_path],
            OUTPUT_PATH,
            version="1.0.0",
            author="gizmax",
            license_id="MIT",
            example_inputs=EXAMPLE_INPUTS,
        )

    verdict = verify_bundle(OUTPUT_PATH)
    if not verdict.ok:
        raise SystemExit(f"freshly built bundle failed verification: {verdict.errors}")

    sha = hashlib.sha256(OUTPUT_PATH.read_bytes()).hexdigest()
    print(f"Built {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    print(f"  sha256: {sha}")
    print(f"  cassettes: {len(verdict.cassette_results)} - all PASS")
    print(
        json.dumps(
            {"name": verdict.manifest["name"], "version": verdict.manifest["version"]},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
