"""AI Workflow Generator - creates valid YAML workflows from natural language."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml

from sandcastle.engine.dag import parse_yaml_string, validate
from sandcastle.engine.providers import KNOWN_MODELS


@dataclass
class GenerateResult:
    """Result from the AI workflow generator."""

    yaml_content: str
    name: str = ""
    description: str = ""
    steps_count: int = 0
    validation_errors: list[str] = field(default_factory=list)
    input_schema: dict | None = None


# ---------------------------------------------------------------------------
# Few-shot examples
# ---------------------------------------------------------------------------

_EXAMPLE_TEMPLATES = [
    "research_agent",
    "data_extractor",
    "email_campaign",
    "review_and_approve",
]


def _load_example_templates() -> str:
    """Load curated templates as few-shot examples for the system prompt."""
    templates_dir = Path(__file__).parent.parent / "templates"
    parts: list[str] = []
    for name in _EXAMPLE_TEMPLATES:
        path = templates_dir / f"{name}.yaml"
        if path.exists():
            parts.append(f"--- Example: {name} ---\n{path.read_text()}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

def _build_system_prompt() -> str:
    """Build the system prompt with schema docs, model list, and examples."""
    models = ", ".join(sorted(KNOWN_MODELS))
    examples = _load_example_templates()

    return f"""\
You are a workflow generator for Sandcastle, an AI agent orchestrator.
Your job is to produce valid YAML workflow definitions based on the user's description.

## YAML Schema

A workflow YAML has these top-level fields:
- name: kebab-case identifier (required)
- description: short description (required)
- default_model: model name (optional, default: sonnet)
- default_max_turns: integer (optional, default: 10)
- default_timeout: seconds (optional, default: 300)
- input_schema: JSON Schema for user inputs (required)
  - required: list of required field names
  - properties: object with field definitions (type, description)
- steps: list of step objects (required)

Each step has:
- id: unique kebab-case identifier (required)
- prompt: the instruction for the agent (required)
- depends_on: list of step IDs this step waits for (optional)
- model: model name (optional, overrides default_model)
- max_turns: integer (optional)
- type: "approval" for human-in-the-loop steps (optional)
- approval_config: config for approval steps (optional)
  - message: reviewer message
  - show_data: variable path to show reviewer
  - timeout_hours: float
  - on_timeout: "abort" or "skip"
  - allow_edit: boolean

## Available Models
{models}
Always use these short names - NEVER use full API model IDs.

## Variable Syntax
- {{input.X}} - reference user input field X
- {{steps.STEP_ID.output}} - reference output of a previous step

## Rules
1. Every workflow MUST have input_schema with required and properties
2. Use kebab-case for workflow name and step IDs
3. First step should have no depends_on
4. Steps that run in parallel share the same depends_on
5. Use descriptive prompts that reference inputs and previous step outputs
6. Output ONLY valid YAML - no markdown fencing, no explanations
7. Choose appropriate models: sonnet for complex tasks, haiku for simple formatting

## Examples

{examples}

Generate a complete, valid workflow YAML based on the user's description.
Output ONLY the YAML content, nothing else."""


# ---------------------------------------------------------------------------
# Core generation
# ---------------------------------------------------------------------------

_API_URL = "https://api.anthropic.com/v1/messages"
_MODEL = "claude-sonnet-4-20250514"
_MAX_TOKENS = 4096
_TIMEOUT = 60


async def generate_workflow(
    description: str,
    *,
    refine_from: str | None = None,
    refine_instruction: str | None = None,
) -> GenerateResult:
    """Generate a workflow YAML from a natural language description.

    Args:
        description: What the workflow should do.
        refine_from: Existing YAML to refine.
        refine_instruction: What to change in the existing YAML.

    Returns:
        GenerateResult with the generated YAML and metadata.

    Raises:
        ValueError: If ANTHROPIC_API_KEY is not set.
        httpx.HTTPStatusError: If the Anthropic API returns an error.
    """
    import os

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        from sandcastle.config import settings
        api_key = settings.anthropic_api_key
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is required for workflow generation. "
            "Set it in your .env file or environment."
        )

    system_prompt = _build_system_prompt()

    # Build user message
    if refine_from and refine_instruction:
        user_msg = (
            f"Here is an existing workflow YAML:\n\n{refine_from}\n\n"
            f"Please modify it as follows: {refine_instruction}\n\n"
            f"Original description: {description}"
        )
    else:
        user_msg = description

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            _API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": _MODEL,
                "max_tokens": _MAX_TOKENS,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_msg}],
            },
        )
        resp.raise_for_status()

    data = resp.json()
    raw_text = data["content"][0]["text"]

    # Strip markdown fencing if present
    yaml_content = _strip_fencing(raw_text)

    # Validate the generated YAML
    result = GenerateResult(yaml_content=yaml_content)
    try:
        wf = parse_yaml_string(yaml_content)
        result.name = wf.name
        result.description = wf.description
        result.steps_count = len(wf.steps)
        result.input_schema = wf.input_schema
        errors = validate(wf)
        result.validation_errors = errors
    except Exception as exc:
        result.validation_errors = [f"YAML parse error: {exc}"]

    return result


def _strip_fencing(text: str) -> str:
    """Remove markdown code fencing from generated YAML."""
    text = text.strip()
    # Remove ```yaml ... ``` or ``` ... ```
    m = re.match(r"^```(?:ya?ml)?\s*\n(.*?)```\s*$", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


# ---------------------------------------------------------------------------
# Sync wrapper for CLI
# ---------------------------------------------------------------------------

def generate_workflow_sync(
    description: str,
    *,
    refine_from: str | None = None,
    refine_instruction: str | None = None,
) -> GenerateResult:
    """Synchronous wrapper around generate_workflow for CLI usage."""
    return asyncio.run(
        generate_workflow(
            description,
            refine_from=refine_from,
            refine_instruction=refine_instruction,
        )
    )
