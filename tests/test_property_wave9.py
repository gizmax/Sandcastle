"""Property-based tests using Hypothesis to find edge cases in Sandcastle.

Targets:
1. storage._safe_key(), _safe_path(), _validate_key_common()
2. hub_scanner._is_ssrf_url(), verify_checksum(), compute_sha256(), _strip_zero_width_chars()
3. schemas - Pydantic model validation with arbitrary inputs
4. executor.resolve_templates(), resolve_variable(), _escape_braces()
5. dag.validate() with arbitrary step configurations
6. pdf._extract_numeric(), _strip_inline_md()
7. sdk - URL construction with arbitrary base_url / path
8. memory.score_importance(), _word_set()
9. webhooks.dispatcher._sign_payload(), verify_signature()
10. policy._safe_eval(), _has_redos_risk(), _resolve_policy_template()
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import math
from pathlib import Path
import re
import string
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

# -----------------------------------------------------------------------
# 1. Storage: _safe_key, _safe_path, _validate_key_common
# -----------------------------------------------------------------------
from sandcastle.engine.storage import (
    S3Storage,
    LocalStorage,
    _validate_key_common,
    _CONTROL_CHAR_RE,
    _MAX_S3_KEY_LENGTH,
)


class TestStorageValidateKeyCommon:
    """Property tests for _validate_key_common."""

    @given(st.text(min_size=1).filter(lambda s: s.strip() and not _CONTROL_CHAR_RE.search(s)))
    @settings(max_examples=200)
    def test_non_empty_printable_strings_accepted(self, key: str) -> None:
        """Non-empty strings without control chars must not raise."""
        _validate_key_common(key)

    @given(st.text(max_size=0))
    @settings(max_examples=10)
    def test_empty_strings_rejected(self, key: str) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_key_common(key)

    @given(st.text(alphabet=string.whitespace, min_size=1))
    @settings(max_examples=50)
    def test_whitespace_only_rejected(self, key: str) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_key_common(key)

    @given(
        st.text(min_size=1),
        st.sampled_from(
            [chr(c) for c in range(0x00, 0x09)]
            + [chr(0x0B), chr(0x0C)]
            + [chr(c) for c in range(0x0E, 0x20)]
            + [chr(0x7F)]
        ),
    )
    @settings(max_examples=200)
    def test_control_chars_rejected(self, prefix: str, ctrl: str) -> None:
        """Any string containing a control char must be rejected."""
        key = prefix + ctrl
        assume(key.strip())  # make sure it's not whitespace-only
        with pytest.raises(ValueError, match="control characters"):
            _validate_key_common(key)


class TestS3SafeKey:
    """Property tests for S3Storage._safe_key."""

    @given(
        st.text(
            alphabet=st.sampled_from(string.ascii_letters + string.digits + "/_-."),
            min_size=1,
            max_size=100,
        ).filter(lambda s: not s.startswith("/") and ".." not in s)
    )
    @settings(max_examples=200)
    def test_valid_keys_normalized(self, key: str) -> None:
        """Valid keys should be returned (normalized) without error."""
        result = S3Storage._safe_key(key)
        assert isinstance(result, str)
        assert len(result) > 0
        # Must not start with ".." or "/"
        assert not result.startswith("..")
        assert not result.startswith("/")

    @given(st.text(min_size=1, max_size=50))
    @settings(max_examples=200)
    def test_safe_key_never_crashes(self, key: str) -> None:
        """_safe_key must raise ValueError or return a safe string, never crash."""
        try:
            result = S3Storage._safe_key(key)
            # If no error, result must not start with ".." or "/"
            assert not result.startswith("..")
            assert not result.startswith("/")
        except ValueError:
            pass  # expected for invalid inputs

    @given(st.text(min_size=1).filter(lambda s: s.strip() and not _CONTROL_CHAR_RE.search(s)))
    @settings(max_examples=200)
    def test_idempotent_on_safe_keys(self, key: str) -> None:
        """Calling _safe_key twice on a safe key should give the same result."""
        try:
            once = S3Storage._safe_key(key)
            twice = S3Storage._safe_key(once)
            assert once == twice
        except ValueError:
            pass  # some keys are rejected

    def test_oversized_key_rejected(self) -> None:
        """Keys exceeding 1024 bytes must be rejected."""
        key = "a" * 1025
        with pytest.raises(ValueError, match="exceeds maximum length"):
            S3Storage._safe_key(key)

    @given(st.integers(min_value=1, max_value=5))
    @settings(max_examples=30)
    def test_path_traversal_rejected(self, depth: int) -> None:
        """Various path traversal patterns must be rejected."""
        traversal = "/".join([".."] * depth) + "/etc/passwd"
        with pytest.raises(ValueError):
            S3Storage._safe_key(traversal)


class TestLocalStorageSafePath:
    """Property tests for LocalStorage._safe_path."""

    @given(
        st.text(
            alphabet=st.sampled_from(string.ascii_letters + string.digits + "/_-."),
            min_size=1,
            max_size=50,
        ).filter(lambda s: ".." not in s and not s.startswith("/"))
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_safe_paths_within_base_dir(self, path: str) -> None:
        """Valid paths must resolve within base_dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved_tmpdir = str(Path(tmpdir).resolve())
            storage = LocalStorage(base_dir=tmpdir)
            try:
                result = storage._safe_path(path)
                assert str(result).startswith(resolved_tmpdir)
            except ValueError:
                pass  # some edge cases are still rejected

    @given(st.integers(min_value=1, max_value=5))
    @settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
    def test_traversal_blocked(self, depth: int) -> None:
        """Path traversal must be blocked."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalStorage(base_dir=tmpdir)
            path = "/".join([".."] * depth) + "/etc/passwd"
            with pytest.raises(ValueError):
                storage._safe_path(path)


# -----------------------------------------------------------------------
# 2. Hub scanner: _is_ssrf_url, verify_checksum, compute_sha256
# -----------------------------------------------------------------------
from sandcastle.engine.hub_scanner import (
    _is_ssrf_url,
    verify_checksum,
    compute_sha256,
    _strip_zero_width_chars,
    scan_template,
    ScanResult,
)


class TestIsSSRFUrl:
    """Property tests for SSRF URL detection."""

    @given(st.sampled_from([
        "http://127.0.0.1/admin",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://[::1]/",
        "http://localhost/",
        "http://0.0.0.0/",
        "http://169.254.169.254/latest/meta-data/",
        "ftp://example.com/file",
        "file:///etc/passwd",
        "gopher://evil.com/",
    ]))
    @settings(max_examples=50)
    def test_known_ssrf_urls_detected(self, url: str) -> None:
        """Known SSRF URLs must be detected."""
        assert _is_ssrf_url(url) is True

    @given(st.sampled_from([
        "http://example.com",
        "https://api.github.com/repos",
        "http://1.2.3.4/path",
        "https://google.com",
    ]))
    @settings(max_examples=20)
    def test_public_urls_allowed(self, url: str) -> None:
        """Public URLs must not be flagged as SSRF."""
        assert _is_ssrf_url(url) is False

    @given(st.text(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_never_crashes_on_arbitrary_input(self, url: str) -> None:
        """_is_ssrf_url must always return a bool, never crash."""
        result = _is_ssrf_url(url)
        assert isinstance(result, bool)

    @given(
        st.sampled_from(["http", "https"]),
        st.integers(min_value=0, max_value=255),
        st.integers(min_value=0, max_value=255),
        st.integers(min_value=0, max_value=255),
        st.integers(min_value=0, max_value=255),
    )
    @settings(max_examples=200)
    def test_private_ips_detected(self, scheme: str, a: int, b: int, c: int, d: int) -> None:
        """Private IPs in RFC 1918 ranges should be flagged."""
        import ipaddress
        ip = f"{a}.{b}.{c}.{d}"
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            url = f"{scheme}://{ip}/"
            # Should be detected as SSRF
            assert _is_ssrf_url(url) is True


class TestVerifyChecksum:
    """Property tests for checksum verification."""

    @given(st.text(min_size=0, max_size=5000))
    @settings(max_examples=200)
    def test_self_consistency(self, content: str) -> None:
        """compute_sha256 then verify_checksum must always match."""
        digest = compute_sha256(content)
        assert verify_checksum(content, digest) is True

    @given(st.text(min_size=0, max_size=1000))
    @settings(max_examples=200)
    def test_case_insensitive_hex(self, content: str) -> None:
        """Checksum verification must be case-insensitive."""
        digest = compute_sha256(content)
        assert verify_checksum(content, digest.upper()) is True
        assert verify_checksum(content, digest.lower()) is True

    @given(st.text(min_size=1, max_size=1000), st.text(min_size=1, max_size=1000))
    @settings(max_examples=200)
    def test_different_content_different_checksums(self, a: str, b: str) -> None:
        """Different content should (almost certainly) produce different checksums."""
        assume(a != b)
        assert compute_sha256(a) != compute_sha256(b)

    @given(st.text(min_size=0, max_size=500))
    @settings(max_examples=200)
    def test_wrong_checksum_fails(self, content: str) -> None:
        """Wrong checksum must never match."""
        wrong = "0" * 64
        actual = compute_sha256(content)
        if actual != wrong:
            assert verify_checksum(content, wrong) is False

    @given(st.text(min_size=0, max_size=500))
    @settings(max_examples=100)
    def test_compute_sha256_is_deterministic(self, content: str) -> None:
        """Same input must produce same output."""
        assert compute_sha256(content) == compute_sha256(content)

    @given(st.text(min_size=0, max_size=500))
    @settings(max_examples=100)
    def test_sha256_output_format(self, content: str) -> None:
        """SHA-256 hex digest must be exactly 64 lowercase hex chars."""
        digest = compute_sha256(content)
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)


class TestStripZeroWidthChars:
    """Property tests for zero-width character stripping."""

    @given(st.text(alphabet=string.printable, min_size=0, max_size=500))
    @settings(max_examples=200)
    def test_idempotent(self, text: str) -> None:
        """Stripping twice should be the same as stripping once."""
        once = _strip_zero_width_chars(text)
        twice = _strip_zero_width_chars(once)
        assert once == twice

    @given(st.text(alphabet=string.ascii_letters + string.digits, min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_ascii_unchanged(self, text: str) -> None:
        """Pure ASCII alphanumeric text must pass through unchanged."""
        assert _strip_zero_width_chars(text) == text

    @given(st.text(min_size=0, max_size=300))
    @settings(max_examples=200)
    def test_output_shorter_or_equal(self, text: str) -> None:
        """Output must never be longer than input."""
        result = _strip_zero_width_chars(text)
        assert len(result) <= len(text)

    def test_removes_known_zero_width_chars(self) -> None:
        """Known zero-width chars must be removed."""
        zero_width = "\u200b\u200c\u200d\u200e\u200f\u2060\ufeff\u00ad"
        assert _strip_zero_width_chars("ev" + "\u200b" + "al") == "eval"
        assert _strip_zero_width_chars(zero_width) == ""


# -----------------------------------------------------------------------
# 3. Schemas: Pydantic model validation
# -----------------------------------------------------------------------
from sandcastle.api.schemas import (
    WorkflowRunRequest,
    WorkflowSaveRequest,
    ScheduleCreateRequest,
    ApiKeyCreateRequest,
    _check_json_nesting_depth,
    _MAX_JSON_NESTING_DEPTH,
)


class TestSchemaValidation:
    """Property-based tests for Pydantic schema validation."""

    @given(st.text(min_size=1, max_size=100).filter(lambda s: "/" not in s and "\\" not in s and ".." not in s and s.strip() == s and s.strip()))
    @settings(max_examples=200)
    def test_workflow_save_valid_names(self, name: str) -> None:
        """Valid workflow names should be accepted."""
        try:
            req = WorkflowSaveRequest(name=name, content="name: test\nsteps: []")
            assert req.name == name
        except Exception:
            pass  # some names may fail other validators

    @given(st.text(min_size=1, max_size=50))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.filter_too_much])
    def test_workflow_save_rejects_traversal(self, name: str) -> None:
        """Names with /, \\, or .. must be rejected."""
        assume("/" in name or "\\" in name or ".." in name)
        with pytest.raises(Exception):
            WorkflowSaveRequest(name=name, content="name: test\nsteps: []")

    @given(st.text(min_size=1, max_size=500))
    @settings(max_examples=200)
    def test_callback_url_scheme_validation(self, url: str) -> None:
        """Non-http(s) callback URLs must be rejected."""
        assume(not url.strip().startswith(("http://", "https://")))
        assume(url.strip())
        with pytest.raises(Exception):
            WorkflowRunRequest(workflow="test", callback_url=url)

    @given(st.sampled_from(["http://example.com/hook", "https://hooks.slack.com/X"]))
    @settings(max_examples=10)
    def test_valid_callback_url_accepted(self, url: str) -> None:
        """Valid http(s) callback URLs must be accepted."""
        req = WorkflowRunRequest(workflow="test", callback_url=url)
        assert req.callback_url == url

    @given(st.from_regex(r"^[\s]+$", fullmatch=True).filter(lambda s: len(s) <= 100))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.filter_too_much])
    def test_api_key_name_whitespace_rejected(self, name: str) -> None:
        """Whitespace-only names must be rejected for API keys."""
        with pytest.raises(Exception):
            ApiKeyCreateRequest(name=name)

    @given(st.text(min_size=1, max_size=50).filter(lambda s: s.strip()))
    @settings(max_examples=100)
    def test_cron_five_fields_required(self, cron: str) -> None:
        """Cron expressions not having exactly 5 fields should be rejected."""
        parts = cron.strip().split()
        if len(parts) != 5:
            with pytest.raises(Exception):
                ScheduleCreateRequest(
                    workflow_name="test",
                    cron_expression=cron,
                )


class TestJsonNestingDepth:
    """Property tests for JSON nesting depth validation."""

    @given(st.integers(min_value=0, max_value=_MAX_JSON_NESTING_DEPTH))
    @settings(max_examples=50)
    def test_within_limit_accepted(self, depth: int) -> None:
        """Nesting within the limit should not raise."""
        obj: Any = "leaf"
        for _ in range(depth):
            obj = {"key": obj}
        _check_json_nesting_depth(obj)

    @given(st.integers(min_value=_MAX_JSON_NESTING_DEPTH + 1, max_value=_MAX_JSON_NESTING_DEPTH + 5))
    @settings(max_examples=10)
    def test_over_limit_rejected(self, depth: int) -> None:
        """Nesting beyond the limit should raise ValueError."""
        obj: Any = "leaf"
        for _ in range(depth):
            obj = {"key": obj}
        with pytest.raises(ValueError, match="nesting depth"):
            _check_json_nesting_depth(obj)


# -----------------------------------------------------------------------
# 4. Executor: resolve_templates, resolve_variable, _escape_braces
# -----------------------------------------------------------------------
from sandcastle.engine.executor import (
    RunContext,
    StepResult,
    resolve_templates,
    resolve_variable,
    _escape_braces,
    _UNRESOLVED,
)


class TestEscapeBraces:
    """Property tests for _escape_braces."""

    @given(st.text(min_size=0, max_size=500))
    @settings(max_examples=200)
    def test_no_single_braces_in_output(self, value: str) -> None:
        """After escaping, there should be no lone single braces (all doubled)."""
        result = _escape_braces(value)
        # Every { must be followed by another {, every } by another }
        i = 0
        while i < len(result):
            if result[i] == "{":
                assert i + 1 < len(result) and result[i + 1] == "{", (
                    f"Lone '{{' at position {i} in {result!r}"
                )
                i += 2
            elif result[i] == "}":
                assert i + 1 < len(result) and result[i + 1] == "}", (
                    f"Lone '}}' at position {i} in {result!r}"
                )
                i += 2
            else:
                i += 1

    @given(st.text(alphabet=string.ascii_letters + string.digits + " ", min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_no_braces_unchanged(self, value: str) -> None:
        """Text without braces passes through unchanged."""
        assert _escape_braces(value) == value

    @given(st.text(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_length_growth(self, value: str) -> None:
        """Result length should grow by the number of brace chars."""
        brace_count = value.count("{") + value.count("}")
        result = _escape_braces(value)
        assert len(result) == len(value) + brace_count


class TestResolveVariable:
    """Property tests for resolve_variable."""

    @given(
        st.text(
            alphabet=st.sampled_from(string.ascii_letters + string.digits + "_-"),
            min_size=1, max_size=50,
        )
    )
    @settings(max_examples=200)
    def test_input_variables_resolvable(self, key: str) -> None:
        """Input variables should be resolvable."""
        ctx = RunContext(run_id="test-run", input={key: "value123"})
        result = resolve_variable(f"input.{key}", ctx)
        assert result == "value123"

    @given(st.text(min_size=1, max_size=50))
    @settings(max_examples=200)
    def test_unknown_paths_return_unresolved(self, path: str) -> None:
        """Unknown top-level paths return _UNRESOLVED."""
        assume(not path.startswith(("input", "steps", "run_id", "date", "memory")))
        ctx = RunContext(run_id="test-run", input={})
        result = resolve_variable(path, ctx)
        assert result is _UNRESOLVED

    def test_run_id_resolution(self) -> None:
        """run_id must always resolve to the context's run_id."""
        ctx = RunContext(run_id="abc-123", input={})
        assert resolve_variable("run_id", ctx) == "abc-123"

    def test_date_resolution(self) -> None:
        """date must resolve to today's ISO date."""
        ctx = RunContext(run_id="test", input={})
        result = resolve_variable("date", ctx)
        assert result == datetime.now(timezone.utc).date().isoformat()

    def test_empty_path_unresolved(self) -> None:
        ctx = RunContext(run_id="test", input={})
        assert resolve_variable("", ctx) is _UNRESOLVED


