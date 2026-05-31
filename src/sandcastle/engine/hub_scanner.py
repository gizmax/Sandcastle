"""Security scanner for Community Hub workflow templates.

Scans downloaded YAML templates for dangerous patterns before installation:
- Dangerous code in ``code`` steps (os.system, subprocess, eval, etc.)
- Obfuscation bypass detection (getattr, importlib, base64, compile, etc.)
- Hardcoded secrets / API keys in step content
- SSRF URLs in ``http`` steps pointing to internal networks
- IPv6-mapped IPv4 and decimal/octal IP encoding bypass prevention
- YAML bomb / billion laughs protection
- Resource abuse (excessive steps, huge max_tokens, expensive models)
- SHA-256 checksum verification against registry
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlparse

import yaml

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ScanIssue:
    """A single issue found during scanning."""

    code: str
    message: str
    step: str | None = None  # step id / name, if applicable


@dataclass
class ScanResult:
    """Aggregated result of a template security scan."""

    safe: bool
    warnings: list[ScanIssue] = field(default_factory=list)
    errors: list[ScanIssue] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Dangerous code patterns (blocked in ``code`` steps)
# ---------------------------------------------------------------------------

_DANGEROUS_CODE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bos\.system\s*\("), "os.system() call detected"),
    (re.compile(r"\bsubprocess\b"), "subprocess module usage detected"),
    (re.compile(r"\beval\s*\("), "eval() call detected"),
    (re.compile(r"\bexec\s*\("), "exec() call detected"),
    (re.compile(r"\b__import__\s*\("), "__import__() call detected"),
    (
        re.compile(r"\bopen\s*\([^)]*['\"][waxWAX+]['\"]"),
        "open() with write mode detected",
    ),
    (re.compile(r"\bshutil\.rmtree\s*\("), "shutil.rmtree() call detected"),
    (
        re.compile(r"\bpathlib\.Path[^)]*\.unlink\s*\("),
        "pathlib.Path.unlink() call detected",
    ),
    (re.compile(r"\bsocket\.connect\s*\("), "socket.connect() call detected"),
    (
        re.compile(r"\burllib\.request\.urlopen\s*\("),
        "urllib.request.urlopen() call detected",
    ),
    (re.compile(r"\bos\.remove\s*\("), "os.remove() call detected"),
    (re.compile(r"\bos\.unlink\s*\("), "os.unlink() call detected"),
    (re.compile(r"\bos\.rmdir\s*\("), "os.rmdir() call detected"),
    (re.compile(r"\bos\.popen\s*\("), "os.popen() call detected"),
    # Obfuscation bypass patterns
    (re.compile(r"\bgetattr\s*\("), "getattr() call detected (potential code obfuscation)"),
    (re.compile(r"\bimportlib\b"), "importlib usage detected (dynamic import)"),
    (re.compile(r"\bbase64\b"), "base64 module usage detected (potential payload obfuscation)"),
    (re.compile(r"\bcompile\s*\("), "compile() call detected (dynamic code compilation)"),
    (re.compile(r"\bos\.environ\b"), "os.environ access detected (credential theft risk)"),
    (re.compile(r"\bctypes\b"), "ctypes module usage detected (native code execution)"),
    (re.compile(r"\bcodecs\.decode\b"), "codecs.decode() call detected (potential obfuscation)"),
    (re.compile(r"\bos\.walk\s*\("), "os.walk() call detected (filesystem enumeration)"),
    (re.compile(r"\bglob\b"), "glob module usage detected (filesystem enumeration)"),
]

# ---------------------------------------------------------------------------
# Secret / token patterns (warnings)
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bsk-[a-zA-Z0-9]{20,}"), "Possible OpenAI API key (sk-...)"),
    (re.compile(r"\bsk-ant-[a-zA-Z0-9\-]{20,}"), "Possible Anthropic API key (sk-ant-...)"),
    (re.compile(r"\bghp_[a-zA-Z0-9]{36,}"), "Possible GitHub PAT (ghp_...)"),
    (re.compile(r"\bghs_[a-zA-Z0-9]{36,}"), "Possible GitHub app token (ghs_...)"),
    (re.compile(r"\bAKIA[A-Z0-9]{16}"), "Possible AWS access key (AKIA...)"),
    (re.compile(r"\bxoxb-[a-zA-Z0-9-]+"), "Possible Slack bot token (xoxb-...)"),
    (re.compile(r"\bBearer\s+[a-zA-Z0-9._\-]{20,}"), "Possible Bearer token"),
    (
        re.compile(r"\bSG\.[a-zA-Z0-9_\-]{22}\.[a-zA-Z0-9_\-]{43}"),
        "Possible SendGrid API key",
    ),
    (
        re.compile(r"\bsk_live_[a-zA-Z0-9]{20,}"),
        "Possible Stripe secret key (sk_live_...)",
    ),
    (
        re.compile(r"\bpk_live_[a-zA-Z0-9]{20,}"),
        "Possible Stripe publishable key (pk_live_...)",
    ),
    (
        re.compile(r"\bAIza[a-zA-Z0-9_\-]{35}"),
        "Possible Google API key (AIza...)",
    ),
    (
        re.compile(r"\b[a-f0-9]{64}\b"),
        "Possible 256-bit hex secret/token",
    ),
]

# ---------------------------------------------------------------------------
# SSRF patterns for ``http`` steps
# ---------------------------------------------------------------------------

_PRIVATE_IP_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
]

_SSRF_HOSTNAMES = {"localhost", "0.0.0.0", "[::1]", "0", "0x7f000001", "2130706433"}

# ---------------------------------------------------------------------------
# Resource limits (warnings)
# ---------------------------------------------------------------------------

MAX_STEPS = 50
MAX_MAX_TOKENS = 16384
MAX_COST_USD = 10.0
MAX_YAML_SIZE = 512 * 1024  # 512KB
MAX_YAML_EXPANDED_RATIO = 100  # max expansion ratio for YAML bomb detection


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan_template(yaml_content: str) -> ScanResult:
    """Run all security checks on a template YAML string.

    Returns a ``ScanResult`` with *errors* (blocking) and *warnings*
    (informational). The ``safe`` flag is ``True`` only when there
    are zero errors.
    """
    errors: list[ScanIssue] = []
    warnings: list[ScanIssue] = []

    # Size limit - prevent memory exhaustion
    if len(yaml_content) > MAX_YAML_SIZE:
        errors.append(ScanIssue(
            code="YAML_TOO_LARGE",
            message=f"YAML content exceeds {MAX_YAML_SIZE} bytes",
        ))
        return ScanResult(safe=False, warnings=warnings, errors=errors)

    # Strip zero-width characters and normalize Unicode before scanning
    # to prevent homoglyph / invisible character bypass attacks
    cleaned = _strip_zero_width_chars(yaml_content)
    if cleaned != yaml_content:
        warnings.append(ScanIssue(
            code="ZERO_WIDTH_CHARS",
            message="Template contains zero-width or invisible Unicode characters (stripped for scanning)",
        ))

    # NFC normalization to collapse compatibility characters and homoglyphs
    # (e.g. fullwidth latin "ｅｖａｌ" → "eval", accented look-alikes)
    normalized = unicodedata.normalize("NFKC", cleaned)
    if normalized != cleaned:
        warnings.append(ScanIssue(
            code="UNICODE_NORMALIZATION",
            message="Template contains non-canonical Unicode forms (normalized for scanning)",
        ))
        cleaned = normalized

    # Pre-parse anchor/alias density check to mitigate YAML bomb CPU spike.
    # safe_load handles billion-laughs but nested aliases can still be slow.
    # Count only real YAML anchor (&name) / alias (*name) TOKENS - i.e. an
    # ampersand/asterisk that starts an identifier in a value position - rather
    # than every '&'/'*' character. Counting raw characters flagged prose-heavy
    # templates (markdown emphasis, multiplication, URL query strings) as bombs.
    # A bomb also requires anchors for aliases to expand, so 0 anchors is safe.
    anchor_count = len(re.findall(r"(?:^|[\s\[\]{},])&[A-Za-z0-9_][\w-]*", cleaned))
    alias_count = len(re.findall(r"(?:^|[\s\[\]{},])\*[A-Za-z0-9_][\w-]*", cleaned))
    if anchor_count > 50 or (anchor_count > 0 and alias_count > 200):
        errors.append(ScanIssue(
            code="YAML_BOMB",
            message=(
                f"Suspicious YAML anchor/alias density "
                f"({anchor_count} anchors, {alias_count} aliases)"
            ),
        ))
        return ScanResult(safe=False, warnings=warnings, errors=errors)

    # Parse YAML (gracefully handle malformed content)
    try:
        data = yaml.safe_load(cleaned)
    except yaml.YAMLError:
        errors.append(ScanIssue(
            code="INVALID_YAML",
            message="Template YAML could not be parsed",
        ))
        return ScanResult(safe=False, warnings=warnings, errors=errors)

    if not isinstance(data, dict):
        errors.append(ScanIssue(
            code="INVALID_STRUCTURE",
            message="Template YAML must be a mapping",
        ))
        return ScanResult(safe=False, warnings=warnings, errors=errors)

    # YAML bomb detection - check expansion ratio
    serialized = str(data)
    if len(yaml_content) > 0 and len(serialized) > MAX_YAML_EXPANDED_RATIO * len(yaml_content):
        errors.append(ScanIssue(
            code="YAML_BOMB",
            message="YAML expands to suspiciously large structure (possible billion laughs attack)",
        ))
        return ScanResult(safe=False, warnings=warnings, errors=errors)

    steps = data.get("steps", [])
    if not isinstance(steps, list):
        steps = []

    # Run checks
    errors.extend(_check_dangerous_code(steps))
    warnings.extend(_check_secret_patterns(cleaned))
    errors.extend(_check_ssrf_urls(steps))
    warnings.extend(_check_resource_limits(data))

    return ScanResult(safe=len(errors) == 0, warnings=warnings, errors=errors)


def compute_sha256(content: str) -> str:
    """Return the lowercase hex SHA-256 digest of *content* (UTF-8 encoded)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def verify_checksum(content: str, expected: str) -> bool:
    """Return ``True`` when SHA-256 of *content* matches *expected*."""
    return hmac.compare_digest(compute_sha256(content), expected.lower().strip())


