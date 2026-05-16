"""End-to-end wiring tests for v0.32 prep modules.

These tests verify that the 9 new modules (memory_stores, multiagent,
agent_webhooks, tool_search, outcomes, trajectory_replay, agent_skills,
computer_use, agent_sdk_runtime) are wired into the Sandcastle backend.

All HTTP/DB/Anthropic calls are mocked so the suite runs fast (<5s) and
makes zero outbound network calls.
"""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sandcastle.api.agent_webhooks import router as agent_webhooks_router
from sandcastle.engine import executor as _executor_mod
from sandcastle.engine.dag import (
    ManagedAgentConfig,
    StepDefinition,
    VALID_STEP_TYPES,
)
from sandcastle.engine.executor import (
    RunContext,
    _execute_computer_use_step,
    _execute_managed_agent_step,
    _execute_trajectory_replay_step,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(**overrides) -> RunContext:
    defaults = dict(
        run_id="run-wiring-1",
        input={"topic": "x"},
        step_outputs={},
        step_results={},
    )
    defaults.update(overrides)
    return RunContext(**defaults)


def _mock_sse_stream(events: list[dict]):
    """Build a stub httpx streaming response that yields SSE lines."""
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


def _make_managed_agent_mock(
    *,
    captured_session_body: dict | None = None,
    captured_agent_body: dict | None = None,
    captured_events_payloads: list[dict] | None = None,
    sse_events: list[dict] | None = None,
):
    """Construct an httpx.AsyncClient mock that records what was sent."""

    mock_client = AsyncMock()

    async def mock_post(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        body = kwargs.get("json", {}) or {}
        if "/agents" in url and "/sessions" not in url:
            if captured_agent_body is not None:
                captured_agent_body.clear()
                captured_agent_body.update(body)
            resp.json.return_value = {"id": "ag_xx"}
        elif "/environments" in url:
            resp.json.return_value = {"id": "env_xx"}
        elif "/sessions" in url and "/events" in url:
            if captured_events_payloads is not None:
                captured_events_payloads.append(body)
            resp.json.return_value = {}
        elif "/sessions" in url:
            if captured_session_body is not None:
                captured_session_body.clear()
                captured_session_body.update(body)
            resp.json.return_value = {"id": "sess_xx"}
        else:
            resp.json.return_value = {}
        return resp

    mock_client.post = AsyncMock(side_effect=mock_post)
    mock_client.delete = AsyncMock(return_value=MagicMock(status_code=200))
    mock_client.stream = MagicMock(
        return_value=_mock_sse_stream(
            sse_events
            or [
                {
                    "type": "agent.message",
                    "content": [{"type": "text", "text": "ok"}],
                },
                {"type": "session.status_idle"},
            ]
        )
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    cleanup = AsyncMock()
    cleanup.delete = AsyncMock(return_value=MagicMock(status_code=200))
    cleanup.__aenter__ = AsyncMock(return_value=cleanup)
    cleanup.__aexit__ = AsyncMock(return_value=False)

    return mock_client, cleanup


def _clear_caches():
    _executor_mod._managed_agent_cache.clear()
    _executor_mod._managed_env_cache.clear()


# ---------------------------------------------------------------------------
# 1. trajectory-replay step
# ---------------------------------------------------------------------------


class TestTrajectoryReplayStep:
    """Trajectory-replay step parses + executes against synthetic DB rows."""

    def test_step_type_registered(self):
        assert "trajectory-replay" in VALID_STEP_TYPES

    @pytest.mark.asyncio
    async def test_executes_with_mocked_run_data(self):
        """Mock async_session to yield identical golden + candidate runs.

        With identical data, replay_score == 1.0 and the step passes.
        """
        step = StepDefinition(
            id="tr-1",
            type="trajectory-replay",
            trajectory_replay_config={
                "golden_run_id": "golden-abc",
                "fail_below_score": 0.5,
                "allow_cost_delta_pct": 50.0,
            },
        )
        ctx = _make_context(run_id="cand-xyz")

        # Build synthetic SQLAlchemy result rows for both runs.
        class _StepRow:
            def __init__(self, sid):
                self.step_id = sid
                self.output_data = {"tool_name": "bash", "args": {}}
                self.error = None
                self.cost_usd = 0.001
                self.duration_seconds = 0.5
                self.started_at = None

        class _Result:
            def __init__(self, items):
                self._items = items

            def scalars(self):
                return self

            def all(self):
                return self._items

        async def _execute(query):
            # Inspect the entity type from the compiled SQL via class name.
            from sandcastle.models.db import AuditEvent, RunStep

            entity = query.column_descriptions[0]["entity"]
            if entity is RunStep:
                return _Result([_StepRow("s1"), _StepRow("s2")])
            if entity is AuditEvent:
                return _Result([])
            return _Result([])

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=_execute)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "sandcastle.models.db.async_session",
            return_value=mock_session,
        ):
            result = await _execute_trajectory_replay_step(step, ctx)

        assert result.status == "completed", result.error
        assert result.output["pass"] is True
        assert result.output["score"] >= 0.5
        assert result.output["golden_run_id"] == "golden-abc"


# ---------------------------------------------------------------------------
# 2. computer-use step
# ---------------------------------------------------------------------------


class TestComputerUseStep:
    """Computer-use step parses + executes returning screenshots + actions."""

    def test_step_type_registered(self):
        assert "computer-use" in VALID_STEP_TYPES

    @pytest.mark.asyncio
    async def test_executes_and_returns_payload(self):
        step = StepDefinition(
            id="cu-1",
            type="computer-use",
            computer_use_config={
                "display_width_px": 1280,
                "display_height_px": 800,
                "tools": ["bash", "text_editor", "computer"],
                "model": "claude-sonnet-4-6",
                "message": "Open browser",
            },
        )
        ctx = _make_context()
        result = await _execute_computer_use_step(step, ctx)
        assert result.status == "completed", result.error
        assert "screenshots" in result.output
        assert "actions_taken" in result.output
        assert isinstance(result.output["screenshots"], list)
        assert isinstance(result.output["actions_taken"], list)
        # The Computer Use beta header must be populated.
        assert result.output["beta_header"].startswith("computer-use-")


# ---------------------------------------------------------------------------
# 3. managed-agent + memory_stores
# ---------------------------------------------------------------------------


class TestManagedAgentMemoryStores:
    """managed-agent step injects memory_stores into session-create."""

    def setup_method(self):
        _clear_caches()

    @pytest.mark.asyncio
    async def test_memory_stores_merged_into_session_resources(self):
        step = StepDefinition(
            id="ma-mem",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="ag_existing",
                environment_id="env_existing",
                message="hi",
                memory_stores=["ms_a", "ms_b"],
            ),
        )
        ctx = _make_context()

        captured_session_body: dict = {}
        mock_client, cleanup = _make_managed_agent_mock(
            captured_session_body=captured_session_body,
        )

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            with patch(
                "httpx.AsyncClient",
                side_effect=[mock_client, cleanup],
            ):
                result = await _execute_managed_agent_step(step, ctx)

        assert result.status == "completed", result.error
        assert "resources" in captured_session_body
        resources = captured_session_body["resources"]
        assert {"type": "memory_store", "id": "ms_a"} in resources
        assert {"type": "memory_store", "id": "ms_b"} in resources


