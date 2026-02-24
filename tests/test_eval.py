"""Tests for the evaluation & testing framework."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import yaml

from sandcastle.engine.eval import (
    AssertionDef,
    AssertionResult,
    CaseResult,
    EvalCase,
    EvalSuiteDef,
    SuiteResult,
    check_assertion,
    parse_eval_suite_string,
    run_eval_suite,
)


# ---------------------------------------------------------------------------
# TestEvalSuiteParsing
# ---------------------------------------------------------------------------


class TestEvalSuiteParsing:
    """Tests for parsing eval suite YAML."""

    def test_parse_basic_suite(self):
        yaml_content = """
workflow: summarize
description: "Basic summarize tests"
cases:
  - name: "short text"
    input:
      text: "Hello world"
    assertions:
      - type: not_empty
      - type: contains
        value: "summary"
"""
        suite = parse_eval_suite_string(yaml_content)
        assert suite.workflow == "summarize"
        assert suite.description == "Basic summarize tests"
        assert len(suite.cases) == 1
        assert suite.cases[0].name == "short text"
        assert suite.cases[0].input == {"text": "Hello world"}
        assert len(suite.cases[0].assertions) == 2
        assert suite.cases[0].assertions[0].type == "not_empty"
        assert suite.cases[0].assertions[1].type == "contains"
        assert suite.cases[0].assertions[1].value == "summary"

    def test_parse_all_assertion_types(self):
        yaml_content = """
workflow: test-workflow
cases:
  - name: "all types"
    input: {}
    assertions:
      - type: contains
        value: "hello"
      - type: not_contains
        value: "bad"
      - type: not_empty
      - type: equals
        value: "exact match"
      - type: regex_match
        value: "\\\\d+"
      - type: llm_judge
        criteria: "Is this good?"
        threshold: 0.8
      - type: schema_match
        value:
          properties:
            name:
              type: string
        threshold: 0.9
      - type: max_cost
        value: 0.05
      - type: max_duration
        value: 30
"""
        suite = parse_eval_suite_string(yaml_content)
        assertions = suite.cases[0].assertions
        assert len(assertions) == 9
        assert assertions[0].type == "contains"
        assert assertions[1].type == "not_contains"
        assert assertions[2].type == "not_empty"
        assert assertions[3].type == "equals"
        assert assertions[4].type == "regex_match"
        assert assertions[5].type == "llm_judge"
        assert assertions[5].criteria == "Is this good?"
        assert assertions[5].threshold == 0.8
        assert assertions[6].type == "schema_match"
        assert assertions[6].threshold == 0.9
        assert assertions[7].type == "max_cost"
        assert assertions[7].value == 0.05
        assert assertions[8].type == "max_duration"
        assert assertions[8].value == 30

    def test_parse_with_tags(self):
        yaml_content = """
workflow: summarize
cases:
  - name: "tagged case"
    input: { text: "test" }
    tags: [regression, smoke]
    assertions:
      - type: not_empty
"""
        suite = parse_eval_suite_string(yaml_content)
        assert suite.cases[0].tags == ["regression", "smoke"]

    def test_parse_with_concurrency(self):
        yaml_content = """
workflow: summarize
concurrency: 5
cases:
  - name: "case1"
    input: {}
"""
        suite = parse_eval_suite_string(yaml_content)
        assert suite.concurrency == 5

    def test_parse_missing_workflow_raises(self):
        yaml_content = """
description: "No workflow field"
cases:
  - name: "case1"
    input: {}
"""
        with pytest.raises(ValueError, match="workflow"):
            parse_eval_suite_string(yaml_content)

    def test_parse_invalid_yaml(self):
        with pytest.raises((ValueError, yaml.YAMLError)):
            parse_eval_suite_string("not: a: valid: yaml: [")

    def test_parse_non_mapping(self):
        with pytest.raises(ValueError, match="mapping"):
            parse_eval_suite_string("- just a list\n- of items")

    def test_parse_empty_cases(self):
        yaml_content = """
workflow: test
cases: []
"""
        suite = parse_eval_suite_string(yaml_content)
        assert suite.workflow == "test"
        assert len(suite.cases) == 0

    def test_parse_step_targeted_assertion(self):
        yaml_content = """
workflow: multi-step
cases:
  - name: "step target"
    input: {}
    assertions:
      - type: not_empty
        step: enrich
"""
        suite = parse_eval_suite_string(yaml_content)
        assert suite.cases[0].assertions[0].step == "enrich"

    def test_parse_default_threshold(self):
        yaml_content = """
workflow: test
cases:
  - name: "defaults"
    input: {}
    assertions:
      - type: llm_judge
        criteria: "quality"
