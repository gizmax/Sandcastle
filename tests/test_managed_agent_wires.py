"""Tier-1 wire fixes for the managed-agent step.

Covers:
- tools_enabled forwarding to the agent-create payload
- temperature / max_tokens / thinking_budget plumbing
- stream: False collecting events server-side before assembly
- per-model pricing table with fallback + single warning
- fallback_template chain (str or list, walked left-to-right, capped)
"""

from __future__ import annotations

import json
import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import httpx

from sandcastle.engine import executor as _executor_mod
from sandcastle.engine.dag import ManagedAgentConfig, StepDefinition
from sandcastle.engine.executor import RunContext


_execute_managed_agent_step = _executor_mod._execute_managed_agent_step


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context() -> RunContext:
    return RunContext(
        run_id="run-wires-1",
        input={"topic": "demo"},
        step_outputs={},
        step_results={},
    )


def _clear_caches():
    _executor_mod._managed_agent_cache.clear()
    _executor_mod._managed_env_cache.clear()
    _executor_mod._warned_unknown_agent_models.clear()


def _mock_sse_stream(events: list[dict]):
    lines = [f"data: {json.dumps(e)}" for e in events]
    lines.append("")

    class FakeStream:
        async def aiter_lines(self):
            for line in lines:
                yield line

    stream_ctx = AsyncMock()
    stream_ctx.__aenter__ = AsyncMock(return_value=FakeStream())
    stream_ctx.__aexit__ = AsyncMock(return_value=False)
    return stream_ctx


def _build_mock_client(
    captured: dict | None = None,
    sse_events: list[dict] | None = None,
):
    """Construct a single AsyncClient mock that records POST payloads."""
    client = AsyncMock()
    if captured is None:
        captured = {}
    captured.setdefault("agents", [])
    captured.setdefault("environments", [])
    captured.setdefault("sessions", [])
    captured.setdefault("events", [])

    async def mock_post(url, **kwargs):
        body = kwargs.get("json", {})
        resp = MagicMock()
        resp.status_code = 200
        if "/agents" in url and "/sessions" not in url:
            captured["agents"].append(body)
            resp.json.return_value = {"id": "ag_test"}
        elif "/environments" in url:
            captured["environments"].append(body)
            resp.json.return_value = {"id": "env_test"}
        elif "/sessions" in url and "/events" in url:
            captured["events"].append(body)
            resp.json.return_value = {}
        elif "/sessions" in url:
            captured["sessions"].append(body)
            resp.json.return_value = {"id": "sess_test"}
        else:
            resp.json.return_value = {"id": "x"}
        return resp

    async def mock_delete(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        return resp

    events = sse_events if sse_events is not None else [
        {"type": "agent.message", "content": [{"type": "text", "text": "ok"}]},
        {"type": "session.status_idle"},
    ]
    client.post = AsyncMock(side_effect=mock_post)
    client.delete = AsyncMock(side_effect=mock_delete)
    client.stream = MagicMock(return_value=_mock_sse_stream(events))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client, captured


def _cleanup_client():
    c = AsyncMock()
    c.delete = AsyncMock(return_value=MagicMock(status_code=200))
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    return c


# ---------------------------------------------------------------------------
# 1. tools_enabled wiring
# ---------------------------------------------------------------------------

class TestToolsEnabledWiring:

    def setup_method(self):
        _clear_caches()

    @pytest.mark.asyncio
    async def test_tools_enabled_list_is_forwarded(self):
        """When tools_enabled is set, request 'tools' field maps names -> {type: name}."""
        step = StepDefinition(
            id="ma-tools",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="auto",
                tools_enabled=["bash", "web_search"],
                message="hi",
            ),
        )
        client, captured = _build_mock_client()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            with patch("httpx.AsyncClient", side_effect=[client, _cleanup_client()]):
                result = await _execute_managed_agent_step(step, _make_context())
        assert result.status == "completed"
        assert captured["agents"], "agent-create call should have happened"
        tools = captured["agents"][0]["tools"]
        assert tools == [{"type": "bash"}, {"type": "web_search"}]

    @pytest.mark.asyncio
    async def test_tools_enabled_none_uses_default_toolset(self):
        """When tools_enabled is None, default managed toolset is used."""
        step = StepDefinition(
            id="ma-default",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="auto", tools_enabled=None, message="hi"
            ),
        )
        client, captured = _build_mock_client()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            with patch("httpx.AsyncClient", side_effect=[client, _cleanup_client()]):
                await _execute_managed_agent_step(step, _make_context())
        assert captured["agents"][0]["tools"] == [{"type": "agent_toolset_20260401"}]

    @pytest.mark.asyncio
    async def test_tools_enabled_empty_list_uses_default(self):
        """An empty list is treated like None - default toolset stays."""
        step = StepDefinition(
            id="ma-empty",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="auto", tools_enabled=[], message="hi"
            ),
        )
        client, captured = _build_mock_client()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            with patch("httpx.AsyncClient", side_effect=[client, _cleanup_client()]):
                await _execute_managed_agent_step(step, _make_context())
        assert captured["agents"][0]["tools"] == [{"type": "agent_toolset_20260401"}]


