"""Tests for the EnvironmentWorker (self_hosted_worker).

All HTTP traffic is mocked via ``httpx.MockTransport`` so no network is
involved. Each test isolates one behavioural contract from the worker
docstring: claim, complete, fail, reclaim, drain, auto_stop, idle exit,
webhook dispatch.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from sandcastle.engine.self_hosted_sandbox import EnvironmentKeyFormatError
from sandcastle.engine.self_hosted_worker import (
    DEFAULT_BETA_HEADER,
    SelfHostedWorker,
    WorkerContext,
    default_toolset,
)

GOOD_KEY = "sk-ant-oat01-test-key-abc123"


# ---------------------------------------------------------------------------
# Mock transport helper
# ---------------------------------------------------------------------------


class _Router:
    """Tiny request router for httpx.MockTransport.

    Maps ``(method, path)`` -> handler. Records every request for
    assertions. Path matching is exact - tests register the exact URL
    they expect the worker to hit.
    """

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Any] = {}
        self.calls: list[httpx.Request] = []

    def add(self, method: str, path: str, handler):
        self.routes[(method.upper(), path)] = handler

    def transport(self) -> httpx.MockTransport:
        def _dispatch(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            key = (request.method.upper(), request.url.path)
            handler = self.routes.get(key)
            if handler is None:
                return httpx.Response(
                    404, json={"error": f"no mock for {key}"}
                )
            if callable(handler):
                return handler(request)
            return handler

        return httpx.MockTransport(_dispatch)


def _worker(
    router: _Router,
    *,
    on_work,
    environment_id: str = "env_abc",
    environment_key: str = GOOD_KEY,
    drain: bool = False,
    auto_stop: bool = True,
    max_idle_ms: int = 5,
    block_ms: int = 1,
    reclaim_older_than_ms: int = 2000,
) -> SelfHostedWorker:
    client = httpx.AsyncClient(transport=router.transport())
    return SelfHostedWorker(
        environment_id=environment_id,
        environment_key=environment_key,
        on_work=on_work,
        client=client,
        drain=drain,
        auto_stop=auto_stop,
        max_idle_ms=max_idle_ms,
        block_ms=block_ms,
        reclaim_older_than_ms=reclaim_older_than_ms,
    )


# ---------------------------------------------------------------------------
# WorkerContext + default_toolset
# ---------------------------------------------------------------------------


def test_worker_context_shape():
    ctx = WorkerContext(
        session_id="sess_1",
        work_id="work_1",
        environment_id="env_1",
        environment_key=GOOD_KEY,
        workdir="/tmp/x",
        client=httpx.AsyncClient(),
    )
    assert ctx.session_id == "sess_1"
    assert ctx.work_id == "work_1"
    assert ctx.environment_id == "env_1"
    assert ctx.environment_key.startswith("sk-ant-oat01-")
    assert ctx.workdir == "/tmp/x"
    assert ctx.payload == {}


def test_default_toolset_returns_six_named_tools():
    tools = default_toolset()
    assert len(tools) == 6
    names = {t["name"] for t in tools}
    assert names == {"bash", "read", "write", "edit", "glob", "grep"}
    for tool in tools:
        assert tool["description"]
        assert tool["input_schema"]["type"] == "object"


# ---------------------------------------------------------------------------
# Key-format guard runs BEFORE network
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bad_env_key_prefix_raises_before_network():
    # If we get past the format check, the AsyncClient would be needed.
    # We don't even build one - the constructor must explode first.
    with pytest.raises(EnvironmentKeyFormatError):
        SelfHostedWorker(
            environment_id="env_1",
            environment_key="sk-ant-not-an-oat-key",
            on_work=lambda ctx: None,  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# poll() happy path - one item
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_claims_one_item_runs_callback_and_completes():
    router = _Router()
    seen: list[WorkerContext] = []

    list_calls = {"n": 0}

    def list_handler(request: httpx.Request) -> httpx.Response:
        list_calls["n"] += 1
        if list_calls["n"] == 1:
            return httpx.Response(
                200,
                json={
                    "items": [{"id": "w1", "claimed_by": None}],
                    "depth": 1,
                },
            )
        return httpx.Response(200, json={"items": [], "depth": 0})

    router.add("GET", "/v1/environments/env_abc/work", list_handler)
    router.add(
        "POST",
        "/v1/environments/env_abc/work/w1/claim",
        httpx.Response(
            200, json={"session_id": "sess_w1", "messages": []}
        ),
    )
    router.add(
        "POST",
        "/v1/environments/env_abc/work/w1/complete",
        httpx.Response(200, json={"ok": True}),
    )

    async def on_work(ctx: WorkerContext) -> dict[str, Any]:
        seen.append(ctx)
        return {"answer": 42}

    worker = _worker(router, on_work=on_work, drain=True)
    await worker.poll()

    assert len(seen) == 1
    assert seen[0].work_id == "w1"
    assert seen[0].session_id == "sess_w1"
    # complete call carried our result.
    complete_call = next(
        c for c in router.calls if c.url.path.endswith("/complete")
    )
    body = complete_call.read()
    assert b"answer" in body


# ---------------------------------------------------------------------------
# poll() with no work then drain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_with_no_work_exits_under_drain():
    router = _Router()
    router.add(
        "GET",
        "/v1/environments/env_abc/work",
        httpx.Response(200, json={"items": [], "depth": 0}),
    )

    async def on_work(ctx: WorkerContext) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError("on_work should not be called on empty queue")

    worker = _worker(router, on_work=on_work, drain=True, auto_stop=False)
    await worker.poll()
    # One list call, no claims.
    assert any(c.url.path.endswith("/work") for c in router.calls)
    assert not any(c.url.path.endswith("/claim") for c in router.calls)


# ---------------------------------------------------------------------------
# poll() reclaim of stale items
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_reclaims_stale_items():
    router = _Router()
    reclaim_calls: list[httpx.Request] = []

    list_calls = {"n": 0}

    def list_handler(request: httpx.Request) -> httpx.Response:
        list_calls["n"] += 1
        if list_calls["n"] == 1:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "stuck1",
                            "claimed_by": "worker_dead",
                            "claim_age_ms": 5000,
                        }
                    ],
                    "depth": 1,
                },
            )
        return httpx.Response(200, json={"items": [], "depth": 0})

    def reclaim_handler(request: httpx.Request) -> httpx.Response:
        reclaim_calls.append(request)
        return httpx.Response(200, json={"ok": True})

    router.add("GET", "/v1/environments/env_abc/work", list_handler)
    router.add(
        "POST",
        "/v1/environments/env_abc/work/stuck1/reclaim",
        reclaim_handler,
    )

    async def on_work(ctx: WorkerContext) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError("should not run on stale (still-claimed) item")

    worker = _worker(
        router, on_work=on_work, drain=True, reclaim_older_than_ms=2000
    )
    await worker.poll()

    assert len(reclaim_calls) == 1


# ---------------------------------------------------------------------------
# poll() respects max_idle_ms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_respects_max_idle_ms():
    router = _Router()
    router.add(
        "GET",
        "/v1/environments/env_abc/work",
        httpx.Response(200, json={"items": [], "depth": 0}),
    )

    async def on_work(ctx: WorkerContext) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError

    worker = _worker(
        router,
        on_work=on_work,
        drain=False,
        auto_stop=True,
        max_idle_ms=3,
        block_ms=1,
    )
    # Bounded - max_idle_ms 3 with block_ms 1 means at most ~3 list
    # calls before auto_stop fires.
    await worker.poll()
    # At least one list call happened, but loop terminated cleanly.
    assert any(c.url.path.endswith("/work") for c in router.calls)


# ---------------------------------------------------------------------------
# handle_one() webhook mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_one_webhook_mode_claims_runs_completes():
    router = _Router()
    router.add(
        "POST",
        "/v1/environments/env_abc/work/wh1/claim",
        httpx.Response(
            200, json={"session_id": "sess_wh1", "messages": []}
        ),
    )
    router.add(
        "POST",
        "/v1/environments/env_abc/work/wh1/complete",
        httpx.Response(200, json={"ok": True}),
    )

    seen: list[str] = []

    async def on_work(ctx: WorkerContext) -> dict[str, Any]:
        seen.append(ctx.work_id)
        return {"ok": True}

    worker = _worker(router, on_work=on_work)
    await worker.handle_one("wh1")

    assert seen == ["wh1"]
    paths = [c.url.path for c in router.calls]
    assert "/v1/environments/env_abc/work/wh1/claim" in paths
    assert "/v1/environments/env_abc/work/wh1/complete" in paths


# ---------------------------------------------------------------------------
# on_work raises -> POST /fail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_work_exception_posts_fail():
    router = _Router()
    router.add(
        "POST",
        "/v1/environments/env_abc/work/wf1/claim",
        httpx.Response(200, json={"session_id": "sess_wf1"}),
    )
    fail_calls: list[httpx.Request] = []

    def fail_handler(request: httpx.Request) -> httpx.Response:
        fail_calls.append(request)
        return httpx.Response(200, json={"ok": True})

    router.add(
        "POST", "/v1/environments/env_abc/work/wf1/fail", fail_handler
    )

    async def on_work(ctx: WorkerContext) -> dict[str, Any]:
        raise RuntimeError("tool loop blew up")

    worker = _worker(router, on_work=on_work)
    await worker.handle_one("wf1")

    assert len(fail_calls) == 1
    body = fail_calls[0].read()
    assert b"tool loop blew up" in body


# ---------------------------------------------------------------------------
# claim 404 -> continue, no /complete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_404_skips_run():
    router = _Router()
    list_calls = {"n": 0}

    def list_handler(request: httpx.Request) -> httpx.Response:
        list_calls["n"] += 1
        if list_calls["n"] == 1:
            return httpx.Response(
                200,
                json={
                    "items": [{"id": "raced", "claimed_by": None}],
                    "depth": 1,
                },
            )
        return httpx.Response(200, json={"items": [], "depth": 0})

    router.add("GET", "/v1/environments/env_abc/work", list_handler)
    router.add(
        "POST",
        "/v1/environments/env_abc/work/raced/claim",
        httpx.Response(404, json={"error": "already claimed"}),
    )

    ran = {"yes": False}

    async def on_work(ctx: WorkerContext) -> dict[str, Any]:  # pragma: no cover
        ran["yes"] = True
        return {}

    worker = _worker(router, on_work=on_work, drain=True)
    await worker.poll()

    assert ran["yes"] is False
    assert not any(c.url.path.endswith("/complete") for c in router.calls)


# ---------------------------------------------------------------------------
# reclaim of nonexistent item is caught
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reclaim_404_is_silent():
    router = _Router()
    list_calls = {"n": 0}

    def list_handler(request: httpx.Request) -> httpx.Response:
        list_calls["n"] += 1
        if list_calls["n"] == 1:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "ghost",
                            "claimed_by": "worker_x",
                            "claim_age_ms": 9999,
                        }
                    ],
                    "depth": 1,
                },
            )
        return httpx.Response(200, json={"items": [], "depth": 0})

    router.add("GET", "/v1/environments/env_abc/work", list_handler)
    router.add(
        "POST",
        "/v1/environments/env_abc/work/ghost/reclaim",
        httpx.Response(404, json={"error": "gone"}),
    )

    async def on_work(ctx: WorkerContext) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError

    worker = _worker(router, on_work=on_work, drain=True)
    # Should not raise.
    await worker.poll()


# ---------------------------------------------------------------------------
# drain=True with depth 0 exits cleanly even if items still claimed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_exits_when_depth_zero():
    router = _Router()
    router.add(
        "GET",
        "/v1/environments/env_abc/work",
        httpx.Response(200, json={"items": [], "depth": 0}),
    )

    async def on_work(ctx: WorkerContext) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError

    worker = _worker(router, on_work=on_work, drain=True)
    await worker.poll()
    # Only the list call should have happened.
    assert len(
        [c for c in router.calls if c.url.path.endswith("/work")]
    ) == 1


# ---------------------------------------------------------------------------
# auto_stop=False keeps polling - we use stop() to break out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_stop_false_keeps_polling_until_stop():
    router = _Router()
    list_count = {"n": 0}
    worker_ref: dict[str, SelfHostedWorker] = {}

    def list_handler(request: httpx.Request) -> httpx.Response:
        list_count["n"] += 1
        if list_count["n"] >= 3:
            # After several empty polls, ask the worker to exit so the
            # test bounds itself.
            worker_ref["w"].stop()
        return httpx.Response(200, json={"items": [], "depth": 0})

    router.add("GET", "/v1/environments/env_abc/work", list_handler)

    async def on_work(ctx: WorkerContext) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError

    worker = _worker(
        router,
        on_work=on_work,
        drain=False,
        auto_stop=False,
        max_idle_ms=1,
        block_ms=1,
    )
    worker_ref["w"] = worker
    await worker.poll()
    assert list_count["n"] >= 3


# ---------------------------------------------------------------------------
# Headers carry the env key + beta header
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requests_carry_env_key_and_beta_header():
    router = _Router()
    router.add(
        "GET",
        "/v1/environments/env_abc/work",
        httpx.Response(200, json={"items": [], "depth": 0}),
    )

    async def on_work(ctx: WorkerContext) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError

    worker = _worker(router, on_work=on_work, drain=True)
    await worker.poll()

    req = next(c for c in router.calls if c.url.path.endswith("/work"))
    assert req.headers["x-api-key"] == GOOD_KEY
    assert req.headers["anthropic-beta"] == DEFAULT_BETA_HEADER


# ---------------------------------------------------------------------------
# stop() prevents further use
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_blocks_subsequent_handle_one():
    router = _Router()

    async def on_work(ctx: WorkerContext) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError

    worker = _worker(router, on_work=on_work)
    worker.stop()
    with pytest.raises(Exception):
        await worker.handle_one("anything")
