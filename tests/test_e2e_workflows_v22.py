"""
E2E WORKFLOW YAML SCENARIOS FOR SANDCASTLE v0.22
=================================================
Tests realistic workflow YAML scenarios exercising Composio integration,
all 17 step types, cost estimation, DAG planning, and validation edge cases.

Run:
    cd /Users/gizmax/Documents/Sandcastle
    .venv/bin/python -m pytest tests/test_e2e_workflows_v22.py -v --tb=short
"""

from __future__ import annotations

import pytest

from sandcastle.engine.dag import (
    ExecutionPlan,
    StepDefinition,
    WorkflowDefinition,
    build_plan,
    parse_yaml_string,
    validate,
    _extract_implicit_deps,
    _extract_step_refs,
    VALID_STEP_TYPES,
)


# ======================================================================
# YAML FIXTURES
# ======================================================================

LEAD_ENRICHMENT_COMPOSIO = """\
name: lead-enrichment-composio
description: Multi-step lead enrichment with Composio
steps:
  - id: fetch-lead
    type: http
    http_config:
      url: "https://api.clearbit.com/v2/people/find?email={input.email}"
  - id: enrich-via-composio
    type: composio
    composio_config:
      action: hubspot_create_contact
      params:
        email: "{steps.fetch-lead.output.email}"
        firstName: "{steps.fetch-lead.output.name.givenName}"
        company: "{steps.fetch-lead.output.employment.name}"
  - id: notify
    type: notify
    depends_on: [enrich-via-composio]
    notify_config:
      service: slack
      channel: "#leads"
      message: "New lead enriched: {steps.enrich-via-composio.output}"
"""

BULK_EMAIL_COMPOSIO = """\
name: bulk-email
description: Fan-out email sending via Composio
steps:
  - id: send-emails
    type: composio
    parallel_over: "{input.recipients}"
    composio_config:
      action: gmail_send_email
      params:
        to: "{_item.email}"
        subject: "Hello {_item.name}"
"""

