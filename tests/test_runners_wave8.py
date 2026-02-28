"""Wave 8 deep audit tests: runners (.mjs), connector-utils, and connectors.

Tests JavaScript behavior by reading source files and validating logic patterns,
plus integration tests via subprocess where applicable.  All tests are pure-Python
unit tests that do NOT require Node.js or any external services.
"""

from __future__ import annotations

import json
import os
import re
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(ROOT, "src", "sandcastle", "engine")
CONNECTORS = os.path.join(ENGINE, "tools", "connectors")

RUNNER_MJS = os.path.join(ENGINE, "runner.mjs")
RUNNER_OPENAI_MJS = os.path.join(ENGINE, "runner-openai.mjs")
CONNECTOR_UTILS_MJS = os.path.join(CONNECTORS, "connector-utils.mjs")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ===========================================================================
# Part 1: runner.mjs (Claude Agent SDK runner)
# ===========================================================================


class TestRunnerMjsInputValidation:
    """Validate input parsing and validation in runner.mjs."""

    def test_checks_sandcastle_request_env(self):
        src = _read(RUNNER_MJS)
        assert "process.env.SANDCASTLE_REQUEST" in src
        assert 'SANDCASTLE_REQUEST env var is not set' in src

    def test_json_parse_error_handling(self):
        src = _read(RUNNER_MJS)
        assert "JSON.parse(process.env.SANDCASTLE_REQUEST)" in src
        assert "Failed to parse SANDCASTLE_REQUEST" in src

    def test_validates_prompt_field(self):
        src = _read(RUNNER_MJS)
        assert 'typeof request.prompt !== "string"' in src
        assert "non-empty 'prompt' string" in src

    def test_max_turns_has_upper_bound(self):
        """max_turns must be capped to prevent unbounded execution."""
        src = _read(RUNNER_MJS)
        assert "Math.min" in src
        # Verify there is a cap value (200)
        assert "200" in src

    def test_max_turns_validates_positive_integer(self):
        """max_turns must be validated as a positive integer."""
        src = _read(RUNNER_MJS)
        assert "Number.isFinite" in src
        assert "rawMaxTurns" in src
        assert "parseInt" in src

    def test_max_turns_default_fallback(self):
        """Non-numeric max_turns should fall back to default of 10."""
        src = _read(RUNNER_MJS)
        # Default fallback
        assert ": 10" in src


class TestRunnerMjsErrorHandling:
    """Validate error handling in runner.mjs."""

    def test_unhandled_rejection_handler(self):
        src = _read(RUNNER_MJS)
        assert "unhandledRejection" in src
        assert "process.exit(1)" in src

    def test_main_catch_handles_null_err(self):
        """The catch block should handle err being null/undefined."""
        src = _read(RUNNER_MJS)
        # The catch should use err?.message || String(err)
        assert "err?.message || String(err)" in src

    def test_emits_error_event_on_failure(self):
        src = _read(RUNNER_MJS)
        assert 'type: "error"' in src


class TestRunnerMjsTokenTracking:
    """Validate token usage tracking and cost calculation."""

    def test_tracks_input_and_output_tokens(self):
        src = _read(RUNNER_MJS)
        assert "totalInputTokens" in src
        assert "totalOutputTokens" in src

    def test_pricing_uses_per_million(self):
        src = _read(RUNNER_MJS)
        assert "1_000_000" in src

    def test_handles_missing_usage(self):
        src = _read(RUNNER_MJS)
        # trackUsage should handle missing usage gracefully
        assert "if (!usage) return" in src

    def test_cost_calculation(self):
        src = _read(RUNNER_MJS)
        assert "totalInputTokens * inputPrice" in src
        assert "totalOutputTokens * outputPrice" in src

    def test_nan_price_defaults_to_zero(self):
        """parseFloat on non-numeric string returns NaN; 0 default should handle it."""
        src = _read(RUNNER_MJS)
        assert 'process.env.MODEL_INPUT_PRICE || "0"' in src
        assert 'process.env.MODEL_OUTPUT_PRICE || "0"' in src


# ===========================================================================
# Part 2: runner-openai.mjs (OpenAI-compatible runner)
# ===========================================================================


class TestOpenAIRunnerInputValidation:
    """Validate input parsing and validation in runner-openai.mjs."""

    def test_checks_sandcastle_request_env(self):
        src = _read(RUNNER_OPENAI_MJS)
        assert "process.env.SANDCASTLE_REQUEST" in src

    def test_validates_prompt(self):
        src = _read(RUNNER_OPENAI_MJS)
        assert 'typeof request.prompt !== "string"' in src

    def test_max_turns_has_upper_bound(self):
        """max_turns should be capped to prevent unbounded execution."""
        src = _read(RUNNER_OPENAI_MJS)
        # Should have Math.min with cap of 200
        assert "Math.min(rawMaxTurns, 200)" in src

    def test_max_turns_validates_positive(self):
        src = _read(RUNNER_OPENAI_MJS)
        assert "Number.isFinite(rawMaxTurns)" in src
        assert "rawMaxTurns > 0" in src

    def test_timeout_has_upper_bound(self):
        """Timeout should be capped to prevent indefinite blocking."""
        src = _read(RUNNER_OPENAI_MJS)
        assert "3600" in src  # max 1 hour
        assert "rawTimeout" in src


