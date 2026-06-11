/**
 * Mission Control - pure state machine and telemetry aggregation for the
 * live run theater (/runs/:id/live).
 *
 * The backend streams SSE events from GET /runs/{run_id}/stream:
 *   - event: status  data: { run_id, status, total_cost_usd }
 *   - event: step    data: { step_id, parallel_index, status, cost_usd, duration_seconds }
 *   - event: result  data: { run_id, status, outputs, total_cost_usd, error }
 *   - event: error   data: { message }
 *
 * Everything in this file is pure so the theater can be developed and
 * tested against event fixtures without a live backend.
 */

import { parseUTC } from "@/lib/utils";

/* ── Types ── */

export interface MissionStep {
  step_id: string;
  parallel_index: number | null;
  status: string;
  cost_usd: number;
  duration_seconds: number;
  model?: string | null;
  output?: unknown;
  error?: string | null;
  started_at?: string | null;
  depends_on?: string[];
}

export interface MissionEvent {
  event: string;
  data: Record<string, unknown>;
  timestamp: Date;
}

export interface StepOverride {
  status: string;
  costUsd: number;
  durationSeconds: number;
}

export interface MissionState {
  runStatus: string;
  totalCostUsd: number;
  /** Live per-step overrides keyed by step_id (latest event wins). */
  steps: Record<string, StepOverride>;
  /** True once a terminal `result` event (or terminal status) was seen. */
  finished: boolean;
  error: string | null;
}

export interface Telemetry {
  costUsd: number;
  tokensEst: number;
  stepsTotal: number;
  stepsDone: number;
  stepsFailed: number;
  stepsRunning: number;
  models: string[];
  activeStepId: string | null;
}

export type FeedKind = "run" | "step-start" | "step-done" | "step-fail" | "output" | "error";

export interface FeedEntry {
  id: string;
  ts: Date;
  kind: FeedKind;
  stepId?: string;
  title: string;
  detail?: string;
}

/* ── Run status helpers ── */

export const TERMINAL_RUN_STATUSES = new Set([
  "completed",
  "failed",
  "partial",
  "cancelled",
  "budget_exceeded",
  "awaiting_approval",
]);

export function isTerminalRunStatus(status: string): boolean {
  return TERMINAL_RUN_STATUSES.has(status);
}

const DONE_STEP_STATUSES = new Set(["completed", "skipped"]);

/* ── State machine ── */

export function initialMissionState(run?: {
  status?: string;
  total_cost_usd?: number;
}): MissionState {
  const status = run?.status ?? "queued";
  return {
    runStatus: status,
    totalCostUsd: run?.total_cost_usd ?? 0,
    steps: {},
    finished: isTerminalRunStatus(status),
    error: null,
  };
}

/** Apply a single SSE event to the mission state. Returns a new state. */
export function applyMissionEvent(state: MissionState, event: MissionEvent): MissionState {
  switch (event.event) {
    case "status": {
      const status = typeof event.data.status === "string" ? event.data.status : state.runStatus;
      const cost =
        typeof event.data.total_cost_usd === "number"
          ? event.data.total_cost_usd
          : state.totalCostUsd;
      return {
        ...state,
        runStatus: status,
        totalCostUsd: Math.max(state.totalCostUsd, cost),
        finished: state.finished || isTerminalRunStatus(status),
      };
    }
    case "step": {
      const stepId = typeof event.data.step_id === "string" ? event.data.step_id : null;
      if (!stepId) return state;
      const status = typeof event.data.status === "string" ? event.data.status : "running";
      const costUsd = typeof event.data.cost_usd === "number" ? event.data.cost_usd : 0;
      const durationSeconds =
        typeof event.data.duration_seconds === "number" ? event.data.duration_seconds : 0;
      return {
        ...state,
        steps: {
          ...state.steps,
          [stepId]: { status, costUsd, durationSeconds },
        },
      };
    }
    case "result": {
      const status = typeof event.data.status === "string" ? event.data.status : state.runStatus;
      const cost =
        typeof event.data.total_cost_usd === "number"
          ? event.data.total_cost_usd
          : state.totalCostUsd;
      const error = typeof event.data.error === "string" ? event.data.error : null;
      return {
        ...state,
        runStatus: status,
        totalCostUsd: Math.max(state.totalCostUsd, cost),
        finished: true,
        error,
      };
    }
    case "error": {
      const message = typeof event.data.message === "string" ? event.data.message : "Stream error";
      return { ...state, error: message };
    }
    default:
      return state;
  }
}

