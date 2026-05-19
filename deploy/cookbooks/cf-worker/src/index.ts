/**
 * Cloudflare Worker (pure-Worker variant): webhook -> drain environment work
 * queue -> spin up a per-session SessionToolRunner Durable Object that runs
 * the tool dispatcher inside the V8 isolate against an in-memory fake
 * filesystem. No container, no real shell.
 *
 * Webhook event consumed: `session.status_run_started`. The Anthropic SDK
 * auto-sends `anthropic-beta: managed-agents-2026-04-01` for the
 * `beta.environments.*` namespace; the alternate `mcp-client-2025-11-20`
 * beta is documented for MCP tool registration but not required here.
 */
import Anthropic from "@anthropic-ai/sdk";
import type { SessionToolRunner } from "./SessionToolRunner";

export { SessionToolRunner } from "./SessionToolRunner";

export interface Env {
  RUNNER: DurableObjectNamespace<SessionToolRunner>;
  ANTHROPIC_BASE_URL: string;
  ANTHROPIC_ENVIRONMENT_ID: string;
  ANTHROPIC_WEBHOOK_SECRET: string;
  ANTHROPIC_ENVIRONMENT_KEY: string;
}

const MAX_DRAIN = 25;

function redact(s: string): string {
  return s
    .replace(/sk-ant-[A-Za-z0-9._-]+/g, "sk-ant-[REDACTED]")
    .replace(/whsec_[A-Za-z0-9+/=_-]+/g, "whsec_[REDACTED]")
    .replace(/Bearer\s+\S{8,}/gi, "Bearer [REDACTED]");
}

function errDetail(e: unknown): string {
  if (e instanceof Error) {
    const { status, requestID } = e as Error & { status?: number; requestID?: string | null };
    const parts: string[] = [e.constructor.name];
    if (status !== undefined) parts.push(`status=${status}`);
    if (requestID) parts.push(`request_id=${requestID}`);
    if (e.message) parts.push(redact(e.message));
    return parts.join(" ");
  }
  return String(e);
}

interface SpawnRecord {
  readonly session_id: string;
  readonly work_id: string;
  readonly created?: boolean;
  readonly error?: string;
}

async function drainWork(env: Env, anthropic: Anthropic): Promise<SpawnRecord[]> {
  const spawned: SpawnRecord[] = [];
  for (let i = 0; i < MAX_DRAIN; i++) {
    let work: Awaited<ReturnType<typeof anthropic.beta.environments.work.poll>>;
    try {
      work = await anthropic.beta.environments.work.poll(env.ANTHROPIC_ENVIRONMENT_ID, {
        reclaim_older_than_ms: 2000,
      });
    } catch (e) {
      const { status } = (e ?? {}) as { status?: number };
      console.warn(`[webhook] poll failed status=${status ?? "?"}`);
      if (status === 404) continue;
      break;
    }
    if (!work) break;
    if (work.data.type !== "session") continue;

    const sessionId: string = work.data.id;
    try {
      await anthropic.beta.environments.work.ack(work.id, {
        environment_id: env.ANTHROPIC_ENVIRONMENT_ID,
      });

      const stub = env.RUNNER.get(env.RUNNER.idFromName(sessionId));
      const wasLive = await stub.isLive();
      if (!wasLive) {
        await stub.start({
          sessionId,
          workId: work.id,
          environmentId: env.ANTHROPIC_ENVIRONMENT_ID,
          environmentKey: env.ANTHROPIC_ENVIRONMENT_KEY,
          baseURL: env.ANTHROPIC_BASE_URL,
        });
      }
      spawned.push({ session_id: sessionId, work_id: work.id, created: !wasLive });
    } catch (e) {
      const detail = errDetail(e);
      console.warn(`[webhook] FAILED work=${work.id} session=${sessionId}: ${detail}`);
      spawned.push({ session_id: sessionId, work_id: work.id, error: detail });
    }
  }
  return spawned;
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const anthropic = new Anthropic({
      authToken: env.ANTHROPIC_ENVIRONMENT_KEY,
      baseURL: env.ANTHROPIC_BASE_URL,
      webhookKey: env.ANTHROPIC_WEBHOOK_SECRET,
    });

    const body = await req.text();
    let event: ReturnType<typeof anthropic.beta.webhooks.unwrap>;
    try {
      event = anthropic.beta.webhooks.unwrap(body, {
        headers: Object.fromEntries(req.headers),
      });
    } catch (e) {
      console.warn(
        `[webhook] signature reject: ${e instanceof Error ? e.constructor.name : "Error"}`,
      );
      return new Response("signature verification failed", { status: 401 });
    }

    if (event.data.type !== "session.status_run_started") {
      return Response.json({ status: "ignored", event_type: event.data.type });
    }

    const spawned = await drainWork(env, anthropic);
    return Response.json({ status: "ok", spawned });
  },
};