# ---------------------------------------------------------------------------
# 4. managed-agent + multiagent (validation + valid payload)
# ---------------------------------------------------------------------------


class TestManagedAgentMultiagent:
    """managed-agent step builds + validates multiagent rosters."""

    def setup_method(self):
        _clear_caches()

    @pytest.mark.asyncio
    async def test_invalid_roster_fails_step(self):
        step = StepDefinition(
            id="ma-mai",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="auto",
                message="hi",
                multiagent={
                    # Two self entries is invalid per validate_roster.
                    "roster": [
                        {"type": "self"},
                        {"type": "self"},
                    ],
                },
            ),
        )
        ctx = _make_context()

        mock_client, cleanup = _make_managed_agent_mock()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            with patch(
                "httpx.AsyncClient",
                side_effect=[mock_client, cleanup],
            ):
                result = await _execute_managed_agent_step(step, ctx)

        assert result.status == "failed"
        assert "multiagent" in result.error.lower() or "self" in result.error.lower()

    @pytest.mark.asyncio
    async def test_valid_roster_sends_coordinator_payload(self):
        step = StepDefinition(
            id="ma-mav",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="auto",
                message="hi",
                multiagent={
                    "roster": [
                        {"type": "agent", "id": "ag_1", "nickname": "researcher"},
                        {"type": "agent", "id": "ag_2", "nickname": "writer"},
                    ],
                    "max_concurrent_threads": 5,
                    "prompt_routing_hint": "delegate research to researcher",
                },
            ),
        )
        ctx = _make_context()

        captured_agent_body: dict = {}
        mock_client, cleanup = _make_managed_agent_mock(
            captured_agent_body=captured_agent_body,
        )
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            with patch(
                "httpx.AsyncClient",
                side_effect=[mock_client, cleanup],
            ):
                result = await _execute_managed_agent_step(step, ctx)

        assert result.status == "completed", result.error
        assert "multiagent" in captured_agent_body
        ma = captured_agent_body["multiagent"]
        assert ma["type"] == "coordinator"
        assert ma["max_concurrent_threads"] == 5
        assert ma["prompt_routing_hint"] == "delegate research to researcher"
        nicknames = [a.get("nickname") for a in ma["agents"]]
        assert "researcher" in nicknames
        assert "writer" in nicknames


