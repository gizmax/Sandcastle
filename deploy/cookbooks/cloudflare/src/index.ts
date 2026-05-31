/**
 * Cloudflare Worker: webhook -> drain environment work queue -> spin up a
 * per-session Container running `ant beta:worker run`.
 *
 * The webhook is a wake-up signal only. Each delivery drains all pending
 * work items, so a single arriving webhook recovers any earlier missed ones.
 *
 * Anthropic SDK auto-sends the `anthropic-beta: managed-agents-2026-04-01`
 * header for the `beta.environments.*` namespace; the alternative
 * `mcp-client-2025-11-20` beta is documented for MCP tool registration but
 * is not used by this Worker.
 *
 * Webhook event consumed: `session.status_run_started`.
 */
import Anthropic from "@anthropic-ai/sdk";
import type { WorkRequest } from "./types";

export { SandboxContainer } from "./container";

export interface SandboxContainer {
  isLive(): Promise<boolean>;
  dispatch(req: WorkRequest): Promise<void>;
}

export interface Env {
  SANDBOX_CONTAINER: DurableObjectNamespace<SandboxContainer>;
  ANTHROPIC_BASE_URL: string;
  ANTHROPIC_ENVIRONMENT_ID: string;
  ANTHROPIC_WEBHOOK_SECRET: string;
  ANTHROPIC_ENVIRONMENT_KEY: string;
}

const MAX_DRAIN = 25;

/** Scrub credential shapes before mirroring an error message to the function log. */
function redact(s: string): string {
  return s
    .replace(/sk-ant-[A-Za-z0-9._-]+/g, "sk-ant-[REDACTED]")
    .replace(/whsec_[A-Za-z0-9+/=_-]+/g, "whsec_[REDACTED]")
    .replace(/Bearer\s+\S{8,}/gi, "Bearer [REDACTED]");
}

/**
 * Structured, redacted error detail for log lines. Surfaces the SDK's
 * `status` + `requestID` when present so an API failure is correlatable to a
 * server-side trace.
 */
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

/**
 * Drain the work queue. The TS WorkPoller long-polls and never returns on an
 * empty queue, but a Worker fetch handler must respond, so poll -> ack until
 * empty here. The Container owns the lease (heartbeat + force-stop), so the
 * webhook never posts `stop`.
 */
async function drainWork(env: Env, anthropic: Anthropic): Promise<SpawnRecord[]> {
  const spawned: SpawnRecord[] = [];
  for (let i = 0; i < MAX_DRAIN; i++) {
    let work: Awaited<ReturnType<typeof anthropic.beta.environments.work.poll>>;
    try {
      work = await anthropic.beta.environments.work.poll(env.ANTHROPIC_ENVIRONMENT_ID, {
        reclaim_older_than_ms: 2000,
      });
    } catch (e) {
      const { status, requestID } = (e ?? {}) as { status?: number; requestID?: string | null };
      console.warn(
        `[webhook] poll failed status=${status ?? "?"} request_id=${requestID ?? "?"}`,
      );
      if (status === 404) continue;
      break;
    }
    if (!work) break;
    console.log(
      `[webhook] polled work=${JSON.stringify({
        id: work.id,
        environment_id: work.environment_id,
        data: { type: work.data.type, id: work.data.id },
      })}`,
    );
    if (work.data.type !== "session") continue;

    const sessionId: string = work.data.id;
    try {
      await anthropic.beta.environments.work.ack(work.id, {
        environment_id: env.ANTHROPIC_ENVIRONMENT_ID,
      });

      const stub = env.SANDBOX_CONTAINER.get(env.SANDBOX_CONTAINER.idFromName(sessionId));
      const wasLive = await stub.isLive();
      if (!wasLive) {
        await stub.dispatch({
          sessionId,
          environmentKey: env.ANTHROPIC_ENVIRONMENT_KEY,
          workId: work.id,
          environmentId: env.ANTHROPIC_ENVIRONMENT_ID,
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
