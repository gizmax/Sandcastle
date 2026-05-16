"""Anthropic Outcomes API client and composite aggregator.

A self-contained async client for the Outcomes preview surface (beta header
`managed-agents-2026-04-01`, added 2026-05-06). Outcomes let callers attach
named pass/fail evaluations to a managed-agents session by posting
`user.define_outcome` events; the platform streams back
`span.outcome_evaluation_start` and `span.outcome_evaluation_end` events with
structured judging results.

This module provides:

- `OutcomeDefinition` / `OutcomeEvaluation` dataclasses
- `build_define_outcome_event` to shape outgoing payloads
- `parse_outcome_evaluation` to decode incoming SSE events
- `aggregate_outcomes` to compute composite, weighted scores (aligns with
  Sandcastle's v0.25 Evolution scoring contract)
- `AnthropicOutcomesClient` async HTTP client

Designed for v0.32 of Sandcastle. No dependencies outside stdlib + httpx and
no imports from other Sandcastle engine modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

DEFAULT_BETA_HEADER = "managed-agents-2026-04-01"
DEFINE_OUTCOME_EVENT_TYPE = "user.define_outcome"
OUTCOME_EVAL_START_TYPE = "span.outcome_evaluation_start"
OUTCOME_EVAL_END_TYPE = "span.outcome_evaluation_end"


class OutcomeValidationError(Exception):
    """Raised when an OutcomeDefinition is malformed.

    Examples: empty success_criteria, non-positive weight, missing name.
    """


class OutcomesAPIError(Exception):
    """Raised on 4xx/5xx responses from the Outcomes API."""


@dataclass
class OutcomeDefinition:
    """Declarative spec for a single outcome to evaluate against a session.

    Validation runs in __post_init__ so callers cannot construct an invalid
    definition silently. The `model` field overrides the judge model the
    platform would otherwise pick on its own.
    """

    name: str
    description: str
    success_criteria: list[str]
    weight: float = 1.0
    model: str | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise OutcomeValidationError("outcome name must be a non-empty string")
        if not isinstance(self.success_criteria, list) or not self.success_criteria:
            raise OutcomeValidationError(
                "success_criteria must be a non-empty list of strings"
            )
        if any(
            not isinstance(c, str) or not c.strip() for c in self.success_criteria
        ):
            raise OutcomeValidationError(
                "each success criterion must be a non-empty string"
            )
        if self.weight <= 0:
            raise OutcomeValidationError(
                f"weight must be > 0; got {self.weight}"
            )


@dataclass
class OutcomeEvaluation:
    """Parsed result of a `span.outcome_evaluation_end` event."""

    outcome_name: str
    passed: bool
    score: float
    reasoning: str
    evaluator_model: str
    started_at: datetime
    completed_at: datetime
    cost_usd: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


def build_define_outcome_event(definition: OutcomeDefinition) -> dict[str, Any]:
    """Build the request body for POST /v1/sessions/{id}/events.

    The shape mirrors the Outcomes preview spec: a `type` discriminator plus
    an `outcome` object carrying the judging contract.
    """
    outcome: dict[str, Any] = {
        "name": definition.name,
        "description": definition.description,
        "success_criteria": list(definition.success_criteria),
        "weight": definition.weight,
    }
    if definition.model is not None:
        outcome["model"] = definition.model
    return {"type": DEFINE_OUTCOME_EVENT_TYPE, "outcome": outcome}


def _parse_iso(value: Any) -> datetime:
    """Parse an ISO-8601 timestamp. Accepts trailing 'Z' as UTC."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return datetime.now(tz=timezone.utc)
    normalised = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalised)
    except ValueError:
        return datetime.now(tz=timezone.utc)


def parse_outcome_evaluation(event: dict[str, Any]) -> OutcomeEvaluation | None:
    """Decode a `span.outcome_evaluation_end` event into an OutcomeEvaluation.

    Returns None for any other event type (start events, unrelated spans, or
    malformed payloads missing the outcome envelope).
    """
    if not isinstance(event, dict):
        return None
    if event.get("type") != OUTCOME_EVAL_END_TYPE:
        return None
    outcome = event.get("outcome") or event.get("data") or {}
    if not isinstance(outcome, dict):
        return None
    name = outcome.get("name") or outcome.get("outcome_name")
    if not name:
        return None
    passed = bool(outcome.get("passed", False))
    try:
        score = float(outcome.get("score", 1.0 if passed else 0.0))
    except (TypeError, ValueError):
        score = 1.0 if passed else 0.0
    reasoning = str(outcome.get("reasoning") or outcome.get("explanation") or "")
    evaluator_model = str(
        outcome.get("evaluator_model") or outcome.get("model") or ""
    )
    started_at = _parse_iso(outcome.get("started_at") or event.get("started_at"))
    completed_at = _parse_iso(
        outcome.get("completed_at") or event.get("completed_at")
    )
    cost_raw = outcome.get("cost_usd")
    if cost_raw is None:
        usage = outcome.get("usage") or {}
        cost_raw = usage.get("cost_usd", 0.0) if isinstance(usage, dict) else 0.0
    try:
        cost_usd = float(cost_raw or 0.0)
    except (TypeError, ValueError):
        cost_usd = 0.0
    return OutcomeEvaluation(
        outcome_name=str(name),
        passed=passed,
        score=score,
        reasoning=reasoning,
        evaluator_model=evaluator_model,
        started_at=started_at,
        completed_at=completed_at,
        cost_usd=cost_usd,
        raw=event,
    )


