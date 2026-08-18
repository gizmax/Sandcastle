"""Regression tests for the four blockers found reviewing commit 6d52711.

Each of these was a way for the recovered July work to fail open: send mail an
attacker controls, recurse until a worker dies, silently disable every policy on
a step, or publish unscrubbed PII. They are grouped here because they share a
cause, not a module.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

CONNECTOR = (
    Path(__file__).parent.parent
    / "src" / "sandcastle" / "engine" / "tools" / "connectors" / "gmail.mjs"
)


def _node_missing() -> bool:
    try:
        subprocess.run(["node", "--version"], capture_output=True, check=True)
    except Exception:
        return True
    return False


@pytest.mark.skipif(_node_missing(), reason="node not available")
class TestSmtpInjection:
    """gmail.mjs was dead on main (bad import); this batch switches it on."""

    def _send(self, to: str, subject: str = "Subject") -> subprocess.CompletedProcess:
        script = f"""
        const m = await import({str(CONNECTOR)!r});
        try {{
            await m.send_email(process.argv[1], process.argv[2], "body");
            console.log("SENT");
        }} catch (e) {{
            console.log("REJECTED:" + e.message);
        }}
        """
        return subprocess.run(
            ["node", "--input-type=module", "-e", script, "--", to, subject],
            capture_output=True, text=True, timeout=60,
            env={
                "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                "TOOL_SMTP_USER": "sender@example.com",
                "TOOL_SMTP_PASSWORD": "pw",
                "TOOL_SMTP_HOST": "127.0.0.1",
                "TOOL_SMTP_PORT": "1",
            },
        )

    def test_crlf_in_recipient_is_rejected(self):
        """A second RCPT TO would BCC every notification silently."""
        out = self._send("a@b.com\r\nRCPT TO:<evil@x.com>")
        assert "REJECTED" in out.stdout
        assert "newline" in out.stdout

    def test_lf_in_recipient_is_rejected(self):
        assert "REJECTED" in self._send("a@b.com\nRCPT TO:<evil@x.com>").stdout

    def test_nul_in_recipient_is_rejected(self):
        """Built inside JS: a NUL cannot survive argv, so it is constructed there."""
        script = f"""
        const m = await import({str(CONNECTOR)!r});
        try {{
            await m.send_email("a@b.com" + String.fromCharCode(0), "Subject", "body");
            console.log("SENT");
        }} catch (e) {{
            console.log("REJECTED:" + e.message);
        }}
        """
        out = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            capture_output=True, text=True, timeout=60,
            env={
                "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                "TOOL_SMTP_USER": "sender@example.com",
                "TOOL_SMTP_PASSWORD": "pw",
                "TOOL_SMTP_HOST": "127.0.0.1",
                "TOOL_SMTP_PORT": "1",
            },
        )
        assert "REJECTED" in out.stdout

    def test_crlf_in_subject_is_rejected(self):
        """Buys a forged Reply-To or a split body."""
        out = self._send("a@b.com", "Hi\r\nBcc: evil@x.com")
        assert "REJECTED" in out.stdout

    def test_malformed_address_is_rejected(self):
        assert "REJECTED" in self._send("not-an-address").stdout

    def test_password_is_not_in_argv(self):
        """argv is world-readable via ps and /proc/<pid>/cmdline."""
        source = CONNECTOR.read_text(encoding="utf-8")
        assert '"--user"' not in source
        assert "--config" in source


class TestCompositeStepCycles:
    """A loop listing itself validated clean and recursed until the stack died."""

    def _validate(self, yaml_text: str) -> list[str]:
        from sandcastle.engine.dag import parse_yaml_string, validate

        return validate(parse_yaml_string(yaml_text))

    def test_self_referencing_loop_is_rejected(self):
        errors = self._validate("""
name: cyclic-loop
steps:
  - id: a
    type: loop
    loop_config:
      over: "{input.items}"
      step_ids: [a]
""")
        assert any("cannot include itself" in e for e in errors)

    def test_self_referencing_race_is_rejected(self):
        errors = self._validate("""
name: cyclic-race
steps:
  - id: r
    type: race
    race_config:
      branches: [[r]]