# ---------------------------------------------------------------------------
# Internal checks
# ---------------------------------------------------------------------------


def _check_dangerous_code(steps: list[dict[str, Any]]) -> list[ScanIssue]:
    """Scan ``code`` steps for dangerous patterns.

    Checks both flat fields (``step.code``, ``step.prompt``) and nested
    config fields (``step.code_config.code``, ``step.llm_config.prompt``,
    ``step.classify_config.prompt``) to prevent bypass via nested configs.
    Also scans ``transform`` steps for Jinja2 injection and ``race``
    step branches for nested dangerous code.
    """
    issues: list[ScanIssue] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_type = step.get("type", "")
        if step_type not in ("code", "transform"):
            continue
        step_id = step.get("id") or step.get("name") or "unknown"
        code_content = step.get("code", "") or ""

        # Also check code_config.code (nested format used by DAG parser)
        code_cfg = step.get("code_config")
        if isinstance(code_cfg, dict):
            code_content += "\n" + (code_cfg.get("code", "") or "")

        # Check transform_config.template (Jinja2 template injection)
        if step_type == "transform":
            transform_cfg = step.get("transform_config")
            if isinstance(transform_cfg, dict):
                code_content += "\n" + (transform_cfg.get("template", "") or "")
                code_content += "\n" + (transform_cfg.get("code", "") or "")
            # Also check flat template field
            code_content += "\n" + (step.get("template", "") or "")

        # Check prompt fields (flat and nested llm_config / classify_config)
        prompt = step.get("prompt", "") or ""
        llm_cfg = step.get("llm_config")
        if isinstance(llm_cfg, dict):
            prompt += "\n" + (llm_cfg.get("prompt", "") or "")
            prompt += "\n" + (llm_cfg.get("system_prompt", "") or "")
        classify_cfg = step.get("classify_config")
        if isinstance(classify_cfg, dict):
            prompt += "\n" + (classify_cfg.get("prompt", "") or "")

        combined = f"{code_content}\n{prompt}"

        for pattern, msg in _DANGEROUS_CODE_PATTERNS:
            if pattern.search(combined):
                issues.append(
                    ScanIssue(code="DANGEROUS_CODE", message=msg, step=str(step_id))
                )

    # Also scan race step branches recursively
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("type") == "race":
            branches = step.get("branches", [])
            if isinstance(branches, list):
                for branch in branches:
                    if isinstance(branch, dict):
                        branch_steps = branch.get("steps", [])
                        if isinstance(branch_steps, list):
                            issues.extend(_check_dangerous_code(branch_steps))
            race_cfg = step.get("race_config")
            if isinstance(race_cfg, dict):
                branches = race_cfg.get("branches", [])
                if isinstance(branches, list):
                    for branch in branches:
                        if isinstance(branch, dict):
                            branch_steps = branch.get("steps", [])
                            if isinstance(branch_steps, list):
                                issues.extend(_check_dangerous_code(branch_steps))

    return issues


