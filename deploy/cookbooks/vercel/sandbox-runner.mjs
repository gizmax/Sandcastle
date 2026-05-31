// Inner worker that runs INSIDE the @vercel/sandbox microVM.
//
// Note: ANTHROPIC_ENVIRONMENT_KEY is deliberately NOT in process.env here.
// All Anthropic traffic egresses via Vercel's firewall, which injects the
// credential at the network boundary based on `networkPolicy.allow`
// matchers configured in api/runner.mjs.

import Anthropic from "@anthropic-ai/sdk";

const client = new Anthropic({
  // The SDK requires a string but Vercel's firewall rewrites the
  // Authorization header on the wire. Using a sentinel makes it obvious
  // that any leaked memory dump would show no real secret.
  apiKey: "vercel-firewall-managed",
});

async function main() {
  const sessionId = process.env.SESSION_ID;
  const workItemId = process.env.WORK_ITEM_ID;
  const environmentId = process.env.ANTHROPIC_ENVIRONMENT_ID;

  if (!sessionId || !workItemId || !environmentId) {
    throw new Error("missing routing env vars");
  }

  const worker = await client.beta.environments.work.worker({
    environmentId,
    workdir: "/mnt/session",
  });

  await worker.handleItem(workItemId);
}

main().catch((err) => {
  console.error("[runner] fatal", err);
  process.exit(1);
});