# ---------------------------------------------------------------------------
# 2. Sampling params plumbed through agent-create
# ---------------------------------------------------------------------------

class TestSamplingParams:

    def setup_method(self):
        _clear_caches()

    @pytest.mark.asyncio
    async def test_all_sampling_fields_forwarded(self):
        step = StepDefinition(
            id="ma-samp",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="auto",
                message="hi",
                temperature=0.4,
                max_tokens=2048,
                thinking_budget=8000,
            ),
        )
        client, captured = _build_mock_client()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            with patch("httpx.AsyncClient", side_effect=[client, _cleanup_client()]):
                await _execute_managed_agent_step(step, _make_context())
        body = captured["agents"][0]
        assert body["temperature"] == 0.4
        assert body["max_tokens"] == 2048
        assert body["thinking"] == {"type": "enabled", "budget_tokens": 8000}

    @pytest.mark.asyncio
    async def test_none_sampling_fields_omitted(self):
        """When fields are None, agent payload must not include them."""
        step = StepDefinition(
            id="ma-samp-none",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(agent_id="auto", message="hi"),
        )
        client, captured = _build_mock_client()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            with patch("httpx.AsyncClient", side_effect=[client, _cleanup_client()]):
                await _execute_managed_agent_step(step, _make_context())
        body = captured["agents"][0]
        assert "temperature" not in body
        assert "max_tokens" not in body
        assert "thinking" not in body

    @pytest.mark.asyncio
    async def test_partial_sampling_fields(self):
        """Only the set fields appear in payload; the rest are omitted."""
        step = StepDefinition(
            id="ma-samp-part",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="auto", message="hi", temperature=0.0
            ),
        )
        client, captured = _build_mock_client()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            with patch("httpx.AsyncClient", side_effect=[client, _cleanup_client()]):
                await _execute_managed_agent_step(step, _make_context())
        body = captured["agents"][0]
        assert body["temperature"] == 0.0
        assert "max_tokens" not in body
        assert "thinking" not in body


# ---------------------------------------------------------------------------
# 3. stream: False buffers events server-side
# ---------------------------------------------------------------------------

