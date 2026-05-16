"""Anthropic Agent Skills publisher for Sandcastle.

Converts Sandcastle workflow YAMLs into uploadable Skill packages following the
Anthropic Agent Skills spec (beta header `skills-2025-10-02`). A Skill is a
tar.gz archive containing a SKILL.md (YAML frontmatter + markdown body) and any
bundled support files. Frontmatter is loaded at agent init; the body is loaded
on trigger; bundled files are loaded on demand (progressive disclosure).

Public surface:
    - SkillFrontmatter, SkillPackage dataclasses
    - SkillValidationError
    - workflow_to_skill(workflow_yaml) -> SkillPackage
    - serialize_skill(package) -> bytes
    - parse_skill(blob) -> SkillPackage
    - AnthropicSkillsClient (async)
    - publish_workflows_as_skills(workflow_dir, dry_run=True)
"""

from __future__ import annotations

import io
import json
import re
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_BETA_HEADERS = [
    "skills-2025-10-02",
    "code-execution-2025-10-02",
    "files-api-2025-04-14",
]

# Anthropic Skills spec limits
_NAME_MAX = 64
_DESC_MAX = 1024

# Names with these tokens are reserved per the spec.
_RESERVED_TOKENS = ("anthropic", "claude")

_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SkillValidationError(Exception):
    """Raised when a Skill frontmatter or package fails validation."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SkillFrontmatter:
    """YAML frontmatter that appears at the top of every SKILL.md.

    The frontmatter is always loaded into the agent's context, so it must stay
    compact. The full body is loaded only when the Skill is triggered.
    """

    name: str
    description: str
    version: str = "1.0.0"
    model: str | None = None
    allowed_tools: list[str] | None = None

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise SkillValidationError("name must be a non-empty string")
        if len(self.name) > _NAME_MAX:
            raise SkillValidationError(
                f"name must be <= {_NAME_MAX} chars, got {len(self.name)}"
            )
        if not _NAME_PATTERN.match(self.name):
            raise SkillValidationError(
                "name must be lowercase alphanumeric with single hyphens"
            )
        lowered = self.name.lower()
        for token in _RESERVED_TOKENS:
            if token in lowered:
                raise SkillValidationError(
                    f"name must not contain reserved token '{token}'"
                )

        if not isinstance(self.description, str) or not self.description:
            raise SkillValidationError("description must be a non-empty string")
        if len(self.description) > _DESC_MAX:
            raise SkillValidationError(
                f"description must be <= {_DESC_MAX} chars, got {len(self.description)}"
            )

        if self.allowed_tools is not None and not isinstance(self.allowed_tools, list):
            raise SkillValidationError("allowed_tools must be a list or None")

    def to_yaml_dict(self) -> dict[str, Any]:
        """Render to a dict ready for YAML dumping (uses spec key names)."""
        out: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "version": self.version,
        }
        if self.model is not None:
            out["model"] = self.model
        if self.allowed_tools is not None:
            out["allowed-tools"] = list(self.allowed_tools)
        return out


@dataclass
class SkillPackage:
    """A complete Skill ready to be archived and uploaded."""

    frontmatter: SkillFrontmatter
    body: str
    bundled_files: dict[str, bytes] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Workflow conversion
# ---------------------------------------------------------------------------


def _slugify(value: str) -> str:
    """Convert any string into a Skill-safe kebab-case slug."""
    value = value.lower().strip()
    # Drop reserved tokens before they trip validation.
    for token in _RESERVED_TOKENS:
        value = value.replace(token, "")
    # Replace anything not alnum with a hyphen.
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    if not value:
        value = "workflow"
    # Collapse repeated hyphens left over from token removal.
    value = re.sub(r"-{2,}", "-", value)
    return value[:_NAME_MAX].rstrip("-") or "workflow"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "."


def workflow_to_skill(workflow_yaml: str) -> SkillPackage:
    """Convert a Sandcastle workflow YAML string into a SkillPackage.

    Generates a markdown body that documents the workflow's inputs, steps, and
    example invocation so that an agent can decide whether to trigger it.
    """
    data = yaml.safe_load(workflow_yaml) or {}
    if not isinstance(data, dict):
        raise SkillValidationError("workflow YAML must be a mapping at the top level")

    raw_name = str(data.get("name") or "workflow").strip()
    slug = _slugify(raw_name)

    raw_desc = str(data.get("description") or f"Sandcastle workflow: {raw_name}").strip()
    description = _truncate(raw_desc, _DESC_MAX)

    frontmatter = SkillFrontmatter(name=slug, description=description)

    # Build the markdown body.
    input_schema = data.get("input_schema") or {}
    properties = input_schema.get("properties") or {}
    required = input_schema.get("required") or []
    steps = data.get("steps") or []

    lines: list[str] = []
    lines.append(f"# {raw_name}")
    lines.append("")
    lines.append(raw_desc)
    lines.append("")
    lines.append("## Inputs")
    lines.append("")
    if properties:
        for key, spec in properties.items():
            spec = spec if isinstance(spec, dict) else {}
            req = " (required)" if key in required else ""
            type_str = spec.get("type", "any")
            desc = spec.get("description", "")
            lines.append(f"- `{key}` ({type_str}){req}: {desc}".rstrip())
    else:
        lines.append("- No structured inputs declared.")
    lines.append("")

    lines.append("## Steps")
    lines.append("")
    if steps:
        for idx, step in enumerate(steps, start=1):
            step = step if isinstance(step, dict) else {}
            step_id = step.get("id", f"step-{idx}")
            depends = step.get("depends_on") or []
            dep_str = f" (after: {', '.join(depends)})" if depends else ""
            model = step.get("model")
            model_str = f" [model: {model}]" if model else ""
            lines.append(f"{idx}. **{step_id}**{dep_str}{model_str}")
    else:
        lines.append("- No steps defined.")
    lines.append("")

    lines.append("## Example invocation")
    lines.append("")
    example_input = {key: f"<{key}>" for key in properties} or {"input": "<value>"}
    lines.append("```json")
    lines.append(json.dumps({"workflow": slug, "input": example_input}, indent=2))
    lines.append("```")
    lines.append("")

    body = "\n".join(lines)
    return SkillPackage(frontmatter=frontmatter, body=body)


# ---------------------------------------------------------------------------
# Serialization (tar.gz)
# ---------------------------------------------------------------------------


def _render_skill_md(package: SkillPackage) -> bytes:
    fm_yaml = yaml.safe_dump(
        package.frontmatter.to_yaml_dict(),
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    ).strip()
    text = f"---\n{fm_yaml}\n---\n\n{package.body}"
    if not text.endswith("\n"):
        text += "\n"
    return text.encode("utf-8")


def serialize_skill(package: SkillPackage) -> bytes:
    """Serialize a SkillPackage into a tar.gz blob ready for upload."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        skill_md = _render_skill_md(package)
        info = tarfile.TarInfo(name="SKILL.md")
        info.size = len(skill_md)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(skill_md))

        for rel_path, content in package.bundled_files.items():
            # Disallow absolute or escaping paths.
            clean = rel_path.lstrip("/").replace("..", "")
            if not clean or clean == "SKILL.md":
                continue
            f_info = tarfile.TarInfo(name=clean)
            f_info.size = len(content)
            f_info.mode = 0o644
            tar.addfile(f_info, io.BytesIO(content))
    return buf.getvalue()


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        raise SkillValidationError("SKILL.md missing YAML frontmatter")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise SkillValidationError("SKILL.md frontmatter is not terminated")
    fm_raw = parts[1].strip()
    body = parts[2].lstrip("\n")
    fm = yaml.safe_load(fm_raw) or {}
    if not isinstance(fm, dict):
        raise SkillValidationError("SKILL.md frontmatter must be a mapping")
    return fm, body


