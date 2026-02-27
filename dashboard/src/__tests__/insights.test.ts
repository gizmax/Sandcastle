import { describe, it, expect, beforeEach } from "vitest";
import {
  computeScore,
  generateInsights,
  dismissInsight,
  filterDismissed,
  getDismissedIds,
  type AdvisorData,
} from "@/lib/insights";

function makeData(overrides: Partial<AdvisorData> = {}): AdvisorData {
  return {
    health: { status: "ok", runtime: true, redis: true, database: true },
    stats: { total_runs_today: 10, success_rate: 0.95, total_cost_today: 2.5 },
    runs: [{ status: "completed" }, { status: "completed" }],
    tools: [],
    dlq: [],
    violationStats: { total_violations_30d: 0, violations_by_severity: {} },
    optimizerStats: { total_decisions_30d: 5, estimated_savings_30d_usd: 0 },
    autopilotStats: { total_experiments: 1, active_experiments: 0 },
    approvals: [],
    workflows: [{ name: "test-workflow" }],
    schedules: [{ id: "s1", enabled: true }],
    evalStats: { total_runs: 5 },
    apiKeys: [{ id: "k1" }],
    ...overrides,
  };
}

// ── computeScore ────────────────────────────────────────────────────────────

describe("computeScore", () => {
  it("returns 100 for healthy system", () => {
    const result = computeScore(makeData());
    expect(result.score).toBe(100);
    expect(result.deductions).toHaveLength(0);
  });

  it("deducts 15 for runtime unhealthy", () => {
    const data = makeData({
      health: { status: "unhealthy", runtime: false, redis: true, database: true },
    });
    const result = computeScore(data);
    expect(result.score).toBe(85);
    expect(result.deductions).toContainEqual({
      category: "health",
      label: "Runtime unhealthy",
      points: 15,
    });
  });

  it("deducts for multiple unhealthy services", () => {
    const data = makeData({
      health: { status: "unhealthy", runtime: false, redis: false, database: false },
    });
    const result = computeScore(data);
    expect(result.score).toBe(55); // 100 - 15 - 15 - 15
  });

  it("deducts for DLQ items (max 30)", () => {
    const data = makeData({
      dlq: [
        { id: "1", resolved_at: null },
        { id: "2", resolved_at: null },
        { id: "3", resolved_at: null },
        { id: "4", resolved_at: null },
      ],
    });
    const result = computeScore(data);
    // 4 items * 10 = 40, capped at 30
    expect(result.deductions.find((d) => d.label === "Dead-letter queue items")?.points).toBe(30);
  });

  it("ignores resolved DLQ items", () => {
    const data = makeData({
      dlq: [{ id: "1", resolved_at: "2025-01-01T00:00:00Z" }],
    });
    const result = computeScore(data);
    expect(result.score).toBe(100);
  });

  it("deducts for high failure rate", () => {
    const data = makeData({
      stats: { total_runs_today: 10, success_rate: 0.5, total_cost_today: 1 },
    });
    const result = computeScore(data);
    expect(result.deductions.find((d) => d.label === "High failure rate")).toBeDefined();
  });

  it("deducts for critical policy violations (max 30)", () => {
    const data = makeData({
      violationStats: {
        total_violations_30d: 5,
        violations_by_severity: { critical: 5 },
      },
    });
    const result = computeScore(data);
    expect(result.deductions.find((d) => d.label === "Critical policy violations")?.points).toBe(30);
  });

  it("deducts for unconfigured tools with connections", () => {
    const data = makeData({
      tools: [
        { name: "slack", configured: false, connections: [{}] },
        { name: "github", configured: true, connections: [{}] },
      ],
    });
    const result = computeScore(data);
    expect(result.deductions.find((d) => d.label === "Unconfigured integrations")?.points).toBe(5);
  });

  it("deducts for approval backlog > 5", () => {
    const approvals = Array.from({ length: 7 }, (_, i) => ({
      id: `a${i}`,
      status: "pending",
    }));
    const data = makeData({ approvals });
    const result = computeScore(data);
    expect(result.deductions.find((d) => d.label === "Approval backlog")).toBeDefined();
  });

  it("does not deduct for 5 or fewer pending approvals", () => {
    const approvals = Array.from({ length: 5 }, (_, i) => ({
      id: `a${i}`,
      status: "pending",
    }));
    const data = makeData({ approvals });
    const result = computeScore(data);
    expect(result.deductions.find((d) => d.label === "Approval backlog")).toBeUndefined();
  });

  it("deducts for missing adoption features", () => {
    const data = makeData({
      schedules: [],
      evalStats: null,
      autopilotStats: null,
      optimizerStats: null,
    });
    const result = computeScore(data);
    expect(result.deductions.filter((d) => d.category === "adoption")).toHaveLength(4);
  });

  it("never goes below 0", () => {
    const data = makeData({
      health: { status: "down", runtime: false, redis: false, database: false },
      dlq: Array.from({ length: 10 }, (_, i) => ({ id: `d${i}`, resolved_at: null })),
      violationStats: {
        total_violations_30d: 10,
        violations_by_severity: { critical: 10 },
      },
      stats: { total_runs_today: 10, success_rate: 0.1, total_cost_today: 50 },
      schedules: [],
      evalStats: null,
      autopilotStats: null,
      optimizerStats: null,
    });
    const result = computeScore(data);
    expect(result.score).toBe(0);
  });
});

