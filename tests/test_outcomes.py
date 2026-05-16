"""Tests for the Anthropic Outcomes API client and composite aggregator.

Uses unittest.mock to patch httpx.AsyncClient so no network calls are made.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from sandcastle.engine.outcomes import (
    DEFAULT_BETA_HEADER,
    DEFINE_OUTCOME_EVENT_TYPE,
    OUTCOME_EVAL_END_TYPE,
    OUTCOME_EVAL_START_TYPE,
    AnthropicOutcomesClient,
    OutcomeDefinition,
    OutcomeEvaluation,
    OutcomesAPIError,
    OutcomeValidationError,
    aggregate_outcomes,
    build_define_outcome_event,
    parse_outcome_evaluation,
)


# ---------------------------------------------------------------------------
# httpx mocking helpers
# ---------------------------------------------------------------------------
def _make_response(
    status: int = 200,
    json_body: Any = None,
    text: str = "",
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.text = text or ""
    if json_body is None:
        json_body = {}
    resp.json = MagicMock(return_value=json_body)
    return resp


class _CapturingClient:
    def __init__(self, calls: list[dict[str, Any]], response: MagicMock) -> None:
        self._calls = calls
        self._response = response

    async def __aenter__(self) -> "_CapturingClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def request(self, method: str, url: str, **kwargs: Any) -> MagicMock:
        self._calls.append({"method": method, "url": url, **kwargs})
        return self._response


def _patch_httpx(response: MagicMock) -> tuple[Any, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    def _factory(*args: Any, **kwargs: Any) -> _CapturingClient:
        return _CapturingClient(calls, response)

    return patch("sandcastle.engine.outcomes.httpx.AsyncClient", _factory), calls


# ---------------------------------------------------------------------------
# build_define_outcome_event
# ---------------------------------------------------------------------------
def test_build_define_outcome_event_minimal_shape() -> None:
    definition = OutcomeDefinition(
        name="answers_question",
        description="Final response must address the user's question.",
        success_criteria=["Mentions the topic", "Includes a citation"],
    )
    event = build_define_outcome_event(definition)
    assert event["type"] == DEFINE_OUTCOME_EVENT_TYPE
    assert event["type"] == "user.define_outcome"
    outcome = event["outcome"]
    assert outcome["name"] == "answers_question"
    assert outcome["description"].startswith("Final response")
    assert outcome["success_criteria"] == [
        "Mentions the topic",
        "Includes a citation",
    ]
    assert outcome["weight"] == 1.0
    # Default model omission keeps the platform free to pick a judge.
    assert "model" not in outcome


def test_build_define_outcome_event_includes_model_override() -> None:
    definition = OutcomeDefinition(
        name="safe_output",
        description="Output must not contain PII.",
        success_criteria=["No emails", "No phone numbers"],
        weight=2.5,
        model="claude-opus-4-7",
    )
    event = build_define_outcome_event(definition)
    assert event["outcome"]["weight"] == 2.5
    assert event["outcome"]["model"] == "claude-opus-4-7"


# ---------------------------------------------------------------------------
# parse_outcome_evaluation
# ---------------------------------------------------------------------------
def test_parse_outcome_evaluation_returns_none_for_wrong_type() -> None:
    start_event = {
        "type": OUTCOME_EVAL_START_TYPE,
        "outcome": {"name": "answers_question"},
    }
    assert parse_outcome_evaluation(start_event) is None
    assert parse_outcome_evaluation({"type": "message.delta"}) is None
    assert parse_outcome_evaluation({}) is None


def test_parse_outcome_evaluation_parses_full_event() -> None:
    event = {
        "type": OUTCOME_EVAL_END_TYPE,
        "outcome": {
            "name": "answers_question",
            "passed": True,
            "score": 0.92,
            "reasoning": "Cited two sources and answered directly.",
            "evaluator_model": "claude-sonnet-4-7",
            "started_at": "2026-05-10T08:00:00Z",
            "completed_at": "2026-05-10T08:00:04Z",
            "cost_usd": 0.0123,
        },
    }
    parsed = parse_outcome_evaluation(event)
    assert isinstance(parsed, OutcomeEvaluation)
    assert parsed.outcome_name == "answers_question"
    assert parsed.passed is True
    assert parsed.score == 0.92
    assert parsed.reasoning.startswith("Cited")
    assert parsed.evaluator_model == "claude-sonnet-4-7"
    assert isinstance(parsed.started_at, datetime)
    assert isinstance(parsed.completed_at, datetime)
    assert parsed.cost_usd == 0.0123


# ---------------------------------------------------------------------------
# aggregate_outcomes
# ---------------------------------------------------------------------------
def _ev(name: str, passed: bool, cost: float = 0.01) -> OutcomeEvaluation:
    now = datetime.now(tz=timezone.utc)
    return OutcomeEvaluation(
        outcome_name=name,
        passed=passed,
        score=1.0 if passed else 0.0,
        reasoning="",
        evaluator_model="claude-sonnet-4-7",
        started_at=now,
        completed_at=now,
        cost_usd=cost,
    )


def test_aggregate_outcomes_equal_weights() -> None:
    evaluations = [
        _ev("a", True, 0.01),
        _ev("b", False, 0.02),
        _ev("c", True, 0.03),
        _ev("d", True, 0.04),
    ]
    result = aggregate_outcomes(evaluations)
    # 3 of 4 passed with equal weights -> 0.75
    assert result["composite_score"] == pytest.approx(0.75)
    assert result["pass_count"] == 3
    assert result["fail_count"] == 1
    assert result["total_cost_usd"] == pytest.approx(0.10)
    assert result["evaluated"] == 4


def test_aggregate_outcomes_custom_weights() -> None:
    evaluations = [
        _ev("critical", True),
        _ev("nice_to_have", False),
    ]
    result = aggregate_outcomes(
        evaluations, weights={"critical": 4.0, "nice_to_have": 1.0}
    )
    # passed * weight / total_weight = (1 * 4 + 0 * 1) / 5 = 0.8
    assert result["composite_score"] == pytest.approx(0.8)
    assert result["weights_used"] == {"critical": 4.0, "nice_to_have": 1.0}


def test_aggregate_outcomes_empty_list() -> None:
    result = aggregate_outcomes([])
    assert result["composite_score"] == 0.0
    assert result["pass_count"] == 0
    assert result["fail_count"] == 0
    assert result["total_cost_usd"] == 0.0
    assert result["evaluated"] == 0
    assert result["weights_used"] == {}


# ---------------------------------------------------------------------------
# OutcomeValidationError
# ---------------------------------------------------------------------------
def test_outcome_validation_error_empty_success_criteria() -> None:
    with pytest.raises(OutcomeValidationError):
        OutcomeDefinition(
            name="x",
            description="d",
            success_criteria=[],
        )


def test_outcome_validation_error_non_positive_weight() -> None:
    with pytest.raises(OutcomeValidationError):
        OutcomeDefinition(
            name="x",
            description="d",
            success_criteria=["ok"],
            weight=0.0,
        )
    with pytest.raises(OutcomeValidationError):
        OutcomeDefinition(
            name="x",
            description="d",
            success_criteria=["ok"],
            weight=-1.0,
        )


# ---------------------------------------------------------------------------
# AnthropicOutcomesClient
# ---------------------------------------------------------------------------
@pytest.fixture
def client() -> AnthropicOutcomesClient:
    return AnthropicOutcomesClient(api_key="sk-test-outcomes")


@pytest.mark.asyncio
async def test_define_outcome_posts_correct_body_and_beta_header(
    client: AnthropicOutcomesClient,
) -> None:
    response = _make_response(
        status=200, json_body={"id": "evt_123", "type": "user.define_outcome"}
    )
    patcher, calls = _patch_httpx(response)
    with patcher:
        definition = OutcomeDefinition(
            name="answers_question",
            description="Address the user's question.",
            success_criteria=["Cites a source"],
            weight=2.0,
        )
        result = await client.define_outcome("sess_abc", definition)
    assert result == {"id": "evt_123", "type": "user.define_outcome"}
    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/v1/sessions/sess_abc/events")
    headers = call["headers"]
    assert headers["x-api-key"] == "sk-test-outcomes"
    assert headers["anthropic-beta"] == DEFAULT_BETA_HEADER
    body = call["json"]
    assert body["type"] == "user.define_outcome"
    assert body["outcome"]["name"] == "answers_question"
    assert body["outcome"]["success_criteria"] == ["Cites a source"]
    assert body["outcome"]["weight"] == 2.0


@pytest.mark.asyncio
async def test_define_outcome_maps_4xx_to_readable_error(
    client: AnthropicOutcomesClient,
) -> None:
    response = _make_response(
        status=400,
        json_body={"error": {"message": "invalid success_criteria"}},
    )
    patcher, _calls = _patch_httpx(response)
    with patcher:
        definition = OutcomeDefinition(
            name="x",
            description="d",
            success_criteria=["only one"],
        )
        with pytest.raises(OutcomesAPIError) as exc_info:
            await client.define_outcome("sess_abc", definition)
    msg = str(exc_info.value)
    assert "400" in msg
    assert "invalid success_criteria" in msg
