"""Computer Use integration helper for Sandcastle.

Anthropic's Computer Use beta lets the model drive a sandboxed VM via three
tools: ``bash``, ``text_editor`` and ``computer`` (mouse, keyboard, screenshot).

This module is intentionally self-contained: it only builds tool definitions,
beta headers, and validates configuration. It does not perform any network
calls to the Anthropic API. Wiring into the executor happens in a separate
phase.

Anthropic mandates three operational requirements when using Computer Use:

1. Run the target environment inside a sandboxed VM or container.
2. Keep the prompt-injection classifier enabled (default on).
3. Require human-in-the-loop confirmation for consequential actions.

The :data:`SAFETY_CHECKLIST` and :func:`should_pause_for_approval` helpers
exist to make those requirements easy to satisfy from workflow code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ToolName = Literal["bash", "text_editor", "computer"]


# Display bounds enforced by Anthropic for the computer tool.
MIN_DISPLAY_WIDTH_PX = 640
MAX_DISPLAY_WIDTH_PX = 2576  # Opus 4.7 upper bound.

# Beta header strings (kept as module constants for easy reuse in tests).
BETA_HEADER_2025_11_24 = "computer-use-2025-11-24"
BETA_HEADER_2025_01_24 = "computer-use-2025-01-24"

# Models that use the newer 2025-11-24 beta header.
_NEW_BETA_MODELS = {
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
}

# Older Sonnet / Opus generations still on the 2025-01-24 header.
_OLD_BETA_MODELS = {
    "claude-sonnet-4-5",
    "claude-opus-4-5",
}


SAFETY_CHECKLIST: list[str] = [
    "Run the target environment inside a sandboxed VM or ephemeral container - never on a host with user data.",
    "Keep Anthropic's prompt-injection classifier enabled (it is on by default; do not disable it).",
    "Maintain an allowlist of domains the agent is permitted to navigate to and block everything else at the network layer.",
    "Require explicit human approval for consequential actions (purchases, form submissions, destructive shell commands, sending email).",
    "Strip secrets, cookies, and tokens from any screenshots or transcripts before logging or persisting them.",
    "Set a hard wall-clock timeout and a maximum tool-call budget per session so a runaway loop cannot drain resources.",
    "Audit every bash and text_editor call: log the command, the working directory, and the exit code to the Sandcastle audit trail.",
    "Tear the sandbox down at the end of each session - do not reuse VMs across users or workflows.",
]


class ComputerUseError(Exception):
    """Raised for Computer Use configuration or validation failures."""


@dataclass
class ComputerUseConfig:
    """Configuration for a Computer Use enabled agent session."""

    display_width_px: int = 1280
    display_height_px: int = 800
    tools: list[ToolName] = field(
        default_factory=lambda: ["bash", "text_editor", "computer"]
    )
    model: str = "claude-sonnet-4-6"
    require_human_approval_for: list[str] = field(
        default_factory=lambda: ["mouse_click", "key_combo", "submit_form"]
    )
    sandbox_url: str | None = None


def validate_config(config: ComputerUseConfig) -> list[str]:
    """Validate a :class:`ComputerUseConfig`.

    Returns a list of issue strings. Hard errors are returned as plain
    sentences, soft warnings are prefixed with ``WARN:`` so callers can
    decide whether to fail or merely log.
    """

    issues: list[str] = []

    if config.display_width_px > MAX_DISPLAY_WIDTH_PX:
        issues.append(
            f"display_width_px {config.display_width_px} exceeds maximum "
            f"{MAX_DISPLAY_WIDTH_PX}px supported by Opus 4.7."
        )
    if config.display_width_px < MIN_DISPLAY_WIDTH_PX:
        issues.append(
            f"display_width_px {config.display_width_px} is below minimum "
            f"{MIN_DISPLAY_WIDTH_PX}px."
        )

    if not config.tools:
        issues.append("tools list is empty; at least one tool must be enabled.")

    if not config.require_human_approval_for:
        issues.append(
            "WARN: require_human_approval_for is empty; the agent will not "
            "pause for any actions. Anthropic recommends human-in-loop "
            "approval for consequential actions."
        )

    return issues


def build_tool_definitions(config: ComputerUseConfig) -> list[dict]:
    """Return Anthropic-format tool definitions for the enabled tools."""

    definitions: list[dict] = []

    for tool in config.tools:
        if tool == "bash":
            definitions.append({"type": "bash_20250124", "name": "bash"})
        elif tool == "text_editor":
            definitions.append(
                {"type": "text_editor_20250124", "name": "str_replace_editor"}
            )
        elif tool == "computer":
            definitions.append(
                {
                    "type": "computer_20251124",
                    "name": "computer",
                    "display_width_px": config.display_width_px,
                    "display_height_px": config.display_height_px,
                }
            )
        else:  # pragma: no cover - guarded by Literal typing.
            raise ComputerUseError(f"Unknown tool: {tool}")

    return definitions


def build_beta_header(model: str) -> str:
    """Return the Computer Use beta header string for ``model``.

    Opus 4.7, Sonnet 4.6 and Haiku 4.5 use the newer ``computer-use-2025-11-24``
    header. Older Sonnet 4.5 / Opus 4.5 generations remain on the
    ``computer-use-2025-01-24`` header.
    """

    if model in _NEW_BETA_MODELS:
        return BETA_HEADER_2025_11_24
    if model in _OLD_BETA_MODELS:
        return BETA_HEADER_2025_01_24

    # Default: assume the newer header for unknown / future model ids.
    return BETA_HEADER_2025_11_24


def should_pause_for_approval(
    tool_use: dict, config: ComputerUseConfig
) -> bool:
    """Decide whether a tool_use event should pause for human approval.

    The check compares against both the raw tool name (``bash``,
    ``str_replace_editor`` etc.) and the dotted form ``<tool>.<action>``
    (for example ``computer.mouse_click``). An action sub-type is read from
    ``tool_use['input']['action']`` if present.
    """

    if not config.require_human_approval_for:
        return False

    name = tool_use.get("name") or ""
    action = ""
    raw_input = tool_use.get("input") or {}
    if isinstance(raw_input, dict):
        action = str(raw_input.get("action") or "")

    candidates = {name}
    if action:
        candidates.add(action)
        candidates.add(f"{name}.{action}")

    return any(item in config.require_human_approval_for for item in candidates if item)


def print_safety_checklist() -> None:
    """Print the Computer Use safety checklist to stdout."""

    print("Computer Use safety pre-flight checklist:")
    for idx, item in enumerate(SAFETY_CHECKLIST, start=1):
        print(f"  {idx}. {item}")


__all__ = [
    "BETA_HEADER_2025_01_24",
    "BETA_HEADER_2025_11_24",
    "ComputerUseConfig",
    "ComputerUseError",
    "MAX_DISPLAY_WIDTH_PX",
    "MIN_DISPLAY_WIDTH_PX",
    "SAFETY_CHECKLIST",
    "build_beta_header",
    "build_tool_definitions",
    "print_safety_checklist",
    "should_pause_for_approval",
    "validate_config",
]