""")
        assert any("cannot include itself" in e for e in errors)

    def test_unknown_loop_target_is_rejected(self):
        errors = self._validate("""
name: bad-loop-target
steps:
  - id: a
    type: loop
    loop_config:
      over: "{input.items}"
      step_ids: [nope]
""")
        assert any("unknown step" in e for e in errors)

    def test_valid_loop_still_passes(self):
        errors = self._validate("""
name: fine
steps:
  - id: work
    type: code
    code_config:
      language: python
      code: "result = 1"
  - id: a
    type: loop
    loop_config:
      over: "{input.items}"
      step_ids: [work]
""")
        assert not any("itself" in e or "unknown step" in e for e in errors)


class TestPolicyFailsClosed:
    """One bad apply_to used to disable block and inject_approval too."""

    def _policy(self, targets):
        from sandcastle.engine.policy import PolicyAction, PolicyDefinition, PolicyTrigger

        return PolicyDefinition(
            id="p",
            trigger=PolicyTrigger(type="pattern"),
            action=PolicyAction(type="redact", apply_to=targets),
            severity="high",
        )

    def test_invalid_target_raises_config_error(self):
        from sandcastle.engine.policy import PolicyConfigError, PolicyEngine

        with pytest.raises(PolicyConfigError):
            PolicyEngine([self._policy(["nonsense"])])

    def test_privacy_router_default_is_accepted(self):
        """privacy.py defaults to ["outputs", "webhooks"] - the plural was rejected."""
        from sandcastle.engine.policy import PolicyEngine

        PolicyEngine([self._policy(["outputs", "webhooks"])])

    def test_config_error_is_not_a_bare_value_error_by_accident(self):
        from sandcastle.engine.policy import PolicyConfigError

        assert issubclass(PolicyConfigError, ValueError)

    def test_invalid_severity_also_fails_closed(self):
        from sandcastle.engine.policy import (
            PolicyAction,
            PolicyConfigError,
            PolicyDefinition,
            PolicyEngine,
            PolicyTrigger,
        )

        bad = PolicyDefinition(
            id="p",
            trigger=PolicyTrigger(type="pattern"),
            action=PolicyAction(type="redact", apply_to=["outputs"]),
            severity="catastrophic",
        )
        with pytest.raises(PolicyConfigError):
            PolicyEngine([bad])


class TestEventBusScrubbing:
    """Published events are never rewritten, unlike the DB row."""

    def test_scrub_uses_the_run_privacy_router(self):
        from sandcastle.engine.executor import RunContext, _scrub_for_event_bus

        class _Router:
            class config:
                apply_to = ["outputs"]

            def scrub_dict(self, value):
                return "[REDACTED]", ["match"]

        ctx = RunContext(run_id="r", input={})
        ctx._privacy_router = _Router()
        assert _scrub_for_event_bus("email: a@b.com", "s1", ctx) == "[REDACTED]"

    def test_output_withheld_when_scrubbing_fails(self):
        """Publishing raw output on failure is the thing being prevented."""
        from sandcastle.engine.executor import RunContext, _scrub_for_event_bus

        class _Broken:
            class config:
                apply_to = ["outputs"]

            def scrub_dict(self, value):
                raise RuntimeError("boom")

        ctx = RunContext(run_id="r", input={})
        ctx._privacy_router = _Broken()
        out = _scrub_for_event_bus("secret@example.com", "s1", ctx)
        assert "withheld" in out
        assert "secret@example.com" not in out

    def test_passthrough_without_a_router(self):
        from sandcastle.engine.executor import RunContext, _scrub_for_event_bus

        ctx = RunContext(run_id="r", input={})
        assert _scrub_for_event_bus("plain", "s1", ctx) == "plain"

    def test_passthrough_when_outputs_not_targeted(self):
        from sandcastle.engine.executor import RunContext, _scrub_for_event_bus

        class _Router:
            class config:
                apply_to = ["webhooks"]

            def scrub_dict(self, value):  # pragma: no cover - must not be called
                raise AssertionError("should not scrub")

        ctx = RunContext(run_id="r", input={})
        ctx._privacy_router = _Router()
        assert _scrub_for_event_bus("plain", "s1", ctx) == "plain"
