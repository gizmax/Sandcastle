/**
 * Shared types between the Worker fetch handler and the per-session
 * `SandboxContainer` Durable Object that owns the Container instance.
 */

/**
 * Single work item claimed from `/v1/environments/{id}/work/poll`. The Worker
 * passes this verbatim to the Container DO; the Container then heartbeats and
 * eventually posts a result.
 */
export interface WorkRequest {
  readonly sessionId: string;
  readonly workId: string;
  readonly environmentId: string;
  readonly environmentKey: string;
  readonly baseURL: string;
}

/**
 * Terminal result published by the Container DO once `ant beta:worker run`
 * exits. Mirrors the shape posted to `/v1/environments/{id}/work/{wid}/stop`
 * minus the credentials.
 */
export interface WorkResult {
  readonly sessionId: string;
  readonly workId: string;
  readonly status: "completed" | "failed" | "cancelled";
  readonly exitCode: number;
  readonly durationMs: number;
  readonly stopReason?: string;
}

/**
 * Minimal shape of the unwrapped webhook event. We only consume the
 * `session.status_run_started` variant; everything else is acknowledged with
 * `{status: "ignored"}` and dropped.
 */
export interface WebhookEvent {
  readonly id: string;
  readonly type: string;
  readonly data: {
    readonly type: string;
    readonly id: string;
  };
}