ALL_17_STEP_TYPES = """\
name: all-step-types
description: Workflow exercising all step types
steps:
  - id: std-step
    type: standard
    prompt: "Analyze the input data"

  - id: llm-step
    type: llm
    prompt: "Summarize {steps.std-step.output}"
    llm_config:
      system_prompt: "You are a summarizer"

  - id: http-step
    type: http
    http_config:
      url: "https://api.example.com/data"
      method: GET

  - id: code-step
    type: code
    code_config:
      code: "result = 2 + 2"
      language: python

  - id: condition-step
    type: condition
    depends_on: [code-step]
    condition_config:
      expression: "{steps.code-step.output} == 4"
      then: [then-branch]
      else: [else-branch]

  - id: then-branch
    type: standard
    prompt: "Handle true branch"
    depends_on: [condition-step]

  - id: else-branch
    type: standard
    prompt: "Handle false branch"
    depends_on: [condition-step]

  - id: classify-step
    type: classify
    classify_config:
      categories: [positive, negative, neutral]
      input: "{steps.std-step.output}"
      model: haiku
      branches:
        positive: [then-branch]
        negative: [else-branch]

  - id: loop-step
    type: loop
    loop_config:
      over: "{input.items}"
      step_ids: [std-step]
      max_iterations: 50

  - id: race-step
    type: race
    race_config:
      branches:
        - [http-step]
        - [code-step]
      validator: "len(result) > 0"

  - id: sensor-step
    type: sensor
    sensor_config:
      url: "https://api.example.com/status"
      check_interval: 60
      timeout: 3600
      condition: "response.status == 'ready'"

  - id: gate-step
    type: gate
    depends_on: [std-step]
    gate_config:
      strategies:
        - type: llm_eval
          config:
            model: haiku
            threshold: 0.8

  - id: transform-step
    type: transform
    depends_on: [std-step]
    transform_config:
      template: "Result: {{ steps.std-step.output }}"

  - id: notify-step
    type: notify
    depends_on: [transform-step]
    notify_config:
      service: slack
      channel: "#results"
      message: "Transform complete: {steps.transform-step.output}"

  - id: delegate-step
    type: delegate
    delegate_config:
      workflow: child-workflow
      task_description: "Process {steps.std-step.output}"
      timeout: 1800

  - id: browser-step
    type: browser
    browser_config:
      mode: playwright
      start_url: "https://example.com"
      timeout_seconds: 60

  - id: sub-workflow-step
    type: sub_workflow
    sub_workflow:
      workflow: sub-process.yaml
      input_mapping:
        data: "{steps.std-step.output}"

  - id: approval-step
    type: approval
    depends_on: [browser-step]
    approval_config:
      message: "Please review browser output"
      timeout_hours: 24

  - id: composio-step
    type: composio
    depends_on: [std-step]
    composio_config:
      action: github_create_issue
      params:
        title: "{steps.std-step.output}"
        body: "Auto-generated issue"

  - id: openclaw-step
    type: openclaw
    depends_on: [std-step]
    openclaw_config:
      gateway_url: "https://openclaw.example.com/v1"
      message: "Summarize {steps.std-step.output}"
      timeout_seconds: 120

  - id: parse-step
    type: parse
    depends_on: [http-step]
    parse_config:
      output: json

  - id: report-step
    type: report
    prompt: "Generate a summary report of all findings from {steps.std-step.output}"
    depends_on: [std-step]
    report_config:
      theme: professional
      title: "Analysis Report"
      format: pdf

  - id: managed-agent-step
    type: managed-agent
    depends_on: [std-step]
    managed_agent_config:
      agent_template: researcher
      message: "Research {steps.std-step.output}"

  - id: agent-step
    type: agent
    depends_on: [std-step]
    agent_config:
      template: researcher
      task: "Investigate {steps.std-step.output}"

  - id: computer-use-step
    type: computer-use
    depends_on: [std-step]
    computer_use_config:
      display_width_px: 1280
      display_height_px: 800
      message: "Open the report and take a screenshot"

  - id: tool-step
    type: tool
    depends_on: [std-step]
    tool_config:
      tool: openai
      function: chat_completion
      arguments:
        - "Summarize {steps.std-step.output}"

  - id: trajectory-replay-step
    type: trajectory-replay
    depends_on: [std-step]
    trajectory_replay_config:
      golden_run_id: "00000000-0000-0000-0000-000000000000"
      fail_below_score: 0.7

  - id: acp-step
    type: acp
    depends_on: [std-step]
    acp_config:
      agent: claude
      cwd: /srv/checkouts/repo
      message: "Implement {steps.std-step.output}"

  - id: accept-step
    type: accept
    accept_config:
      target: std-step
      checks:
        - type: not_empty
      judges:
        - model: haiku
          rubric: "Did the step meet the goal?"
"""

COMPOSIO_TEMPLATE_ACTION = """\
name: composio-template-action
description: Composio with templated action name
steps:
  - id: dynamic-composio
    type: composio
    composio_config:
      action: "github_{input.action_type}"
      params:
        repo: "{input.repo}"
"""

COMPOSIO_NESTED_PARAMS = """\
name: composio-nested-params
description: Composio with deeply nested params
steps:
  - id: nested-composio
    type: composio
    composio_config:
      action: notion_create_page
      params:
        parent:
          database_id: "abc123"
        properties:
          Title:
            title:
              - text:
                  content: "Hello World"
          Status:
            select:
              name: "In Progress"
        children:
          - object: "block"
            type: "paragraph"
            paragraph:
              rich_text:
                - text:
                    content: "Body text here"
"""

COMPOSIO_IMPLICIT_DEPS = """\
name: composio-implicit-deps
description: Composio step with implicit deps via template params
steps:
  - id: fetch-data
    type: http
    http_config:
      url: "https://api.example.com/users"
  - id: process
    type: llm
    prompt: "Analyze {steps.fetch-data.output}"
  - id: create-ticket
    type: composio
    composio_config:
      action: jira_create_issue
      params:
        summary: "{steps.process.output}"
        description: "{steps.fetch-data.output}"
"""

COMPOSIO_RETRY = """\
name: composio-retry
description: Composio step with retry config
steps:
  - id: retry-composio
    type: composio
    retry:
      max_attempts: 3
      backoff: exponential
    composio_config:
      action: salesforce_create_lead
      params:
        name: "John Doe"
        email: "john@example.com"
"""