// ── generateInsights ────────────────────────────────────────────────────────

describe("generateInsights", () => {
  it("generates critical insight for runtime down", () => {
    const data = makeData({
      health: { status: "down", runtime: false, redis: true, database: true },
    });
    const insights = generateInsights(data);
    expect(insights.find((i) => i.id === "health-runtime")).toBeDefined();
    expect(insights.find((i) => i.id === "health-runtime")?.severity).toBe("critical");
  });

  it("generates critical insight for DLQ items", () => {
    const data = makeData({
      dlq: [{ id: "1", resolved_at: null }],
    });
    const insights = generateInsights(data);
    expect(insights.find((i) => i.id === "dlq-items")?.title).toBe("1 dead-letter item");
  });

  it("pluralizes DLQ title for multiple items", () => {
    const data = makeData({
      dlq: [
        { id: "1", resolved_at: null },
        { id: "2", resolved_at: null },
      ],
    });
    const insights = generateInsights(data);
    expect(insights.find((i) => i.id === "dlq-items")?.title).toBe("2 dead-letter items");
  });

  it("generates warning for success rate between 50-80%", () => {
    const data = makeData({
      stats: { total_runs_today: 10, success_rate: 0.65, total_cost_today: 1 },
    });
    const insights = generateInsights(data);
    expect(insights.find((i) => i.id === "success-rate-warning")).toBeDefined();
  });

  it("generates critical for success rate below 50%", () => {
    const data = makeData({
      stats: { total_runs_today: 10, success_rate: 0.3, total_cost_today: 1 },
    });
    const insights = generateInsights(data);
    expect(insights.find((i) => i.id === "success-rate-critical")).toBeDefined();
    expect(insights.find((i) => i.id === "success-rate-warning")).toBeUndefined();
  });

  it("generates discover insights for missing features", () => {
    const data = makeData({
      schedules: [],
      evalStats: null,
      autopilotStats: null,
      optimizerStats: null,
    });
    const insights = generateInsights(data);
    expect(insights.find((i) => i.id === "discover-schedules")).toBeDefined();
    expect(insights.find((i) => i.id === "discover-evals")).toBeDefined();
    expect(insights.find((i) => i.id === "discover-autopilot")).toBeDefined();
    expect(insights.find((i) => i.id === "discover-optimizer")).toBeDefined();
  });

  it("generates optimize insight for potential savings", () => {
    const data = makeData({
      optimizerStats: { total_decisions_30d: 5, estimated_savings_30d_usd: 5.5 },
    });
    const insights = generateInsights(data);
    expect(insights.find((i) => i.id === "optimizer-savings")?.title).toBe("$5.50 potential savings");
  });

  it("generates warning for expiring API keys", () => {
    const tomorrow = new Date(Date.now() + 2 * 24 * 60 * 60 * 1000).toISOString();
    const data = makeData({
      apiKeys: [{ id: "k1", expires_at: tomorrow }],
    });
    const insights = generateInsights(data);
    expect(insights.find((i) => i.id === "expiring-keys")).toBeDefined();
  });

  it("does not flag API keys expiring more than 7 days out", () => {
    const farFuture = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString();
    const data = makeData({
      apiKeys: [{ id: "k1", expires_at: farFuture }],
    });
    const insights = generateInsights(data);
    expect(insights.find((i) => i.id === "expiring-keys")).toBeUndefined();
  });

  it("generates high daily spend insight", () => {
    const data = makeData({
      stats: { total_runs_today: 100, success_rate: 0.95, total_cost_today: 25 },
    });
    const insights = generateInsights(data);
    expect(insights.find((i) => i.id === "high-daily-spend")).toBeDefined();
  });

  it("generates disabled schedules insight", () => {
    const data = makeData({
      schedules: [
        { id: "s1", enabled: true },
        { id: "s2", enabled: false },
      ],
    });
    const insights = generateInsights(data);
    expect(insights.find((i) => i.id === "disabled-schedules")?.title).toBe("1 disabled schedule");
  });
});

// ── Dismiss helpers ─────────────────────────────────────────────────────────

describe("dismiss helpers", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("starts with empty dismissed set", () => {
    expect(getDismissedIds().size).toBe(0);
  });

  it("persists dismissed IDs", () => {
    dismissInsight("health-runtime");
    const ids = getDismissedIds();
    expect(ids.has("health-runtime")).toBe(true);
  });

  it("filters dismissed insights", () => {
    dismissInsight("dlq-items");
    const insights = [
      { id: "dlq-items", severity: "critical" as const, title: "", description: "", link: "", icon: "" },
      { id: "health-runtime", severity: "critical" as const, title: "", description: "", link: "", icon: "" },
    ];
    const filtered = filterDismissed(insights);
    expect(filtered).toHaveLength(1);
    expect(filtered[0].id).toBe("health-runtime");
  });

  it("handles corrupted localStorage", () => {
    localStorage.setItem("sandcastle_lighthouse_dismissed", "not-json");
    expect(getDismissedIds().size).toBe(0);
  });
});
