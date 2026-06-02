"""Tests for shareable run permalinks - the public page must never leak secrets or PII.

The redaction gate is the load-bearing safety control: a finished run is published to a
public, auth-bypassed URL, so every string in every step output has to pass through the
secret scrubber and the PII router first. These tests prove the gate removes a battery of
secret shapes and PII while keeping benign content, that the rendered HTML page never
contains a planted secret, and that the public route only serves shared runs.
"""

from __future__ import annotations

import asyncio
import json
import types
import uuid

import pytest

from sandcastle.api.share import redact_public, render_run_page

SECRETS = [
    "sk-ABC123def456ghi789jkl",        # OpenAI-style key
    "ghp_AbC123dEf456GhI789jKl0",       # GitHub PAT
    "AKIAIOSFODNN7EXAMPLE",             # AWS access key id
    "ya29.A0ARrdaM-SECRETtoken123456",  # Google OAuth (via bearer assignment)
    "eyJhbGciOi.eyJzdWIiOiJ.SflKxwRJSMe",  # JWT
]
PII = ["victim@example.com", "555-123-4567", "123-45-6789"]


def test_redact_removes_secrets_and_pii_keeps_benign():
    payload = {
        "openai_key": "sk-ABC123def456ghi789jkl",
        "github": "ghp_AbC123dEf456GhI789jKl0",
        "aws": "AKIAIOSFODNN7EXAMPLE",
        "authorization": "Bearer ya29.A0ARrdaM-SECRETtoken123456",
        "jwt": "eyJhbGciOi.eyJzdWIiOiJ.SflKxwRJSMe",
        "password=": "password=hunter2supersecret",
        "db_url": "postgres://dbuser:dbpass@host:5432/app",
        "nested": [{"email": "victim@example.com"}, "call 555-123-4567", "ssn 123-45-6789"],
        "benign": "Rolled back deploy abc123, error rate recovered.",
    }
    blob = json.dumps(redact_public(payload))
    for secret in SECRETS + ["hunter2supersecret", "dbuser:dbpass@host"]:
        assert secret not in blob, f"secret leaked: {secret}"
    for pii in PII:
        assert pii not in blob, f"PII leaked: {pii}"
    assert "Rolled back deploy" in blob, "benign content should be preserved"


def test_redact_handles_primitives_and_none():
    assert redact_public(42) == 42
    assert redact_public(None) is None
    assert redact_public(True) is True
    assert redact_public(["a@b.com", 7]) == ["[EMAIL]", 7]


def _fake_run(output):
    step = types.SimpleNamespace(
        step_id="diagnose", status="completed", model="google/gemini-2.5-pro",
        cost_usd=0.012, duration_seconds=1.4, started_at=1, error=None, output_data=output,
    )
    return types.SimpleNamespace(
        workflow_name="closed-loop-autoremediator", status="completed",
        total_cost_usd=0.012, started_at="t0", completed_at="t1", error=None, steps=[step],
    )


def test_rendered_page_is_html_and_leak_free():
    run = _fake_run({"fix": "rollback", "leaked": "sk-ABC123def456ghi789jkl", "to": "victim@example.com"})
    html = render_run_page(run)
    assert "<html" in html.lower() and len(html) > 1000
    assert "closed-loop-autoremediator" in html           # workflow shown
    assert "gemini-2.5-pro" in html                        # provider transparency
    assert "sk-ABC123def456ghi789jkl" not in html          # secret scrubbed
    assert "victim@example.com" not in html                # PII scrubbed
    assert "Run this yourself" in html                     # viral loop CTA


# ----------------------------------------------------------------------------
# Route-level: the public page only serves shared runs and stays leak-free.
# ----------------------------------------------------------------------------

def _seed_shared_run(token: str, secret_in_output: str) -> None:
    from sandcastle.models.db import Run, RunStatus, RunStep, StepStatus, async_session, init_db

    async def _seed():
        await init_db()
        run_id = uuid.uuid4()
        async with async_session() as session:
            session.add(Run(
                id=run_id, workflow_name="shared-wf", status=RunStatus.COMPLETED,
                total_cost_usd=0.05, share_token=token,
            ))
            session.add(RunStep(
                id=uuid.uuid4(), run_id=run_id, step_id="s1", parallel_index=None,
                status=StepStatus.COMPLETED, model="sonnet", cost_usd=0.05,
                duration_seconds=2.0, output_data={"note": "ok", "key": secret_in_output},
            ))
            await session.commit()

    asyncio.run(_seed())


def test_public_route_serves_shared_run_without_leaking():
    from fastapi.testclient import TestClient

    from sandcastle.main import app

    token = "pubtoken_test_123"
    _seed_shared_run(token, "sk-ROUTELEAK999shouldNotAppear")
    client = TestClient(app)

    resp = client.get(f"/api/r/{token}")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    body = resp.text
    assert "shared-wf" in body
    assert "sk-ROUTELEAK999shouldNotAppear" not in body, "route leaked a secret"


def test_public_route_unknown_token_is_404():
    from fastapi.testclient import TestClient

    from sandcastle.main import app

    client = TestClient(app)
    resp = client.get("/api/r/this-token-does-not-exist")
    assert resp.status_code == 404