class TestStreamCollection:

    def setup_method(self):
        _clear_caches()

    @pytest.mark.asyncio
    async def test_stream_false_returns_final_text_only(self):
        """With stream=False, all events buffer first; final text is the concatenation."""
        step = StepDefinition(
            id="ma-buf",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="auto", message="hi", stream=False
            ),
        )
        sse = [
            {"type": "agent.message",
             "content": [{"type": "text", "text": "part-A "}],
             "usage": {"input_tokens": 10, "output_tokens": 5}},
            {"type": "agent.message",
             "content": [{"type": "text", "text": "part-B"}],
             "usage": {"input_tokens": 0, "output_tokens": 3}},
            {"type": "session.status_idle"},
        ]
        client, _ = _build_mock_client(sse_events=sse)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            with patch("httpx.AsyncClient", side_effect=[client, _cleanup_client()]):
                result = await _execute_managed_agent_step(step, _make_context())
        assert result.status == "completed"
        assert result.output == "part-A part-B"
        # Cost computed from tokens regardless of mode
        assert result.cost_usd > 0

    @pytest.mark.asyncio
    async def test_stream_true_default_still_works(self):
        """Default stream=True still collects text incrementally."""
        step = StepDefinition(
            id="ma-stream",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(agent_id="auto", message="hi"),
        )
        sse = [
            {"type": "agent.message", "content": [{"type": "text", "text": "x"}]},
            {"type": "agent.message", "content": [{"type": "text", "text": "y"}]},
            {"type": "session.status_idle"},
        ]
        client, _ = _build_mock_client(sse_events=sse)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            with patch("httpx.AsyncClient", side_effect=[client, _cleanup_client()]):
                result = await _execute_managed_agent_step(step, _make_context())
        assert result.output == "xy"

    def test_stream_default_is_true(self):
        """Default value preserves backward compat."""
        assert ManagedAgentConfig().stream is True


# ---------------------------------------------------------------------------
# 4. Pricing table
# ---------------------------------------------------------------------------

class TestPricingTable:

    def setup_method(self):
        _clear_caches()

    def test_known_model_returns_table_value(self):
        assert _executor_mod._agent_model_pricing("claude-opus-4-7") == (5.0, 25.0)
        assert _executor_mod._agent_model_pricing("claude-haiku-4-5") == (1.0, 5.0)
        assert _executor_mod._agent_model_pricing("claude-sonnet-4-6") == (3.0, 15.0)

    def test_unknown_model_falls_back_to_sonnet(self):
        price = _executor_mod._agent_model_pricing("claude-future-99")
        assert price == _executor_mod._AGENT_PRICING_FALLBACK
        assert price == (3.0, 15.0)

    def test_unknown_model_warns_once_per_process(self, caplog):
        with caplog.at_level(logging.WARNING, logger="sandcastle.engine.executor"):
            _executor_mod._agent_model_pricing("model-zzz")
            _executor_mod._agent_model_pricing("model-zzz")
            _executor_mod._agent_model_pricing("model-zzz")
        zzz_warnings = [r for r in caplog.records if "model-zzz" in r.getMessage()]
        assert len(zzz_warnings) == 1

    @pytest.mark.asyncio
    async def test_cost_uses_model_pricing(self):
        """Cost computed for opus-4-7 uses the table (5/25), not Sonnet defaults."""
        step = StepDefinition(
            id="ma-price",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="auto", message="hi", model="claude-opus-4-7"
            ),
        )
        sse = [
            {"type": "agent.message",
             "content": [{"type": "text", "text": "ok"}],
             "usage": {"input_tokens": 1_000_000, "output_tokens": 1_000_000}},
            {"type": "session.status_idle"},
        ]
        client, _ = _build_mock_client(sse_events=sse)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            with patch("httpx.AsyncClient", side_effect=[client, _cleanup_client()]):
                result = await _execute_managed_agent_step(step, _make_context())
        # 1M input * $5 + 1M output * $25 = $30
        assert result.cost_usd == pytest.approx(30.0, rel=1e-3)


# ---------------------------------------------------------------------------
# 5. Fallback chain semantics
# ---------------------------------------------------------------------------