def parse_skill(blob: bytes) -> SkillPackage:
    """Inverse of serialize_skill: read a tar.gz blob into a SkillPackage."""
    if not blob:
        raise SkillValidationError("empty Skill archive")
    try:
        tar = tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz")
    except (tarfile.TarError, OSError) as exc:
        raise SkillValidationError(f"not a valid tar.gz archive: {exc}") from exc

    bundled: dict[str, bytes] = {}
    skill_md_bytes: bytes | None = None
    try:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            f = tar.extractfile(member)
            if f is None:
                continue
            data = f.read()
            if member.name == "SKILL.md":
                skill_md_bytes = data
            else:
                bundled[member.name] = data
    finally:
        tar.close()

    if skill_md_bytes is None:
        raise SkillValidationError("archive missing SKILL.md")

    fm_dict, body = _split_frontmatter(skill_md_bytes.decode("utf-8"))
    allowed_tools = fm_dict.get("allowed-tools")
    if allowed_tools is None:
        allowed_tools = fm_dict.get("allowed_tools")
    frontmatter = SkillFrontmatter(
        name=str(fm_dict.get("name", "")),
        description=str(fm_dict.get("description", "")),
        version=str(fm_dict.get("version", "1.0.0")),
        model=fm_dict.get("model"),
        allowed_tools=allowed_tools,
    )
    return SkillPackage(frontmatter=frontmatter, body=body.rstrip("\n"), bundled_files=bundled)


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