DAG_COMPOSIO_AFTER_LLM = """\
name: dag-composio-after-llm
description: Composio step depending on LLM via template
steps:
  - id: analyze
    type: llm
    prompt: "Analyze the input data"
  - id: act-on-analysis
    type: composio
    composio_config:
      action: slack_send_message
      params:
        text: "{steps.analyze.output}"
        channel: "#results"
"""

DAG_INDEPENDENT_COMPOSIO = """\
name: dag-independent-composio
description: Two independent composio steps
steps:
  - id: composio-a
    type: composio
    composio_config:
      action: github_create_issue
      params:
        title: "Issue A"
  - id: composio-b
    type: composio
    composio_config:
      action: slack_send_message
      params:
        text: "Hello"
"""

DAG_CHAIN_HTTP_COMPOSIO_NOTIFY = """\
name: dag-chain
description: Chain http -> composio -> notify (3 stages)
steps:
  - id: fetch
    type: http
    http_config:
      url: "https://api.example.com/data"
  - id: process-composio
    type: composio
    composio_config:
      action: hubspot_create_contact
      params:
        email: "{steps.fetch.output.email}"
  - id: send-notification
    type: notify
    depends_on: [process-composio]
    notify_config:
      service: slack
      channel: "#updates"
      message: "Processed: {steps.process-composio.output}"
"""


# ======================================================================
# TEST 1: Composio in multi-step workflow
# ======================================================================

class TestComposioMultiStep:
    """Test 1: Lead enrichment workflow with Composio."""

    def test_parse_succeeds(self):
        wf = parse_yaml_string(LEAD_ENRICHMENT_COMPOSIO)
        assert wf.name == "lead-enrichment-composio"
        assert len(wf.steps) == 3

    def test_step_types_correct(self):
        wf = parse_yaml_string(LEAD_ENRICHMENT_COMPOSIO)
        types = {s.id: s.type for s in wf.steps}
        assert types["fetch-lead"] == "http"
        assert types["enrich-via-composio"] == "composio"
        assert types["notify"] == "notify"

    def test_composio_config_parsed(self):
        wf = parse_yaml_string(LEAD_ENRICHMENT_COMPOSIO)
        composio_step = wf.get_step("enrich-via-composio")
        assert composio_step.composio_config is not None
        assert composio_step.composio_config.action == "hubspot_create_contact"
        assert "email" in composio_step.composio_config.params
        assert "firstName" in composio_step.composio_config.params
        assert "company" in composio_step.composio_config.params

    def test_validate_passes(self):
        wf = parse_yaml_string(LEAD_ENRICHMENT_COMPOSIO)
        errors = validate(wf)
        assert errors == [], f"Unexpected validation errors: {errors}"

    def test_implicit_deps_enrich_depends_on_fetch(self):
        """enrich-via-composio params reference {steps.fetch-lead.output} so
        it should have an implicit dependency on fetch-lead."""
        wf = parse_yaml_string(LEAD_ENRICHMENT_COMPOSIO)
        composio_step = wf.get_step("enrich-via-composio")
        valid_ids = {s.id for s in wf.steps}
        implicit = _extract_implicit_deps(composio_step, valid_ids)
        assert "fetch-lead" in implicit

    def test_implicit_deps_notify_depends_on_composio(self):
        """notify message references {steps.enrich-via-composio.output}."""
        wf = parse_yaml_string(LEAD_ENRICHMENT_COMPOSIO)
        notify_step = wf.get_step("notify")
        valid_ids = {s.id for s in wf.steps}
        implicit = _extract_implicit_deps(notify_step, valid_ids)
        # notify has explicit depends_on [enrich-via-composio], so implicit
        # should NOT include it (already explicit)
        assert "enrich-via-composio" not in implicit

    def test_build_plan_orders_correctly(self):
        """fetch-lead first, then enrich-via-composio, then notify."""
        wf = parse_yaml_string(LEAD_ENRICHMENT_COMPOSIO)
        plan = build_plan(wf)
        assert len(plan.stages) >= 2

        # Flatten to get order
        flat = [sid for stage in plan.stages for sid in stage]
        assert flat.index("fetch-lead") < flat.index("enrich-via-composio")
        assert flat.index("enrich-via-composio") < flat.index("notify")

    def test_build_plan_three_stages(self):
        """Each step depends on the previous, so should be 3 stages."""
        wf = parse_yaml_string(LEAD_ENRICHMENT_COMPOSIO)
        plan = build_plan(wf)
        assert len(plan.stages) == 3
        assert plan.stages[0] == ["fetch-lead"]
        assert plan.stages[1] == ["enrich-via-composio"]
        assert plan.stages[2] == ["notify"]

    def test_http_config_parsed(self):
        wf = parse_yaml_string(LEAD_ENRICHMENT_COMPOSIO)
        http_step = wf.get_step("fetch-lead")
        assert http_step.http_config is not None
        assert "clearbit.com" in http_step.http_config.url

    def test_notify_config_parsed(self):
        wf = parse_yaml_string(LEAD_ENRICHMENT_COMPOSIO)
        notify_step = wf.get_step("notify")
        assert notify_step.notify_config is not None
        assert notify_step.notify_config.service == "slack"
        assert notify_step.notify_config.channel == "#leads"