class TestFallbackChain:

    def setup_method(self):
        _clear_caches()

    @pytest.mark.asyncio
    async def test_string_form_still_accepted(self):
        """Single template string still triggers a one-step fallback chain."""
        step = StepDefinition(
            id="ma-fb-str",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="auto",
                message="hi",
                agent_template="researcher",
                fallback_template="coder",
            ),
        )
        # Primary call times out (httpx exception path triggers fallback chain).
        primary_client = AsyncMock()
        primary_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        primary_client.delete = AsyncMock()
        primary_client.__aenter__ = AsyncMock(return_value=primary_client)
        primary_client.__aexit__ = AsyncMock(return_value=False)

        fb_client, _ = _build_mock_client()
        clients = [primary_client, fb_client, _cleanup_client()]
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            with patch("httpx.AsyncClient", side_effect=clients):
                result = await _execute_managed_agent_step(step, _make_context())
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_list_of_two_walked_in_order(self):
        """First fallback fails, second succeeds; we walk in declared order."""
        step = StepDefinition(
            id="ma-fb-chain",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="auto",
                message="hi",
                agent_template="researcher",
                fallback_template=["coder", "writer"],
            ),
        )
        # Primary fails, first fallback fails, second fallback succeeds.
        def _failing_client():
            c = AsyncMock()
            c.post = AsyncMock(side_effect=httpx.TimeoutException("nope"))
            c.delete = AsyncMock()
            c.__aenter__ = AsyncMock(return_value=c)
            c.__aexit__ = AsyncMock(return_value=False)
            return c

        fb2_client, _ = _build_mock_client()
        clients = [
            _failing_client(),       # primary times out
            _failing_client(),       # fb1 = coder times out
            fb2_client,              # fb2 = writer succeeds
            _cleanup_client(),       # cleanup for fb2 session
        ]
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            with patch("httpx.AsyncClient", side_effect=clients):
                result = await _execute_managed_agent_step(step, _make_context())
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_primary_success_skips_chain(self):
        """When the primary completes, fallback list is never invoked."""
        step = StepDefinition(
            id="ma-fb-skip",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="auto",
                message="hi",
                agent_template="researcher",
                fallback_template=["coder", "writer"],
            ),
        )
        primary_client, _ = _build_mock_client()
        # Only one client + cleanup should be consumed. If the chain ran, we'd
        # exhaust the side_effect list and StopIteration would surface.
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            with patch(
                "httpx.AsyncClient",
                side_effect=[primary_client, _cleanup_client()],
            ):
                result = await _execute_managed_agent_step(step, _make_context())
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_all_fail_returns_last_error(self):
        """When every link in the chain fails, the last error is surfaced."""
        step = StepDefinition(
            id="ma-fb-allfail",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="auto",
                message="hi",
                agent_template="researcher",
                fallback_template=["coder", "writer"],
            ),
        )

        def _failing_client():
            c = AsyncMock()
            c.post = AsyncMock(side_effect=httpx.TimeoutException("boom"))
            c.delete = AsyncMock()
            c.__aenter__ = AsyncMock(return_value=c)
            c.__aexit__ = AsyncMock(return_value=False)
            return c

        clients = [
            _failing_client(),       # primary
            _failing_client(),       # fb1 = coder
            _failing_client(),       # fb2 = writer
        ]
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            with patch("httpx.AsyncClient", side_effect=clients):
                result = await _execute_managed_agent_step(step, _make_context())
        assert result.status == "failed"
        assert "Primary failed" in result.error
        # Last fallback name in the chain surfaces
        assert "writer" in result.error

    @pytest.mark.asyncio
    async def test_chain_capped_at_five(self):
        """Chains longer than five entries are truncated."""
        step = StepDefinition(
            id="ma-fb-cap",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="auto",
                message="hi",
                agent_template="researcher",
                # 7 entries: only the first 5 should be attempted.
                fallback_template=[
                    "coder", "writer", "analyst", "reviewer",
                    "scraper", "tester", "devops",
                ],
            ),
        )

        def _failing_client():
            c = AsyncMock()
            c.post = AsyncMock(side_effect=httpx.TimeoutException("boom"))
            c.delete = AsyncMock()
            c.__aenter__ = AsyncMock(return_value=c)
            c.__aexit__ = AsyncMock(return_value=False)
            return c

        # 1 primary + 5 fallbacks = 6 clients. If the cap is ignored we'd
        # need 8 and StopIteration would be raised.
        clients: list[AsyncMock] = [_failing_client() for _ in range(6)]
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            with patch("httpx.AsyncClient", side_effect=clients):
                result = await _execute_managed_agent_step(step, _make_context())
        assert result.status == "failed"
        # Truncation means "scraper" is the last attempted name (5th fallback),
        # not the trailing "devops".
        assert "scraper" in result.error
