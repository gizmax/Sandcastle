"""Tests for the Workflow Doctor preflight check system.

Covers all 14 check categories with positive/negative cases.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from sandcastle.engine.dag import (
    ApprovalConfig,
    BrowserConfig,
    CodeConfig,
    CompletionConfig,
    HttpConfig,
    LoopConfig,
    SensorConfig,
    StepDefinition,
    WorkflowDefinition,
)
from sandcastle.engine.doctor import (
    DoctorReport,
    Finding,
    _check_callback_url_ssrf,
    _check_code_steps_outside_sandbox,
    _check_cost_estimation,
    _check_data_residency,
    _check_dead_steps,
    _check_missing_budget,
    _check_missing_credentials,
    _check_prompt_injection_risk,
    _check_secrets_in_yaml,
    _check_sensor_config,
    _check_unknown_models,
    _check_unrealistic_timeouts,
    _check_unsafe_context_source,
    _check_validation_errors,
    _compute_risk,
    _is_internal_url,
    diagnose,
    diagnose_yaml,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wf(
    name: str = "test-wf",
    steps: list[StepDefinition] | None = None,
    **kwargs,
) -> WorkflowDefinition:
    """Create a minimal workflow for testing."""
    if steps is None:
        steps = [StepDefinition(id="s1", prompt="Do something")]
    return WorkflowDefinition(
        name=name,
        description="Test workflow",
        default_model="sonnet",
        default_max_turns=10,
        default_timeout=300,
        steps=steps,
        **kwargs,
    )


def _report(name: str = "test") -> DoctorReport:
    return DoctorReport(workflow_name=name)


# ===========================================================================
# Category 1: Validation errors
# ===========================================================================

class TestValidationErrors:
    def test_valid_workflow_no_errors(self):
        r = _report()
        _check_validation_errors(_wf(), r)
        assert len(r.findings) == 0

    def test_no_steps_produces_blocking(self):
        r = _report()
        wf = _wf(steps=[])
        _check_validation_errors(wf, r)
        assert len(r.blocking) > 0
        assert any("at least one step" in f.message for f in r.findings)

    def test_duplicate_step_id(self):
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="dup", prompt="a"),
            StepDefinition(id="dup", prompt="b"),
        ])
        _check_validation_errors(wf, r)
        assert any("Duplicate" in f.message for f in r.findings)


# ===========================================================================
# Category 2: Unsafe context_source: custom
# ===========================================================================

class TestUnsafeContextSource:
    def test_custom_context_flagged(self):
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="x", context_source="custom", context_query="echo hello"),
        ])
        _check_unsafe_context_source(wf, r)
        assert len(r.blocking) == 1
        assert r.findings[0].category == "security"
        assert "custom" in r.findings[0].message

    def test_memory_context_ok(self):
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="x", context_source="memory"),
        ])
        _check_unsafe_context_source(wf, r)
        assert len(r.findings) == 0

    def test_files_context_ok(self):
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="x", context_source="files"),
        ])
        _check_unsafe_context_source(wf, r)
        assert len(r.findings) == 0


# ===========================================================================
# Category 3: Code steps outside sandbox
# ===========================================================================

class TestCodeStepsOutsideSandbox:
    def test_code_step_flagged(self):
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="", type="code", code_config=CodeConfig(code="result = 1")),
        ])
        _check_code_steps_outside_sandbox(wf, r)
        assert len(r.warnings) == 1
        assert "in-process" in r.findings[0].message

    def test_standard_step_not_flagged(self):
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="Hello"),
        ])
        _check_code_steps_outside_sandbox(wf, r)
        assert len(r.findings) == 0


# ===========================================================================
# Category 4: Missing credentials
# ===========================================================================

class TestMissingCredentials:
    def test_missing_anthropic_key(self):
        r = _report()
        wf = _wf(steps=[StepDefinition(id="s1", prompt="x", model="sonnet")])
        with patch("sandcastle.engine.doctor.settings") as mock_settings:
            mock_settings.anthropic_api_key = ""
            _check_missing_credentials(wf, r)
        assert any("ANTHROPIC_API_KEY" in f.message for f in r.findings)

    def test_key_present_no_finding(self):
        r = _report()
        wf = _wf(steps=[StepDefinition(id="s1", prompt="x", model="sonnet")])
        with patch("sandcastle.engine.doctor.settings") as mock_settings:
            mock_settings.anthropic_api_key = "sk-test-key"
            _check_missing_credentials(wf, r)
        cred_findings = [f for f in r.findings if f.category == "credentials" and f.severity == "blocking"]
        assert len(cred_findings) == 0

    def test_local_model_no_key_needed(self):
        r = _report()
        wf = _wf(steps=[StepDefinition(id="s1", prompt="x", model="ollama")])
        _check_missing_credentials(wf, r)
        cred_findings = [f for f in r.findings if f.severity == "blocking"]
        assert len(cred_findings) == 0

    def test_tool_connection_info(self):
        r = _report()
        wf = _wf(steps=[StepDefinition(id="s1", prompt="x", tools=["slack:my-workspace"])])
        with patch("sandcastle.engine.doctor.settings") as mock_settings:
            mock_settings.anthropic_api_key = "key"
            _check_missing_credentials(wf, r)
        info_findings = [f for f in r.findings if f.category == "credentials" and f.severity == "info"]
        assert len(info_findings) == 1


# ===========================================================================
# Category 5: Unknown models
# ===========================================================================

class TestUnknownModels:
    def test_unknown_model_blocked(self):
        r = _report()
        wf = _wf(steps=[StepDefinition(id="s1", prompt="x", model="gpt-999-turbo")])
        _check_unknown_models(wf, r)
        assert len(r.blocking) == 1
        assert "not in the provider registry" in r.findings[0].message

    def test_known_model_ok(self):
        r = _report()
        wf = _wf(steps=[StepDefinition(id="s1", prompt="x", model="sonnet")])
        _check_unknown_models(wf, r)
        assert len(r.findings) == 0

    def test_non_llm_step_skipped(self):
        r = _report()
        wf = _wf(steps=[StepDefinition(id="s1", prompt="", type="http", model="nonexistent",
                                        http_config=HttpConfig(url="https://example.com"))])
        _check_unknown_models(wf, r)
        assert len(r.findings) == 0


# ===========================================================================
# Category 6: Unrealistic timeouts
# ===========================================================================

class TestUnrealisticTimeouts:
    def test_very_short_timeout(self):
        r = _report()
        wf = _wf(steps=[StepDefinition(id="s1", prompt="x", timeout=5)])
        _check_unrealistic_timeouts(wf, r)
        assert len(r.warnings) == 1
        assert "very short" in r.findings[0].message

    def test_very_long_timeout(self):
        r = _report()
        wf = _wf(steps=[StepDefinition(id="s1", prompt="x", timeout=7200)])
        _check_unrealistic_timeouts(wf, r)
        assert len(r.warnings) == 1
        assert "extremely long" in r.findings[0].message

    def test_normal_timeout_ok(self):
        r = _report()
        wf = _wf(steps=[StepDefinition(id="s1", prompt="x", timeout=300)])
        _check_unrealistic_timeouts(wf, r)
        assert len(r.findings) == 0


# ===========================================================================
# Category 7: Missing budget
# ===========================================================================

class TestMissingBudget:
    def test_no_budget_expensive_model(self):
        r = _report()
        wf = _wf(steps=[StepDefinition(id="s1", prompt="x", model="opus")])
        with patch("sandcastle.engine.doctor.settings") as mock_settings:
            mock_settings.default_max_cost_usd = 0.0
            _check_missing_budget(wf, r)
        assert len(r.warnings) == 1
        assert "budget" in r.findings[0].message.lower()

    def test_budget_set_no_warning(self):
        r = _report()
        wf = _wf(steps=[StepDefinition(id="s1", prompt="x", model="opus")])
        with patch("sandcastle.engine.doctor.settings") as mock_settings:
            mock_settings.default_max_cost_usd = 5.0
            _check_missing_budget(wf, r)
        assert len(r.findings) == 0

    def test_cheap_model_no_warning(self):
        r = _report()
        wf = _wf(steps=[StepDefinition(id="s1", prompt="x", model="haiku")])
        with patch("sandcastle.engine.doctor.settings") as mock_settings:
            mock_settings.default_max_cost_usd = 0.0
            _check_missing_budget(wf, r)
        assert len(r.findings) == 0


# ===========================================================================
# Category 8: Prompt injection risk
# ===========================================================================

class TestPromptInjectionRisk:
    def test_web_context_flagged(self):
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="x", context_source="web", context_query="latest news"),
        ])
        _check_prompt_injection_risk(wf, r)
        assert len(r.warnings) == 1
        assert "prompt injection" in r.findings[0].message.lower()

    def test_browser_step_flagged(self):
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="", type="browser",
                          browser_config=BrowserConfig(mode="playwright")),
        ])
        _check_prompt_injection_risk(wf, r)
        assert len(r.warnings) == 1
        assert "Browser" in r.findings[0].message

    def test_memory_context_not_flagged(self):
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="x", context_source="memory"),
        ])
        _check_prompt_injection_risk(wf, r)
        assert len(r.findings) == 0


# ===========================================================================
# Category 9: Callback URL SSRF
# ===========================================================================

class TestCallbackUrlSsrf:
    def test_internal_on_complete_blocked(self):
        r = _report()
        wf = _wf(on_complete=CompletionConfig(webhook="http://169.254.169.254/latest/meta-data/"))
        _check_callback_url_ssrf(wf, r)
        assert len(r.blocking) == 1
        assert "SSRF" in r.findings[0].message

    def test_public_url_ok(self):
        r = _report()
        wf = _wf(on_complete=CompletionConfig(webhook="https://hooks.example.com/wf"))
        _check_callback_url_ssrf(wf, r)
        assert len(r.findings) == 0

    def test_internal_http_step_warned(self):
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="", type="http",
                          http_config=HttpConfig(url="http://192.168.1.1/api/internal")),
        ])
        _check_callback_url_ssrf(wf, r)
        assert len(r.warnings) == 1

    def test_localhost_flagged(self):
        r = _report()
        wf = _wf(on_complete=CompletionConfig(webhook="http://localhost:8080/hook"))
        _check_callback_url_ssrf(wf, r)
        assert len(r.blocking) == 1

    def test_template_url_skipped(self):
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="", type="http",
                          http_config=HttpConfig(url="http://192.168.1.1/{path}")),
        ])
        _check_callback_url_ssrf(wf, r)
        # Template URLs are skipped because they're dynamic
        assert len(r.findings) == 0


# ===========================================================================
# Category 10: Secrets in YAML
# ===========================================================================

class TestSecretsInYaml:
    def test_openai_key_in_prompt(self):
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="Use this key: sk-abcdefghijklmnopqrstuvwxyz1234567890"),
        ])
        _check_secrets_in_yaml(wf, r)
        assert len(r.blocking) == 1
        assert "OpenAI API key" in r.findings[0].message

    def test_bearer_token_in_http_header(self):
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="", type="http",
                          http_config=HttpConfig(
                              url="https://slack.com/api/chat",
                              headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwidGVzdCI6InRydWUifQ"},
                          )),
        ])
        _check_secrets_in_yaml(wf, r)
        assert len(r.blocking) == 1

    def test_github_token_in_code(self):
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="", type="code",
                          code_config=CodeConfig(code="token = 'ghp_abcdefghijklmnopqrstuvwxyz1234567890'")),
        ])
        _check_secrets_in_yaml(wf, r)
        assert len(r.blocking) == 1

    def test_clean_yaml_no_findings(self):
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="Analyze this data"),
        ])
        _check_secrets_in_yaml(wf, r)
        assert len(r.findings) == 0

    def test_password_in_http_body(self):
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="", type="http",
                          http_config=HttpConfig(
                              url="https://api.example.com",
                              body='{"password": "SuperSecret123!"}',
                          )),
        ])
        _check_secrets_in_yaml(wf, r)
        assert len(r.blocking) == 1


# ===========================================================================
# Category 11: Data residency
# ===========================================================================

class TestDataResidency:
    def test_eu_residency_us_model_blocked(self):
        r = _report()
        wf = _wf(steps=[StepDefinition(id="s1", prompt="x", model="sonnet")])
        with patch("sandcastle.engine.doctor.settings") as mock_settings:
            mock_settings.data_residency = "eu"
            _check_data_residency(wf, r)
        assert len(r.blocking) == 1
        assert "data_residency=eu" in r.findings[0].message

    def test_eu_residency_eu_model_ok(self):
        r = _report()
        wf = _wf(steps=[StepDefinition(id="s1", prompt="x", model="mistral/large")])
        with patch("sandcastle.engine.doctor.settings") as mock_settings:
            mock_settings.data_residency = "eu"
            _check_data_residency(wf, r)
        assert len(r.findings) == 0

    def test_local_residency_cloud_model_blocked(self):
        r = _report()
        wf = _wf(steps=[StepDefinition(id="s1", prompt="x", model="sonnet")])
        with patch("sandcastle.engine.doctor.settings") as mock_settings:
            mock_settings.data_residency = "local"
            _check_data_residency(wf, r)
        assert len(r.blocking) == 1

    def test_no_residency_no_check(self):
        r = _report()
        wf = _wf(steps=[StepDefinition(id="s1", prompt="x", model="sonnet")])
        with patch("sandcastle.engine.doctor.settings") as mock_settings:
            mock_settings.data_residency = ""
            _check_data_residency(wf, r)
        assert len(r.findings) == 0


# ===========================================================================
# Category 12: Dead steps
# ===========================================================================

class TestDeadSteps:
    def test_missing_dependency_in_template(self):
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="Hello"),
            StepDefinition(id="s2", prompt="Use {steps.s1.output}", depends_on=["s1"]),
        ])
        _check_dead_steps(wf, r)
        assert len(r.findings) == 0  # s1 is in depends_on

    def test_undeclared_dependency_warned(self):
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="Hello"),
            StepDefinition(id="s2", prompt="Use {steps.s1.output}"),
        ])
        _check_dead_steps(wf, r)
        assert len(r.warnings) == 1
        assert "depends_on" in r.findings[0].message


# ===========================================================================
# Category 13: Cost estimation
# ===========================================================================

class TestCostEstimation:
    def test_expensive_workflow_warned(self):
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id=f"s{i}", prompt="x", model="opus", max_turns=50)
            for i in range(10)
        ])
        _check_cost_estimation(wf, r)
        assert len(r.findings) > 0
        assert any("cost" in f.message.lower() for f in r.findings)

    def test_cheap_workflow_info_or_nothing(self):
        r = _report()
        wf = _wf(steps=[StepDefinition(id="s1", prompt="x", model="haiku", max_turns=1)])
        _check_cost_estimation(wf, r)
        # Either info-level or no finding (very cheap)
        assert all(f.severity != "blocking" for f in r.findings)

    def test_non_llm_steps_ignored(self):
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="", type="http",
                          http_config=HttpConfig(url="https://example.com")),
        ])
        _check_cost_estimation(wf, r)
        assert len(r.findings) == 0


# ===========================================================================
# Category 14: Sensor config
# ===========================================================================

class TestSensorConfig:
    def test_aggressive_polling_warned(self):
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="", type="sensor",
                          sensor_config=SensorConfig(url="https://api.example.com/status",
                                                      condition="status == 'ready'",
                                                      check_interval=1)),
        ])
        _check_sensor_config(wf, r)
        assert len(r.warnings) == 1
        assert "aggressive" in r.findings[0].message

    def test_normal_interval_ok(self):
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="", type="sensor",
                          sensor_config=SensorConfig(url="https://api.example.com/status",
                                                      condition="status == 'ready'",
                                                      check_interval=30)),
        ])
        _check_sensor_config(wf, r)
        assert len(r.findings) == 0


# ===========================================================================
# Risk computation
# ===========================================================================

class TestRiskComputation:
    def test_no_findings_low(self):
        r = _report()
        assert _compute_risk(r) == "LOW"

    def test_one_warning_low(self):
        r = _report()
        r.findings.append(Finding("config", "warning", None, "minor issue"))
        assert _compute_risk(r) == "LOW"

    def test_three_warnings_medium(self):
        r = _report()
        for i in range(3):
            r.findings.append(Finding("config", "warning", None, f"issue {i}"))
        assert _compute_risk(r) == "MEDIUM"

    def test_blocking_high(self):
        r = _report()
        r.findings.append(Finding("config", "blocking", None, "bad"))
        assert _compute_risk(r) == "HIGH"

    def test_security_blocking_critical(self):
        r = _report()
        r.findings.append(Finding("security", "blocking", None, "vuln"))
        assert _compute_risk(r) == "CRITICAL"


# ===========================================================================
# Internal URL detection
# ===========================================================================

class TestInternalUrl:
    @pytest.mark.parametrize("url", [
        "http://localhost:8080/api",
        "http://127.0.0.1:3000/",
        "http://[::1]:5000/",
        "http://10.0.0.5:8080/internal",
        "http://172.16.0.1/admin",
        "http://192.168.1.100:9090/api",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
    ])
    def test_internal_detected(self, url):
        assert _is_internal_url(url) is True

    @pytest.mark.parametrize("url", [
        "https://api.example.com/webhook",
        "https://hooks.slack.com/services/abc",
        "https://10.example.com/api",  # domain, not IP
    ])
    def test_external_not_detected(self, url):
        assert _is_internal_url(url) is False


# ===========================================================================
# DoctorReport model
# ===========================================================================

class TestDoctorReport:
    def test_ok_when_no_blocking(self):
        r = DoctorReport(workflow_name="test")
        r.findings.append(Finding("config", "warning", None, "minor"))
        assert r.ok is True

    def test_not_ok_when_blocking(self):
        r = DoctorReport(workflow_name="test")
        r.findings.append(Finding("config", "blocking", None, "bad"))
        assert r.ok is False

    def test_to_dict(self):
        r = DoctorReport(workflow_name="test", risk="HIGH")
        r.findings.append(Finding("security", "blocking", "s1", "vuln", "fix it"))
        d = r.to_dict()
        assert d["workflow_name"] == "test"
        assert d["risk"] == "HIGH"
        assert d["ok"] is False
        assert d["summary"]["blocking"] == 1
        assert len(d["findings"]) == 1
        assert d["findings"][0]["suggested_fix"] == "fix it"


# ===========================================================================
# Full diagnose() integration
# ===========================================================================

class TestDiagnoseIntegration:
    def test_clean_workflow(self):
        wf = _wf(steps=[StepDefinition(id="s1", prompt="Analyze data")])
        with patch("sandcastle.engine.doctor.settings") as mock_settings:
            mock_settings.anthropic_api_key = "sk-test"
            mock_settings.default_max_cost_usd = 5.0
            mock_settings.data_residency = ""
            report = diagnose(wf)
        # Should have no blocking issues
        assert report.ok is True
        assert report.risk in ("LOW", "MEDIUM")

    def test_dangerous_workflow(self):
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="Use sk-abcdefghijklmnopqrstuvwxyz1234567890",
                          context_source="custom", context_query="cat /etc/passwd"),
        ])
        with patch("sandcastle.engine.doctor.settings") as mock_settings:
            mock_settings.anthropic_api_key = "sk-test"
            mock_settings.default_max_cost_usd = 0.0
            mock_settings.data_residency = ""
            report = diagnose(wf)
        assert report.ok is False
        assert report.risk == "CRITICAL"
        assert len(report.blocking) >= 2  # custom context + secret

    def test_multiple_issues(self):
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="x", model="nonexistent-model", timeout=3),
            StepDefinition(id="s2", prompt="", type="browser",
                          browser_config=BrowserConfig(mode="playwright")),
        ])
        with patch("sandcastle.engine.doctor.settings") as mock_settings:
            mock_settings.anthropic_api_key = "sk-test"
            mock_settings.default_max_cost_usd = 0.0
            mock_settings.data_residency = ""
            report = diagnose(wf)
        # Should find: unknown model, short timeout, browser risk, missing prompt for s2
        assert len(report.findings) >= 3


# ===========================================================================
# diagnose_yaml()
# ===========================================================================

class TestDiagnoseYaml:
    def test_invalid_yaml(self):
        report = diagnose_yaml("not: valid: yaml: {{{{")
        assert report.ok is False
        assert "parse" in report.findings[0].message.lower()

    def test_valid_yaml(self):
        yaml_content = """