# ======================================================================
# TEST 2: Composio with parallel_over (fan-out)
# ======================================================================

class TestComposioParallelOver:
    """Test 2: Bulk email with Composio parallel_over."""

    def test_parse_succeeds(self):
        wf = parse_yaml_string(BULK_EMAIL_COMPOSIO)
        assert wf.name == "bulk-email"
        assert len(wf.steps) == 1

    def test_parallel_over_is_set(self):
        wf = parse_yaml_string(BULK_EMAIL_COMPOSIO)
        step = wf.steps[0]
        assert step.parallel_over == "{input.recipients}"

    def test_composio_config_parsed(self):
        wf = parse_yaml_string(BULK_EMAIL_COMPOSIO)
        step = wf.steps[0]
        assert step.composio_config is not None
        assert step.composio_config.action == "gmail_send_email"
        assert step.composio_config.params["to"] == "{_item.email}"
        assert step.composio_config.params["subject"] == "Hello {_item.name}"

    def test_validate_passes(self):
        wf = parse_yaml_string(BULK_EMAIL_COMPOSIO)
        errors = validate(wf)
        assert errors == [], f"Unexpected validation errors: {errors}"

    def test_step_type_is_composio(self):
        wf = parse_yaml_string(BULK_EMAIL_COMPOSIO)
        assert wf.steps[0].type == "composio"

    def test_build_plan_single_stage(self):
        wf = parse_yaml_string(BULK_EMAIL_COMPOSIO)
        plan = build_plan(wf)
        assert len(plan.stages) == 1
        assert plan.stages[0] == ["send-emails"]


# ======================================================================
# TEST 3: Mixed step types workflow - ALL 17 step types
# ======================================================================