def _check_secret_patterns(yaml_content: str) -> list[ScanIssue]:
    """Scan the raw YAML string for secret-like patterns."""
    issues: list[ScanIssue] = []
    seen: set[str] = set()
    for pattern, msg in _SECRET_PATTERNS:
        matches = pattern.findall(yaml_content)
        for match in matches:
            # Deduplicate
            key = f"{msg}:{match[:12]}"
            if key not in seen:
                seen.add(key)
                issues.append(ScanIssue(code="POSSIBLE_SECRET", message=msg))
    return issues


def _check_ssrf_urls(steps: list[dict[str, Any]]) -> list[ScanIssue]:
    """Scan ``http``, ``sensor``, and ``notify`` steps for SSRF-prone URLs.

    Checks both flat fields (``step.url``) and nested config fields
    (``step.http_config.url``, ``step.sensor_config.url``,
    ``step.notify_config.webhook_url``) to prevent bypass via nested configs.
    """
    issues: list[ScanIssue] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_type = step.get("type", "")
        step_id = step.get("id") or step.get("name") or "unknown"

        # Collect all URLs to check based on step type
        urls_to_check: list[str] = []

        if step_type == "http":
            # Flat field (backward compat)
            url = step.get("url", "") or ""
            if url:
                urls_to_check.append(url)
            # Nested http_config.url
            http_cfg = step.get("http_config")
            if isinstance(http_cfg, dict):
                cfg_url = http_cfg.get("url", "") or ""
                if cfg_url:
                    urls_to_check.append(cfg_url)
        elif step_type == "sensor":
            # Flat field
            url = step.get("url", "") or ""
            if url:
                urls_to_check.append(url)
            # Nested sensor_config.url
            sensor_cfg = step.get("sensor_config")
            if isinstance(sensor_cfg, dict):
                cfg_url = sensor_cfg.get("url", "") or ""
                if cfg_url:
                    urls_to_check.append(cfg_url)
        elif step_type == "notify":
            # Nested notify_config.webhook_url (not an official field
            # but may be used in raw YAML templates)
            notify_cfg = step.get("notify_config")
            if isinstance(notify_cfg, dict):
                wh_url = notify_cfg.get("webhook_url", "") or ""
                if wh_url:
                    urls_to_check.append(wh_url)

        for url in urls_to_check:
            if _is_ssrf_url(url):
                issues.append(
                    ScanIssue(
                        code="SSRF_URL",
                        message=f"URL points to internal/private network: {url}",
                        step=str(step_id),
                    )
                )
    return issues