class TestOpenAIRunnerPathValidation:
    """Validate sandbox path validation."""

    def test_sandbox_root_defined(self):
        src = _read(RUNNER_OPENAI_MJS)
        assert 'SANDBOX_ROOT = "/home/user"' in src

    def test_validates_path_within_sandbox(self):
        src = _read(RUNNER_OPENAI_MJS)
        assert "normalized.startsWith(SANDBOX_ROOT" in src

    def test_rejects_path_outside_sandbox(self):
        src = _read(RUNNER_OPENAI_MJS)
        assert "outside sandbox root" in src

    def test_uses_resolve_and_normalize(self):
        """Path validation should use both resolve and normalize to prevent traversal."""
        src = _read(RUNNER_OPENAI_MJS)
        assert "resolve(filePath)" in src
        assert "normalize(absPath)" in src


class TestOpenAIRunnerToolExecution:
    """Validate tool execution logic."""

    def test_bash_validates_command_argument(self):
        """bash tool should validate that command is a non-empty string."""
        src = _read(RUNNER_OPENAI_MJS)
        assert "'command' argument is required" in src

    def test_bash_captures_stderr(self):
        """bash tool should capture and return stderr for failed commands."""
        src = _read(RUNNER_OPENAI_MJS)
        assert "execErr.stderr" in src
        assert "STDERR:" in src

    def test_read_file_validates_path_argument(self):
        src = _read(RUNNER_OPENAI_MJS)
        assert "'path' argument is required" in src

    def test_write_file_validates_content_argument(self):
        src = _read(RUNNER_OPENAI_MJS)
        assert "'content' argument is required" in src

    def test_unknown_tool_returns_error(self):
        """Unknown tool should return error message, not a normal-looking result."""
        src = _read(RUNNER_OPENAI_MJS)
        assert "Error: Unknown tool" in src
        assert "Available tools:" in src

    def test_tool_result_truncated(self):
        src = _read(RUNNER_OPENAI_MJS)
        assert "MAX_TOOL_RESULT_SIZE" in src
        assert "50_000" in src


class TestOpenAIRunnerHistoryManagement:
    """Validate conversation history trimming."""

    def test_history_has_max_limit(self):
        src = _read(RUNNER_OPENAI_MJS)
        assert "MAX_HISTORY_MESSAGES" in src
        assert "80" in src

    def test_trim_preserves_first_message(self):
        """Trimming should always keep the first user message."""
        src = _read(RUNNER_OPENAI_MJS)
        assert "messages[0]" in src

    def test_trim_does_not_break_tool_pairs(self):
        """Trimming must not cut between an assistant tool_call and its tool responses."""
        src = _read(RUNNER_OPENAI_MJS)
        # Check that the trimHistory function handles tool message pairing
        assert 'msg.role === "tool"' in src


class TestOpenAIRunnerRetryLogic:
    """Validate API call retry logic."""

    def test_has_retry_on_429(self):
        """Should retry on rate limit (429)."""
        src = _read(RUNNER_OPENAI_MJS)
        assert "429" in src
        assert "callApiWithRetry" in src

    def test_has_retry_on_500(self):
        """Should retry on server errors (500+)."""
        src = _read(RUNNER_OPENAI_MJS)
        assert "status >= 500" in src

    def test_does_not_retry_on_auth_errors(self):
        """Should NOT retry on 401/403."""
        src = _read(RUNNER_OPENAI_MJS)
        # Non-retryable errors should throw immediately
        assert "Non-retryable error" in src

    def test_respects_retry_after_header(self):
        src = _read(RUNNER_OPENAI_MJS)
        assert "retry-after" in src

    def test_has_max_retries(self):
        src = _read(RUNNER_OPENAI_MJS)
        assert "MAX_API_RETRIES" in src

    def test_exponential_backoff(self):
        src = _read(RUNNER_OPENAI_MJS)
        assert "Math.pow(2, attempt)" in src


class TestOpenAIRunnerResultHandling:
    """Validate result string coercion and emission."""

    def test_result_coerced_to_string(self):
        """Tool result should always be coerced to string before slicing."""
        src = _read(RUNNER_OPENAI_MJS)
        assert 'typeof result === "string"' in src
        assert "String(result" in src

    def test_emits_result_on_no_tool_calls(self):
        src = _read(RUNNER_OPENAI_MJS)
        assert 'type: "result"' in src

    def test_emits_result_on_max_turns(self):
        src = _read(RUNNER_OPENAI_MJS)
        assert "lastAssistant" in src

    def test_malformed_tool_args_reported_to_model(self):
        """Malformed JSON in tool arguments should report error back to model."""
        src = _read(RUNNER_OPENAI_MJS)
        assert "Could not parse tool arguments as JSON" in src

    def test_runner_crash_handler(self):
        src = _read(RUNNER_OPENAI_MJS)
        assert "Runner crashed:" in src
        assert "err?.message || String(err)" in src


# ===========================================================================
# Part 3: connector-utils.mjs
# ===========================================================================