class TestAll17StepTypes:
    """Test 3: Workflow with all 17 step types."""

    def test_parse_succeeds(self):
        wf = parse_yaml_string(ALL_17_STEP_TYPES)
        assert wf.name == "all-step-types"

    def test_all_17_types_present(self):
        wf = parse_yaml_string(ALL_17_STEP_TYPES)
        types_in_wf = {s.type for s in wf.steps}
        assert types_in_wf == VALID_STEP_TYPES, (
            f"Missing types: {VALID_STEP_TYPES - types_in_wf}, "
            f"Extra types: {types_in_wf - VALID_STEP_TYPES}"
        )

    def test_step_count(self):
        """We have more steps than types because condition has then/else branches."""
        wf = parse_yaml_string(ALL_17_STEP_TYPES)
        # 17 types + 2 branch steps (then-branch, else-branch used by condition)
        # but then-branch and else-branch are standard type, so total > 17
        assert len(wf.steps) >= 17

    def test_validate_passes_or_expected_errors(self):
        """Validation should pass - all configs are properly set."""
        wf = parse_yaml_string(ALL_17_STEP_TYPES)
        errors = validate(wf)
        # Filter out any errors about unknown tools (we don't have real tool registry loaded)
        # All step-level validations should pass
        step_errors = [
            e for e in errors
            if "Unknown tool" not in e
        ]
        assert step_errors == [], f"Unexpected validation errors: {step_errors}"

    def test_build_plan_produces_valid_order(self):
        wf = parse_yaml_string(ALL_17_STEP_TYPES)
        plan = build_plan(wf)
        assert len(plan.stages) >= 1
        # All step IDs should be in the plan
        all_planned = {sid for stage in plan.stages for sid in stage}
        all_defined = {s.id for s in wf.steps}
        assert all_planned == all_defined

    def test_composio_step_in_workflow(self):
        wf = parse_yaml_string(ALL_17_STEP_TYPES)
        composio_step = wf.get_step("composio-step")
        assert composio_step.type == "composio"
        assert composio_step.composio_config is not None
        assert composio_step.composio_config.action == "github_create_issue"

    def test_browser_step_config(self):
        wf = parse_yaml_string(ALL_17_STEP_TYPES)
        browser_step = wf.get_step("browser-step")
        assert browser_step.type == "browser"
        assert browser_step.browser_config is not None
        assert browser_step.browser_config.mode == "playwright"

    def test_approval_step_config(self):
        wf = parse_yaml_string(ALL_17_STEP_TYPES)
        approval_step = wf.get_step("approval-step")
        assert approval_step.type == "approval"
        assert approval_step.approval_config is not None
        assert approval_step.approval_config.message == "Please review browser output"

    def test_delegate_step_config(self):
        wf = parse_yaml_string(ALL_17_STEP_TYPES)
        delegate_step = wf.get_step("delegate-step")
        assert delegate_step.type == "delegate"
        assert delegate_step.delegate_config is not None
        assert delegate_step.delegate_config.workflow == "child-workflow"

    def test_sub_workflow_step_config(self):
        wf = parse_yaml_string(ALL_17_STEP_TYPES)
        sw_step = wf.get_step("sub-workflow-step")
        assert sw_step.type == "sub_workflow"
        assert sw_step.sub_workflow is not None
        assert sw_step.sub_workflow.workflow == "sub-process.yaml"

    def test_sensor_step_config(self):
        wf = parse_yaml_string(ALL_17_STEP_TYPES)
        sensor_step = wf.get_step("sensor-step")
        assert sensor_step.type == "sensor"
        assert sensor_step.sensor_config is not None
        assert sensor_step.sensor_config.check_interval == 60

    def test_gate_step_config(self):
        wf = parse_yaml_string(ALL_17_STEP_TYPES)
        gate_step = wf.get_step("gate-step")
        assert gate_step.type == "gate"
        assert gate_step.gate_config is not None
        assert len(gate_step.gate_config.strategies) == 1

    def test_race_step_config(self):
        wf = parse_yaml_string(ALL_17_STEP_TYPES)
        race_step = wf.get_step("race-step")
        assert race_step.type == "race"
        assert race_step.race_config is not None
        assert len(race_step.race_config.branches) == 2


# ======================================================================
# TEST 4: Cost estimation for realistic workflows
# ======================================================================

