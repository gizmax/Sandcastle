"""Tests for the Computer Use integration helper."""

from __future__ import annotations

import pytest

from sandcastle.engine.computer_use import (
    BETA_HEADER_2025_01_24,
    BETA_HEADER_2025_11_24,
    SAFETY_CHECKLIST,
    ComputerUseConfig,
    build_beta_header,
    build_tool_definitions,
    should_pause_for_approval,
    validate_config,
)


# ---------------------------------------------------------------------------
# validate_config
# ---------------------------------------------------------------------------


def test_validate_config_rejects_display_width_too_large() -> None:
    config = ComputerUseConfig(display_width_px=2600)
    issues = validate_config(config)
    assert any("exceeds maximum" in issue for issue in issues)


def test_validate_config_rejects_display_width_too_small() -> None:
    config = ComputerUseConfig(display_width_px=320)
    issues = validate_config(config)
    assert any("below minimum" in issue for issue in issues)


def test_validate_config_rejects_empty_tools() -> None:
    config = ComputerUseConfig(tools=[])
    issues = validate_config(config)
    assert any("tools list is empty" in issue for issue in issues)


def test_validate_config_warns_on_empty_approval_list() -> None:
    config = ComputerUseConfig(require_human_approval_for=[])
    issues = validate_config(config)
    warn = [issue for issue in issues if issue.startswith("WARN:")]
    assert warn, "expected a WARN: entry for empty approval list"
    assert "require_human_approval_for" in warn[0]


def test_validate_config_clean_default() -> None:
    config = ComputerUseConfig()
    assert validate_config(config) == []


# ---------------------------------------------------------------------------
# build_tool_definitions
# ---------------------------------------------------------------------------


def test_build_tool_definitions_bash_shape() -> None:
    config = ComputerUseConfig(tools=["bash"])
    defs = build_tool_definitions(config)
    assert defs == [{"type": "bash_20250124", "name": "bash"}]


def test_build_tool_definitions_text_editor_shape() -> None:
    config = ComputerUseConfig(tools=["text_editor"])
    defs = build_tool_definitions(config)
    assert defs == [
        {"type": "text_editor_20250124", "name": "str_replace_editor"}
    ]


def test_build_tool_definitions_computer_shape() -> None:
    config = ComputerUseConfig(
        tools=["computer"], display_width_px=1440, display_height_px=900
    )
    defs = build_tool_definitions(config)
    assert defs == [
        {
            "type": "computer_20251124",
            "name": "computer",
            "display_width_px": 1440,
            "display_height_px": 900,
        }
    ]


# ---------------------------------------------------------------------------
# build_beta_header
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model",
    ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"],
)
def test_build_beta_header_new_models(model: str) -> None:
    assert build_beta_header(model) == BETA_HEADER_2025_11_24


def test_build_beta_header_older_sonnet() -> None:
    assert build_beta_header("claude-sonnet-4-5") == BETA_HEADER_2025_01_24


# ---------------------------------------------------------------------------
# should_pause_for_approval
# ---------------------------------------------------------------------------


def test_should_pause_for_approval_matches_action_name() -> None:
    config = ComputerUseConfig()  # default includes "mouse_click"
    event = {"name": "computer", "input": {"action": "mouse_click", "x": 10, "y": 20}}
    assert should_pause_for_approval(event, config) is True


def test_should_pause_for_approval_skips_unlisted_action() -> None:
    config = ComputerUseConfig(require_human_approval_for=["submit_form"])
    event = {"name": "computer", "input": {"action": "screenshot"}}
    assert should_pause_for_approval(event, config) is False


# ---------------------------------------------------------------------------
# SAFETY_CHECKLIST
# ---------------------------------------------------------------------------


def test_safety_checklist_has_enough_items() -> None:
    assert len(SAFETY_CHECKLIST) >= 6
    for item in SAFETY_CHECKLIST:
        assert isinstance(item, str)
        assert item.strip(), "safety checklist items must be non-empty"
