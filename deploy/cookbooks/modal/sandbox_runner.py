"""Modal sandbox runner for Anthropic Managed Agents (self-hosted sandboxes).

Pattern based on:
  https://github.com/anthropics/claude-cookbooks/tree/main/managed_agents/
  self_hosted_sandboxes/modal

Webhook (`@modal.web_endpoint`) receives `session.status_run_started`,
verifies the signature, drains the environment work queue and creates one
`modal.Sandbox` per work item. Each sandbox is GPU-attached (default L4)
and mounts a per-session `modal.Volume` at `/workspace`, so model weights
and intermediate artefacts persist across the 24h `timeout` window.
"""

from __future__ import annotations

import logging
import os

import modal
from anthropic import Anthropic

from image import IMAGE

log = logging.getLogger("modal-runner")
logging.basicConfig(level=logging.INFO)

app = modal.App("cma-self-hosted-sandboxes")
secrets = modal.Secret.from_name("cma-self-hosted-sandboxes-secrets")

# GPU class is configurable so the same code deploys for cheap inference
# (L4, ~$0.80/hr) or large-model fine-tuning (B200, ~$5/hr).
GPU_KIND = os.environ.get("MODAL_GPU", "l4")


@app.function(image=IMAGE, secrets=[secrets])
@modal.web_endpoint(method="POST")
async def webhook(request) -> dict:
    """Handle `session.status_run_started` from the Anthropic control plane."""

    client = Anthropic(api_key=os.environ["ANTHROPIC_ENVIRONMENT_KEY"])

    body = await request.body()
    event = client.beta.webhooks.unwrap(
        body=body,
        headers=dict(request.headers),
        secret=os.environ["ANTHROPIC_WEBHOOK_SECRET"],
    )

    if event.type != "session.status_run_started":
        return {"status": "ignored", "type": event.type}

    env_id = os.environ["ANTHROPIC_ENVIRONMENT_ID"]
    env_key = os.environ["ANTHROPIC_ENVIRONMENT_KEY"]

    spawned: list[str] = []
    async with client.beta.environments.work.poller(
        environment_id=env_id,
        drain=True,
        auto_stop=False,
    ) as poller:
        async for item in poller:
            sandbox_id = _spawn(item.session_id, env_id, env_key)
            spawned.append(sandbox_id)

    return {"status": "ok", "spawned": spawned}


def _spawn(session_id: str, env_id: str, env_key: str) -> str:
    """Create the per-session GPU sandbox + persistent volume."""

    volume = modal.Volume.from_name(
        f"workspace-{session_id}",
        create_if_missing=True,
    )

    sandbox = modal.Sandbox.create(
        "bash",
        "-lc",
        "cd /workspace && ant beta:worker run --once",
        image=IMAGE,
        volumes={"/workspace": volume},
        gpu=GPU_KIND,
        # 24h max lifetime - safety net for runaway sessions.
        timeout=86400,
        # Reap the sandbox 600s after the last bash heartbeat. The next
        # session.status_run_started rebuilds it fresh; /workspace
        # persists via the volume above.
        idle_timeout=600,
        secrets=[secrets],
        env={
            "ANTHROPIC_ENVIRONMENT_ID": env_id,
            "ANTHROPIC_ENVIRONMENT_KEY": env_key,
            "SESSION_ID": session_id,
        },
    )
    log.info("created sandbox=%s session=%s gpu=%s", sandbox.object_id, session_id, GPU_KIND)
    return sandbox.object_id


# Local entrypoint for `modal run sandbox_runner.py` smoke tests.
@app.local_entrypoint()
def smoke() -> None:  # pragma: no cover
    print("Modal app ready. Deploy with: modal deploy sandbox_runner.py")