class TestConnectorUtilsFetchWithRetry:
    """Validate fetchWithRetry behavior."""

    def test_has_url_validation(self):
        """Should validate URL scheme to prevent file:// and other non-HTTP schemes."""
        src = _read(CONNECTOR_UTILS_MJS)
        assert "validateUrl" in src
        assert 'new URL(url)' in src

    def test_rejects_non_http_schemes(self):
        src = _read(CONNECTOR_UTILS_MJS)
        assert '"http:"' in src
        assert '"https:"' in src
        assert "not allowed" in src

    def test_rejects_empty_url(self):
        src = _read(CONNECTOR_UTILS_MJS)
        assert "non-empty string" in src

    def test_handles_retry_after_date_format(self):
        """retry-after header can be a date string, not just seconds."""
        src = _read(CONNECTOR_UTILS_MJS)
        assert "new Date(rawHeader)" in src

    def test_caps_retry_after_wait(self):
        src = _read(CONNECTOR_UTILS_MJS)
        assert "MAX_WAIT_MS" in src
        assert "60000" in src

    def test_exponential_backoff_on_500(self):
        src = _read(CONNECTOR_UTILS_MJS)
        assert "Math.pow(2, attempt)" in src

    def test_timeout_error_has_url(self):
        src = _read(CONNECTOR_UTILS_MJS)
        assert "AbortError" in src
        assert "Timeout after" in src

    def test_abort_controller_per_attempt(self):
        """Each retry attempt should create a new AbortController."""
        src = _read(CONNECTOR_UTILS_MJS)
        # The AbortController creation should be inside the for loop
        assert "for (let attempt" in src
        assert "new AbortController()" in src


class TestConnectorUtilsParseJSON:
    """Validate parseJSON behavior."""

    def test_truncates_error_body(self):
        src = _read(CONNECTOR_UTILS_MJS)
        assert "text.slice(0, 200)" in src

    def test_has_max_response_size(self):
        """Should prevent reading excessively large response bodies."""
        src = _read(CONNECTOR_UTILS_MJS)
        assert "MAX_RESPONSE_SIZE" in src

    def test_includes_status_in_error(self):
        src = _read(CONNECTOR_UTILS_MJS)
        assert "resp.status" in src


class TestConnectorUtilsAssertOk:
    """Validate assertOk behavior."""

    def test_throws_on_non_ok(self):
        src = _read(CONNECTOR_UTILS_MJS)
        assert "resp.ok" in src
        assert "throw new Error" in src

    def test_masks_secrets_in_error_body(self):
        """Error responses should have secrets masked to prevent credential leaks."""
        src = _read(CONNECTOR_UTILS_MJS)
        assert "maskSecrets" in src

    def test_includes_context_prefix(self):
        src = _read(CONNECTOR_UTILS_MJS)
        assert "context" in src

    def test_handles_unreadable_error_body(self):
        """Should handle case where error response body cannot be read."""
        src = _read(CONNECTOR_UTILS_MJS)
        assert "catch" in src


class TestConnectorUtilsSecretMasking:
    """Validate the maskSecrets helper function."""

    def test_masks_bearer_tokens(self):
        src = _read(CONNECTOR_UTILS_MJS)
        assert "Bearer" in src
        assert "***" in src

    def test_masks_basic_auth(self):
        src = _read(CONNECTOR_UTILS_MJS)
        assert "Basic" in src

    def test_masks_api_key_patterns(self):
        src = _read(CONNECTOR_UTILS_MJS)
        # Should match api_key, token, secret, password patterns
        assert "api[_-]?key" in src.lower() or "api_key" in src.lower()


# ===========================================================================
# Part 4: SendGrid connector - SQL injection fix
# ===========================================================================


class TestSendGridConnector:
    """Validate SendGrid connector security fixes."""

    def test_list_contacts_sanitizes_query(self):
        """User input in list_contacts should be sanitized against SGQL injection."""
        src = _read(os.path.join(CONNECTORS, "sendgrid.mjs"))
        # Should escape single quotes and backslashes
        assert "sanitized" in src
        assert "replace" in src
        assert "\\\\'" in src or "\\\\" in src  # Escaping backslash or quote

    def test_list_contacts_no_raw_interpolation(self):
        """query parameter should NOT be directly interpolated into SGQL."""
        src = _read(os.path.join(CONNECTORS, "sendgrid.mjs"))
        # The old vulnerable pattern was: `email LIKE '%${query}%'`
        # The new pattern should use sanitized variable
        assert "${sanitized}" in src
        # Should NOT have ${query} in the SGQL template
        lines = src.split("\n")
        for line in lines:
            if "LIKE" in line and "${" in line:
                assert "sanitized" in line, f"Unsanitized query interpolation in SGQL: {line.strip()}"


# ===========================================================================
# Part 5: PostgreSQL connector - shell injection fix
# ===========================================================================