/** Fold a list of events over an initial state (fixture-driven testing). */
export function reduceMissionEvents(state: MissionState, events: MissionEvent[]): MissionState {
  return events.reduce(applyMissionEvent, state);
}

/**
 * Merge live SSE overrides into the steps fetched from the REST API.
 * SSE events win on status; REST data wins on output/model/error detail.
 */
export function mergeLiveSteps(baseSteps: MissionStep[], state: MissionState): MissionStep[] {
  return baseSteps.map((step) => {
    const override = state.steps[step.step_id];
    if (!override) return step;
    return {
      ...step,
      status: override.status,
      cost_usd: Math.max(step.cost_usd ?? 0, override.costUsd),
      duration_seconds: Math.max(step.duration_seconds ?? 0, override.durationSeconds),
    };
  });
}

/* ── Telemetry aggregation ── */

/** Rough token estimate from an arbitrary step output (~4 chars per token). */
export function estimateTokens(output: unknown): number {
  if (output == null) return 0;
  let chars = 0;
  if (typeof output === "string") {
    chars = output.length;
  } else {
    try {
      chars = JSON.stringify(output)?.length ?? 0;
    } catch {
      chars = 0;
    }
  }
  return Math.round(chars / 4);
}

export function aggregateTelemetry(steps: MissionStep[], state: MissionState): Telemetry {
  let tokensEst = 0;
  let stepsDone = 0;
  let stepsFailed = 0;
  let stepsRunning = 0;
  const models = new Set<string>();
  let activeStepId: string | null = null;
  let lastDoneStepId: string | null = null;

  for (const step of steps) {
    if (step.model) models.add(step.model);
    tokensEst += estimateTokens(step.output);
    if (DONE_STEP_STATUSES.has(step.status)) {
      stepsDone += 1;
      lastDoneStepId = step.step_id;
    } else if (step.status === "failed") {
      stepsFailed += 1;
      lastDoneStepId = step.step_id;
    } else if (step.status === "running") {
      stepsRunning += 1;
      if (!activeStepId) activeStepId = step.step_id;
    }
  }

  return {
    costUsd: state.totalCostUsd,
    tokensEst,
    stepsTotal: steps.length,
    stepsDone,
    stepsFailed,
    stepsRunning,
    models: [...models],
    activeStepId: activeStepId ?? lastDoneStepId,
  };
}

/**
 * Token throughput over a sliding window from (time, cumulative tokens)
 * samples. Returns tokens/second, 0 when there is not enough signal.
 */
export function computeThroughput(
  samples: { t: number; tokens: number }[],
  windowMs = 30_000
): number {
  if (samples.length < 2) return 0;
  const latest = samples[samples.length - 1];
  const cutoff = latest.t - windowMs;
  // Oldest sample still inside the window (samples are time-ordered)
  let oldest = samples[0];
  for (const s of samples) {
    if (s.t >= cutoff) {
      oldest = s;
      break;
    }
  }
  const dt = (latest.t - oldest.t) / 1000;
  if (dt <= 0) return 0;
  const delta = latest.tokens - oldest.tokens;
  return delta > 0 ? delta / dt : 0;
}

/** Control-room clock format: mm:ss, or hh:mm:ss past the hour. */
export function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "00:00";
  const s = Math.floor(seconds);
  const hh = Math.floor(s / 3600);
  const mm = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return hh > 0 ? `${pad(hh)}:${pad(mm)}:${pad(ss)}` : `${pad(mm)}:${pad(ss)}`;
}

/* ── Thought stream feed ── */

const RUN_STATUS_TITLES: Record<string, string> = {
  queued: "Run queued — waiting for a runner",
  running: "Run started",
  completed: "Run completed",
  failed: "Run failed",
  partial: "Run finished partially",
  cancelled: "Run cancelled",
  budget_exceeded: "Run stopped — budget exceeded",
  awaiting_approval: "Run paused — awaiting approval",
};

export function outputPreview(output: unknown, maxChars = 400): string | undefined {
  if (output == null) return undefined;
  let text: string;
  if (typeof output === "string") {
    text = output;
  } else {
    try {
      text = JSON.stringify(output, null, 2) ?? "";
    } catch {
      return undefined;
    }
  }
  text = text.trim();
  if (!text) return undefined;
  return text.length > maxChars ? `${text.slice(0, maxChars)}…` : text;
}

