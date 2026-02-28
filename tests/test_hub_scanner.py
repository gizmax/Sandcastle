"""Tests for hub_scanner - Community Hub template security scanning."""

from __future__ import annotations

import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from sandcastle.engine.hub_scanner import (
    ScanIssue,
    ScanResult,
    _check_dangerous_code,
    _check_resource_limits,
    _check_secret_patterns,
    _check_ssrf_urls,
    _is_ssrf_url,
    compute_sha256,
    scan_template,
    verify_checksum,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_yaml(**kwargs) -> str:
    """Build a minimal valid workflow YAML string."""
    data = {"name": "Test Workflow", "steps": [], **kwargs}
    return yaml.dump(data, default_flow_style=False)


def _make_code_step(code: str, step_id: str = "step1") -> dict:
    return {"type": "code", "id": step_id, "code": code}


def _make_http_step(url: str, step_id: str = "http1") -> dict:
    return {"type": "http", "id": step_id, "url": url}


# ===================================================================
# scan_template - integration
# ===================================================================


class TestScanTemplate:
    def test_clean_yaml_is_safe(self):
        content = _make_yaml(
            steps=[
                {"type": "llm", "id": "s1", "prompt": "Hello"},
                {"type": "http", "id": "s2", "url": "https://api.example.com/data"},
            ]
        )
        result = scan_template(content)
        assert result.safe is True
        assert result.errors == []

    def test_invalid_yaml_returns_error(self):
        result = scan_template("key: [unclosed bracket")
        assert result.safe is False
        assert any(e.code == "INVALID_YAML" for e in result.errors)

    def test_non_mapping_yaml(self):
        result = scan_template("- item1\n- item2\n")
        assert result.safe is False
        assert any(e.code == "INVALID_STRUCTURE" for e in result.errors)

    def test_dangerous_code_blocks_install(self):
        content = _make_yaml(
            steps=[_make_code_step("import os; os.system('rm -rf /')")]
        )
        result = scan_template(content)
        assert result.safe is False
        assert any(e.code == "DANGEROUS_CODE" for e in result.errors)

    def test_ssrf_url_blocks_install(self):
        content = _make_yaml(
            steps=[_make_http_step("http://169.254.169.254/latest/meta-data/")]
        )
        result = scan_template(content)
        assert result.safe is False
        assert any(e.code == "SSRF_URL" for e in result.errors)

    def test_secrets_produce_warnings_not_errors(self):
        content = _make_yaml(
            steps=[{"type": "llm", "id": "s1", "prompt": "Use key sk-abcdefghijklmnopqrstuvwxyz"}]
        )
        result = scan_template(content)
        assert result.safe is True  # warnings don't block
        assert len(result.warnings) > 0
        assert any(w.code == "POSSIBLE_SECRET" for w in result.warnings)

    def test_resource_warnings_dont_block(self):
        steps = [{"type": "llm", "id": f"s{i}", "prompt": "x"} for i in range(55)]
        content = _make_yaml(steps=steps)
        result = scan_template(content)
        assert result.safe is True
        assert any(w.code == "EXCESSIVE_STEPS" for w in result.warnings)

    def test_empty_steps_is_safe(self):
        content = _make_yaml(steps=[])
        result = scan_template(content)
        assert result.safe is True

    def test_missing_steps_key_is_safe(self):
        content = yaml.dump({"name": "No steps"})
        result = scan_template(content)
        assert result.safe is True

    def test_multiple_errors_collected(self):
        content = _make_yaml(
            steps=[
                _make_code_step("eval('bad')", "s1"),
                _make_code_step("exec('worse')", "s2"),
                _make_http_step("http://127.0.0.1/admin", "s3"),
            ]
        )
        result = scan_template(content)
        assert result.safe is False
        assert len(result.errors) >= 3


# ===================================================================
# _check_dangerous_code
# ===================================================================


class TestDangerousCode:
    def test_os_system(self):
        issues = _check_dangerous_code([_make_code_step("os.system('ls')")])
        assert any("os.system" in i.message for i in issues)

    def test_subprocess(self):
        issues = _check_dangerous_code([_make_code_step("import subprocess")])
        assert any("subprocess" in i.message for i in issues)

    def test_eval(self):
        issues = _check_dangerous_code([_make_code_step("result = eval(expr)")])
        assert any("eval()" in i.message for i in issues)

    def test_exec(self):
        issues = _check_dangerous_code([_make_code_step("exec(code_str)")])
        assert any("exec()" in i.message for i in issues)

    def test_dunder_import(self):
        issues = _check_dangerous_code([_make_code_step("mod = __import__('os')")])
        assert any("__import__" in i.message for i in issues)

    def test_open_write_mode(self):
        issues = _check_dangerous_code([_make_code_step("f = open('/tmp/x', 'w')")])
        assert any("open()" in i.message for i in issues)

    def test_open_append_mode(self):
        issues = _check_dangerous_code([_make_code_step("f = open('/tmp/x', 'a')")])
        assert any("open()" in i.message for i in issues)

    def test_shutil_rmtree(self):
        issues = _check_dangerous_code([_make_code_step("shutil.rmtree('/tmp')")])
        assert any("shutil.rmtree" in i.message for i in issues)

    def test_socket_connect(self):
        issues = _check_dangerous_code([_make_code_step("socket.connect(('1.2.3.4', 80))")])
        assert any("socket.connect" in i.message for i in issues)

    def test_urllib_urlopen(self):
        issues = _check_dangerous_code(
            [_make_code_step("urllib.request.urlopen('http://evil.com')")]
        )
        assert any("urllib.request.urlopen" in i.message for i in issues)

    def test_os_remove(self):
        issues = _check_dangerous_code([_make_code_step("os.remove('/etc/passwd')")])
        assert any("os.remove" in i.message for i in issues)

    def test_os_popen(self):
        issues = _check_dangerous_code([_make_code_step("os.popen('whoami')")])
        assert any("os.popen" in i.message for i in issues)

    def test_safe_code_passes(self):
        safe = _make_code_step("result = sum([1, 2, 3])\nprint(result)")
        issues = _check_dangerous_code([safe])
        assert issues == []

    def test_safe_math(self):
        issues = _check_dangerous_code([_make_code_step("x = 2 ** 10 + len('hello')")])
        assert issues == []

    def test_safe_json_loads(self):
        issues = _check_dangerous_code([_make_code_step("data = json.loads(raw)")])
        assert issues == []

    def test_safe_string_ops(self):
        issues = _check_dangerous_code([_make_code_step("s = 'hello'.upper().strip()")])
        assert issues == []

    def test_open_read_mode_passes(self):
        issues = _check_dangerous_code([_make_code_step("f = open('data.json', 'r')")])
        assert issues == []

    def test_non_code_step_ignored(self):
        step = {"type": "llm", "id": "s1", "prompt": "eval(os.system('rm'))"}
        issues = _check_dangerous_code([step])
        assert issues == []

    def test_dangerous_code_in_prompt_field(self):
        step = {"type": "code", "id": "s1", "code": "", "prompt": "eval('evil')"}
        issues = _check_dangerous_code([step])
        assert any("eval()" in i.message for i in issues)

    def test_step_id_in_issue(self):
        issues = _check_dangerous_code([_make_code_step("eval('x')", step_id="my_step")])
        assert issues[0].step == "my_step"

    def test_non_dict_steps_skipped(self):
        issues = _check_dangerous_code(["not a dict", 42, None])
        assert issues == []


# ===================================================================
# _check_secret_patterns
# ===================================================================


class TestSecretPatterns:
    def test_openai_key(self):
        content = "api_key: sk-abcdefghijklmnopqrstuvwxyz1234567890"
        issues = _check_secret_patterns(content)
        assert any("OpenAI" in i.message for i in issues)

    def test_github_pat(self):
        content = "token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmno"
        issues = _check_secret_patterns(content)
        assert any("GitHub PAT" in i.message for i in issues)

    def test_github_app_token(self):
        content = "token: ghs_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmno"
        issues = _check_secret_patterns(content)
        assert any("GitHub app" in i.message for i in issues)

    def test_aws_key(self):
        content = "aws_key: AKIAIOSFODNN7EXAMPLE"
        issues = _check_secret_patterns(content)
        assert any("AWS" in i.message for i in issues)

    def test_slack_token(self):
        content = "slack: xoxb-123456789-abcdefghijk"
        issues = _check_secret_patterns(content)
        assert any("Slack" in i.message for i in issues)

    def test_bearer_token(self):
        content = "auth: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc"
        issues = _check_secret_patterns(content)
        assert any("Bearer" in i.message for i in issues)

    def test_normal_text_no_warnings(self):
        content = _make_yaml(
            steps=[{"type": "llm", "id": "s1", "prompt": "Summarize this article"}]
        )
        issues = _check_secret_patterns(content)
        assert issues == []

    def test_short_sk_not_matched(self):
        content = "Use sk-short to do stuff"
        issues = _check_secret_patterns(content)
        # sk-short is < 20 chars after prefix, should not match
        assert not any("OpenAI" in i.message for i in issues)

    def test_deduplication(self):
        content = "sk-abcdefghijklmnopqrstuvwxyz\nsk-abcdefghijklmnopqrstuvwxyz"
        issues = _check_secret_patterns(content)
        openai_warnings = [i for i in issues if "OpenAI" in i.message]
        assert len(openai_warnings) == 1


# ===================================================================
# _check_ssrf_urls
# ===================================================================


class TestSSRFUrls:
    def test_private_10_range(self):
        issues = _check_ssrf_urls([_make_http_step("http://10.0.0.1/api")])
        assert any(i.code == "SSRF_URL" for i in issues)

    def test_private_172_range(self):
        issues = _check_ssrf_urls([_make_http_step("http://172.16.0.1/api")])
        assert any(i.code == "SSRF_URL" for i in issues)

    def test_private_192_range(self):
        issues = _check_ssrf_urls([_make_http_step("http://192.168.1.1/")])
        assert any(i.code == "SSRF_URL" for i in issues)

    def test_localhost(self):
        issues = _check_ssrf_urls([_make_http_step("http://localhost:8080/admin")])
        assert any(i.code == "SSRF_URL" for i in issues)

    def test_loopback(self):
        issues = _check_ssrf_urls([_make_http_step("http://127.0.0.1/admin")])
        assert any(i.code == "SSRF_URL" for i in issues)

    def test_cloud_metadata(self):
        issues = _check_ssrf_urls(
            [_make_http_step("http://169.254.169.254/latest/meta-data/")]
        )
        assert any(i.code == "SSRF_URL" for i in issues)

    def test_link_local(self):
        issues = _check_ssrf_urls([_make_http_step("http://169.254.1.1/data")])
        assert any(i.code == "SSRF_URL" for i in issues)

    def test_zero_address(self):
        issues = _check_ssrf_urls([_make_http_step("http://0.0.0.0:5000/")])
        assert any(i.code == "SSRF_URL" for i in issues)

    def test_ipv6_loopback(self):
        issues = _check_ssrf_urls([_make_http_step("http://[::1]:8080/")])
        assert any(i.code == "SSRF_URL" for i in issues)

    def test_public_url_passes(self):
        issues = _check_ssrf_urls(
            [_make_http_step("https://api.example.com/v1/data")]
        )
        assert issues == []

    def test_github_url_passes(self):
        issues = _check_ssrf_urls(
            [_make_http_step("https://api.github.com/repos/gizmax/Sandcastle")]
        )
        assert issues == []

    def test_non_http_step_ignored(self):
        step = {"type": "llm", "id": "s1", "url": "http://127.0.0.1/"}
        issues = _check_ssrf_urls([step])
        assert issues == []

    def test_empty_url_skipped(self):
        issues = _check_ssrf_urls([{"type": "http", "id": "h1", "url": ""}])
        assert issues == []

    def test_step_id_in_issue(self):
        issues = _check_ssrf_urls([_make_http_step("http://10.0.0.1/", "my_http")])
        assert issues[0].step == "my_http"


# ===================================================================
# _is_ssrf_url
# ===================================================================


class TestIsSSRFUrl:
    def test_public_ip(self):
        assert _is_ssrf_url("http://8.8.8.8/") is False

    def test_domain_name(self):
        assert _is_ssrf_url("https://example.com/api") is False

    def test_private_ip(self):
        assert _is_ssrf_url("http://192.168.0.1/") is True

    def test_metadata(self):
        assert _is_ssrf_url("http://169.254.169.254/") is True

    def test_invalid_url(self):
        assert _is_ssrf_url("not a url at all") is False


# ===================================================================
# _check_resource_limits
# ===================================================================


class TestResourceLimits:
    def test_normal_workflow_no_warnings(self):
        workflow = {"steps": [{"type": "llm", "id": f"s{i}"} for i in range(10)]}
        issues = _check_resource_limits(workflow)
        assert issues == []

    def test_excessive_steps(self):
        workflow = {"steps": [{"type": "llm", "id": f"s{i}"} for i in range(55)]}
        issues = _check_resource_limits(workflow)
        assert any(i.code == "EXCESSIVE_STEPS" for i in issues)

    def test_exactly_50_steps_no_warning(self):
        workflow = {"steps": [{"type": "llm", "id": f"s{i}"} for i in range(50)]}
        issues = _check_resource_limits(workflow)
        assert not any(i.code == "EXCESSIVE_STEPS" for i in issues)

    def test_excessive_max_tokens(self):
        workflow = {
            "steps": [{"type": "llm", "id": "s1", "max_tokens": 32768}]
        }
        issues = _check_resource_limits(workflow)
        assert any(i.code == "EXCESSIVE_TOKENS" for i in issues)

    def test_normal_max_tokens(self):
        workflow = {
            "steps": [{"type": "llm", "id": "s1", "max_tokens": 4096}]
        }
        issues = _check_resource_limits(workflow)
        assert not any(i.code == "EXCESSIVE_TOKENS" for i in issues)

    def test_excessive_cost(self):
        workflow = {"steps": [], "max_cost_usd": 25.0}
        issues = _check_resource_limits(workflow)
        assert any(i.code == "EXCESSIVE_COST" for i in issues)

    def test_normal_cost(self):
        workflow = {"steps": [], "max_cost_usd": 5.0}
        issues = _check_resource_limits(workflow)
        assert not any(i.code == "EXCESSIVE_COST" for i in issues)

    def test_missing_steps_key(self):
        issues = _check_resource_limits({"name": "test"})
        assert issues == []

    def test_non_list_steps(self):
        issues = _check_resource_limits({"steps": "not a list"})
        assert issues == []


# ===================================================================
# compute_sha256 / verify_checksum
# ===================================================================


class TestChecksum:
    def test_compute_sha256_deterministic(self):
        content = "hello world"
        h1 = compute_sha256(content)
        h2 = compute_sha256(content)
        assert h1 == h2
        assert len(h1) == 64  # hex digits

    def test_compute_sha256_known_value(self):
        # SHA-256 of "hello" is well-known
        import hashlib
        expected = hashlib.sha256(b"hello").hexdigest()
        assert compute_sha256("hello") == expected

    def test_different_content_different_hash(self):
        assert compute_sha256("abc") != compute_sha256("def")

    def test_verify_checksum_match(self):
        content = "test content"
        sha = compute_sha256(content)
        assert verify_checksum(content, sha) is True

    def test_verify_checksum_mismatch(self):
        assert verify_checksum("content", "0" * 64) is False

    def test_verify_checksum_case_insensitive(self):
        content = "test"
        sha = compute_sha256(content)
        assert verify_checksum(content, sha.upper()) is True

    def test_verify_checksum_strips_whitespace(self):
        content = "test"
        sha = compute_sha256(content)
        assert verify_checksum(content, f"  {sha}  ") is True

    def test_empty_content(self):
        h = compute_sha256("")
        assert len(h) == 64
        assert verify_checksum("", h) is True


# ===================================================================
# API integration
# ===================================================================


class TestAPIIntegration:
    """Test that the API route properly uses the scanner."""

    @pytest.mark.asyncio
    async def test_unsafe_template_rejected(self):
        """API should return 400 UNSAFE_TEMPLATE for dangerous code."""
        from unittest.mock import patch as _patch

        from sandcastle.engine.hub_scanner import scan_template

        malicious_yaml = _make_yaml(
            steps=[_make_code_step("import os; os.system('rm -rf /')")]
        )
        result = scan_template(malicious_yaml)
        assert result.safe is False
        assert any(e.code == "DANGEROUS_CODE" for e in result.errors)

    @pytest.mark.asyncio
    async def test_safe_template_accepted(self):
        from sandcastle.engine.hub_scanner import scan_template

        safe_yaml = _make_yaml(
            steps=[
                {"type": "llm", "id": "s1", "prompt": "Hello world"},
            ]
        )
        result = scan_template(safe_yaml)
        assert result.safe is True

    @pytest.mark.asyncio
    async def test_checksum_mismatch_detected(self):
        from sandcastle.engine.hub_scanner import compute_sha256, verify_checksum

        content = "name: Test\nsteps: []"
        wrong_hash = "a" * 64
        assert verify_checksum(content, wrong_hash) is False
        assert verify_checksum(content, compute_sha256(content)) is True


# ===================================================================
# CLI integration
# ===================================================================


class TestCLIIntegration:
    """Test scanner integration points in CLI flow."""

    def test_scan_then_validate_pipeline(self):
        """Scanning + validation pipeline returns consistent results."""
        clean = _make_yaml(
            steps=[{"type": "llm", "id": "s1", "prompt": "Summarize"}]
        )
        result = scan_template(clean)
        assert result.safe is True

        malicious = _make_yaml(
            steps=[_make_code_step("subprocess.run(['rm', '-rf', '/'])")]
        )
        result = scan_template(malicious)
        assert result.safe is False

    def test_scan_result_dataclass(self):
        """ScanResult fields are correctly populated."""
        result = ScanResult(
            safe=False,
            warnings=[ScanIssue(code="W1", message="warn")],
            errors=[ScanIssue(code="E1", message="err", step="s1")],
        )
        assert not result.safe
        assert len(result.warnings) == 1
        assert len(result.errors) == 1
        assert result.errors[0].step == "s1"

    def test_scan_issue_optional_step(self):
        issue = ScanIssue(code="TEST", message="test msg")
        assert issue.step is None
        issue2 = ScanIssue(code="TEST", message="test msg", step="step1")
        assert issue2.step == "step1"


# ===================================================================
# Edge cases
# ===================================================================


class TestEdgeCases:
    def test_yaml_with_only_name(self):
        result = scan_template("name: Minimal\n")
        assert result.safe is True

    def test_empty_string(self):
        result = scan_template("")
        # yaml.safe_load("") returns None
        assert result.safe is False

    def test_yaml_with_non_dict_steps(self):
        content = yaml.dump({"name": "test", "steps": "not a list"})
        result = scan_template(content)
        assert result.safe is True  # no errors, just no steps to check

    def test_code_step_with_none_code(self):
        step = {"type": "code", "id": "s1", "code": None}
        issues = _check_dangerous_code([step])
        assert issues == []

    def test_http_step_with_none_url(self):
        step = {"type": "http", "id": "h1", "url": None}
        issues = _check_ssrf_urls([step])
        assert issues == []

    def test_step_without_id_or_name(self):
        step = {"type": "code", "code": "eval('x')"}
        issues = _check_dangerous_code([step])
        assert len(issues) > 0
        assert issues[0].step == "unknown"


# ===================================================================
# Nested config bypass prevention
# ===================================================================


class TestNestedCodeConfigBypass:
    """Verify that dangerous code in code_config.code is caught."""

    def test_code_config_code_subprocess(self):
        step = {
            "type": "code",
            "id": "bypass1",
            "code_config": {"code": "import subprocess; subprocess.run(['rm', '-rf', '/'])"},
        }
        issues = _check_dangerous_code([step])
        assert any("subprocess" in i.message for i in issues)

    def test_code_config_code_eval(self):
        step = {
            "type": "code",
            "id": "bypass2",
            "code_config": {"code": "eval(user_input)"},
        }
        issues = _check_dangerous_code([step])
        assert any("eval()" in i.message for i in issues)

    def test_code_config_code_os_system(self):
        step = {
            "type": "code",
            "id": "bypass3",
            "code_config": {"code": "os.system('whoami')"},
        }
        issues = _check_dangerous_code([step])
        assert any("os.system" in i.message for i in issues)

    def test_code_config_code_exec(self):
        step = {
            "type": "code",
            "id": "bypass4",
            "code_config": {"code": "exec(malicious_payload)"},
        }
        issues = _check_dangerous_code([step])
        assert any("exec()" in i.message for i in issues)

    def test_code_config_safe_code_passes(self):
        step = {
            "type": "code",
            "id": "safe1",
            "code_config": {"code": "result = sum([1, 2, 3])"},
        }
        issues = _check_dangerous_code([step])
        assert issues == []

    def test_code_config_none_value(self):
        step = {
            "type": "code",
            "id": "safe2",
            "code_config": {"code": None},
        }
        issues = _check_dangerous_code([step])
        assert issues == []

    def test_code_config_not_a_dict(self):
        step = {
            "type": "code",
            "id": "safe3",
            "code_config": "not a dict",
        }
        issues = _check_dangerous_code([step])
        assert issues == []

    def test_flat_code_still_works(self):
        """Backward compatibility - flat 'code' field is still checked."""
        step = {"type": "code", "id": "flat1", "code": "eval('x')"}
        issues = _check_dangerous_code([step])
        assert any("eval()" in i.message for i in issues)

    def test_both_flat_and_nested_code_checked(self):
        """Both flat and nested code fields contribute to detection."""
        step = {
            "type": "code",
            "id": "both1",
            "code": "import os",
            "code_config": {"code": "subprocess.run(['ls'])"},
        }
        issues = _check_dangerous_code([step])
        assert any("subprocess" in i.message for i in issues)

    def test_integration_code_config_blocks_template(self):
        """Full scan_template catches code_config bypass."""
        content = _make_yaml(
            steps=[{
                "type": "code",
                "id": "sneaky",
                "code_config": {"code": "os.system('cat /etc/shadow')"},
            }]
        )
        result = scan_template(content)
        assert result.safe is False
        assert any(e.code == "DANGEROUS_CODE" for e in result.errors)


class TestNestedHttpConfigBypass:
    """Verify that SSRF URLs in http_config.url are caught."""

    def test_http_config_url_private_ip(self):
        step = {
            "type": "http",
            "id": "bypass_http1",
            "http_config": {"url": "http://10.0.0.1/admin"},
        }
        issues = _check_ssrf_urls([step])
        assert any(i.code == "SSRF_URL" for i in issues)

    def test_http_config_url_localhost(self):
        step = {
            "type": "http",
            "id": "bypass_http2",
            "http_config": {"url": "http://localhost:8080/secret"},
        }
        issues = _check_ssrf_urls([step])
        assert any(i.code == "SSRF_URL" for i in issues)

    def test_http_config_url_metadata(self):
        step = {
            "type": "http",
            "id": "bypass_http3",
            "http_config": {"url": "http://169.254.169.254/latest/meta-data/"},
        }
        issues = _check_ssrf_urls([step])
        assert any(i.code == "SSRF_URL" for i in issues)

    def test_http_config_url_safe_passes(self):
        step = {
            "type": "http",
            "id": "safe_http",
            "http_config": {"url": "https://api.example.com/data"},
        }
        issues = _check_ssrf_urls([step])
        assert issues == []

    def test_http_config_url_none(self):
        step = {
            "type": "http",
            "id": "none_http",
            "http_config": {"url": None},
        }
        issues = _check_ssrf_urls([step])
        assert issues == []

    def test_http_config_not_a_dict(self):
        step = {
            "type": "http",
            "id": "bad_cfg",
            "http_config": "not a dict",
        }
        issues = _check_ssrf_urls([step])
        assert issues == []

    def test_flat_url_still_works(self):
        """Backward compatibility - flat 'url' field is still checked."""
        step = {"type": "http", "id": "flat_http", "url": "http://192.168.1.1/"}
        issues = _check_ssrf_urls([step])
        assert any(i.code == "SSRF_URL" for i in issues)

    def test_both_flat_and_nested_http_checked(self):
        """Both flat and nested URL fields are checked."""
        step = {
            "type": "http",
            "id": "both_http",
            "url": "https://api.example.com",
            "http_config": {"url": "http://127.0.0.1/secret"},
        }
        issues = _check_ssrf_urls([step])
        assert any(i.code == "SSRF_URL" for i in issues)

    def test_integration_http_config_blocks_template(self):
        """Full scan_template catches http_config bypass."""
        content = _make_yaml(
            steps=[{
                "type": "http",
                "id": "sneaky_http",
                "http_config": {"url": "http://169.254.169.254/latest/"},
            }]
        )
        result = scan_template(content)
        assert result.safe is False
        assert any(e.code == "SSRF_URL" for e in result.errors)


class TestSensorConfigBypass:
    """Verify that SSRF URLs in sensor_config.url are caught."""

    def test_sensor_flat_url_private(self):
        step = {"type": "sensor", "id": "sensor1", "url": "http://10.0.0.1/status"}
        issues = _check_ssrf_urls([step])
        assert any(i.code == "SSRF_URL" for i in issues)

    def test_sensor_config_url_private(self):
        step = {
            "type": "sensor",
            "id": "sensor2",
            "sensor_config": {"url": "http://192.168.0.1/health"},
        }
        issues = _check_ssrf_urls([step])
        assert any(i.code == "SSRF_URL" for i in issues)

    def test_sensor_config_url_metadata(self):
        step = {
            "type": "sensor",
            "id": "sensor3",
            "sensor_config": {"url": "http://169.254.169.254/latest/meta-data/"},
        }
        issues = _check_ssrf_urls([step])
        assert any(i.code == "SSRF_URL" for i in issues)

    def test_sensor_config_url_safe(self):
        step = {
            "type": "sensor",
            "id": "sensor_safe",
            "sensor_config": {"url": "https://api.example.com/health"},
        }
        issues = _check_ssrf_urls([step])
        assert issues == []

    def test_sensor_config_not_a_dict(self):
        step = {
            "type": "sensor",
            "id": "sensor_bad",
            "sensor_config": "not a dict",
        }
        issues = _check_ssrf_urls([step])
        assert issues == []

    def test_integration_sensor_blocks_template(self):
        """Full scan_template catches sensor_config SSRF."""
        content = _make_yaml(
            steps=[{
                "type": "sensor",
                "id": "sneaky_sensor",
                "sensor_config": {"url": "http://localhost:9090/metrics"},
            }]
        )
        result = scan_template(content)
        assert result.safe is False
        assert any(e.code == "SSRF_URL" for e in result.errors)


class TestNotifyConfigBypass:
    """Verify that SSRF URLs in notify_config.webhook_url are caught."""

    def test_notify_webhook_url_private(self):
        step = {
            "type": "notify",
            "id": "notify1",
            "notify_config": {"webhook_url": "http://10.0.0.5:3000/hooks"},
        }
        issues = _check_ssrf_urls([step])
        assert any(i.code == "SSRF_URL" for i in issues)

    def test_notify_webhook_url_localhost(self):
        step = {
            "type": "notify",
            "id": "notify2",
            "notify_config": {"webhook_url": "http://localhost:8080/webhook"},
        }
        issues = _check_ssrf_urls([step])
        assert any(i.code == "SSRF_URL" for i in issues)

    def test_notify_webhook_url_safe(self):
        step = {
            "type": "notify",
            "id": "notify_safe",
            "notify_config": {"webhook_url": "https://hooks.slack.com/services/xxx"},
        }
        issues = _check_ssrf_urls([step])
        assert issues == []

    def test_notify_no_webhook_url(self):
        step = {
            "type": "notify",
            "id": "notify_none",
            "notify_config": {"service": "slack", "channel": "#general"},
        }
        issues = _check_ssrf_urls([step])
        assert issues == []

    def test_notify_config_not_a_dict(self):
        step = {
            "type": "notify",
            "id": "notify_bad",
            "notify_config": "not a dict",
        }
        issues = _check_ssrf_urls([step])
        assert issues == []

    def test_integration_notify_blocks_template(self):
        """Full scan_template catches notify_config SSRF."""
        content = _make_yaml(
            steps=[{
                "type": "notify",
                "id": "sneaky_notify",
                "notify_config": {"webhook_url": "http://192.168.1.1:9000/hook"},
            }]
        )
        result = scan_template(content)
        assert result.safe is False
        assert any(e.code == "SSRF_URL" for e in result.errors)


class TestNestedPromptBypass:
    """Verify that dangerous code in llm_config.prompt and classify_config.prompt is caught."""

    def test_llm_config_prompt_eval(self):
        step = {
            "type": "code",
            "id": "llm_bypass1",
            "llm_config": {"prompt": "Run this: eval('malicious')"},
        }
        issues = _check_dangerous_code([step])
        assert any("eval()" in i.message for i in issues)

    def test_llm_config_system_prompt_subprocess(self):
        step = {
            "type": "code",
            "id": "llm_bypass2",
            "llm_config": {"system_prompt": "import subprocess; subprocess.call(['ls'])"},
        }
        issues = _check_dangerous_code([step])
        assert any("subprocess" in i.message for i in issues)

    def test_classify_config_prompt_exec(self):
        step = {
            "type": "code",
            "id": "classify_bypass1",
            "classify_config": {"prompt": "exec(payload)"},
        }
        issues = _check_dangerous_code([step])
        assert any("exec()" in i.message for i in issues)

    def test_flat_prompt_still_works(self):
        """Backward compatibility - flat 'prompt' field is still checked."""
        step = {"type": "code", "id": "flat_prompt", "code": "", "prompt": "eval('x')"}
        issues = _check_dangerous_code([step])
        assert any("eval()" in i.message for i in issues)

    def test_nested_prompt_safe_content(self):
        step = {
            "type": "code",
            "id": "safe_prompt",
            "llm_config": {"prompt": "Summarize the following text"},
        }
        issues = _check_dangerous_code([step])
        assert issues == []

    def test_llm_config_none_prompt(self):
        step = {
            "type": "code",
            "id": "none_prompt",
            "llm_config": {"prompt": None},
        }
        issues = _check_dangerous_code([step])
        assert issues == []

    def test_llm_config_not_a_dict(self):
        step = {
            "type": "code",
            "id": "bad_llm",
            "llm_config": "not a dict",
        }
        issues = _check_dangerous_code([step])
        assert issues == []

    def test_classify_config_not_a_dict(self):
        step = {
            "type": "code",
            "id": "bad_classify",
            "classify_config": 42,
        }
        issues = _check_dangerous_code([step])
        assert issues == []

    def test_integration_nested_prompt_blocks_template(self):
        """Full scan_template catches nested prompt bypass."""
        content = _make_yaml(
            steps=[{
                "type": "code",
                "id": "sneaky_prompt",
                "llm_config": {"prompt": "Use os.system('cat /etc/passwd') to read"},
            }]
        )
        result = scan_template(content)
        assert result.safe is False
        assert any(e.code == "DANGEROUS_CODE" for e in result.errors)