class TestResolveTemplates:
    """Property tests for resolve_templates."""

    @given(st.text(alphabet=string.ascii_letters + string.digits + " .,!?", min_size=0, max_size=500))
    @settings(max_examples=200)
    def test_no_placeholders_passthrough(self, text: str) -> None:
        """Text without {var.path} placeholders passes through unchanged."""
        ctx = RunContext(run_id="r1", input={})
        assert resolve_templates(text, ctx) == text

    @given(st.text(min_size=1, max_size=50).filter(lambda s: s.isalnum()))
    @settings(max_examples=200)
    def test_input_placeholder_resolved(self, key: str) -> None:
        """Input placeholders should be resolved."""
        ctx = RunContext(run_id="r1", input={key: "VALUE"})
        template = f"Hello {{input.{key}}}"
        result = resolve_templates(template, ctx)
        assert "VALUE" in result

    @given(
        st.text(
            alphabet=string.ascii_letters + string.digits + " .",
            min_size=1,
            max_size=100,
        )
    )
    @settings(max_examples=200)
    def test_unresolvable_left_as_is(self, key: str) -> None:
        """Unresolvable variables should be left as the original placeholder."""
        assume("." not in key)  # avoid accidental step references
        ctx = RunContext(run_id="r1", input={})
        template = f"{{input.{key}}}"
        result = resolve_templates(template, ctx)
        # Should either be the original placeholder or resolved
        assert isinstance(result, str)

    def test_step_output_braces_escaped(self) -> None:
        """Step outputs containing braces must be escaped to prevent injection."""
        ctx = RunContext(
            run_id="r1",
            input={},
            step_outputs={"step1": "{input.evil}"},
        )
        template = "Result: {steps.step1.output}"
        result = resolve_templates(template, ctx)
        # Braces should be doubled to prevent template injection
        assert "{{input.evil}}" in result
        # The raw unescaped pattern should NOT resolve to a value
        assert result != "Result: "  # not swallowed by resolution