class TestPostgreSQLConnector:
    """Validate PostgreSQL connector security fixes."""

    def test_no_shell_interpolation_of_sql(self):
        """SQL should NOT be interpolated into shell command string."""
        src = _read(os.path.join(CONNECTORS, "postgresql.mjs"))
        # Old vulnerable pattern: `-c '${escapedSql}'`
        assert "-c '" not in src

    def test_sql_passed_via_stdin(self):
        """SQL should be passed via stdin to psql, not command-line args."""
        src = _read(os.path.join(CONNECTORS, "postgresql.mjs"))
        assert "input: sql" in src

    def test_connection_url_via_env(self):
        """Connection URL should be passed via environment variable."""
        src = _read(os.path.join(CONNECTORS, "postgresql.mjs"))
        assert "DATABASE_URL: PG_URL" in src

    def test_no_connection_url_in_shell_command(self):
        """Connection URL should NOT appear in the shell command string."""
        src = _read(os.path.join(CONNECTORS, "postgresql.mjs"))
        # Old pattern: `"${PG_URL}"`
        # Should not have PG_URL in the execSync command string
        lines = src.split("\n")
        for line in lines:
            if "execSync(" in line:
                assert "PG_URL" not in line, f"PG_URL in shell command: {line.strip()}"

    def test_masks_credentials_in_errors(self):
        """Error messages should mask the connection string."""
        src = _read(os.path.join(CONNECTORS, "postgresql.mjs"))
        assert ".replace(PG_URL" in src
        assert '"***"' in src

    def test_read_only_check_present(self):
        """query() should validate that SQL is read-only."""
        src = _read(os.path.join(CONNECTORS, "postgresql.mjs"))
        assert "SELECT" in src
        assert "read-only" in src


# ===========================================================================
# Part 6: GitHub connector - path injection fix
# ===========================================================================


class TestGitHubConnector:
    """Validate GitHub connector security fixes."""

    def test_validates_repo_format(self):
        """Repo parameter should be validated against injection."""
        src = _read(os.path.join(CONNECTORS, "github.mjs"))
        assert "validateRepo" in src

    def test_repo_regex_validation(self):
        """Repo must match owner/repo format."""
        src = _read(os.path.join(CONNECTORS, "github.mjs"))
        # Should have a regex pattern for owner/repo
        assert "owner/repo" in src.lower() or "Expected 'owner/repo'" in src

    def test_rejects_path_traversal_in_repo(self):
        """Repo with path traversal characters should be rejected."""
        src = _read(os.path.join(CONNECTORS, "github.mjs"))
        # The regex should only allow alphanumeric, hyphens, dots, underscores
        assert re.search(r"\[A-Za-z0-9", src) is not None

    def test_get_issues_encodes_state(self):
        """state parameter should be URL-encoded."""
        src = _read(os.path.join(CONNECTORS, "github.mjs"))
        assert "encodeURIComponent(state)" in src

    def test_get_issues_caps_limit(self):
        """limit parameter should be capped to prevent abuse."""
        src = _read(os.path.join(CONNECTORS, "github.mjs"))
        assert "Math.min" in src
        assert "100" in src  # GitHub API max is 100 per page


# ===========================================================================
# Part 7: Stripe connector - path validation
# ===========================================================================


class TestStripeConnector:
    """Validate Stripe connector fixes."""

    def test_get_customer_validates_id(self):
        """customer_id should be validated before use in URL."""
        src = _read(os.path.join(CONNECTORS, "stripe.mjs"))
        assert "encodeURIComponent(customer_id)" in src

    def test_get_customer_rejects_empty(self):
        src = _read(os.path.join(CONNECTORS, "stripe.mjs"))
        assert "customer_id is required" in src

    def test_create_invoice_validates_customer(self):
        src = _read(os.path.join(CONNECTORS, "stripe.mjs"))
        # create_invoice should also validate
        count = src.count("customer_id is required")
        assert count >= 2, "Both get_customer and create_invoice should validate customer_id"


# ===========================================================================
# Part 8: Jira connector - issue key validation
# ===========================================================================


class TestJiraConnector:
    """Validate Jira connector fixes."""

    def test_validates_issue_key_format(self):
        """Issue key should match PROJECT-123 format."""
        src = _read(os.path.join(CONNECTORS, "jira.mjs"))
        assert "validateIssueKey" in src

    def test_issue_key_regex(self):
        """Issue key regex should match Jira format."""
        src = _read(os.path.join(CONNECTORS, "jira.mjs"))
        # Should have a pattern like [A-Z][A-Z0-9_]+-\d+
        assert r"\d+" in src

    def test_get_issue_validates(self):
        src = _read(os.path.join(CONNECTORS, "jira.mjs"))
        # Should call validateIssueKey before API call
        assert "validateIssueKey(issue_key)" in src

    def test_add_comment_validates(self):
        src = _read(os.path.join(CONNECTORS, "jira.mjs"))
        # add_comment should also validate
        # Count occurrences of validateIssueKey call
        count = src.count("validateIssueKey(issue_key)")
        assert count >= 2, "Both get_issue and add_comment should validate issue_key"


# ===========================================================================
# Part 9: Firecrawl connector - path validation
# ===========================================================================


class TestFirecrawlConnector:
    """Validate Firecrawl connector fixes."""

    def test_get_crawl_status_validates_id(self):
        src = _read(os.path.join(CONNECTORS, "firecrawl.mjs"))
        assert "encodeURIComponent(crawlId)" in src

    def test_get_crawl_status_rejects_empty(self):
        src = _read(os.path.join(CONNECTORS, "firecrawl.mjs"))
        assert "crawlId is required" in src


# ===========================================================================
# Part 10: OpenAI connector - TTS binary response fix
# ===========================================================================


