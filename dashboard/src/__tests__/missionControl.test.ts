import { describe, it, expect } from "vitest";
import {
  aggregateTelemetry,
  applyMissionEvent,
  buildThoughtFeed,
  computeThroughput,
  estimateTokens,
  initialMissionState,
  isTerminalRunStatus,
  mergeLiveSteps,
  outputPreview,
  reduceMissionEvents,
  synthesizeEventsFromSteps,
  type MissionEvent,
  type MissionStep,
} from "@/lib/missionControl";

/* ── Fixtures ── */

const ts = (offsetSec: number) => new Date(Date.UTC(2026, 5, 9, 12, 0, offsetSec));

const baseSteps: MissionStep[] = [
  {
    step_id: "research",
    parallel_index: null,
    status: "pending",
    cost_usd: 0,
    duration_seconds: 0,
    model: "mistral/large",
    output: null,
  },
  {
    step_id: "analyze",
    parallel_index: null,
    status: "pending",
    cost_usd: 0,
    duration_seconds: 0,
    model: "claude/sonnet",
    output: null,
  },
  {
    step_id: "report",
    parallel_index: null,
    status: "pending",
    cost_usd: 0,
    duration_seconds: 0,
    output: null,
  },
];

/** Canonical happy-path event log mirroring GET /runs/{id}/stream. */
const happyPathEvents: MissionEvent[] = [
  { event: "status", data: { run_id: "r1", status: "queued", total_cost_usd: 0 }, timestamp: ts(0) },
  { event: "status", data: { run_id: "r1", status: "running", total_cost_usd: 0 }, timestamp: ts(1) },
  { event: "step", data: { step_id: "research", parallel_index: null, status: "running", cost_usd: 0, duration_seconds: 0 }, timestamp: ts(2) },
  { event: "step", data: { step_id: "research", parallel_index: null, status: "completed", cost_usd: 0.0123, duration_seconds: 4.2 }, timestamp: ts(6) },
  { event: "status", data: { run_id: "r1", status: "running", total_cost_usd: 0.0123 }, timestamp: ts(6) },
  { event: "step", data: { step_id: "analyze", parallel_index: null, status: "running", cost_usd: 0, duration_seconds: 0 }, timestamp: ts(7) },
];

const terminalEvents: MissionEvent[] = [
  ...happyPathEvents,
  { event: "step", data: { step_id: "analyze", parallel_index: null, status: "completed", cost_usd: 0.02, duration_seconds: 3.0 }, timestamp: ts(10) },
  { event: "step", data: { step_id: "report", parallel_index: null, status: "running", cost_usd: 0, duration_seconds: 0 }, timestamp: ts(11) },
  { event: "step", data: { step_id: "report", parallel_index: null, status: "failed", cost_usd: 0, duration_seconds: 1.1 }, timestamp: ts(12) },
  { event: "result", data: { run_id: "r1", status: "failed", outputs: null, total_cost_usd: 0.0323, error: "report: sandbox timeout" }, timestamp: ts(12) },
];

/* ── State machine ── */

describe("mission control state machine", () => {
  it("starts from the run snapshot", () => {
    const state = initialMissionState({ status: "queued", total_cost_usd: 0 });
    expect(state.runStatus).toBe("queued");
    expect(state.finished).toBe(false);
    expect(state.totalCostUsd).toBe(0);
  });

  it("marks terminal statuses as finished from the snapshot", () => {
    expect(initialMissionState({ status: "completed" }).finished).toBe(true);
    expect(initialMissionState({ status: "failed" }).finished).toBe(true);
    expect(initialMissionState({ status: "running" }).finished).toBe(false);
  });

  it("tracks run status, step overrides and cost through the happy path", () => {
    const state = reduceMissionEvents(initialMissionState({ status: "queued" }), happyPathEvents);
    expect(state.runStatus).toBe("running");
    expect(state.finished).toBe(false);
    expect(state.steps.research.status).toBe("completed");
    expect(state.steps.research.costUsd).toBeCloseTo(0.0123);
    expect(state.steps.analyze.status).toBe("running");
    expect(state.totalCostUsd).toBeCloseTo(0.0123);
  });

  it("finishes on the result event and surfaces the error", () => {
    const state = reduceMissionEvents(initialMissionState({ status: "queued" }), terminalEvents);
    expect(state.finished).toBe(true);
    expect(state.runStatus).toBe("failed");
    expect(state.steps.report.status).toBe("failed");
    expect(state.totalCostUsd).toBeCloseTo(0.0323);
    expect(state.error).toBe("report: sandbox timeout");
  });

  it("cost never goes backwards even if events arrive stale", () => {
    let state = reduceMissionEvents(initialMissionState({ status: "running" }), happyPathEvents);
    state = applyMissionEvent(state, {
      event: "status",
      data: { status: "running", total_cost_usd: 0.001 },
      timestamp: ts(8),
    });
    expect(state.totalCostUsd).toBeCloseTo(0.0123);
  });

  it("ignores unknown and malformed events", () => {
    const initial = initialMissionState({ status: "running" });
    const state = reduceMissionEvents(initial, [
      { event: "keepalive", data: {}, timestamp: ts(0) },
      { event: "step", data: { status: "running" }, timestamp: ts(1) }, // no step_id
    ]);
    expect(state).toEqual(initial);
  });

  it("merges live overrides onto REST steps", () => {
    const state = reduceMissionEvents(initialMissionState({ status: "running" }), happyPathEvents);
    const merged = mergeLiveSteps(baseSteps, state);
    expect(merged.find((s) => s.step_id === "research")?.status).toBe("completed");
    expect(merged.find((s) => s.step_id === "research")?.cost_usd).toBeCloseTo(0.0123);
    expect(merged.find((s) => s.step_id === "analyze")?.status).toBe("running");
    expect(merged.find((s) => s.step_id === "report")?.status).toBe("pending");
  });

  it("knows which run statuses are terminal", () => {
    for (const s of ["completed", "failed", "partial", "cancelled", "budget_exceeded", "awaiting_approval"]) {
      expect(isTerminalRunStatus(s)).toBe(true);
    }
    expect(isTerminalRunStatus("running")).toBe(false);
    expect(isTerminalRunStatus("queued")).toBe(false);
  });
});

