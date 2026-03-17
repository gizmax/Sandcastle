"""Comprehensive tests for POST /runs/estimate and GET /stats/forecast endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from sandcastle.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Workflow YAML fixtures
# ---------------------------------------------------------------------------

SIMPLE_WORKFLOW = """\
name: simple-test
description: A simple one-step workflow
steps:
  - id: step1
    prompt: "Hello world"
    model: sonnet
    max_turns: 3
"""

TWO_STEP_WORKFLOW = """\
name: two-step
description: Two LLM steps
steps:
  - id: step1
    prompt: "First step"
    model: haiku
    max_turns: 1
  - id: step2
    prompt: "Second step"
    model: opus
    max_turns: 2
    depends_on:
      - step1
"""

UNKNOWN_MODEL_WORKFLOW = """\
name: unknown-model
description: Step with unknown model
steps:
  - id: step1
    prompt: "Do something"
    model: gpt-42-turbo-ultra
    max_turns: 1
"""

CLASSIFY_WORKFLOW = """\
name: classify-test
description: Workflow with classify step
steps:
  - id: classify_input
    type: classify
    prompt: "Classify this"
    classify_config:
      categories:
        - positive
        - negative
      input: "{input.text}"
      model: haiku
      branches:
        positive: [handle_pos]
        negative: [handle_neg]
  - id: handle_pos
    prompt: "Handle positive"
    depends_on: [classify_input]
  - id: handle_neg
    prompt: "Handle negative"
    depends_on: [classify_input]
"""

GATE_WORKFLOW = """\
name: gate-test
description: Workflow with gate step
steps:
  - id: generate
    prompt: "Generate something"
    model: sonnet
  - id: quality_gate
    type: gate
    prompt: "Check quality"
    depends_on: [generate]
    gate_config:
      strategies:
        - type: llm_eval
          config:
            model: haiku
            threshold: 0.7
"""

SUB_WORKFLOW_YAML = """\
name: sub-workflow-test
description: Workflow with sub_workflow step
steps:
  - id: parent_step
    type: sub_workflow
    prompt: "Run child"
    sub_workflow_config:
      workflow: child.yaml
"""

COMPOSIO_WORKFLOW = """\
name: composio-test
description: Workflow with composio step
steps:
  - id: send_email
    type: composio
    prompt: "Send email"
    composio_config:
      action: gmail_send_email
      params:
        to: test@example.com
"""

PARALLEL_OVER_WORKFLOW = """\
name: parallel-test
description: Workflow with parallel_over
steps:
  - id: process
    prompt: "Process item {item}"
    model: haiku
    max_turns: 1
    parallel_over: "input.items"
"""

BROKEN_DEPS_WORKFLOW = """\
name: broken-deps
description: Workflow with broken dependencies
steps:
  - id: step1
    prompt: "First"
    depends_on:
      - nonexistent_step
  - id: step2
    prompt: "Second"
    depends_on:
      - step1
"""

MIXED_WORKFLOW = """\
name: mixed-types
description: Workflow with various step types
steps:
  - id: llm_step
    prompt: "Analyze data"
    model: sonnet
    max_turns: 2
  - id: http_step
    type: http
    prompt: "Fetch data"
    http_config:
      url: "https://api.example.com/data"
      method: GET
    depends_on: [llm_step]
  - id: code_step
    type: code
    prompt: "Process"
    code_config:
      language: python
      code: "print('hello')"
    depends_on: [http_step]
  - id: composio_step
    type: composio
    prompt: "Send"
    composio_config:
      action: slack_post_message
    depends_on: [code_step]
"""

DEFAULT_MODEL_WORKFLOW = """\
name: default-model-test
description: Workflow using default_model
default_model: opus
steps:
  - id: step1
    prompt: "Use default model"
    max_turns: 1
