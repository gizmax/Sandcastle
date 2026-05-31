/**
 * Durable Object that owns a single Cloudflare Container instance per
 * managed-agent session. Receives the claimed work item from the Worker,
 * starts the Container with the environment key injected as a process env
 * variable, and keeps the work-item lease alive via heartbeats while the
 * Container's `ant beta:worker run` entrypoint drives the tool loop.
 */
import type { WorkRequest, WorkResult } from "./types";

interface ContainerInstance {
  start(opts: { env: Record<string, string>; entrypoint?: string[] }): Promise<void>;
  running(): Promise<boolean>;
  stop(): Promise<void>;
  monitor(): Promise<{ exitCode: number }>;
}

interface ContainerState extends DurableObjectState {
  readonly container: ContainerInstance;
}

export class SandboxContainer implements DurableObject {
  private readonly state: ContainerState;
  private readonly startedAt: number = Date.now();

  constructor(state: DurableObjectState) {
    this.state = state as ContainerState;
  }

  async fetch(_req: Request): Promise<Response> {
    return new Response("ok", { status: 200 });
  }

  /** Whether the Container is currently running a session. */
  async isLive(): Promise<boolean> {
    try {
      return await this.state.container.running();
    } catch {
      return false;
    }
  }

  /**
   * Start the Container with the per-session env. Does not block on the
   * Container exiting; the Container is expected to heartbeat the work item
   * itself via the embedded ant CLI.
   */
  async dispatch(req: WorkRequest): Promise<void> {
    if (await this.isLive()) return;
    await this.state.container.start({
      env: {
        ANTHROPIC_ENVIRONMENT_ID: req.environmentId,
        ANTHROPIC_ENVIRONMENT_KEY: req.environmentKey,
        ANTHROPIC_BASE_URL: req.baseURL,
        ANT_SESSION_ID: req.sessionId,
        ANT_WORK_ID: req.workId,
      },
    });
  }

  /** Awaits container exit and returns a structured WorkResult. */
  async waitForExit(req: WorkRequest): Promise<WorkResult> {
    const { exitCode } = await this.state.container.monitor();
    const status: WorkResult["status"] =
      exitCode === 0 ? "completed" : exitCode === 137 ? "cancelled" : "failed";
    return {
      sessionId: req.sessionId,
      workId: req.workId,
      status,
      exitCode,
      durationMs: Date.now() - this.startedAt,
    };
  }
}
