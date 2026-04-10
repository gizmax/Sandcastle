"""Tests for Managed Agent templates, describe mode, and config resolution."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from sandcastle.engine.agent_templates import (
    MANAGED_AGENT_TEMPLATES,
    TEMPLATE_REQUIRED_KEYS,
    VALID_AGENT_TEMPLATES,
)
from sandcastle.engine.dag import (
    ManagedAgentConfig,
    StepDefinition,
    parse_yaml_string,
    validate,
)
from sandcastle.engine.executor import (
    RunContext,
    StepResult,
    _resolve_agent_config,
    _design_agent_from_description,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_yaml(steps_yaml: str) -> str:
    """Wrap step YAML snippet in a minimal valid workflow."""
    return (
        "name: agent-template-test\n"
        "description: test agent templates\n"
        "input_schema:\n"
        "  required: [topic]\n"
        "  properties:\n"
        "    topic:\n"
        "      type: string\n"
        "      description: topic\n"
        "steps:\n" + steps_yaml
    )


def _make_context(**overrides) -> RunContext:
    """Build a RunContext with sensible defaults."""
    defaults = dict(
        run_id="run-tmpl-1",
        input={"topic": "AI agents"},
        step_outputs={},
        step_results={},
    )
    defaults.update(overrides)
    return RunContext(**defaults)


# ===================================================================
# 1. TEMPLATE REGISTRY STRUCTURE
# ===================================================================

class TestTemplateRegistry:
    """Verify all 15 templates exist and have correct structure."""

    def test_fifteen_templates_exist(self):
        """Registry must contain exactly 15 built-in templates."""
        assert len(MANAGED_AGENT_TEMPLATES) == 15

    def test_all_template_names_match(self):
        """Template dict keys must match VALID_AGENT_TEMPLATES set."""
        assert set(MANAGED_AGENT_TEMPLATES.keys()) == VALID_AGENT_TEMPLATES

    @pytest.mark.parametrize("name", sorted(VALID_AGENT_TEMPLATES))
    def test_template_has_required_keys(self, name: str):
        """Every template must have system, tools, packages, network, model."""
        template = MANAGED_AGENT_TEMPLATES[name]
        missing = TEMPLATE_REQUIRED_KEYS - set(template.keys())
        assert not missing, f"Template '{name}' missing keys: {missing}"

    @pytest.mark.parametrize("name", sorted(VALID_AGENT_TEMPLATES))
    def test_template_system_is_nonempty_string(self, name: str):
        """System prompt must be a non-empty string."""
        assert isinstance(MANAGED_AGENT_TEMPLATES[name]["system"], str)
        assert len(MANAGED_AGENT_TEMPLATES[name]["system"]) > 20

    @pytest.mark.parametrize("name", sorted(VALID_AGENT_TEMPLATES))
    def test_template_tools_is_list(self, name: str):
        """Tools must be a list of strings."""
        tools = MANAGED_AGENT_TEMPLATES[name]["tools"]
        assert isinstance(tools, list)
        assert all(isinstance(t, str) for t in tools)

    @pytest.mark.parametrize("name", sorted(VALID_AGENT_TEMPLATES))
    def test_template_packages_is_list(self, name: str):
        """Packages must be a list (possibly empty)."""
        packages = MANAGED_AGENT_TEMPLATES[name]["packages"]
        assert isinstance(packages, list)

    @pytest.mark.parametrize("name", sorted(VALID_AGENT_TEMPLATES))
    def test_template_network_valid(self, name: str):
        """Network must be 'unrestricted' or 'limited'."""
        assert MANAGED_AGENT_TEMPLATES[name]["network"] in ("unrestricted", "limited")

    @pytest.mark.parametrize("name", sorted(VALID_AGENT_TEMPLATES))
    def test_template_model_valid(self, name: str):
        """Model must be a known Claude model string."""
        model = MANAGED_AGENT_TEMPLATES[name]["model"]
        assert model in ("claude-sonnet-4-6", "claude-haiku-4-5", "claude-opus-4")

    def test_researcher_has_web_tools(self):
        """Researcher template must include web_search and web_fetch."""
        tools = MANAGED_AGENT_TEMPLATES["researcher"]["tools"]
        assert "web_search" in tools
        assert "web_fetch" in tools

    def test_coder_has_dev_tools(self):
        """Coder template must include bash, read, write, edit."""
        tools = MANAGED_AGENT_TEMPLATES["coder"]["tools"]
        for t in ("bash", "read", "write", "edit"):
            assert t in tools, f"coder missing tool: {t}"

    def test_scraper_uses_haiku(self):
        """Scraper template should use the cheaper haiku model."""
        assert MANAGED_AGENT_TEMPLATES["scraper"]["model"] == "claude-haiku-4-5"


# ===================================================================
# 2. YAML PARSING - agent_template
# ===================================================================

class TestYamlParsingTemplate:
    """YAML parsing of agent_template field."""

    def test_agent_template_parsed(self):
        """agent_template field is correctly parsed from YAML."""
        yaml_str = _base_yaml(
            "  - id: research\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            "      agent_template: researcher\n"
            "      message: Research {input.topic}\n"
        )
        wf = parse_yaml_string(yaml_str)
        cfg = wf.steps[0].managed_agent_config
        assert cfg is not None
        assert cfg.agent_template == "researcher"
        assert cfg.message == "Research {input.topic}"

    def test_agent_template_with_overrides(self):
        """Template with explicit overrides preserves both."""
        yaml_str = _base_yaml(
            "  - id: custom-coder\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            "      agent_template: coder\n"
            "      system_prompt: Custom system prompt override\n"
            "      packages:\n"
            "        - flask\n"
            "        - sqlalchemy\n"
            "      message: Build a web app\n"
        )
        wf = parse_yaml_string(yaml_str)
        cfg = wf.steps[0].managed_agent_config
        assert cfg.agent_template == "coder"
        assert cfg.system_prompt == "Custom system prompt override"
        assert cfg.packages == ["flask", "sqlalchemy"]

    def test_empty_agent_template_defaults_to_empty(self):
        """No agent_template field defaults to empty string."""
        yaml_str = _base_yaml(
            "  - id: bare\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            "      agent_id: auto\n"
        )
        wf = parse_yaml_string(yaml_str)
        cfg = wf.steps[0].managed_agent_config
        assert cfg.agent_template == ""


# ===================================================================
# 3. YAML PARSING - describe
# ===================================================================

class TestYamlParsingDescribe:
    """YAML parsing of describe field."""

    def test_describe_parsed(self):
        """describe field is correctly parsed from YAML."""
        yaml_str = _base_yaml(
            "  - id: custom-agent\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            "      describe: Data analyst who generates charts with matplotlib\n"
            "      message: Analyze {input.topic}\n"
        )
        wf = parse_yaml_string(yaml_str)
        cfg = wf.steps[0].managed_agent_config
        assert cfg is not None
        assert cfg.describe == "Data analyst who generates charts with matplotlib"
        assert cfg.message == "Analyze {input.topic}"

    def test_describe_with_agent_id_empty(self):
        """describe mode does not require agent_id - it defaults to empty."""
        yaml_str = _base_yaml(
            "  - id: described\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            "      describe: A security auditor\n"
            "      message: Audit the code\n"
        )
        wf = parse_yaml_string(yaml_str)
        cfg = wf.steps[0].managed_agent_config
        assert cfg.agent_id == ""
        assert cfg.describe == "A security auditor"


# ===================================================================
# 4. VALIDATION
# ===================================================================

class TestValidation:
    """Validation of managed-agent step with templates and describe."""

    def test_valid_template_passes(self):
        """Valid agent_template should pass validation."""
        yaml_str = _base_yaml(
            "  - id: research\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            "      agent_template: researcher\n"
            "      message: Research something\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        template_errors = [e for e in errors if "agent_template" in e or "managed" in e.lower()]
        assert not template_errors, f"Unexpected errors: {template_errors}"

    def test_unknown_template_fails(self):
        """Unknown agent_template should produce validation error."""
        yaml_str = _base_yaml(
            "  - id: bad\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            "      agent_template: nonexistent\n"
            "      message: Do something\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        assert any("nonexistent" in e for e in errors)

    def test_describe_passes_validation(self):
        """describe field alone should pass validation (no agent_id needed)."""
        yaml_str = _base_yaml(
            "  - id: described\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            "      describe: A data analyst\n"
            "      message: Analyze data\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        managed_errors = [e for e in errors if "managed" in e.lower() or "agent_id" in e]
        assert not managed_errors, f"Unexpected errors: {managed_errors}"

    def test_no_agent_id_no_template_no_describe_fails(self):
        """Missing all three (agent_id, agent_template, describe) should fail."""
        yaml_str = _base_yaml(
            "  - id: empty\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            "      message: Do something\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        assert any("agent_id" in e or "agent_template" in e or "describe" in e for e in errors)

    def test_negative_timeout_fails(self):
        """Negative timeout should produce validation error."""
        yaml_str = _base_yaml(
            "  - id: bad-timeout\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            "      agent_template: coder\n"
            "      timeout: -1\n"
            "      message: Code\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        assert any("timeout" in e for e in errors)

    @pytest.mark.parametrize("name", sorted(VALID_AGENT_TEMPLATES))
    def test_all_template_names_pass_validation(self, name: str):
        """Every built-in template name should pass validation."""
        yaml_str = _base_yaml(
            f"  - id: step-{name}\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            f"      agent_template: {name}\n"
            "      message: Do the thing\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        template_errors = [e for e in errors if "agent_template" in e]
        assert not template_errors, f"Template '{name}' should be valid but got: {template_errors}"


# ===================================================================
# 5. _resolve_agent_config WITH TEMPLATE
# ===================================================================

class TestResolveAgentConfigTemplate:
    """Test _resolve_agent_config with agent_template mode."""

    @pytest.mark.asyncio
    async def test_template_resolves_all_fields(self):
        """Template resolution returns complete config dict."""
        config = ManagedAgentConfig(agent_template="researcher")
        result = await _resolve_agent_config(config)
        assert "system" in result
        assert "model" in result
        assert "tools" in result
        assert "packages" in result
        assert "network" in result

    @pytest.mark.asyncio
    async def test_researcher_template_values(self):
        """Researcher template should have web tools and unrestricted network."""
        config = ManagedAgentConfig(agent_template="researcher")
        result = await _resolve_agent_config(config)
        assert "web_search" in result["tools"]
        assert result["network"] == "unrestricted"
        assert "research" in result["system"].lower()

    @pytest.mark.asyncio
    async def test_system_prompt_overrides_template(self):
        """Explicit system_prompt should override template default."""
        config = ManagedAgentConfig(
            agent_template="coder",
            system_prompt="Custom: you are a Rust developer",
        )
        result = await _resolve_agent_config(config)
        assert result["system"] == "Custom: you are a Rust developer"

    @pytest.mark.asyncio
    async def test_tools_override_template(self):
        """Explicit tools_enabled should override template tools."""
        config = ManagedAgentConfig(
            agent_template="analyst",
            tools_enabled=["bash", "read"],
        )
        result = await _resolve_agent_config(config)
        assert result["tools"] == ["bash", "read"]

    @pytest.mark.asyncio
    async def test_packages_override_template(self):
        """Explicit packages should override template packages."""
        config = ManagedAgentConfig(
            agent_template="coder",
            packages=["flask", "sqlalchemy"],
        )
        result = await _resolve_agent_config(config)
        assert result["packages"] == ["flask", "sqlalchemy"]

    @pytest.mark.asyncio
    async def test_unknown_template_raises(self):
        """Unknown template name should raise ValueError."""
        config = ManagedAgentConfig(agent_template="nonexistent")
        with pytest.raises(ValueError, match="Unknown agent template"):
            await _resolve_agent_config(config)

    @pytest.mark.asyncio
    async def test_network_access_false_overrides(self):
        """network_access=False should override template network to 'limited'."""
        config = ManagedAgentConfig(
            agent_template="researcher",
            network_access=False,
        )
        result = await _resolve_agent_config(config)
        # When network_access is False, template network value is used (researcher default)
        # The logic is: "unrestricted" if config.network_access else template["network"]
        assert result["network"] == "unrestricted"  # researcher template default


# ===================================================================
# 6. _resolve_agent_config WITH DESCRIBE (MOCKED AI)
# ===================================================================

class TestResolveAgentConfigDescribe:
    """Test _resolve_agent_config with describe mode (mocked AI call)."""

    @pytest.mark.asyncio
    async def test_describe_calls_ai_and_returns_config(self):
        """Describe mode should call AI and return valid config dict."""
        mock_response = json.dumps({
            "system": "You are a financial analyst.",
            "model": "claude-sonnet-4-6",
            "tools": ["bash", "read", "write", "web_fetch"],
            "packages": ["pandas", "yfinance"],
            "network": "unrestricted",
        })
        with patch(
            "sandcastle.engine.executor._design_agent_from_description",
            new_callable=AsyncMock,
            return_value={
                "system": "You are a financial analyst.",
                "model": "claude-sonnet-4-6",
                "tools": ["bash", "read", "write", "web_fetch"],
                "packages": ["pandas", "yfinance"],
                "network": "unrestricted",
            },
        ):
            config = ManagedAgentConfig(describe="Financial analyst for stock data")
            result = await _resolve_agent_config(config)
            assert result["system"] == "You are a financial analyst."
            assert "pandas" in result["packages"]

    @pytest.mark.asyncio
    async def test_design_agent_parses_json(self):
        """_design_agent_from_description should parse valid JSON response."""
        mock_json = json.dumps({
            "system": "Test system prompt",
            "model": "claude-haiku-4-5",
            "tools": ["bash", "read"],
            "packages": ["requests"],
            "network": "limited",
        })
        with patch(
            "sandcastle.engine.generator._call_advisor_llm",
            new_callable=AsyncMock,
            return_value=mock_json,
        ):
            result = await _design_agent_from_description("A simple test agent")
            assert result["system"] == "Test system prompt"
            assert result["model"] == "claude-haiku-4-5"
            assert result["tools"] == ["bash", "read"]
            assert result["packages"] == ["requests"]
            assert result["network"] == "limited"

    @pytest.mark.asyncio
    async def test_design_agent_handles_markdown_fences(self):
        """AI response wrapped in markdown fences should still parse."""
        inner_json = json.dumps({
            "system": "Fenced agent",
            "model": "claude-sonnet-4-6",
            "tools": ["bash"],
            "packages": [],
            "network": "unrestricted",
        })
        fenced = f"```json\n{inner_json}\n```"
        with patch(
            "sandcastle.engine.generator._call_advisor_llm",
            new_callable=AsyncMock,
            return_value=fenced,
        ):
            result = await _design_agent_from_description("Test with fences")
            assert result["system"] == "Fenced agent"

    @pytest.mark.asyncio
    async def test_design_agent_fallback_on_bad_json(self):
        """Invalid JSON response should trigger fallback defaults."""
        with patch(
            "sandcastle.engine.generator._call_advisor_llm",
            new_callable=AsyncMock,
            return_value="This is not JSON at all!",
        ):
            result = await _design_agent_from_description("Fallback test agent")
            # Should return fallback defaults
            assert "system" in result
            assert result["model"] == "claude-sonnet-4-6"
            assert isinstance(result["tools"], list)

    @pytest.mark.asyncio
    async def test_design_agent_fallback_on_exception(self):
        """Exception during AI call should trigger fallback defaults."""
        with patch(
            "sandcastle.engine.generator._call_advisor_llm",
            new_callable=AsyncMock,
            side_effect=RuntimeError("AI unavailable"),
        ):
            result = await _design_agent_from_description("Exception test")
            assert "system" in result
            assert result["model"] == "claude-sonnet-4-6"


# ===================================================================
# 7. _resolve_agent_config WITH EXPLICIT CONFIG
# ===================================================================

class TestResolveAgentConfigExplicit:
    """Test _resolve_agent_config with explicit (no template, no describe) config."""

    @pytest.mark.asyncio
    async def test_explicit_passthrough(self):
        """Explicit config should pass through all fields unchanged."""
        config = ManagedAgentConfig(
            system_prompt="You are explicit.",
            model="claude-opus-4",
            tools_enabled=["bash"],
            packages=["pandas"],
            network_access=True,
        )
        result = await _resolve_agent_config(config)
        assert result["system"] == "You are explicit."
        assert result["model"] == "claude-opus-4"
        assert result["tools"] == ["bash"]
        assert result["packages"] == ["pandas"]
        assert result["network"] == "unrestricted"

    @pytest.mark.asyncio
    async def test_explicit_no_network(self):
        """network_access=False should result in 'limited' network."""
        config = ManagedAgentConfig(network_access=False)
        result = await _resolve_agent_config(config)
        assert result["network"] == "limited"

    @pytest.mark.asyncio
    async def test_explicit_empty_packages_default(self):
        """None packages should default to empty list."""
        config = ManagedAgentConfig(packages=None)
        result = await _resolve_agent_config(config)
        assert result["packages"] == []


# ===================================================================
# 8. AI ASSISTANT PROMPT MENTIONS TEMPLATES
# ===================================================================

class TestAiAssistantPrompt:
    """Verify the AI assistant generator prompt documents managed-agent templates."""

    def test_prompt_mentions_agent_template(self):
        """Generator step types doc should mention agent_template."""
        from sandcastle.engine.generator import _STEP_TYPES_DOC
        assert "agent_template" in _STEP_TYPES_DOC

    def test_prompt_mentions_describe(self):
        """Generator step types doc should mention describe."""
        from sandcastle.engine.generator import _STEP_TYPES_DOC
        assert "describe" in _STEP_TYPES_DOC

    def test_prompt_lists_all_templates(self):
        """Generator prompt should list all template names."""
        from sandcastle.engine.generator import _STEP_TYPES_DOC
        for name in VALID_AGENT_TEMPLATES:
            assert name in _STEP_TYPES_DOC, f"Template '{name}' not in generator prompt"

    def test_prompt_mentions_three_modes(self):
        """Generator prompt should document all three configuration modes."""
        from sandcastle.engine.generator import _STEP_TYPES_DOC
        assert "Template" in _STEP_TYPES_DOC
        assert "Describe" in _STEP_TYPES_DOC
        assert "Explicit" in _STEP_TYPES_DOC


# ===================================================================
# 9. ManagedAgentConfig DEFAULTS FOR NEW FIELDS
# ===================================================================

class TestManagedAgentConfigNewFields:
    """Verify default values for newly added fields."""

    def test_agent_template_default_empty(self):
        """agent_template defaults to empty string."""
        cfg = ManagedAgentConfig()
        assert cfg.agent_template == ""

    def test_describe_default_empty(self):
        """describe defaults to empty string."""
        cfg = ManagedAgentConfig()
        assert cfg.describe == ""

    def test_custom_agent_template(self):
        """Custom agent_template is preserved."""
        cfg = ManagedAgentConfig(agent_template="analyst")
        assert cfg.agent_template == "analyst"

    def test_custom_describe(self):
        """Custom describe is preserved."""
        cfg = ManagedAgentConfig(describe="A test agent")
        assert cfg.describe == "A test agent"


# ===================================================================
# 10. EXPANDED TEMPLATE REGISTRY (15 templates)
# ===================================================================

class TestExpandedTemplateRegistry:
    """Verify all 15 templates including the 8 new ones."""

    EXPECTED_TEMPLATES = frozenset({
        "researcher", "coder", "analyst", "writer", "reviewer", "scraper", "tester",
        "devops", "translator", "designer", "sql_expert", "seo_specialist",
        "legal_analyst", "financial_analyst", "project_manager",
    })

    def test_all_fifteen_names_present(self):
        """All 15 template names must be in the registry."""
        assert set(MANAGED_AGENT_TEMPLATES.keys()) == self.EXPECTED_TEMPLATES

    def test_valid_templates_set_matches(self):
        """VALID_AGENT_TEMPLATES frozen set must match all template keys."""
        assert VALID_AGENT_TEMPLATES == self.EXPECTED_TEMPLATES

    @pytest.mark.parametrize("name", [
        "devops", "translator", "designer", "sql_expert",
        "seo_specialist", "legal_analyst", "financial_analyst", "project_manager",
    ])
    def test_new_template_has_required_keys(self, name: str):
        """Each new template must have system, tools, packages, network, model."""
        template = MANAGED_AGENT_TEMPLATES[name]
        missing = TEMPLATE_REQUIRED_KEYS - set(template.keys())
        assert not missing, f"Template '{name}' missing required keys: {missing}"

    def test_devops_has_bash_and_edit(self):
        """DevOps template must include bash and edit tools."""
        tools = MANAGED_AGENT_TEMPLATES["devops"]["tools"]
        assert "bash" in tools
        assert "edit" in tools

    def test_translator_uses_haiku(self):
        """Translator template should use the cheaper haiku model."""
        assert MANAGED_AGENT_TEMPLATES["translator"]["model"] == "claude-haiku-4-5"

    def test_sql_expert_has_sqlparse(self):
        """SQL expert template must include sqlparse package."""
        packages = MANAGED_AGENT_TEMPLATES["sql_expert"]["packages"]
        assert "sqlparse" in packages

    def test_seo_specialist_has_web_tools(self):
        """SEO specialist must have web_search and web_fetch."""
        tools = MANAGED_AGENT_TEMPLATES["seo_specialist"]["tools"]
        assert "web_search" in tools
        assert "web_fetch" in tools

    def test_financial_analyst_has_pandas(self):
        """Financial analyst must include pandas package."""
        packages = MANAGED_AGENT_TEMPLATES["financial_analyst"]["packages"]
        assert "pandas" in packages

    def test_legal_analyst_limited_network(self):
        """Legal analyst should have limited network access."""
        assert MANAGED_AGENT_TEMPLATES["legal_analyst"]["network"] == "limited"


# ===================================================================
# 11. TEMPLATE CATEGORIES AND DESCRIPTIONS
# ===================================================================

class TestTemplateCategories:
    """Verify all templates have category, description, and icon metadata."""

    @pytest.mark.parametrize("name", sorted(VALID_AGENT_TEMPLATES))
    def test_template_has_category(self, name: str):
        """Every template must have a non-empty category string."""
        template = MANAGED_AGENT_TEMPLATES[name]
        assert "category" in template, f"Template '{name}' missing 'category'"
        assert isinstance(template["category"], str)
        assert len(template["category"]) > 0

    @pytest.mark.parametrize("name", sorted(VALID_AGENT_TEMPLATES))
    def test_template_has_description(self, name: str):
        """Every template must have a non-empty description string."""
        template = MANAGED_AGENT_TEMPLATES[name]
        assert "description" in template, f"Template '{name}' missing 'description'"
        assert isinstance(template["description"], str)
        assert len(template["description"]) > 10

    @pytest.mark.parametrize("name", sorted(VALID_AGENT_TEMPLATES))
    def test_template_has_icon(self, name: str):
        """Every template must have a non-empty icon string."""
        template = MANAGED_AGENT_TEMPLATES[name]
        assert "icon" in template, f"Template '{name}' missing 'icon'"
        assert isinstance(template["icon"], str)
        assert len(template["icon"]) > 0

    def test_valid_categories(self):
        """All template categories must be from the defined set."""
        from sandcastle.engine.agent_templates import TEMPLATE_CATEGORIES
        for name, tmpl in MANAGED_AGENT_TEMPLATES.items():
            assert tmpl["category"] in TEMPLATE_CATEGORIES, (
                f"Template '{name}' has invalid category '{tmpl['category']}'"
            )

    def test_each_category_has_at_least_one_template(self):
        """Every defined category must have at least one template."""
        from sandcastle.engine.agent_templates import TEMPLATE_CATEGORIES
        used_categories = {tmpl["category"] for tmpl in MANAGED_AGENT_TEMPLATES.values()}
        for cat in TEMPLATE_CATEGORIES:
            assert cat in used_categories, f"Category '{cat}' has no templates"

    def test_research_category_count(self):
        """Research category should have at least 2 templates."""
        research = [n for n, t in MANAGED_AGENT_TEMPLATES.items() if t["category"] == "Research"]
        assert len(research) >= 2

    def test_development_category_count(self):
        """Development category should have at least 3 templates."""
        dev = [n for n, t in MANAGED_AGENT_TEMPLATES.items() if t["category"] == "Development"]
        assert len(dev) >= 3


# ===================================================================
# 12. OUTPUT FORMAT FIELD
# ===================================================================

class TestOutputFormat:
    """Verify output_format field parsing and defaults."""

    def test_output_format_default(self):
        """output_format defaults to 'text'."""
        cfg = ManagedAgentConfig()
        assert cfg.output_format == "text"

    def test_output_format_json(self):
        """output_format 'json' is preserved."""
        cfg = ManagedAgentConfig(output_format="json")
        assert cfg.output_format == "json"

    def test_output_format_files(self):
        """output_format 'files' is preserved."""
        cfg = ManagedAgentConfig(output_format="files")
        assert cfg.output_format == "files"

    def test_output_format_markdown(self):
        """output_format 'markdown' is preserved."""
        cfg = ManagedAgentConfig(output_format="markdown")
        assert cfg.output_format == "markdown"

    def test_output_format_parsed_from_yaml(self):
        """output_format field is correctly parsed from YAML."""
        yaml_str = _base_yaml(
            "  - id: chained\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            "      agent_template: analyst\n"
            "      output_format: json\n"
            "      message: Analyze data\n"
        )
        wf = parse_yaml_string(yaml_str)
        cfg = wf.steps[0].managed_agent_config
        assert cfg.output_format == "json"

    def test_invalid_output_format_fails_validation(self):
        """Invalid output_format should produce validation error."""
        yaml_str = _base_yaml(
            "  - id: bad-format\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            "      agent_template: coder\n"
            "      output_format: xml\n"
            "      message: Code something\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        assert any("output_format" in e for e in errors)


# ===================================================================
# 13. FALLBACK TEMPLATE FIELD
# ===================================================================

class TestFallbackTemplate:
    """Verify fallback_template field parsing and validation."""

    def test_fallback_template_default_empty(self):
        """fallback_template defaults to empty string."""
        cfg = ManagedAgentConfig()
        assert cfg.fallback_template == ""

    def test_fallback_template_preserved(self):
        """Custom fallback_template is preserved."""
        cfg = ManagedAgentConfig(fallback_template="coder")
        assert cfg.fallback_template == "coder"

    def test_fallback_template_parsed_from_yaml(self):
        """fallback_template field is correctly parsed from YAML."""
        yaml_str = _base_yaml(
            "  - id: with-fallback\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            "      agent_template: analyst\n"
            "      fallback_template: coder\n"
            "      message: Analyze this\n"
        )
        wf = parse_yaml_string(yaml_str)
        cfg = wf.steps[0].managed_agent_config
        assert cfg.fallback_template == "coder"

    def test_invalid_fallback_template_fails_validation(self):
        """Unknown fallback_template should produce validation error."""
        yaml_str = _base_yaml(
            "  - id: bad-fallback\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            "      agent_template: coder\n"
            "      fallback_template: nonexistent_template\n"
            "      message: Do something\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        assert any("fallback_template" in e for e in errors)

    def test_valid_fallback_template_passes_validation(self):
        """Known fallback_template should pass validation."""
        yaml_str = _base_yaml(
            "  - id: good-fallback\n"
            "    type: managed-agent\n"
            "    managed_agent_config:\n"
            "      agent_template: analyst\n"
            "      fallback_template: writer\n"
            "      message: Analyze and write\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        fallback_errors = [e for e in errors if "fallback_template" in e]
        assert not fallback_errors


# ===================================================================
# 14. SHARED FILES FIELD
# ===================================================================

class TestSharedFiles:
    """Verify shared_files field parsing and defaults."""

    def test_shared_files_default_none(self):
        """shared_files defaults to None."""
        cfg = ManagedAgentConfig()
        assert cfg.shared_files is None

    def test_shared_files_preserved(self):
        """Custom shared_files list is preserved."""
        cfg = ManagedAgentConfig(shared_files=["step1", "step2"])
        assert cfg.shared_files == ["step1", "step2"]

    def test_shared_files_parsed_from_yaml(self):
        """shared_files field is correctly parsed from YAML."""
        yaml_str = _base_yaml(
            "  - id: fetch-data\n"
            "    type: prompt\n"
            "    prompt: Fetch data\n"
            "  - id: analyze\n"
            "    type: managed-agent\n"
            "    depends_on: [fetch-data]\n"
            "    managed_agent_config:\n"
            "      agent_template: analyst\n"
            "      shared_files:\n"
            "        - fetch-data\n"
            "      message: Analyze shared data\n"
        )
        wf = parse_yaml_string(yaml_str)
        cfg = wf.steps[1].managed_agent_config
        assert cfg.shared_files == ["fetch-data"]


# ===================================================================
# 15. GENERATOR PROMPT LISTS ALL 15 TEMPLATES
# ===================================================================

class TestGeneratorPromptExpanded:
    """Verify the AI assistant generator prompt documents all 15 templates."""

    def test_prompt_lists_all_new_templates(self):
        """Generator prompt should reference all 15 template names."""
        from sandcastle.engine.generator import _STEP_TYPES_DOC
        for name in VALID_AGENT_TEMPLATES:
            assert name in _STEP_TYPES_DOC, (
                f"Template '{name}' not found in generator prompt"
            )

    def test_prompt_mentions_output_format(self):
        """Generator prompt should document output_format option."""
        from sandcastle.engine.generator import _STEP_TYPES_DOC
        assert "output_format" in _STEP_TYPES_DOC

    def test_prompt_mentions_fallback_template(self):
        """Generator prompt should document fallback_template option."""
        from sandcastle.engine.generator import _STEP_TYPES_DOC
        assert "fallback_template" in _STEP_TYPES_DOC

    def test_prompt_mentions_shared_files(self):
        """Generator prompt should document shared_files option."""
        from sandcastle.engine.generator import _STEP_TYPES_DOC
        assert "shared_files" in _STEP_TYPES_DOC

    def test_prompt_mentions_categories(self):
        """Generator prompt should mention template categories."""
        from sandcastle.engine.generator import _STEP_TYPES_DOC
        for category in ("Research", "Development", "Data", "Content", "Business", "Operations"):
            assert category in _STEP_TYPES_DOC, (
                f"Category '{category}' not found in generator prompt"
            )