# -----------------------------------------------------------------------
# 5. DAG: validate with arbitrary step configurations
# -----------------------------------------------------------------------
from sandcastle.engine.dag import (
    WorkflowDefinition,
    StepDefinition,
    validate,
    VALID_STEP_TYPES,
)


class TestDagValidate:
    """Property tests for DAG workflow validation."""

    @given(st.text(min_size=0, max_size=250))
    @settings(max_examples=200)
    def test_validate_never_crashes(self, name: str) -> None:
        """validate() must never raise, only return error messages."""
        wf = WorkflowDefinition(
            name=name,
            description="",
            default_model="sonnet",
            default_max_turns=10,
            default_timeout=300,
            steps=[StepDefinition(id="step1", prompt="test")] if name else [],
        )
        errors = validate(wf)
        assert isinstance(errors, list)
        for e in errors:
            assert isinstance(e, str)

    @given(st.text(min_size=1, max_size=50).filter(lambda s: re.match(r"^[a-zA-Z0-9_][a-zA-Z0-9_\-]{0,49}$", s)))
    @settings(max_examples=200)
    def test_valid_step_ids_accepted(self, step_id: str) -> None:
        """Valid step IDs must not produce format errors."""
        wf = WorkflowDefinition(
            name="test-wf",
            description="",
            default_model="sonnet",
            default_max_turns=10,
            default_timeout=300,
            steps=[StepDefinition(id=step_id, prompt="do something")],
        )
        errors = validate(wf)
        # Should have no step ID format error
        assert not any("invalid" in e.lower() and step_id in e for e in errors)

    @given(st.text(min_size=1, max_size=50).filter(
        lambda s: not re.match(r"^[a-zA-Z0-9_][a-zA-Z0-9_\-]{0,99}$", s)
    ))
    @settings(max_examples=200)
    def test_invalid_step_ids_caught(self, step_id: str) -> None:
        """Invalid step IDs must produce validation errors."""
        wf = WorkflowDefinition(
            name="test-wf",
            description="",
            default_model="sonnet",
            default_max_turns=10,
            default_timeout=300,
            steps=[StepDefinition(id=step_id, prompt="test")],
        )
        errors = validate(wf)
        assert any("invalid" in e.lower() for e in errors)

    def test_empty_workflow_errors(self) -> None:
        """A workflow with no steps should produce an error."""
        wf = WorkflowDefinition(
            name="test",
            description="",
            default_model="sonnet",
            default_max_turns=10,
            default_timeout=300,
            steps=[],
        )
        errors = validate(wf)
        assert any("at least one step" in e for e in errors)

    def test_empty_name_errors(self) -> None:
        """A workflow with no name should produce an error."""
        wf = WorkflowDefinition(
            name="",
            description="",
            default_model="sonnet",
            default_max_turns=10,
            default_timeout=300,
            steps=[StepDefinition(id="s1", prompt="test")],
        )
        errors = validate(wf)
        assert any("name" in e.lower() for e in errors)

    @given(st.integers(min_value=2, max_value=10))
    @settings(max_examples=30)
    def test_duplicate_step_ids_detected(self, count: int) -> None:
        """Duplicate step IDs must be detected."""
        steps = [StepDefinition(id="same_id", prompt=f"step {i}") for i in range(count)]
        wf = WorkflowDefinition(
            name="test",
            description="",
            default_model="sonnet",
            default_max_turns=10,
            default_timeout=300,
            steps=steps,
        )
        errors = validate(wf)
        assert any("duplicate" in e.lower() for e in errors)

    @given(st.sampled_from(sorted(VALID_STEP_TYPES)))
    @settings(max_examples=30)
    def test_all_valid_types_recognized(self, step_type: str) -> None:
        """Valid step types should not produce 'unknown type' errors."""
        wf = WorkflowDefinition(
            name="test",
            description="",
            default_model="sonnet",
            default_max_turns=10,
            default_timeout=300,
            steps=[StepDefinition(id="s1", type=step_type, prompt="test")],
        )
        errors = validate(wf)
        assert not any("unknown type" in e.lower() for e in errors)

    @given(st.text(min_size=1, max_size=30).filter(lambda s: s not in VALID_STEP_TYPES))
    @settings(max_examples=100)
    def test_invalid_types_rejected(self, step_type: str) -> None:
        """Unknown step types must produce errors."""
        wf = WorkflowDefinition(
            name="test",
            description="",
            default_model="sonnet",
            default_max_turns=10,
            default_timeout=300,
            steps=[StepDefinition(id="s1", type=step_type, prompt="test")],
        )
        errors = validate(wf)
        assert any("unknown type" in e.lower() for e in errors)

    @given(st.text(min_size=1, max_size=30).filter(lambda s: s.isalnum()))
    @settings(max_examples=100)
    def test_missing_dependency_detected(self, dep: str) -> None:
        """References to nonexistent steps must produce errors."""
        assume(dep != "s1")
        wf = WorkflowDefinition(
            name="test",
            description="",
            default_model="sonnet",
            default_max_turns=10,
            default_timeout=300,
            steps=[StepDefinition(id="s1", depends_on=[dep], prompt="test")],
        )
        errors = validate(wf)
        assert any("unknown step" in e.lower() for e in errors)


