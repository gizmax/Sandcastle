"""Tests for workflow-aware token estimation in evolution engine.

Verifies that `_estimate_tokens_per_run` uses run history when available,
caches per workflow_name for 60 seconds, and falls back to the default
when the DB is empty or errors.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine import evolution
from sandcastle.engine.evolution import (
    _BASELINE_BLENDED_USD_PER_M,
    _build_cost_estimates,
    _cache_clear,
    _cache_get,
    _cache_set,
    _estimate_tokens_per_run,
    estimate_model_costs,
)


@pytest.fixture(autouse=True)
def _clear_cache_between_tests():
    """Ensure the in-process cache is empty between tests."""
    _cache_clear()
    yield
    _cache_clear()


def _make_async_session(costs: list[float] | None = None, raise_exc: Exception | None = None):
    """Build a fake `async_session()` context manager.

    When `raise_exc` is set, `session.execute` raises it. Otherwise it returns
    rows shaped like (Run.total_cost_usd,) for each value in `costs`.
    """
    session = MagicMock()

    if raise_exc is not None:
        session.execute = AsyncMock(side_effect=raise_exc)
    else:
        rows = [(c,) for c in (costs or [])]
        result = MagicMock()
        result.all.return_value = rows
        session.execute = AsyncMock(return_value=result)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)

    factory = MagicMock(return_value=cm)
    return factory, session


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_empty_db_returns_default():
    """No completed runs in history -> returns the default 1000."""
    factory, _ = _make_async_session(costs=[])
    with patch("sandcastle.models.db.async_session", factory):
        tokens = await _estimate_tokens_per_run("missing-workflow")
    assert tokens == 1000


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_history_returns_cost_derived_mean():
    """Five mocked runs are averaged and converted to tokens via baseline price."""
    # Average cost = 0.0035 USD -> tokens = 0.0035 * 1e6 / 7.0 = 500
    costs = [0.001, 0.002, 0.003, 0.005, 0.0065]
    factory, _ = _make_async_session(costs=costs)
    with patch("sandcastle.models.db.async_session", factory):
        tokens = await _estimate_tokens_per_run("wf-history")

    expected = int(round(sum(costs) / len(costs) * 1_000_000 / _BASELINE_BLENDED_USD_PER_M))
    assert tokens == expected
    assert tokens != 1000  # Must differ from default to prove derivation.


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_cache_hit_on_second_call_within_ttl():
    """Second call within TTL must not touch the DB again."""
    costs = [0.01, 0.02, 0.03]
    factory, session = _make_async_session(costs=costs)
    with patch("sandcastle.models.db.async_session", factory):
        first = await _estimate_tokens_per_run("wf-cached")
        second = await _estimate_tokens_per_run("wf-cached")

    assert first == second
    # session.execute should only be called once - second call hits cache.
    assert session.execute.await_count == 1


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_db_error_falls_back_to_default():
    """Any DB exception must yield the default, never propagate."""
    factory, _ = _make_async_session(raise_exc=RuntimeError("DB down"))
    with patch("sandcastle.models.db.async_session", factory):
        tokens = await _estimate_tokens_per_run("wf-broken", default=1234)
    assert tokens == 1234


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_empty_workflow_name_returns_default():
    """Blank workflow_name short-circuits to default without DB access."""
    factory, session = _make_async_session(costs=[0.5])
    with patch("sandcastle.models.db.async_session", factory):
        tokens = await _estimate_tokens_per_run("", default=42)
    assert tokens == 42
    assert session.execute.await_count == 0


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_estimate_model_costs_scales_with_history():
    """Higher historical cost -> higher per-model cost estimates."""
    # Average 0.014 USD -> tokens = 0.014 * 1e6 / 7 = 2000 (2x default).
    costs = [0.014] * 5
    factory, _ = _make_async_session(costs=costs)
    with patch("sandcastle.models.db.async_session", factory):
        estimates = await estimate_model_costs("wf-scaled")

    baseline = _build_cost_estimates(1000)
    # All non-zero estimates should be ~2x baseline.
    for model, price in estimates.items():
        if baseline[model] > 0:
            assert price == pytest.approx(baseline[model] * 2, rel=0.01)


@pytest.mark.timeout(10)
def test_cache_set_get_roundtrip():
    """Cache helpers store and return tokens correctly."""
    _cache_set("wf-x", 4242)
    assert _cache_get("wf-x") == 4242
    _cache_clear()
    assert _cache_get("wf-x") is None


@pytest.mark.timeout(10)
def test_module_level_estimates_use_default_baseline():
    """MODEL_COST_ESTIMATES at module import time uses the 1000-token baseline."""
    expected = _build_cost_estimates(1000)
    assert evolution.MODEL_COST_ESTIMATES == expected
