"""Tool Search + Tool Use Examples registry.

Lightweight registry for agent-callable tools with:
- Token-overlap search across name, description, tags
- Hot/lazy partitioning for deferred loading of rare tools
- 1-5 worked examples per tool (input/output) baked into the definition
- JSON-Schema validation of examples against parameter schemas
- Anthropic-compatible tool definition shape on demand

Based on observed accuracy gains:
- Tool selection accuracy: 49 percent -> 74 percent with tool search
- Parameter-shape accuracy: 72 percent -> 90 percent with 1-5 examples per tool

This module is self-contained and does not touch the executor, DAG, or
existing connector tool registries. Connector authors opt in by registering
their tools with the module-level ``default_registry``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

__all__ = [
    "ToolDefinition",
    "ToolRegistry",
    "default_registry",
    "validate_tool",
]


_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")


def _tokenize(text: str) -> set[str]:
    """Lowercase token set used by the search ranker."""
    if not text:
        return set()
    return {tok.lower() for tok in _TOKEN_RE.findall(text)}


@dataclass
class ToolDefinition:
    """Single tool entry in the registry.

    Attributes:
        name: Stable tool identifier. Unique within a registry.
        description: Human and agent readable summary (>= 20 chars).
        parameters: JSON Schema describing accepted inputs.
        examples: 1-5 entries, each ``{"input": dict, "output": dict}``.
        defer_loading: When True the tool is considered "lazy" and is only
            surfaced via explicit search. Use for rare or expensive tools.
        tags: Free-form labels used by the search ranker.
    """

    name: str
    description: str
    parameters: dict
    examples: list[dict]
    defer_loading: bool = False
    tags: list[str] = field(default_factory=list)


def validate_tool(tool: ToolDefinition) -> list[str]:
    """Return a list of human-readable validation errors.

    Empty list means the tool is well-formed. Checks:
    - name is non-empty
    - description >= 20 characters
    - parameters is a valid JSON Schema (Draft 2020-12)
    - 1 <= len(examples) <= 5
    - each example has both 'input' and 'output' as dicts
    - each example input validates against parameters
    """
    errors: list[str] = []

    if not tool.name or not isinstance(tool.name, str):
        errors.append("tool.name must be a non-empty string")

    if not isinstance(tool.description, str) or len(tool.description) < 20:
        errors.append(
            "tool.description must be at least 20 characters"
            " so agents can disambiguate similar tools"
        )

    if not isinstance(tool.parameters, dict):
        errors.append("tool.parameters must be a dict JSON Schema")
        # Cannot validate examples without a schema.
        return errors

    try:
        Draft202012Validator.check_schema(tool.parameters)
    except Exception as exc:  # jsonschema.SchemaError or similar
        errors.append(f"tool.parameters is not a valid JSON Schema: {exc}")
        return errors

    if not isinstance(tool.examples, list) or len(tool.examples) < 1:
        errors.append(
            "tool.examples must contain 1 to 5 entries"
            " (parameter-shape accuracy jumps with even one example)"
        )
    elif len(tool.examples) > 5:
        errors.append(
            f"tool.examples has {len(tool.examples)} entries; max is 5"
            " (more examples bloat the system prompt without gain)"
        )
    else:
        validator = Draft202012Validator(tool.parameters)
        for idx, ex in enumerate(tool.examples):
            if not isinstance(ex, dict):
                errors.append(f"examples[{idx}] must be a dict")
                continue
            if "input" not in ex or not isinstance(ex["input"], dict):
                errors.append(f"examples[{idx}].input must be a dict")
                continue
            if "output" not in ex or not isinstance(ex["output"], dict):
                errors.append(f"examples[{idx}].output must be a dict")
                continue
            try:
                validator.validate(ex["input"])
            except ValidationError as exc:
                errors.append(
                    f"examples[{idx}].input fails parameters schema: {exc.message}"
                )

    return errors


class ToolRegistry:
    """In-memory registry of ``ToolDefinition`` keyed by name."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    # ------------------------------------------------------------------ core

    def register(self, tool: ToolDefinition) -> None:
        """Add or replace a tool by name."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def all(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    # ---------------------------------------------------------------- search

    def search(self, query: str, limit: int = 5) -> list[ToolDefinition]:
        """Return up to ``limit`` tools ranked by relevance to ``query``.

        Scoring:
        - +100 if the query exactly matches a tool name
        - +10 per query token found in the tool name
        - +3 per query token found in tags
        - +1 per query token found in the description
        Ties are broken by registration order (stable).
        """
        if not query or not self._tools:
            return []

        q_lower = query.strip().lower()
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []

        scored: list[tuple[float, int, ToolDefinition]] = []
        for idx, tool in enumerate(self._tools.values()):
            name_tokens = _tokenize(tool.name)
            desc_tokens = _tokenize(tool.description)
            tag_tokens = _tokenize(" ".join(tool.tags))

            score = 0.0
            if tool.name.lower() == q_lower:
                score += 100.0

            for tok in q_tokens:
                if tok in name_tokens:
                    score += 10.0
                if tok in tag_tokens:
                    score += 3.0
                if tok in desc_tokens:
                    score += 1.0

            if score > 0:
                # Negative idx so earlier registrations win ties.
                scored.append((score, -idx, tool))

        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [tool for _, _, tool in scored[:limit]]

    # ------------------------------------------------------------ partitions

    def hot_tools(self) -> list[ToolDefinition]:
        """Tools loaded eagerly into the agent's system prompt."""
        return [t for t in self._tools.values() if not t.defer_loading]

    def lazy_tools(self) -> list[ToolDefinition]:
        """Tools the agent must explicitly fetch via search."""
        return [t for t in self._tools.values() if t.defer_loading]

    # --------------------------------------------------------------- adapter

    @staticmethod
    def format_for_agent(tools: list[ToolDefinition]) -> list[dict]:
        """Convert tools to the Anthropic tool definition shape.

        Produces a list of ``{name, description, input_schema, examples?}``.
        Examples are included when present so they ride along in the prompt.
        """
        out: list[dict] = []
        for tool in tools:
            entry: dict[str, Any] = {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            if tool.examples:
                entry["examples"] = list(tool.examples)
            out.append(entry)
        return out


# Module-level singleton used by connector authors.
default_registry: ToolRegistry = ToolRegistry()
