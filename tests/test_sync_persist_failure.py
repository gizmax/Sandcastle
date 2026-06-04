"""The sync run endpoint must not report 'completed' when post-run persistence fails.

Previously a failed post-execution DB commit was swallowed and the caller still got a
'completed' result while the DB row stayed RUNNING (silent data loss + a lie about
durability). The scoped fix retries the write and, if it still fails, returns
status='verification_pending' instead.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient


def _flaky_session_factory():
    """async_session() whose commit fails ONLY on the post-run update session.

    The post-run update is the only path that calls session.get(); the run-creation
    path uses session.add(). So commit raises iff get() was called on that session -
    order-independent, unlike a call counter.
    """

    def make_cm():
        sess = AsyncMock()
        state = {"post_run": False}

        async def _get(*_a, **_k):
            state["post_run"] = True
            return MagicMock()  # truthy db_run so the update path runs + commits

        async def _commit():
            if state["post_run"]:
                raise Exception("simulated post-run DB write failure")

        sess.get = _get
        sess.commit = _commit
        sess.add = MagicMock()
        sess.execute = AsyncMock(return_value=MagicMock())
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=sess)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    return make_cm


def test_sync_run_reports_verification_pending_on_persist_failure():
    import sandcastle.api.routes as routes
    from sandcastle.engine.executor import WorkflowResult
    from sandcastle.main import app

    client = TestClient(app, raise_server_exceptions=False)
    yaml_content = (
        "name: persist_fail_test\n"
        "steps:\n"
        "  - id: s\n"
        "    type: llm\n"
        "    model: haiku\n"
        "    prompt: hi\n"
    )

    async def fake_execute(*_a, **kwargs):
        return WorkflowResult(
            run_id=kwargs.get("run_id", "00000000-0000-0000-0000-000000000000"),
            outputs={"s": "done"},
            total_cost_usd=0.0,
            status="completed",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )

    with patch("sandcastle.api.routes.async_session", side_effect=_flaky_session_factory()), patch(
        "sandcastle.api.routes.execute_workflow", side_effect=fake_execute
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            original = routes.settings.workflows_dir
            routes.settings.workflows_dir = tmpdir
            try:
                resp = client.post(
                    "/api/workflows/run/sync",
                    json={"workflow": yaml_content, "input": {}},
                )
            finally:
                routes.settings.workflows_dir = original

    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    # The run finished but persistence failed -> must NOT claim "completed".
    assert body["status"] == "verification_pending", body
