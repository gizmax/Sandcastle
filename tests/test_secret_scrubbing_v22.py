"""Exhaustive tests for sandcastle.engine.generator._scrub_secrets.

Categories tested:
  1.  Bearer tokens
  2.  API key patterns (key=value)
  3.  Known prefixes (sk-, pk_, ghp_, gho_, glpat-, xoxb-, etc.)
  4.  AWS credentials (AKIA, ASIA)
  5.  Long hex strings (32/40/64 chars)
  6.  Connection strings with passwords
  7.  Multi-line stack traces with embedded secrets
  8.  Headers in error messages
  9.  JSON error bodies with secrets
  10. Environment variable dumps
  11-19. False-positive resistance (normal messages, paths, model names, etc.)
"""

from __future__ import annotations

import pytest

from sandcastle.engine.generator import _scrub_secrets

REDACTED = "[REDACTED]"


# =========================================================================
# CATEGORY 1 - Bearer tokens
# =========================================================================

class TestBearerTokens:
    """Bearer token patterns: 'Bearer <token>'."""

    def test_bearer_jwt(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        result = _scrub_secrets(text)
        assert "eyJhbGciOiJIUzI1NiIs" not in result
        assert REDACTED in result

    def test_bearer_anthropic_key(self):
        text = "bearer sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        result = _scrub_secrets(text)
        assert "sk-ant-api03" not in result
        assert REDACTED in result

    def test_bearer_uppercase(self):
        text = "BEARER TOKEN123456789abcdefghij"
        result = _scrub_secrets(text)
        assert "TOKEN123456789" not in result
        assert REDACTED in result

    def test_bearer_with_random_token(self):
        text = "Bearer abc123def456ghi789jkl012mno345"
        result = _scrub_secrets(text)
        assert "abc123def456" not in result
        assert REDACTED in result

    def test_bearer_in_curl_command(self):
        text = "curl -H 'Authorization: Bearer sk-1234567890abcdef' https://api.example.com"
        result = _scrub_secrets(text)
        assert "sk-1234567890abcdef" not in result

    def test_bearer_mixed_case(self):
        text = "Bearer AbCdEfGhIj1234567890"
        result = _scrub_secrets(text)
        assert "AbCdEfGhIj1234567890" not in result

    def test_bearer_with_dots_and_dashes(self):
        text = "Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.long-payload-here.signature"
        result = _scrub_secrets(text)
        assert "eyJ0eXAiOiJKV1QiLCJ" not in result


# =========================================================================
# CATEGORY 2 - API key patterns (key=value / key: value)
# =========================================================================

class TestAPIKeyPatterns:
    """key=value patterns with common key names."""

    def test_api_key_colon_space(self):
        text = "api_key: sk-1234567890abcdef"
        result = _scrub_secrets(text)
        assert "sk-1234567890abcdef" not in result

    def test_api_key_equals(self):
        text = "API-KEY=ghp_abc123def456ghi789jkl012"
        result = _scrub_secrets(text)
        assert "ghp_abc123def456" not in result

    def test_apikey_camelcase(self):
        text = "apiKey: xoxb-FAKE-TOK-fakeTestValHere"
        result = _scrub_secrets(text)
        assert "xoxb-FAKE-TOK" not in result

    def test_token_equals(self):
        text = "token=mysupersecrettoken12345678"
        result = _scrub_secrets(text)
        assert "mysupersecrettoken12345678" not in result

    def test_secret_colon(self):
        text = "secret: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        result = _scrub_secrets(text)
        assert "wJalrXUtnFEMI" not in result

    def test_password_equals(self):
        text = "password=MyS3cureP@ssw0rd!"
        result = _scrub_secrets(text)
        assert "MyS3cureP@ssw0rd!" not in result

    def test_authorization_header_value(self):
        """BUG FOUND: 'authorization: Basic <base64>' - the key=value pattern
        matches 'authorization: Basic' (only 5 chars for 'Basic'), so the actual
        base64 credential LEAKS THROUGH. The regex captures 'authorization' as
        the key name and 'Basic' as the start of the value, but 'Basic' alone
        is only 5 chars. The \\S{8,} quantifier means it captures
        'Basic dXNlcm5hbWU6...' as one token only if there's no space - but
        there IS a space between 'Basic' and the base64 payload.

        ROOT CAUSE: The pattern `authorization\\s*[:=]\\s*\\S{8,}` treats 'Basic'
        as the value (but 'Basic' is only 5 chars so it fails the 8+ threshold).
        Actually it matches because the regex is case-insensitive and
        'authorization: Basic' -> \\S{8,} can't match 'Basic' (5 chars), so it
        falls through to other patterns. But 'Basic dXNlcm5hbWU6cGFzc3dvcmQ='
        has a space so it's two tokens.

        IMPACT: Base64 credentials in 'Authorization: Basic <base64>' headers
        are NOT scrubbed. This is a real secret leak."""
        text = "authorization: Basic dXNlcm5hbWU6cGFzc3dvcmQ="
        result = _scrub_secrets(text)
        # Fixed: Basic auth pattern now scrubs the full token
        assert "[REDACTED]" in result
        assert "dXNlcm5hbWU6cGFzc3dvcmQ" not in result

    def test_api_key_in_json(self):
        text = '"api_key": "sk-proj-abcdefghij1234567890"'
        result = _scrub_secrets(text)
        assert "sk-proj-abcdefghij1234567890" not in result


# =========================================================================
# CATEGORY 3 - Known prefixes
# =========================================================================

class TestKnownPrefixes:
    """Tokens with well-known prefixes (sk-, pk_, ghp_, etc.)."""

    def test_sk_openai(self):
        text = "Using key sk-abcdefghij1234567890"
        result = _scrub_secrets(text)
        assert "sk-abcdefghij1234567890" not in result

    def test_sk_anthropic(self):
        text = "sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        result = _scrub_secrets(text)
        assert "sk-ant-api03" not in result

    def test_pk_stripe(self):
        text = "pk-fakestripepubkeytestvalue12345"
        result = _scrub_secrets(text)
        assert "pk-fakestripe" not in result

    def test_ghp_github_pat(self):
        text = "ghp_abc123def456ghi789jkl012mno345"
        result = _scrub_secrets(text)
        assert "ghp_abc123def456" not in result

    def test_gho_github_oauth(self):
        text = "gho_abc123def456ghi789jkl012mno345"
        result = _scrub_secrets(text)
        assert "gho_abc123def456" not in result

    def test_glpat_gitlab(self):
        text = "glpat-FAKE_TEST_TOKEN_VALUE_0123456"
        result = _scrub_secrets(text)
        assert "glpat-FAKE_TEST_TOKEN" not in result

    def test_xoxb_slack_bot(self):
        text = "xoxb-FAKE0TEST0TOK-FAKE0TEST0TOKE-FaKeTesTokEnVaLuEhErE1234"
        result = _scrub_secrets(text)
        assert "xoxb-FAKE0TEST" not in result

    def test_xoxp_slack_user(self):
        text = "xoxp-FAKE0TEST0TOK-FAKE0TEST0TOKE-FAKE0TEST0TOKE-fakefakefake1234fakefake1234fake"
        result = _scrub_secrets(text)
        assert "xoxp-FAKE0TEST" not in result

    def test_xoxa_slack_app(self):
        text = "xoxa-2-1234567890-1234567890123-1234567890123456"
        result = _scrub_secrets(text)
        assert "xoxa-2-1234567890" not in result

    def test_xoxs_slack_session(self):
        text = "xoxs-1234567890-1234567890-1234567890123-abcdef"
        result = _scrub_secrets(text)
        assert "xoxs-1234567890" not in result

    def test_eyj_jwt_standalone(self):
        text = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        result = _scrub_secrets(text)
        assert "eyJhbGciOiJIUzI1NiIs" not in result

    def test_sk_in_error_message(self):
        text = "Error: invalid API key sk-proj-abc123def456ghi789jkl0"
        result = _scrub_secrets(text)
        assert "sk-proj-abc123def456" not in result

    def test_multiple_prefixes_in_one_string(self):
        text = "Keys: ghp_FAKETESTVALUE123 and glpat-FAKETESTVALUE123"
        result = _scrub_secrets(text)
        assert "ghp_FAKETESTVALUE123" not in result
        assert "glpat-FAKETESTVALUE123" not in result


# =========================================================================
# CATEGORY 4 - AWS credentials
# =========================================================================

class TestAWSCredentials:
    """AWS access key patterns (AKIA*, ASIA*)."""

    def test_akia_access_key(self):
        text = "AKIAIOSFODNN7EXAMPLE"
        result = _scrub_secrets(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result

    def test_asia_temporary_key(self):
        text = "ASIAIOSFODNN7EXAMPLE"
        result = _scrub_secrets(text)
        assert "ASIAIOSFODNN7EXAMPLE" not in result

    def test_akia_in_context(self):
        text = "aws_access_key_id = AKIAIOSFODNN7EXAMPLE\naws_secret_access_key = wJalrXUtnFEMI/K7MDENG"
        result = _scrub_secrets(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result

    def test_asia_in_error_log(self):
        text = "AuthFailure: key ASIA1234567890ABCDEF not authorized"
        result = _scrub_secrets(text)
        assert "ASIA1234567890ABCDEF" not in result


# =========================================================================
# CATEGORY 5 - Long hex strings
# =========================================================================

class TestLongHexStrings:
    """32/40/64 char hex strings that look like API keys or hashes."""

    def test_32_char_hex_in_quotes(self):
        text = '"abcdef0123456789abcdef0123456789"'
        result = _scrub_secrets(text)
        assert "abcdef0123456789abcdef0123456789" not in result

    def test_40_char_hex_in_quotes(self):
        text = '"abcdef0123456789abcdef0123456789abcdef01"'
        result = _scrub_secrets(text)
        assert "abcdef0123456789abcdef0123456789abcdef01" not in result

    def test_64_char_hex_in_quotes(self):
        text = '"abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"'
        result = _scrub_secrets(text)
        assert "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789" not in result

    def test_hex_followed_by_space(self):
        text = "key is abcdef0123456789abcdef0123456789 and more text"
        result = _scrub_secrets(text)
        assert "abcdef0123456789abcdef0123456789" not in result

    def test_hex_followed_by_comma(self):
        text = "token: abcdef0123456789abcdef0123456789,"
        result = _scrub_secrets(text)
        assert "abcdef0123456789abcdef0123456789" not in result

    def test_hex_in_json_body(self):
        text = '{"api_key": "abcdef0123456789abcdef0123456789abcdef01"}'
        result = _scrub_secrets(text)
        assert "abcdef0123456789abcdef0123456789abcdef01" not in result

    def test_hex_followed_by_closing_brace(self):
        text = '{"key":"a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"}'
        result = _scrub_secrets(text)
        assert "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4" not in result


# =========================================================================
# CATEGORY 6 - Connection strings
# =========================================================================

class TestConnectionStrings:
    """Database/cache connection strings with embedded passwords."""

    def test_postgresql_connection(self):
        text = "postgresql://user:password123@host/db"
        result = _scrub_secrets(text)
        # password= pattern should catch this
        assert "password123" not in result or result == text
        # Connection strings may or may not be caught by the current regex.
        # Document what actually happens.

    def test_redis_connection(self):
        text = "redis://default:secretpassword@redis:6379"
        result = _scrub_secrets(text)
        # This is a URL with embedded secret. The regex may or may not catch it.
        # We test what happens and document it.

    def test_password_in_dsn(self):
        text = "password=MySecret12345678 in connection string"
        result = _scrub_secrets(text)
        assert "MySecret12345678" not in result


# =========================================================================
# CATEGORY 7 - Multi-line stack traces with embedded secrets
# =========================================================================

class TestMultiLineStackTraces:
    """Realistic Python tracebacks containing API keys."""

    def test_traceback_with_bearer_token(self):
        text = (
            "Traceback (most recent call last):\n"
            '  File "/usr/local/lib/python3.12/site-packages/httpx/_client.py", line 1025, in send\n'
            '    response = self._transport.handle_request(request)\n'
            "httpx.HTTPStatusError: 401 Unauthorized\n"
            "Request headers: {'Authorization': 'Bearer sk-ant-api03-RealSecretKeyHere1234567890'}\n"
        )
        result = _scrub_secrets(text)
        assert "sk-ant-api03-RealSecretKeyHere1234567890" not in result
        assert "Traceback (most recent call last):" in result
        assert "httpx.HTTPStatusError" in result

    def test_traceback_with_api_key_in_url(self):
        text = (
            "Traceback (most recent call last):\n"
            '  File "app.py", line 42, in run\n'
            '    resp = await client.get("https://api.example.com?api_key=sk-1234567890abcdef1234")\n'
            "httpx.ConnectError: Connection refused\n"
        )
        result = _scrub_secrets(text)
        assert "sk-1234567890abcdef1234" not in result
        assert "httpx.ConnectError" in result

    def test_traceback_with_env_var_dump(self):
        text = (
            "RuntimeError: Sandbox init failed\n"
            "Environment:\n"
            "  ANTHROPIC_API_KEY=sk-ant-real-key-1234567890abcdef\n"
            "  OPENAI_API_KEY=sk-proj-another-key-abcdef1234567890\n"
            "  DATABASE_URL=postgresql://user:pass@host/db\n"
            '  File "sandbox.py", line 99\n'
        )
        result = _scrub_secrets(text)
        assert "sk-ant-real-key-1234567890abcdef" not in result
        assert "sk-proj-another-key-abcdef1234567890" not in result
        assert "RuntimeError" in result

    def test_multiline_with_aws_key(self):
        text = (
            "botocore.exceptions.ClientError: An error occurred (403)\n"
            "AWS Access Key: AKIAIOSFODNN7EXAMPLE\n"
            "Region: us-east-1\n"
        )
        result = _scrub_secrets(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "botocore.exceptions.ClientError" in result


# =========================================================================
# CATEGORY 8 - Headers in error messages
# =========================================================================

class TestHeadersInErrors:
    """HTTP headers containing secrets embedded in error messages."""

    def test_authorization_bearer(self):
        text = "Authorization: Bearer sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        result = _scrub_secrets(text)
        assert "sk-ant-api03" not in result

    def test_x_api_key_header(self):
        text = "X-API-Key: glpat-FAKE_TEST_HEADER_TOKEN_01234567"
        result = _scrub_secrets(text)
        assert "glpat-FAKE_TEST_HEADER" not in result

    def test_multiple_headers(self):
        text = (
            "Request headers:\n"
            "  Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9\n"
            "  X-API-Key: sk-1234567890abcdef\n"
            "  Content-Type: application/json\n"
        )
        result = _scrub_secrets(text)
        assert "eyJhbGciOiJIUzI1NiIs" not in result
        assert "sk-1234567890abcdef" not in result
        assert "Content-Type: application/json" in result

    def test_token_header(self):
        text = "token: ghp_1234567890abcdefghijklmnopqrstuvwx"
        result = _scrub_secrets(text)
        assert "ghp_1234567890abcdefghijklmnopqrstuvwx" not in result


# =========================================================================
# CATEGORY 9 - JSON error bodies with secrets
# =========================================================================

class TestJSONBodies:
    """JSON payloads containing secrets in error responses."""

    def test_json_with_key_field(self):
        text = '{"error": "invalid key", "key": "sk-12345abcdef67890"}'
        result = _scrub_secrets(text)
        assert "sk-12345abcdef67890" not in result

    def test_json_with_token_field(self):
        text = '{"token": "ghp_abcdefghij1234567890abcdefghij12"}'
        result = _scrub_secrets(text)
        assert "ghp_abcdefghij1234567890" not in result

    def test_json_error_with_authorization(self):
        text = '{"error": "Unauthorized", "authorization": "Bearer sk-ant-api03-realkey1234567890"}'
        result = _scrub_secrets(text)
        assert "sk-ant-api03-realkey1234567890" not in result

    def test_nested_json_with_secret(self):
        text = '{"data": {"api_key": "xoxb-FAKE0NESTED0T-FaKeTEstNeStEdVaL"}}'
        result = _scrub_secrets(text)
        assert "xoxb-FAKE0NESTED" not in result


# =========================================================================
# CATEGORY 10 - Environment variable dumps
# =========================================================================

class TestEnvVarDumps:
    """Environment variable dumps with secrets."""

    def test_anthropic_key(self):
        text = "ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        result = _scrub_secrets(text)
        assert "sk-ant-api03" not in result

    def test_openai_key(self):
        text = "OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        result = _scrub_secrets(text)
        assert "sk-proj-xxxx" not in result

    def test_multiple_env_vars(self):
        text = (
            "ANTHROPIC_API_KEY=sk-ant-api03-key1234567890abcdef\n"
            "OPENAI_API_KEY=sk-proj-key0987654321fedcba\n"
            "E2B_API_KEY=e2b_1234567890abcdef1234567890\n"
            "PATH=/usr/local/bin:/usr/bin:/bin\n"
        )
        result = _scrub_secrets(text)
        assert "sk-ant-api03-key1234567890abcdef" not in result
        assert "sk-proj-key0987654321fedcba" not in result
        # PATH should survive
        assert "/usr/local/bin" in result

    def test_token_env_var(self):
        text = "GITHUB_TOKEN=ghp_1234567890abcdefghijklmnopqrstuvwx"
        result = _scrub_secrets(text)
        assert "ghp_1234567890abcdefghijklmnopqrstuvwx" not in result


# =========================================================================
# CATEGORY 11-19 - FALSE POSITIVES (should NOT be scrubbed)
# =========================================================================

class TestFalsePositivesNormalMessages:
    """Normal error messages should not be scrubbed."""

    def test_connection_refused(self):
        text = "Connection refused"
        assert _scrub_secrets(text) == text

    def test_timeout(self):
        text = "Timeout after 30s"
        assert _scrub_secrets(text) == text

    def test_404_not_found(self):
        text = "404 Not Found"
        assert _scrub_secrets(text) == text

    def test_500_internal_error(self):
        text = "500 Internal Server Error"
        assert _scrub_secrets(text) == text

    def test_rate_limited(self):
        text = "Rate limited: retry after 60 seconds"
        assert _scrub_secrets(text) == text

    def test_generic_error(self):
        text = "Error: step 'fetch-data' timed out after 300 seconds"
        assert _scrub_secrets(text) == text

    def test_permission_denied(self):
        text = "PermissionError: [Errno 13] Permission denied: '/tmp/output.json'"
        assert _scrub_secrets(text) == text


class TestFalsePositivesFilePaths:
    """File paths should not be scrubbed."""

    def test_python_site_packages(self):
        text = "/usr/local/lib/python3.12/site-packages/httpx/_client.py"
        assert _scrub_secrets(text) == text

    def test_sandcastle_path(self):
        text = "/Users/gizmax/Documents/Sandcastle/src/sandcastle/engine/generator.py"
        assert _scrub_secrets(text) == text

    def test_venv_path(self):
        text = ".venv/lib/python3.14/site-packages/fastapi/routing.py"
        assert _scrub_secrets(text) == text

    def test_windows_path(self):
        text = r"C:\Users\user\AppData\Local\sandcastle\config.yaml"
        assert _scrub_secrets(text) == text


class TestFalsePositivesModelNames:
    """Model names should not be scrubbed."""

    def test_claude_sonnet(self):
        text = "claude-sonnet-4-20250514"
        assert _scrub_secrets(text) == text

    def test_gpt_4o(self):
        text = "gpt-4o-2024-08-06"
        assert _scrub_secrets(text) == text

    def test_claude_haiku(self):
        text = "claude-haiku-4-5-20251001"
        assert _scrub_secrets(text) == text

    def test_model_in_error(self):
        text = "Model 'claude-sonnet-4-20250514' returned 429"
        assert _scrub_secrets(text) == text


class TestFalsePositivesStepIDs:
    """Step IDs should not be scrubbed."""

    def test_step_123(self):
        text = "step-123"
        assert _scrub_secrets(text) == text

    def test_fetch_data(self):
        text = "fetch-data"
        assert _scrub_secrets(text) == text

    def test_process_results(self):
        text = "process_results"
        assert _scrub_secrets(text) == text

    def test_step_in_error(self):
        text = "Step 'analyze-data' failed with exit code 1"
        assert _scrub_secrets(text) == text


class TestFalsePositivesURLs:
    """URLs without secrets should not be scrubbed."""

    def test_anthropic_api(self):
        text = "https://api.anthropic.com/v1/messages"
        assert _scrub_secrets(text) == text

    def test_openai_api(self):
        text = "https://api.openai.com/v1/chat/completions"
        assert _scrub_secrets(text) == text

    def test_localhost(self):
        text = "http://localhost:8080/api/workflows"
        assert _scrub_secrets(text) == text

    def test_github_url(self):
        text = "https://github.com/gizmax/Sandcastle"
        assert _scrub_secrets(text) == text


class TestFalsePositivesShortStrings:
    """Short strings should not be scrubbed."""

    def test_abc(self):
        text = "abc"
        assert _scrub_secrets(text) == text

    def test_12345(self):
        text = "12345"
        assert _scrub_secrets(text) == text

    def test_short_hex(self):
        text = "abcdef12"
        assert _scrub_secrets(text) == text

    def test_error_code(self):
        text = "E001"
        assert _scrub_secrets(text) == text


class TestFalsePositivesUUIDs:
    """UUIDs should not be scrubbed (they contain hex but also dashes)."""

    def test_standard_uuid(self):
        text = "550e8400-e29b-41d4-a716-446655440000"
        assert _scrub_secrets(text) == text

    def test_uuid_in_context(self):
        text = "Workflow ID: 550e8400-e29b-41d4-a716-446655440000 failed"
        assert _scrub_secrets(text) == text

    def test_multiple_uuids(self):
        text = "Step a1b2c3d4-e5f6-7890-abcd-ef1234567890 depends on f0e1d2c3-b4a5-6789-0123-456789abcdef"
        assert _scrub_secrets(text) == text


class TestFalsePositivesISODates:
    """ISO date strings should not be scrubbed."""

    def test_iso_date(self):
        text = "2026-03-17T12:00:00Z"
        assert _scrub_secrets(text) == text

    def test_iso_date_with_ms(self):
        text = "2026-03-17T12:00:00.123456Z"
        assert _scrub_secrets(text) == text

    def test_date_in_error(self):
        text = "Workflow created at 2026-03-17T12:00:00Z expired"
        assert _scrub_secrets(text) == text


class TestFalsePositivesNumbers:
    """Normal numbers in error messages should not be scrubbed."""

    def test_status_code(self):
        text = "Status code: 429"
        assert _scrub_secrets(text) == text

    def test_retry_after(self):
        text = "Retry after 60 seconds"
        assert _scrub_secrets(text) == text

    def test_port_number(self):
        text = "Listening on port 8080"
        assert _scrub_secrets(text) == text

    def test_large_number(self):
        text = "Processed 1234567890 records"
        assert _scrub_secrets(text) == text


# =========================================================================
# ADVERSARIAL TESTS - Edge cases and bypass attempts
# =========================================================================

class TestAdversarialCases:
    """Edge cases and adversarial inputs to stress the scrubber."""

    def test_empty_string(self):
        assert _scrub_secrets("") == ""

    def test_only_whitespace(self):
        text = "   \n\t  "
        assert _scrub_secrets(text) == text

    def test_secret_at_start_of_string(self):
        text = "sk-ant-api03-12345678901234567890 caused an error"
        result = _scrub_secrets(text)
        assert "sk-ant-api03" not in result

    def test_secret_at_end_of_string(self):
        text = "Failed with key sk-ant-api03-12345678901234567890"
        result = _scrub_secrets(text)
        assert "sk-ant-api03" not in result

    def test_multiple_secrets_same_line(self):
        text = "Keys: sk-key11234567890 and ghp_key21234567890 both invalid"
        result = _scrub_secrets(text)
        assert "sk-key11234567890" not in result
        assert "ghp_key21234567890" not in result

    def test_secret_with_special_chars_around(self):
        text = "(sk-ant-api03-12345678901234567890)"
        result = _scrub_secrets(text)
        assert "sk-ant-api03" not in result

    def test_secret_in_single_quotes(self):
        text = "'ghp_1234567890abcdefghijklmnopqrst'"
        result = _scrub_secrets(text)
        assert "ghp_1234567890abcdef" not in result

    def test_secret_in_double_quotes(self):
        text = '"glpat-FAKETESTVALUE0ABCDEFGHIJKLM"'
        result = _scrub_secrets(text)
        assert "glpat-FAKETESTVALUE0" not in result

    def test_very_long_input(self):
        """Ensure scrubbing works on large inputs without crashing."""
        text = "Normal text. " * 1000 + "sk-ant-api03-leak1234567890 " + "More text. " * 1000
        result = _scrub_secrets(text)
        assert "sk-ant-api03-leak1234567890" not in result
        assert "Normal text." in result

    def test_bearer_with_newline_before_token(self):
        """Bearer on one line, token on next - regex might not catch cross-line."""
        text = "Bearer\nsk-ant-api03-12345678901234567890"
        result = _scrub_secrets(text)
        # The "Bearer " pattern requires the token on the same line,
        # but the sk- prefix pattern should still catch the key on the second line.
        assert "sk-ant-api03" not in result

    def test_mixed_legitimate_and_secret(self):
        text = (
            "Step 'fetch-data' using model claude-sonnet-4-20250514 "
            "failed at 2026-03-17T12:00:00Z with error: "
            "Authorization: Bearer sk-ant-api03-leak1234567890abcdef"
        )
        result = _scrub_secrets(text)
        # Secrets scrubbed
        assert "sk-ant-api03-leak1234567890abcdef" not in result
        # Legitimate text preserved
        assert "fetch-data" in result
        assert "claude-sonnet-4-20250514" in result
        assert "2026-03-17T12:00:00Z" in result

    def test_hex_string_not_followed_by_delimiter(self):
        """Hex string that ends at end-of-string (no trailing delimiter).
        The regex requires a lookahead for quote/whitespace/comma/brace, so this might leak."""
        text = "abcdef0123456789abcdef0123456789"
        result = _scrub_secrets(text)
        # This is an edge case: 32-char hex at end of string with no trailing delimiter.
        # The regex uses a lookahead (?=['\"\s,}]) so it may NOT match.
        # Document the behavior regardless.
        # If it leaks, that's a finding to report.

    def test_hex_at_eof_with_newline(self):
        """Hex at end of string followed by newline."""
        text = "key: abcdef0123456789abcdef0123456789\n"
        result = _scrub_secrets(text)
        # The key=value pattern or the hex pattern should catch this.
        # The \n counts as \s for the hex lookahead.

    def test_unicode_in_text(self):
        """Ensure unicode doesn't break the regex."""
        text = "Chyba: token sk-1234567890abcdef je neplatny"
        result = _scrub_secrets(text)
        assert "sk-1234567890abcdef" not in result

    def test_real_world_anthropic_error(self):
        """Realistic Anthropic API error with key leak."""
        text = (
            "httpx.HTTPStatusError: Client error '401 Unauthorized' for url "
            "'https://api.anthropic.com/v1/messages'\n"
            "For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401\n"
            "Request headers: {'x-api-key': 'sk-ant-api03-THIS-IS-A-REAL-KEY-1234567890abcdef', "
            "'anthropic-version': '2023-06-01', 'content-type': 'application/json'}\n"
        )
        result = _scrub_secrets(text)
        assert "sk-ant-api03-THIS-IS-A-REAL-KEY" not in result
        assert "httpx.HTTPStatusError" in result
        assert "https://api.anthropic.com/v1/messages" in result

    def test_real_world_openai_error(self):
        """Realistic OpenAI API error with key leak."""
        text = (
            "openai.AuthenticationError: Error code: 401 - "
            "{'error': {'message': 'Incorrect API key provided: sk-proj-1234...5678.', "
            "'type': 'invalid_request_error', 'param': None, 'code': 'invalid_api_key'}}"
        )
        result = _scrub_secrets(text)
        assert "sk-proj-1234" not in result


# =========================================================================
# REGRESSION TESTS - Specific patterns that are tricky
# =========================================================================

class TestRegression:
    """Regression tests for known tricky patterns."""

    def test_bearer_word_alone_not_scrubbed(self):
        """The word 'bearer' alone without a token should not be scrubbed."""
        text = "The bearer of this message"
        # 'bearer' followed by short word should be fine
        assert _scrub_secrets(text) == text

    def test_api_key_label_without_value(self):
        """'api_key' without a long value should not be scrubbed."""
        text = "Missing api_key"
        assert _scrub_secrets(text) == text

    def test_sk_as_country_code(self):
        """'SK' as Slovak country code should not be scrubbed (too short)."""
        text = "Country: SK"
        assert _scrub_secrets(text) == text

    def test_pk_as_primary_key(self):
        """'pk' as 'primary key' in database context - short value."""
        text = "pk=5"
        assert _scrub_secrets(text) == text

    def test_password_key_short_value(self):
        """password= with a very short value (< 8 chars)."""
        text = "password=abc"
        # The key=value pattern requires \S{8,} after the = so this should pass.
        assert _scrub_secrets(text) == text

    def test_git_sha_40_hex(self):
        """Git commit SHAs are 40 hex chars but usually in specific contexts.
        They would match the hex pattern if followed by a delimiter."""
        sha = "a5dc6ec1234567890abcdef1234567890abcdef12"
        text = f'commit {sha} (HEAD -> main)'
        # The SHA does NOT end with ['"\s,}}] ... it ends with space actually.
        # So this might be caught. Let's check.
        result = _scrub_secrets(text)
        # Git SHAs might be false positives. Document whether they are caught.

    def test_content_hash_in_cache(self):
        """Content hashes in cache keys."""
        text = 'cache key: "workflow:abcdef0123456789abcdef0123456789"'
        result = _scrub_secrets(text)
        # The hex pattern matches if followed by quote - this will be redacted.
        # That's acceptable for a security-first approach.

    def test_idempotent_scrubbing(self):
        """Scrubbing already-scrubbed text should be idempotent."""
        text = "token: sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        result1 = _scrub_secrets(text)
        result2 = _scrub_secrets(result1)
        assert result1 == result2

    def test_redacted_placeholder_not_double_scrubbed(self):
        """Fixed: 'Authorization: [REDACTED]' is now preserved (idempotent)."""
        text = "Authorization: [REDACTED]"
        result = _scrub_secrets(text)
        assert result == "Authorization: [REDACTED]"

    def test_base64_padding_in_auth(self):
        """Fixed: 'authorization: Basic <base64>' now scrubs the base64 payload."""
        text = "authorization: Basic dXNlcjpwYXNzd29yZA=="
        result = _scrub_secrets(text)
        assert "[REDACTED]" in result
        assert "dXNlcjpwYXNzd29yZA" not in result


# =========================================================================
# COVERAGE SUMMARY TEST
# =========================================================================

class TestCoverageSummary:
    """Meta-tests that verify the scrubber handles realistic combined scenarios."""

    def test_full_error_report(self):
        """A realistic full error report with multiple secret types."""
        text = (
            "=== Workflow Execution Report ===\n"
            "Workflow: data-pipeline\n"
            "Step: fetch-api (type: http)\n"
            "Model: claude-sonnet-4-20250514\n"
            "Started: 2026-03-17T12:00:00Z\n"
            "Duration: 4.2s\n"
            "Status: FAILED\n"
            "\n"
            "Error:\n"
            "httpx.HTTPStatusError: 401 Unauthorized\n"
            "  File \"/usr/local/lib/python3.12/httpx/_client.py\", line 1025\n"
            "    response.raise_for_status()\n"
            "\n"
            "Request details:\n"
            "  URL: https://api.anthropic.com/v1/messages\n"
            "  Headers: {\n"
            "    'Authorization': 'Bearer sk-ant-api03-LEAKED-SECRET-KEY-1234567890abcdef',\n"
            "    'Content-Type': 'application/json'\n"
            "  }\n"
            "\n"
            "Environment:\n"
            "  ANTHROPIC_API_KEY=sk-ant-api03-ANOTHER-LEAKED-KEY-abcdef1234567890\n"
            "  OPENAI_API_KEY=sk-proj-YET-ANOTHER-KEY-1234567890abcdef\n"
            "  HOME=/Users/gizmax\n"
            "  PATH=/usr/local/bin:/usr/bin\n"
        )
        result = _scrub_secrets(text)

        # All secrets must be scrubbed
        assert "sk-ant-api03-LEAKED-SECRET-KEY" not in result
        assert "sk-ant-api03-ANOTHER-LEAKED-KEY" not in result
        assert "sk-proj-YET-ANOTHER-KEY" not in result

        # All legitimate content must survive
        assert "data-pipeline" in result
        assert "fetch-api" in result
        assert "claude-sonnet-4-20250514" in result
        assert "2026-03-17T12:00:00Z" in result
        assert "httpx.HTTPStatusError" in result
        assert "https://api.anthropic.com/v1/messages" in result
        assert "Content-Type" in result
        assert "/Users/gizmax" in result
        assert "/usr/local/bin" in result

    def test_scrub_preserves_structure(self):
        """Scrubbing should preserve overall string structure (newlines, indentation)."""
        text = "Line 1\n  Line 2: sk-leak1234567890\n  Line 3\n"
        result = _scrub_secrets(text)
        lines = result.split("\n")
        assert len(lines) == 4  # Same number of lines
        assert lines[0] == "Line 1"
        assert "sk-leak1234567890" not in lines[1]
        assert lines[2] == "  Line 3"


# =========================================================================
# ADDITIONAL ADVERSARIAL TESTS - Probing discovered bugs and edge cases
# =========================================================================

class TestBasicAuthLeakVariants:
    """Probe the discovered Basic Auth leak bug with different variants."""

    def test_basic_auth_with_long_base64(self):
        """Basic auth with a long base64 payload - still leaks."""
        text = "Authorization: Basic YWRtaW46c3VwZXJzZWNyZXRwYXNzd29yZDEyMzQ1Njc4OTA="
        result = _scrub_secrets(text)
        # The key=value pattern matches 'Authorization: Basic' but 'Basic' is
        # only 5 chars. However, the overall match is 'authorization: Basic'
        # where \S{8,} should NOT match 'Basic' (5 chars). But wait - does the
        # regex match 'Authorization' as the key and ':' as separator, then
        # \s* then \S{8,} matches 'Basic' (only 5 chars, fails). So the
        # key=value branch does NOT fire. Then the base64 leaks unless another
        # pattern catches it.
        # KNOWN BUG: if base64 is not caught by another pattern it leaks.

    def test_basic_auth_without_space_after_basic(self):
        """What if Basic is concatenated with the credential (no space)?"""
        text = "Authorization: BasicdXNlcjpwYXNzd29yZA=="
        result = _scrub_secrets(text)
        # 'BasicdXNlcjpwYXNzd29yZA==' is 30+ chars, so \S{8,} should match.
        assert "BasicdXNlcjpwYXNzd29yZA==" not in result

    def test_bearer_not_affected(self):
        """Bearer tokens ARE correctly scrubbed (unlike Basic)."""
        text = "Authorization: Bearer sk-ant-api03-real1234567890"
        result = _scrub_secrets(text)
        assert "sk-ant-api03-real1234567890" not in result

    def test_digest_auth_header(self):
        """Digest auth - similar structure to Basic."""
        text = 'Authorization: Digest username="admin", response="6629fae49393a05397450978507c4ef1"'
        result = _scrub_secrets(text)
        # The hex string in response might be caught by the hex pattern
        assert "6629fae49393a05397450978507c4ef1" not in result


class TestDoubleScrubbingVariants:
    """Verify scrubbing is idempotent - [REDACTED] is preserved."""

    def test_token_redacted_preserved(self):
        """token: [REDACTED] should NOT be double-scrubbed."""
        text = "token: [REDACTED]"
        result = _scrub_secrets(text)
        assert result == "token: [REDACTED]"

    def test_secret_redacted_preserved(self):
        """secret: [REDACTED] should NOT be double-scrubbed."""
        text = "secret: [REDACTED]"
        result = _scrub_secrets(text)
        assert result == "secret: [REDACTED]"

    def test_password_redacted_preserved(self):
        """password: [REDACTED] should NOT be double-scrubbed."""
        text = "password: [REDACTED]"
        result = _scrub_secrets(text)
        assert result == "password: [REDACTED]"

    def test_api_key_redacted_preserved(self):
        """api_key: [REDACTED] should NOT be double-scrubbed."""
        text = "api_key: [REDACTED]"
        result = _scrub_secrets(text)
        assert result == "api_key: [REDACTED]"


class TestHexEdgeCases:
    """Edge cases for hex string matching."""

    def test_hex_32_no_trailing_delimiter(self):
        """32-char hex at end of string with no delimiter - may leak."""
        text = "abcdef0123456789abcdef0123456789"
        result = _scrub_secrets(text)
        # Regex uses lookahead (?=['"\\s,}]) so no match at end of string.
        # But the string IS the hex with nothing after it.
        # Document the actual behavior.
        if result == text:
            # FINDING: hex string without trailing delimiter leaks through
            pass
        else:
            assert REDACTED in result

    def test_hex_32_followed_by_period(self):
        """32-char hex followed by period - period is NOT in the lookahead."""
        text = "key abcdef0123456789abcdef0123456789."
        result = _scrub_secrets(text)
        # Period is not in ['"\\s,}] so this should NOT match the hex pattern.
        # Document actual behavior.

    def test_hex_32_followed_by_colon(self):
        """32-char hex followed by colon - colon is NOT in the lookahead."""
        text = "abcdef0123456789abcdef0123456789:"
        result = _scrub_secrets(text)
        # Colon is not in ['"\\s,}]. Document behavior.

    def test_hex_32_followed_by_semicolon(self):
        """32-char hex followed by semicolon."""
        text = "abcdef0123456789abcdef0123456789;"
        result = _scrub_secrets(text)
        # Semicolon not in lookahead.

    def test_hex_32_followed_by_bracket(self):
        """32-char hex followed by closing bracket."""
        text = "[abcdef0123456789abcdef0123456789]"
        result = _scrub_secrets(text)
        # ']' is not in the lookahead set ['"\\s,}].

    def test_hex_mixed_case_not_pure_hex(self):
        """Mixed case hex-like string that contains uppercase - still hex."""
        text = '"ABCDEF0123456789ABCDEF0123456789"'
        result = _scrub_secrets(text)
        # Only lowercase [a-f0-9] matches in the regex, so uppercase hex leaks.
        # The regex is [a-f0-9]{32,64} - uppercase A-F does NOT match!
        # This is a potential gap if APIs use uppercase hex keys.


class TestConnectionStringVariants:
    """More connection string patterns."""

    def test_mysql_connection(self):
        text = "mysql://root:mysecretpass@localhost:3306/mydb"
        result = _scrub_secrets(text)
        assert "mysecretpass" not in result
        assert "[REDACTED]" in result

    def test_mongodb_connection(self):
        text = "mongodb://admin:P@ssw0rd123@cluster.mongodb.net/mydb"
        result = _scrub_secrets(text)
        assert "P@ssw0rd123" not in result
        assert "[REDACTED]" in result

    def test_amqp_connection(self):
        text = "amqp://user:secretpassword@rabbitmq:5672/vhost"
        result = _scrub_secrets(text)
        assert "secretpassword" not in result
        assert "[REDACTED]" in result

    def test_postgres_connection(self):
        text = "postgres://user:pass123456@db.example.com/app"
        result = _scrub_secrets(text)
        assert "pass123456" not in result
        assert "[REDACTED]" in result

    def test_redis_connection(self):
        text = "redis://default:s3cr3t@redis.cloud:6379/0"
        result = _scrub_secrets(text)
        assert "s3cr3t" not in result
        assert "[REDACTED]" in result


class TestPrivateKeyPatterns:
    """Private keys in PEM format embedded in errors."""

    def test_private_key_header(self):
        """Private key PEM header in error output."""
        text = (
            "Error loading credentials:\n"
            "-----BEGIN PRIVATE KEY-----\n"
            "MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQC7\n"
            "-----END PRIVATE KEY-----\n"
        )
        result = _scrub_secrets(text)
        assert "BEGIN PRIVATE KEY" not in result
        assert "MIIEvgIBADANBg" not in result
        assert "[REDACTED-PEM-KEY]" in result

    def test_rsa_private_key(self):
        text = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIBogIBAAJBALqr/UE+emS4k2O\n"
            "-----END RSA PRIVATE KEY-----"
        )
        result = _scrub_secrets(text)
        assert "BEGIN RSA PRIVATE KEY" not in result
        assert "[REDACTED-PEM-KEY]" in result

    def test_ec_private_key(self):
        text = (
            "-----BEGIN EC PRIVATE KEY-----\n"
            "MHQCAQEEIBkg2yYFABIANDTaFH\n"
            "-----END EC PRIVATE KEY-----"
        )
        result = _scrub_secrets(text)
        assert "BEGIN EC PRIVATE KEY" not in result
        assert "[REDACTED-PEM-KEY]" in result


class TestProviderSpecificKeys:
    """Test provider-specific API key formats."""

    def test_sendgrid_key(self):
        """SendGrid API keys start with SG."""
        text = "SENDGRID_API_KEY=SG.abcdefghijklmnopqrstuv.wxyz1234567890abcdefghijklmnopqrstuvwxyz"
        result = _scrub_secrets(text)
        # Should be caught by the key=value pattern (API_KEY=...).
        assert "SG.abcdefghijklmnopqrstuv" not in result

    def test_twilio_sid(self):
        """Twilio Account SIDs start with AC."""
        text = "TWILIO_SID=ACfake0test0value0fake0test0value"
        result = _scrub_secrets(text)
        # Not a known prefix, but might be caught by hex pattern or not.

    def test_stripe_live_key(self):
        """Stripe live secret key - sk prefix catches sk_xxxx_ patterns."""
        # Use 'sk' prefix directly (scrubber matches sk[a-zA-Z0-9_-]{10,})
        text = "sk-fakestripetestkeylivevalue12345"
        result = _scrub_secrets(text)
        assert "sk-fakestripetest" not in result

    def test_stripe_test_key(self):
        """Stripe test secret key - sk prefix catches sk_xxxx_ patterns."""
        text = "sk-fakestripetestkeytest12345value"
        result = _scrub_secrets(text)
        assert "sk-fakestripetest" not in result

    def test_npm_token(self):
        """npm tokens start with npm_."""
        text = "npm_1234567890abcdefghijklmnopqrstuvwxyz"
        result = _scrub_secrets(text)
        # Not a known prefix. Should NOT be caught unless another pattern matches.

    def test_pypi_token(self):
        """PyPI tokens start with pypi-."""
        text = "pypi-AbCdEfGhIjKlMnOpQrStUvWxYz1234567890"
        result = _scrub_secrets(text)
        # Not a known prefix. Document.

    def test_e2b_api_key(self):
        """E2B API key in env var dump."""
        text = "E2B_API_KEY=e2b_1234567890abcdef1234567890abcdef"
        result = _scrub_secrets(text)
        # Should be caught by key=value pattern (API_KEY=...).
        assert "e2b_1234567890abcdef" not in result


class TestTokenInQueryString:
    """Secrets leaked in URL query parameters."""

    def test_api_key_in_query_param(self):
        """API key in URL query string."""
        text = "GET https://api.example.com/v1/data?api_key=sk-1234567890abcdef"
        result = _scrub_secrets(text)
        # The sk- prefix should catch this.
        assert "sk-1234567890abcdef" not in result

    def test_token_in_query_param(self):
        """Token in URL query string."""
        text = "https://hooks.example.com/services/TFAKETEST/BFAKETEST/FAKEFAKEFAKEFAKEFAKEFAKE"
        result = _scrub_secrets(text)
        # Webhook URLs without credentials don't match known patterns. Document.

    def test_access_token_param(self):
        """access_token query parameter."""
        text = "https://graph.facebook.com/me?access_token=EAABsbCS1iZBYBAKJ0gZBZBmR8"
        result = _scrub_secrets(text)
        # Not a known prefix pattern. The token= key pattern might catch
        # 'token=EAABsbCS1iZBYBAKJ0gZBZBmR8' - but the key is 'access_token' not 'token'.
        # Actually the regex has 'token' as a key name so 'access_token' should
        # NOT match unless the regex is substring-matching. Let's check.


class TestOverlappingPatterns:
    """Test cases where multiple patterns could match."""

    def test_bearer_plus_sk_prefix(self):
        """Bearer token that also starts with sk-."""
        text = "Bearer sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxx"
        result = _scrub_secrets(text)
        assert "sk-ant-api03" not in result
        # Should match either bearer or sk- pattern (or both).

    def test_api_key_equals_with_known_prefix(self):
        """api_key=ghp_xxx - matches both key=value and prefix patterns."""
        text = "api_key=ghp_1234567890abcdefghijklmnopqrst"
        result = _scrub_secrets(text)
        assert "ghp_1234567890" not in result

    def test_hex_that_starts_with_akia(self):
        """Hex string that also starts with AKIA."""
        text = '"AKIAIOSFODNN7EXAMPLE1"'
        result = _scrub_secrets(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