"""
        suite = parse_eval_suite_string(yaml_content)
        assert suite.cases[0].assertions[0].threshold == 0.7


# ---------------------------------------------------------------------------
# TestAssertionChecks
# ---------------------------------------------------------------------------


class TestAssertionChecks:
    """Tests for individual assertion type checking."""

    @pytest.mark.asyncio
    async def test_contains_pass(self):
        a = AssertionDef(type="contains", value="hello")
        result = await check_assertion(a, "hello world")
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_contains_fail(self):
        a = AssertionDef(type="contains", value="xyz")
        result = await check_assertion(a, "hello world")
        assert result.passed is False
        assert "xyz" in result.message

    @pytest.mark.asyncio
    async def test_not_contains_pass(self):
        a = AssertionDef(type="not_contains", value="bad")
        result = await check_assertion(a, "good content")
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_not_contains_fail(self):
        a = AssertionDef(type="not_contains", value="bad")
        result = await check_assertion(a, "this is bad content")
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_not_empty_pass_string(self):
        a = AssertionDef(type="not_empty")
        result = await check_assertion(a, "content")
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_not_empty_pass_dict(self):
        a = AssertionDef(type="not_empty")
        result = await check_assertion(a, {"key": "value"})
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_not_empty_fail_none(self):
        a = AssertionDef(type="not_empty")
        result = await check_assertion(a, None)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_not_empty_fail_empty_string(self):
        a = AssertionDef(type="not_empty")
        result = await check_assertion(a, "")
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_not_empty_fail_empty_dict(self):
        a = AssertionDef(type="not_empty")
        result = await check_assertion(a, {})
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_equals_pass(self):
        a = AssertionDef(type="equals", value=42)
        result = await check_assertion(a, 42)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_equals_fail(self):
        a = AssertionDef(type="equals", value="expected")
        result = await check_assertion(a, "actual")
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_regex_match_pass(self):
        a = AssertionDef(type="regex_match", value=r"\d{3}-\d{4}")
        result = await check_assertion(a, "Phone: 555-1234")
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_regex_match_fail(self):
        a = AssertionDef(type="regex_match", value=r"^\d+$")
        result = await check_assertion(a, "not a number")
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_max_cost_pass(self):
        a = AssertionDef(type="max_cost", value=0.05)
        meta = {"cost_usd": 0.03}
        result = await check_assertion(a, "output", meta)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_max_cost_fail(self):
        a = AssertionDef(type="max_cost", value=0.05)
        meta = {"cost_usd": 0.10}
        result = await check_assertion(a, "output", meta)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_max_duration_pass(self):
        a = AssertionDef(type="max_duration", value=30)
        meta = {"duration_seconds": 15.0}
        result = await check_assertion(a, "output", meta)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_max_duration_fail(self):
        a = AssertionDef(type="max_duration", value=10)
        meta = {"duration_seconds": 15.0}
        result = await check_assertion(a, "output", meta)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_unknown_type(self):
        a = AssertionDef(type="unknown_assertion")
        result = await check_assertion(a, "output")
        assert result.passed is False
        assert "Unknown" in result.message

    @pytest.mark.asyncio
    async def test_step_targeted_assertion(self):
        a = AssertionDef(type="contains", value="enriched", step="enrich")
        step_outputs = {
            "scrape": "raw data",
            "enrich": "enriched data with details",
        }
        result = await check_assertion(a, "final output", step_outputs=step_outputs)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_contains_with_none_output(self):
        a = AssertionDef(type="contains", value="hello")
        result = await check_assertion(a, None)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_schema_match_pass(self):
        a = AssertionDef(
            type="schema_match",
            value={"properties": {"name": {}, "age": {}}},
            threshold=0.5,
        )
        result = await check_assertion(a, {"name": "John", "age": 30})
        assert result.passed is True
        assert result.score == 1.0

    @pytest.mark.asyncio
    async def test_schema_match_fail(self):
        a = AssertionDef(
            type="schema_match",
            value={"properties": {"name": {}, "age": {}, "email": {}}},
            threshold=0.9,
        )
        result = await check_assertion(a, {"name": "John"})
        assert result.passed is False


# ---------------------------------------------------------------------------
# TestEvalExecution
# ---------------------------------------------------------------------------


class TestEvalExecution:
    """Tests for eval suite execution with mocked workflow runs."""

    @pytest.mark.asyncio
    @patch("sandcastle.engine.eval.run_eval_case")
    async def test_run_suite_all_pass(self, mock_run_case):
        mock_run_case.return_value = CaseResult(
            name="test",
            passed=True,
            assertions=[
                AssertionResult(type="not_empty", passed=True),
            ],
            run_id="run-123",
            cost_usd=0.05,
            duration_seconds=5.0,
            output="output data",
        )

        suite = EvalSuiteDef(
            workflow="test-workflow",
            cases=[
                EvalCase(name="case1", input={"text": "hello"}),
                EvalCase(name="case2", input={"text": "world"}),
            ],
        )

        result = await run_eval_suite(suite)

        assert result.total == 2
        assert result.passed == 2
        assert result.failed == 0
        assert result.pass_rate == 1.0
        assert result.total_cost_usd == 0.10

    @pytest.mark.asyncio
    @patch("sandcastle.engine.eval.run_eval_case")
    async def test_run_suite_mixed_results(self, mock_run_case):
        results = [
            CaseResult(
                name="pass",
                passed=True,
                assertions=[AssertionResult(type="not_empty", passed=True)],
                cost_usd=0.03,
                duration_seconds=3.0,
            ),
            CaseResult(
                name="fail",
                passed=False,
                assertions=[AssertionResult(type="contains", passed=False, message="not found")],
                cost_usd=0.02,
                duration_seconds=2.0,
                error="assertion failed",
            ),
        ]
        mock_run_case.side_effect = results

        suite = EvalSuiteDef(
            workflow="test",
            cases=[
                EvalCase(name="pass", input={}),
                EvalCase(name="fail", input={}),
            ],
        )

        result = await run_eval_suite(suite)

        assert result.total == 2
        assert result.passed == 1
        assert result.failed == 1
        assert result.pass_rate == 0.5

    @pytest.mark.asyncio
    @patch("sandcastle.engine.eval.run_eval_case")
    async def test_run_suite_tag_filter(self, mock_run_case):
        mock_run_case.return_value = CaseResult(
            name="tagged",
            passed=True,
            assertions=[],
            cost_usd=0.01,
            duration_seconds=1.0,
        )

        suite = EvalSuiteDef(
            workflow="test",
            cases=[
                EvalCase(name="smoke", input={}, tags=["smoke"]),
                EvalCase(name="full", input={}, tags=["full"]),
                EvalCase(name="both", input={}, tags=["smoke", "full"]),
            ],
        )

        result = await run_eval_suite(suite, tag_filter=["smoke"])

        assert result.total == 2  # "smoke" and "both"
        assert mock_run_case.call_count == 2

    @pytest.mark.asyncio
    @patch("sandcastle.engine.eval.run_eval_case")
    async def test_run_suite_exception_handling(self, mock_run_case):
        mock_run_case.side_effect = RuntimeError("workflow not found")

        suite = EvalSuiteDef(
            workflow="missing",
            cases=[EvalCase(name="case1", input={})],
        )

        result = await run_eval_suite(suite)

        assert result.total == 1
        assert result.failed == 1
        assert result.cases[0].passed is False
        assert "workflow not found" in result.cases[0].error

    def test_suite_result_cost_aggregation(self):
        suite_result = SuiteResult(
            suite_name="test",
            workflow="test",
            total=3,
            passed=2,
            failed=1,
            pass_rate=2 / 3,
            total_cost_usd=0.15,
            total_duration_seconds=30.0,
            cases=[
                CaseResult(name="c1", passed=True, cost_usd=0.05, duration_seconds=10.0),
                CaseResult(name="c2", passed=True, cost_usd=0.05, duration_seconds=10.0),
                CaseResult(name="c3", passed=False, cost_usd=0.05, duration_seconds=10.0),
            ],
        )
        assert suite_result.total_cost_usd == 0.15
        assert suite_result.pass_rate == pytest.approx(0.6667, rel=1e-2)


class TestEvalDataclasses:
    """Test dataclass defaults and structure."""

    def test_assertion_def_defaults(self):
        a = AssertionDef(type="not_empty")
        assert a.value is None
        assert a.criteria == ""
        assert a.threshold == 0.7
        assert a.step is None

    def test_eval_case_defaults(self):
        c = EvalCase(name="test", input={})
        assert c.assertions == []
        assert c.tags == []

    def test_eval_suite_def_defaults(self):
        s = EvalSuiteDef(workflow="test", cases=[])
        assert s.description == ""
        assert s.concurrency == 1

    def test_assertion_result_fields(self):
        r = AssertionResult(
            type="llm_judge",
            passed=True,
            expected=">= 0.7",
            actual="0.85",
            message="",
            score=0.85,
        )
        assert r.score == 0.85

    def test_case_result_defaults(self):
        c = CaseResult(name="test", passed=True)
        assert c.assertions == []
        assert c.run_id is None
        assert c.cost_usd == 0.0
        assert c.error is None

    def test_suite_result_defaults(self):
        s = SuiteResult(suite_name="test", workflow="test")
        assert s.total == 0
        assert s.passed == 0
        assert s.cases == []
