"""Tests for the Anthropic Agent Skills publisher (engine.agent_skills)."""

from __future__ import annotations

import io
import tarfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.agent_skills import (
    DEFAULT_BETA_HEADERS,
    AnthropicSkillsClient,
    SkillFrontmatter,
    SkillPackage,
    SkillValidationError,
    parse_skill,
    publish_workflows_as_skills,
    serialize_skill,
    workflow_to_skill,
)


# ---------------------------------------------------------------------------
# Frontmatter validation
# ---------------------------------------------------------------------------


def test_frontmatter_rejects_reserved_anthropic_token() -> None:
    with pytest.raises(SkillValidationError):
        SkillFrontmatter(name="anthropic-foo", description="A reserved name.")


def test_frontmatter_accepts_simple_name() -> None:
    fm = SkillFrontmatter(name="researcher", description="A research helper.")
    assert fm.name == "researcher"
    assert fm.version == "1.0.0"


def test_frontmatter_rejects_long_description() -> None:
    long_desc = "x" * 1025
    with pytest.raises(SkillValidationError):
        SkillFrontmatter(name="researcher", description=long_desc)


def test_frontmatter_rejects_reserved_claude_token() -> None:
    with pytest.raises(SkillValidationError):
        SkillFrontmatter(name="claude-helper", description="reserved")


def test_frontmatter_rejects_uppercase_name() -> None:
    with pytest.raises(SkillValidationError):
        SkillFrontmatter(name="Researcher", description="bad casing")


# ---------------------------------------------------------------------------
# workflow_to_skill
# ---------------------------------------------------------------------------


_MINIMAL_WORKFLOW = """
name: "Lead Enrichment"
description: "Enrich a company lead with industry data and a sales score."
input_schema:
  required: ["company"]
  properties:
    company:
      type: string
      description: "Company name or URL"
steps:
  - id: "extract"
    prompt: "..."
  - id: "score"
    prompt: "..."
    depends_on: ["extract"]
""".strip()


def test_workflow_to_skill_produces_valid_frontmatter() -> None:
    package = workflow_to_skill(_MINIMAL_WORKFLOW)
    assert package.frontmatter.name == "lead-enrichment"
    assert "Enrich" in package.frontmatter.description
    assert len(package.frontmatter.description) <= 1024
    # Body should mention each step id and input.
    assert "extract" in package.body
    assert "score" in package.body
    assert "company" in package.body


def test_workflow_to_skill_handles_reserved_tokens_in_name() -> None:
    yaml_text = """
name: "Anthropic Helper"
description: "Should slugify around the reserved token."
""".strip()
    package = workflow_to_skill(yaml_text)
    # Slugify must strip "anthropic" before validation runs.
    assert "anthropic" not in package.frontmatter.name
    assert "claude" not in package.frontmatter.name
    assert package.frontmatter.name  # non-empty


# ---------------------------------------------------------------------------
# serialize / parse round-trip
# ---------------------------------------------------------------------------


def test_serialize_skill_roundtrips_through_parse_skill() -> None:
    original = SkillPackage(
        frontmatter=SkillFrontmatter(
            name="researcher",
            description="A research helper.",
            version="2.1.0",
            model="sonnet",
            allowed_tools=["bash", "edit"],
        ),
        body="# Researcher\n\nHelpful body text.",
    )
    blob = serialize_skill(original)
    parsed = parse_skill(blob)
    assert parsed.frontmatter.name == "researcher"
    assert parsed.frontmatter.description == "A research helper."
    assert parsed.frontmatter.version == "2.1.0"
    assert parsed.frontmatter.model == "sonnet"
    assert parsed.frontmatter.allowed_tools == ["bash", "edit"]
    assert "Helpful body text." in parsed.body


def test_serialize_skill_archive_is_valid_targz() -> None:
    package = workflow_to_skill(_MINIMAL_WORKFLOW)
    blob = serialize_skill(package)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        names = tar.getnames()
    assert "SKILL.md" in names


