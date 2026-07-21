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
    category: str | None = None
    source: str = "built-in"  # "built-in" or "community"


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


def _parse_template(path: Path, source: str = "built-in") -> TemplateInfo:
    """Parse a single template YAML file into a TemplateInfo object.

    Args:
        path: Path to the YAML template file.
        source: Origin of the template - "built-in" or "community".

    Returns:
        A TemplateInfo with metadata extracted from the file.
    """
    content = path.read_text()
    meta = _parse_comment_metadata(content)

    # Count steps from the parsed YAML body
    data = yaml.safe_load(content)
    if not isinstance(data, dict):
        data = {}
    step_count = len(data.get("steps", []))

    # Fall back to YAML body keys when comment metadata is missing
    name_val = str(meta.get("name") or data.get("name") or path.stem)
    desc_val = str(meta.get("description") or data.get("description") or "")
    tags_val = list(meta.get("tags") or data.get("tags") or [])
    category_val = (
        str(meta["category"]) if "category" in meta
        else str(data["category"]) if "category" in data
        else None
    )

    return TemplateInfo(
        name=name_val,
        description=desc_val,
        tags=tags_val,
        file_name=path.name,
        step_count=step_count,
        input_schema=data.get("input_schema"),
        category=category_val,
        source=source,
    )


def list_templates() -> list[TemplateInfo]:
    """List all available workflow templates with their metadata.

    Returns a list of TemplateInfo objects sorted alphabetically by file name.
    Includes both built-in templates and user-installed community templates.
    """
    templates: list[TemplateInfo] = []

    # Built-in templates
    for path in sorted([*_TEMPLATES_DIR.glob("*.yaml"), *_TEMPLATES_DIR.glob("*.yml")]):
        templates.append(_parse_template(path, source="built-in"))

    # Community templates (user-installed)
    community_dir = _TEMPLATES_DIR / "community"
    if community_dir.is_dir():
        for path in sorted([*community_dir.glob("*.yaml"), *community_dir.glob("*.yml")]):
            templates.append(_parse_template(path, source="community"))

    return templates


def find_template_yaml_by_workflow_name(workflow_name: str) -> str | None:
    """Return a template's YAML whose declared workflow ``name:`` matches.

    Runs started from a hub template record the workflow name declared inside
    the YAML (e.g. ``seo-content-writer``), which need not match the template
    file stem (``seo_content.yaml``). Replay uses this as a fallback when the
    workflow is not in the user's workflows directory. Returns None when no
    template declares that name.
    """
    import re as _re

    if not workflow_name:
        return None
    pattern = _re.compile(
        r"^name:\s*['\"]?" + _re.escape(workflow_name) + r"['\"]?\s*$", _re.M
    )
    search_dirs = [_TEMPLATES_DIR]
    community_dir = _TEMPLATES_DIR / "community"
    if community_dir.is_dir():
        search_dirs.append(community_dir)
    for dir_path in search_dirs:
        for path in sorted([*dir_path.glob("*.yaml"), *dir_path.glob("*.yml")]):
            try:
                content = path.read_text()
            except OSError:
                continue
            if pattern.search(content):
                return content
    return None


def get_template(name: str) -> tuple[str, TemplateInfo]:
    """Get a template's raw YAML content and metadata by name.

    The name can be the file stem (e.g. "summarize") or the file name
    with extension (e.g. "summarize.yaml").

    Searches both built-in and community templates.

    Returns:
        A tuple of (yaml_content, template_info).

    Raises:
        FileNotFoundError: If no template matches the given name.
    """
    # Normalize: strip .yaml/.yml suffix if present
    stem = name.removesuffix(".yaml").removesuffix(".yml")

    # Collect all search directories
    search_dirs: list[tuple[Path, str]] = [(_TEMPLATES_DIR, "built-in")]
    community_dir = _TEMPLATES_DIR / "community"
    if community_dir.is_dir():
        search_dirs.append((community_dir, "community"))

    for dir_path, source in search_dirs:
        for path in sorted([*dir_path.glob("*.yaml"), *dir_path.glob("*.yml")]):
            content = path.read_text()
            meta = _parse_comment_metadata(content)
            display_name = str(meta.get("name", path.stem))

            # Match by file stem or by display name
            if path.stem != stem and display_name != name:
                continue

            info = _parse_template(path, source=source)
            return content, info

    available = [p.stem for d, _ in search_dirs for ext in ("*.yaml", "*.yml") for p in d.glob(ext)]
    raise FileNotFoundError(f"Template '{name}' not found. Available: {', '.join(available)}")