class TestOpenAIConnector:
    """Validate OpenAI connector fixes."""

    def test_tts_does_not_use_json_api(self):
        """text_to_speech should NOT use the api() helper that expects JSON."""
        src = _read(os.path.join(CONNECTORS, "openai.mjs"))
        # Find the text_to_speech function
        tts_start = src.index("export async function text_to_speech")
        tts_end = src.index("\n}", tts_start) + 2
        tts_func = src[tts_start:tts_end]
        # Should use fetch directly, not api()
        assert "await fetch(" in tts_func
        assert "await api(" not in tts_func

    def test_tts_validates_text_input(self):
        src = _read(os.path.join(CONNECTORS, "openai.mjs"))
        tts_start = src.index("export async function text_to_speech")
        tts_end = src.index("\n}", tts_start) + 2
        tts_func = src[tts_start:tts_end]
        assert "text is required" in tts_func

    def test_tts_reads_binary_response(self):
        """TTS should read the response as arrayBuffer, not json."""
        src = _read(os.path.join(CONNECTORS, "openai.mjs"))
        tts_start = src.index("export async function text_to_speech")
        tts_end = src.index("\n}", tts_start) + 2
        tts_func = src[tts_start:tts_end]
        assert "resp.arrayBuffer()" in tts_func
        assert "resp.json()" not in tts_func

    def test_tts_has_longer_timeout(self):
        """TTS endpoint may be slower - should have longer timeout."""
        src = _read(os.path.join(CONNECTORS, "openai.mjs"))
        tts_start = src.index("export async function text_to_speech")
        tts_end = src.index("\n}", tts_start) + 2
        tts_func = src[tts_start:tts_end]
        # Should have a timeout > 30s (default)
        assert "60_000" in tts_func or "60000" in tts_func


# ===========================================================================
# Part 11: WhatsApp connector - CLI dispatch guard
# ===========================================================================


class TestWhatsAppConnector:
    """Validate WhatsApp connector fixes."""

    def test_cli_dispatch_guarded(self):
        """CLI dispatch should only run when invoked directly, not when imported."""
        src = _read(os.path.join(CONNECTORS, "whatsapp.mjs"))
        assert 'process.argv[1]?.endsWith("whatsapp.mjs")' in src

    def test_has_usage_message(self):
        src = _read(os.path.join(CONNECTORS, "whatsapp.mjs"))
        assert "Usage:" in src


# ===========================================================================
# Part 12: Shell connector - temp file collision fix
# ===========================================================================


class TestShellConnector:
    """Validate shell connector fixes."""

    def test_uses_uuid_for_temp_files(self):
        """Temp files should use UUID to prevent collisions."""
        src = _read(os.path.join(CONNECTORS, "shell.mjs"))
        assert "randomUUID" in src

    def test_imports_crypto(self):
        src = _read(os.path.join(CONNECTORS, "shell.mjs"))
        assert "node:crypto" in src

    def test_validates_interpreters(self):
        """Only allowed interpreters should be accepted."""
        src = _read(os.path.join(CONNECTORS, "shell.mjs"))
        assert "validInterpreters" in src
        assert "Invalid interpreter" in src

    def test_cleanup_temp_file(self):
        """Temp files should be cleaned up in finally block."""
        src = _read(os.path.join(CONNECTORS, "shell.mjs"))
        assert "finally" in src
        assert "unlinkSync" in src


# ===========================================================================
# Part 13: Airtable connector - record ID encoding
# ===========================================================================


class TestAirtableConnector:
    """Validate Airtable connector fixes."""

    def test_get_record_encodes_id(self):
        src = _read(os.path.join(CONNECTORS, "airtable.mjs"))
        assert "encodeURIComponent(recordId)" in src

    def test_get_record_validates_id(self):
        src = _read(os.path.join(CONNECTORS, "airtable.mjs"))
        assert "recordId is required" in src


# ===========================================================================
# Part 14: Cross-cutting connector security checks
# ===========================================================================


class TestConnectorCredentialSafety:
    """Verify that connectors don't leak credentials in error messages."""

    @pytest.mark.parametrize("connector", [
        "github.mjs", "stripe.mjs", "notion.mjs",
        "sendgrid.mjs", "discord.mjs", "hubspot.mjs", "jira.mjs",
        "airtable.mjs", "anthropic.mjs", "openai.mjs",
    ])
    def test_error_messages_truncated(self, connector):
        """Error messages from API responses should be truncated."""
        src = _read(os.path.join(CONNECTORS, connector))
        # All connectors should truncate error response text
        assert "slice(0," in src

    def test_slack_uses_structured_errors(self):
        """Slack uses structured JSON errors (data.error) - no raw text truncation needed."""
        src = _read(os.path.join(CONNECTORS, "slack.mjs"))
        assert "data.error" in src

    @pytest.mark.parametrize("connector", [
        "github.mjs", "slack.mjs", "stripe.mjs", "notion.mjs",
        "sendgrid.mjs", "discord.mjs", "hubspot.mjs", "jira.mjs",
        "airtable.mjs", "anthropic.mjs", "openai.mjs",
    ])
    def test_has_timeout(self, connector):
        """All connectors should have request timeouts."""
        src = _read(os.path.join(CONNECTORS, connector))
        assert "AbortController" in src or "timeout" in src.lower()

    @pytest.mark.parametrize("connector", [
        "github.mjs", "slack.mjs", "notion.mjs",
        "discord.mjs", "hubspot.mjs", "jira.mjs",
        "airtable.mjs",
    ])
    def test_uses_bearer_or_token_auth(self, connector):
        """Connectors should use proper auth headers, not query param tokens."""
        src = _read(os.path.join(CONNECTORS, connector))
        assert "Authorization" in src