def test_serialize_skill_includes_bundled_files() -> None:
    package = SkillPackage(
        frontmatter=SkillFrontmatter(name="bundler", description="Has extras."),
        body="# Bundler",
        bundled_files={
            "reference/notes.txt": b"hello world",
            "data/sample.json": b'{"a": 1}',
        },
    )
    blob = serialize_skill(package)
    parsed = parse_skill(blob)
    assert parsed.bundled_files["reference/notes.txt"] == b"hello world"
    assert parsed.bundled_files["data/sample.json"] == b'{"a": 1}'


# ---------------------------------------------------------------------------
# parse_skill error handling
# ---------------------------------------------------------------------------


def test_parse_skill_rejects_malformed_archive() -> None:
    with pytest.raises(SkillValidationError):
        parse_skill(b"not-a-tar-gz-blob")


def test_parse_skill_rejects_archive_without_skill_md() -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="other.txt")
        info.size = 3
        tar.addfile(info, io.BytesIO(b"abc"))
    with pytest.raises(SkillValidationError):
        parse_skill(buf.getvalue())


# ---------------------------------------------------------------------------
# AnthropicSkillsClient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_upload_sends_all_beta_headers_and_multipart() -> None:
    package = SkillPackage(
        frontmatter=SkillFrontmatter(name="researcher", description="Helps."),
        body="# Researcher",
    )

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"id": "skill_123", "name": "researcher"})

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "sandcastle.engine.agent_skills.httpx.AsyncClient",
        return_value=mock_client,
    ):
        client = AnthropicSkillsClient(api_key="sk-test")
        result = await client.upload(package)

    assert result == {"id": "skill_123", "name": "researcher"}
    mock_client.post.assert_awaited_once()
    call = mock_client.post.await_args
    # URL
    assert call.args[0].endswith("/v1/skills")
    # Headers contain all three beta flags
    headers = call.kwargs["headers"]
    beta_value = headers["anthropic-beta"]
    for flag in DEFAULT_BETA_HEADERS:
        assert flag in beta_value
    assert headers["x-api-key"] == "sk-test"
    # Multipart payload present
    files = call.kwargs["files"]
    assert "skill" in files
    filename, blob, mime = files["skill"]
    assert filename == "researcher.tar.gz"
    assert mime == "application/gzip"
    assert blob.startswith(b"\x1f\x8b")  # gzip magic


@pytest.mark.asyncio
async def test_client_list_skills_unwraps_data_key() -> None:
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"data": [{"id": "a"}, {"id": "b"}]})

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "sandcastle.engine.agent_skills.httpx.AsyncClient",
        return_value=mock_client,
    ):
        client = AnthropicSkillsClient(api_key="sk-test")
        skills = await client.list_skills()

    assert skills == [{"id": "a"}, {"id": "b"}]


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_workflows_dry_run_does_not_call_upload(tmp_path) -> None:
    wf = tmp_path / "demo.yaml"
    wf.write_text(_MINIMAL_WORKFLOW, encoding="utf-8")

    client = MagicMock()
    client.upload = AsyncMock()

    results = await publish_workflows_as_skills(
        str(tmp_path), dry_run=True, client=client
    )

    assert len(results) == 1
    assert results[0]["status"] == "dry_run"
    assert results[0]["name"] == "lead-enrichment"
    client.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_workflows_uploads_when_not_dry_run(tmp_path) -> None:
    wf = tmp_path / "demo.yaml"
    wf.write_text(_MINIMAL_WORKFLOW, encoding="utf-8")

    client = MagicMock()
    client.upload = AsyncMock(return_value={"id": "skill_ok"})

    results = await publish_workflows_as_skills(
        str(tmp_path), dry_run=False, client=client
    )

    assert results[0]["status"] == "uploaded"
    assert results[0]["response"] == {"id": "skill_ok"}
    client.upload.assert_awaited_once()
