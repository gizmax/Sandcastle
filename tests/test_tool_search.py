"""Tests for the tool search + tool use examples registry."""

from __future__ import annotations

import pytest

from sandcastle.engine.tool_search import (
    ToolDefinition,
    ToolRegistry,
    validate_tool,
)


# ---------------------------------------------------------------- helpers


def _make_tool(
    name: str,
    description: str | None = None,
    tags: list[str] | None = None,
    defer_loading: bool = False,
    examples: list[dict] | None = None,
    parameters: dict | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description or f"{name} is a sample tool used for exercises in tests.",
        parameters=parameters
        or {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        examples=examples
        or [
            {"input": {"query": "hello"}, "output": {"result": "world"}},
        ],
        defer_loading=defer_loading,
        tags=tags or [],
    )


# ---------------------------------------------------------------- search


def test_search_exact_name_match_ranks_first():
    reg = ToolRegistry()
    reg.register(_make_tool("send_email", tags=["email", "smtp"]))
    reg.register(_make_tool("send_slack_message", tags=["slack", "chat"]))

    hits = reg.search("send_email")
    assert hits[0].name == "send_email"


def test_search_exact_name_beats_partial_token_overlap():
    reg = ToolRegistry()
    # Description mentions "email" many times but name does not match exactly.
    reg.register(
        _make_tool(
            "noisy_helper",
            description="email email email helper for email workflows over email.",
        )
    )
    reg.register(_make_tool("email", tags=["mail"]))

    hits = reg.search("email")
    assert hits[0].name == "email"


def test_search_tag_overlap_ranks_multiple_tools():
    reg = ToolRegistry()
    reg.register(_make_tool("alpha", tags=["pdf", "ocr"]))
    reg.register(_make_tool("beta", tags=["pdf"]))
    reg.register(_make_tool("gamma", tags=["audio"]))

    hits = reg.search("pdf ocr")
    names = [t.name for t in hits]
    assert "alpha" in names and "beta" in names
    assert names.index("alpha") < names.index("beta")
    assert "gamma" not in names


def test_search_ranks_by_token_overlap_in_tags():
    reg = ToolRegistry()
    reg.register(_make_tool("one", tags=["report"]))
    reg.register(_make_tool("two", tags=["report", "pdf", "charts"]))

    hits = reg.search("report pdf charts")
    assert hits[0].name == "two"


# ---------------------------------------------------------------- partitions


def test_hot_tools_excludes_deferred():
    reg = ToolRegistry()
    reg.register(_make_tool("fast"))
    reg.register(_make_tool("rare", defer_loading=True))

    hot = [t.name for t in reg.hot_tools()]
    assert hot == ["fast"]


def test_lazy_tools_only_includes_deferred():
    reg = ToolRegistry()
    reg.register(_make_tool("fast"))
    reg.register(_make_tool("rare", defer_loading=True))
    reg.register(_make_tool("rare2", defer_loading=True))

    lazy = sorted(t.name for t in reg.lazy_tools())
    assert lazy == ["rare", "rare2"]


# ---------------------------------------------------------------- formatting


def test_format_for_agent_produces_anthropic_shape():
    reg = ToolRegistry()
    reg.register(_make_tool("search_web"))

    out = ToolRegistry.format_for_agent(reg.all())
    assert len(out) == 1
    entry = out[0]
    assert entry["name"] == "search_web"
    assert "description" in entry
    assert entry["input_schema"]["type"] == "object"
    assert isinstance(entry["examples"], list)
    assert entry["examples"][0]["input"] == {"query": "hello"}


def test_format_for_agent_omits_examples_key_when_none_present():
    tool = ToolDefinition(
        name="bare",
        description="A bare tool with no worked examples baked in at all.",
        parameters={"type": "object", "properties": {}},
        examples=[],
    )
    out = ToolRegistry.format_for_agent([tool])
    assert out[0]["name"] == "bare"
    assert "examples" not in out[0]


# ---------------------------------------------------------------- validation


def test_validate_rejects_zero_examples():
    tool = ToolDefinition(
        name="t",
        description="A tool with a sufficiently long description for the validator.",
        parameters={"type": "object", "properties": {}},
        examples=[],
    )
    errors = validate_tool(tool)
    assert any("1 to 5" in e for e in errors)


def test_validate_rejects_more_than_five_examples():
    tool = _make_tool(
        "t",
        examples=[
            {"input": {"query": f"q{i}"}, "output": {"r": i}} for i in range(6)
        ],
    )
    errors = validate_tool(tool)
    assert any("max is 5" in e for e in errors)


def test_validate_rejects_input_that_violates_parameters_schema():
    tool = _make_tool(
        "t",
        examples=[
            {"input": {"query": 123}, "output": {"r": "ok"}},  # query must be str
        ],
    )
    errors = validate_tool(tool)
    assert any("fails parameters schema" in e for e in errors)


def test_validate_rejects_too_short_description():
    tool = _make_tool("t", description="short")
    errors = validate_tool(tool)
    assert any("20 characters" in e for e in errors)


def test_validate_passes_well_formed_tool():
    tool = _make_tool("good")
    assert validate_tool(tool) == []


# ---------------------------------------------------------------- misc


def test_search_returns_empty_when_registry_empty():
    reg = ToolRegistry()
    assert reg.search("anything") == []


def test_search_limit_is_respected():
    reg = ToolRegistry()
    for i in range(10):
        reg.register(_make_tool(f"tool_{i}", tags=["common"]))

    hits = reg.search("common", limit=3)
    assert len(hits) == 3


def test_register_replaces_existing_tool_with_same_name():
    reg = ToolRegistry()
    reg.register(_make_tool("dup", description="first version of the tool, long enough."))
    reg.register(_make_tool("dup", description="second version of the tool, long enough."))
    assert len(reg) == 1
    assert reg.get("dup").description.startswith("second")


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