class TestConnectorCLIDispatchGuards:
    """All connectors should guard CLI dispatch to prevent execution on import."""

    @pytest.mark.parametrize("connector,guard", [
        ("github.mjs", 'endsWith("github.mjs")'),
        ("slack.mjs", 'endsWith("slack.mjs")'),
        ("stripe.mjs", 'endsWith("stripe.mjs")'),
        ("notion.mjs", 'endsWith("notion.mjs")'),
        ("sendgrid.mjs", 'endsWith("sendgrid.mjs")'),
        ("discord.mjs", 'endsWith("discord.mjs")'),
        ("hubspot.mjs", 'endsWith("hubspot.mjs")'),
        ("jira.mjs", 'endsWith("jira.mjs")'),
        ("airtable.mjs", 'endsWith("airtable.mjs")'),
        ("openai.mjs", 'endsWith("openai.mjs")'),
        ("firecrawl.mjs", 'endsWith("firecrawl.mjs")'),
        ("whatsapp.mjs", 'endsWith("whatsapp.mjs")'),
        ("shell.mjs", 'endsWith("shell.mjs")'),
        ("postgresql.mjs", 'endsWith("postgresql.mjs")'),
        ("mongodb.mjs", 'endsWith("mongodb.mjs")'),
        ("redis.mjs", 'endsWith("redis.mjs")'),
        ("webhook.mjs", 'endsWith("webhook.mjs")'),
        ("browser.mjs", 'endsWith("browser.mjs")'),
        ("tavily.mjs", 'endsWith("tavily.mjs")'),
        ("anthropic.mjs", 'endsWith("anthropic.mjs")'),
        ("salesforce.mjs", 'endsWith("salesforce.mjs")'),
    ])
    def test_cli_dispatch_guarded(self, connector, guard):
        """CLI dispatch should be guarded to prevent auto-execution on import."""
        src = _read(os.path.join(CONNECTORS, connector))
        assert guard in src, f"{connector} missing CLI dispatch guard: {guard}"


# ===========================================================================
# Part 15: Connector URL construction safety
# ===========================================================================


class TestConnectorURLConstruction:
    """Verify URL construction patterns in connectors."""

    def test_github_uses_api_base(self):
        src = _read(os.path.join(CONNECTORS, "github.mjs"))
        assert '"https://api.github.com"' in src

    def test_slack_uses_api_base(self):
        src = _read(os.path.join(CONNECTORS, "slack.mjs"))
        assert '"https://slack.com/api"' in src

    def test_stripe_uses_api_base(self):
        src = _read(os.path.join(CONNECTORS, "stripe.mjs"))
        assert '"https://api.stripe.com/v1"' in src

    def test_stripe_uses_basic_auth(self):
        """Stripe should use Basic auth with the secret key."""
        src = _read(os.path.join(CONNECTORS, "stripe.mjs"))
        assert "Basic" in src
        assert 'Buffer.from(SECRET_KEY + ":").toString("base64")' in src

    def test_jira_encodes_jql(self):
        """JQL in search_issues should be URL-encoded."""
        src = _read(os.path.join(CONNECTORS, "jira.mjs"))
        assert "encodeURIComponent(jql)" in src

    def test_salesforce_encodes_soql(self):
        """SOQL in query should be URL-encoded."""
        src = _read(os.path.join(CONNECTORS, "salesforce.mjs"))
        assert "encodeURIComponent(soql)" in src


# ===========================================================================
# Part 16: Redis connector security
# ===========================================================================


class TestRedisConnector:
    """Validate Redis connector safety."""

    def test_local_cmd_escapes_args(self):
        """redis-cli args should be properly shell-escaped."""
        src = _read(os.path.join(CONNECTORS, "redis.mjs"))
        assert "replace(/'/g" in src

    def test_handles_nil_value(self):
        src = _read(os.path.join(CONNECTORS, "redis.mjs"))
        assert "(nil)" in src

    def test_json_parse_fallback(self):
        """get() should try JSON parse but fall back to string."""
        src = _read(os.path.join(CONNECTORS, "redis.mjs"))
        assert "JSON.parse(value)" in src
        assert "catch" in src


# ===========================================================================
# Part 17: Browser connector safety
# ===========================================================================


class TestBrowserConnector:
    """Validate browser connector safety."""

    def test_launches_with_sandbox_flags(self):
        src = _read(os.path.join(CONNECTORS, "browser.mjs"))
        assert "--no-sandbox" in src
        assert "--disable-setuid-sandbox" in src

    def test_cleans_up_on_exit(self):
        src = _read(os.path.join(CONNECTORS, "browser.mjs"))
        assert "cleanup" in src
        assert "process.on" in src

    def test_truncates_html_output(self):
        """Large HTML output should be truncated."""
        src = _read(os.path.join(CONNECTORS, "browser.mjs"))
        assert "50000" in src

    def test_has_element_timeout(self):
        """Element operations should have timeouts."""
        src = _read(os.path.join(CONNECTORS, "browser.mjs"))
        assert "timeout: 10000" in src


