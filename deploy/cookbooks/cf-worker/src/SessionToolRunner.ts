/**
 * Durable Object that drives one managed-agent session entirely inside the
 * V8 isolate. No Container, no subprocess. The tool loop dispatches against
 * the in-isolate FakeFS and a no-op `bash` stub.
 *
 * Implements the tool surface advertised by `beta_agent_toolset_20260401`:
 *
 *   - bash         no-op stub (returns "[isolate: bash disabled]")
 *   - read         FakeFS.read
 *   - write        FakeFS.write
 *   - edit         FakeFS.edit (single-shot str replace)
 *   - glob         FakeFS.glob
 *   - grep         FakeFS.grep
 *
 * The DO also owns the work-item lease: it claims, heartbeats, and finally
 * posts a stop with the structured result. Anthropic SDK auto-sends the
 * `anthropic-beta: managed-agents-2026-04-01` header for these calls.
 */
import Anthropic from "@anthropic-ai/sdk";
import { FakeFS } from "./fakefs";

interface StartOpts {
  readonly sessionId: string;
  readonly workId: string;
  readonly environmentId: string;
  readonly environmentKey: string;
  readonly baseURL: string;
}

interface ToolCall {
  readonly name: string;
  readonly input: Record<string, unknown>;
}

const HEARTBEAT_MS = 30_000;
const MAX_IDLE_MS = 60_000;
const MAX_TURNS = 200;

export class SessionToolRunner implements DurableObject {
  private readonly state: DurableObjectState;
  private fs: FakeFS = new FakeFS();
  private live: boolean = false;
  private abort: AbortController = new AbortController();

  constructor(state: DurableObjectState) {
    this.state = state;
  }

  async fetch(_req: Request): Promise<Response> {
    return new Response("ok");
  }

  async isLive(): Promise<boolean> {
    return this.live;
  }

  /** Start running the session. Returns immediately; the loop runs in waitUntil. */
  async start(opts: StartOpts): Promise<void> {
    if (this.live) return;
    this.live = true;
    this.fs = new FakeFS();
    this.abort = new AbortController();
    this.state.waitUntil(this.runWorkItem(opts));
  }

  async stop(): Promise<void> {
    this.abort.abort();
    this.live = false;
  }

  private async runWorkItem(opts: StartOpts): Promise<void> {
    const anthropic = new Anthropic({
      authToken: opts.environmentKey,
      baseURL: opts.baseURL,
    });
    const heartbeat = this.startHeartbeat(anthropic, opts);
    try {
      await this.toolLoop(anthropic, opts);
      await anthropic.beta.environments.work.stop(opts.workId, {
        environment_id: opts.environmentId,
        stop_reason: "end_turn",
      });
    } catch (e) {
      if (!this.abort.signal.aborted) {
        console.warn(`[runner] session=${opts.sessionId} failed: ${errStr(e)}`);
      }
    } finally {
      clearInterval(heartbeat);
      this.live = false;
    }
  }

  private startHeartbeat(anthropic: Anthropic, opts: StartOpts): ReturnType<typeof setInterval> {
    return setInterval(() => {
      void anthropic.beta.environments.work.heartbeat(opts.workId, {
        environment_id: opts.environmentId,
      });
    }, HEARTBEAT_MS);
  }

  /**
   * Drive the session tool dispatch. Each iteration polls the next pending
   * tool call from the session, runs it against the FakeFS, and posts the
   * result. Exits when the session reports `status_idle` with end_turn or
   * when no new tool call arrives within MAX_IDLE_MS.
   */
  private async toolLoop(anthropic: Anthropic, opts: StartOpts): Promise<void> {
    let lastActivityAt = Date.now();
    for (let turn = 0; turn < MAX_TURNS; turn++) {
      if (this.abort.signal.aborted) return;
      if (Date.now() - lastActivityAt > MAX_IDLE_MS) return;

      const next = await anthropic.beta.environments.sessions.tools.next(opts.sessionId, {
        environment_id: opts.environmentId,
      });
      if (!next) {
        await sleep(250);
        continue;
      }
      if (next.status === "idle" && next.stop_reason === "end_turn") return;

      const call = next.tool_call as ToolCall | undefined;
      if (!call) continue;

      lastActivityAt = Date.now();
      const output = this.dispatchTool(call);
      await anthropic.beta.environments.sessions.tools.result(opts.sessionId, {
        environment_id: opts.environmentId,
        tool_use_id: String((next as { tool_use_id?: string }).tool_use_id ?? ""),
        output,
      });
    }
  }

  /** Run a single tool call against the in-isolate state. */
  private dispatchTool(call: ToolCall): string {
    const args = call.input;
    switch (call.name) {
      case "bash":
        // No-op: subprocess is not available inside a Worker isolate.
        return "[isolate: bash disabled; use read/write/edit/glob/grep]";
      case "read":
        return this.fs.read(strArg(args, "path"));
      case "write":
        this.fs.write(strArg(args, "path"), strArg(args, "content"));
        return `wrote ${strArg(args, "path")}`;
      case "edit":
        this.fs.edit(
          strArg(args, "path"),
          strArg(args, "old_str"),
          strArg(args, "new_str"),
        );
        return `edited ${strArg(args, "path")}`;
      case "glob":
        return this.fs.glob(strArg(args, "pattern")).join("\n");
      case "grep":
        return this.fs
          .grep(strArg(args, "pattern"), optStrArg(args, "path") ?? "/workspace")
          .map((m) => `${m.path}:${m.line}: ${m.text}`)
          .join("\n");
      default:
        return `[unknown tool: ${call.name}]`;
    }
  }
}

function strArg(args: Record<string, unknown>, key: string): string {
  const v = args[key];
  if (typeof v !== "string") throw new Error(`tool arg ${key} not a string`);
  return v;
}

function optStrArg(args: Record<string, unknown>, key: string): string | undefined {
  const v = args[key];
  return typeof v === "string" ? v : undefined;
}

function sleep(ms: number): Promise<void> {
  return new Promise((res) => setTimeout(res, ms));
}

function errStr(e: unknown): string {
  if (e instanceof Error) return `${e.constructor.name}: ${e.message}`;
  return String(e);
}