"""


# ===========================================================================
# POST /runs/estimate
# ===========================================================================


class TestEstimateValidWorkflow:
    """Test area 1: Valid workflow YAML returns step estimates with correct models."""

    def test_simple_workflow_returns_200(self):
        resp = client.post("/api/runs/estimate", json={"yaml_content": SIMPLE_WORKFLOW})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["workflow_name"] == "simple-test"
        assert data["total_estimated_cost_usd"] > 0
        assert len(data["steps"]) == 1
        assert data["steps"][0]["model"] == "sonnet"
        assert data["steps"][0]["estimated_cost_usd"] > 0
        assert "disclaimer" in data

    def test_two_step_workflow_model_mapping(self):
        resp = client.post("/api/runs/estimate", json={"yaml_content": TWO_STEP_WORKFLOW})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["steps"]) == 2
        step_map = {s["step_id"]: s for s in data["steps"]}
        assert step_map["step1"]["model"] == "haiku"
        assert step_map["step2"]["model"] == "opus"
        # Opus should cost more than haiku (per token)
        assert step_map["step2"]["estimated_cost_usd"] > step_map["step1"]["estimated_cost_usd"]

    def test_total_is_sum_of_steps(self):
        resp = client.post("/api/runs/estimate", json={"yaml_content": TWO_STEP_WORKFLOW})
        data = resp.json()["data"]
        step_sum = sum(s["estimated_cost_usd"] for s in data["steps"])
        assert abs(data["total_estimated_cost_usd"] - round(step_sum, 4)) < 0.0001

    def test_default_model_applied(self):
        """When step has no explicit model, default_model from workflow is used."""
        resp = client.post("/api/runs/estimate", json={"yaml_content": DEFAULT_MODEL_WORKFLOW})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["steps"][0]["model"] == "opus"

    def test_response_structure(self):
        resp = client.post("/api/runs/estimate", json={"yaml_content": SIMPLE_WORKFLOW})
        data = resp.json()["data"]
        # Required keys
        assert "workflow_name" in data
        assert "total_estimated_cost_usd" in data
        assert "steps" in data
        assert "validation_errors" in data
        assert "disclaimer" in data
        # Step structure
        step = data["steps"][0]
        assert "step_id" in step
        assert "type" in step
        assert "model" in step
        assert "estimated_cost_usd" in step
        assert "note" in step


class TestEstimateInvalidYaml:
    """Test area 2: Invalid YAML returns 400."""

    def test_broken_yaml_syntax(self):
        resp = client.post("/api/runs/estimate", json={"yaml_content": "this is not valid yaml: ["})
        assert resp.status_code == 400

    def test_yaml_without_name(self):
        resp = client.post("/api/runs/estimate", json={
            "yaml_content": "steps:\n  - id: step1\n    prompt: hello\n"
        })
        assert resp.status_code == 400

    def test_yaml_without_steps_returns_validation_error(self):
        """A workflow with name but no steps is parseable but invalid.

        The endpoint returns 200 with an empty steps array and a
        validation_errors list that flags the missing steps.
        """
        resp = client.post("/api/runs/estimate", json={
            "yaml_content": "name: no-steps\ndescription: missing steps\n"
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["steps"] == []
        assert any("at least one step" in e for e in data["validation_errors"])

    def test_error_response_format(self):
        resp = client.post("/api/runs/estimate", json={"yaml_content": "invalid: ["})
        assert resp.status_code == 400
        body = resp.json()
        # The error should be structured
        assert "detail" in body or "error" in body.get("detail", {}) or "error" in body


class TestEstimateMalformedJson:
    """Test area 3: Malformed JSON body returns 422 (Pydantic validation)."""

    def test_missing_yaml_content_field(self):
        resp = client.post("/api/runs/estimate", json={"wrong_field": "hello"})
        assert resp.status_code == 422

    def test_yaml_content_wrong_type(self):
        resp = client.post("/api/runs/estimate", json={"yaml_content": 12345})
        assert resp.status_code == 422

    def test_no_body(self):
        resp = client.post(
            "/api/runs/estimate",
            content=b"",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422


class TestEstimateEmptyYamlContent:
    """Test area 4: Empty yaml_content returns 422."""

    def test_empty_string(self):
        resp = client.post("/api/runs/estimate", json={"yaml_content": ""})
        assert resp.status_code == 422

    def test_whitespace_only(self):
        """Whitespace-only should fail validation (min_length=1 after stripping or empty parse)."""
        resp = client.post("/api/runs/estimate", json={"yaml_content": "   "})
        # Either 422 (pydantic strips to empty) or 400 (YAML parse fails)
        assert resp.status_code in (400, 422)


class TestEstimateUnknownModel:
    """Test area 5: Unknown model falls back to sonnet pricing, NOT $0.00."""

    def test_unknown_model_fallback_nonzero(self):
        resp = client.post("/api/runs/estimate", json={"yaml_content": UNKNOWN_MODEL_WORKFLOW})
        assert resp.status_code == 200
        data = resp.json()["data"]
        step = data["steps"][0]
        assert step["estimated_cost_usd"] > 0, "Unknown model should NOT have $0.00 cost"

    def test_unknown_model_note_mentions_fallback(self):
        resp = client.post("/api/runs/estimate", json={"yaml_content": UNKNOWN_MODEL_WORKFLOW})
        data = resp.json()["data"]
        step = data["steps"][0]
        assert "unknown model" in step["note"].lower() or "sonnet pricing" in step["note"].lower()

    def test_unknown_model_keeps_original_key(self):
        """The model field should still reflect the original model key from YAML."""
        resp = client.post("/api/runs/estimate", json={"yaml_content": UNKNOWN_MODEL_WORKFLOW})
        data = resp.json()["data"]
        step = data["steps"][0]
        assert step["model"] == "gpt-42-turbo-ultra"


class TestEstimateClassifyStep:
    """Test area 6: Classify step should estimate as single call (turns=1)."""

    def test_classify_single_call(self):
        resp = client.post("/api/runs/estimate", json={"yaml_content": CLASSIFY_WORKFLOW})
        assert resp.status_code == 200
        data = resp.json()["data"]
        step_map = {s["step_id"]: s for s in data["steps"]}
        classify_step = step_map["classify_input"]
        assert classify_step["estimated_cost_usd"] > 0

    def test_classify_uses_classify_config_model(self):
        resp = client.post("/api/runs/estimate", json={"yaml_content": CLASSIFY_WORKFLOW})
        data = resp.json()["data"]
        step_map = {s["step_id"]: s for s in data["steps"]}
        classify_step = step_map["classify_input"]
        assert classify_step["model"] == "haiku"

    def test_classify_cheaper_than_multi_turn(self):
        """classify with 1 call should be cheaper than a standard step with max_turns."""
        resp = client.post("/api/runs/estimate", json={"yaml_content": CLASSIFY_WORKFLOW})
        data = resp.json()["data"]
        step_map = {s["step_id"]: s for s in data["steps"]}
        classify_cost = step_map["classify_input"]["estimated_cost_usd"]
        # handle_pos uses default model (sonnet) with default max_turns (up to 5)
        standard_cost = step_map["handle_pos"]["estimated_cost_usd"]
        assert classify_cost < standard_cost


class TestEstimateGateStep:
    """Test area 7: Gate step reads model from strategies[].config.model."""

    def test_gate_reads_strategy_model(self):
        resp = client.post("/api/runs/estimate", json={"yaml_content": GATE_WORKFLOW})
        assert resp.status_code == 200
        data = resp.json()["data"]
        step_map = {s["step_id"]: s for s in data["steps"]}
        gate_step = step_map["quality_gate"]
        assert gate_step["model"] == "haiku"

    def test_gate_single_call(self):
        """Gate is a SINGLE_CALL_TYPE, should use turns=1."""
        resp = client.post("/api/runs/estimate", json={"yaml_content": GATE_WORKFLOW})
        data = resp.json()["data"]
        step_map = {s["step_id"]: s for s in data["steps"]}
        gate_step = step_map["quality_gate"]
        assert gate_step["estimated_cost_usd"] > 0


class TestEstimateSubWorkflow:
    """Test area 8: sub_workflow shows 'Cost in child workflow', not LLM charge."""

    def test_sub_workflow_note(self):
        resp = client.post("/api/runs/estimate", json={"yaml_content": SUB_WORKFLOW_YAML})
        assert resp.status_code == 200
        data = resp.json()["data"]
        step = data["steps"][0]
        assert step["note"] == "Cost in child workflow"
        assert step["estimated_cost_usd"] == 0.0
        assert step["model"] is None


class TestEstimateComposioStep:
    """Test area 9: composio step shows 'No LLM cost'."""

    def test_composio_no_llm_cost(self):
        resp = client.post("/api/runs/estimate", json={"yaml_content": COMPOSIO_WORKFLOW})
        assert resp.status_code == 200
        data = resp.json()["data"]
        step = data["steps"][0]
        assert step["note"] == "No LLM cost"
        assert step["estimated_cost_usd"] == 0.0
        assert step["model"] is None


class TestEstimateParallelOver:
    """Test area 10: parallel_over should multiply by 10."""

    def test_parallel_over_multiplier(self):
        resp = client.post("/api/runs/estimate", json={"yaml_content": PARALLEL_OVER_WORKFLOW})
        assert resp.status_code == 200
        data = resp.json()["data"]
        step = data["steps"][0]
        assert "x10" in step["note"]
        assert step["estimated_cost_usd"] > 0

    def test_parallel_over_cost_is_10x(self):
        """parallel_over cost should be 10x the single-step cost."""
        # Single step without parallel_over
        single_yaml = """\