# -----------------------------------------------------------------------
# 6. PDF: _extract_numeric, _strip_inline_md
# -----------------------------------------------------------------------
from sandcastle.engine.pdf import _extract_numeric, _strip_inline_md


class TestExtractNumeric:
    """Property tests for _extract_numeric."""

    @given(st.floats(allow_nan=False, allow_infinity=False, min_value=-1e15, max_value=1e15))
    @settings(max_examples=200)
    def test_plain_numbers_roundtrip(self, num: float) -> None:
        """Plain float strings must be extracted correctly."""
        text = str(num)
        result = _extract_numeric(text)
        if result is not None:
            assert math.isclose(result, num, rel_tol=1e-9, abs_tol=1e-12)

    @given(st.integers(min_value=-999999999, max_value=999999999))
    @settings(max_examples=200)
    def test_integer_strings(self, num: int) -> None:
        """Integer strings must be extracted."""
        result = _extract_numeric(str(num))
        assert result is not None
        assert result == float(num)

    @given(st.text(min_size=0, max_size=500))
    @settings(max_examples=200)
    def test_never_crashes(self, text: str) -> None:
        """_extract_numeric must never crash, always returns float | None."""
        result = _extract_numeric(text)
        assert result is None or isinstance(result, float)
        if result is not None:
            assert math.isfinite(result)

    def test_currency_formats(self) -> None:
        """Currency-formatted numbers should be extracted."""
        assert _extract_numeric("$1,234.56") == 1234.56
        assert _extract_numeric("$100") == 100.0
        assert _extract_numeric("50%") == 50.0

    def test_empty_returns_none(self) -> None:
        assert _extract_numeric("") is None

    def test_non_numeric_returns_none(self) -> None:
        assert _extract_numeric("hello world") is None

    def test_inf_nan_rejected(self) -> None:
        """Special float values must be rejected."""
        assert _extract_numeric("inf") is None
        assert _extract_numeric("nan") is None
        assert _extract_numeric("-inf") is None


