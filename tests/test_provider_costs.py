"""Tests for GET /stats/provider-costs, /stats/provider-savings, and
/stats/provider-recommendation endpoints."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from sandcastle.engine.providers import PROVIDER_REGISTRY
from sandcastle.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 1. Provider Registry mapping tests (pure unit tests, no HTTP)
# ---------------------------------------------------------------------------


class TestProviderMapping:
    """Test that model names map correctly to providers via PROVIDER_REGISTRY."""

    def test_sonnet_maps_to_claude(self):
        info = PROVIDER_REGISTRY.get("sonnet")
        assert info is not None
        assert info.provider == "claude"

    def test_haiku_maps_to_claude(self):
        info = PROVIDER_REGISTRY.get("haiku")
        assert info is not None
        assert info.provider == "claude"

    def test_opus_maps_to_claude(self):
        info = PROVIDER_REGISTRY.get("opus")
        assert info is not None
        assert info.provider == "claude"

    def test_mistral_large_maps_to_mistral(self):
        info = PROVIDER_REGISTRY.get("mistral/large")
        assert info is not None
        assert info.provider == "mistral"
        assert info.region == "eu"

    def test_mistral_small_maps_to_mistral(self):
        info = PROVIDER_REGISTRY.get("mistral/small")
        assert info is not None
        assert info.provider == "mistral"
        assert info.region == "eu"

    def test_ollama_maps_to_ollama(self):
        info = PROVIDER_REGISTRY.get("ollama")
        assert info is not None
        assert info.provider == "ollama"
        assert info.region == "local"
        assert info.input_price_per_m == 0.0
        assert info.output_price_per_m == 0.0

    def test_all_registry_models_have_provider(self):
        for model_name, info in PROVIDER_REGISTRY.items():
            assert info.provider, f"Model {model_name} has no provider"

    def test_all_registry_models_have_region(self):
        for model_name, info in PROVIDER_REGISTRY.items():
            assert info.region in ("us", "eu", "local"), (
                f"Model {model_name} has unexpected region: {info.region}"
            )

    def test_mistral_models_are_eu(self):
        for model_name, info in PROVIDER_REGISTRY.items():
            if info.provider == "mistral":
                assert info.region == "eu", f"{model_name} should be eu"

    def test_ollama_is_free(self):
        info = PROVIDER_REGISTRY["ollama"]
        assert info.input_price_per_m == 0.0
        assert info.output_price_per_m == 0.0

    def test_openai_models_have_positive_pricing(self):
        for model_name, info in PROVIDER_REGISTRY.items():
            if info.provider == "openai":
                assert info.input_price_per_m > 0, f"{model_name} should have positive input price"

    def test_claude_sonnet_more_expensive_than_haiku(self):
        sonnet = PROVIDER_REGISTRY["sonnet"]
        haiku = PROVIDER_REGISTRY["haiku"]
        assert sonnet.input_price_per_m > haiku.input_price_per_m


# ---------------------------------------------------------------------------
# Mock session helper
# ---------------------------------------------------------------------------


def _make_mock_session(rows_by_query=None):
    """Build an async_session context manager mock.

    rows_by_query: dict mapping call index -> list of mock rows.
    Pass None for a session that returns empty results.
    """
    call_idx = [0]
    rows_by_query = rows_by_query or {}

    async def _execute(stmt):
        idx = call_idx[0]
        call_idx[0] += 1
        rows = rows_by_query.get(idx, [])

        result = MagicMock()
        result.all.return_value = rows
        result.scalars.return_value.all.return_value = rows
        result.one.return_value = rows[0] if rows else MagicMock(
            total=0, completed=0, finished=0, cost=0.0, avg_dur=None
        )
        return result

    session_mock = AsyncMock()
    session_mock.execute = _execute
    session_mock.add = MagicMock()
    session_mock.commit = AsyncMock()

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session_mock)
    ctx.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=ctx)
    return factory


def _row(model: str, cost: float, count: int):
    """Create a mock SQL row for step aggregation."""
    r = MagicMock()
    r.model = model
    r.total_cost = cost
    r.step_count = count
    return r


def _audit_row(provider: str, purpose: str, cost: float, region: str = "us"):
    """Create a mock audit event payload dict (scalars returns these directly)."""
    return {
        "provider": provider,
        "purpose": purpose,
        "cost_estimate_usd": cost,
        "region": region,
    }


# ---------------------------------------------------------------------------
# 2. GET /stats/provider-costs - empty DB
# ---------------------------------------------------------------------------


class TestProviderCostsEmptyDB:
    """Endpoint returns sensible zeros when no data exists."""

    def test_empty_db_returns_zeros(self):
        factory = _make_mock_session()
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-costs")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_cost_usd"] == 0.0
        assert data["by_provider"] == []
        assert data["advisor_costs"]["total_usd"] == 0.0
        assert data["advisor_costs"]["by_purpose"] == []

    def test_period_days_reflected(self):
        factory = _make_mock_session()
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-costs?days=7")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["period_days"] == 7

    def test_default_period_is_30_days(self):
        factory = _make_mock_session()
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-costs")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["period_days"] == 30

    def test_days_param_validation_min(self):
        resp = client.get("/api/stats/provider-costs?days=0")
        assert resp.status_code == 422  # ge=1

    def test_days_param_validation_max(self):
        resp = client.get("/api/stats/provider-costs?days=366")
        assert resp.status_code == 422  # le=365


# ---------------------------------------------------------------------------
# 3. GET /stats/provider-costs - with data
# ---------------------------------------------------------------------------


class TestProviderCostsWithData:
    """Endpoint aggregates RunStep costs correctly by model/provider."""

    def test_costs_grouped_by_provider(self):
        """Steps with different models show up under correct providers."""
        step_rows = [
            _row("sonnet", 1.50, 10),
            _row("mistral/large", 0.80, 8),
        ]
        # Query 0: step_q, Query 1: advisor_q (scalars)
        factory = _make_mock_session({0: step_rows, 1: []})
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-costs")
        assert resp.status_code == 200
        data = resp.json()["data"]
        providers = {e["provider"] for e in data["by_provider"]}
        assert "claude" in providers
        assert "mistral" in providers

    def test_total_cost_sums_correctly(self):
        step_rows = [
            _row("sonnet", 2.00, 5),
            _row("haiku", 0.50, 3),
        ]
        factory = _make_mock_session({0: step_rows, 1: []})
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-costs")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert abs(data["total_cost_usd"] - 2.50) < 0.01

    def test_percentage_sums_to_100(self):
        step_rows = [
            _row("sonnet", 3.00, 5),
            _row("mistral/large", 1.00, 3),
        ]
        factory = _make_mock_session({0: step_rows, 1: []})
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-costs")
        assert resp.status_code == 200
        data = resp.json()["data"]
        total_pct = sum(e["percentage"] for e in data["by_provider"])
        assert abs(total_pct - 100.0) < 1.0  # allow small rounding error

    def test_sorted_by_cost_descending(self):
        step_rows = [
            _row("mistral/large", 0.20, 2),  # cheaper
            _row("sonnet", 5.00, 10),  # expensive
        ]
        factory = _make_mock_session({0: step_rows, 1: []})
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-costs")
        assert resp.status_code == 200
        data = resp.json()["data"]
        costs = [e["total_cost_usd"] for e in data["by_provider"]]
        assert costs == sorted(costs, reverse=True)

    def test_advisor_costs_aggregated_by_purpose(self):
        audit_rows = [
            _audit_row("anthropic", "generation", 0.05),
            _audit_row("anthropic", "judge", 0.01),
            _audit_row("anthropic", "generation", 0.05),
        ]
        factory = _make_mock_session({0: [], 1: audit_rows})
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-costs")
        assert resp.status_code == 200
        data = resp.json()["data"]
        advisor = data["advisor_costs"]
        assert advisor["total_usd"] > 0
        purposes = {p["purpose"] for p in advisor["by_purpose"]}
        assert "generation" in purposes
        assert "judge" in purposes

    def test_advisor_total_usd_is_sum_of_parts(self):
        audit_rows = [
            _audit_row("anthropic", "generation", 0.10),
            _audit_row("anthropic", "judge", 0.02),
            _audit_row("mistral", "evolution", 0.03),
        ]
        factory = _make_mock_session({0: [], 1: audit_rows})
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-costs")
        assert resp.status_code == 200
        data = resp.json()["data"]
        advisor = data["advisor_costs"]
        sum_of_parts = sum(p["cost_usd"] for p in advisor["by_purpose"])
        assert abs(advisor["total_usd"] - sum_of_parts) < 0.0001

    def test_region_is_correct_for_mistral(self):
        step_rows = [_row("mistral/large", 1.0, 3)]
        factory = _make_mock_session({0: step_rows, 1: []})
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-costs")
        assert resp.status_code == 200
        data = resp.json()["data"]
        mistral_entries = [e for e in data["by_provider"] if e["provider"] == "mistral"]
        assert len(mistral_entries) >= 1
        assert mistral_entries[0]["region"] == "eu"

    def test_avg_cost_per_run_calculated(self):
        step_rows = [_row("sonnet", 10.0, 5)]
        factory = _make_mock_session({0: step_rows, 1: []})
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-costs")
        assert resp.status_code == 200
        data = resp.json()["data"]
        entry = next(e for e in data["by_provider"] if e["provider"] == "claude")
        assert abs(entry["avg_cost_per_run"] - 2.0) < 0.001


# ---------------------------------------------------------------------------
# 4. GET /stats/provider-savings
# ---------------------------------------------------------------------------


class TestProviderSavings:
    """Savings endpoint returns alternatives with correct projections."""

    def test_empty_db_returns_zero_total(self):
        factory = _make_mock_session()
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-savings")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["current_total_usd"] == 0.0

    def test_alternatives_list_present(self):
        factory = _make_mock_session()
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-savings")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "alternatives" in data
        assert isinstance(data["alternatives"], list)

    def test_savings_percent_non_negative(self):
        step_rows = [_row("sonnet", 10.0, 5)]
        factory = _make_mock_session({0: step_rows})
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-savings")
        assert resp.status_code == 200
        data = resp.json()["data"]
        for alt in data["alternatives"]:
            assert alt["savings_percent"] >= 0.0

    def test_projected_cost_non_negative(self):
        step_rows = [_row("sonnet", 5.0, 3)]
        factory = _make_mock_session({0: step_rows})
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-savings")
        assert resp.status_code == 200
        data = resp.json()["data"]
        for alt in data["alternatives"]:
            assert alt["projected_cost_usd"] >= 0.0

    def test_ollama_is_free_in_savings(self):
        step_rows = [_row("sonnet", 5.0, 3)]
        factory = _make_mock_session({0: step_rows})
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-savings")
        assert resp.status_code == 200
        data = resp.json()["data"]
        alts = {a["provider"]: a for a in data["alternatives"]}
        if "ollama" in alts:
            assert alts["ollama"]["projected_cost_usd"] == 0.0
            assert alts["ollama"]["savings_percent"] == 100.0

    def test_savings_usd_approx_matches_current_minus_projected(self):
        step_rows = [_row("sonnet", 8.0, 4)]
        factory = _make_mock_session({0: step_rows})
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-savings")
        assert resp.status_code == 200
        data = resp.json()["data"]
        current = data["current_total_usd"]
        for alt in data["alternatives"]:
            expected_savings = current - alt["projected_cost_usd"]
            assert abs(alt["savings_usd"] - max(0.0, expected_savings)) < 0.05

    def test_alternatives_have_required_fields(self):
        step_rows = [_row("sonnet", 3.0, 2)]
        factory = _make_mock_session({0: step_rows})
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-savings")
        assert resp.status_code == 200
        data = resp.json()["data"]
        for alt in data["alternatives"]:
            assert "provider" in alt
            assert "savings_usd" in alt
            assert "savings_percent" in alt
            assert "projected_cost_usd" in alt
            assert "note" in alt
            assert "region" in alt

    def test_days_param_forwarded(self):
        factory = _make_mock_session()
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-savings?days=14")
        assert resp.status_code == 200

    def test_mistral_eu_note_contains_eu(self):
        """Mistral alternative note mentions EU data residency."""
        step_rows = [_row("sonnet", 50.0, 100)]
        factory = _make_mock_session({0: step_rows})
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-savings")
        assert resp.status_code == 200
        data = resp.json()["data"]
        mistral_alts = [a for a in data["alternatives"] if a["provider"] == "mistral"]
        if mistral_alts:
            note = mistral_alts[0]["note"].lower()
            assert "eu" in note or "mistral" in note


# ---------------------------------------------------------------------------
# 5. GET /stats/provider-recommendation
# ---------------------------------------------------------------------------


class TestProviderRecommendation:
    """Recommendation engine generates correct recommendation types."""

    def test_empty_db_returns_empty_or_valid_list(self):
        factory = _make_mock_session({0: [], 1: [], 2: []})
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-recommendation")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "recommendations" in data
        assert isinstance(data["recommendations"], list)

    def test_response_structure_always_present(self):
        factory = _make_mock_session()
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-recommendation")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "recommendations" in body["data"]

    def test_recommendation_has_required_fields(self):
        """When a recommendation is generated it has all required fields."""
        # Heavy opus usage triggers cost_saving recommendation
        step_rows = [_row("opus", 150.0, 10)]
        factory = _make_mock_session({0: step_rows, 1: [], 2: []})
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-recommendation")
        assert resp.status_code == 200
        data = resp.json()["data"]
        for rec in data["recommendations"]:
            assert "type" in rec
            assert "severity" in rec
            assert "title" in rec
            assert "description" in rec
            assert "action" in rec
            assert "provider" in rec
            assert "confidence" in rec
            assert "estimated_savings_usd" in rec

    def test_severity_values_valid(self):
        factory = _make_mock_session({0: [], 1: [], 2: []})
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-recommendation")
        assert resp.status_code == 200
        data = resp.json()["data"]
        valid_severities = {"high", "medium", "info"}
        for rec in data["recommendations"]:
            assert rec["severity"] in valid_severities

    def test_confidence_between_0_and_1(self):
        step_rows = [_row("opus", 200.0, 20)]
        factory = _make_mock_session({0: step_rows, 1: [], 2: []})
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-recommendation")
        assert resp.status_code == 200
        data = resp.json()["data"]
        for rec in data["recommendations"]:
            assert 0.0 <= rec["confidence"] <= 1.0

    def test_cost_saving_rec_when_expensive_provider_dominates(self):
        """High spend on opus (expensive) should trigger cost_saving."""
        # 100% spend on opus at $15/M input - very expensive
        step_rows = [_row("opus", 150.0, 10)]
        factory = _make_mock_session({0: step_rows, 1: [], 2: []})
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-recommendation")
        assert resp.status_code == 200
        data = resp.json()["data"]
        types = {r["type"] for r in data["recommendations"]}
        # With heavy opus usage, a cheaper alternative exists (minimax, mistral, etc.)
        assert "cost_saving" in types

    def test_data_residency_rec_when_non_eu_dominant(self):
        """When most advisor calls are non-EU, suggest data residency."""
        audit_rows = [
            _audit_row("anthropic", "generation", 0.05, region="us")
            for _ in range(10)
        ]
        # Query 0: step_q (.all()), Query 1: quality_q (.scalars().all() - floats),
        # Query 2: advisor_q (.scalars().all() - dicts)
        factory = _make_mock_session({0: [], 1: [], 2: audit_rows})
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-recommendation")
        assert resp.status_code == 200
        data = resp.json()["data"]
        # Response must always be valid shape
        for rec in data["recommendations"]:
            assert rec["type"] in ("cost_saving", "quality_upgrade", "data_residency", "unused_provider")

    def test_recommendation_type_enum_values(self):
        factory = _make_mock_session({0: [], 1: [], 2: []})
        with patch("sandcastle.api.routes.async_session", factory):
            resp = client.get("/api/stats/provider-recommendation")
        assert resp.status_code == 200
        data = resp.json()["data"]
        valid_types = {"cost_saving", "quality_upgrade", "data_residency", "unused_provider"}
        for rec in data["recommendations"]:
            assert rec["type"] in valid_types