class TestCostEstimation:
    """Test 4: Cost estimation via /api/runs/estimate."""

    def _estimate(self, yaml_content: str) -> dict:
        """Call the estimate endpoint and return parsed response."""
        from fastapi.testclient import TestClient
        from sandcastle.main import app

        client = TestClient(app)
        response = client.post(
            "/api/runs/estimate",
            json={"yaml_content": yaml_content},
        )
        assert response.status_code == 200, f"Estimate failed: {response.text}"
        return response.json()

    def test_lead_enrichment_estimate(self):
        data = self._estimate(LEAD_ENRICHMENT_COMPOSIO)
        assert data["data"]["workflow_name"] == "lead-enrichment-composio"
        steps = data["data"]["steps"]
        assert len(steps) == 3

    def test_composio_step_zero_cost(self):
        data = self._estimate(LEAD_ENRICHMENT_COMPOSIO)
        steps = {s["step_id"]: s for s in data["data"]["steps"]}
        assert steps["enrich-via-composio"]["estimated_cost_usd"] == 0.0
        assert steps["enrich-via-composio"]["type"] == "composio"

    def test_http_step_zero_cost(self):
        data = self._estimate(LEAD_ENRICHMENT_COMPOSIO)
        steps = {s["step_id"]: s for s in data["data"]["steps"]}
        assert steps["fetch-lead"]["estimated_cost_usd"] == 0.0
        assert steps["fetch-lead"]["type"] == "http"

    def test_notify_step_zero_cost(self):
        data = self._estimate(LEAD_ENRICHMENT_COMPOSIO)
        steps = {s["step_id"]: s for s in data["data"]["steps"]}
        assert steps["notify"]["estimated_cost_usd"] == 0.0
        assert steps["notify"]["type"] == "notify"

    def test_total_cost_zero_for_non_llm_workflow(self):
        """Lead enrichment has only non-LLM steps, total should be 0."""
        data = self._estimate(LEAD_ENRICHMENT_COMPOSIO)
        assert data["data"]["total_estimated_cost_usd"] == 0.0

    def test_llm_step_has_nonzero_cost(self):
        """A workflow with an LLM step should have non-zero cost."""
        yaml_with_llm = """\
name: llm-workflow
description: Workflow with LLM step
steps:
  - id: analyze
    type: standard
    prompt: "Analyze the data"
    model: sonnet
    max_turns: 3
  - id: notify-result
    type: composio
    composio_config:
      action: slack_send_message
      params:
        text: "{steps.analyze.output}"
"""
        data = self._estimate(yaml_with_llm)
        steps = {s["step_id"]: s for s in data["data"]["steps"]}
        assert steps["analyze"]["estimated_cost_usd"] > 0.0
        assert steps["notify-result"]["estimated_cost_usd"] == 0.0
        assert data["data"]["total_estimated_cost_usd"] > 0.0

    def test_composio_parallel_over_still_zero_cost(self):
        """Even with parallel_over, composio step should be $0."""
        data = self._estimate(BULK_EMAIL_COMPOSIO)
        steps = {s["step_id"]: s for s in data["data"]["steps"]}
        assert steps["send-emails"]["estimated_cost_usd"] == 0.0

    def test_estimate_includes_disclaimer(self):
        data = self._estimate(LEAD_ENRICHMENT_COMPOSIO)
        assert "disclaimer" in data["data"]


# ======================================================================
# TEST 5: Composio validation edge cases
# ======================================================================

class TestComposioValidationEdgeCases:
    """Test 5: Edge cases for composio step validation."""

    def test_template_vars_in_action(self):
        """composio_config.action can contain template vars."""
        wf = parse_yaml_string(COMPOSIO_TEMPLATE_ACTION)
        step = wf.steps[0]
        assert step.composio_config is not None
        assert "{input.action_type}" in step.composio_config.action

    def test_template_action_validates(self):
        """Action with template vars should still pass validation
        (non-empty after template resolution is a runtime concern)."""
        wf = parse_yaml_string(COMPOSIO_TEMPLATE_ACTION)
        errors = validate(wf)
        assert errors == [], f"Unexpected errors: {errors}"

    def test_deeply_nested_params(self):
        """composio_config.params can be deeply nested JSON."""
        wf = parse_yaml_string(COMPOSIO_NESTED_PARAMS)
        step = wf.steps[0]
        assert step.composio_config is not None
        assert step.composio_config.action == "notion_create_page"
        params = step.composio_config.params
        assert "parent" in params
        assert "properties" in params
        assert "children" in params
        # Verify deep nesting preserved
        assert params["parent"]["database_id"] == "abc123"
        assert params["properties"]["Title"]["title"][0]["text"]["content"] == "Hello World"

    def test_deeply_nested_validates(self):
        wf = parse_yaml_string(COMPOSIO_NESTED_PARAMS)
        errors = validate(wf)
        assert errors == [], f"Unexpected errors: {errors}"

    def test_implicit_deps_auto_inferred(self):
        """composio step without depends_on but params referencing steps
        should auto-infer deps via _extract_implicit_deps."""
        wf = parse_yaml_string(COMPOSIO_IMPLICIT_DEPS)
        composio_step = wf.get_step("create-ticket")
        valid_ids = {s.id for s in wf.steps}
        implicit = _extract_implicit_deps(composio_step, valid_ids)
        assert "process" in implicit
        assert "fetch-data" in implicit

    def test_implicit_deps_build_plan_respects_order(self):
        """build_plan should order create-ticket after both fetch-data and process."""
        wf = parse_yaml_string(COMPOSIO_IMPLICIT_DEPS)
        plan = build_plan(wf)
        flat = [sid for stage in plan.stages for sid in stage]
        assert flat.index("fetch-data") < flat.index("create-ticket")
        assert flat.index("process") < flat.index("create-ticket")

    def test_composio_without_action_fails_validation(self):
        """composio step without action should fail validation."""
        yaml = """\
name: invalid-composio
steps:
  - id: bad-step
    type: composio
    composio_config:
      params:
        key: value
"""
        wf = parse_yaml_string(yaml)
        errors = validate(wf)
        composio_errors = [e for e in errors if "composio" in e.lower() or "action" in e.lower()]
        assert len(composio_errors) > 0, "Expected validation error for missing action"

    def test_composio_without_config_fails_validation(self):
        """composio step without composio_config should fail validation."""
        yaml = """\
name: invalid-composio-no-config
steps:
  - id: bad-step
    type: composio
"""
        wf = parse_yaml_string(yaml)
        errors = validate(wf)
        composio_errors = [e for e in errors if "composio" in e.lower()]
        assert len(composio_errors) > 0, "Expected validation error for missing composio_config"


