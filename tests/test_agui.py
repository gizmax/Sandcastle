"""Tests for the AG-UI (Agent-User Interaction) protocol endpoints."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from sandcastle.main import app

client = TestClient(app)


class TestAguiStream:
    """Tests for GET /api/agui/stream/{run_id}."""

    def test_invalid_run_id(self):
        response = client.get("/api/agui/stream/not-a-uuid")
        assert response.status_code == 400

    def test_nonexistent_run(self):
        with patch("sandcastle.api.agui.async_session") as mock_session_ctx:
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_session_ctx.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            run_id = str(uuid.uuid4())
            response = client.get(f"/api/agui/stream/{run_id}")
        assert response.status_code == 404

    def test_terminal_run_emits_finished(self):
        """A completed run should emit run_finished immediately."""
        run_id = str(uuid.uuid4())
        mock_run = MagicMock()
        mock_run.id = uuid.UUID(run_id)
        mock_run.workflow_name = "test-workflow"
        mock_run.status = MagicMock(value="completed")
        mock_run.total_cost_usd = 0.05
        mock_run.error = None

        with patch("sandcastle.api.agui.async_session") as mock_session_ctx:
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_run
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_session_ctx.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            response = client.get(f"/api/agui/stream/{run_id}")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        # Parse events from SSE stream
        events = _parse_sse_events(response.text)
        assert len(events) >= 1
        last_event = events[-1]
        assert last_event["type"] == "run_finished"
        assert last_event["data"]["run_id"] == run_id

    def test_failed_run_emits_error(self):
        """A failed run should emit run_error immediately."""
        run_id = str(uuid.uuid4())
        mock_run = MagicMock()
        mock_run.id = uuid.UUID(run_id)
        mock_run.workflow_name = "test-workflow"
        mock_run.status = MagicMock(value="failed")
        mock_run.total_cost_usd = 0.0
        mock_run.error = "Something went wrong"

        with patch("sandcastle.api.agui.async_session") as mock_session_ctx:
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_run
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_session_ctx.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            response = client.get(f"/api/agui/stream/{run_id}")

        events = _parse_sse_events(response.text)
        assert len(events) >= 1
        last_event = events[-1]
        assert last_event["type"] == "run_error"
        assert "error" in last_event["data"]


class TestAguiEventMapping:
    """Test the Sandcastle -> AG-UI event mapping functions."""

    def test_map_run_started(self):
        from sandcastle.api.agui import _map_event_bus_event

        event = {
            "type": "run.started",
            "data": {"run_id": "abc-123", "workflow_name": "test"},
        }
        result = _map_event_bus_event(event, "abc-123")
        assert result is not None
        parsed = _parse_single_sse(result)
        assert parsed["type"] == "run_started"
        assert parsed["data"]["workflow"] == "test"

    def test_map_step_completed(self):
        from sandcastle.api.agui import _map_event_bus_event

        event = {
            "type": "step.completed",
            "data": {
                "run_id": "abc-123",
                "step_id": "step1",
                "output": "Hello World",
            },
        }
        result = _map_event_bus_event(event, "abc-123")
        assert result is not None
        # step.completed emits text_message + state_delta (two events)
        lines = [
            line for line in result.split("\n")
            if line.startswith("data: ")
        ]
        assert len(lines) == 2

        text_event = json.loads(lines[0].removeprefix("data: "))
        delta_event = json.loads(lines[1].removeprefix("data: "))
        assert text_event["type"] == "text_message"
        assert delta_event["type"] == "state_delta"

    def test_map_filters_other_runs(self):
        from sandcastle.api.agui import _map_event_bus_event

        event = {
            "type": "run.started",
            "data": {"run_id": "other-run", "workflow_name": "test"},
        }
        result = _map_event_bus_event(event, "my-run")
        assert result is None

    def test_map_unknown_event(self):
        from sandcastle.api.agui import _map_event_bus_event

        event = {
            "type": "something.else",
            "data": {"run_id": "abc-123"},
        }
        result = _map_event_bus_event(event, "abc-123")
        assert result is None

    def test_map_run_failed(self):
        from sandcastle.api.agui import _map_event_bus_event

        event = {
            "type": "run.failed",
            "data": {"run_id": "abc-123", "error": "Boom"},
        }
        result = _map_event_bus_event(event, "abc-123")
        assert result is not None
        parsed = _parse_single_sse(result)
        assert parsed["type"] == "run_error"
        assert parsed["data"]["error"] == "Boom"

    def test_map_step_started(self):
        from sandcastle.api.agui import _map_event_bus_event

        event = {
            "type": "step.started",
            "data": {"run_id": "abc-123", "step_id": "step1", "step_type": "llm"},
        }
        result = _map_event_bus_event(event, "abc-123")
        assert result is not None
        parsed = _parse_single_sse(result)
        assert parsed["type"] == "step_started"
        assert parsed["data"]["type"] == "llm"


# -- Helpers --


def _parse_sse_events(text: str) -> list[dict]:
    """Parse AG-UI SSE events from response body."""
    events = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            payload = json.loads(line.removeprefix("data: "))
            events.append(payload)
    return events


def _parse_single_sse(text: str) -> dict:
    """Parse a single SSE data line."""
    for line in text.strip().split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            return json.loads(line.removeprefix("data: "))
    raise ValueError(f"No SSE data found in: {text}")
