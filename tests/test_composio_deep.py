"""Deep tests for the Composio step type: YAML parsing, validation,
implicit dependency inference, deep template resolution, executor, and
membership in NON_PROMPT_TYPES / NON_LLM_TYPES."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from sandcastle.engine.dag import (
    ComposioConfig,
    NON_LLM_TYPES,
    NON_PROMPT_TYPES,
    StepDefinition,
    VALID_STEP_TYPES,
    WorkflowDefinition,
    build_plan,
    parse_yaml_string,
    validate,
)
from sandcastle.engine import executor as _executor_mod
from sandcastle.engine.executor import (
    RunContext,
    StepResult,
    resolve_templates,
)

_execute_composio_step = _executor_mod._execute_composio_step
_resolve_params_deep = _executor_mod._resolve_params_deep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_yaml(steps_yaml: str) -> str:
    """Wrap step YAML snippet in a minimal valid workflow."""
    return (
        "name: composio-test\n"
        "description: test composio step\n"
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
        run_id="run-1",
        input={"topic": "test"},
        step_outputs={},
        step_results={},
    )
    defaults.update(overrides)
    return RunContext(**defaults)


# ===================================================================
# 1. YAML PARSING - all config combinations
# ===================================================================

class TestYamlParsing:
    """YAML parsing of composio_config in all valid combinations."""

    def test_action_only(self):
        yaml_str = _base_yaml(
            "  - id: send\n"
            "    type: composio\n"
            "    composio_config:\n"
            "      action: gmail_send_email\n"
        )
        wf = parse_yaml_string(yaml_str)
        step = wf.steps[0]
        assert step.type == "composio"
        assert step.composio_config is not None
        assert step.composio_config.action == "gmail_send_email"
        assert step.composio_config.params == {}
        assert step.composio_config.connected_account_id == ""
        assert step.composio_config.app == ""

    def test_action_with_params(self):
        yaml_str = _base_yaml(
            "  - id: send\n"
            "    type: composio\n"
            "    composio_config:\n"
            "      action: gmail_send_email\n"
            "      params:\n"
            "        to: user@example.com\n"
            "        subject: Hello\n"
            "        body: World\n"
        )
        wf = parse_yaml_string(yaml_str)
        cfg = wf.steps[0].composio_config
        assert cfg.action == "gmail_send_email"
        assert cfg.params == {"to": "user@example.com", "subject": "Hello", "body": "World"}

    def test_full_config(self):
        yaml_str = _base_yaml(
            "  - id: create-issue\n"
            "    type: composio\n"
            "    composio_config:\n"
            "      action: github_create_issue\n"
            "      params:\n"
            "        title: Bug report\n"
            "        body: Details here\n"
            "      app: github\n"
            "      connected_account_id: acc-123\n"
        )
        wf = parse_yaml_string(yaml_str)
        cfg = wf.steps[0].composio_config
        assert cfg.action == "github_create_issue"
        assert cfg.params == {"title": "Bug report", "body": "Details here"}
        assert cfg.app == "github"
        assert cfg.connected_account_id == "acc-123"

    def test_missing_action_parses_empty(self):
        """Parsing with missing action should work (validation catches it)."""
        yaml_str = _base_yaml(
            "  - id: bad\n"
            "    type: composio\n"
            "    composio_config:\n"
            "      params:\n"
            "        key: value\n"
        )
        wf = parse_yaml_string(yaml_str)
        cfg = wf.steps[0].composio_config
        assert cfg is not None
        assert cfg.action == ""

    def test_empty_composio_config(self):
        """Empty composio_config should parse to a ComposioConfig with defaults."""
        yaml_str = _base_yaml(
            "  - id: empty\n"
            "    type: composio\n"
            "    composio_config: {}\n"
        )
        wf = parse_yaml_string(yaml_str)
        cfg = wf.steps[0].composio_config
        assert cfg is not None
        assert cfg.action == ""
        assert cfg.params == {}

    def test_no_composio_config_key(self):
        """Type composio without composio_config key => composio_config is None."""
        yaml_str = _base_yaml(
            "  - id: bare\n"
            "    type: composio\n"
        )
        wf = parse_yaml_string(yaml_str)
        assert wf.steps[0].composio_config is None

    def test_nested_params(self):
        """Nested dict params should be preserved during parsing."""
        yaml_str = _base_yaml(
            "  - id: nested\n"
            "    type: composio\n"
            "    composio_config:\n"
            "      action: slack_send\n"
            "      params:\n"
            "        message:\n"
            "          text: hello\n"
            "          channel: general\n"
            "        metadata:\n"
            "          priority: high\n"
        )
        wf = parse_yaml_string(yaml_str)
        cfg = wf.steps[0].composio_config
        assert cfg.params["message"]["text"] == "hello"
        assert cfg.params["metadata"]["priority"] == "high"

    def test_params_with_template_variables(self):
        """Template variables in params should survive parsing as strings."""
        yaml_str = _base_yaml(
            "  - id: fetch\n"
            "    type: composio\n"
            "    composio_config:\n"
            "      action: gmail_send_email\n"
            "      params:\n"
            "        body: \"{steps.writer.output}\"\n"
        )
        wf = parse_yaml_string(yaml_str)
        cfg = wf.steps[0].composio_config
        assert cfg.params["body"] == "{steps.writer.output}"

    def test_composio_type_in_valid_step_types(self):
        assert "composio" in VALID_STEP_TYPES

    def test_non_prompt_type_gets_placeholder(self):
        """Composio steps without a prompt should get a placeholder."""
        yaml_str = _base_yaml(
            "  - id: send\n"
            "    type: composio\n"
            "    composio_config:\n"
            "      action: gmail_send_email\n"
        )
        wf = parse_yaml_string(yaml_str)
        step = wf.steps[0]
        assert step.prompt != ""  # placeholder injected


# ===================================================================
# 2. VALIDATION
# ===================================================================

class TestValidation:
    """validate() checks for composio steps."""

    def test_composio_without_composio_config_fails(self):
        yaml_str = _base_yaml(
            "  - id: bad\n"
            "    type: composio\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        assert any("composio_config" in e.lower() or "action" in e.lower() for e in errors), (
            f"Expected composio validation error, got: {errors}"
        )

    def test_composio_without_action_fails(self):
        yaml_str = _base_yaml(
            "  - id: bad\n"
            "    type: composio\n"
            "    composio_config:\n"
            "      params:\n"
            "        key: value\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        assert any("action" in e.lower() for e in errors), (
            f"Expected action validation error, got: {errors}"
        )

    def test_composio_empty_config_fails(self):
        yaml_str = _base_yaml(
            "  - id: bad\n"
            "    type: composio\n"
            "    composio_config: {}\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        assert any("action" in e.lower() for e in errors)

    def test_valid_composio_passes(self):
        yaml_str = _base_yaml(
            "  - id: send\n"
            "    type: composio\n"
            "    composio_config:\n"
            "      action: gmail_send_email\n"
            "      params:\n"
            "        to: user@example.com\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        assert errors == [], f"Unexpected validation errors: {errors}"

    def test_valid_composio_action_only_passes(self):
        yaml_str = _base_yaml(
            "  - id: send\n"
            "    type: composio\n"
            "    composio_config:\n"
            "      action: github_star_repo\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        assert errors == [], f"Unexpected validation errors: {errors}"

    def test_composio_does_not_require_prompt(self):
        """Composio steps should NOT produce a 'missing prompt' error."""
        yaml_str = _base_yaml(
            "  - id: send\n"
            "    type: composio\n"
            "    composio_config:\n"
            "      action: gmail_send_email\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        prompt_errors = [e for e in errors if "prompt" in e.lower()]
        assert prompt_errors == [], f"Composio should not require prompt, got: {prompt_errors}"


# ===================================================================
# 3. IMPLICIT DEPENDENCY INFERENCE
# ===================================================================

class TestImplicitDependencies:
    """build_plan() should infer dependencies from composio_config template vars."""

    def test_params_create_implicit_dep(self):
        """composio_config.params referencing {steps.X.output} should create deps."""
        yaml_str = _base_yaml(
            "  - id: writer\n"
            "    prompt: write something\n"
            "  - id: send\n"
            "    type: composio\n"
            "    composio_config:\n"
            "      action: gmail_send_email\n"
            "      params:\n"
            "        body: \"{steps.writer.output}\"\n"
        )
        wf = parse_yaml_string(yaml_str)
        plan = build_plan(wf)
        # writer should come before send
        writer_stage = next(i for i, s in enumerate(plan.stages) if "writer" in s)
        send_stage = next(i for i, s in enumerate(plan.stages) if "send" in s)
        assert writer_stage < send_stage, (
            f"writer (stage {writer_stage}) should come before send (stage {send_stage})"
        )

    def test_connected_account_id_implicit_dep(self):
        """connected_account_id referencing {steps.X.output} should create deps."""
        yaml_str = _base_yaml(
            "  - id: auth\n"
            "    prompt: get auth\n"
            "  - id: send\n"
            "    type: composio\n"
            "    composio_config:\n"
            "      action: gmail_send_email\n"
            "      connected_account_id: \"{steps.auth.output}\"\n"
        )
        wf = parse_yaml_string(yaml_str)
        plan = build_plan(wf)
        auth_stage = next(i for i, s in enumerate(plan.stages) if "auth" in s)
        send_stage = next(i for i, s in enumerate(plan.stages) if "send" in s)
        assert auth_stage < send_stage

    def test_no_ref_no_dep(self):
        """Composio step without step refs should be independent."""
        yaml_str = _base_yaml(
            "  - id: writer\n"
            "    prompt: write something\n"
            "  - id: send\n"
            "    type: composio\n"
            "    composio_config:\n"
            "      action: gmail_send_email\n"
            "      params:\n"
            "        body: hello world\n"
        )
        wf = parse_yaml_string(yaml_str)
        plan = build_plan(wf)
        # Both should be in stage 0 (parallel)
        assert len(plan.stages) == 1, (
            f"Expected 1 stage (parallel), got {len(plan.stages)}: {plan.stages}"
        )

    def test_nested_param_ref_creates_dep(self):
        """Deeply nested params with {steps.X.output} should create deps."""
        yaml_str = _base_yaml(
            "  - id: lookup\n"
            "    prompt: look up email\n"
            "  - id: send\n"
            "    type: composio\n"
            "    composio_config:\n"
            "      action: gmail_send_email\n"
            "      params:\n"
            "        to:\n"
            "          email: \"{steps.lookup.output.email}\"\n"
        )
        wf = parse_yaml_string(yaml_str)
        plan = build_plan(wf)
        lookup_stage = next(i for i, s in enumerate(plan.stages) if "lookup" in s)
        send_stage = next(i for i, s in enumerate(plan.stages) if "send" in s)
        assert lookup_stage < send_stage

    def test_three_step_chain(self):
        """A -> B (composio refs A) -> C (composio refs B) should be 3 stages."""
        yaml_str = _base_yaml(
            "  - id: step-a\n"
            "    prompt: first step\n"
            "  - id: step-b\n"
            "    type: composio\n"
            "    composio_config:\n"
            "      action: slack_send\n"
            "      params:\n"
            "        text: \"{steps.step-a.output}\"\n"
            "  - id: step-c\n"
            "    type: composio\n"
            "    composio_config:\n"
            "      action: gmail_send_email\n"
            "      params:\n"
            "        body: \"{steps.step-b.output}\"\n"
        )
        wf = parse_yaml_string(yaml_str)
        plan = build_plan(wf)
        assert len(plan.stages) == 3
        assert plan.stages[0] == ["step-a"]
        assert plan.stages[1] == ["step-b"]
        assert plan.stages[2] == ["step-c"]


# ===================================================================
# 4. DEEP TEMPLATE RESOLUTION
# ===================================================================

class TestDeepTemplateResolution:
    """_resolve_params_deep should recursively resolve template variables."""

    def test_flat_string_resolution(self):
        ctx = _make_context(
            step_outputs={"writer": "Hello from writer"},
        )
        result = _resolve_params_deep(
            {"body": "{steps.writer.output}"},
            ctx,
        )
        assert "Hello from writer" in result["body"]

    def test_nested_dict_resolution(self):
        ctx = _make_context(
            step_outputs={"lookup": {"email": "user@example.com"}},
        )
        result = _resolve_params_deep(
            {"to": {"email": "{steps.lookup.output.email}"}},
            ctx,
        )
        assert "user@example.com" in result["to"]["email"]

    def test_list_resolution(self):
        ctx = _make_context(
            step_outputs={"gen": "tag-value"},
        )
        result = _resolve_params_deep(
            {"tags": ["{steps.gen.output}", "static"]},
            ctx,
        )
        assert "tag-value" in result["tags"][0]
        assert result["tags"][1] == "static"

    def test_mixed_nesting(self):
        ctx = _make_context(
            step_outputs={"a": "val-a", "b": {"x": "val-bx"}},
        )
        result = _resolve_params_deep(
            {
                "level1": "{steps.a.output}",
                "nested": {
                    "level2": "{steps.b.output.x}",
                    "list": ["{steps.a.output}"],
                },
            },
            ctx,
        )
        assert "val-a" in result["level1"]
        assert "val-bx" in result["nested"]["level2"]
        assert "val-a" in result["nested"]["list"][0]

    def test_non_string_passthrough(self):
        ctx = _make_context()
        assert _resolve_params_deep(42, ctx) == 42
        assert _resolve_params_deep(True, ctx) is True
        assert _resolve_params_deep(None, ctx) is None

    def test_input_variable_resolution(self):
        ctx = _make_context(input={"topic": "AI workflows"})
        result = _resolve_params_deep(
            {"subject": "About {input.topic}"},
            ctx,
        )
        assert "AI workflows" in result["subject"]


# ===================================================================
# 5. EXECUTOR - _execute_composio_step
# ===================================================================

class TestExecutor:
    """Tests for the composio step executor."""

    @pytest.mark.asyncio
    async def test_missing_config(self):
        step = StepDefinition(id="s1", type="composio", composio_config=None)
        ctx = _make_context()
        result = await _execute_composio_step(step, ctx)
        assert result.status == "failed"
        assert "composio_config" in result.error.lower()

    @pytest.mark.asyncio
    async def test_missing_action(self):
        step = StepDefinition(
            id="s1",
            type="composio",
            composio_config=ComposioConfig(action=""),
        )
        ctx = _make_context()
        result = await _execute_composio_step(step, ctx)
        assert result.status == "failed"
        assert "action" in result.error.lower()

    @pytest.mark.asyncio
    async def test_missing_api_key(self):
        step = StepDefinition(
            id="s1",
            type="composio",
            composio_config=ComposioConfig(action="gmail_send_email"),
        )
        ctx = _make_context()
        with patch.dict("os.environ", {}, clear=True):
            import os
            os.environ.pop("TOOL_COMPOSIO_API_KEY", None)
            result = await _execute_composio_step(step, ctx)
        assert result.status == "failed"
        assert "TOOL_COMPOSIO_API_KEY" in result.error

    @pytest.mark.asyncio
    async def test_successful_mock_call(self):
        step = StepDefinition(
            id="send-email",
            type="composio",
            composio_config=ComposioConfig(
                action="gmail_send_email",
                params={"to": "a@b.com", "subject": "Hi", "body": "Hello"},
                connected_account_id="acc-1",
                app="gmail",
            ),
        )
        ctx = _make_context()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {"messageId": "msg-123", "status": "sent"},
        }
        mock_response.raise_for_status = MagicMock()

        async def mock_post(*args, **kwargs):
            return mock_response

        with patch.dict("os.environ", {"TOOL_COMPOSIO_API_KEY": "test-key"}):
            with patch("httpx.AsyncClient") as MockClient:
                mock_client_instance = AsyncMock()
                mock_client_instance.post = mock_post
                mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
                mock_client_instance.__aexit__ = AsyncMock(return_value=False)
                MockClient.return_value = mock_client_instance

                result = await _execute_composio_step(step, ctx)

        assert result.status == "completed"
        assert result.output == {"messageId": "msg-123", "status": "sent"}
        assert result.error is None
        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_api_error_handling(self):
        step = StepDefinition(
            id="s1",
            type="composio",
            composio_config=ComposioConfig(action="gmail_send_email"),
        )
        ctx = _make_context()

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        async def mock_post(*args, **kwargs):
            raise httpx.HTTPStatusError(
                "500 error",
                request=httpx.Request("POST", "https://test.com"),
                response=mock_response,
            )

        with patch.dict("os.environ", {"TOOL_COMPOSIO_API_KEY": "test-key"}):
            with patch("httpx.AsyncClient") as MockClient:
                mock_client_instance = AsyncMock()
                mock_client_instance.post = mock_post
                mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
                mock_client_instance.__aexit__ = AsyncMock(return_value=False)
                MockClient.return_value = mock_client_instance

                result = await _execute_composio_step(step, ctx)

        assert result.status == "failed"
        assert "500" in result.error
        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_composio_level_error(self):
        """Composio API returns 200 but with an error field in the response."""
        step = StepDefinition(
            id="s1",
            type="composio",
            composio_config=ComposioConfig(action="gmail_send_email"),
        )
        ctx = _make_context()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "error": "Invalid connected account",
            "data": None,
        }
        mock_response.raise_for_status = MagicMock()

        async def mock_post(*args, **kwargs):
            return mock_response

        with patch.dict("os.environ", {"TOOL_COMPOSIO_API_KEY": "test-key"}):
            with patch("httpx.AsyncClient") as MockClient:
                mock_client_instance = AsyncMock()
                mock_client_instance.post = mock_post
                mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
                mock_client_instance.__aexit__ = AsyncMock(return_value=False)
                MockClient.return_value = mock_client_instance

                result = await _execute_composio_step(step, ctx)

        assert result.status == "failed"
        assert "Invalid connected account" in result.error

    @pytest.mark.asyncio
    async def test_generic_exception_handling(self):
        step = StepDefinition(
            id="s1",
            type="composio",
            composio_config=ComposioConfig(action="gmail_send_email"),
        )
        ctx = _make_context()

        async def mock_post(*args, **kwargs):
            raise ConnectionError("Network unreachable")

        with patch.dict("os.environ", {"TOOL_COMPOSIO_API_KEY": "test-key"}):
            with patch("httpx.AsyncClient") as MockClient:
                mock_client_instance = AsyncMock()
                mock_client_instance.post = mock_post
                mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
                mock_client_instance.__aexit__ = AsyncMock(return_value=False)
                MockClient.return_value = mock_client_instance

                result = await _execute_composio_step(step, ctx)

        assert result.status == "failed"
        assert "Network unreachable" in result.error

    @pytest.mark.asyncio
    async def test_payload_structure(self):
        """Verify the payload sent to Composio API has the correct structure."""
        step = StepDefinition(
            id="s1",
            type="composio",
            composio_config=ComposioConfig(
                action="github_create_issue",
                params={"title": "Bug", "body": "Details"},
                connected_account_id="acc-42",
                app="github",
            ),
        )
        ctx = _make_context()

        captured_payload = {}
        captured_headers = {}

        mock_response = MagicMock()
        mock_response.json.return_value = {"data": {"id": 1}}
        mock_response.raise_for_status = MagicMock()

        async def mock_post(url, json=None, headers=None):
            captured_payload.update(json or {})
            captured_headers.update(headers or {})
            return mock_response

        with patch.dict("os.environ", {"TOOL_COMPOSIO_API_KEY": "my-api-key"}):
            with patch("httpx.AsyncClient") as MockClient:
                mock_client_instance = AsyncMock()
                mock_client_instance.post = mock_post
                mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
                mock_client_instance.__aexit__ = AsyncMock(return_value=False)
                MockClient.return_value = mock_client_instance

                await _execute_composio_step(step, ctx)

        assert captured_payload["actionName"] == "github_create_issue"
        assert captured_payload["input"] == {"title": "Bug", "body": "Details"}
        assert captured_payload["connectedAccountId"] == "acc-42"
        assert captured_payload["appName"] == "github"
        assert captured_headers["X-API-Key"] == "my-api-key"

    @pytest.mark.asyncio
    async def test_template_resolution_in_executor(self):
        """Executor should resolve template variables in action and params."""
        step = StepDefinition(
            id="send",
            type="composio",
            composio_config=ComposioConfig(
                action="gmail_send_email",
                params={
                    "to": "{steps.lookup.output.email}",
                    "body": "{steps.writer.output}",
                },
            ),
        )
        ctx = _make_context(
            step_outputs={
                "lookup": {"email": "resolved@test.com"},
                "writer": "The email body text",
            },
        )

        captured_payload = {}

        mock_response = MagicMock()
        mock_response.json.return_value = {"data": {"sent": True}}
        mock_response.raise_for_status = MagicMock()

        async def mock_post(url, json=None, headers=None):
            captured_payload.update(json or {})
            return mock_response

        with patch.dict("os.environ", {"TOOL_COMPOSIO_API_KEY": "key"}):
            with patch("httpx.AsyncClient") as MockClient:
                mock_client_instance = AsyncMock()
                mock_client_instance.post = mock_post
                mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
                mock_client_instance.__aexit__ = AsyncMock(return_value=False)
                MockClient.return_value = mock_client_instance

                await _execute_composio_step(step, ctx)

        assert "resolved@test.com" in captured_payload["input"]["to"]
        assert "The email body text" in captured_payload["input"]["body"]

    @pytest.mark.asyncio
    async def test_no_connected_account_omitted(self):
        """If connected_account_id is empty, it should not appear in payload."""
        step = StepDefinition(
            id="s1",
            type="composio",
            composio_config=ComposioConfig(
                action="github_star_repo",
                params={"repo": "test/repo"},
            ),
        )
        ctx = _make_context()

        captured_payload = {}

        mock_response = MagicMock()
        mock_response.json.return_value = {"data": {"starred": True}}
        mock_response.raise_for_status = MagicMock()

        async def mock_post(url, json=None, headers=None):
            captured_payload.update(json or {})
            return mock_response

        with patch.dict("os.environ", {"TOOL_COMPOSIO_API_KEY": "key"}):
            with patch("httpx.AsyncClient") as MockClient:
                mock_client_instance = AsyncMock()
                mock_client_instance.post = mock_post
                mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
                mock_client_instance.__aexit__ = AsyncMock(return_value=False)
                MockClient.return_value = mock_client_instance

                await _execute_composio_step(step, ctx)

        assert "connectedAccountId" not in captured_payload
        assert "appName" not in captured_payload


# ===================================================================
# 6. NON_PROMPT_TYPES / NON_LLM_TYPES membership
# ===================================================================

class TestTypeSets:
    """Composio must be in the correct type sets."""

    def test_composio_in_non_prompt_types(self):
        assert "composio" in NON_PROMPT_TYPES

    def test_composio_in_non_llm_types(self):
        assert "composio" in NON_LLM_TYPES

    def test_composio_in_valid_step_types(self):
        assert "composio" in VALID_STEP_TYPES

    def test_non_llm_types_subset_of_non_prompt_types(self):
        """NON_LLM_TYPES should be subset of NON_PROMPT_TYPES.
        Note: gate is in NON_PROMPT (no prompt needed) but NOT in NON_LLM (uses LLM for eval)."""
        assert NON_LLM_TYPES <= NON_PROMPT_TYPES


# ===================================================================
# 7. StepDefinition field count
# ===================================================================

class TestStepDefinitionFieldCount:
    """StepDefinition should have exactly 38 fields."""

    def test_field_count(self):
        fields = dataclasses.fields(StepDefinition)
        assert len(fields) == 38, (
            f"Expected 38 fields, got {len(fields)}: "
            f"{[f.name for f in fields]}"
        )

    def test_composio_config_field_exists(self):
        field_names = [f.name for f in dataclasses.fields(StepDefinition)]
        assert "composio_config" in field_names


# ===================================================================
# Additional edge case tests
# ===================================================================

class TestComposioConfigDataclass:
    """Direct tests on the ComposioConfig dataclass."""

    def test_defaults(self):
        cfg = ComposioConfig()
        assert cfg.action == ""
        assert cfg.params == {}
        assert cfg.connected_account_id == ""
        assert cfg.app == ""

    def test_custom_values(self):
        cfg = ComposioConfig(
            action="jira_create_ticket",
            params={"summary": "Test"},
            connected_account_id="conn-1",
            app="jira",
        )
        assert cfg.action == "jira_create_ticket"
        assert cfg.params == {"summary": "Test"}
        assert cfg.connected_account_id == "conn-1"
        assert cfg.app == "jira"

    def test_field_count(self):
        fields = dataclasses.fields(ComposioConfig)
        assert len(fields) == 4, (
            f"Expected 4 fields in ComposioConfig, got {len(fields)}"
        )


class TestMultiStepWorkflow:
    """Integration-style tests with composio in a multi-step workflow."""

    def test_composio_after_standard_step(self):
        yaml_str = _base_yaml(
            "  - id: research\n"
            "    prompt: research {input.topic}\n"
            "  - id: notify\n"
            "    type: composio\n"
            "    depends_on: [research]\n"
            "    composio_config:\n"
            "      action: slack_send_message\n"
            "      params:\n"
            "        text: \"{steps.research.output}\"\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        assert errors == [], f"Unexpected errors: {errors}"
        plan = build_plan(wf)
        assert plan.stages[0] == ["research"]
        assert plan.stages[1] == ["notify"]

    def test_parallel_composio_steps(self):
        yaml_str = _base_yaml(
            "  - id: writer\n"
            "    prompt: write content\n"
            "  - id: email\n"
            "    type: composio\n"
            "    depends_on: [writer]\n"
            "    composio_config:\n"
            "      action: gmail_send_email\n"
            "      params:\n"
            "        body: \"{steps.writer.output}\"\n"
            "  - id: slack\n"
            "    type: composio\n"
            "    depends_on: [writer]\n"
            "    composio_config:\n"
            "      action: slack_send_message\n"
            "      params:\n"
            "        text: \"{steps.writer.output}\"\n"
        )
        wf = parse_yaml_string(yaml_str)
        errors = validate(wf)
        assert errors == [], f"Unexpected errors: {errors}"
        plan = build_plan(wf)
        assert len(plan.stages) == 2
        assert plan.stages[0] == ["writer"]
        assert set(plan.stages[1]) == {"email", "slack"}

    def test_composio_with_explicit_and_implicit_deps(self):
        """Explicit depends_on and implicit {steps.X} refs should both work."""
        yaml_str = _base_yaml(
            "  - id: step-a\n"
            "    prompt: do A\n"
            "  - id: step-b\n"
            "    prompt: do B\n"
            "  - id: send\n"
            "    type: composio\n"
            "    depends_on: [step-a]\n"
            "    composio_config:\n"
            "      action: gmail_send_email\n"
            "      params:\n"
            "        body: \"{steps.step-b.output}\"\n"
        )
        wf = parse_yaml_string(yaml_str)
        plan = build_plan(wf)
        # send depends on both step-a (explicit) and step-b (implicit)
        send_stage = next(i for i, s in enumerate(plan.stages) if "send" in s)
        a_stage = next(i for i, s in enumerate(plan.stages) if "step-a" in s)
        b_stage = next(i for i, s in enumerate(plan.stages) if "step-b" in s)
        assert send_stage > a_stage
        assert send_stage > b_stage
