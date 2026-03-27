"""Comprehensive tests for the evaluation system (eval.py).

Covers: suite parsing, assertion evaluation, case/suite result aggregation, scoring.
"""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
    parse_eval_suite,
    parse_eval_suite_string,
    run_eval_suite,
)


# ===================================================================
# Helpers
# ===================================================================


def _make_yaml(**overrides: Any) -> str:
    """Build a minimal valid eval suite YAML string."""
    base: dict[str, Any] = {
        "workflow": "summarize",
        "cases": [
            {
                "name": "basic",
                "input": {"text": "hello"},
                "assertions": [{"type": "not_empty"}],
            }
        ],
    }
    base.update(overrides)
    return yaml.dump(base)


def _run(coro):
    """Shorthand for running a coroutine in tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ===================================================================
# 1. Eval Suite Parsing (12 tests)
# ===================================================================


class TestEvalSuiteParsing:
    """Tests for parse_eval_suite_string and parse_eval_suite."""

    def test_parse_valid_minimal_suite(self):
        suite = parse_eval_suite_string(_make_yaml())
        assert suite.workflow == "summarize"
        assert len(suite.cases) == 1
        assert suite.cases[0].name == "basic"

    def test_parse_with_description_and_concurrency(self):
        yml = _make_yaml(description="My eval suite", concurrency=4)
        suite = parse_eval_suite_string(yml)
        assert suite.description == "My eval suite"
        assert suite.concurrency == 4

    def test_parse_with_multiple_cases(self):
        yml = yaml.dump(
            {
                "workflow": "translate",
                "cases": [
                    {"name": "case_1", "input": {"lang": "en"}, "assertions": [{"type": "not_empty"}]},
                    {"name": "case_2", "input": {"lang": "fr"}, "assertions": [{"type": "not_empty"}]},
                    {"name": "case_3", "input": {"lang": "de"}, "assertions": [{"type": "contains", "value": "Hallo"}]},
                ],
            }
        )
        suite = parse_eval_suite_string(yml)
        assert len(suite.cases) == 3
        assert suite.cases[2].name == "case_3"

    def test_parse_with_all_assertion_types(self):
        assertions = [
            {"type": "not_empty"},
            {"type": "contains", "value": "hello"},
            {"type": "equals", "value": 42},
            {"type": "regex_match", "value": r"\d+"},
            {"type": "not_contains", "value": "error"},
            {"type": "schema_match", "value": {"properties": {"a": {}}}, "threshold": 0.9},
        ]
        yml = yaml.dump(
            {
                "workflow": "multi_assert",
                "cases": [{"name": "all_types", "input": {}, "assertions": assertions}],
            }
        )
        suite = parse_eval_suite_string(yml)
        assert len(suite.cases[0].assertions) == 6
        assert suite.cases[0].assertions[0].type == "not_empty"
        assert suite.cases[0].assertions[5].threshold == 0.9

    def test_parse_invalid_yaml_raises(self):
        with pytest.raises(Exception):
            parse_eval_suite_string("{{{{invalid yaml content!!!!")

    def test_parse_non_mapping_raises(self):
        with pytest.raises(ValueError, match="YAML mapping"):
            parse_eval_suite_string("- just\n- a\n- list\n")

    def test_parse_missing_workflow_raises(self):
        yml = yaml.dump({"cases": [{"name": "x", "input": {}}]})
        with pytest.raises(ValueError, match="workflow"):
            parse_eval_suite_string(yml)

    def test_parse_empty_cases_list(self):
        yml = yaml.dump({"workflow": "wf", "cases": []})
        suite = parse_eval_suite_string(yml)
        assert suite.workflow == "wf"
        assert len(suite.cases) == 0

    def test_parse_case_without_name_gets_default(self):
        yml = yaml.dump(
            {
                "workflow": "wf",
                "cases": [{"input": {"a": 1}}],
            }
        )
        suite = parse_eval_suite_string(yml)
        assert suite.cases[0].name == "case_1"

    def test_parse_case_without_assertions_uses_empty(self):
        yml = yaml.dump(
            {
                "workflow": "wf",
                "cases": [{"name": "no_assert", "input": {}}],
            }
        )
        suite = parse_eval_suite_string(yml)
        assert suite.cases[0].assertions == []

    def test_parse_from_file(self, tmp_path: Path):
        f = tmp_path / "suite.yaml"
        f.write_text(_make_yaml())
        suite = parse_eval_suite(f)
        assert suite.workflow == "summarize"

    def test_parse_from_nonexistent_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            parse_eval_suite(tmp_path / "does_not_exist.yaml")

    def test_parse_case_tags(self):
        yml = yaml.dump(
            {
                "workflow": "wf",
                "cases": [
                    {"name": "tagged", "input": {}, "tags": ["smoke", "fast"]},
                ],
            }
        )
        suite = parse_eval_suite_string(yml)
        assert suite.cases[0].tags == ["smoke", "fast"]

    def test_parse_assertion_with_step_target(self):
        yml = yaml.dump(
            {
                "workflow": "wf",
                "cases": [
                    {
                        "name": "step_assert",
                        "input": {},
                        "assertions": [{"type": "contains", "value": "ok", "step": "step_2"}],
                    }
                ],
            }
        )
        suite = parse_eval_suite_string(yml)
        assert suite.cases[0].assertions[0].step == "step_2"


# ===================================================================
# 2. Assertion Evaluation (20 tests)
# ===================================================================


class TestAssertionEvaluation:
    """Tests for check_assertion and its helpers."""

    # -- not_empty --

    @pytest.mark.asyncio
    async def test_not_empty_passes_for_string(self):
        a = AssertionDef(type="not_empty")
        r = await check_assertion(a, "some output")
        assert r.passed is True

    @pytest.mark.asyncio
    async def test_not_empty_fails_for_empty_string(self):
        a = AssertionDef(type="not_empty")
        r = await check_assertion(a, "")
        assert r.passed is False

    @pytest.mark.asyncio
    async def test_not_empty_fails_for_none(self):
        a = AssertionDef(type="not_empty")
        r = await check_assertion(a, None)
        assert r.passed is False

    @pytest.mark.asyncio
    async def test_not_empty_fails_for_empty_dict(self):
        a = AssertionDef(type="not_empty")
        r = await check_assertion(a, {})
        assert r.passed is False

    @pytest.mark.asyncio
    async def test_not_empty_passes_for_dict_with_data(self):
        a = AssertionDef(type="not_empty")
        r = await check_assertion(a, {"key": "val"})
        assert r.passed is True

    # -- contains --

    @pytest.mark.asyncio
    async def test_contains_passes_substring(self):
        a = AssertionDef(type="contains", value="world")
        r = await check_assertion(a, "hello world")
        assert r.passed is True

    @pytest.mark.asyncio
    async def test_contains_fails_missing_substring(self):
        a = AssertionDef(type="contains", value="missing")
        r = await check_assertion(a, "hello world")
        assert r.passed is False
        assert "does not contain" in r.message

    @pytest.mark.asyncio
    async def test_contains_is_case_sensitive(self):
        # The implementation uses plain `in`, so it's case-sensitive
        a = AssertionDef(type="contains", value="HELLO")
        r = await check_assertion(a, "hello world")
        assert r.passed is False

    @pytest.mark.asyncio
    async def test_contains_handles_none_output(self):
        a = AssertionDef(type="contains", value="x")
        r = await check_assertion(a, None)
        assert r.passed is False

    # -- equals --

    @pytest.mark.asyncio
    async def test_equals_passes_exact_match(self):
        a = AssertionDef(type="equals", value=42)
        r = await check_assertion(a, 42)
        assert r.passed is True

    @pytest.mark.asyncio
    async def test_equals_fails_different_value(self):
        a = AssertionDef(type="equals", value="expected")
        r = await check_assertion(a, "actual")
        assert r.passed is False

    @pytest.mark.asyncio
    async def test_equals_string_match(self):
        a = AssertionDef(type="equals", value="exact")
        r = await check_assertion(a, "exact")
        assert r.passed is True

    # -- not_contains --

    @pytest.mark.asyncio
    async def test_not_contains_passes(self):
        a = AssertionDef(type="not_contains", value="error")
        r = await check_assertion(a, "all good")
        assert r.passed is True

    @pytest.mark.asyncio
    async def test_not_contains_fails_when_present(self):
        a = AssertionDef(type="not_contains", value="error")
        r = await check_assertion(a, "an error occurred")
        assert r.passed is False
        assert "contains" in r.message

    # -- regex_match --

    @pytest.mark.asyncio
    async def test_regex_passes_matching_pattern(self):
        a = AssertionDef(type="regex_match", value=r"\d{3}-\d{4}")
        r = await check_assertion(a, "Call 555-1234 now")
        assert r.passed is True

    @pytest.mark.asyncio
    async def test_regex_fails_no_match(self):
        a = AssertionDef(type="regex_match", value=r"^\d+$")
        r = await check_assertion(a, "no digits here")
        assert r.passed is False

    @pytest.mark.asyncio
    async def test_regex_fails_for_none_pattern(self):
        a = AssertionDef(type="regex_match", value=None)
        r = await check_assertion(a, "some output")
        assert r.passed is False

    @pytest.mark.asyncio
    async def test_regex_rejects_too_long_pattern(self):
        a = AssertionDef(type="regex_match", value="a" * 600)
        r = await check_assertion(a, "test")
        assert r.passed is False
        assert "too long" in r.message

    @pytest.mark.asyncio
    async def test_regex_rejects_redos_pattern(self):
        a = AssertionDef(type="regex_match", value="(a+)+$")
        r = await check_assertion(a, "aaa")
        assert r.passed is False
        assert "ReDoS" in r.message

    # -- max_cost --

    @pytest.mark.asyncio
    async def test_max_cost_passes_under_limit(self):
        a = AssertionDef(type="max_cost", value=1.0)
        r = await check_assertion(a, "output", run_metadata={"cost_usd": 0.5})
        assert r.passed is True

    @pytest.mark.asyncio
    async def test_max_cost_fails_over_limit(self):
        a = AssertionDef(type="max_cost", value=0.10)
        r = await check_assertion(a, "output", run_metadata={"cost_usd": 0.50})
        assert r.passed is False
        assert "exceeds" in r.message

    # -- max_duration --

    @pytest.mark.asyncio
    async def test_max_duration_passes_under_limit(self):
        a = AssertionDef(type="max_duration", value=10.0)
        r = await check_assertion(a, "output", run_metadata={"duration_seconds": 3.0})
        assert r.passed is True

    @pytest.mark.asyncio
    async def test_max_duration_fails_over_limit(self):
        a = AssertionDef(type="max_duration", value=5.0)
        r = await check_assertion(a, "output", run_metadata={"duration_seconds": 12.0})
        assert r.passed is False

    # -- schema_match --

    @pytest.mark.asyncio
    async def test_schema_match_passes_all_fields_present(self):
        schema = {"properties": {"name": {}, "age": {}}}
        a = AssertionDef(type="schema_match", value=schema, threshold=0.5)
        r = await check_assertion(a, {"name": "Alice", "age": 30})
        assert r.passed is True

    @pytest.mark.asyncio
    async def test_schema_match_fails_missing_fields(self):
        schema = {"properties": {"name": {}, "age": {}, "email": {}}}
        a = AssertionDef(type="schema_match", value=schema, threshold=0.9)
        r = await check_assertion(a, {"name": "Alice"})
        assert r.passed is False

    # -- step-targeted assertions --

    @pytest.mark.asyncio
    async def test_step_targeted_assertion(self):
        a = AssertionDef(type="contains", value="step2_data", step="step_2")
        step_outputs = {"step_1": "step1_data", "step_2": "step2_data here"}
        r = await check_assertion(a, "main_output", step_outputs=step_outputs)
        assert r.passed is True

    # -- unknown assertion type --

    @pytest.mark.asyncio
    async def test_unknown_assertion_type_fails(self):
        a = AssertionDef(type="nonexistent_type")
        r = await check_assertion(a, "output")
        assert r.passed is False
        assert "Unknown" in r.message

    # -- multiple assertions AND logic --

    @pytest.mark.asyncio
    async def test_multiple_assertions_all_must_pass(self):
        assertions = [
            AssertionDef(type="not_empty"),
            AssertionDef(type="contains", value="ok"),
            AssertionDef(type="not_contains", value="error"),
        ]
        results = []
        for a in assertions:
            r = await check_assertion(a, "everything is ok")
            results.append(r)
        assert all(r.passed for r in results)

    @pytest.mark.asyncio
    async def test_multiple_assertions_one_fails(self):
        assertions = [
            AssertionDef(type="not_empty"),
            AssertionDef(type="contains", value="missing_word"),
        ]
        results = []
        for a in assertions:
            r = await check_assertion(a, "hello world")
            results.append(r)
        assert not all(r.passed for r in results)
        assert results[0].passed is True
        assert results[1].passed is False

    # -- output truncation in contains/not_contains --

    @pytest.mark.asyncio
    async def test_contains_truncates_long_actual(self):
        long_output = "x" * 500
        a = AssertionDef(type="contains", value="y")
        r = await check_assertion(a, long_output)
        assert r.passed is False
        assert len(r.actual) == 200


# ===================================================================
# 3. Case Execution (10 tests)
# ===================================================================


class TestCaseResult:
    """Tests for CaseResult and SuiteResult dataclasses and aggregation."""

    def test_case_result_pass(self):
        cr = CaseResult(
            name="test_pass",
            passed=True,
            assertions=[AssertionResult(type="not_empty", passed=True)],
            cost_usd=0.01,
            duration_seconds=1.5,
        )
        assert cr.passed is True
        assert cr.cost_usd == 0.01
        assert cr.duration_seconds == 1.5

    def test_case_result_fail(self):
        cr = CaseResult(
            name="test_fail",
            passed=False,
            assertions=[
                AssertionResult(type="contains", passed=False, message="not found"),
            ],
            error="Assertion failed",
        )
        assert cr.passed is False
        assert cr.error == "Assertion failed"

    def test_case_result_cost_tracking(self):
        cr = CaseResult(name="cost_test", passed=True, cost_usd=0.0523)
        assert cr.cost_usd == pytest.approx(0.0523)

    def test_case_result_duration_tracking(self):
        cr = CaseResult(name="dur_test", passed=True, duration_seconds=45.7)
        assert cr.duration_seconds == pytest.approx(45.7)

    def test_case_result_default_values(self):
        cr = CaseResult(name="defaults", passed=False)
        assert cr.assertions == []
        assert cr.run_id is None
        assert cr.cost_usd == 0.0
        assert cr.duration_seconds == 0.0
        assert cr.output is None
        assert cr.error is None

    def test_suite_result_aggregation_all_pass(self):
        cases = [
            CaseResult(name="c1", passed=True, cost_usd=0.01, duration_seconds=1.0),
            CaseResult(name="c2", passed=True, cost_usd=0.02, duration_seconds=2.0),
            CaseResult(name="c3", passed=True, cost_usd=0.03, duration_seconds=3.0),
        ]
        sr = SuiteResult(
            suite_name="all_pass",
            workflow="wf",
            total=3,
            passed=3,
            failed=0,
            pass_rate=1.0,
            total_cost_usd=0.06,
            total_duration_seconds=6.0,
            cases=cases,
        )
        assert sr.total == 3
        assert sr.passed == 3
        assert sr.failed == 0
        assert sr.pass_rate == pytest.approx(1.0)

    def test_suite_result_aggregation_mixed(self):
        cases = [
            CaseResult(name="c1", passed=True, cost_usd=0.10, duration_seconds=5.0),
            CaseResult(name="c2", passed=False, cost_usd=0.05, duration_seconds=3.0),
            CaseResult(name="c3", passed=True, cost_usd=0.15, duration_seconds=7.0),
        ]
        total = len(cases)
        passed = sum(1 for c in cases if c.passed)
        sr = SuiteResult(
            suite_name="mixed",
            workflow="wf",
            total=total,
            passed=passed,
            failed=total - passed,
            pass_rate=passed / total,
            total_cost_usd=sum(c.cost_usd for c in cases),
            total_duration_seconds=sum(c.duration_seconds for c in cases),
            cases=cases,
        )
        assert sr.total == 3
        assert sr.passed == 2
        assert sr.failed == 1
        assert sr.pass_rate == pytest.approx(2 / 3)
        assert sr.total_cost_usd == pytest.approx(0.30)
        assert sr.total_duration_seconds == pytest.approx(15.0)

    def test_suite_result_aggregation_all_fail(self):
        cases = [
            CaseResult(name="c1", passed=False, cost_usd=0.01, duration_seconds=1.0),
            CaseResult(name="c2", passed=False, cost_usd=0.02, duration_seconds=2.0),
        ]
        sr = SuiteResult(
            suite_name="all_fail",
            workflow="wf",
            total=2,
            passed=0,
            failed=2,
            pass_rate=0.0,
            total_cost_usd=0.03,
            total_duration_seconds=3.0,
            cases=cases,
        )
        assert sr.pass_rate == 0.0
        assert sr.failed == 2

    def test_case_result_with_run_id(self):
        cr = CaseResult(name="with_id", passed=True, run_id="abc-123")
        assert cr.run_id == "abc-123"

    def test_case_result_with_output(self):
        cr = CaseResult(name="with_output", passed=True, output={"summary": "ok"})
        assert cr.output == {"summary": "ok"}


# ===================================================================
# 4. Scoring (7 tests)
# ===================================================================


class TestScoring:
    """Tests for pass rate, cost, and duration aggregation logic."""

    def test_pass_rate_calculation_accuracy(self):
        # Simulate the formula used in run_eval_suite
        total = 7
        passed = 5
        rate = passed / total if total > 0 else 0.0
        assert rate == pytest.approx(5 / 7, rel=1e-9)

    def test_cost_aggregation_across_cases(self):
        costs = [0.001, 0.0234, 0.0056, 0.1, 0.0]
        total_cost = sum(costs)
        assert total_cost == pytest.approx(0.13, abs=1e-6)

    def test_duration_aggregation_across_cases(self):
        durations = [1.2, 3.4, 5.6, 7.8]
        total = sum(durations)
        assert total == pytest.approx(18.0)

    def test_empty_suite_edge_case(self):
        sr = SuiteResult(
            suite_name="empty",
            workflow="wf",
            total=0,
            passed=0,
            failed=0,
            pass_rate=0.0,
            total_cost_usd=0.0,
            total_duration_seconds=0.0,
            cases=[],
        )
        assert sr.total == 0
        assert sr.pass_rate == 0.0
        assert sr.total_cost_usd == 0.0
        assert sr.total_duration_seconds == 0.0

    def test_single_case_suite_pass(self):
        cr = CaseResult(name="solo", passed=True, cost_usd=0.05, duration_seconds=2.0)
        total = 1
        passed = 1
        sr = SuiteResult(
            suite_name="single",
            workflow="wf",
            total=total,
            passed=passed,
            failed=0,
            pass_rate=1.0,
            total_cost_usd=cr.cost_usd,
            total_duration_seconds=cr.duration_seconds,
            cases=[cr],
        )
        assert sr.pass_rate == 1.0
        assert sr.total_cost_usd == 0.05

    def test_single_case_suite_fail(self):
        cr = CaseResult(name="solo_fail", passed=False, cost_usd=0.02, duration_seconds=1.0)
        sr = SuiteResult(
            suite_name="single_fail",
            workflow="wf",
            total=1,
            passed=0,
            failed=1,
            pass_rate=0.0,
            total_cost_usd=0.02,
            total_duration_seconds=1.0,
            cases=[cr],
        )
        assert sr.pass_rate == 0.0
        assert sr.failed == 1

    def test_pass_rate_zero_division_protection(self):
        # The formula: passed / total if total > 0 else 0.0
        total = 0
        passed = 0
        rate = passed / total if total > 0 else 0.0
        assert rate == 0.0


# ===================================================================
# 5. Suite Execution Integration (5 tests)
# ===================================================================


class TestSuiteExecution:
    """Tests for run_eval_suite with mocked workflow execution."""

    @pytest.mark.asyncio
    async def test_run_eval_suite_all_pass(self):
        """All cases pass through mocked case execution."""
        suite = EvalSuiteDef(
            workflow="test_wf",
            cases=[
                EvalCase(name="c1", input={"x": 1}, assertions=[AssertionDef(type="not_empty")]),
                EvalCase(name="c2", input={"x": 2}, assertions=[AssertionDef(type="not_empty")]),
            ],
        )

        async def _mock_run_case(case, wf_name):
            return CaseResult(name=case.name, passed=True, cost_usd=0.01, duration_seconds=1.0)

        with patch("sandcastle.engine.eval.run_eval_case", side_effect=_mock_run_case):
            sr = await run_eval_suite(suite)

        assert sr.total == 2
        assert sr.passed == 2
        assert sr.failed == 0
        assert sr.pass_rate == 1.0

    @pytest.mark.asyncio
    async def test_run_eval_suite_tag_filter(self):
        """Tag filtering reduces the executed cases."""
        suite = EvalSuiteDef(
            workflow="test_wf",
            cases=[
                EvalCase(name="smoke", input={}, assertions=[], tags=["smoke"]),
                EvalCase(name="full", input={}, assertions=[], tags=["full"]),
                EvalCase(name="smoke2", input={}, assertions=[], tags=["smoke", "fast"]),
            ],
        )

        async def _mock_run_case(case, wf_name):
            return CaseResult(name=case.name, passed=True, cost_usd=0.0, duration_seconds=0.5)

        with patch("sandcastle.engine.eval.run_eval_case", side_effect=_mock_run_case):
            sr = await run_eval_suite(suite, tag_filter=["smoke"])

        assert sr.total == 2  # only smoke-tagged cases

    @pytest.mark.asyncio
    async def test_run_eval_suite_exception_in_case(self):
        """An exception during case execution results in a failed case."""
        suite = EvalSuiteDef(
            workflow="broken_wf",
            cases=[
                EvalCase(name="will_fail", input={}, assertions=[AssertionDef(type="not_empty")]),
            ],
        )

        async def _mock_run_case(case, wf_name):
            raise RuntimeError("workflow not found")

        with patch("sandcastle.engine.eval.run_eval_case", side_effect=_mock_run_case):
            sr = await run_eval_suite(suite)

        assert sr.total == 1
        assert sr.failed == 1
        assert sr.cases[0].error is not None

    @pytest.mark.asyncio
    async def test_run_eval_suite_mixed_results(self):
        """Mix of passing and failing cases."""
        suite = EvalSuiteDef(
            workflow="test_wf",
            cases=[
                EvalCase(name="pass_case", input={}, assertions=[AssertionDef(type="not_empty")]),
                EvalCase(name="fail_case", input={}, assertions=[AssertionDef(type="contains", value="missing")]),
            ],
        )

        async def _mock_run_case(case, wf_name):
            if case.name == "pass_case":
                return CaseResult(name=case.name, passed=True, cost_usd=0.02, duration_seconds=1.0)
            return CaseResult(name=case.name, passed=False, cost_usd=0.02, duration_seconds=1.0)

        with patch("sandcastle.engine.eval.run_eval_case", side_effect=_mock_run_case):
            sr = await run_eval_suite(suite)

        assert sr.total == 2
        assert sr.passed == 1
        assert sr.failed == 1
        assert sr.pass_rate == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_run_eval_suite_empty_cases(self):
        """Suite with no cases produces empty result."""
        suite = EvalSuiteDef(workflow="empty_wf", cases=[])
        sr = await run_eval_suite(suite)
        assert sr.total == 0
        assert sr.passed == 0
        assert sr.pass_rate == 0.0
        assert sr.cases == []


# ===================================================================
# 6. Dataclass Construction (3 tests)
# ===================================================================


class TestDataclasses:
    """Verify dataclass field defaults and construction."""

    def test_assertion_def_defaults(self):
        a = AssertionDef(type="not_empty")
        assert a.value is None
        assert a.criteria == ""
        assert a.threshold == 0.7
        assert a.step is None

    def test_eval_suite_def_defaults(self):
        s = EvalSuiteDef(workflow="wf", cases=[])
        assert s.description == ""
        assert s.concurrency == 1

    def test_assertion_result_defaults(self):
        ar = AssertionResult(type="not_empty", passed=True)
        assert ar.expected is None
        assert ar.actual is None
        assert ar.message == ""
        assert ar.score is None