# ---------------------------------------------------------------------------
# 5. managed-agent + outcomes
# ---------------------------------------------------------------------------


class TestManagedAgentOutcomes:
    """managed-agent step POSTs define_outcome events for each outcome."""

    def setup_method(self):
        _clear_caches()

    @pytest.mark.asyncio
    async def test_define_outcome_events_posted(self):
        step = StepDefinition(
            id="ma-out",
            type="managed-agent",
            managed_agent_config=ManagedAgentConfig(
                agent_id="ag_existing",
                environment_id="env_existing",
                message="hi",
                outcomes=[
                    {
                        "name": "accuracy",
                        "description": "Output is factually correct",
                        "success_criteria": ["No hallucinations"],
                        "weight": 2.0,
                    },
                    {
                        "name": "brevity",
                        "description": "Output is under 100 words",
                        "success_criteria": ["Under 100 words"],
                    },
                ],
            ),
        )
        ctx = _make_context()

        captured_event_payloads: list[dict] = []
        mock_client, cleanup = _make_managed_agent_mock(
            captured_events_payloads=captured_event_payloads,
        )

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            with patch(
                "httpx.AsyncClient",
                side_effect=[mock_client, cleanup],
            ):
                result = await _execute_managed_agent_step(step, ctx)

        assert result.status == "completed", result.error
        # Two define_outcome events + one user.message event.
        define_events = []
        for payload in captured_event_payloads:
            for evt in payload.get("events", []):
                if evt.get("type") == "user.define_outcome":
                    define_events.append(evt)
        assert len(define_events) == 2
        names = {e["outcome"]["name"] for e in define_events}
        assert names == {"accuracy", "brevity"}


# ---------------------------------------------------------------------------
# 6. agent_webhooks router mounted
# ---------------------------------------------------------------------------