/* ── Telemetry aggregation ── */

describe("telemetry aggregation", () => {
  it("estimates tokens from string and structured outputs", () => {
    expect(estimateTokens(null)).toBe(0);
    expect(estimateTokens("a".repeat(400))).toBe(100);
    expect(estimateTokens({ text: "abcd" })).toBe(Math.round('{"text":"abcd"}'.length / 4));
  });

  it("aggregates steps, models, tokens and the active step", () => {
    const state = reduceMissionEvents(initialMissionState({ status: "running" }), happyPathEvents);
    const steps = mergeLiveSteps(
      baseSteps.map((s) =>
        s.step_id === "research" ? { ...s, output: "x".repeat(800) } : s
      ),
      state
    );
    const t = aggregateTelemetry(steps, state);
    expect(t.stepsTotal).toBe(3);
    expect(t.stepsDone).toBe(1);
    expect(t.stepsRunning).toBe(1);
    expect(t.stepsFailed).toBe(0);
    expect(t.tokensEst).toBe(200);
    expect(t.costUsd).toBeCloseTo(0.0123);
    expect(t.models).toEqual(["mistral/large", "claude/sonnet"]);
    expect(t.activeStepId).toBe("analyze");
  });

  it("falls back to the last finished step when nothing is running", () => {
    const state = reduceMissionEvents(initialMissionState({ status: "queued" }), terminalEvents);
    const steps = mergeLiveSteps(baseSteps, state);
    const t = aggregateTelemetry(steps, state);
    expect(t.stepsDone).toBe(2);
    expect(t.stepsFailed).toBe(1);
    expect(t.stepsRunning).toBe(0);
    expect(t.activeStepId).toBe("report");
  });

  it("computes token throughput over a sliding window", () => {
    const t0 = 1_000_000;
    const samples = [
      { t: t0, tokens: 0 },
      { t: t0 + 5_000, tokens: 250 },
      { t: t0 + 10_000, tokens: 500 },
    ];
    expect(computeThroughput(samples)).toBeCloseTo(50);
    // Not enough signal
    expect(computeThroughput([])).toBe(0);
    expect(computeThroughput([{ t: t0, tokens: 100 }])).toBe(0);
    // Token count flat => zero rate
    expect(
      computeThroughput([
        { t: t0, tokens: 100 },
        { t: t0 + 1000, tokens: 100 },
      ])
    ).toBe(0);
  });
});

/* ── Thought stream feed ── */

describe("thought stream feed", () => {
  it("builds readable entries from the event log", () => {
    const steps = baseSteps.map((s) =>
      s.step_id === "research" ? { ...s, output: "Found 3 sources about sandcastles." } : s
    );
    const feed = buildThoughtFeed(terminalEvents, steps);

    const titles = feed.map((e) => e.title);
    expect(titles).toContain("Run started");
    expect(titles).toContain("research started");
    expect(titles.some((t) => t.startsWith("research completed"))).toBe(true);
    expect(titles).toContain("report failed");
    expect(titles).toContain("Run failed");

    // Output preview is attached after the completion entry
    const outputEntry = feed.find((e) => e.kind === "output");
    expect(outputEntry?.stepId).toBe("research");
    expect(outputEntry?.detail).toContain("Found 3 sources");

    // Terminal failure carries the error detail
    const errorEntry = feed.find((e) => e.title === "Run failed");
    expect(errorEntry?.detail).toBe("report: sandbox timeout");
  });

  it("includes duration and cost metadata on completed steps", () => {
    const feed = buildThoughtFeed(terminalEvents, baseSteps);
    const research = feed.find((e) => e.kind === "step-done" && e.stepId === "research");
    expect(research?.title).toContain("4.2s");
    expect(research?.title).toContain("$0.0123");
  });

  it("truncates long output previews", () => {
    expect(outputPreview("x".repeat(1000))?.length).toBeLessThanOrEqual(401);
    expect(outputPreview(null)).toBeUndefined();
    expect(outputPreview("   ")).toBeUndefined();
  });

  it("synthesizes an honest archive feed for finished runs", () => {
    const finishedSteps: MissionStep[] = [
      { ...baseSteps[0], status: "completed", cost_usd: 0.01, duration_seconds: 2, started_at: "2026-06-09T12:00:00", output: "result A" },
      { ...baseSteps[1], status: "completed", cost_usd: 0.02, duration_seconds: 3, started_at: "2026-06-09T12:00:05" },
    ];
    const events = synthesizeEventsFromSteps(finishedSteps, {
      status: "completed",
      total_cost_usd: 0.03,
      completed_at: "2026-06-09T12:00:10",
    });
    // Steps in start order, then the terminal result
    expect(events.map((e) => e.event)).toEqual(["step", "step", "result"]);
    expect(events[0].data.step_id).toBe("research");

    const state = reduceMissionEvents(initialMissionState({ status: "completed" }), events);
    expect(state.finished).toBe(true);
    expect(state.totalCostUsd).toBeCloseTo(0.03);

    const feed = buildThoughtFeed(events, finishedSteps);
    expect(feed.find((e) => e.kind === "output")?.detail).toBe("result A");
    expect(feed[feed.length - 1].title).toBe("Run completed");
  });
});
