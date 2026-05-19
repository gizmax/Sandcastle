"""EnvironmentWorker runtime for Anthropic Managed Agents self-hosted sandboxes.

Companion to ``self_hosted_sandbox.py``. Where that module owns the *typed
config* and the session-create payload, this module owns the *worker* that
actually pulls work items off ``/v1/environments/{id}/work`` and runs them.

Two modes:

* ``poll()`` - always-on loop. Claims work items, dispatches each to a user
  callback, posts the result back. Reclaims stuck items every cycle.
* ``handle_one(work_id)`` - webhook-triggered single-item dispatch. Same
  lifecycle, no loop.

Lifecycle per work item:

1. ``POST /v1/environments/{environment_id}/work/{work_id}/claim``
2. user ``on_work(WorkerContext)`` callback - typically shells out to a
   provider-specific spawn.sh from the Anthropic cookbook
3. ``POST .../complete`` on success, ``POST .../fail`` on exception

Token safety:

The environment key (``sk-ant-oat01-...``) is the ONLY credential the
worker is allowed to hold. We validate the prefix via
``self_hosted_sandbox.assert_env_key_format`` at construction time so a
mispasted ``ANTHROPIC_API_KEY`` fails fast before any network call.

Default toolset:

``default_toolset()`` returns the 6 tools from Anthropic's
``beta_agent_toolset_20260401``: bash, read, write, edit, glob, grep.
These are *definitions* (name + description + input schema) - the
actual execution lives in the user's spawn.sh.

Reclaim semantics:

Anthropic's docs (May 2026) recommend reclaiming work items whose claim
is older than ~2000 ms; the queue assumes the previous worker died.
We default ``reclaim_older_than_ms=2000``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from sandcastle.engine.self_hosted_sandbox import assert_env_key_format

logger = logging.getLogger(__name__)


DEFAULT_BETA_HEADER = "managed-agents-2026-04-01"
DEFAULT_BASE_URL = "https://api.anthropic.com"

# Anthropic-recommended defaults (docs May 2026).
_DEFAULT_BLOCK_MS = 1000
_DEFAULT_MAX_IDLE_MS = 60_000
_DEFAULT_RECLAIM_MS = 2_000


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class WorkerError(Exception):
    """Base error for the EnvironmentWorker."""


class WorkClaimFailedError(WorkerError):
    """Raised when a claim attempt fails for a reason other than 404.

    A 404 means another worker beat us to it; that is NOT an error.
    """


class WorkerStoppedError(WorkerError):
    """Raised when the worker is asked to act after it has stopped."""


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


def default_toolset() -> list[dict[str, Any]]:
    """Return the 6 default tools from beta_agent_toolset_20260401.

    These are *definitions* only - the worker forwards them to the
    session-create call; actual execution happens inside the
    user-supplied spawn.sh.
    """
    return [
        {
            "name": "bash",
            "description": (
                "Run a shell command in the sandbox. Returns combined "
                "stdout+stderr and the exit status."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_ms": {"type": "integer"},
                },
                "required": ["command"],
            },
        },
        {
            "name": "read",
            "description": "Read the contents of a file inside the sandbox.",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
        {
            "name": "write",
            "description": (
                "Write content to a file inside the sandbox, creating "
                "parent directories as needed."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
        {
            "name": "edit",
            "description": (
                "Apply an in-place edit to a file by replacing an exact "
                "substring."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
        {
            "name": "glob",
            "description": "Expand a glob pattern under the sandbox workdir.",
            "input_schema": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
        {
            "name": "grep",
            "description": (
                "Search file contents for a regex pattern inside the "
                "sandbox."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["pattern"],
            },
        },
    ]


# ---------------------------------------------------------------------------
# Worker context
# ---------------------------------------------------------------------------


@dataclass
class WorkerContext:
    """Per-work-item context handed to the user's on_work callback.

    Anything the callback needs to drive the tool loop (HTTP client,
    sandbox workdir, session/work identifiers) is exposed here. We pass
    a context object instead of positional args so future fields stay
    backward-compatible.
    """

    session_id: str
    work_id: str
    environment_id: str
    environment_key: str
    workdir: str
    client: httpx.AsyncClient
    # Raw work payload as returned by the claim endpoint - tools,
    # messages, model selection live here.
    payload: dict[str, Any] = field(default_factory=dict)


OnWork = Callable[[WorkerContext], Awaitable[dict[str, Any] | None]]


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


class SelfHostedWorker:
    """Async worker that drains an Anthropic environment work queue.

    Construction does NOT touch the network; it only validates the env
    key prefix. Use ``poll()`` for the always-on loop or
    ``handle_one(work_id)`` for webhook dispatch.

    Parameters mirror the knobs in the Anthropic cookbook worker
    template (May 2026), so the dataclass-ish kwargs map 1:1 to the
    Python flags users will be reading in those examples.
    """

    def __init__(
        self,
        *,
        environment_id: str,
        environment_key: str,
        on_work: OnWork,
        base_url: str = DEFAULT_BASE_URL,
        beta_header: str = DEFAULT_BETA_HEADER,
        max_idle_ms: int = _DEFAULT_MAX_IDLE_MS,
        reclaim_older_than_ms: int = _DEFAULT_RECLAIM_MS,
        workdir: str = "/tmp/sandcastle-work",
        block_ms: int = _DEFAULT_BLOCK_MS,
        drain: bool = False,
        auto_stop: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # Fail fast on a bad key shape; we never want a worker to
        # surface a 401 mid-loop because of an obvious format error.
        assert_env_key_format(environment_key)
        if not environment_id:
            raise ValueError("environment_id must be a non-empty string")
        if not callable(on_work):
            raise TypeError("on_work must be an async callable")

        self.environment_id = environment_id
        self._environment_key = environment_key
        self.base_url = base_url.rstrip("/")
        self.beta_header = beta_header
        self.on_work = on_work
        self.max_idle_ms = max_idle_ms
        self.reclaim_older_than_ms = reclaim_older_than_ms
        self.workdir = workdir
        self.block_ms = block_ms
        self.drain = drain
        self.auto_stop = auto_stop

        # Test seam: callers can inject a pre-built AsyncClient (e.g.
        # one wired to httpx.MockTransport). When we create the client
        # ourselves we close it on shutdown.
        self._client = client
        self._owns_client = client is None
        self._stopped = False

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    @property
    def environment_key(self) -> str:
        """Expose the cached env key for the WorkerContext.

        Kept as a property so future logic (rotation, refresh) has a
        single point to hook into without breaking callers.
        """
        return self._environment_key

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._environment_key,
            "anthropic-beta": self.beta_header,
            "content-type": "application/json",
        }

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url)
        return self._client

    def _url(self, path: str) -> str:
        # When the client carries a base_url we still pass the full
        # path-and-query; httpx merges them correctly.
        return f"{self.base_url}{path}"

    # ------------------------------------------------------------------
    # Endpoints (thin wrappers - one method per HTTP call so tests can
    # assert against URL shapes)
    # ------------------------------------------------------------------
    async def _list_work(self) -> dict[str, Any]:
        """List pending+claimed work items for this environment."""
        client = self._ensure_client()
        resp = await client.get(
            self._url(f"/v1/environments/{self.environment_id}/work"),
            headers=self._headers(),
            params={"block_ms": self.block_ms},
        )
        if resp.status_code >= 400:
            raise WorkerError(
                f"List work failed: HTTP {resp.status_code} {resp.text}"
            )
        return resp.json()

    async def _claim(self, work_id: str) -> dict[str, Any] | None:
        """Claim a work item. Returns None on 404 (already claimed)."""
        client = self._ensure_client()
        resp = await client.post(
            self._url(
                f"/v1/environments/{self.environment_id}/work/{work_id}/claim"
            ),
            headers=self._headers(),
            json={},
        )
        if resp.status_code == 404:
            # Another worker beat us to it - not an error.
            logger.debug("work %s already claimed elsewhere", work_id)
            return None
        if resp.status_code >= 400:
            raise WorkClaimFailedError(
                f"Claim of {work_id} failed: HTTP {resp.status_code} "
                f"{resp.text}"
            )
        return resp.json()

    async def _complete(self, work_id: str, result: dict[str, Any]) -> None:
        client = self._ensure_client()
        resp = await client.post(
            self._url(
                f"/v1/environments/{self.environment_id}/work/{work_id}/complete"
            ),
            headers=self._headers(),
            json={"result": result},
        )
        if resp.status_code >= 400:
            raise WorkerError(
                f"Complete of {work_id} failed: HTTP {resp.status_code} "
                f"{resp.text}"
            )

    async def _fail(self, work_id: str, error: str) -> None:
        client = self._ensure_client()
        resp = await client.post(
            self._url(
                f"/v1/environments/{self.environment_id}/work/{work_id}/fail"
            ),
            headers=self._headers(),
            json={"error": error},
        )
        if resp.status_code >= 400:
            # Don't raise here - failing to record a fail is logged but
            # must not kill the worker loop.
            logger.warning(
                "Fail-report for %s rejected: HTTP %s %s",
                work_id,
                resp.status_code,
                resp.text,
            )

    async def _reclaim(self, work_id: str) -> None:
        """Best-effort reclaim of a stuck work item.

        Anthropic returns 404 if the item has since completed; we treat
        that as a no-op rather than an error.
        """
        client = self._ensure_client()
        try:
            resp = await client.post(
                self._url(
                    f"/v1/environments/{self.environment_id}/work/"
                    f"{work_id}/reclaim"
                ),
                headers=self._headers(),
                json={},
            )
        except httpx.HTTPError as exc:
            logger.warning("Reclaim of %s raised: %s", work_id, exc)
            return
        if resp.status_code == 404:
            logger.debug("Reclaim target %s no longer exists", work_id)
            return
        if resp.status_code >= 400:
            logger.warning(
                "Reclaim of %s rejected: HTTP %s %s",
                work_id,
                resp.status_code,
                resp.text,
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def handle_one(self, work_id: str) -> None:
        """Webhook entry point - claim + run + complete a single item."""
        if self._stopped:
            raise WorkerStoppedError("worker was stopped")
        claimed = await self._claim(work_id)
        if claimed is None:
            # Lost the race; nothing to do.
            return
        await self._run_one(work_id, claimed)

    async def poll(self) -> None:
        """Drain the work queue until idle or drained.

        Exit conditions:

        * ``drain=True`` and the queue depth reaches zero
        * ``auto_stop=True`` and we have been idle for ``max_idle_ms``
        * external caller flipped ``self._stopped``
        """
        if self._stopped:
            raise WorkerStoppedError("worker was stopped")

        idle_ms = 0
        try:
            while not self._stopped:
                listing = await self._list_work()
                items = listing.get("items", []) or []
                depth = listing.get("depth", len(items))

                # Reclaim stuck items first so they reappear as
                # unclaimed candidates within this same cycle.
                await self._reclaim_stale(items)

                # Pick up unclaimed items.
                unclaimed = [
                    it for it in items if not it.get("claimed_by")
                ]

                if not unclaimed:
                    if self.drain and depth == 0:
                        logger.info(
                            "drain complete - queue depth zero, exiting"
                        )
                        return
                    idle_ms += self.block_ms
                    if self.auto_stop and idle_ms >= self.max_idle_ms:
                        logger.info(
                            "idle for %s ms, exiting (auto_stop)", idle_ms
                        )
                        return
                    await asyncio.sleep(self.block_ms / 1000)
                    continue

                idle_ms = 0
                for item in unclaimed:
                    work_id = item.get("id") or item.get("work_id")
                    if not work_id:
                        continue
                    try:
                        claimed = await self._claim(work_id)
                    except WorkClaimFailedError as exc:
                        logger.warning("claim failed: %s", exc)
                        continue
                    if claimed is None:
                        continue
                    await self._run_one(work_id, claimed)
        finally:
            if self._owns_client and self._client is not None:
                await self._client.aclose()
                self._client = None

    def stop(self) -> None:
        """Request the poll loop to exit at the next iteration."""
        self._stopped = True

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    async def _run_one(self, work_id: str, claimed: dict[str, Any]) -> None:
        """Run the user callback and post the result."""
        ctx = WorkerContext(
            session_id=claimed.get("session_id", ""),
            work_id=work_id,
            environment_id=self.environment_id,
            environment_key=self._environment_key,
            workdir=self.workdir,
            client=self._ensure_client(),
            payload=claimed,
        )
        try:
            result = await self.on_work(ctx)
        except Exception as exc:  # noqa: BLE001 - we surface the error
            logger.exception("on_work raised for %s", work_id)
            await self._fail(work_id, f"{type(exc).__name__}: {exc}")
            return
        await self._complete(work_id, result or {})

    async def _reclaim_stale(self, items: list[dict[str, Any]]) -> None:
        """Reclaim items whose claim is older than reclaim_older_than_ms."""
        for it in items:
            age_ms = it.get("claim_age_ms")
            if age_ms is None or age_ms < self.reclaim_older_than_ms:
                continue
            work_id = it.get("id") or it.get("work_id")
            if not work_id:
                continue
            await self._reclaim(work_id)