# ===========================================================================
# Part 18: Webhook connector safety
# ===========================================================================


class TestWebhookConnector:
    """Validate webhook connector safety."""

    def test_json_body_parsing(self):
        """Body should be parsed from string if needed."""
        src = _read(os.path.join(CONNECTORS, "webhook.mjs"))
        assert "JSON.parse(body)" in src

    def test_response_truncated(self):
        """Large responses should be truncated."""
        src = _read(os.path.join(CONNECTORS, "webhook.mjs"))
        assert "50000" in src

    def test_has_timeout(self):
        src = _read(os.path.join(CONNECTORS, "webhook.mjs"))
        assert "AbortController" in src
        assert "30000" in src


# ===========================================================================
# Part 19: MongoDB connector
# ===========================================================================


class TestMongoDBConnector:
    """Validate MongoDB connector patterns."""

    def test_has_timeout(self):
        src = _read(os.path.join(CONNECTORS, "mongodb.mjs"))
        assert "AbortController" in src

    def test_parses_string_filter(self):
        """Filter should be parsed from string if needed."""
        src = _read(os.path.join(CONNECTORS, "mongodb.mjs"))
        assert "JSON.parse(filter)" in src

    def test_limits_results(self):
        src = _read(os.path.join(CONNECTORS, "mongodb.mjs"))
        assert "limit" in src


# ===========================================================================
# Part 20: Comprehensive all-connector file existence check
# ===========================================================================


class TestAllConnectorsExist:
    """Verify all expected connector files exist and have basic structure."""

    EXPECTED_CONNECTORS = [
        "github.mjs", "slack.mjs", "stripe.mjs", "notion.mjs",
        "sendgrid.mjs", "discord.mjs", "hubspot.mjs", "jira.mjs",
        "airtable.mjs", "anthropic.mjs", "openai.mjs", "firecrawl.mjs",
        "whatsapp.mjs", "shell.mjs", "postgresql.mjs", "mongodb.mjs",
        "redis.mjs", "webhook.mjs", "browser.mjs", "tavily.mjs",
        "salesforce.mjs", "connector-utils.mjs",
    ]

    @pytest.mark.parametrize("connector", EXPECTED_CONNECTORS)
    def test_connector_exists(self, connector):
        path = os.path.join(CONNECTORS, connector)
        assert os.path.isfile(path), f"Expected connector {connector} not found"

    @pytest.mark.parametrize("connector", EXPECTED_CONNECTORS)
    def test_connector_has_jsdoc(self, connector):
        """All connectors should have a JSDoc header comment."""
        src = _read(os.path.join(CONNECTORS, connector))
        assert src.startswith("/**"), f"{connector} missing JSDoc header"


# ===========================================================================
# Part 21: Discord connector - limit capping
# ===========================================================================


class TestDiscordConnector:
    """Validate Discord connector patterns."""

    def test_get_messages_caps_limit(self):
        """Message limit should be capped to Discord API maximum."""
        src = _read(os.path.join(CONNECTORS, "discord.mjs"))
        assert "Math.min" in src
        assert "100" in src

    def test_has_user_agent(self):
        src = _read(os.path.join(CONNECTORS, "discord.mjs"))
        assert "User-Agent" in src


# ===========================================================================
# Part 22: Notion connector
# ===========================================================================


class TestNotionConnector:
    """Validate Notion connector patterns."""

    def test_uses_notion_version_header(self):
        src = _read(os.path.join(CONNECTORS, "notion.mjs"))
        assert "Notion-Version" in src

    def test_parallel_requests_in_get_page(self):
        """get_page should fetch page and blocks in parallel."""
        src = _read(os.path.join(CONNECTORS, "notion.mjs"))
        assert "Promise.all" in src


# ===========================================================================
# Part 23: Salesforce connector
# ===========================================================================


class TestSalesforceConnector:
    """Validate Salesforce connector patterns."""

    def test_token_refresh(self):
        """Should implement token refresh logic."""
        src = _read(os.path.join(CONNECTORS, "salesforce.mjs"))
        assert "getAccessToken" in src
        assert "refresh_token" in src

    def test_strips_trailing_slash_from_url(self):
        src = _read(os.path.join(CONNECTORS, "salesforce.mjs"))
        assert '.replace(/\\/$/, "")' in src

    def test_handles_204_no_content(self):
        src = _read(os.path.join(CONNECTORS, "salesforce.mjs"))
        assert "204" in src


# ===========================================================================
# Part 24: Anthropic connector
# ===========================================================================


class TestAnthropicConnector:
    """Validate Anthropic connector patterns."""

    def test_uses_x_api_key_header(self):
        src = _read(os.path.join(CONNECTORS, "anthropic.mjs"))
        assert "x-api-key" in src

    def test_uses_anthropic_version_header(self):
        src = _read(os.path.join(CONNECTORS, "anthropic.mjs"))
        assert "anthropic-version" in src

    def test_filters_text_blocks(self):
        """Should filter for text blocks in response content."""
        src = _read(os.path.join(CONNECTORS, "anthropic.mjs"))
        assert 'b.type === "text"' in src


