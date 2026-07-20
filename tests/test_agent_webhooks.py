"""Tests for the Anthropic Managed Agents webhook subscriber + handler."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sandcastle.api import agent_webhooks
from sandcastle.api.agent_webhooks import (
    AGENT_WEBHOOK_HANDLERS,
    ANTHROPIC_BETA_HEADER,
    SUPPORTED_EVENTS,
    AnthropicWebhookSubscription,
    register_handler,
    router,
    verify_signature,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_handlers():
    """Reset the global handler registry around each test."""
    snapshot = {k: list(v) for k, v in AGENT_WEBHOOK_HANDLERS.items()}
    for k in list(AGENT_WEBHOOK_HANDLERS.keys()):
        AGENT_WEBHOOK_HANDLERS[k] = []
    yield
    AGENT_WEBHOOK_HANDLERS.clear()
    AGENT_WEBHOOK_HANDLERS.update(snapshot)


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def test_valid_signature_accepted(client: TestClient, monkeypatch):
    secret = "topsecret"
    monkeypatch.setenv("ANTHROPIC_WEBHOOK_SECRET", secret)
    payload = {"type": "session.status_idle", "session_id": "s1"}
    body = json.dumps(payload).encode()
    sig = _sign(secret, body)

    resp = client.post(
        "/agent-webhooks/anthropic",
        content=body,
        headers={
            "X-Anthropic-Signature": sig,
            "content-type": "application/json",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"


def test_invalid_signature_rejected(client: TestClient, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_WEBHOOK_SECRET", "realsecret")
    payload = {"type": "session.status_idle"}
    body = json.dumps(payload).encode()
    bad_sig = _sign("wrongsecret", body)

    resp = client.post(
        "/agent-webhooks/anthropic",
        content=body,
        headers={
            "X-Anthropic-Signature": bad_sig,
            "content-type": "application/json",
        },
    )
    assert resp.status_code == 401


def test_signature_with_sha256_prefix(client: TestClient, monkeypatch):
    secret = "abc"
    monkeypatch.setenv("ANTHROPIC_WEBHOOK_SECRET", secret)
    body = json.dumps({"type": "session.status_running"}).encode()
    sig = "sha256=" + _sign(secret, body)
    resp = client.post(
        "/agent-webhooks/anthropic",
        content=body,
        headers={"X-Anthropic-Signature": sig},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Missing-secret behaviour
# ---------------------------------------------------------------------------


def test_local_mode_without_secret_accepted_with_warning(
    client: TestClient, monkeypatch, caplog
):
    monkeypatch.delenv("ANTHROPIC_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(
        agent_webhooks,
        "settings",
        SimpleNamespace(is_local_mode=True, auth_required=False),
    )

    body = json.dumps({"type": "session.status_idle"}).encode()
    with caplog.at_level("WARNING"):
        resp = client.post(
            "/agent-webhooks/anthropic",
            content=body,
            headers={"content-type": "application/json"},
        )
    assert resp.status_code == 200
    assert any("skipping signature verify" in r.message for r in caplog.records)


def test_auth_required_without_secret_rejected_even_in_local_mode(client: TestClient, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(
        agent_webhooks,
        "settings",
        SimpleNamespace(is_local_mode=True, auth_required=True),
    )

    body = json.dumps({"type": "session.status_idle"}).encode()
    resp = client.post(
        "/agent-webhooks/anthropic",
        content=body,
        headers={"content-type": "application/json"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "webhook secret not configured"


def test_auth_required_with_secret_still_verifies_hmac(client: TestClient, monkeypatch):
    secret = "topsecret"
    monkeypatch.setenv("ANTHROPIC_WEBHOOK_SECRET", secret)
    monkeypatch.setattr(
        agent_webhooks,
        "settings",
        SimpleNamespace(is_local_mode=True, auth_required=True),
    )
    body = json.dumps({"type": "session.status_idle"}).encode()

    valid = client.post(
        "/agent-webhooks/anthropic",
        content=body,
        headers={"X-Anthropic-Signature": _sign(secret, body)},
    )
    invalid = client.post(
        "/agent-webhooks/anthropic",
        content=body,
        headers={"X-Anthropic-Signature": _sign("wrongsecret", body)},
    )

    assert valid.status_code == 200
    assert invalid.status_code == 401


def test_production_mode_without_secret_rejected(client: TestClient, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(
        agent_webhooks,
        "settings",
        SimpleNamespace(is_local_mode=False, auth_required=False),
    )

    body = json.dumps({"type": "session.status_idle"}).encode()
    resp = client.post(
        "/agent-webhooks/anthropic",
        content=body,
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Per-event dispatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("event_type", list(SUPPORTED_EVENTS))
def test_event_dispatched_to_registered_handler(
    client: TestClient, monkeypatch, event_type: str
):
    monkeypatch.delenv("ANTHROPIC_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(
        agent_webhooks,
        "settings",
        SimpleNamespace(is_local_mode=True, auth_required=False),
    )

    received: list[dict[str, Any]] = []

    async def handler(event: dict[str, Any]) -> None:
        received.append(event)

    register_handler(event_type, handler)

    payload = {"type": event_type, "id": f"evt-{event_type}"}
    body = json.dumps(payload).encode()

    resp = client.post(
        "/agent-webhooks/anthropic",
        content=body,
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 200

    # Give the asyncio.create_task a chance to run.
    for _ in range(20):
        if received:
            break
        time.sleep(0.02)

    assert len(received) == 1
    assert received[0]["type"] == event_type


def test_register_handler_accumulates_multiple_handlers(
    client: TestClient, monkeypatch
):
    monkeypatch.delenv("ANTHROPIC_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(
        agent_webhooks,
        "settings",
        SimpleNamespace(is_local_mode=True, auth_required=False),
    )

    calls: list[str] = []

    async def h1(event):
        calls.append("h1")

    async def h2(event):
        calls.append("h2")

    register_handler("session.error", h1)
    register_handler("session.error", h2)
    assert len(AGENT_WEBHOOK_HANDLERS["session.error"]) == 2

    body = json.dumps({"type": "session.error"}).encode()
    resp = client.post(
        "/agent-webhooks/anthropic",
        content=body,
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 200

    for _ in range(25):
        if len(calls) == 2:
            break
        time.sleep(0.02)
    assert sorted(calls) == ["h1", "h2"]


# ---------------------------------------------------------------------------
# Slow handler must not block ACK
# ---------------------------------------------------------------------------


def test_slow_handler_does_not_delay_ack(client: TestClient, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(
        agent_webhooks,
        "settings",
        SimpleNamespace(is_local_mode=True, auth_required=False),
    )

    async def slow(event):
        await asyncio.sleep(0.5)

    register_handler("session.status_running", slow)

    body = json.dumps({"type": "session.status_running"}).encode()
    start = time.perf_counter()
    resp = client.post(
        "/agent-webhooks/anthropic",
        content=body,
        headers={"content-type": "application/json"},
    )
    elapsed = time.perf_counter() - start

    assert resp.status_code == 200
    assert elapsed < 0.3, f"ACK took too long: {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# AnthropicWebhookSubscription client
# ---------------------------------------------------------------------------


def _mock_response(status_code: int, body: Any) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    return resp


def test_create_subscription_posts_correct_body_and_beta_header():
    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(
        return_value=_mock_response(
            200, {"id": "sub_1", "url": "https://x/cb", "events": ["session.status_idle"]}
        )
    )

    sub = AnthropicWebhookSubscription(api_key="key", client=mock_client)
    result = asyncio.run(
        sub.create_subscription(
            callback_url="https://x/cb",
            events=["session.status_idle"],
            secret="shh",
        )
    )
    assert result["id"] == "sub_1"

    mock_client.post.assert_awaited_once()
    args, kwargs = mock_client.post.call_args
    assert args[0] == "/v1/webhooks"
    assert kwargs["json"] == {
        "url": "https://x/cb",
        "events": ["session.status_idle"],
        "secret": "shh",
    }
    headers = kwargs["headers"]
    assert headers["anthropic-beta"] == ANTHROPIC_BETA_HEADER
    assert headers["x-api-key"] == "key"
    assert headers["content-type"] == "application/json"


def test_create_subscription_omits_secret_when_not_provided():
    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=_mock_response(200, {"id": "sub_2"}))

    sub = AnthropicWebhookSubscription(api_key="k", client=mock_client)
    asyncio.run(
        sub.create_subscription(
            callback_url="https://x/cb", events=["session.status_running"]
        )
    )
    _, kwargs = mock_client.post.call_args
    assert "secret" not in kwargs["json"]


def test_list_and_delete_subscription():
    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(
        return_value=_mock_response(200, {"data": [{"id": "sub_a"}, {"id": "sub_b"}]})
    )
    mock_client.delete = AsyncMock(return_value=_mock_response(204, {}))

    sub = AnthropicWebhookSubscription(api_key="k", client=mock_client)
    items = asyncio.run(sub.list_subscriptions())
    assert [s["id"] for s in items] == ["sub_a", "sub_b"]

    asyncio.run(sub.delete_subscription("sub_a"))
    mock_client.delete.assert_awaited_once()
    args, _ = mock_client.delete.call_args
    assert args[0] == "/v1/webhooks/sub_a"


# ---------------------------------------------------------------------------
# verify_signature unit
# ---------------------------------------------------------------------------


def test_verify_signature_unit():
    body = b'{"hello": "world"}'
    secret = "s3cr3t"
    sig = _sign(secret, body)
    assert verify_signature(secret, body, sig) is True
    assert verify_signature(secret, body, "sha256=" + sig) is True
    assert verify_signature(secret, body, "deadbeef") is False
    assert verify_signature(secret, body, "") is False
