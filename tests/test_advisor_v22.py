"""Comprehensive tests for the advisor/generator system (v22).

Tests cover: _scrub_secrets, _get_advisor_config, _is_anthropic_provider,
_build_request_body, _parse_response_text, _resolve_api_key, explain_error,
and ExplainErrorRequest schema validation.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import patch, MagicMock

import pytest

from sandcastle.engine.generator import (
    _scrub_secrets,
    _get_advisor_config,
    _is_anthropic_provider,
    _build_request_body,
    _parse_response_text,
    _resolve_api_key,
    explain_error,
)
from sandcastle.api.schemas import ExplainErrorRequest


# ===================================================================
# 1. _scrub_secrets - secrets ARE redacted
# ===================================================================


class TestScrubSecretsRedaction:
    """Verify that _scrub_secrets redacts various secret patterns."""

    def test_bearer_token(self):
        text = "Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig"
        result = _scrub_secrets(text)
        assert "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9" not in result
        assert "[REDACTED]" in result

    def test_sk_api_key(self):
        text = "Using key sk-proj-abc123def456ghi789jkl012mno345"
        result = _scrub_secrets(text)
        assert "sk-proj-abc123def456ghi789jkl012mno345" not in result
        assert "[REDACTED]" in result

    def test_ghp_token(self):
        text = "Token ghp_1234567890abcdefABCDEF"
        result = _scrub_secrets(text)
        assert "ghp_1234567890abcdefABCDEF" not in result
        assert "[REDACTED]" in result

    def test_xoxb_slack_token(self):
        text = "Slack token: xoxb-FAKE0TEST0TOK-fakeTestValue"
        result = _scrub_secrets(text)
        assert "xoxb-FAKE0TEST0TOK-fakeTestValue" not in result
        assert "[REDACTED]" in result

    def test_glpat_gitlab_token(self):
        text = "GitLab: glpat-FAKE_TEST_TOKEN_VALUE_HERE"
        result = _scrub_secrets(text)
        assert "glpat-FAKE_TEST_TOKEN" not in result
        assert "[REDACTED]" in result

    def test_aws_access_key(self):
        text = "AWS key: AKIAIOSFODNN7EXAMPLE"
        result = _scrub_secrets(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "[REDACTED]" in result

    def test_aws_asia_key(self):
        # ASIA + exactly 16 uppercase alphanumeric chars
        key = "ASIAJEXAMPLEKEY01X2Z"  # ASIA + 16 chars = 20 total
        text = f"Temp key: {key}"
        result = _scrub_secrets(text)
        assert key not in result
        assert "[REDACTED]" in result

    def test_long_hex_string_32_chars(self):
        hex_key = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"  # 32 hex chars
        text = f'API key: "{hex_key}"'
        result = _scrub_secrets(text)
        assert hex_key not in result
        assert "[REDACTED]" in result

    def test_long_hex_string_64_chars(self):
        hex_key = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
        text = f"secret={hex_key} "
        result = _scrub_secrets(text)
        assert hex_key not in result

    def test_key_value_pattern_api_key(self):
        text = "api_key=mysupersecretapikey1234"
        result = _scrub_secrets(text)
        assert "mysupersecretapikey1234" not in result
        assert "[REDACTED]" in result

    def test_key_value_pattern_token(self):
        text = "token: very_secret_token_1234"
        result = _scrub_secrets(text)
        assert "very_secret_token_1234" not in result
        assert "[REDACTED]" in result

    def test_key_value_pattern_secret(self):
        text = "secret=this_is_a_very_long_secret"
        result = _scrub_secrets(text)
        assert "this_is_a_very_long_secret" not in result
        assert "[REDACTED]" in result

    def test_key_value_pattern_password(self):
        text = "password: myP@ssw0rd_Very_Long!"
        result = _scrub_secrets(text)
        assert "myP@ssw0rd_Very_Long!" not in result
        assert "[REDACTED]" in result

    def test_key_value_pattern_authorization(self):
        text = "authorization=Bearer_some_long_tok"
        result = _scrub_secrets(text)
        assert "Bearer_some_long_tok" not in result
        assert "[REDACTED]" in result

    def test_pk_key_prefix(self):
        text = "pk_live_abc1234567890xyz"
        result = _scrub_secrets(text)
        assert "pk_live_abc1234567890xyz" not in result
        assert "[REDACTED]" in result

    def test_gho_github_oauth_token(self):
        text = "gho_1234567890abcdefghij"
        result = _scrub_secrets(text)
        assert "gho_1234567890abcdefghij" not in result
        assert "[REDACTED]" in result

    def test_xoxp_slack_token(self):
        text = "xoxp-FAKE0TEST0TOK-fakeTestValue"
        result = _scrub_secrets(text)
        assert "xoxp-FAKE0TEST0TOK-fakeTestValue" not in result
        assert "[REDACTED]" in result

    def test_xoxa_slack_token(self):
        text = "xoxa-123456789012-abcdefghijklm"
        result = _scrub_secrets(text)
        assert "xoxa-123456789012-abcdefghijklm" not in result
        assert "[REDACTED]" in result

    def test_xoxs_slack_token(self):
        text = "xoxs-123456789012-abcdefghijklm"
        result = _scrub_secrets(text)
        assert "xoxs-123456789012-abcdefghijklm" not in result
        assert "[REDACTED]" in result

    def test_jwt_like_token(self):
        text = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature123"
        result = _scrub_secrets(text)
        assert "eyJhbGciOiJIUzI1NiJ9" not in result
        assert "[REDACTED]" in result

    def test_multiple_secrets_in_one_text(self):
        text = (
            "Error connecting with Bearer abc123def456ghij "
            "and api_key=secretkey12345678 "
            "also ghp_mytokenvalue12345"
        )
        result = _scrub_secrets(text)
        assert "abc123def456ghij" not in result
        assert "secretkey12345678" not in result
        assert "ghp_mytokenvalue12345" not in result
        assert result.count("[REDACTED]") >= 2


# ===================================================================
# 2. _scrub_secrets - normal text is NOT mangled
# ===================================================================


class TestScrubSecretsPreservesNormal:
    """Verify that _scrub_secrets does NOT mangle normal text."""

    def test_normal_error_message(self):
        text = "Connection refused: localhost:8080 - ECONNREFUSED"
        assert _scrub_secrets(text) == text

    def test_python_traceback(self):
        text = (
            'Traceback (most recent call last):\n'
            '  File "/app/main.py", line 42, in run\n'
            '    result = process(data)\n'
            "TypeError: 'NoneType' object is not iterable"
        )
        assert _scrub_secrets(text) == text

    def test_http_status_error(self):
        text = "HTTP 404 Not Found: /api/v1/workflows/test-workflow"
        assert _scrub_secrets(text) == text

    def test_timeout_error(self):
        text = "Step timed out after 300 seconds (max_turns=10, model=sonnet)"
        assert _scrub_secrets(text) == text

    def test_validation_error(self):
        text = "Validation error: field 'name' is required, got empty string"
        assert _scrub_secrets(text) == text

    def test_yaml_parse_error(self):
        text = "YAML parse error at line 15: mapping values are not allowed here"
        assert _scrub_secrets(text) == text

    def test_short_words_not_matched(self):
        text = "The token is invalid"
        # "token" followed by a short value should not trigger key=value pattern
        assert _scrub_secrets(text) == text

    def test_file_paths(self):
        text = "/Users/gizmax/Documents/Sandcastle/src/sandcastle/engine/generator.py"
        assert _scrub_secrets(text) == text

    def test_stack_trace_with_line_numbers(self):
        text = (
            "RuntimeError: Step 'fetch-data' failed\n"
            "  at engine.py:142\n"
            "  at runner.py:89\n"
            "  at main.py:25"
        )
        assert _scrub_secrets(text) == text

    def test_numeric_ids_not_matched(self):
        text = "Workflow ID: 12345, Step index: 3, Run ID: 67890"
        assert _scrub_secrets(text) == text

    def test_empty_string(self):
        assert _scrub_secrets("") == ""

    def test_model_names_preserved(self):
        text = "Using model claude-sonnet-4-6 with max_tokens=4096"
        assert _scrub_secrets(text) == text

    def test_url_without_secrets(self):
        text = "Requesting https://api.example.com/v1/data?page=2&limit=50"
        assert _scrub_secrets(text) == text


# ===================================================================
# 3. _get_advisor_config - provider selection
# ===================================================================


class TestGetAdvisorConfig:
    """Test _get_advisor_config provider resolution."""

    @patch.dict(os.environ, {}, clear=True)
    def test_default_is_anthropic(self):
        cfg = _get_advisor_config()
        assert cfg["api_key_env"] == "ANTHROPIC_API_KEY"
        assert "anthropic" in cfg["api_url"]
        assert cfg["model"] == "claude-sonnet-4-6"

    @patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "anthropic"}, clear=True)
    def test_explicit_anthropic(self):
        cfg = _get_advisor_config()
        assert cfg["api_key_env"] == "ANTHROPIC_API_KEY"
        assert "anthropic" in cfg["api_url"]

    @patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "openai"}, clear=True)
    def test_openai_provider(self):
        cfg = _get_advisor_config()
        assert cfg["api_key_env"] == "OPENAI_API_KEY"
        assert "openai" in cfg["api_url"]
        assert cfg["model"] == "gpt-4o"

    @patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "ollama"}, clear=True)
    def test_ollama_provider(self):
        cfg = _get_advisor_config()
        assert cfg["api_key_env"] == ""
        assert "localhost" in cfg["api_url"] or "11434" in cfg["api_url"]
        assert cfg["model"] == "llama3.2"

    @patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "unknown_provider"}, clear=True)
    def test_unknown_provider_fallback_to_anthropic(self):
        cfg = _get_advisor_config()
        assert cfg["api_key_env"] == "ANTHROPIC_API_KEY"
        assert "anthropic" in cfg["api_url"]

    @patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "OPENAI"}, clear=True)
    def test_case_insensitive_provider(self):
        cfg = _get_advisor_config()
        assert cfg["api_key_env"] == "OPENAI_API_KEY"

    @patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "OpenAI"}, clear=True)
    def test_mixed_case_provider(self):
        cfg = _get_advisor_config()
        assert cfg["api_key_env"] == "OPENAI_API_KEY"


# ===================================================================
# 4. _get_advisor_config - model override
# ===================================================================


class TestGetAdvisorConfigModelOverride:
    """Test SANDCASTLE_ADVISOR_MODEL override."""

    @patch.dict(os.environ, {"SANDCASTLE_ADVISOR_MODEL": "claude-3-opus"}, clear=True)
    def test_model_override_anthropic(self):
        cfg = _get_advisor_config()
        assert cfg["model"] == "claude-3-opus"

    @patch.dict(
        os.environ,
        {"SANDCASTLE_ADVISOR_PROVIDER": "openai", "SANDCASTLE_ADVISOR_MODEL": "gpt-4-turbo"},
        clear=True,
    )
    def test_model_override_openai(self):
        cfg = _get_advisor_config()
        assert cfg["model"] == "gpt-4-turbo"
        assert cfg["api_key_env"] == "OPENAI_API_KEY"

    @patch.dict(
        os.environ,
        {"SANDCASTLE_ADVISOR_PROVIDER": "ollama", "SANDCASTLE_ADVISOR_MODEL": "mixtral"},
        clear=True,
    )
    def test_model_override_ollama(self):
        cfg = _get_advisor_config()
        assert cfg["model"] == "mixtral"

    @patch.dict(os.environ, {"SANDCASTLE_ADVISOR_MODEL": ""}, clear=True)
    def test_empty_model_override_uses_default(self):
        cfg = _get_advisor_config()
        assert cfg["model"] == "claude-sonnet-4-6"

    @patch.dict(os.environ, {}, clear=True)
    def test_no_model_override_uses_default(self):
        cfg = _get_advisor_config()
        assert cfg["model"] == "claude-sonnet-4-6"


# ===================================================================
# 5. _is_anthropic_provider
# ===================================================================


class TestIsAnthropicProvider:
    """Test _is_anthropic_provider detection."""

    @patch.dict(os.environ, {}, clear=True)
    def test_default_is_anthropic(self):
        assert _is_anthropic_provider() is True

    @patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "anthropic"}, clear=True)
    def test_explicit_anthropic(self):
        assert _is_anthropic_provider() is True

    @patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "openai"}, clear=True)
    def test_openai_not_anthropic(self):
        assert _is_anthropic_provider() is False

    @patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "ollama"}, clear=True)
    def test_ollama_not_anthropic(self):
        assert _is_anthropic_provider() is False

    @patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "unknown"}, clear=True)
    def test_unknown_falls_back_to_anthropic(self):
        # Unknown provider falls back to anthropic
        assert _is_anthropic_provider() is True


# ===================================================================
# 6. _build_request_body - format differences
# ===================================================================


class TestBuildRequestBody:
    """Test provider-specific request body building."""

    @patch.dict(os.environ, {}, clear=True)
    def test_anthropic_format_has_system_top_level(self):
        body = _build_request_body(
            model="claude-sonnet-4-6",
            system="You are a helper.",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=1024,
        )
        # Anthropic format: system is a top-level field
        assert "system" in body
        assert body["system"] == "You are a helper."
        assert body["model"] == "claude-sonnet-4-6"
        assert body["max_tokens"] == 1024
        assert body["messages"] == [{"role": "user", "content": "Hello"}]
        # System should NOT be in messages
        for msg in body["messages"]:
            assert msg["role"] != "system"

    @patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "openai"}, clear=True)
    def test_openai_format_system_as_first_message(self):
        body = _build_request_body(
            model="gpt-4o",
            system="You are a helper.",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=1024,
        )
        # OpenAI format: system is the first message
        assert "system" not in body
        assert len(body["messages"]) == 2
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][0]["content"] == "You are a helper."
        assert body["messages"][1]["role"] == "user"
        assert body["messages"][1]["content"] == "Hello"

    @patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "ollama"}, clear=True)
    def test_ollama_format_same_as_openai(self):
        body = _build_request_body(
            model="llama3.2",
            system="System prompt.",
            messages=[{"role": "user", "content": "Hi"}],
        )
        # Ollama uses OpenAI-compatible format
        assert "system" not in body
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][0]["content"] == "System prompt."

    @patch.dict(os.environ, {}, clear=True)
    def test_anthropic_default_max_tokens(self):
        body = _build_request_body(
            model="test-model",
            system="sys",
            messages=[{"role": "user", "content": "msg"}],
        )
        assert body["max_tokens"] == 4096

    @patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "openai"}, clear=True)
    def test_openai_preserves_multiple_messages(self):
        messages = [
            {"role": "user", "content": "First"},
            {"role": "assistant", "content": "Reply"},
            {"role": "user", "content": "Second"},
        ]
        body = _build_request_body(
            model="gpt-4o",
            system="System",
            messages=messages,
            max_tokens=2048,
        )
        # System + 3 original messages = 4 total
        assert len(body["messages"]) == 4
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][1]["role"] == "user"
        assert body["messages"][2]["role"] == "assistant"
        assert body["messages"][3]["role"] == "user"


# ===================================================================
# 7. _parse_response_text - format differences
# ===================================================================


class TestParseResponseText:
    """Test provider-specific response parsing."""

    @patch.dict(os.environ, {}, clear=True)
    def test_anthropic_format(self):
        data = {
            "content": [
                {"type": "text", "text": "Here is the YAML workflow."}
            ]
        }
        assert _parse_response_text(data) == "Here is the YAML workflow."

    @patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "openai"}, clear=True)
    def test_openai_format(self):
        data = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Here is the response.",
                    }
                }
            ]
        }
        assert _parse_response_text(data) == "Here is the response."

    @patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "ollama"}, clear=True)
    def test_ollama_format_same_as_openai(self):
        data = {
            "choices": [
                {"message": {"role": "assistant", "content": "Ollama response."}}
            ]
        }
        assert _parse_response_text(data) == "Ollama response."

    @patch.dict(os.environ, {}, clear=True)
    def test_anthropic_empty_text(self):
        data = {"content": [{"type": "text", "text": ""}]}
        assert _parse_response_text(data) == ""

    @patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "openai"}, clear=True)
    def test_openai_multiline_content(self):
        data = {
            "choices": [
                {"message": {"content": "line1\nline2\nline3"}}
            ]
        }
        assert _parse_response_text(data) == "line1\nline2\nline3"


# ===================================================================
# 8. _resolve_api_key
# ===================================================================


class TestResolveApiKey:
    """Test _resolve_api_key resolution logic."""

    @patch.dict(
        os.environ,
        {"ANTHROPIC_API_KEY": "sk-ant-test123456", "SANDCASTLE_ADVISOR_PROVIDER": ""},
        clear=True,
    )
    def test_anthropic_key_from_env(self):
        key = _resolve_api_key()
        assert key == "sk-ant-test123456"

    @patch.dict(
        os.environ,
        {
            "SANDCASTLE_ADVISOR_PROVIDER": "openai",
            "OPENAI_API_KEY": "sk-openai-test789",
        },
        clear=True,
    )
    def test_openai_key_from_env(self):
        key = _resolve_api_key()
        assert key == "sk-openai-test789"

    @patch.dict(
        os.environ,
        {"SANDCASTLE_ADVISOR_PROVIDER": "ollama"},
        clear=True,
    )
    def test_ollama_returns_dummy_key(self):
        key = _resolve_api_key()
        assert key == "ollama-no-key"

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "my-key-123"}, clear=True)
    def test_default_provider_uses_anthropic_key(self):
        key = _resolve_api_key()
        assert key == "my-key-123"


# ===================================================================
# 9. explain_error - no API key fallback
# ===================================================================


class TestExplainErrorNoApiKey:
    """Test explain_error graceful fallback when no API key is set."""

    @pytest.mark.asyncio
    async def test_no_api_key_returns_fallback_dict(self):
        # Patch _resolve_api_key to return empty string (no key)
        with patch("sandcastle.engine.generator._resolve_api_key", return_value=""):
            result = await explain_error(
                step_id="fetch-data",
                step_type="http",
                error="Connection timeout after 30s",
            )
        assert isinstance(result, dict)
        assert "summary" in result
        assert "cause" in result
        assert "fix" in result
        assert "severity" in result
        # Should mention API key not set
        assert "API key" in result["cause"] or "API key" in result["fix"]

    @pytest.mark.asyncio
    async def test_no_api_key_does_not_crash(self):
        with patch("sandcastle.engine.generator._resolve_api_key", return_value=""):
            # Should NOT raise any exception
            result = await explain_error(
                step_id="process",
                step_type="code",
                error="RuntimeError: division by zero",
                prompt="Calculate the average of the inputs",
                model="sonnet",
                workflow_name="test-workflow",
            )
        assert result["severity"] == "medium"

    @pytest.mark.asyncio
    async def test_no_api_key_truncates_long_error(self):
        long_error = "X" * 500
        with patch("sandcastle.engine.generator._resolve_api_key", return_value=""):
            result = await explain_error(
                step_id="step1",
                step_type="standard",
                error=long_error,
            )
        # Summary should be truncated to at most 200 chars
        assert len(result["summary"]) <= 200

    @pytest.mark.asyncio
    async def test_fallback_dict_has_all_required_keys(self):
        with patch("sandcastle.engine.generator._resolve_api_key", return_value=""):
            result = await explain_error(
                step_id="s1",
                step_type="llm",
                error="some error",
            )
        required_keys = {"summary", "cause", "fix", "severity"}
        assert required_keys.issubset(result.keys())


# ===================================================================
# 10. ExplainErrorRequest schema validation
# ===================================================================


class TestExplainErrorRequestSchema:
    """Test Pydantic validation for ExplainErrorRequest."""

    def test_valid_minimal_request(self):
        req = ExplainErrorRequest(step_id="step-1", error="Something broke")
        assert req.step_id == "step-1"
        assert req.error == "Something broke"
        assert req.step_type == "standard"  # default
        assert req.prompt == ""  # default
        assert req.model == ""  # default
        assert req.workflow_name == ""  # default

    def test_valid_full_request(self):
        req = ExplainErrorRequest(
            step_id="fetch-data",
            step_type="http",
            error="HTTP 500 Internal Server Error",
            prompt="Fetch user data from API",
            model="sonnet",
            workflow_name="user-pipeline",
        )
        assert req.step_id == "fetch-data"
        assert req.step_type == "http"
        assert req.error == "HTTP 500 Internal Server Error"
        assert req.prompt == "Fetch user data from API"
        assert req.model == "sonnet"
        assert req.workflow_name == "user-pipeline"

    def test_missing_step_id_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ExplainErrorRequest(error="something broke")

    def test_missing_error_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ExplainErrorRequest(step_id="step-1")

    def test_empty_step_id_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ExplainErrorRequest(step_id="", error="error")

    def test_empty_error_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ExplainErrorRequest(step_id="step-1", error="")

    def test_error_max_length(self):
        from pydantic import ValidationError

        # max_length=10000
        long_error = "x" * 10001
        with pytest.raises(ValidationError):
            ExplainErrorRequest(step_id="step-1", error=long_error)

    def test_error_at_max_length_accepted(self):
        req = ExplainErrorRequest(step_id="step-1", error="x" * 10000)
        assert len(req.error) == 10000

    def test_prompt_max_length(self):
        from pydantic import ValidationError

        # max_length=5000
        long_prompt = "y" * 5001
        with pytest.raises(ValidationError):
            ExplainErrorRequest(step_id="step-1", error="err", prompt=long_prompt)

    def test_prompt_at_max_length_accepted(self):
        req = ExplainErrorRequest(step_id="s", error="e", prompt="y" * 5000)
        assert len(req.prompt) == 5000

    def test_missing_both_required_fields(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ExplainErrorRequest()