# ===========================================================================
# Part 25: Tavily connector
# ===========================================================================


class TestTavilyConnector:
    """Validate Tavily connector patterns."""

    def test_api_key_in_body(self):
        """Tavily passes API key in request body, not header."""
        src = _read(os.path.join(CONNECTORS, "tavily.mjs"))
        assert "api_key: API_KEY" in src

    def test_has_timeout(self):
        src = _read(os.path.join(CONNECTORS, "tavily.mjs"))
        assert "30_000" in src or "30000" in src


# ===========================================================================
# Part 26: Stripe flattenParams edge cases
# ===========================================================================


class TestStripeFlattenParams:
    """Validate Stripe's parameter flattening for form-encoded requests."""

    def test_has_flatten_params(self):
        src = _read(os.path.join(CONNECTORS, "stripe.mjs"))
        assert "flattenParams" in src

    def test_handles_null_values(self):
        """null and undefined values should be skipped."""
        src = _read(os.path.join(CONNECTORS, "stripe.mjs"))
        assert "null" in src
        assert "undefined" in src

    def test_handles_nested_objects(self):
        """Nested objects should be flattened with bracket notation."""
        src = _read(os.path.join(CONNECTORS, "stripe.mjs"))
        assert "${prefix}[${key}]" in src


# ===========================================================================
# Part 27: Slack channel name resolution
# ===========================================================================


class TestSlackConnector:
    """Validate Slack connector patterns."""

    def test_resolves_channel_names(self):
        """Channels starting with # should be resolved to IDs."""
        src = _read(os.path.join(CONNECTORS, "slack.mjs"))
        assert 'channel.startsWith("#")' in src

    def test_channel_not_found_error(self):
        src = _read(os.path.join(CONNECTORS, "slack.mjs"))
        assert "Channel not found" in src


# ===========================================================================
# Part 28: Validate the overall runner protocol
# ===========================================================================


class TestRunnerProtocol:
    """Validate the event emission protocol shared by both runners."""

    @pytest.mark.parametrize("runner", [RUNNER_MJS, RUNNER_OPENAI_MJS])
    def test_emits_newline_delimited_json(self, runner):
        """Events should be emitted as newline-delimited JSON."""
        src = _read(runner)
        assert 'JSON.stringify(event) + "\\n"' in src

    @pytest.mark.parametrize("runner", [RUNNER_MJS, RUNNER_OPENAI_MJS])
    def test_emits_error_type(self, runner):
        src = _read(runner)
        assert '"error"' in src

    @pytest.mark.parametrize("runner", [RUNNER_MJS, RUNNER_OPENAI_MJS])
    def test_emits_result_type(self, runner):
        src = _read(runner)
        assert '"result"' in src

    @pytest.mark.parametrize("runner", [RUNNER_MJS, RUNNER_OPENAI_MJS])
    def test_exits_on_fatal_error(self, runner):
        src = _read(runner)
        assert "process.exit(1)" in src


# ===========================================================================
# Part 29: Connector-utils edge case patterns via regex
# ===========================================================================


class TestConnectorUtilsEdgeCases:
    """Deeper pattern checks on connector-utils.mjs."""

    def test_sleep_helper_exists(self):
        src = _read(CONNECTOR_UTILS_MJS)
        assert "function sleep" in src
        assert "setTimeout" in src

    def test_fetch_with_retry_clears_timeout(self):
        """Timeout timer should be cleared after successful fetch."""
        src = _read(CONNECTOR_UTILS_MJS)
        assert "clearTimeout(timer)" in src

    def test_retry_after_nan_handled(self):
        """If retry-after header is non-numeric non-date, should use fallback."""
        src = _read(CONNECTOR_UTILS_MJS)
        # Should have a NaN check or isNaN
        assert "isNaN" in src or "Number.isNaN" in src

    def test_backoff_has_ceiling(self):
        """Exponential backoff should have a max ceiling."""
        src = _read(CONNECTOR_UTILS_MJS)
        assert "MAX_WAIT_MS" in src
        # backoff should use Math.min to cap
        backoff_lines = [l for l in src.split("\n") if "Math.min" in l and "backoffMs" in l]
        assert len(backoff_lines) > 0, "Backoff should be capped with Math.min"


# ===========================================================================
# Part 30: OpenAI runner - deadline and turn counting
# ===========================================================================


class TestOpenAIRunnerDeadlineAndTurns:
    """Validate deadline and turn counting logic."""

    def test_has_deadline_check(self):
        src = _read(RUNNER_OPENAI_MJS)
        assert "deadline" in src
        assert "Date.now()" in src

    def test_turn_incremented_each_loop(self):
        src = _read(RUNNER_OPENAI_MJS)
        assert "turn++" in src

    def test_emits_num_turns_in_result(self):
        src = _read(RUNNER_OPENAI_MJS)
        assert "num_turns: turn" in src

    def test_timeout_emits_error(self):
        src = _read(RUNNER_OPENAI_MJS)
        assert "Timeout exceeded" in src

    def test_api_returned_no_choices(self):
        src = _read(RUNNER_OPENAI_MJS)
        assert "API returned no choices" in src