class TestWebhooksMounted:
    """The agent-webhooks router responds 200 in production after mount."""

    def test_anthropic_webhook_responds_200(self, monkeypatch):
        import hashlib
        import hmac

        secret = "wh-secret"
        monkeypatch.setenv("ANTHROPIC_WEBHOOK_SECRET", secret)

        app = FastAPI()
        app.include_router(agent_webhooks_router)
        client = TestClient(app)

        payload = {"type": "session.status_idle", "session_id": "s1"}
        body = json.dumps(payload).encode()
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        resp = client.post(
            "/agent-webhooks/anthropic",
            content=body,
            headers={
                "X-Anthropic-Signature": sig,
                "content-type": "application/json",
            },
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 7. publish-skills CLI
# ---------------------------------------------------------------------------


class TestPublishSkillsCLI:
    """`sandcastle publish-skills` lists / uploads workflows-as-skills."""

    def test_dry_run_lists_workflows(self, tmp_path, capsys, monkeypatch):
        """No --upload: prints JSON, does not call upload()."""

        wf_dir = tmp_path / "wf"
        wf_dir.mkdir()
        (wf_dir / "demo.yaml").write_text(
            "name: demo\n"
            "description: a demo workflow used for publish-skills tests\n"
            "default_model: sonnet\n"
            "steps:\n"
            "  - id: s1\n"
            "    prompt: do x\n",
            encoding="utf-8",
        )

        from sandcastle.__main__ import _cmd_publish_skills

        args = SimpleNamespace(upload=False, dir=str(wf_dir))
        _cmd_publish_skills(args)

        captured = capsys.readouterr()
        results = json.loads(captured.out)
        assert isinstance(results, list)
        assert results[0]["status"] == "dry_run"
        assert results[0]["name"]

    def test_upload_invokes_publish_with_dry_run_false(
        self, tmp_path, capsys, monkeypatch
    ):
        """With --upload: publish_workflows_as_skills(dry_run=False) is called."""

        wf_dir = tmp_path / "wf"
        wf_dir.mkdir()
        (wf_dir / "demo.yaml").write_text(
            "name: demo\n"
            "description: a demo workflow used for publish-skills tests\n"
            "default_model: sonnet\n"
            "steps:\n"
            "  - id: s1\n"
            "    prompt: do x\n",
            encoding="utf-8",
        )

        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

        captured_kwargs: dict = {}

        async def fake_publish(*, workflow_dir, dry_run, client):
            captured_kwargs.update(
                workflow_dir=workflow_dir,
                dry_run=dry_run,
                client=client,
            )
            return [{"path": "demo.yaml", "status": "uploaded"}]

        with patch(
            "sandcastle.engine.agent_skills.publish_workflows_as_skills",
            new=fake_publish,
        ):
            from sandcastle.__main__ import _cmd_publish_skills

            args = SimpleNamespace(upload=True, dir=str(wf_dir))
            _cmd_publish_skills(args)

        captured = capsys.readouterr()
        results = json.loads(captured.out)
        assert results[0]["status"] == "uploaded"
        assert captured_kwargs["dry_run"] is False
        assert captured_kwargs["client"] is not None


# ---------------------------------------------------------------------------
# 8. agent_runtime dispatch for "agent-sdk" + unknown runtimes
# ---------------------------------------------------------------------------


class TestAgentRuntimeDispatch:
    """get_runtime('agent-sdk') routes to AgentSDKRunner."""

    @pytest.mark.asyncio
    async def test_agent_sdk_dispatch_calls_runner(self):
        from sandcastle.engine import agent_runtime as ar_mod
        from sandcastle.engine.agent_runtime import get_runtime
        from sandcastle.engine.agent_sdk_runtime import AgentSDKResult

        runtime = get_runtime("agent-sdk")
        assert runtime.name == "agent-sdk"

        async def fake_run(self, prompt, config):  # noqa: ARG001
            return AgentSDKResult(
                output="hello from sdk",
                tool_calls=[],
                cost_usd=0.001,
                duration_ms=42,
            )

        with patch(
            "sandcastle.engine.agent_sdk_runtime.AgentSDKRunner.run",
            new=fake_run,
        ):
            result = await runtime.execute(
                system_prompt="be brief",
                tools=[],
                packages=[],
                message="hi",
                model="claude-sonnet-4-6",
                timeout=30,
                network="unrestricted",
            )

        assert result["output"] == "hello from sdk"
        assert result["runtime"] == "agent-sdk"
        assert result["cost_usd"] == 0.001
        assert result["duration_ms"] == 42

    def test_unknown_runtime_raises(self):
        from sandcastle.engine.agent_runtime import get_runtime

        with pytest.raises(ValueError):
            get_runtime("not-a-real-runtime")
