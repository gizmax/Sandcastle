"""Wave-2 fix A: in-process `code` steps are admin-gated by default.

The sync/async run endpoints previously passed `admin_trusted=True`
unconditionally, letting ANY authenticated tenant execute in-process code
steps. Now the handler resolves
`admin_trusted = is_admin(req) or settings.code_steps_allow_untrusted`
and threads it into the executor / enqueue path.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient


def _ok_session_factory():
    """async_session() context manager where all DB ops succeed."""

    def make_cm():
        sess = AsyncMock()
        sess.get = AsyncMock(return_value=MagicMock())
        sess.commit = AsyncMock()
        sess.add = MagicMock()
        sess.execute = AsyncMock(return_value=MagicMock())
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=sess)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    return make_cm


_CODE_WORKFLOW = (
    "name: code_gate_test\n"
    "steps:\n"
    "  - id: transform\n"
    "    type: code\n"
    "    code_config:\n"
    "      code: |\n"
    "        result = 42\n"
)


def _run_sync_capture(is_admin_return: bool, allow_untrusted: bool):
    """POST to /run/sync and return the admin_trusted kwarg the executor saw."""
    import sandcastle.api.routes as routes
    from sandcastle.engine.executor import WorkflowResult
    from sandcastle.main import app

    client = TestClient(app, raise_server_exceptions=False)
    captured: dict = {}

    async def fake_execute(*_a, **kwargs):
        captured["admin_trusted"] = kwargs.get("admin_trusted")
        return WorkflowResult(
            run_id=kwargs.get("run_id", "00000000-0000-0000-0000-000000000000"),
            outputs={"transform": 42},
            total_cost_usd=0.0,
            status="completed",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )

    with (
        patch("sandcastle.api.routes.async_session", side_effect=_ok_session_factory()),
        patch("sandcastle.api.routes.execute_workflow", side_effect=fake_execute),
        patch("sandcastle.api.routes.is_admin", return_value=is_admin_return),
    ):
        original = routes.settings.code_steps_allow_untrusted
        with tempfile.TemporaryDirectory() as tmpdir:
            wf_dir = routes.settings.workflows_dir
            routes.settings.workflows_dir = tmpdir
            routes.settings.code_steps_allow_untrusted = allow_untrusted
            try:
                resp = client.post(
                    "/api/workflows/run/sync",
                    json={"workflow": _CODE_WORKFLOW, "input": {}},
                )
            finally:
                routes.settings.workflows_dir = wf_dir
                routes.settings.code_steps_allow_untrusted = original

    assert resp.status_code == 200, resp.text
    return captured.get("admin_trusted")


def test_non_admin_default_is_untrusted():
    """Non-admin caller with the flag off => admin_trusted False (code steps blocked)."""
    assert _run_sync_capture(is_admin_return=False, allow_untrusted=False) is False


def test_flag_enables_untrusted_code_steps():
    """code_steps_allow_untrusted=True lets a non-admin run code steps."""
    assert _run_sync_capture(is_admin_return=False, allow_untrusted=True) is True


def test_admin_is_always_trusted():
    """Admin caller is trusted regardless of the flag."""
    assert _run_sync_capture(is_admin_return=True, allow_untrusted=False) is True


def test_async_enqueue_threads_admin_trusted():
    """The async endpoint resolves admin_trusted in the handler and enqueues it."""
    import sandcastle.api.routes as routes
    from sandcastle.main import app

    client = TestClient(app, raise_server_exceptions=False)
    captured: dict = {}

    async def fake_enqueue(*_a, **kwargs):
        captured["admin_trusted"] = kwargs.get("admin_trusted")

    with (
        patch("sandcastle.api.routes.async_session", side_effect=_ok_session_factory()),
        patch("sandcastle.api.routes.enqueue_workflow", side_effect=fake_enqueue),
        patch("sandcastle.api.routes.is_admin", return_value=False),
    ):
        original = routes.settings.code_steps_allow_untrusted
        with tempfile.TemporaryDirectory() as tmpdir:
            wf_dir = routes.settings.workflows_dir
            routes.settings.workflows_dir = tmpdir
            routes.settings.code_steps_allow_untrusted = False
            try:
                resp = client.post(
                    "/api/workflows/run",
                    json={"workflow": _CODE_WORKFLOW, "input": {}},
                )
            finally:
                routes.settings.workflows_dir = wf_dir
                routes.settings.code_steps_allow_untrusted = original

    assert resp.status_code == 202, resp.text
    assert captured.get("admin_trusted") is False
