"""Guards for the llms.txt / llms-full.txt discovery files.

llms.txt is a machine-readable entry point for code-generating assistants. The
worked example in llms-full.txt teaches the YAML format, so it must stay a valid,
parseable, model-independent Sandcastle workflow; this test fails if the docs rot.
"""

from __future__ import annotations

import re
from pathlib import Path

from sandcastle.engine.dag import build_plan, parse_yaml_string, validate

ROOT = Path(__file__).resolve().parent.parent


def test_llms_txt_files_exist() -> None:
    for name in ("llms.txt", "llms-full.txt"):
        assert (ROOT / name).is_file(), f"{name} missing at repo root"


def test_llms_full_example_is_a_valid_workflow() -> None:
    """The minimal example in llms-full.txt must parse, validate clean, and have a
    build plan that covers every step - otherwise the reference teaches broken YAML."""
    text = (ROOT / "llms-full.txt").read_text()
    blocks = re.findall(r"```yaml\n(.*?)```", text, re.S)
    assert blocks, "llms-full.txt has no ```yaml example block"
    example = blocks[-1]
    wf = parse_yaml_string(example)
    assert wf.name and len(wf.steps) > 0
    errors = validate(wf)
    assert errors == [], f"llms-full.txt example does not validate: {errors}"
    plan = build_plan(wf)
    planned = [sid for stage in plan.stages for sid in stage]
    assert set(planned) == {s.id for s in wf.steps}
    # It should also demonstrate the thesis: a cross-provider race.
    races = [s for s in wf.steps if s.type == "race" and s.race_config]
    by_id = {s.id: s for s in wf.steps}
    providers = {
        (by_id[sid].model or "").split("/")[0]
        for race in races
        for branch in race.race_config.branches
        for sid in branch
        if sid in by_id and by_id[sid].model
    }
    assert len({p for p in providers if p}) >= 2, (
        "llms-full.txt example should show a cross-provider race"
    )