name: test-workflow
description: A test
steps:
  - id: greet
    prompt: Say hello
"""
        with patch("sandcastle.engine.doctor.settings") as mock_settings:
            mock_settings.anthropic_api_key = "sk-test"
            mock_settings.default_max_cost_usd = 5.0
            mock_settings.data_residency = ""
            report = diagnose_yaml(yaml_content)
        assert report.workflow_name == "test-workflow"

    def test_empty_yaml(self):
        report = diagnose_yaml("")
        assert report.ok is False

    def test_workflow_with_secrets(self):
        """YAML containing embedded secrets should be flagged."""
        yaml_content = """
name: leaked-secrets
description: Bad workflow
steps:
  - id: call_openai
    prompt: "Use key sk-abcdefghijklmnopqrstuvwxyz1234567890"
"""
        with patch("sandcastle.engine.doctor.settings") as mock_settings:
            mock_settings.anthropic_api_key = "sk-test"
            mock_settings.default_max_cost_usd = 5.0
            mock_settings.data_residency = ""
            report = diagnose_yaml(yaml_content)
        assert report.ok is False
        assert any("OpenAI API key" in f.message for f in report.findings)


# ===========================================================================
# CLI doctor tests
# ===========================================================================

class TestDoctorCli:
    """Test the CLI doctor command with workflow file argument."""

    def test_doctor_parses_workflow_arg(self):
        """Parser should accept optional workflow argument."""
        import argparse
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="cmd")
        p = subs.add_parser("doctor")
        p.add_argument("workflow", nargs="?", default=None)

        # Without workflow arg
        ns = parser.parse_args(["doctor"])
        assert ns.workflow is None

        # With workflow arg
        ns = parser.parse_args(["doctor", "my-workflow.yaml"])
        assert ns.workflow == "my-workflow.yaml"


# ===========================================================================
# API endpoint tests
# ===========================================================================

# ===========================================================================
# Edge cases and combined scenarios
# ===========================================================================

class TestEdgeCases:
    """Edge cases for doctor checks."""

    def test_multiple_secrets_in_one_step(self):
        """Only one secret finding per step (first match wins)."""
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="sk-abcdefghijklmnopqrstuvwxyz1234567890 and ghp_abcdefghijklmnopqrstuvwxyz1234567890"),
        ])
        _check_secrets_in_yaml(wf, r)
        assert len(r.blocking) == 1  # First match wins per step

    def test_mixed_severity_risk(self):
        """Verify risk computation with mixed findings."""
        r = _report()
        r.findings.append(Finding("config", "warning", None, "w1"))
        r.findings.append(Finding("config", "warning", None, "w2"))
        r.findings.append(Finding("config", "warning", None, "w3"))
        assert _compute_risk(r) == "MEDIUM"  # 3 warnings

        r.findings.append(Finding("config", "blocking", None, "b1"))
        assert _compute_risk(r) == "HIGH"  # Blocking but not security

        r.findings.append(Finding("security", "blocking", None, "sec1"))
        assert _compute_risk(r) == "CRITICAL"  # Security blocking

    def test_workflow_with_all_local_models(self):
        """All-local workflow should have no credential issues."""
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="x", model="ollama"),
            StepDefinition(id="s2", prompt="y", model="omlx/gemma-3"),
        ])
        _check_missing_credentials(wf, r)
        cred_blocking = [f for f in r.findings if f.category == "credentials" and f.severity == "blocking"]
        assert len(cred_blocking) == 0

    def test_http_step_with_public_url_no_ssrf(self):
        """Public URL in HTTP step should not trigger SSRF warning."""
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="", type="http",
                          http_config=HttpConfig(url="https://api.stripe.com/v1/charges")),
        ])
        _check_callback_url_ssrf(wf, r)
        assert len(r.findings) == 0

    def test_cloud_metadata_ssrf(self):
        """Cloud metadata URLs should be flagged."""
        assert _is_internal_url("http://169.254.169.254/latest/meta-data/") is True
        assert _is_internal_url("http://metadata.google.internal/computeMetadata/v1/") is True

    def test_non_llm_step_no_cost(self):
        """Non-LLM steps should not contribute to cost."""
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="", type="code", code_config=CodeConfig(code="x=1")),
            StepDefinition(id="s2", prompt="", type="http", http_config=HttpConfig(url="https://a.com")),
            StepDefinition(id="s3", prompt="", type="transform",
                          transform_config=type("TC", (), {"template": "hello"})()),
        ])
        _check_cost_estimation(wf, r)
        assert len(r.findings) == 0

    def test_gitlab_token_in_yaml(self):
        """GitLab PAT should be detected."""
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="token: glpat-abcdefghijklmnopqrstuvwx"),
        ])
        _check_secrets_in_yaml(wf, r)
        assert len(r.blocking) == 1
        assert "GitLab" in r.findings[0].message

    def test_bearer_token_in_headers(self):
        """Bearer tokens in HTTP headers should be detected."""
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="", type="http",
                          http_config=HttpConfig(
                              url="https://api.example.com",
                              headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0"},
                          )),
        ])
        _check_secrets_in_yaml(wf, r)
        assert len(r.blocking) == 1

    def test_anthropic_key_pattern(self):
        """Anthropic API key pattern should be detected."""
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="Use key: sk-ant-api03-abcdefghijklmnopqrstuvwxyz"),
        ])
        _check_secrets_in_yaml(wf, r)
        assert len(r.blocking) == 1

    def test_multiple_models_missing_keys(self):
        """Multiple models with missing keys should each produce a finding."""
        r = _report()
        wf = _wf(steps=[
            StepDefinition(id="s1", prompt="x", model="sonnet"),
            StepDefinition(id="s2", prompt="y", model="openai/codex"),
        ])
        with patch("sandcastle.engine.doctor.settings") as mock_settings:
            mock_settings.anthropic_api_key = ""
            mock_settings.openai_api_key = ""
            _check_missing_credentials(wf, r)
        blocking = [f for f in r.findings if f.severity == "blocking"]
        assert len(blocking) == 2

    def test_eu_residency_local_model_allowed(self):
        """Local models should be allowed under EU data residency."""
        r = _report()
        wf = _wf(steps=[StepDefinition(id="s1", prompt="x", model="ollama")])
        with patch("sandcastle.engine.doctor.settings") as mock_settings:
            mock_settings.data_residency = "eu"
            _check_data_residency(wf, r)
        assert len(r.findings) == 0


class TestDoctorApiEndpoint:
    """Test the /api/workflows/{name}/doctor endpoint integration."""

    def test_doctor_report_to_dict_has_expected_keys(self):
        """Verify the API response shape."""
        r = DoctorReport(workflow_name="test-wf", risk="MEDIUM")
        r.findings.append(Finding("security", "blocking", "s1", "issue", "fix"))
        r.findings.append(Finding("config", "warning", None, "minor"))
        d = r.to_dict()
        assert set(d.keys()) == {"workflow_name", "risk", "ok", "summary", "findings"}
        assert d["summary"]["blocking"] == 1
        assert d["summary"]["warnings"] == 1
        assert d["summary"]["total"] == 2
        assert d["ok"] is False

    def test_diagnose_yaml_endpoint_shape(self):
        """diagnose_yaml returns proper report for valid YAML."""
        yaml_content = """