def aggregate_outcomes(
    evaluations: list[OutcomeEvaluation],
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Aggregate evaluations into a composite, weighted score.

    The composite is sum(passed_i * w_i) / sum(w_i), where `passed_i` is 1.0
    when the evaluation passed and 0.0 otherwise. Weights default to 1.0 per
    outcome; the optional `weights` map overrides by outcome name.

    Returns a dict with keys: composite_score, pass_count, fail_count,
    total_cost_usd, evaluated, weights_used.
    """
    if not evaluations:
        return {
            "composite_score": 0.0,
            "pass_count": 0,
            "fail_count": 0,
            "total_cost_usd": 0.0,
            "evaluated": 0,
            "weights_used": {},
        }
    weights = weights or {}
    weighted_sum = 0.0
    weight_total = 0.0
    pass_count = 0
    fail_count = 0
    total_cost = 0.0
    weights_used: dict[str, float] = {}
    for ev in evaluations:
        w = float(weights.get(ev.outcome_name, 1.0))
        if w <= 0:
            continue
        weights_used[ev.outcome_name] = w
        weight_total += w
        weighted_sum += (1.0 if ev.passed else 0.0) * w
        if ev.passed:
            pass_count += 1
        else:
            fail_count += 1
        total_cost += ev.cost_usd
    composite = weighted_sum / weight_total if weight_total > 0 else 0.0
    return {
        "composite_score": composite,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "total_cost_usd": total_cost,
        "evaluated": len(evaluations),
        "weights_used": weights_used,
    }


class AnthropicOutcomesClient:
    """Async client for the Anthropic Outcomes API (preview)."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.anthropic.com",
        beta_header: str = DEFAULT_BETA_HEADER,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must be a non-empty string")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.beta_header = beta_header

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-beta": self.beta_header,
            "content-type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        try:
            payload = response.json()
            message = payload.get("error", {}).get("message") or str(payload)
        except Exception:
            message = response.text or f"HTTP {status}"
        raise OutcomesAPIError(f"HTTP {status}: {message}")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                self._url(path),
                headers=self._headers(),
                json=json,
                params=params,
            )
        self._raise_for_status(response)
        return response

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def define_outcome(
        self,
        session_id: str,
        definition: OutcomeDefinition,
    ) -> dict[str, Any]:
        """POST a `user.define_outcome` event into a session.

        Re-validates the definition (success_criteria non-empty) before
        making the network call to fail fast on bad inputs.
        """
        if not definition.success_criteria:
            raise OutcomeValidationError(
                "cannot define outcome without success_criteria"
            )
        body = build_define_outcome_event(definition)
        response = await self._request(
            "POST", f"/v1/sessions/{session_id}/events", json=body
        )
        try:
            return response.json()
        except Exception:
            return {}

    async def list_outcomes(self, session_id: str) -> list[OutcomeEvaluation]:
        """Fetch all completed evaluations for a session.

        Tolerates two response shapes: `{"data": [...]}` or a bare list. Each
        item is interpreted as a `span.outcome_evaluation_end`-shaped event
        and run through `parse_outcome_evaluation`; items that fail to parse
        are silently dropped.
        """
        response = await self._request(
            "GET", f"/v1/sessions/{session_id}/outcomes"
        )
        payload = response.json()
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = payload.get("data", []) or payload.get("outcomes", [])
        else:
            items = []
        parsed: list[OutcomeEvaluation] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            # The /outcomes endpoint returns bare outcome objects; wrap them
            # in the SSE event envelope so parse_outcome_evaluation can be
            # used uniformly.
            if item.get("type") == OUTCOME_EVAL_END_TYPE:
                envelope = item
            else:
                envelope = {"type": OUTCOME_EVAL_END_TYPE, "outcome": item}
            ev = parse_outcome_evaluation(envelope)
            if ev is not None:
                parsed.append(ev)
        return parsed


__all__ = [
    "AnthropicOutcomesClient",
    "OutcomeDefinition",
    "OutcomeEvaluation",
    "OutcomeValidationError",
    "OutcomesAPIError",
    "build_define_outcome_event",
    "parse_outcome_evaluation",
    "aggregate_outcomes",
    "DEFAULT_BETA_HEADER",
    "DEFINE_OUTCOME_EVENT_TYPE",
    "OUTCOME_EVAL_START_TYPE",
    "OUTCOME_EVAL_END_TYPE",
]
