"""Daytona sandbox runner for Anthropic Managed Agents (self-hosted sandboxes).

Pattern based on:
  https://github.com/anthropics/claude-cookbooks/tree/main/managed_agents/
  self_hosted_sandboxes/daytona

Webhook orchestrator: receives `session.status_run_started`, verifies the
signature with `client.beta.webhooks.unwrap()`, drains the environment work
queue and spawns one Daytona sandbox per work item. Each sandbox is created
from a pre-baked snapshot (`byoc-env-default`) so cold-start is sub-second
and per-session state survives restarts via Daytona's auto-pause / resume.

Run:
    uvicorn sandbox_runner:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from anthropic import Anthropic
from daytona_sdk import CreateSandboxFromSnapshotParams, Daytona
from fastapi import FastAPI, Header, HTTPException, Request

log = logging.getLogger("daytona-runner")
logging.basicConfig(level=logging.INFO)

# -- env contract (identical to `ant beta:worker poll --on-work`) --
ENV_KEY = os.environ["ANTHROPIC_ENVIRONMENT_KEY"]
ENV_ID = os.environ["ANTHROPIC_ENVIRONMENT_ID"]
WEBHOOK_SECRET = os.environ["ANTHROPIC_WEBHOOK_SECRET"]
SNAPSHOT_NAME = os.environ.get("DAYTONA_SNAPSHOT", "byoc-env-default")

# auto-pause idle threshold: stop the sandbox after N seconds of no activity.
# Daytona keeps the disk snapshot so the next webhook resumes in <1s instead
# of paying the full create+install cost. 600s is the cookbook default.
AUTO_STOP_INTERVAL = int(os.environ.get("DAYTONA_AUTO_STOP_INTERVAL", "600"))

app = FastAPI(title="daytona-sandbox-runner")
client = Anthropic(api_key=ENV_KEY)
daytona = Daytona(api_key=os.environ["DAYTONA_API_KEY"])


@app.post("/webhook")
async def webhook(
    request: Request,
    webhook_id: str = Header(..., alias="webhook-id"),
    webhook_timestamp: str = Header(..., alias="webhook-timestamp"),
    webhook_signature: str = Header(..., alias="webhook-signature"),
) -> dict[str, Any]:
    """Handle `session.status_run_started` from the Anthropic control plane."""

    raw = await request.body()
    try:
        event = client.beta.webhooks.unwrap(
            body=raw,
            headers={
                "webhook-id": webhook_id,
                "webhook-timestamp": webhook_timestamp,
                "webhook-signature": webhook_signature,
            },
            secret=WEBHOOK_SECRET,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("webhook verification failed: %s", exc)
        raise HTTPException(status_code=401, detail="invalid signature") from exc

    if event.type != "session.status_run_started":
        log.info("ignoring event type=%s", event.type)
        return {"status": "ignored"}

    # Drain work items synchronously; each item gets its own sandbox.
    spawned: list[str] = []
    async with client.beta.environments.work.poller(
        environment_id=ENV_ID,
        drain=True,
        auto_stop=False,
    ) as poller:
        async for item in poller:
            sandbox_id = await _spawn_sandbox(item.session_id, item.id)
            spawned.append(sandbox_id)

    return {"status": "ok", "spawned": spawned}


async def _spawn_sandbox(session_id: str, work_item_id: str) -> str:
    """Create a Daytona sandbox from the pre-baked snapshot and run the worker."""

    params = CreateSandboxFromSnapshotParams(
        snapshot=SNAPSHOT_NAME,
        # session_id label lets us route resumed sandboxes back to the right
        # session via daytona.find(labels={...}) on subsequent webhooks.
        labels={
            "byoc.session_id": session_id,
            "byoc.environment_id": ENV_ID,
            "byoc.work_item_id": work_item_id,
        },
        env_vars={
            "ANTHROPIC_ENVIRONMENT_KEY": ENV_KEY,
            "ANTHROPIC_ENVIRONMENT_ID": ENV_ID,
            "SESSION_ID": session_id,
        },
        auto_stop_interval=AUTO_STOP_INTERVAL,
    )

    sandbox = await asyncio.to_thread(daytona.create, params)
    log.info("created sandbox=%s session=%s", sandbox.id, session_id)

    # Run the worker. `ant beta:worker run` reads ANTHROPIC_ENVIRONMENT_* from
    # the env, dispatches one item, then exits. The sandbox auto-pauses
    # `auto_stop_interval` seconds later, preserving /mnt/session/outputs.
    cmd = "cd /mnt/session && ant beta:worker run --once"
    result = await asyncio.to_thread(sandbox.process.exec, cmd, timeout=3600)

    # Capture outputs for downstream auditors. Outputs live on the snapshot
    # disk so they survive auto-pause/resume cycles.
    outputs = await asyncio.to_thread(
        sandbox.process.exec,
        "ls -la /mnt/session/outputs",
    )
    log.info(
        "worker exit=%s outputs=%s",
        result.exit_code,
        outputs.result.strip()[:200],
    )

    return sandbox.id


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