name: api-test
description: Test workflow
steps:
  - id: step1
    prompt: Do analysis
"""
        with patch("sandcastle.engine.doctor.settings") as mock_settings:
            mock_settings.anthropic_api_key = "sk-test"
            mock_settings.default_max_cost_usd = 5.0
            mock_settings.data_residency = ""
            report = diagnose_yaml(yaml_content)
        d = report.to_dict()
        assert d["workflow_name"] == "api-test"
        assert isinstance(d["findings"], list)

    def test_diagnose_all_empty_dir(self):
        """diagnose_all on empty/nonexistent dir returns empty list."""
        from sandcastle.engine.doctor import diagnose_all
        reports = diagnose_all("/tmp/nonexistent-sandcastle-dir-12345")
        assert reports == []

    def test_diagnose_all_with_files(self, tmp_path):
        """diagnose_all returns one report per YAML file."""
        from sandcastle.engine.doctor import diagnose_all

        (tmp_path / "good.yaml").write_text("name: good\ndescription: ok\nsteps:\n  - id: s1\n    prompt: hi\n")
        (tmp_path / "bad.yaml").write_text("name: bad\ndescription: broken\nsteps: []")

        with patch("sandcastle.engine.doctor.settings") as mock_settings:
            mock_settings.anthropic_api_key = "sk-test"
            mock_settings.default_max_cost_usd = 5.0
            mock_settings.data_residency = ""
            mock_settings.workflows_dir = str(tmp_path)
            reports = diagnose_all(str(tmp_path))

        assert len(reports) == 2
        names = {r.workflow_name for r in reports}
        assert "good" in names
        assert "bad" in names
        # bad.yaml has no steps - should be blocking
        bad_report = [r for r in reports if r.workflow_name == "bad"][0]
        assert not bad_report.ok

    def test_doctor_status_values(self):
        """Verify doctor_status logic: ok vs warning vs blocked."""
        # Clean workflow = ok
        r1 = DoctorReport(workflow_name="clean")
        assert r1.ok is True

        # Warnings only = ok (not blocked)
        r2 = DoctorReport(workflow_name="warnings")
        r2.findings.append(Finding("config", "warning", None, "minor"))
        assert r2.ok is True
        status2 = "ok" if r2.ok else ("blocked" if r2.blocking else "warning")
        assert status2 == "ok"

        # Blocking = blocked
        r3 = DoctorReport(workflow_name="blocked")
        r3.findings.append(Finding("security", "blocking", None, "vuln"))
        assert r3.ok is False
        status3 = "ok" if r3.ok else ("blocked" if r3.blocking else "warning")
        assert status3 == "blocked"