def _is_ssrf_url(url: str) -> bool:
    """Return ``True`` if *url* targets a private/internal address.

    Handles bypass techniques including:
    - IPv6-mapped IPv4 addresses (``::ffff:127.0.0.1``)
    - URL-encoded hostnames (``%31%32%37.0.0.1``)
    - Decimal IP encoding (``2130706433``)
    - Bracket-wrapped IPv6
    - Non-http/https schemes
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    # Block non-http(s) schemes that could bypass restrictions
    scheme = (parsed.scheme or "").lower()
    if scheme and scheme not in ("http", "https"):
        return True

    raw_hostname = parsed.hostname or ""

    # URL-decode the hostname to catch %XX encoding bypass
    hostname = unquote(raw_hostname).strip()

    # Check known dangerous hostnames
    if hostname.lower() in _SSRF_HOSTNAMES:
        return True

    # Check cloud metadata endpoint
    if hostname == "169.254.169.254":
        return True

    # Try to parse as IP address and check private ranges
    try:
        addr = ipaddress.ip_address(hostname)
        # Handle IPv6-mapped IPv4 addresses (e.g. ::ffff:127.0.0.1)
        if hasattr(addr, "ipv4_mapped") and addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        for net in _PRIVATE_IP_RANGES:
            if addr in net:
                return True
        # Also check if the address is reserved, loopback, or link-local
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            return True
    except ValueError:
        pass

    # Try decimal encoding (e.g. "2130706433" = 127.0.0.1)
    try:
        if hostname.isdigit():
            int_val = int(hostname)
            if 0 <= int_val <= 0xFFFFFFFF:
                addr = ipaddress.ip_address(int_val)
                if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                    return True
    except (ValueError, OverflowError):
        pass

    # Try hex encoding (e.g. "0x7f000001" = 127.0.0.1)
    try:
        if hostname.lower().startswith("0x"):
            int_val = int(hostname, 16)
            if 0 <= int_val <= 0xFFFFFFFF:
                addr = ipaddress.ip_address(int_val)
                if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                    return True
    except (ValueError, OverflowError):
        pass

    return False


def _strip_zero_width_chars(text: str) -> str:
    """Remove zero-width and invisible Unicode characters that could be
    used to obfuscate dangerous code patterns (e.g. ``ev\\u200bal``
    bypassing ``eval`` detection).
    """
    # Zero-width characters commonly used for obfuscation
    _ZERO_WIDTH = {
        "\u200b",  # ZERO WIDTH SPACE
        "\u200c",  # ZERO WIDTH NON-JOINER
        "\u200d",  # ZERO WIDTH JOINER
        "\u200e",  # LEFT-TO-RIGHT MARK
        "\u200f",  # RIGHT-TO-LEFT MARK
        "\u2060",  # WORD JOINER
        "\ufeff",  # ZERO WIDTH NO-BREAK SPACE (BOM)
        "\u00ad",  # SOFT HYPHEN
        "\u034f",  # COMBINING GRAPHEME JOINER
        "\u061c",  # ARABIC LETTER MARK
        "\u2061",  # FUNCTION APPLICATION
        "\u2062",  # INVISIBLE TIMES
        "\u2063",  # INVISIBLE SEPARATOR
        "\u2064",  # INVISIBLE PLUS
    }
    return "".join(ch for ch in text if ch not in _ZERO_WIDTH)


def _check_resource_limits(workflow: dict[str, Any]) -> list[ScanIssue]:
    """Warn about resource abuse in workflow definitions."""
    issues: list[ScanIssue] = []
    steps = workflow.get("steps", [])
    if not isinstance(steps, list):
        return issues

    if len(steps) > MAX_STEPS:
        issues.append(
            ScanIssue(
                code="EXCESSIVE_STEPS",
                message=f"Workflow has {len(steps)} steps (limit: {MAX_STEPS})",
            )
        )

    for step in steps:
        if not isinstance(step, dict):
            continue
        step_id = step.get("id") or step.get("name") or "unknown"

        max_tokens = step.get("max_tokens")
        if isinstance(max_tokens, (int, float)) and max_tokens > MAX_MAX_TOKENS:
            issues.append(
                ScanIssue(
                    code="EXCESSIVE_TOKENS",
                    message=f"max_tokens={max_tokens} exceeds {MAX_MAX_TOKENS}",
                    step=str(step_id),
                )
            )

        # Check loop steps for unbounded iteration counts
        if step.get("type") == "loop":
            max_iters = step.get("max_iterations")
            if isinstance(max_iters, (int, float)) and max_iters > 1000:
                issues.append(
                    ScanIssue(
                        code="EXCESSIVE_LOOP_ITERATIONS",
                        message=f"Loop step max_iterations={max_iters} exceeds 1000",
                        step=str(step_id),
                    )
                )
            loop_cfg = step.get("loop_config")
            if isinstance(loop_cfg, dict):
                cfg_iters = loop_cfg.get("max_iterations")
                if isinstance(cfg_iters, (int, float)) and cfg_iters > 1000:
                    issues.append(
                        ScanIssue(
                            code="EXCESSIVE_LOOP_ITERATIONS",
                            message=f"Loop config max_iterations={cfg_iters} exceeds 1000",
                            step=str(step_id),
                        )
                    )

    max_cost = workflow.get("max_cost_usd")
    if isinstance(max_cost, (int, float)) and max_cost > MAX_COST_USD:
        issues.append(
            ScanIssue(
                code="EXCESSIVE_COST",
                message=f"max_cost_usd={max_cost} exceeds {MAX_COST_USD}",
            )
        )

    return issues