class AnthropicSkillsClient:
    """Async client for the Anthropic Skills API.

    The Skills API is in beta and requires the `anthropic-beta` header listing
    the feature flags the request opts into. Defaults cover Skills, Code
    Execution, and the Files API (used for bundled file references).
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.anthropic.com",
        beta_headers: list[str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._beta_headers = list(beta_headers) if beta_headers is not None else list(
            DEFAULT_BETA_HEADERS
        )
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": ", ".join(self._beta_headers),
        }

    async def upload(self, package: SkillPackage) -> dict[str, Any]:
        """Upload a SkillPackage as a multipart POST to /v1/skills."""
        blob = serialize_skill(package)
        filename = f"{package.frontmatter.name}.tar.gz"
        files = {
            "skill": (filename, blob, "application/gzip"),
        }
        data = {
            "name": package.frontmatter.name,
            "version": package.frontmatter.version,
            "description": package.frontmatter.description,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/v1/skills",
                headers=self._headers(),
                data=data,
                files=files,
            )
            resp.raise_for_status()
            return resp.json()

    async def list_skills(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{self._base_url}/v1/skills",
                headers=self._headers(),
            )
            resp.raise_for_status()
            payload = resp.json()
            if isinstance(payload, dict) and "data" in payload:
                return list(payload["data"])
            if isinstance(payload, list):
                return payload
            return []

    async def get_skill(self, skill_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{self._base_url}/v1/skills/{skill_id}",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def delete_skill(self, skill_id: str) -> None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.delete(
                f"{self._base_url}/v1/skills/{skill_id}",
                headers=self._headers(),
            )
            resp.raise_for_status()


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------


async def publish_workflows_as_skills(
    workflow_dir: str,
    dry_run: bool = True,
    client: AnthropicSkillsClient | None = None,
) -> list[dict[str, Any]]:
    """Scan a directory for .yaml workflows and publish each as a Skill.

    When ``dry_run`` is True (the default) nothing is uploaded; the function
    returns one result per workflow describing what would have happened. When
    False, ``client`` must be provided and each Skill is POSTed via
    ``client.upload``.
    """
    root = Path(workflow_dir)
    if not root.exists() or not root.is_dir():
        raise SkillValidationError(f"workflow_dir not found: {workflow_dir}")

    results: list[dict[str, Any]] = []
    paths = sorted(
        [*root.glob("*.yaml"), *root.glob("*.yml")],
        key=lambda p: p.name,
    )
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
            package = workflow_to_skill(text)
        except (SkillValidationError, yaml.YAMLError) as exc:
            results.append({
                "path": str(path),
                "status": "error",
                "error": str(exc),
            })
            continue

        entry: dict[str, Any] = {
            "path": str(path),
            "name": package.frontmatter.name,
            "description": package.frontmatter.description,
        }
        if dry_run:
            entry["status"] = "dry_run"
            entry["message"] = f"would upload {package.frontmatter.name}"
        else:
            if client is None:
                raise SkillValidationError(
                    "client is required when dry_run=False"
                )
            response = await client.upload(package)
            entry["status"] = "uploaded"
            entry["response"] = response
        results.append(entry)
    return results


__all__ = [
    "DEFAULT_BETA_HEADERS",
    "SkillFrontmatter",
    "SkillPackage",
    "SkillValidationError",
    "workflow_to_skill",
    "serialize_skill",
    "parse_skill",
    "AnthropicSkillsClient",
    "publish_workflows_as_skills",
]
