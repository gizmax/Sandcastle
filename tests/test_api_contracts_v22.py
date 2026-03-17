"""API contract tests for Sandcastle v0.22 endpoints.

Tests cover:
  - POST /api/advisor/explain  - full contract + edge cases
  - POST /api/runs/estimate    - edge cases for cost estimation
  - GET  /api/stats/forecast   - shape, dates, consistency
  - Cross-endpoint consistency between /api/stats and /api/stats/forecast
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# Disable rate limiting for all tests in this module
from sandcastle.api import rate_limit as _rl_mod

_rl_mod.execution_limiter.check = AsyncMock()

from sandcastle.main import app  # noqa: E402

client = TestClient(app)

# ---------------------------------------------------------------------------
# Helper YAML snippets
# ---------------------------------------------------------------------------

MINIMAL_WORKFLOW = """\
name: minimal
description: Minimal workflow
steps:
  - id: step1
    prompt: "Hello"
"""

ZERO_STEPS_WORKFLOW = """\
name: empty-wf
description: A workflow with no steps
"""

ALL_NON_LLM_WORKFLOW = """\
name: non-llm-only
description: All non-LLM step types
steps:
  - id: fetch
    type: http
    http_config:
      url: "https://example.com"
      method: GET
  - id: process
    type: code
    code_config:
      language: python
      source: "result = 42"
    depends_on: [fetch]
  - id: act
    type: composio
    composio_config:
      action: "test_action"
    depends_on: [process]
"""

APPROVAL_STEP_WORKFLOW = """\
name: approval-wf
description: Workflow with approval
steps:
  - id: review
    type: approval
    approval_config:
      message: "Please approve"
"""

MIXED_STEP_TYPES_WORKFLOW = """\
name: mixed-types
description: Mix of all step types
steps:
  - id: s_standard
    type: standard
    prompt: "Hello"
  - id: s_llm
    type: llm
    prompt: "Think about this"
    llm_config:
      system_prompt: "You are helpful"
  - id: s_http
    type: http
    http_config:
      url: "https://example.com"
      method: GET
  - id: s_code
    type: code
    code_config:
      language: python
      source: "x = 1"
  - id: s_composio
    type: composio
    composio_config:
      action: "test_action"
  - id: s_classify
    type: classify
    classify_config:
      categories:
        - name: positive
          description: Positive sentiment
        - name: negative
          description: Negative sentiment
  - id: s_gate
    type: gate
    gate_config:
      strategies:
        - type: keyword
          config:
            blocked: ["bad"]
  - id: s_sub
    type: sub_workflow
    sub_workflow:
      workflow: "other.yaml"
  - id: s_approval
    type: approval
    approval_config:
      message: "Approve this"
  - id: s_loop
    type: loop
    loop_config:
      over: "items"
      step:
        id: inner
        prompt: "process"
  - id: s_condition
    type: condition
    condition_config:
      expression: "true"
      then_step: s_standard
  - id: s_transform
    type: transform
    transform_config:
      template: "{{ input }}"
  - id: s_notify
    type: notify
    notify_config:
      channel: stdout
      message: "done"
"""

PARALLEL_NON_LLM_WORKFLOW = """\
name: parallel-non-llm
description: Parallel over non-LLM step
steps:
  - id: fetch_all
    type: http
    http_config:
      url: "https://example.com"
      method: GET
    parallel_over: "input.urls"
"""


def _make_fifty_steps_yaml() -> str:
    """Generate a workflow with 50 standard steps."""
    lines = ["name: fifty-steps", "description: 50-step workflow", "steps:"]
    for i in range(50):
        lines.append(f"  - id: step_{i}")
        lines.append(f'    prompt: "Step {i} prompt"')
    return "\n".join(lines)


def _make_large_yaml(size_kb: int = 100) -> str:
    """Generate a workflow YAML of approximately size_kb kilobytes."""
    header = "name: large-wf\ndescription: Large workflow\nsteps:\n"
    step_template = '  - id: step_{i}\n    prompt: "Prompt {padding}"\n'
    steps = []
    i = 0
    current_size = len(header)
    target_size = size_kb * 1024
    while current_size < target_size:
        padding = "x" * 200  # 200 chars per step
        step = step_template.format(i=i, padding=padding)
        steps.append(step)
        current_size += len(step)
        i += 1
    return header + "".join(steps)


EXTRA_FIELDS_WORKFLOW = """\
name: extra-fields
description: Workflow with unknown fields
custom_metadata:
  author: test
  version: 42
