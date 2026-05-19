// Vercel webhook handler for Anthropic Managed Agents (self-hosted sandboxes).
//
// Pattern based on:
//   https://github.com/anthropics/claude-cookbooks/tree/main/managed_agents/
//   self_hosted_sandboxes/vercel
//
// Receives `session.status_run_started`, verifies the signature, drains the
// environment work queue, and spawns one `@vercel/sandbox` microVM per work
// item. The Anthropic environment key NEVER enters the sandbox -- it lives
// only inside this Vercel Function. The sandbox authenticates outbound
// Anthropic traffic via Vercel's firewall-level credential injection
// (`networkPolicy.allow`).

import Anthropic from "@anthropic-ai/sdk";
import { Sandbox } from "@vercel/sandbox";
import ms from "ms";

// Anthropic client lives in the FUNCTION, not the sandbox. The env key is
// read here and used to verify webhooks + drain the work queue, but it is
// deliberately omitted from the `env` map passed to `sandbox.spawn()`.
const client = new Anthropic({
  apiKey: process.env.ANTHROPIC_ENVIRONMENT_KEY,
});

export default async function handler(req, res) {
  if (req.method !== "POST") {
    return res.status(405).json({ error: "method_not_allowed" });
  }

  // ---- Step 1: verify webhook signature ----
  let event;
  try {
    event = await client.beta.webhooks.unwrap({
      signature: req.headers["webhook-signature"],
      timestamp: req.headers["webhook-timestamp"],
      id: req.headers["webhook-id"],
      body: typeof req.body === "string" ? req.body : JSON.stringify(req.body),
      secret: process.env.ANTHROPIC_WEBHOOK_SECRET,
    });
  } catch (err) {
    console.warn("[webhook] signature verification failed", err.message);
    return res.status(401).json({ error: "invalid_signature" });
  }

  if (event.type !== "session.status_run_started") {
    return res.status(200).json({ status: "ignored", type: event.type });
  }

  // ---- Step 2: drain work queue (cap at 25 items per webhook) ----
  const environmentId = process.env.ANTHROPIC_ENVIRONMENT_ID;
  const work = [];
  let next;
  while (
    (next = await client.beta.work.poll(environmentId)) &&
    work.length < 25
  ) {
    work.push(next);
  }

  // ---- Step 3: per-item sandbox spawn ----
  const spawned = [];
  for (const item of work) {
    try {
      const sandbox = await Sandbox.create({
        // Total sandbox lifetime. Per Vercel docs the timeout option uses
        // ms-style strings; 1h matches the canonical cookbook.
        timeout: ms("1h"),
        // Firewall-level allowlist. Vercel injects credentials at the
        // network boundary for these hosts so the sandbox can call them
        // without ever holding ANTHROPIC_ENVIRONMENT_KEY in process memory.
        networkPolicy: {
          allow: [
            { host: "api.anthropic.com" },
            { host: "*.anthropic.com" },
            { host: "registry.npmjs.org" },
            { host: "files.pythonhosted.org" },
          ],
        },
      });

      // Ship the worker script.
      await sandbox.writeFiles({
        "runner.mjs": await loadRunnerSource(),
      });
      await sandbox.install();

      // CRITICAL: env map deliberately excludes ANTHROPIC_ENVIRONMENT_KEY.
      // The sandbox authenticates via the firewall, not via a secret it
      // can leak. Only non-secret routing IDs go in.
      await sandbox.spawn(["node", "runner.mjs"], {
        env: {
          SESSION_ID: item.session_id,
          WORK_ITEM_ID: item.id,
          ANTHROPIC_ENVIRONMENT_ID: environmentId,
        },
        detached: true,
      });

      await client.beta.work.ack(item.id);
      spawned.push({ sandboxId: sandbox.id, workItem: item.id });
      console.log(`[webhook] acked work=${item.id} sandbox=${sandbox.id}`);
    } catch (err) {
      // Un-acked items reclaim on the next webhook.
      console.error(`[webhook] failed item=${item.id}: ${err.message}`);
    }
  }

  return res.status(202).json({ status: "ok", spawned });
}

// Lazily load the inner runner source so cold-start stays cheap.
async function loadRunnerSource() {
  const { readFile } = await import("node:fs/promises");
  const { fileURLToPath } = await import("node:url");
  const path = await import("node:path");
  const here = path.dirname(fileURLToPath(import.meta.url));
  return readFile(path.join(here, "..", "sandbox-runner.mjs"), "utf8");
}
