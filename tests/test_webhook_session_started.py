"""Tests for the new session.status_run_started webhook event.

Anthropic's May 19 2026 self-hosted sandbox update introduces a
webhook-triggered alternative to long-poll workers. When a session
needs work, Anthropic emits ``session.status_run_started`` to the
registered URL; the recipient claims one work item and exits.

We extend Sandcastle's existing ``agent_webhooks`` router rather than
forking it - that router already verifies HMAC + dispatches to a
registry, so we just need to confirm:

1. The new event type is in SUPPORTED_EVENTS.
2. A handler registered for ``session.status_run_started`` actually
   fires when an event of that type lands.
3. Unsupported events still get the documented ``ignored`` response.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sandcastle.api.agent_webhooks import (
    AGENT_WEBHOOK_HANDLERS,
    SUPPORTED_EVENTS,
    register_handler,
    router,
    verify_signature,
)


class _StubSettings:
    """Drop-in stand-in for the Pydantic Settings instance.

    The real ``Settings.is_local_mode`` is a Pydantic computed property
    without a setter, so ``monkeypatch.setattr`` cannot flip it. We
    swap the whole ``settings`` reference inside the webhooks module
    instead, which is what the tests actually need.
    """

    def __init__(self, *, is_local_mode: bool = True) -> None:
        self.is_local_mode = is_local_mode


@pytest.fixture
def client(monkeypatch):
    # Skip signature verify so we can post arbitrary payloads from tests.
    monkeypatch.delenv("ANTHROPIC_WEBHOOK_SECRET", raising=False)
    with patch(
        "sandcastle.api.agent_webhooks.settings", _StubSettings(is_local_mode=True)
    ):
        app = FastAPI()
        app.include_router(router)
        yield TestClient(app)


def test_new_event_in_supported_list():
    assert "session.status_run_started" in SUPPORTED_EVENTS


def test_handler_fires_for_run_started(client):
    """Registering a handler for run_started should dispatch on POST."""
    received: list[dict] = []

    async def handler(event):
        received.append(event)

    # Save previous registry so other tests aren't polluted.
    prev = list(AGENT_WEBHOOK_HANDLERS.get("session.status_run_started", []))
    AGENT_WEBHOOK_HANDLERS["session.status_run_started"] = []

    register_handler("session.status_run_started", handler)

    try:
        payload = {
            "type": "session.status_run_started",
            "session_id": "sess_abc",
            "environment_id": "env_xyz",
            "work_id": "work_42",
        }
        resp = client.post(
            "/agent-webhooks/anthropic", content=json.dumps(payload)
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "accepted",
            "event_type": "session.status_run_started",
        }

        # Dispatch is fire-and-forget (asyncio.create_task) so give it
        # the event loop a tick to pick up the queued task before we
        # assert. TestClient runs the app in a thread + its own loop,
        # so a small sleep is the simplest portable wait.
        async def _wait():
            for _ in range(20):
                if received:
                    return
                await asyncio.sleep(0.05)

        asyncio.run(_wait())

        assert len(received) == 1
        assert received[0]["work_id"] == "work_42"
    finally:
        # Restore registry so we don't leak handlers across tests.
        AGENT_WEBHOOK_HANDLERS["session.status_run_started"] = prev


def test_unsupported_event_returns_ignored(client):
    resp = client.post(
        "/agent-webhooks/anthropic",
        content=json.dumps({"type": "session.invented_event"}),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_missing_type_returns_400(client):
    resp = client.post("/agent-webhooks/anthropic", content=json.dumps({}))
    assert resp.status_code == 400


def test_signature_verification_round_trips():
    body = b'{"type":"session.status_run_started","work_id":"x"}'
    secret = "shhh"
    import hashlib
    import hmac

    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_signature(secret, body, digest) is True
    assert verify_signature(secret, body, f"sha256={digest}") is True
    assert verify_signature(secret, body, "bad") is False
    assert verify_signature(secret, body, "") is False


def test_signature_required_in_non_local_mode(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_WEBHOOK_SECRET", raising=False)
    with patch(
        "sandcastle.api.agent_webhooks.settings", _StubSettings(is_local_mode=False)
    ):
        app = FastAPI()
        app.include_router(router)
        tc = TestClient(app)
        resp = tc.post(
            "/agent-webhooks/anthropic",
            content=json.dumps({"type": "session.status_run_started"}),
        )
    assert resp.status_code == 401
