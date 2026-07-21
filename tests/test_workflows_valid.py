"""Validation guard for every workflow shipped with Sandcastle."""

from __future__ import annotations

from pathlib import Path

import pytest

from sandcastle.engine.dag import build_plan, parse_yaml_string, validate

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_FILES = sorted(
    [
        *(REPO_ROOT / "workflows").rglob("*.yaml"),
        *(REPO_ROOT / "hub" / "community").rglob("*.yaml"),
    ]
)


@pytest.mark.parametrize(
    "workflow_path",
    WORKFLOW_FILES,
    ids=lambda path: str(path.relative_to(REPO_ROOT)),
)
def test_shipped_workflow_parses_validates_and_plans(workflow_path: Path) -> None:
    """Every shipped workflow must be parseable, valid, and schedulable."""
    workflow = parse_yaml_string(workflow_path.read_text())
    assert validate(workflow) == [], workflow_path.relative_to(REPO_ROOT)

    plan = build_plan(workflow)
    planned_steps = [step_id for stage in plan.stages for step_id in stage]
    assert set(planned_steps) == {step.id for step in workflow.steps}