/**
 * Synthesize an event log for a finished run that has no live SSE events
 * (the operator opened Mission Control after the fact). Renders the final
 * state honestly - no fake replay, just the recorded step results in order.
 */
export function synthesizeEventsFromSteps(
  steps: MissionStep[],
  run: { status: string; total_cost_usd?: number; error?: string | null; completed_at?: string | null }
): MissionEvent[] {
  const ordered = [...steps].sort((a, b) => {
    const ta = a.started_at ? parseUTC(a.started_at).getTime() : 0;
    const tb = b.started_at ? parseUTC(b.started_at).getTime() : 0;
    return ta - tb;
  });

  const events: MissionEvent[] = [];
  for (const step of ordered) {
    const ts = step.started_at ? parseUTC(step.started_at) : new Date(0);
    events.push({
      event: "step",
      data: {
        step_id: step.step_id,
        parallel_index: step.parallel_index,
        status: step.status,
        cost_usd: step.cost_usd,
        duration_seconds: step.duration_seconds,
      },
      timestamp: ts,
    });
  }
  events.push({
    event: "result",
    data: {
      status: run.status,
      total_cost_usd: run.total_cost_usd ?? 0,
      error: run.error ?? null,
    },
    timestamp: run.completed_at ? parseUTC(run.completed_at) : new Date(0),
  });
  return events;
}

/**
 * Build the thought-stream feed from the SSE event log, enriching completed
 * step entries with output previews from the REST snapshot.
 */
export function buildThoughtFeed(events: MissionEvent[], steps: MissionStep[]): FeedEntry[] {
  const stepsById = new Map(steps.map((s) => [s.step_id, s]));
  const entries: FeedEntry[] = [];

  events.forEach((event, i) => {
    const id = `evt-${i}`;
    switch (event.event) {
      case "status": {
        const status = typeof event.data.status === "string" ? event.data.status : "unknown";
        entries.push({
          id,
          ts: event.timestamp,
          kind: "run",
          title: RUN_STATUS_TITLES[status] ?? `Run status: ${status}`,
        });
        break;
      }
      case "step": {
        const stepId = typeof event.data.step_id === "string" ? event.data.step_id : "step";
        const status = typeof event.data.status === "string" ? event.data.status : "running";
        const step = stepsById.get(stepId);
        if (status === "running" || status === "queued") {
          entries.push({
            id,
            ts: event.timestamp,
            kind: "step-start",
            stepId,
            title: `${stepId} ${status === "queued" ? "queued" : "started"}`,
            detail: step?.model ? `model: ${step.model}` : undefined,
          });
        } else if (status === "failed") {
          entries.push({
            id,
            ts: event.timestamp,
            kind: "step-fail",
            stepId,
            title: `${stepId} failed`,
            detail: step?.error ?? undefined,
          });
        } else {
          const duration =
            typeof event.data.duration_seconds === "number" ? event.data.duration_seconds : 0;
          const cost = typeof event.data.cost_usd === "number" ? event.data.cost_usd : 0;
          const meta: string[] = [];
          if (duration > 0) meta.push(`${duration.toFixed(1)}s`);
          if (cost > 0) meta.push(`$${cost.toFixed(4)}`);
          entries.push({
            id,
            ts: event.timestamp,
            kind: "step-done",
            stepId,
            title: `${stepId} ${status}${meta.length ? ` (${meta.join(" · ")})` : ""}`,
          });
          const preview = outputPreview(step?.output);
          if (preview) {
            entries.push({
              id: `${id}-out`,
              ts: event.timestamp,
              kind: "output",
              stepId,
              title: `${stepId} output`,
              detail: preview,
            });
          }
        }
        break;
      }
      case "result": {
        const status = typeof event.data.status === "string" ? event.data.status : "completed";
        const error = typeof event.data.error === "string" ? event.data.error : undefined;
        entries.push({
          id,
          ts: event.timestamp,
          kind: status === "failed" ? "error" : "run",
          title: RUN_STATUS_TITLES[status] ?? `Run finished: ${status}`,
          detail: error,
        });
        break;
      }
      case "error": {
        const message =
          typeof event.data.message === "string" ? event.data.message : "Stream error";
        entries.push({ id, ts: event.timestamp, kind: "error", title: message });
        break;
      }
    }
  });

  return entries;
}