# ======================================================================
# TEST 6: Retry + composio
# ======================================================================

class TestComposioRetry:
    """Test 6: Composio step with retry configuration."""

    def test_parse_succeeds(self):
        wf = parse_yaml_string(COMPOSIO_RETRY)
        assert wf.name == "composio-retry"

    def test_retry_config_parsed(self):
        wf = parse_yaml_string(COMPOSIO_RETRY)
        step = wf.steps[0]
        assert step.retry is not None
        assert step.retry.max_attempts == 3
        assert step.retry.backoff == "exponential"

    def test_composio_config_with_retry(self):
        wf = parse_yaml_string(COMPOSIO_RETRY)
        step = wf.steps[0]
        assert step.composio_config is not None
        assert step.composio_config.action == "salesforce_create_lead"
        assert step.composio_config.params["name"] == "John Doe"

    def test_validate_passes(self):
        wf = parse_yaml_string(COMPOSIO_RETRY)
        errors = validate(wf)
        assert errors == [], f"Unexpected errors: {errors}"

    def test_retry_with_fixed_backoff(self):
        yaml = """\
name: composio-retry-fixed
steps:
  - id: retry-fixed
    type: composio
    retry:
      max_attempts: 5
      backoff: fixed
      on_failure: skip
    composio_config:
      action: zendesk_create_ticket
      params:
        subject: "Auto ticket"
"""
        wf = parse_yaml_string(yaml)
        step = wf.steps[0]
        assert step.retry.backoff == "fixed"
        assert step.retry.on_failure == "skip"
        errors = validate(wf)
        assert errors == [], f"Unexpected errors: {errors}"

    def test_retry_invalid_backoff_fails(self):
        yaml = """\
name: composio-bad-retry
steps:
  - id: bad-retry
    type: composio
    retry:
      max_attempts: 3
      backoff: linear
    composio_config:
      action: test_action
"""
        wf = parse_yaml_string(yaml)
        errors = validate(wf)
        backoff_errors = [e for e in errors if "backoff" in e.lower()]
        assert len(backoff_errors) > 0, "Expected validation error for invalid backoff"


# ======================================================================
# TEST 7: build_plan ordering (DAG planner)
# ======================================================================

