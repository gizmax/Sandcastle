"""Built-in workflow templates for Sandcastle.

Provides reusable workflow templates that users can browse, preview,
and use as starting points for their own workflows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class TemplateInfo:
    """Metadata for a workflow template."""

    name: str
    description: str
    tags: list[str]
    file_name: str
    step_count: int
    input_schema: dict | None = None


_TEMPLATES_DIR = Path(__file__).parent


def _parse_comment_metadata(content: str) -> dict[str, str | list[str]]:
    """Extract metadata from the YAML comment header.

    Looks for lines like:
        # name: Human readable name
        # description: What this workflow does
        # tags: [tag1, tag2]
    """
    meta: dict[str, str | list[str]] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("#"):
            break
        # Strip leading "# " and parse key: value
        stripped = line.lstrip("#").strip()
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if key == "tags":
            # Parse [tag1, tag2] format
            match = re.match(r"\[(.+)]", value)
            if match:
                meta[key] = [t.strip() for t in match.group(1).split(",")]
            else:
                meta[key] = [value]
        else:
            meta[key] = value
    return meta


def list_templates() -> list[TemplateInfo]:
    """List all available workflow templates with their metadata.

    Returns a list of TemplateInfo objects sorted alphabetically by file name.
    """
    templates: list[TemplateInfo] = []
    for path in sorted(_TEMPLATES_DIR.glob("*.yaml")):
        content = path.read_text()
        meta = _parse_comment_metadata(content)

        # Count steps from the parsed YAML body
        data = yaml.safe_load(content)
        step_count = len(data.get("steps", []))

        templates.append(
            TemplateInfo(
                name=str(meta.get("name", path.stem)),
                description=str(meta.get("description", "")),
                tags=list(meta.get("tags", [])),
                file_name=path.name,
                step_count=step_count,
                input_schema=data.get("input_schema"),
            )
        )
    return templates


def get_template(name: str) -> tuple[str, TemplateInfo]:
    """Get a template's raw YAML content and metadata by name.

    The name can be the file stem (e.g. "summarize") or the file name
    with extension (e.g. "summarize.yaml").

    Returns:
        A tuple of (yaml_content, template_info).

    Raises:
        FileNotFoundError: If no template matches the given name.
    """
    # Normalize: strip .yaml suffix if present
    stem = name.removesuffix(".yaml")

    for path in _TEMPLATES_DIR.glob("*.yaml"):
        content = path.read_text()
        meta = _parse_comment_metadata(content)
        display_name = str(meta.get("name", path.stem))

        # Match by file stem or by display name
        if path.stem != stem and display_name != name:
            continue

        data = yaml.safe_load(content)
        step_count = len(data.get("steps", []))

        info = TemplateInfo(
            name=display_name,
            description=str(meta.get("description", "")),
            tags=list(meta.get("tags", [])),
            file_name=path.name,
            step_count=step_count,
            input_schema=data.get("input_schema"),
        )
        return content, info

    available = [p.stem for p in _TEMPLATES_DIR.glob("*.yaml")]
    raise FileNotFoundError(
        f"Template '{name}' not found. Available: {', '.join(available)}"
    )