name: single
steps:
  - id: process
    prompt: "Process item"
    model: haiku
    max_turns: 1
"""
        resp_single = client.post("/api/runs/estimate", json={"yaml_content": single_yaml})
        single_cost = resp_single.json()["data"]["steps"][0]["estimated_cost_usd"]

        resp_parallel = client.post("/api/runs/estimate", json={"yaml_content": PARALLEL_OVER_WORKFLOW})
        parallel_cost = resp_parallel.json()["data"]["steps"][0]["estimated_cost_usd"]

        assert abs(parallel_cost - single_cost * 10) < 0.000001


class TestEstimateValidationErrors:
    """Test areas 11 & 12: validation_errors in response."""

    def test_valid_workflow_no_errors(self):
        resp = client.post("/api/runs/estimate", json={"yaml_content": SIMPLE_WORKFLOW})
        data = resp.json()["data"]
        assert "validation_errors" in data
        assert data["validation_errors"] == []
        assert data["valid"] is True

    def test_broken_deps_still_estimates(self):
        """Broken deps should still return estimates, plus validation_errors and valid=false."""
        resp = client.post("/api/runs/estimate", json={"yaml_content": BROKEN_DEPS_WORKFLOW})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["valid"] is False
        assert len(data["steps"]) == 2
        assert data["total_estimated_cost_usd"] > 0
        assert len(data["validation_errors"]) > 0
        assert "unreliable" in data["disclaimer"].lower()

    def test_validation_errors_contain_dep_message(self):
        resp = client.post("/api/runs/estimate", json={"yaml_content": BROKEN_DEPS_WORKFLOW})
        data = resp.json()["data"]
        assert data["valid"] is False
        errors_text = " ".join(data["validation_errors"])
        assert "nonexistent_step" in errors_text.lower()


class TestEstimateMixedTypes:
    """Mixed step types: LLM steps have cost, non-LLM have zero."""

    def test_mixed_workflow(self):
        resp = client.post("/api/runs/estimate", json={"yaml_content": MIXED_WORKFLOW})
        assert resp.status_code == 200
        data = resp.json()["data"]
        step_map = {s["step_id"]: s for s in data["steps"]}

        # LLM step has cost
        assert step_map["llm_step"]["estimated_cost_usd"] > 0
        assert step_map["llm_step"]["model"] == "sonnet"

        # HTTP step has no cost
        assert step_map["http_step"]["estimated_cost_usd"] == 0.0
        assert step_map["http_step"]["model"] is None
        assert step_map["http_step"]["note"] == "No LLM cost"

        # Code step has no cost
        assert step_map["code_step"]["estimated_cost_usd"] == 0.0
        assert step_map["code_step"]["model"] is None

        # Composio step has no cost
        assert step_map["composio_step"]["estimated_cost_usd"] == 0.0


# ===========================================================================
# GET /stats/forecast
# ===========================================================================


class TestForecastBasic:
    """Test areas for GET /stats/forecast."""

    def test_returns_200(self):
        resp = client.get("/api/stats/forecast")
        assert resp.status_code == 200

    def test_30_historical_entries(self):
        """Should return exactly 30 historical entries (zero-filled)."""
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        assert "historical" in data
        assert len(data["historical"]) == 30

    def test_7_projected_entries(self):
        """Should return exactly 7 projected entries."""
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        assert "projected" in data
        assert len(data["projected"]) == 7

    def test_required_fields_present(self):
        """daily_average, trend_percent, projected_monthly should be present."""
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        assert "daily_average" in data
        assert "trend_percent" in data
        assert "projected_monthly" in data

    def test_historical_entry_structure(self):
        """Each historical entry should have date, cost, runs."""
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        for entry in data["historical"]:
            assert "date" in entry
            assert "cost" in entry
            assert "runs" in entry

    def test_projected_entry_structure(self):
        """Each projected entry should have date, cost."""
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        for entry in data["projected"]:
            assert "date" in entry
            assert "cost" in entry

    def test_historical_dates_are_ordered(self):
        """Historical entries should be ordered chronologically."""
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        dates = [e["date"] for e in data["historical"]]
        assert dates == sorted(dates)

    def test_projected_dates_after_historical(self):
        """Projected dates should be after the last historical date."""
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        last_hist = data["historical"][-1]["date"]
        first_proj = data["projected"][0]["date"]
        assert first_proj > last_hist


class TestForecastNoRuns:
    """Test area 4 for forecast: fields are non-negative and well-formed even with sparse/no data."""

    def test_fields_non_negative(self):
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        assert data["daily_average"] >= 0.0
        assert data["projected_monthly"] >= 0.0
        assert isinstance(data["trend_percent"], (int, float))

    def test_historical_costs_non_negative(self):
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        for entry in data["historical"]:
            assert entry["cost"] >= 0.0
            assert entry["runs"] >= 0

    def test_projected_costs_non_negative(self):
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        for entry in data["projected"]:
            assert entry["cost"] >= 0.0


class TestForecastTypes:
    """Verify numeric types in forecast response."""

    def test_daily_average_is_numeric(self):
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        assert isinstance(data["daily_average"], (int, float))

    def test_trend_percent_is_numeric(self):
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        assert isinstance(data["trend_percent"], (int, float))

    def test_projected_monthly_is_numeric(self):
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        assert isinstance(data["projected_monthly"], (int, float))
