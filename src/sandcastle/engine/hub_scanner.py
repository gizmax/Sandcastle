"""Security scanner for Community Hub workflow templates.

Scans downloaded YAML templates for dangerous patterns before installation:
- Dangerous code in ``code`` steps (os.system, subprocess, eval, etc.)
- Hardcoded secrets / API keys in step content
- SSRF URLs in ``http`` steps pointing to internal networks
- Resource abuse (excessive steps, huge max_tokens, expensive models)
- SHA-256 checksum verification against registry
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

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
]

# ---------------------------------------------------------------------------
# Secret / token patterns (warnings)
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bsk-[a-zA-Z0-9]{20,}"), "Possible OpenAI API key (sk-...)"),
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

_SSRF_HOSTNAMES = {"localhost", "0.0.0.0", "[::1]"}

# ---------------------------------------------------------------------------
# Resource limits (warnings)
# ---------------------------------------------------------------------------

MAX_STEPS = 50
MAX_MAX_TOKENS = 16384
MAX_COST_USD = 10.0


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

    # Parse YAML (gracefully handle malformed content)
    try:
        data = yaml.safe_load(yaml_content)
    except yaml.YAMLError:
        errors.append(ScanIssue(code="INVALID_YAML", message="Template YAML could not be parsed"))
        return ScanResult(safe=False, warnings=warnings, errors=errors)

    if not isinstance(data, dict):
        errors.append(ScanIssue(
            code="INVALID_STRUCTURE",
            message="Template YAML must be a mapping",
        ))
        return ScanResult(safe=False, warnings=warnings, errors=errors)

    steps = data.get("steps", [])
    if not isinstance(steps, list):
        steps = []

    # Run checks
    errors.extend(_check_dangerous_code(steps))
    warnings.extend(_check_secret_patterns(yaml_content))
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
    """Scan ``code`` steps for dangerous patterns."""
    issues: list[ScanIssue] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("type") != "code":
            continue
        step_id = step.get("id") or step.get("name") or "unknown"
        code_content = step.get("code", "") or ""
        # Also check prompt which may contain inline code
        prompt = step.get("prompt", "") or ""
        combined = f"{code_content}\n{prompt}"

        for pattern, msg in _DANGEROUS_CODE_PATTERNS:
            if pattern.search(combined):
                issues.append(
                    ScanIssue(code="DANGEROUS_CODE", message=msg, step=str(step_id))
                )
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
    """Scan ``http`` steps for SSRF-prone URLs."""
    issues: list[ScanIssue] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("type") != "http":
            continue
        step_id = step.get("id") or step.get("name") or "unknown"
        url = step.get("url", "") or ""
        if not url:
            continue

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
    """Return ``True`` if *url* targets a private/internal address."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    hostname = parsed.hostname or ""

    # Check known dangerous hostnames
    if hostname in _SSRF_HOSTNAMES:
        return True

    # Check cloud metadata endpoint
    if hostname == "169.254.169.254":
        return True

    # Try to parse as IP address and check private ranges
    try:
        addr = ipaddress.ip_address(hostname)
        for net in _PRIVATE_IP_RANGES:
            if addr in net:
                return True
    except ValueError:
        pass

    return False


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

    max_cost = workflow.get("max_cost_usd")
    if isinstance(max_cost, (int, float)) and max_cost > MAX_COST_USD:
        issues.append(
            ScanIssue(
                code="EXCESSIVE_COST",
                message=f"max_cost_usd={max_cost} exceeds {MAX_COST_USD}",
            )
        )

    return issues