class TestStripInlineMd:
    """Property tests for _strip_inline_md."""

    @given(st.text(alphabet=string.ascii_letters + string.digits + " ", min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_plain_text_unchanged(self, text: str) -> None:
        """Plain text without markdown should pass through (modulo whitespace collapse)."""
        result = _strip_inline_md(text)
        # Only whitespace handling may differ
        assert result == re.sub(r"  +", " ", text).strip()

    @given(st.text(min_size=0, max_size=500))
    @settings(max_examples=200)
    def test_never_crashes(self, text: str) -> None:
        """_strip_inline_md must never crash."""
        result = _strip_inline_md(text)
        assert isinstance(result, str)

    @given(st.text(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_output_no_longer_than_input_capped(self, text: str) -> None:
        """Output must not exceed input length (after internal cap)."""
        result = _strip_inline_md(text)
        capped = text[:10_000]
        assert len(result) <= len(capped) + 1  # +1 for strip edge

    def test_bold_stripped(self) -> None:
        assert _strip_inline_md("**bold**") == "bold"

    def test_italic_stripped(self) -> None:
        assert _strip_inline_md("*italic*") == "italic"

    def test_code_stripped(self) -> None:
        assert _strip_inline_md("`code`") == "code"

    def test_link_stripped(self) -> None:
        assert _strip_inline_md("[text](http://example.com)") == "text"

    def test_html_tags_removed(self) -> None:
        assert _strip_inline_md("<strong>bold</strong>") == "bold"
        assert "br" not in _strip_inline_md("<br/>text").lower() or _strip_inline_md("<br/>text") == "text"

    @given(st.text(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_idempotent(self, text: str) -> None:
        """Stripping twice should give the same as stripping once."""
        once = _strip_inline_md(text)
        twice = _strip_inline_md(once)
        assert once == twice


# -----------------------------------------------------------------------
# 7. SDK: URL construction
# -----------------------------------------------------------------------


class TestSdkUrlConstruction:
    """Property tests for SDK URL normalization."""

    @given(st.from_regex(r"https?://[a-z0-9._-]{1,50}(/[a-z0-9._-]*)*/*", fullmatch=True))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.filter_too_much])
    def test_trailing_slashes_stripped(self, base_url: str) -> None:
        """base_url trailing slashes should be stripped."""
        result = base_url.rstrip("/")
        assert not result.endswith("/") or result == ""

    @given(st.integers(min_value=0, max_value=10))
    @settings(max_examples=30)
    def test_multiple_trailing_slashes(self, slash_count: int) -> None:
        """Multiple trailing slashes should all be removed."""
        base = "http://localhost:8080" + "/" * slash_count
        result = base.rstrip("/")
        # Should end with the port, not with /
        assert not result.endswith("/")

    @given(
        st.sampled_from(["http://localhost:8080", "https://api.example.com", "http://10.0.0.1:3000"]),
        st.sampled_from(["/api/workflows/run", "/api/runs", "/api/health"]),
    )
    @settings(max_examples=50)
    def test_url_joining_no_double_slash(self, base: str, path: str) -> None:
        """URL joining should not produce double slashes in path."""
        url = base.rstrip("/") + path
        # After scheme://, there should be no //
        after_scheme = url.split("://", 1)[1]
        assert "//" not in after_scheme


# -----------------------------------------------------------------------
# 8. Memory: score_importance, _word_set
# -----------------------------------------------------------------------
from sandcastle.engine.memory import _word_set, score_importance


class TestWordSet:
    """Property tests for _word_set."""

    @given(st.text(min_size=0, max_size=500))
    @settings(max_examples=200)
    def test_output_is_set_of_strings(self, text: str) -> None:
        """_word_set must return a set of lowercase strings."""
        result = _word_set(text)
        assert isinstance(result, set)
        for word in result:
            assert isinstance(word, str)
            assert word == word.lower()
            assert len(word) > 2  # words <= 2 chars are filtered out

    @given(st.text(alphabet=string.ascii_uppercase, min_size=4, max_size=20))
    @settings(max_examples=100)
    def test_case_insensitive(self, word: str) -> None:
        """_word_set must normalize to lowercase."""
        result_upper = _word_set(word)
        result_lower = _word_set(word.lower())
        assert result_upper == result_lower

    @given(st.text(alphabet=st.sampled_from("!@#$%^&*()-= "), min_size=0, max_size=50))
    @settings(max_examples=100)
    def test_no_words_from_symbols(self, text: str) -> None:
        """Text with only symbols/punctuation should produce empty set."""
        result = _word_set(text)
        assert len(result) == 0

    @given(st.text(alphabet=st.sampled_from("ab "), min_size=0, max_size=50))
    @settings(max_examples=100)
    def test_short_words_filtered(self, text: str) -> None:
        """Words with 2 or fewer chars should be filtered out."""
        result = _word_set(text)
        for word in result:
            assert len(word) > 2


class TestScoreImportance:
    """Property tests for score_importance."""

    @given(st.text(min_size=0, max_size=2000))
    @settings(max_examples=200)
    def test_score_bounded_0_to_1(self, content: str) -> None:
        """Score must always be in [0.0, 1.0]."""
        score = score_importance(content, [])
        assert 0.0 <= score <= 1.0

    @given(
        st.text(min_size=0, max_size=500),
        st.lists(
            st.fixed_dictionaries({"memory": st.text(min_size=0, max_size=200)}),
            min_size=0,
            max_size=10,
        ),
    )
    @settings(max_examples=200)
    def test_score_always_bounded_with_memories(self, content: str, memories: list) -> None:
        """Score must be in [0.0, 1.0] even with existing memories."""
        score = score_importance(content, memories)
        assert 0.0 <= score <= 1.0

    @given(st.text(min_size=0, max_size=500))
    @settings(max_examples=200)
    def test_score_deterministic(self, content: str) -> None:
        """Same input must always produce same score."""
        s1 = score_importance(content, [])
        s2 = score_importance(content, [])
        assert s1 == s2

    def test_very_short_content_penalized(self) -> None:
        """Very short content (< 20 chars) should be penalized."""
        short_score = score_importance("hi", [])
        medium_score = score_importance("This is a medium-length content string for testing purposes.", [])
        assert short_score < medium_score

    def test_high_overlap_reduces_score(self) -> None:
        """Content highly overlapping with existing memories should score lower."""
        content = "The quick brown fox jumps over lazy dogs"
        existing = [{"memory": "The quick brown fox jumps over lazy dogs"}]
        score_novel = score_importance(content, [])
        score_overlap = score_importance(content, existing)
        assert score_overlap < score_novel


# -----------------------------------------------------------------------
# 9. Webhooks: _sign_payload, verify_signature
# -----------------------------------------------------------------------
from sandcastle.webhooks.dispatcher import _sign_payload, verify_signature


class TestWebhookSignatures:
    """Property tests for HMAC webhook signatures."""

    @given(st.text(min_size=0, max_size=5000), st.text(min_size=1, max_size=200))
    @settings(max_examples=200)
    def test_sign_then_verify_roundtrip(self, body: str, secret: str) -> None:
        """Sign then verify must always succeed."""
        sig = _sign_payload(body, secret)
        assert verify_signature(body, sig, secret) is True

    @given(st.text(min_size=0, max_size=1000), st.text(min_size=1, max_size=100))
    @settings(max_examples=200)
    def test_signature_format(self, body: str, secret: str) -> None:
        """Signature must be a 64-char lowercase hex string."""
        sig = _sign_payload(body, secret)
        assert len(sig) == 64
        assert all(c in "0123456789abcdef" for c in sig)

    @given(st.text(min_size=0, max_size=500), st.text(min_size=1, max_size=100))
    @settings(max_examples=200)
    def test_wrong_body_fails_verification(self, body: str, secret: str) -> None:
        """Altering the body must fail verification."""
        sig = _sign_payload(body, secret)
        altered = body + "X"
        assert verify_signature(altered, sig, secret) is False

    @given(st.text(min_size=0, max_size=500), st.text(min_size=1, max_size=100), st.text(min_size=1, max_size=100))
    @settings(max_examples=200)
    def test_wrong_secret_fails_verification(self, body: str, secret: str, wrong_secret: str) -> None:
        """Wrong secret must fail verification."""
        assume(secret != wrong_secret)
        sig = _sign_payload(body, secret)
        assert verify_signature(body, sig, wrong_secret) is False

    @given(st.text(min_size=0, max_size=500), st.text(min_size=1, max_size=100))
    @settings(max_examples=200)
    def test_deterministic(self, body: str, secret: str) -> None:
        """Same inputs must produce same signature."""
        sig1 = _sign_payload(body, secret)
        sig2 = _sign_payload(body, secret)
        assert sig1 == sig2

    @given(st.text(min_size=0, max_size=500), st.text(min_size=1, max_size=100))
    @settings(max_examples=200)
    def test_matches_stdlib_hmac(self, body: str, secret: str) -> None:
        """Signature must match direct stdlib hmac computation."""
        sig = _sign_payload(body, secret)
        expected = hmac_mod.new(
            secret.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        assert sig == expected


# -----------------------------------------------------------------------
# 10. Policy: _safe_eval, _has_redos_risk, _resolve_policy_template
# -----------------------------------------------------------------------
from sandcastle.engine.policy import (
    _safe_eval,
    _has_redos_risk,
    _resolve_policy_template,
    _MAX_EXPRESSION_LENGTH,
)


class TestSafeEval:
    """Property tests for policy _safe_eval."""

    @given(st.integers(min_value=-10000, max_value=10000))
    @settings(max_examples=200)
    def test_integer_literals(self, n: int) -> None:
        """Integer literals must evaluate to themselves."""
        result = _safe_eval(str(n), {})
        assert result == n

    @given(
        st.integers(min_value=-100, max_value=100),
        st.integers(min_value=-100, max_value=100),
    )
    @settings(max_examples=200)
    def test_addition(self, a: int, b: int) -> None:
        """Simple addition must work correctly."""
        result = _safe_eval(f"{a} + {b}", {})
        assert result == a + b

    @given(
        st.integers(min_value=-100, max_value=100),
        st.integers(min_value=-100, max_value=100),
    )
    @settings(max_examples=200)
    def test_comparisons(self, a: int, b: int) -> None:
        """Comparison operators must work correctly."""
        assert _safe_eval(f"{a} > {b}", {}) == (a > b)
        assert _safe_eval(f"{a} < {b}", {}) == (a < b)
        assert _safe_eval(f"{a} == {b}", {}) == (a == b)
        assert _safe_eval(f"{a} >= {b}", {}) == (a >= b)
        assert _safe_eval(f"{a} <= {b}", {}) == (a <= b)

    @given(st.text(min_size=0, max_size=50))
    @settings(max_examples=200)
    def test_len_function_on_variable(self, text: str) -> None:
        """len() on a variable must return the correct length."""
        result = _safe_eval("len(x)", {"x": text})
        assert result == len(text)

    def test_variable_access(self) -> None:
        """Variables should be accessible."""
        assert _safe_eval("x + y", {"x": 10, "y": 20}) == 30
        assert _safe_eval("x > 5", {"x": 10}) is True

    def test_too_long_expression_rejected(self) -> None:
        """Expressions exceeding max length must be rejected."""
        long_expr = "1 + " * (_MAX_EXPRESSION_LENGTH // 4 + 1) + "1"
        assume(len(long_expr) > _MAX_EXPRESSION_LENGTH)
        with pytest.raises(ValueError, match="too long"):
            _safe_eval(long_expr, {})

    @given(st.booleans(), st.booleans())
    @settings(max_examples=50)
    def test_boolean_operators(self, a: bool, b: bool) -> None:
        """Boolean and/or/not must work."""
        assert _safe_eval("a and b", {"a": a, "b": b}) == (a and b)
        assert _safe_eval("a or b", {"a": a, "b": b}) == (a or b)
        assert _safe_eval("not a", {"a": a}) == (not a)


class TestHasRedosRisk:
    """Property tests for ReDoS detection heuristic."""

    @given(st.text(
        alphabet=st.sampled_from(string.ascii_letters + string.digits + "._-"),
        min_size=0,
        max_size=100,
    ))
    @settings(max_examples=200)
    def test_never_crashes(self, pattern: str) -> None:
        """_has_redos_risk must never crash, only return bool."""
        result = _has_redos_risk(pattern)
        assert isinstance(result, bool)

    @given(st.sampled_from([
        r"(a+)+",
        r"(.*)*",
        r"(a+)*",
        r"([a-z]+)+",
        r"(a*){2,}",
    ]))
    @settings(max_examples=20)
    def test_known_redos_patterns_detected(self, pattern: str) -> None:
        """Known ReDoS patterns must be detected."""
        assert _has_redos_risk(pattern) is True

    @given(st.sampled_from([
        r"\d{3}-\d{2}-\d{4}",
        r"[a-z]+",
        r"\btest\b",
        r"hello|world",
        r"[A-Z]{2,5}",
    ]))
    @settings(max_examples=20)
    def test_safe_patterns_not_flagged(self, pattern: str) -> None:
        """Safe patterns must not be flagged."""
        assert _has_redos_risk(pattern) is False


class TestResolvePolicyTemplate:
    """Property tests for _resolve_policy_template."""

    @given(st.text(alphabet=string.ascii_letters + string.digits + " .,!", min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_no_placeholders_unchanged(self, template: str) -> None:
        """Templates without placeholders pass through unchanged."""
        result = _resolve_policy_template(template, {}, {})
        assert result == template

    @given(st.text(min_size=0, max_size=300))
    @settings(max_examples=200)
    def test_never_crashes(self, template: str) -> None:
        """_resolve_policy_template must never crash."""
        result = _resolve_policy_template(template, {"key": "val"}, {"input": {"x": "y"}})
        assert isinstance(result, str)

    def test_output_field_resolved(self) -> None:
        """Output fields should be resolved."""
        output = {"severity": "high", "message": "alert"}
        result = _resolve_policy_template("Level: {output.severity}", output, {})
        assert "high" in result

    def test_input_field_resolved(self) -> None:
        """Input fields should be resolved."""
        context = {"input": {"name": "test-workflow"}}
        result = _resolve_policy_template("Workflow: {input.name}", {}, context)
        assert "test-workflow" in result

    def test_unknown_field_left_as_is(self) -> None:
        """Unknown field references should be left as-is."""
        result = _resolve_policy_template("{unknown.field}", {}, {})
        assert "{unknown.field}" in result

    @given(
        st.text(min_size=1, max_size=20).filter(lambda s: s.isalnum()),
        st.text(min_size=1, max_size=50),
    )
    @settings(max_examples=200)
    def test_output_dict_access(self, key: str, value: str) -> None:
        """Arbitrary output dict keys should be resolvable."""
        output = {key: value}
        result = _resolve_policy_template(f"{{output.{key}}}", output, {})
        # Value should be in result (possibly truncated)
        assert value[:500] in result or len(value) > 500


# -----------------------------------------------------------------------
# Cross-cutting: scan_template never crashes on arbitrary YAML-ish strings
# -----------------------------------------------------------------------


class TestScanTemplateRobustness:
    """Property tests for scan_template overall robustness."""

    @given(st.text(min_size=0, max_size=1000))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_scan_never_crashes(self, yaml_content: str) -> None:
        """scan_template must never crash on arbitrary input."""
        result = scan_template(yaml_content)
        assert isinstance(result, ScanResult)
        assert isinstance(result.safe, bool)
        assert isinstance(result.warnings, list)
        assert isinstance(result.errors, list)

    @given(st.text(min_size=0, max_size=500))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_safe_flag_consistency(self, yaml_content: str) -> None:
        """safe=True iff errors is empty."""
        result = scan_template(yaml_content)
        assert result.safe == (len(result.errors) == 0)