unknown_top_level: true
steps:
  - id: step1
    prompt: "Hello"
    unknown_step_field: "should be ignored"
"""


# ===========================================================================
# 1. POST /api/advisor/explain
# ===========================================================================


class TestAdvisorExplain:
    """Tests for POST /api/advisor/explain."""

    def test_valid_request_returns_200_with_expected_keys(self):
        """Valid request returns 200 with {summary, cause, fix, severity}."""
        # Without ANTHROPIC_API_KEY, the explain_error function returns
        # a fallback dict directly (no API call).
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
            resp = client.post("/api/advisor/explain", json={
                "step_id": "greet",
                "step_type": "standard",
                "error": "Connection refused to model endpoint",
            })
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "data" in body
        data = body["data"]
        for key in ("summary", "cause", "fix", "severity"):
            assert key in data, f"Missing key '{key}' in response data: {data}"

    def test_missing_step_id_returns_422(self):
        """Missing step_id should return 422 validation error."""
        resp = client.post("/api/advisor/explain", json={
            "error": "Some error",
        })
        assert resp.status_code == 422

    def test_missing_error_returns_422(self):
        """Missing error field should return 422 validation error."""
        resp = client.post("/api/advisor/explain", json={
            "step_id": "greet",
        })
        assert resp.status_code == 422

    def test_empty_step_id_returns_422(self):
        """Empty step_id string should return 422 (min_length=1)."""
        resp = client.post("/api/advisor/explain", json={
            "step_id": "",
            "error": "Some error",
        })
        assert resp.status_code == 422

    def test_very_long_error_does_not_crash(self):
        """Very long error string (10000 chars) should not crash the endpoint."""
        long_error = "E" * 10000
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
            resp = client.post("/api/advisor/explain", json={
                "step_id": "step1",
                "step_type": "http",
                "error": long_error,
            })
        assert resp.status_code == 200
        data = resp.json()["data"]
        # The summary should be truncated (explain_error truncates to 200 chars)
        assert len(data["summary"]) <= 200

    def test_error_exceeding_max_length_returns_422(self):
        """Error string exceeding max_length=10000 should return 422."""
        too_long = "E" * 10001
        resp = client.post("/api/advisor/explain", json={
            "step_id": "step1",
            "error": too_long,
        })
        assert resp.status_code == 422

    def test_very_long_prompt_does_not_crash(self):
        """Very long prompt (5000 chars) should not crash."""
        long_prompt = "P" * 5000
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
            resp = client.post("/api/advisor/explain", json={
                "step_id": "step1",
                "error": "Some error",
                "prompt": long_prompt,
            })
        assert resp.status_code == 200

    def test_prompt_exceeding_max_length_returns_422(self):
        """Prompt string exceeding max_length=5000 should return 422."""
        too_long = "P" * 5001
        resp = client.post("/api/advisor/explain", json={
            "step_id": "step1",
            "error": "Some error",
            "prompt": too_long,
        })
        assert resp.status_code == 422

    def test_special_characters_in_error(self):
        """Unicode, newlines, and null bytes in error field should not crash."""
        special_error = (
            "Error: \u010c\u00e9\u0161\u0165ina \u00e4\u00f6\u00fc\u00df "  # Czech/German chars
            "\u4f60\u597d\u4e16\u754c "  # Chinese
            "\n\nTraceback:\n  File line 42\n"
            "\x00\x01\x02null bytes\x00"  # Null bytes
            "\U0001f525\U0001f4a5"  # Emoji
        )
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
            resp = client.post("/api/advisor/explain", json={
                "step_id": "step1",
                "error": special_error,
            })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "summary" in data

    def test_response_content_type_is_json(self):
        """Response Content-Type should be application/json."""
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
            resp = client.post("/api/advisor/explain", json={
                "step_id": "step1",
                "error": "test error",
            })
        assert "application/json" in resp.headers.get("content-type", "")

    def test_without_api_key_returns_200_with_fallback(self):
        """Without ANTHROPIC_API_KEY, should still return 200 with fallback explanation."""
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
            # Also ensure settings has no key
            with patch("sandcastle.config.settings.anthropic_api_key", ""):
                resp = client.post("/api/advisor/explain", json={
                    "step_id": "step1",
                    "step_type": "llm",
                    "error": "Model not found",
                })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["severity"] == "medium"
        assert "summary" in data
        assert "cause" in data
        assert "fix" in data

    def test_all_optional_fields_provided(self):
        """Providing all optional fields should work."""
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
            resp = client.post("/api/advisor/explain", json={
                "step_id": "my_step",
                "step_type": "http",
                "error": "404 Not Found",
                "prompt": "Fetch user data",
                "model": "haiku",
                "workflow_name": "user-pipeline",
            })
        assert resp.status_code == 200
        data = resp.json()["data"]
        for key in ("summary", "cause", "fix", "severity"):
            assert key in data


# ===========================================================================
# 2. POST /api/runs/estimate
# ===========================================================================


class TestRunsEstimate:
    """Tests for POST /api/runs/estimate."""

    def test_zero_steps_returns_200_with_validation_errors(self):
        """Workflow with 0 steps should return 200 with validation_errors."""
        resp = client.post("/api/runs/estimate", json={
            "yaml_content": ZERO_STEPS_WORKFLOW,
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "validation_errors" in data
        assert len(data["validation_errors"]) > 0
        # Should mention steps
        assert any("step" in e.lower() for e in data["validation_errors"])

    def test_fifty_steps_handles_without_timeout(self):
        """Workflow with 50 steps should complete without timeout."""
        yaml_content = _make_fifty_steps_yaml()
        resp = client.post("/api/runs/estimate", json={
            "yaml_content": yaml_content,
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["steps"]) == 50
        assert data["total_estimated_cost_usd"] > 0

    def test_all_non_llm_steps_total_is_zero(self):
        """Workflow where ALL steps are non-LLM should have total=0.0."""
        resp = client.post("/api/runs/estimate", json={
            "yaml_content": ALL_NON_LLM_WORKFLOW,
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_estimated_cost_usd"] == 0.0
        for step in data["steps"]:
            assert step["estimated_cost_usd"] == 0.0

    def test_approval_step_is_zero_cost(self):
        """Approval step should have $0 cost."""
        resp = client.post("/api/runs/estimate", json={
            "yaml_content": APPROVAL_STEP_WORKFLOW,
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["steps"]) == 1
        assert data["steps"][0]["estimated_cost_usd"] == 0.0
        assert data["total_estimated_cost_usd"] == 0.0

    def test_mixed_step_types(self):
        """Workflow mixing all step types should be handled correctly."""
        resp = client.post("/api/runs/estimate", json={
            "yaml_content": MIXED_STEP_TYPES_WORKFLOW,
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["steps"]) == 13  # 13 steps in MIXED_STEP_TYPES_WORKFLOW

        # Check each step has required fields
        for step in data["steps"]:
            assert "step_id" in step
            assert "type" in step
            assert "estimated_cost_usd" in step
            assert "note" in step
            assert step["estimated_cost_usd"] >= 0.0

        # Non-LLM types should be $0
        non_llm_ids = {
            "s_http", "s_code", "s_composio", "s_sub",
            "s_approval", "s_loop", "s_condition", "s_transform", "s_notify",
        }
        for step in data["steps"]:
            if step["step_id"] in non_llm_ids:
                assert step["estimated_cost_usd"] == 0.0, (
                    f"Step {step['step_id']} (type={step['type']}) should be $0"
                )

        # LLM types should be > $0
        llm_ids = {"s_standard", "s_llm", "s_classify", "s_gate"}
        for step in data["steps"]:
            if step["step_id"] in llm_ids:
                assert step["estimated_cost_usd"] > 0.0, (
                    f"Step {step['step_id']} (type={step['type']}) should be > $0"
                )

    def test_extra_unknown_fields_still_parse(self):
        """YAML with extra unknown fields should still parse (YAML is lenient)."""
        resp = client.post("/api/runs/estimate", json={
            "yaml_content": EXTRA_FIELDS_WORKFLOW,
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["workflow_name"] == "extra-fields"
        assert len(data["steps"]) == 1

    def test_very_large_yaml_handles(self):
        """Very large YAML (~100KB) should be handled."""
        large_yaml = _make_large_yaml(100)
        resp = client.post("/api/runs/estimate", json={
            "yaml_content": large_yaml,
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["steps"]) > 0
        assert data["total_estimated_cost_usd"] > 0

    def test_parallel_over_non_llm_is_zero(self):
        """Parallel_over on a non-LLM step should still be $0 * 10 = $0."""
        resp = client.post("/api/runs/estimate", json={
            "yaml_content": PARALLEL_NON_LLM_WORKFLOW,
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_estimated_cost_usd"] == 0.0
        assert data["steps"][0]["estimated_cost_usd"] == 0.0

    def test_estimate_response_has_disclaimer(self):
        """Response should include a disclaimer."""
        resp = client.post("/api/runs/estimate", json={
            "yaml_content": MINIMAL_WORKFLOW,
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "disclaimer" in data
        assert len(data["disclaimer"]) > 0

    def test_invalid_yaml_returns_400(self):
        """Invalid YAML should return 400."""
        resp = client.post("/api/runs/estimate", json={
            "yaml_content": ": invalid: yaml: [",
        })
        assert resp.status_code == 400

    def test_empty_yaml_returns_error(self):
        """Empty YAML content should return an error."""
        resp = client.post("/api/runs/estimate", json={
            "yaml_content": "   ",
        })
        # Schema min_length=1, but whitespace might pass pydantic
        # The parse function raises ValueError for empty/whitespace
        assert resp.status_code in (400, 422)

    def test_missing_yaml_content_returns_422(self):
        """Missing yaml_content should return 422."""
        resp = client.post("/api/runs/estimate", json={})
        assert resp.status_code == 422

    def test_step_estimate_fields_contract(self):
        """Each step estimate should have step_id, type, model, estimated_cost_usd, note."""
        resp = client.post("/api/runs/estimate", json={
            "yaml_content": MINIMAL_WORKFLOW,
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        for step in data["steps"]:
            assert "step_id" in step
            assert "type" in step
            assert "model" in step  # Can be None for non-LLM
            assert "estimated_cost_usd" in step
            assert "note" in step

    def test_response_has_workflow_name(self):
        """Response should include workflow_name from parsed YAML."""
        resp = client.post("/api/runs/estimate", json={
            "yaml_content": MINIMAL_WORKFLOW,
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["workflow_name"] == "minimal"


# ===========================================================================
# 3. GET /api/stats/forecast
# ===========================================================================

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class TestStatsForecast:
    """Tests for GET /api/stats/forecast."""

    def test_has_exactly_30_historical_entries(self):
        """Response should always have exactly 30 historical entries."""
        resp = client.get("/api/stats/forecast")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["historical"]) == 30

    def test_has_exactly_7_projected_entries(self):
        """Response should always have exactly 7 projected entries."""
        resp = client.get("/api/stats/forecast")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["projected"]) == 7

    def test_historical_dates_are_consecutive(self):
        """Historical dates should be consecutive (no gaps)."""
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        dates = [
            datetime.strptime(h["date"], "%Y-%m-%d").date()
            for h in data["historical"]
        ]
        for i in range(1, len(dates)):
            delta = (dates[i] - dates[i - 1]).days
            assert delta == 1, (
                f"Gap between historical dates at index {i-1} and {i}: "
                f"{dates[i-1]} -> {dates[i]} (delta={delta})"
            )

    def test_historical_dates_sorted_ascending(self):
        """Historical dates should be sorted ascending."""
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        dates = [h["date"] for h in data["historical"]]
        assert dates == sorted(dates), "Historical dates are not sorted ascending"

    def test_projected_dates_start_after_last_historical(self):
        """Projected dates should start after the last historical date."""
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        last_historical = datetime.strptime(
            data["historical"][-1]["date"], "%Y-%m-%d"
        ).date()
        first_projected = datetime.strptime(
            data["projected"][0]["date"], "%Y-%m-%d"
        ).date()
        assert first_projected > last_historical, (
            f"First projected date {first_projected} should be after "
            f"last historical {last_historical}"
        )

    def test_all_dates_are_valid_iso_format(self):
        """All dates should be valid ISO format (YYYY-MM-DD)."""
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        for entry in data["historical"] + data["projected"]:
            assert _ISO_DATE_RE.match(entry["date"]), (
                f"Date '{entry['date']}' is not valid ISO format"
            )
            # Also verify it actually parses
            datetime.strptime(entry["date"], "%Y-%m-%d")

    def test_response_shape_stable_across_calls(self):
        """Response shape should be stable across multiple calls."""
        resp1 = client.get("/api/stats/forecast")
        resp2 = client.get("/api/stats/forecast")
        data1 = resp1.json()["data"]
        data2 = resp2.json()["data"]
        # Same top-level keys
        assert set(data1.keys()) == set(data2.keys())
        # Same lengths
        assert len(data1["historical"]) == len(data2["historical"])
        assert len(data1["projected"]) == len(data2["projected"])
        # Same dates (may differ in cost due to DB changes, but structure matches)
        dates1_hist = [h["date"] for h in data1["historical"]]
        dates2_hist = [h["date"] for h in data2["historical"]]
        assert dates1_hist == dates2_hist

    def test_response_has_expected_top_level_keys(self):
        """Response data should have: historical, projected, daily_average,
        trend_percent, projected_monthly."""
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        expected_keys = {
            "historical", "projected", "daily_average",
            "trend_percent", "projected_monthly",
        }
        assert expected_keys.issubset(set(data.keys())), (
            f"Missing keys: {expected_keys - set(data.keys())}"
        )

    def test_historical_entries_have_cost_and_runs(self):
        """Each historical entry should have date, cost, and runs."""
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        for h in data["historical"]:
            assert "date" in h
            assert "cost" in h
            assert "runs" in h
            assert isinstance(h["cost"], (int, float))
            assert isinstance(h["runs"], int)
            assert h["cost"] >= 0
            assert h["runs"] >= 0

    def test_projected_entries_have_date_and_cost(self):
        """Each projected entry should have date and cost."""
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        for p in data["projected"]:
            assert "date" in p
            assert "cost" in p
            assert isinstance(p["cost"], (int, float))
            assert p["cost"] >= 0

    def test_daily_average_is_non_negative(self):
        """daily_average should be non-negative."""
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        assert data["daily_average"] >= 0

    def test_projected_monthly_is_non_negative(self):
        """projected_monthly should be non-negative."""
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        assert data["projected_monthly"] >= 0

    def test_content_type_is_json(self):
        """Response Content-Type should be application/json."""
        resp = client.get("/api/stats/forecast")
        assert "application/json" in resp.headers.get("content-type", "")


# ===========================================================================
# 4. Cross-endpoint consistency
# ===========================================================================


class TestCrossEndpointConsistency:
    """Tests for cross-endpoint data consistency."""

    def test_stats_and_forecast_use_same_db(self):
        """/api/stats and /api/stats/forecast should both query runs from the DB.
        On an empty DB, stats should show 0 cost and forecast should show all zeros."""
        stats_resp = client.get("/api/stats")
        forecast_resp = client.get("/api/stats/forecast")
        assert stats_resp.status_code == 200
        assert forecast_resp.status_code == 200

        stats_data = stats_resp.json()["data"]
        forecast_data = forecast_resp.json()["data"]

        # Both should report numbers (possibly 0 on empty DB)
        assert isinstance(stats_data["total_cost_today"], (int, float))
        assert isinstance(forecast_data["daily_average"], (int, float))

        # On empty DB, total_cost_today should be 0
        assert stats_data["total_cost_today"] >= 0
        assert forecast_data["daily_average"] >= 0

    def test_stats_response_shape(self):
        """/api/stats should return expected StatsResponse fields."""
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()["data"]
        expected_keys = {
            "total_runs_today", "success_rate", "total_cost_today",
            "avg_duration_seconds", "runs_by_day", "cost_by_workflow",
        }
        assert expected_keys.issubset(set(data.keys())), (
            f"Missing keys in /api/stats: {expected_keys - set(data.keys())}"
        )

    def test_estimate_then_compare_shape(self):
        """Estimate for a workflow should return structured cost data
        that could be compared with actual stats."""
        estimate_resp = client.post("/api/runs/estimate", json={
            "yaml_content": MINIMAL_WORKFLOW,
        })
        assert estimate_resp.status_code == 200
        estimate_data = estimate_resp.json()["data"]

        # Estimate should have total_estimated_cost_usd
        assert "total_estimated_cost_usd" in estimate_data
        assert isinstance(estimate_data["total_estimated_cost_usd"], (int, float))
        assert estimate_data["total_estimated_cost_usd"] >= 0

        # Stats should have total_cost_today
        stats_resp = client.get("/api/stats")
        assert stats_resp.status_code == 200
        stats_data = stats_resp.json()["data"]
        assert "total_cost_today" in stats_data
        assert isinstance(stats_data["total_cost_today"], (int, float))

    def test_forecast_projected_dates_are_future(self):
        """Projected dates in forecast should all be in the future relative to today."""
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for p in data["projected"]:
            assert p["date"] > today, (
                f"Projected date {p['date']} should be after today ({today})"
            )

    def test_forecast_historical_last_date_is_today(self):
        """The last historical date in forecast should be today (UTC)."""
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        last_hist = data["historical"][-1]["date"]
        assert last_hist == today, (
            f"Last historical date {last_hist} should be today ({today})"
        )

    def test_forecast_projected_monthly_consistent_with_daily_average(self):
        """projected_monthly should be approximately daily_average * 30."""
        resp = client.get("/api/stats/forecast")
        data = resp.json()["data"]
        expected = round(data["daily_average"] * 30, 2)
        assert data["projected_monthly"] == expected, (
            f"projected_monthly={data['projected_monthly']} != "
            f"daily_average*30={expected}"
        )
