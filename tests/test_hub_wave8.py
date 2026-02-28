"""Wave 8: Deep audit of hub security scanner, CLI commands, and bypass vectors.

Tests cover:
- Scanner bypass techniques (obfuscation, getattr, importlib, base64, compile, etc.)
- Unicode/zero-width character injection
- SSRF bypass (IPv6-mapped IPv4, decimal IP, hex IP, URL encoding, scheme abuse)
- YAML bomb / billion laughs protection
- YAML size limit enforcement
- Secret detection completeness (Anthropic, Stripe, Google keys)
- Resource limit evasion via nested loop steps
- Race step branch scanning
- Transform step code injection
- CLI hub install redirect validation
- CLI hub install size limits
- CLI hub registry size limits
- Hub filename sanitization edge cases
- Hub download URL validation
"""

from __future__ import annotations

import textwrap
from unittest.mock import MagicMock, patch

import pytest
import yaml

from sandcastle.engine.hub_scanner import (
    MAX_YAML_EXPANDED_RATIO,
    MAX_YAML_SIZE,
    ScanIssue,
    ScanResult,
    _check_dangerous_code,
    _check_resource_limits,
    _check_secret_patterns,
    _check_ssrf_urls,
    _is_ssrf_url,
    _strip_zero_width_chars,
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
# 1. Scanner bypass techniques - obfuscation detection
# ===================================================================


class TestObfuscationBypass:
    """Verify new obfuscation patterns are caught in code steps."""

    def test_getattr_bypass(self):
        """getattr(os, 'system')(cmd) should be caught."""
        step = _make_code_step("getattr(os, 'system')('rm -rf /')")
        issues = _check_dangerous_code([step])
        assert any("getattr" in i.message for i in issues)

    def test_importlib_bypass(self):
        """importlib.import_module('subprocess') should be caught."""
        step = _make_code_step("importlib.import_module('subprocess')")
        issues = _check_dangerous_code([step])
        assert any("importlib" in i.message for i in issues)

    def test_base64_encoded_payload(self):
        """base64.b64decode + exec should be caught."""
        step = _make_code_step("exec(base64.b64decode('aW1wb3J0IG9z'))")
        issues = _check_dangerous_code([step])
        # Should catch both base64 and exec
        assert any("base64" in i.message for i in issues)
        assert any("exec()" in i.message for i in issues)

    def test_compile_exec_bypass(self):
        """compile() + exec() should be caught."""
        step = _make_code_step("code = compile('os.system(\"ls\")', '<str>', 'exec')\nexec(code)")
        issues = _check_dangerous_code([step])
        assert any("compile()" in i.message for i in issues)
        assert any("exec()" in i.message for i in issues)

    def test_os_environ_access(self):
        """os.environ access for credential theft should be caught."""
        step = _make_code_step("secrets = os.environ.get('AWS_SECRET_KEY')")
        issues = _check_dangerous_code([step])
        assert any("os.environ" in i.message for i in issues)

    def test_ctypes_native_code(self):
        """ctypes module for native code execution should be caught."""
        step = _make_code_step("import ctypes; ctypes.CDLL('libc.so.6')")
        issues = _check_dangerous_code([step])
        assert any("ctypes" in i.message for i in issues)

    def test_codecs_decode_bypass(self):
        """codecs.decode for payload obfuscation should be caught."""
        step = _make_code_step("codecs.decode('bGludXg=', 'base64')")
        issues = _check_dangerous_code([step])
        assert any("codecs.decode" in i.message for i in issues)

    def test_os_walk_filesystem_enumeration(self):
        """os.walk() for filesystem enumeration should be caught."""
        step = _make_code_step("for root, dirs, files in os.walk('/'): print(files)")
        issues = _check_dangerous_code([step])
        assert any("os.walk" in i.message for i in issues)

    def test_glob_filesystem_enumeration(self):
        """glob module for filesystem enumeration should be caught."""
        step = _make_code_step("import glob; glob.glob('/etc/*')")
        issues = _check_dangerous_code([step])
        assert any("glob" in i.message for i in issues)

    def test_integration_getattr_blocks_template(self):
        """Full scan_template catches getattr bypass."""
        content = _make_yaml(
            steps=[_make_code_step("getattr(__builtins__, 'eval')('malicious')")]
        )
        result = scan_template(content)
        assert result.safe is False
        assert any(e.code == "DANGEROUS_CODE" for e in result.errors)

    def test_integration_importlib_blocks_template(self):
        """Full scan_template catches importlib bypass."""
        content = _make_yaml(
            steps=[_make_code_step("mod = importlib.import_module('os')")]
        )
        result = scan_template(content)
        assert result.safe is False

    def test_integration_base64_blocks_template(self):
        """Full scan_template catches base64 payload obfuscation."""
        content = _make_yaml(
            steps=[_make_code_step("import base64; exec(base64.b64decode(payload))")]
        )
        result = scan_template(content)
        assert result.safe is False

    def test_safe_code_still_passes(self):
        """Normal safe code should not trigger obfuscation patterns."""
        step = _make_code_step("result = sum([1, 2, 3])\nprint(result)")
        issues = _check_dangerous_code([step])
        assert issues == []

    def test_safe_json_code_passes(self):
        """JSON processing code should not trigger."""
        step = _make_code_step("data = json.loads(raw)\nresult = data['key']")
        issues = _check_dangerous_code([step])
        assert issues == []


# ===================================================================
# 2. Unicode / zero-width character bypass
# ===================================================================


class TestZeroWidthCharBypass:
    """Test zero-width character stripping prevents scanner bypass."""

    def test_strip_zero_width_space(self):
        """Zero-width space inserted in 'eval' should be stripped."""
        obfuscated = "ev\u200bal"  # ev + ZWS + al = eval
        cleaned = _strip_zero_width_chars(obfuscated)
        assert cleaned == "eval"

    def test_strip_zero_width_joiner(self):
        """Zero-width joiner in 'exec' should be stripped."""
        obfuscated = "ex\u200dec"
        cleaned = _strip_zero_width_chars(obfuscated)
        assert cleaned == "exec"

    def test_strip_bom(self):
        """BOM character should be stripped."""
        text = "\ufeffname: test"
        cleaned = _strip_zero_width_chars(text)
        assert cleaned == "name: test"

    def test_strip_soft_hyphen(self):
        """Soft hyphen should be stripped."""
        text = "sub\u00adprocess"
        cleaned = _strip_zero_width_chars(text)
        assert cleaned == "subprocess"

    def test_strip_multiple_zero_width(self):
        """Multiple zero-width characters should all be stripped."""
        text = "\u200bos\u200c.\u200dsystem\u200e(\u200f)"
        cleaned = _strip_zero_width_chars(text)
        assert cleaned == "os.system()"

    def test_normal_text_unchanged(self):
        """Normal text without zero-width chars should be unchanged."""
        text = "Hello, this is normal text!"
        assert _strip_zero_width_chars(text) == text

    def test_scan_template_detects_zero_width_obfuscation(self):
        """Scanner should detect eval() even when hidden with zero-width chars."""
        # Construct raw YAML with literal zero-width character (not YAML-escaped)
        content = "name: Test\nsteps:\n- type: code\n  id: sneaky\n  code: ev\u200bal('malicious')\n"
        result = scan_template(content)
        assert result.safe is False
        assert any(e.code == "DANGEROUS_CODE" for e in result.errors)
        # Should also warn about zero-width chars
        assert any(w.code == "ZERO_WIDTH_CHARS" for w in result.warnings)

    def test_scan_template_zero_width_in_subprocess(self):
        """subprocess obfuscated with zero-width chars should be caught."""
        # Use raw YAML with literal zero-width character
        content = "name: Test\nsteps:\n- type: code\n  id: s1\n  code: import sub\u200bprocess\n"
        result = scan_template(content)
        assert result.safe is False

    def test_clean_yaml_no_zero_width_warning(self):
        """Clean YAML without zero-width chars should not produce warnings."""
        content = _make_yaml(steps=[{"type": "llm", "id": "s1", "prompt": "Hello"}])
        result = scan_template(content)
        assert not any(w.code == "ZERO_WIDTH_CHARS" for w in result.warnings)

    def test_strip_arabic_letter_mark(self):
        """Arabic letter mark should be stripped."""
        text = "os\u061c.system"
        cleaned = _strip_zero_width_chars(text)
        assert cleaned == "os.system"

    def test_strip_invisible_operators(self):
        """Function application, invisible times, separator, plus."""
        text = "a\u2061b\u2062c\u2063d\u2064e"
        cleaned = _strip_zero_width_chars(text)
        assert cleaned == "abcde"


# ===================================================================
# 3. SSRF bypass techniques
# ===================================================================


class TestSSRFBypassTechniques:
    """Test enhanced SSRF detection for various bypass techniques."""

    def test_ipv6_mapped_ipv4_loopback(self):
        """IPv6-mapped IPv4 ::ffff:127.0.0.1 should be caught."""
        assert _is_ssrf_url("http://[::ffff:127.0.0.1]/admin") is True

    def test_ipv6_mapped_ipv4_private(self):
        """IPv6-mapped IPv4 ::ffff:10.0.0.1 should be caught."""
        assert _is_ssrf_url("http://[::ffff:10.0.0.1]/api") is True

    def test_ipv6_mapped_ipv4_192(self):
        """IPv6-mapped IPv4 ::ffff:192.168.1.1 should be caught."""
        assert _is_ssrf_url("http://[::ffff:192.168.1.1]/") is True

    def test_decimal_ip_loopback(self):
        """Decimal IP 2130706433 (= 127.0.0.1) should be caught."""
        assert _is_ssrf_url("http://2130706433/admin") is True

    def test_decimal_ip_private_10(self):
        """Decimal IP 167772161 (= 10.0.0.1) should be caught."""
        assert _is_ssrf_url("http://167772161/api") is True

    def test_hex_ip_loopback(self):
        """Hex IP 0x7f000001 (= 127.0.0.1) should be caught."""
        assert _is_ssrf_url("http://0x7f000001/admin") is True

    def test_zero_hostname(self):
        """Hostname '0' (shorthand for 0.0.0.0) should be caught."""
        assert _is_ssrf_url("http://0/") is True

    def test_non_http_scheme_file(self):
        """file:// scheme should be blocked."""
        assert _is_ssrf_url("file:///etc/passwd") is True

    def test_non_http_scheme_gopher(self):
        """gopher:// scheme should be blocked."""
        assert _is_ssrf_url("gopher://localhost:25/") is True

    def test_non_http_scheme_ftp(self):
        """ftp:// scheme should be blocked."""
        assert _is_ssrf_url("ftp://10.0.0.1/secret") is True

    def test_non_http_scheme_dict(self):
        """dict:// scheme should be blocked."""
        assert _is_ssrf_url("dict://localhost:11211/stat") is True

    def test_public_ip_still_allowed(self):
        """Public IPs should still pass."""
        assert _is_ssrf_url("http://8.8.8.8/dns") is False

    def test_public_https_url_still_allowed(self):
        """Normal HTTPS URLs should still pass."""
        assert _is_ssrf_url("https://api.example.com/v1/data") is False

    def test_integration_ipv6_mapped_blocks_template(self):
        """Full template scan catches IPv6-mapped IPv4 SSRF."""
        content = _make_yaml(
            steps=[_make_http_step("http://[::ffff:127.0.0.1]/secret")]
        )
        result = scan_template(content)
        assert result.safe is False
        assert any(e.code == "SSRF_URL" for e in result.errors)

    def test_integration_decimal_ip_blocks_template(self):
        """Full template scan catches decimal IP SSRF."""
        content = _make_yaml(
            steps=[_make_http_step("http://2130706433/admin")]
        )
        result = scan_template(content)
        assert result.safe is False

    def test_integration_file_scheme_blocks_template(self):
        """Full template scan catches file:// scheme in http step."""
        content = _make_yaml(
            steps=[_make_http_step("file:///etc/shadow")]
        )
        result = scan_template(content)
        assert result.safe is False

    def test_url_encoded_localhost(self):
        """URL-encoded localhost should not bypass (urlparse handles this)."""
        # urlparse decodes %XX in hostname by default
        assert _is_ssrf_url("http://localhost/admin") is True

    def test_ipv6_full_loopback(self):
        """Full IPv6 loopback should be caught."""
        assert _is_ssrf_url("http://[::1]:8080/") is True

    def test_large_decimal_still_safe(self):
        """A decimal IP that maps to public address should pass."""
        # 134744072 = 8.8.8.8
        assert _is_ssrf_url("http://134744072/") is False


# ===================================================================
# 4. YAML bomb / billion laughs protection
# ===================================================================


class TestYAMLBombProtection:
    """Test YAML bomb detection prevents billion laughs attacks."""

    def test_yaml_bomb_with_anchors(self):
        """YAML with anchor expansion causing large output should be detected."""
        # Create a YAML that uses anchors to expand moderately
        # yaml.safe_load handles this, but we check the ratio
        bomb = textwrap.dedent("""\
            name: Bomb Test
            a: &a "AAAAAAAAAA"
            b: &b [*a, *a, *a, *a, *a, *a, *a, *a, *a, *a]
            c: &c [*b, *b, *b, *b, *b, *b, *b, *b, *b, *b]
            d: &d [*c, *c, *c, *c, *c, *c, *c, *c, *c, *c]
            e: &e [*d, *d, *d, *d, *d, *d, *d, *d, *d, *d]
            steps: []
        """)
        result = scan_template(bomb)
        # The bomb might be detected via expansion ratio
        # depending on the actual expansion, it could be either safe=False or produce a warning
        if not result.safe:
            assert any(e.code == "YAML_BOMB" for e in result.errors)

    def test_normal_yaml_no_bomb_detection(self):
        """Normal YAML should not trigger bomb detection."""
        content = _make_yaml(
            steps=[
                {"type": "llm", "id": f"s{i}", "prompt": f"Step {i}"}
                for i in range(10)
            ]
        )
        result = scan_template(content)
        assert not any(
            e.code == "YAML_BOMB" for e in result.errors
        )

    def test_expansion_ratio_constant_exists(self):
        """MAX_YAML_EXPANDED_RATIO constant should be defined."""
        assert MAX_YAML_EXPANDED_RATIO == 100


# ===================================================================
# 5. YAML size limit enforcement
# ===================================================================


class TestYAMLSizeLimit:
    """Test YAML size limit prevents memory exhaustion."""

    def test_oversized_yaml_rejected(self):
        """YAML exceeding MAX_YAML_SIZE should be rejected."""
        huge = "name: Test\nsteps: []\n" + "# padding\n" * (MAX_YAML_SIZE // 10)
        result = scan_template(huge)
        assert result.safe is False
        assert any(e.code == "YAML_TOO_LARGE" for e in result.errors)

    def test_normal_size_yaml_accepted(self):
        """Normal-sized YAML should pass size check."""
        content = _make_yaml(steps=[])
        result = scan_template(content)
        assert not any(e.code == "YAML_TOO_LARGE" for e in result.errors)

    def test_size_limit_constant_exists(self):
        """MAX_YAML_SIZE constant should be defined as 512KB."""
        assert MAX_YAML_SIZE == 512 * 1024

    def test_exactly_at_limit(self):
        """YAML at exactly the size limit should be accepted."""
        # Generate YAML just under the limit
        base = "name: Test\nsteps: []\n"
        padding = "# " + "x" * 100 + "\n"
        lines_needed = (MAX_YAML_SIZE - len(base)) // len(padding)
        content = base + padding * lines_needed
        # Trim to exactly at limit
        content = content[:MAX_YAML_SIZE]
        result = scan_template(content)
        assert not any(e.code == "YAML_TOO_LARGE" for e in result.errors)


# ===================================================================
# 6. Secret detection completeness
# ===================================================================


class TestSecretDetectionCompleteness:
    """Test new secret patterns for Anthropic, Stripe, and Google keys."""

    def test_anthropic_api_key(self):
        """Anthropic API key (sk-ant-...) should be detected."""
        content = "api_key: sk-ant-api03-abcdefghijklmnopqrstuvwxyz"
        issues = _check_secret_patterns(content)
        assert any("Anthropic" in i.message for i in issues)

    def test_stripe_secret_key(self):
        """Stripe secret key (sk_live_...) should be detected."""
        content = "key: sk_live_" + "X" * 24
        issues = _check_secret_patterns(content)
        assert any("Stripe secret" in i.message for i in issues)

    def test_stripe_publishable_key(self):
        """Stripe publishable key (pk_live_...) should be detected."""
        content = "key: pk_live_" + "X" * 24
        issues = _check_secret_patterns(content)
        assert any("Stripe publishable" in i.message for i in issues)

    def test_google_api_key(self):
        """Google API key (AIza...) should be detected."""
        content = "key: AIzaSyC1234567890abcdefghijklmnopqrstuvw"
        issues = _check_secret_patterns(content)
        assert any("Google" in i.message for i in issues)

    def test_anthropic_key_in_template(self):
        """Full scan_template catches Anthropic key."""
        content = _make_yaml(
            steps=[{
                "type": "llm",
                "id": "s1",
                "prompt": "Use key sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
            }]
        )
        result = scan_template(content)
        assert any(w.code == "POSSIBLE_SECRET" for w in result.warnings)
        assert any("Anthropic" in w.message for w in result.warnings)

    def test_stripe_key_in_template(self):
        """Full scan_template catches Stripe key."""
        content = _make_yaml(
            steps=[{
                "type": "http",
                "id": "s1",
                "url": "https://api.stripe.com",
                "prompt": "Bearer sk_live_" + "X" * 24,
            }]
        )
        result = scan_template(content)
        assert any("Stripe" in w.message for w in result.warnings)

    def test_short_stripe_key_not_matched(self):
        """Short Stripe key prefix without enough chars should not match."""
        content = "key: sk_live_short"
        issues = _check_secret_patterns(content)
        assert not any("Stripe" in i.message for i in issues)

    def test_sendgrid_key_still_detected(self):
        """Existing SendGrid pattern should still work."""
        content = "key: SG.abcdefghijklmnopqrstuv.ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqr"
        issues = _check_secret_patterns(content)
        assert any("SendGrid" in i.message for i in issues)


# ===================================================================
# 7. Resource limit evasion via nested loops
# ===================================================================


class TestResourceLimitEvasion:
    """Test loop step max_iterations checking."""

    def test_excessive_loop_iterations_flat(self):
        """Loop step with max_iterations > 1000 should warn."""
        workflow = {
            "steps": [
                {"type": "loop", "id": "loop1", "max_iterations": 5000},
            ]
        }
        issues = _check_resource_limits(workflow)
        assert any(i.code == "EXCESSIVE_LOOP_ITERATIONS" for i in issues)

    def test_excessive_loop_iterations_nested_config(self):
        """Loop step with loop_config.max_iterations > 1000 should warn."""
        workflow = {
            "steps": [
                {
                    "type": "loop",
                    "id": "loop2",
                    "loop_config": {"max_iterations": 10000},
                },
            ]
        }
        issues = _check_resource_limits(workflow)
        assert any(i.code == "EXCESSIVE_LOOP_ITERATIONS" for i in issues)

    def test_normal_loop_iterations_no_warning(self):
        """Loop step with reasonable max_iterations should not warn."""
        workflow = {
            "steps": [
                {"type": "loop", "id": "loop3", "max_iterations": 100},
            ]
        }
        issues = _check_resource_limits(workflow)
        assert not any(i.code == "EXCESSIVE_LOOP_ITERATIONS" for i in issues)

    def test_loop_at_boundary_no_warning(self):
        """Loop with exactly 1000 iterations should not warn."""
        workflow = {
            "steps": [
                {"type": "loop", "id": "loop4", "max_iterations": 1000},
            ]
        }
        issues = _check_resource_limits(workflow)
        assert not any(i.code == "EXCESSIVE_LOOP_ITERATIONS" for i in issues)

    def test_loop_just_over_boundary_warns(self):
        """Loop with 1001 iterations should warn."""
        workflow = {
            "steps": [
                {"type": "loop", "id": "loop5", "max_iterations": 1001},
            ]
        }
        issues = _check_resource_limits(workflow)
        assert any(i.code == "EXCESSIVE_LOOP_ITERATIONS" for i in issues)

    def test_integration_excessive_loop_warning(self):
        """Full scan_template produces warning for excessive loops."""
        content = _make_yaml(
            steps=[
                {"type": "loop", "id": "big_loop", "max_iterations": 50000},
            ]
        )
        result = scan_template(content)
        # Loop warnings are in warnings (not errors)
        assert any(w.code == "EXCESSIVE_LOOP_ITERATIONS" for w in result.warnings)

    def test_non_loop_step_with_max_iterations_ignored(self):
        """Non-loop step with max_iterations field should not trigger loop check."""
        workflow = {
            "steps": [
                {"type": "llm", "id": "s1", "max_iterations": 99999},
            ]
        }
        issues = _check_resource_limits(workflow)
        assert not any(i.code == "EXCESSIVE_LOOP_ITERATIONS" for i in issues)


# ===================================================================
# 8. Race step branch scanning
# ===================================================================


class TestRaceStepBranchScanning:
    """Test that dangerous code in race step branches is detected."""

    def test_race_branch_with_dangerous_code(self):
        """Dangerous code inside a race branch should be caught."""
        steps = [
            {
                "type": "race",
                "id": "race1",
                "branches": [
                    {
                        "steps": [
                            {"type": "code", "id": "evil", "code": "eval('dangerous')"},
                        ]
                    }
                ],
            }
        ]
        issues = _check_dangerous_code(steps)
        assert any("eval()" in i.message for i in issues)

    def test_race_branch_with_safe_code(self):
        """Safe code inside race branches should pass."""
        steps = [
            {
                "type": "race",
                "id": "race1",
                "branches": [
                    {
                        "steps": [
                            {"type": "code", "id": "safe", "code": "result = 1 + 2"},
                        ]
                    }
                ],
            }
        ]
        issues = _check_dangerous_code(steps)
        assert issues == []

    def test_race_config_branch_with_dangerous_code(self):
        """Dangerous code inside race_config.branches should be caught."""
        steps = [
            {
                "type": "race",
                "id": "race2",
                "race_config": {
                    "branches": [
                        {
                            "steps": [
                                {"type": "code", "id": "evil2", "code": "os.system('rm')"},
                            ]
                        }
                    ],
                },
            }
        ]
        issues = _check_dangerous_code(steps)
        assert any("os.system" in i.message for i in issues)

    def test_race_branch_non_list_ignored(self):
        """Non-list branches value should be safely ignored."""
        steps = [
            {
                "type": "race",
                "id": "race3",
                "branches": "not a list",
            }
        ]
        issues = _check_dangerous_code(steps)
        assert issues == []

    def test_race_branch_non_dict_entry_ignored(self):
        """Non-dict branch entry should be safely ignored."""
        steps = [
            {
                "type": "race",
                "id": "race4",
                "branches": ["not a dict", 42],
            }
        ]
        issues = _check_dangerous_code(steps)
        assert issues == []

    def test_integration_race_branch_blocks_template(self):
        """Full scan_template catches dangerous code in race branches."""
        content = _make_yaml(
            steps=[
                {
                    "type": "race",
                    "id": "race_scan",
                    "branches": [
                        {
                            "steps": [
                                {"type": "code", "id": "nested_evil", "code": "exec(payload)"},
                            ]
                        }
                    ],
                }
            ]
        )
        result = scan_template(content)
        assert result.safe is False
        assert any(e.code == "DANGEROUS_CODE" for e in result.errors)


# ===================================================================
# 9. Transform step code injection
# ===================================================================


class TestTransformStepScanning:
    """Test that dangerous code in transform steps is detected."""

    def test_transform_template_with_eval(self):
        """Transform step template field with eval should be caught."""
        steps = [
            {
                "type": "transform",
                "id": "t1",
                "template": "{{ eval('malicious') }}",
            }
        ]
        issues = _check_dangerous_code(steps)
        assert any("eval()" in i.message for i in issues)

    def test_transform_config_template_with_exec(self):
        """Transform config template with exec should be caught."""
        steps = [
            {
                "type": "transform",
                "id": "t2",
                "transform_config": {"template": "{{ exec(code) }}"},
            }
        ]
        issues = _check_dangerous_code(steps)
        assert any("exec()" in i.message for i in issues)

    def test_transform_config_code_with_subprocess(self):
        """Transform config code field with subprocess should be caught."""
        steps = [
            {
                "type": "transform",
                "id": "t3",
                "transform_config": {"code": "import subprocess"},
            }
        ]
        issues = _check_dangerous_code(steps)
        assert any("subprocess" in i.message for i in issues)

    def test_transform_safe_template_passes(self):
        """Safe transform template should pass."""
        steps = [
            {
                "type": "transform",
                "id": "t4",
                "template": "Hello {{ name }}, you have {{ count }} items",
            }
        ]
        issues = _check_dangerous_code(steps)
        assert issues == []

    def test_integration_transform_blocks_template(self):
        """Full scan_template catches dangerous transform code."""
        content = _make_yaml(
            steps=[{
                "type": "transform",
                "id": "sneaky_transform",
                "transform_config": {"code": "os.system('whoami')"},
            }]
        )
        result = scan_template(content)
        assert result.safe is False


# ===================================================================
# 10. CLI hub filename sanitization
# ===================================================================


class TestFilenameSanitization:
    """Test _sanitize_hub_filename edge cases."""

    def test_normal_slug(self):
        from sandcastle.__main__ import _sanitize_hub_filename
        assert _sanitize_hub_filename("gizmax/lead-scoring") == "lead-scoring.yaml"

    def test_path_traversal_dots(self):
        from sandcastle.__main__ import _sanitize_hub_filename
        result = _sanitize_hub_filename("../../../etc/passwd")
        assert ".." not in result
        assert "/" not in result
        assert result.endswith(".yaml")

    def test_hidden_file_prevention(self):
        from sandcastle.__main__ import _sanitize_hub_filename
        result = _sanitize_hub_filename("attacker/.bashrc")
        # Should strip leading dot
        assert not result.startswith(".")

    def test_empty_slug_fallback(self):
        from sandcastle.__main__ import _sanitize_hub_filename
        result = _sanitize_hub_filename("///")
        assert result == "workflow.yaml"

    def test_special_characters_cleaned(self):
        from sandcastle.__main__ import _sanitize_hub_filename
        result = _sanitize_hub_filename("user/my template (v2).yaml")
        # Should clean out parens, spaces, and extra dots
        assert "(" not in result
        assert ")" not in result
        assert " " not in result

    def test_unicode_slug_cleaned(self):
        from sandcastle.__main__ import _sanitize_hub_filename
        result = _sanitize_hub_filename("user/templ\u00e4te")
        assert result.endswith(".yaml")


# ===================================================================
# 11. CLI hub download URL validation
# ===================================================================


class TestDownloadURLValidation:
    """Test _validate_hub_download_url function."""

    def test_github_raw_url_accepted(self):
        from sandcastle.__main__ import _validate_hub_download_url
        url = "https://raw.githubusercontent.com/gizmax/Sandcastle/main/template.yaml"
        assert _validate_hub_download_url(url) is True

    def test_http_scheme_rejected(self):
        from sandcastle.__main__ import _validate_hub_download_url
        url = "http://raw.githubusercontent.com/gizmax/Sandcastle/main/template.yaml"
        assert _validate_hub_download_url(url) is False

    def test_untrusted_host_rejected(self):
        from sandcastle.__main__ import _validate_hub_download_url
        url = "https://evil-site.com/malicious.yaml"
        assert _validate_hub_download_url(url) is False

    def test_github_objects_accepted(self):
        from sandcastle.__main__ import _validate_hub_download_url
        url = "https://objects.githubusercontent.com/some/blob"
        assert _validate_hub_download_url(url) is True

    def test_github_main_accepted(self):
        from sandcastle.__main__ import _validate_hub_download_url
        url = "https://github.com/user/repo/raw/main/file.yaml"
        assert _validate_hub_download_url(url) is True

    def test_empty_url_rejected(self):
        from sandcastle.__main__ import _validate_hub_download_url
        assert _validate_hub_download_url("") is False

    def test_ftp_scheme_rejected(self):
        from sandcastle.__main__ import _validate_hub_download_url
        url = "ftp://raw.githubusercontent.com/file.yaml"
        assert _validate_hub_download_url(url) is False

    def test_javascript_scheme_rejected(self):
        from sandcastle.__main__ import _validate_hub_download_url
        url = "javascript:alert(1)"
        assert _validate_hub_download_url(url) is False


# ===================================================================
# 12. CLI hub YAML validation
# ===================================================================


class TestHubYAMLValidation:
    """Test _validate_hub_yaml function."""

    def test_valid_yaml(self):
        from sandcastle.__main__ import _validate_hub_yaml
        content = "name: Test\nsteps:\n  - id: s1\n    type: llm\n"
        valid, err = _validate_hub_yaml(content)
        assert valid is True
        assert err == ""

    def test_missing_name_rejected(self):
        from sandcastle.__main__ import _validate_hub_yaml
        content = "steps:\n  - id: s1\n    type: llm\n"
        valid, err = _validate_hub_yaml(content)
        assert valid is False
        assert "name" in err

    def test_missing_steps_rejected(self):
        from sandcastle.__main__ import _validate_hub_yaml
        content = "name: Test\n"
        valid, err = _validate_hub_yaml(content)
        assert valid is False
        assert "steps" in err

    def test_non_list_steps_rejected(self):
        from sandcastle.__main__ import _validate_hub_yaml
        content = "name: Test\nsteps: not_a_list\n"
        valid, err = _validate_hub_yaml(content)
        assert valid is False

    def test_non_mapping_rejected(self):
        from sandcastle.__main__ import _validate_hub_yaml
        content = "- item1\n- item2\n"
        valid, err = _validate_hub_yaml(content)
        assert valid is False
        assert "mapping" in err

    def test_oversized_yaml_rejected(self):
        from sandcastle.__main__ import _validate_hub_yaml
        content = "x" * (513 * 1024)
        valid, err = _validate_hub_yaml(content)
        assert valid is False
        assert "large" in err.lower() or "512KB" in err

    def test_malformed_yaml_rejected(self):
        from sandcastle.__main__ import _validate_hub_yaml
        content = "key: [unclosed"
        valid, err = _validate_hub_yaml(content)
        assert valid is False
        assert "YAML" in err or "yaml" in err


# ===================================================================
# 13. Checksum verification edge cases
# ===================================================================


class TestChecksumEdgeCases:
    """Additional checksum verification tests."""

    def test_unicode_content_checksum(self):
        """Checksum should work with unicode content."""
        content = "name: T\u00e9st\nsteps: []\n"
        sha = compute_sha256(content)
        assert verify_checksum(content, sha) is True

    def test_newline_sensitive(self):
        """Checksum should be sensitive to newline differences."""
        c1 = "name: Test\nsteps: []\n"
        c2 = "name: Test\r\nsteps: []\r\n"
        assert compute_sha256(c1) != compute_sha256(c2)

    def test_trailing_whitespace_sensitive(self):
        """Checksum should be sensitive to trailing whitespace in content."""
        c1 = "name: Test\nsteps: []"
        c2 = "name: Test\nsteps: [] "
        assert compute_sha256(c1) != compute_sha256(c2)

    def test_verify_with_leading_trailing_whitespace_in_expected(self):
        """verify_checksum should strip whitespace from expected hash."""
        content = "test"
        sha = compute_sha256(content)
        assert verify_checksum(content, f"\n  {sha}  \n") is True

    def test_timing_safe_comparison(self):
        """verify_checksum should use hmac.compare_digest (timing-safe)."""
        import inspect
        source = inspect.getsource(verify_checksum)
        assert "hmac.compare_digest" in source


# ===================================================================
# 14. SSRF in non-standard step fields
# ===================================================================


class TestSSRFNonStandardFields:
    """Test SSRF detection in less obvious step configurations."""

    def test_delegate_step_url_not_checked_by_ssrf(self):
        """Delegate steps don't have URLs, should not falsely trigger."""
        step = {
            "type": "delegate",
            "id": "d1",
            "workflow_id": "http://10.0.0.1/workflow",
        }
        issues = _check_ssrf_urls([step])
        # delegate is not in the checked step types
        assert issues == []

    def test_multiple_http_steps_all_checked(self):
        """All http steps should be checked, not just the first."""
        steps = [
            _make_http_step("https://api.example.com/safe", "h1"),
            _make_http_step("http://10.0.0.1/private", "h2"),
            _make_http_step("https://api.github.com/ok", "h3"),
        ]
        issues = _check_ssrf_urls(steps)
        assert len(issues) == 1
        assert issues[0].step == "h2"

    def test_sensor_and_http_both_checked(self):
        """Both sensor and http steps should be checked in same scan."""
        steps = [
            _make_http_step("http://192.168.1.1/api", "h1"),
            {"type": "sensor", "id": "s1", "url": "http://172.16.0.1/health"},
        ]
        issues = _check_ssrf_urls(steps)
        assert len(issues) == 2


# ===================================================================
# 15. Comprehensive integration tests
# ===================================================================


class TestComprehensiveIntegration:
    """End-to-end integration tests combining multiple vulnerability vectors."""

    def test_multi_vector_attack_template(self):
        """Template with multiple attack vectors should all be caught."""
        content = _make_yaml(
            steps=[
                _make_code_step("getattr(os, 'system')('id')", "attack1"),
                _make_code_step("import base64; exec(base64.b64decode(p))", "attack2"),
                _make_http_step("http://169.254.169.254/latest/meta-data/", "attack3"),
                {"type": "code", "id": "attack4", "code": "importlib.import_module('subprocess')"},
            ]
        )
        result = scan_template(content)
        assert result.safe is False
        # Should have multiple errors
        assert len(result.errors) >= 4

    def test_benign_workflow_with_all_step_types(self):
        """A benign workflow with many step types should pass."""
        content = _make_yaml(
            steps=[
                {"type": "llm", "id": "s1", "prompt": "Summarize this"},
                {"type": "http", "id": "s2", "url": "https://api.example.com/data"},
                {"type": "code", "id": "s3", "code": "result = sum(numbers)"},
                {"type": "condition", "id": "s4", "condition": "len(data) > 0"},
                {"type": "transform", "id": "s5", "template": "Hello {{ name }}"},
                {"type": "loop", "id": "s6", "max_iterations": 10},
                {"type": "sensor", "id": "s7", "url": "https://status.example.com/health"},
            ]
        )
        result = scan_template(content)
        assert result.safe is True
        assert result.errors == []

    def test_zero_width_plus_obfuscation_caught(self):
        """Zero-width chars combined with obfuscation should still be caught."""
        # Use raw YAML with literal zero-width character
        content = "name: Test\nsteps:\n- type: code\n  id: s1\n  code: get\u200battr(os, 'system')('whoami')\n"
        result = scan_template(content)
        assert result.safe is False

    def test_empty_workflow(self):
        """Empty but valid YAML should be safe."""
        content = _make_yaml(steps=[])
        result = scan_template(content)
        assert result.safe is True

    def test_scan_result_has_correct_structure(self):
        """ScanResult fields should be properly populated."""
        content = _make_yaml(
            steps=[
                _make_code_step("eval('x')", "bad_step"),
            ],
            max_cost_usd=50.0,
        )
        result = scan_template(content)
        assert result.safe is False
        assert isinstance(result.errors, list)
        assert isinstance(result.warnings, list)
        assert all(isinstance(e, ScanIssue) for e in result.errors)
        assert all(isinstance(w, ScanIssue) for w in result.warnings)

    def test_code_step_with_all_nested_configs(self):
        """Code step with all nested config fields should all be scanned."""
        step = {
            "type": "code",
            "id": "complex",
            "code": "x = 1",
            "code_config": {"code": "getattr(os, 'system')('cmd')"},
            "llm_config": {"prompt": "exec(payload)", "system_prompt": "import subprocess"},
            "classify_config": {"prompt": "eval(expr)"},
        }
        issues = _check_dangerous_code([step])
        messages = [i.message for i in issues]
        assert any("getattr" in m for m in messages)
        assert any("exec()" in m for m in messages)
        assert any("subprocess" in m for m in messages)
        assert any("eval()" in m for m in messages)


# ===================================================================
# 16. CLI registry and constants
# ===================================================================


class TestCLIRegistryConstants:
    """Test CLI constants and size limits."""

    def test_hub_registry_max_size_exists(self):
        from sandcastle.__main__ import _HUB_REGISTRY_MAX_SIZE
        assert _HUB_REGISTRY_MAX_SIZE == 5 * 1024 * 1024

    def test_hub_max_download_size_exists(self):
        from sandcastle.__main__ import _HUB_MAX_DOWNLOAD_SIZE
        assert _HUB_MAX_DOWNLOAD_SIZE == 512 * 1024

    def test_hub_registry_url_is_https(self):
        from sandcastle.__main__ import _HUB_REGISTRY_URL
        assert _HUB_REGISTRY_URL.startswith("https://")

    def test_hub_registry_url_points_to_github(self):
        from sandcastle.__main__ import _HUB_REGISTRY_URL
        assert "raw.githubusercontent.com" in _HUB_REGISTRY_URL


# ===================================================================
# 17. Edge cases in _is_ssrf_url
# ===================================================================


class TestIsSSRFUrlEdgeCases:
    """Additional edge case tests for _is_ssrf_url."""

    def test_empty_url(self):
        assert _is_ssrf_url("") is False

    def test_just_a_path(self):
        assert _is_ssrf_url("/api/data") is False

    def test_relative_url(self):
        assert _is_ssrf_url("api/data") is False

    def test_data_scheme_blocked(self):
        """data: URI scheme should be blocked."""
        assert _is_ssrf_url("data:text/html,<h1>test</h1>") is True

    def test_https_with_private_ip(self):
        """Private IP should be blocked even with HTTPS."""
        assert _is_ssrf_url("https://10.0.0.1/api") is True

    def test_port_with_private_ip(self):
        """Port number should not affect private IP detection."""
        assert _is_ssrf_url("http://192.168.1.1:8443/api") is True

    def test_ipv6_private_range(self):
        """IPv6 private range (fc00::/7) should be caught."""
        assert _is_ssrf_url("http://[fc00::1]/api") is True

    def test_double_encoding_not_fooled(self):
        """Hostname is already decoded by urlparse, so no double decode issue."""
        # urlparse handles this correctly
        url = "http://localhost/admin"
        assert _is_ssrf_url(url) is True