class TestBuildPlanOrdering:
    """Test 7: Verify DAG planner handles composio step ordering."""

    def test_composio_after_llm_via_template(self):
        """Composio step that references {steps.analyze.output} should come after analyze."""
        wf = parse_yaml_string(DAG_COMPOSIO_AFTER_LLM)
        plan = build_plan(wf)
        assert len(plan.stages) == 2
        assert plan.stages[0] == ["analyze"]
        assert plan.stages[1] == ["act-on-analysis"]

    def test_independent_composio_same_stage(self):
        """Two independent composio steps should be in the same stage."""
        wf = parse_yaml_string(DAG_INDEPENDENT_COMPOSIO)
        plan = build_plan(wf)
        assert len(plan.stages) == 1
        assert set(plan.stages[0]) == {"composio-a", "composio-b"}

    def test_chain_http_composio_notify_three_stages(self):
        """http -> composio -> notify should produce 3 stages."""
        wf = parse_yaml_string(DAG_CHAIN_HTTP_COMPOSIO_NOTIFY)
        plan = build_plan(wf)
        assert len(plan.stages) == 3
        assert plan.stages[0] == ["fetch"]
        assert plan.stages[1] == ["process-composio"]
        assert plan.stages[2] == ["send-notification"]

    def test_mixed_deps_complex_dag(self):
        """Complex DAG with multiple step types and mixed deps."""
        yaml = """\
name: complex-dag
description: Complex DAG with composio
steps:
  - id: input-validate
    type: code
    code_config:
      code: "assert input is not None"
  - id: fetch-external
    type: http
    http_config:
      url: "https://api.example.com/data"
  - id: analyze
    type: llm
    prompt: "Analyze {steps.fetch-external.output} and {steps.input-validate.output}"
  - id: composio-github
    type: composio
    composio_config:
      action: github_create_issue
      params:
        title: "{steps.analyze.output}"
  - id: composio-slack
    type: composio
    composio_config:
      action: slack_send_message
      params:
        text: "{steps.analyze.output}"
  - id: final-notify
    type: notify
    depends_on: [composio-github, composio-slack]
    notify_config:
      service: slack
      channel: "#done"
      message: "All done"
"""
        wf = parse_yaml_string(yaml)
        plan = build_plan(wf)
        flat = [sid for stage in plan.stages for sid in stage]

        # input-validate and fetch-external can run in parallel (stage 0)
        assert set(plan.stages[0]) == {"fetch-external", "input-validate"}

        # analyze depends on both, so stage 1
        assert "analyze" in plan.stages[1]

        # composio-github and composio-slack depend on analyze, same stage
        composio_stage_idx = None
        for i, stage in enumerate(plan.stages):
            if "composio-github" in stage:
                composio_stage_idx = i
                break
        assert composio_stage_idx is not None
        assert "composio-slack" in plan.stages[composio_stage_idx]

        # final-notify comes last
        assert flat[-1] == "final-notify"

    def test_plan_all_steps_scheduled(self):
        """Every step ID should appear exactly once in the plan."""
        wf = parse_yaml_string(DAG_CHAIN_HTTP_COMPOSIO_NOTIFY)
        plan = build_plan(wf)
        all_planned = [sid for stage in plan.stages for sid in stage]
        all_defined = {s.id for s in wf.steps}
        assert set(all_planned) == all_defined
        assert len(all_planned) == len(all_defined)  # no duplicates

    def test_cycle_detection(self):
        """A cycle should raise ValueError during build_plan."""
        yaml = """\
name: cycle-test
steps:
  - id: step-a
    type: composio
    depends_on: [step-b]
    composio_config:
      action: test_a
  - id: step-b
    type: composio
    depends_on: [step-a]
    composio_config:
      action: test_b
"""
        wf = parse_yaml_string(yaml)
        errors = validate(wf)
        cycle_errors = [e for e in errors if "ycle" in e.lower()]
        assert len(cycle_errors) > 0, "Expected cycle detection error"


# ======================================================================
# Additional: extract_step_refs unit tests
# ======================================================================

class TestExtractStepRefs:
    """Unit tests for _extract_step_refs helper."""

    def test_simple_ref(self):
        refs = _extract_step_refs("{steps.my-step.output}")
        assert refs == {"my-step"}

    def test_multiple_refs(self):
        refs = _extract_step_refs(
            "Use {steps.step-a.output} and {steps.step-b.output}"
        )
        assert refs == {"step-a", "step-b"}

    def test_no_refs(self):
        refs = _extract_step_refs("No references here")
        assert refs == set()

    def test_empty_string(self):
        refs = _extract_step_refs("")
        assert refs == set()

    def test_input_ref_not_step(self):
        refs = _extract_step_refs("{input.email}")
        assert refs == set()

    def test_nested_output_path(self):
        refs = _extract_step_refs("{steps.fetch-lead.output.name.givenName}")
        assert refs == {"fetch-lead"}

    def test_composio_params_refs(self):
        """Verify that step refs in composio params JSON are found."""
        import json
        params = {
            "email": "{steps.fetch.output.email}",
            "name": "{steps.analyze.output.name}",
        }
        refs = _extract_step_refs(json.dumps(params))
        assert refs == {"fetch", "analyze"}
