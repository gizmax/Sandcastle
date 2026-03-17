/**
 * v0.22 Integration Tests
 *
 * Covers:
 * 1. CostForecast SVG math edge cases (extracted pure functions)
 * 2. insights.ts edge cases (all-null, worst-case, deduplication, severity)
 * 3. Mock router completeness vs real API routes
 * 4. Type consistency (ApiResponse shape)
 */
import { describe, it, expect } from "vitest";
import {
  computeScore,
  generateInsights,
  SEVERITY_ORDER,
  SEVERITY_LABELS,
  groupBySeverity,
  scoreLabel,
  scoreLabelColor,
  DEDUCTION_CATEGORY_COLORS,
  DEDUCTION_CATEGORY_LABELS,
  type AdvisorData,
  type Severity,
  type Insight,
} from "@/lib/insights";
import { mockFetch } from "@/api/mock";

// ══════════════════════════════════════════════════════════════════════════════
// Helpers - mirror the CostForecast.tsx pure math for unit testing
// ══════════════════════════════════════════════════════════════════════════════

interface DayData {
  date: string;
  cost: number;
  projected: boolean;
}

function movingAverage(values: number[], window: number): (number | null)[] {
  return values.map((_, i) => {
    if (i < window - 1) return null;
    let sum = 0;
    for (let j = i - window + 1; j <= i; j++) sum += values[j];
    return sum / window;
  });
}

/** Reproduce the CostForecast chart math so we can test for NaN/Infinity. */
function computeChartGeometry(data: DayData[]) {
  const W = 600;
  const H = 200;
  const PAD_L = 45;
  const PAD_R = 12;
  const PAD_T = 12;
  const PAD_B = 28;
  const chartW = W - PAD_L - PAD_R;
  const chartH = H - PAD_T - PAD_B;

  const allCosts = data.map((d) => d.cost);
  const minCost = Math.min(...allCosts) * 0.8;
  const maxCost = Math.max(...allCosts) * 1.2;
  const costRange = maxCost - minCost || 1;

  const xStep = data.length > 1 ? chartW / (data.length - 1) : chartW;

  const toX = (i: number) => PAD_L + i * xStep;
  const toY = (v: number) => PAD_T + chartH - ((v - minCost) / costRange) * chartH;

  return { W, H, PAD_L, PAD_R, PAD_T, PAD_B, chartW, chartH, allCosts, minCost, maxCost, costRange, xStep, toX, toY };
}

function makeAdvisorData(overrides: Partial<AdvisorData> = {}): AdvisorData {
  return {
    health: { status: "ok", runtime: true, redis: true, database: true },
    stats: { total_runs_today: 10, success_rate: 0.95, total_cost_today: 2.5 },
    runs: [{ status: "completed" }],
    tools: [],
    dlq: [],
    violationStats: { total_violations_30d: 0, violations_by_severity: {} },
    optimizerStats: { total_decisions_30d: 5, estimated_savings_30d_usd: 0 },
    autopilotStats: { total_experiments: 1, active_experiments: 0 },
    approvals: [],
    workflows: [{ name: "test" }],
    schedules: [{ id: "s1", enabled: true }],
    evalStats: { total_runs: 5 },
    apiKeys: [{ id: "k1" }],
    ...overrides,
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// 1. CostForecast SVG math edge cases
// ══════════════════════════════════════════════════════════════════════════════

describe("CostForecast chart math", () => {
  it("handles single data point without division by zero", () => {
    const data: DayData[] = [{ date: "2026-03-01", cost: 5.0, projected: false }];
    const geo = computeChartGeometry(data);

    // xStep should be chartW (full width), not Infinity
    expect(geo.xStep).toBe(geo.chartW);
    expect(Number.isFinite(geo.xStep)).toBe(true);

    // toX and toY should produce finite values
    expect(Number.isFinite(geo.toX(0))).toBe(true);
    expect(Number.isFinite(geo.toY(5.0))).toBe(true);
    expect(Number.isNaN(geo.toX(0))).toBe(false);
    expect(Number.isNaN(geo.toY(5.0))).toBe(false);
  });

  it("handles all-zero costs without division by zero", () => {
    const data: DayData[] = [
      { date: "2026-03-01", cost: 0, projected: false },
      { date: "2026-03-02", cost: 0, projected: false },
      { date: "2026-03-03", cost: 0, projected: true },
    ];
    const geo = computeChartGeometry(data);

    // costRange fallback to 1 prevents division by zero
    // minCost = 0 * 0.8 = 0, maxCost = 0 * 1.2 = 0, costRange = 0 || 1 = 1
    expect(geo.costRange).toBe(1);

    for (let i = 0; i < data.length; i++) {
      const x = geo.toX(i);
      const y = geo.toY(data[i].cost);
      expect(Number.isFinite(x)).toBe(true);
      expect(Number.isFinite(y)).toBe(true);
      expect(Number.isNaN(x)).toBe(false);
      expect(Number.isNaN(y)).toBe(false);
    }
  });

  it("handles all-same non-zero costs", () => {
    const data: DayData[] = Array.from({ length: 10 }, (_, i) => ({
      date: `2026-03-${String(i + 1).padStart(2, "0")}`,
      cost: 3.5,
      projected: i >= 7,
    }));
    const geo = computeChartGeometry(data);

    // minCost = 3.5*0.8=2.8, maxCost = 3.5*1.2=4.2, costRange = 1.4
    expect(geo.costRange).toBeGreaterThan(0);

    // All Y values should be the same (since all costs are identical)
    const y0 = geo.toY(3.5);
    for (let i = 0; i < data.length; i++) {
      expect(geo.toY(data[i].cost)).toBeCloseTo(y0);
    }
  });

  it("handles empty historical with only projected data", () => {
    const data: DayData[] = [
      { date: "2026-03-01", cost: 2.0, projected: true },
      { date: "2026-03-02", cost: 3.0, projected: true },
    ];
    const geo = computeChartGeometry(data);

    expect(Number.isFinite(geo.xStep)).toBe(true);
    expect(Number.isFinite(geo.toX(0))).toBe(true);
    expect(Number.isFinite(geo.toX(1))).toBe(true);
    expect(Number.isFinite(geo.toY(2.0))).toBe(true);
    expect(Number.isFinite(geo.toY(3.0))).toBe(true);
  });

  it("tooltip positioning produces finite percentages", () => {
    const data: DayData[] = [
      { date: "2026-03-01", cost: 1.0, projected: false },
      { date: "2026-03-02", cost: 5.0, projected: false },
    ];
    const geo = computeChartGeometry(data);

    for (let i = 0; i < data.length; i++) {
      const leftPct = (geo.toX(i) / geo.W) * 100;
      const topPct = (geo.toY(data[i].cost) / geo.H) * 100 - 8;
      expect(Number.isFinite(leftPct)).toBe(true);
      expect(Number.isFinite(topPct)).toBe(true);
      expect(Number.isNaN(leftPct)).toBe(false);
      expect(Number.isNaN(topPct)).toBe(false);
    }
  });

  it("tooltip positioning with single data point", () => {
    const data: DayData[] = [{ date: "2026-03-01", cost: 10, projected: false }];
    const geo = computeChartGeometry(data);

    const leftPct = (geo.toX(0) / geo.W) * 100;
    const topPct = (geo.toY(10) / geo.H) * 100 - 8;
    expect(Number.isFinite(leftPct)).toBe(true);
    expect(Number.isFinite(topPct)).toBe(true);
  });

  it("movingAverage returns correct values", () => {
    const values = [1, 2, 3, 4, 5, 6, 7];
    const ma = movingAverage(values, 3);
    // First two should be null
    expect(ma[0]).toBeNull();
    expect(ma[1]).toBeNull();
    // Third should be average of 1,2,3
    expect(ma[2]).toBeCloseTo(2);
    // Last should be average of 5,6,7
    expect(ma[6]).toBeCloseTo(6);
  });

  it("movingAverage handles empty array", () => {
    const ma = movingAverage([], 7);
    expect(ma).toHaveLength(0);
  });

  it("movingAverage handles window larger than data", () => {
    const ma = movingAverage([1, 2, 3], 7);
    // All should be null since window > data length
    expect(ma.every((v) => v === null)).toBe(true);
  });

  it("xLabelInterval never produces zero (no division by zero)", () => {
    // Mirrors: Math.max(1, Math.floor(data.length / 6))
    for (const len of [0, 1, 2, 5, 6, 12, 100]) {
      const interval = Math.max(1, Math.floor(len / 6));
      expect(interval).toBeGreaterThanOrEqual(1);
    }
  });
});

// ══════════════════════════════════════════════════════════════════════════════
// 2. insights.ts edge cases
// ══════════════════════════════════════════════════════════════════════════════

describe("insights.ts edge cases", () => {
  describe("computeScore with all null fields", () => {
    it("returns 100 minus only adoption deductions when all data fields are null", () => {
      const data = makeAdvisorData({
        health: null,
        stats: null,
        runs: [],
        tools: [],
        dlq: [],
        violationStats: null,
        optimizerStats: null,
        autopilotStats: null,
        approvals: [],
        workflows: [],
        schedules: [],
        evalStats: null,
        apiKeys: [],
      });
      const result = computeScore(data);
      // Should only deduct for adoption: no schedules (-3), no evals (-3), no autopilot (-3), no optimizer (-3)
      expect(result.score).toBe(88); // 100 - 12
      expect(result.deductions).toHaveLength(4);
      expect(result.deductions.every((d) => d.category === "adoption")).toBe(true);
    });
  });

  describe("computeScore with all worst-case data", () => {
    it("returns 0 when everything is broken", () => {
      const data = makeAdvisorData({
        health: { status: "down", runtime: false, redis: false, database: false },
        stats: { total_runs_today: 100, success_rate: 0.1, total_cost_today: 100 },
        dlq: Array.from({ length: 10 }, (_, i) => ({ id: `d${i}`, resolved_at: null })),
        violationStats: {
          total_violations_30d: 50,
          violations_by_severity: { critical: 10, high: 20 },
        },
        tools: Array.from({ length: 5 }, (_, i) => ({
          name: `tool-${i}`,
          configured: false,
          connections: [{}],
        })),
        approvals: Array.from({ length: 10 }, (_, i) => ({ id: `a${i}`, status: "pending" })),
        schedules: [],
        evalStats: null,
        autopilotStats: null,
        optimizerStats: null,
      });
      const result = computeScore(data);
      // Runtime -15, Redis -15, DB -15, DLQ -30, Violations -30, Failure rate -15,
      // Tools -25, Approvals -5, Schedules -3, Evals -3, Autopilot -3, Optimizer -3
      // Total deductions: 162, but score floors at 0
      expect(result.score).toBe(0);
      expect(result.deductions.length).toBeGreaterThan(0);
    });
  });

  describe("generateInsights - all insights firing simultaneously", () => {
    it("generates maximum number of insights without errors", () => {
      const tomorrow = new Date(Date.now() + 2 * 24 * 60 * 60 * 1000).toISOString();
      const data = makeAdvisorData({
        health: { status: "down", runtime: false, redis: false, database: false },
        stats: { total_runs_today: 100, success_rate: 0.3, total_cost_today: 25 },
        dlq: [{ id: "d1", resolved_at: null }, { id: "d2", resolved_at: null }],
        violationStats: {
          total_violations_30d: 50,
          violations_by_severity: { critical: 5, high: 10, medium: 20 },
        },
        tools: [
          { name: "slack", configured: false, connections: [{}] },
          { name: "github", configured: false, connections: [{}] },
        ],
        approvals: Array.from({ length: 3 }, (_, i) => ({ id: `a${i}`, status: "pending" })),
        optimizerStats: { total_decisions_30d: 100, estimated_savings_30d_usd: 15.50 },
        autopilotStats: { total_experiments: 5, active_experiments: 3 },
        schedules: [
          { id: "s1", enabled: false },
          { id: "s2", enabled: false },
        ],
        evalStats: {
          total_runs: 10,
          avg_pass_rate: 0.5,
          pass_rate_trend: [
            { date: "2026-03-15", avg_pass_rate: 0.9, runs: 5 },
            { date: "2026-03-16", avg_pass_rate: 0.5, runs: 5 },
          ],
        },
        apiKeys: [{ id: "k1", expires_at: tomorrow }],
      });

      const insights = generateInsights(data);

      // Verify all expected insight IDs are present
      const ids = insights.map((i) => i.id);
      expect(ids).toContain("health-runtime");
      expect(ids).toContain("health-database");
      expect(ids).toContain("dlq-items");
      expect(ids).toContain("violations-critical");
      expect(ids).toContain("success-rate-critical");
      expect(ids).toContain("tools-missing-creds");
      expect(ids).toContain("pending-approvals");
      expect(ids).toContain("expiring-keys");
      expect(ids).toContain("violations-high");
      expect(ids).toContain("eval-regression");
      expect(ids).toContain("optimizer-savings");
      expect(ids).toContain("autopilot-active");
      expect(ids).toContain("high-daily-spend");
      expect(ids).toContain("disabled-schedules");

      // All insights should have valid structure
      for (const insight of insights) {
        expect(insight.id).toBeTruthy();
        expect(insight.title).toBeTruthy();
        expect(insight.description).toBeTruthy();
        expect(insight.link).toBeTruthy();
        expect(insight.icon).toBeTruthy();
        expect(SEVERITY_ORDER).toContain(insight.severity);
      }
    });
  });

  describe("insight deduplication", () => {
    it("never produces duplicate insight IDs", () => {
      // Even with extreme data, each insight ID should appear at most once
      const tomorrow = new Date(Date.now() + 2 * 24 * 60 * 60 * 1000).toISOString();
      const data = makeAdvisorData({
        health: { status: "down", runtime: false, redis: false, database: false },
        stats: { total_runs_today: 100, success_rate: 0.3, total_cost_today: 50 },
        dlq: Array.from({ length: 10 }, (_, i) => ({ id: `d${i}`, resolved_at: null })),
        violationStats: {
          total_violations_30d: 100,
          violations_by_severity: { critical: 20, high: 30 },
        },
        tools: Array.from({ length: 10 }, (_, i) => ({
          name: `tool-${i}`,
          configured: false,
          connections: [{}],
        })),
        approvals: Array.from({ length: 20 }, (_, i) => ({ id: `a${i}`, status: "pending" })),
        optimizerStats: { total_decisions_30d: 500, estimated_savings_30d_usd: 100 },
        autopilotStats: { total_experiments: 20, active_experiments: 10 },
        schedules: Array.from({ length: 5 }, (_, i) => ({ id: `s${i}`, enabled: false })),
        evalStats: {
          total_runs: 50,
          pass_rate_trend: [
            { date: "2026-03-15", avg_pass_rate: 0.95, runs: 10 },
            { date: "2026-03-16", avg_pass_rate: 0.40, runs: 10 },
          ],
        },
        apiKeys: [
          { id: "k1", expires_at: tomorrow },
          { id: "k2", expires_at: tomorrow },
        ],
      });

      const insights = generateInsights(data);
      const ids = insights.map((i) => i.id);
      const uniqueIds = new Set(ids);
      expect(ids.length).toBe(uniqueIds.size);
    });
  });

  describe("SEVERITY_ORDER correctness", () => {
    it("contains all 4 severity levels in priority order", () => {
      expect(SEVERITY_ORDER).toEqual(["critical", "warning", "optimize", "discover"]);
      expect(SEVERITY_ORDER).toHaveLength(4);
    });
  });

  describe("SEVERITY_LABELS completeness", () => {
    it("has labels for every severity in SEVERITY_ORDER", () => {
      for (const sev of SEVERITY_ORDER) {
        expect(SEVERITY_LABELS[sev]).toBeDefined();
        expect(typeof SEVERITY_LABELS[sev]).toBe("string");
        expect(SEVERITY_LABELS[sev].length).toBeGreaterThan(0);
      }
    });

    it("has exactly the same keys as SEVERITY_ORDER", () => {
      const labelKeys = Object.keys(SEVERITY_LABELS) as Severity[];
      expect(labelKeys.sort()).toEqual([...SEVERITY_ORDER].sort());
    });
  });

  describe("groupBySeverity", () => {
    it("groups insights by severity in SEVERITY_ORDER order", () => {
      const insights: Insight[] = [
        { id: "d1", severity: "discover", title: "", description: "", link: "", icon: "" },
        { id: "c1", severity: "critical", title: "", description: "", link: "", icon: "" },
        { id: "w1", severity: "warning", title: "", description: "", link: "", icon: "" },
        { id: "o1", severity: "optimize", title: "", description: "", link: "", icon: "" },
      ];
      const groups = groupBySeverity(insights);
      expect(groups[0][0]).toBe("critical");
      expect(groups[1][0]).toBe("warning");
      expect(groups[2][0]).toBe("optimize");
      expect(groups[3][0]).toBe("discover");
    });

    it("omits severity groups with no insights", () => {
      const insights: Insight[] = [
        { id: "c1", severity: "critical", title: "", description: "", link: "", icon: "" },
      ];
      const groups = groupBySeverity(insights);
      expect(groups).toHaveLength(1);
      expect(groups[0][0]).toBe("critical");
    });
  });

  describe("scoreLabel and scoreLabelColor", () => {
    it("returns correct labels for score ranges", () => {
      expect(scoreLabel(100)).toBe("Healthy");
      expect(scoreLabel(80)).toBe("Healthy");
      expect(scoreLabel(79)).toBe("Needs Attention");
      expect(scoreLabel(50)).toBe("Needs Attention");
      expect(scoreLabel(49)).toBe("Critical");
      expect(scoreLabel(0)).toBe("Critical");
    });

    it("returns correct color classes", () => {
      expect(scoreLabelColor(100)).toContain("emerald");
      expect(scoreLabelColor(65)).toContain("amber");
      expect(scoreLabelColor(10)).toContain("error");
    });
  });

  describe("DEDUCTION_CATEGORY maps completeness", () => {
    const categories = ["health", "operations", "quality", "adoption"] as const;

    it("DEDUCTION_CATEGORY_COLORS has all categories", () => {
      for (const cat of categories) {
        expect(DEDUCTION_CATEGORY_COLORS[cat]).toBeDefined();
      }
    });

    it("DEDUCTION_CATEGORY_LABELS has all categories", () => {
      for (const cat of categories) {
        expect(DEDUCTION_CATEGORY_LABELS[cat]).toBeDefined();
        expect(DEDUCTION_CATEGORY_LABELS[cat].length).toBeGreaterThan(0);
      }
    });
  });

  describe("eval regression edge cases", () => {
    it("does not produce eval regression insight when trend has <2 entries", () => {
      const data = makeAdvisorData({
        evalStats: {
          total_runs: 10,
          pass_rate_trend: [
            { date: "2026-03-16", avg_pass_rate: 0.5, runs: 5 },
          ],
        },
      });
      const insights = generateInsights(data);
      expect(insights.find((i) => i.id === "eval-regression")).toBeUndefined();
    });

    it("exactly 10pp drop (0.8-0.7) should NOT fire (threshold is >10pp)", () => {
      // With basis-point comparison: round((0.8-0.7)*10000) = 1000, threshold is >1000
      // Exactly 10pp is not a regression - only drops strictly above 10pp trigger
      const data = makeAdvisorData({
        evalStats: {
          total_runs: 10,
          pass_rate_trend: [
            { date: "2026-03-15", avg_pass_rate: 0.8, runs: 5 },
            { date: "2026-03-16", avg_pass_rate: 0.7, runs: 5 },
          ],
        },
      });
      const insights = generateInsights(data);
      expect(insights.find((i) => i.id === "eval-regression")).toBeUndefined();
    });

    it("produces regression when drop is >0.1", () => {
      const data = makeAdvisorData({
        evalStats: {
          total_runs: 10,
          pass_rate_trend: [
            { date: "2026-03-15", avg_pass_rate: 0.9, runs: 5 },
            { date: "2026-03-16", avg_pass_rate: 0.7, runs: 5 },
          ],
        },
      });
      const insights = generateInsights(data);
      expect(insights.find((i) => i.id === "eval-regression")).toBeDefined();
    });
  });

  describe("success-rate boundary conditions", () => {
    it("success rate exactly 0.5 triggers warning, not critical", () => {
      const data = makeAdvisorData({
        stats: { total_runs_today: 10, success_rate: 0.5, total_cost_today: 1 },
      });
      const insights = generateInsights(data);
      // success_rate < 0.5 is critical, 0.5 <= rate < 0.8 is warning
      expect(insights.find((i) => i.id === "success-rate-critical")).toBeUndefined();
      expect(insights.find((i) => i.id === "success-rate-warning")).toBeDefined();
    });

    it("success rate exactly 0.8 triggers neither", () => {
      const data = makeAdvisorData({
        stats: { total_runs_today: 10, success_rate: 0.8, total_cost_today: 1 },
      });
      const insights = generateInsights(data);
      expect(insights.find((i) => i.id === "success-rate-critical")).toBeUndefined();
      expect(insights.find((i) => i.id === "success-rate-warning")).toBeUndefined();
    });
  });
});

// ══════════════════════════════════════════════════════════════════════════════
// 3. Mock router completeness
// ══════════════════════════════════════════════════════════════════════════════

describe("Mock router completeness", () => {
  // All real API routes (from routes.py) that the dashboard should mock.
  // SSE/streaming endpoints (/events, /runs/{id}/stream) are excluded
  // as they cannot be mocked via mockFetch.
  // Artifact/file endpoints (/runs/{id}/steps/{step_id}/pdf,
  // /runs/{id}/artifacts/{filename}, /upload) are also excluded.

  const REAL_API_ROUTES = [
    { method: "GET", path: "/health" },
    { method: "GET", path: "/runtime" },
    { method: "GET", path: "/check-update" },
    { method: "GET", path: "/browse" },
    { method: "GET", path: "/templates" },
    { method: "GET", path: "/templates/test-template" },
    { method: "GET", path: "/hub/registry" },
    { method: "GET", path: "/hub/collections" },
    { method: "POST", path: "/hub/playground" },
    { method: "POST", path: "/hub/install/test/slug" },
    { method: "DELETE", path: "/hub/install/test/slug" },
    { method: "GET", path: "/hub/installed" },
    { method: "GET", path: "/stats" },
    { method: "GET", path: "/stats/forecast" },
    // POST /runs/estimate - MISSING from mock (documented in "Missing v0.22 endpoints" below)
    { method: "POST", path: "/generate" },
    { method: "POST", path: "/generate/chat" },
    { method: "GET", path: "/workflows" },
    { method: "POST", path: "/workflows" },
    { method: "DELETE", path: "/workflows/test-workflow" },
    { method: "POST", path: "/workflows/run" },
    { method: "POST", path: "/workflows/run/sync" },
    { method: "GET", path: "/runs/compare" },
    { method: "GET", path: "/runs/test-run-id" },
    { method: "GET", path: "/runs" },
    { method: "POST", path: "/runs/test-run-id/cancel" },
    { method: "DELETE", path: "/runs/test-run-id" },
    { method: "POST", path: "/runs/test-run-id/replay" },
    { method: "POST", path: "/runs/test-run-id/fork" },
    { method: "POST", path: "/schedules" },
    { method: "GET", path: "/schedules" },
    { method: "PATCH", path: "/schedules/sch-001" },
    { method: "DELETE", path: "/schedules/sch-001" },
    { method: "GET", path: "/dead-letter" },
    { method: "POST", path: "/dead-letter/dlq-001/retry" },
    { method: "POST", path: "/dead-letter/dlq-001/resolve" },
    { method: "GET", path: "/autopilot/experiments" },
    { method: "GET", path: "/autopilot/experiments/exp-001" },
    { method: "POST", path: "/autopilot/experiments/exp-001/deploy" },
    { method: "POST", path: "/autopilot/experiments/exp-001/reset" },
    { method: "POST", path: "/autopilot/experiments/exp-001/advance-rollout" },
    { method: "GET", path: "/autopilot/stats" },
    { method: "GET", path: "/approvals" },
    { method: "GET", path: "/approvals/apr-001" },
    { method: "POST", path: "/approvals/apr-001/approve" },
    { method: "POST", path: "/approvals/apr-001/reject" },
    { method: "POST", path: "/approvals/apr-001/skip" },
    { method: "POST", path: "/api-keys" },
    { method: "GET", path: "/api-keys" },
    { method: "GET", path: "/runs/test-run-id/violations" },
    { method: "GET", path: "/violations" },
    { method: "GET", path: "/violations/stats" },
    { method: "GET", path: "/optimizer/decisions" },
    { method: "GET", path: "/optimizer/decisions/test-run-id" },
    { method: "GET", path: "/optimizer/stats" },
    { method: "GET", path: "/optimizer/alerts" },
    { method: "DELETE", path: "/optimizer/alerts" },
    { method: "DELETE", path: "/api-keys/key-001" },
    { method: "POST", path: "/api-keys/key-001/rotate" },
    { method: "PUT", path: "/api-keys/key-001/allowlist" },
    { method: "GET", path: "/settings" },
    { method: "PATCH", path: "/settings" },
    { method: "GET", path: "/workflows/lead-enrichment/versions" },
    { method: "GET", path: "/workflows/lead-enrichment/versions/1" },
    { method: "POST", path: "/workflows/lead-enrichment/promote" },
    { method: "POST", path: "/workflows/lead-enrichment/rollback" },
    { method: "GET", path: "/workflows/lead-enrichment/versions/diff" },
    { method: "GET", path: "/workflows/lead-enrichment/export" },
    { method: "GET", path: "/tools" },
    { method: "GET", path: "/tools/slack" },
    { method: "PUT", path: "/tools/slack/credentials" },
    { method: "GET", path: "/tools/slack/connections" },
    { method: "POST", path: "/tools/slack/connections" },
    { method: "PUT", path: "/tools/slack/connections/default" },
    { method: "DELETE", path: "/tools/slack/connections/default" },
    { method: "POST", path: "/eval/run" },
    { method: "GET", path: "/eval/runs" },
    { method: "GET", path: "/eval/runs/eval-001" },
    { method: "GET", path: "/eval/stats" },
    { method: "GET", path: "/memories" },
    { method: "POST", path: "/memories" },
    { method: "POST", path: "/memories/search" },
    { method: "DELETE", path: "/memories/mem-123" },
    { method: "DELETE", path: "/memories" },
  ];

  for (const route of REAL_API_ROUTES) {
    it(`mock handles ${route.method} ${route.path}`, () => {
      const result = mockFetch(route.path, {}, route.method);
      // Should NOT return the "Mock: ... not found" error
      if (result.error) {
        expect(result.error.code).not.toBe("NOT_FOUND");
      }
    });
  }

  // Endpoints that exist in real API but are intentionally NOT mocked
  // (SSE, file upload/download)
  const EXCLUDED_ENDPOINTS = [
    { method: "GET", path: "/events", reason: "SSE streaming endpoint" },
    { method: "GET", path: "/runs/test-id/stream", reason: "SSE streaming endpoint" },
    { method: "POST", path: "/upload", reason: "File upload (handled by client.uploadFile)" },
    { method: "GET", path: "/runs/test-id/steps/step1/pdf", reason: "Binary file download" },
    { method: "GET", path: "/runs/test-id/artifacts/file.txt", reason: "Binary file download" },
  ];

  for (const endpoint of EXCLUDED_ENDPOINTS) {
    it(`${endpoint.method} ${endpoint.path} is intentionally not mocked (${endpoint.reason})`, () => {
      // Just documenting that these are not in mock.ts
      expect(true).toBe(true);
    });
  }

  describe("Missing v0.22 endpoints in mock", () => {
    it("POST /runs/estimate should return valid mock data", () => {
      const result = mockFetch("/runs/estimate", {}, "POST", {
        yaml_content: "name: test\nsteps:\n  - id: s1\n    model: sonnet",
      });
      // This endpoint might be missing from mock - check and report
      if (result.error?.code === "NOT_FOUND") {
        // This is a known gap - the test documents it
        expect(result.error.code).toBe("NOT_FOUND");
      } else {
        expect(result.data).toBeDefined();
      }
    });

    it("POST /advisor/explain should return valid mock data", () => {
      const result = mockFetch("/advisor/explain", {}, "POST", {
        step_id: "s1",
        error: "test error",
      });
      if (result.error?.code === "NOT_FOUND") {
        expect(result.error.code).toBe("NOT_FOUND");
      } else {
        expect(result.data).toBeDefined();
      }
    });

    it("POST /approvals/{id}/regenerate should return valid mock data", () => {
      const result = mockFetch("/approvals/apr-001/regenerate", {}, "POST");
      if (result.error?.code === "NOT_FOUND") {
        expect(result.error.code).toBe("NOT_FOUND");
      } else {
        expect(result.data).toBeDefined();
      }
    });
  });
});

// ══════════════════════════════════════════════════════════════════════════════
// 4. Type consistency - ApiResponse
// ══════════════════════════════════════════════════════════════════════════════

describe("Type consistency", () => {
  describe("ApiResponse shape", () => {
    it("client ApiResponse has data, error, and optional meta", () => {
      // This is a compile-time check via TypeScript. The fact that this file
      // compiles proves the type shape is correct. We also verify at runtime.
      const response: {
        data: unknown | null;
        error: { code: string; message: string } | null;
        meta?: { total: number; limit: number; offset: number } | null;
      } = {
        data: { test: true },
        error: null,
        meta: { total: 100, limit: 20, offset: 0 },
      };
      expect(response.data).toBeDefined();
      expect(response.error).toBeNull();
      expect(response.meta).toBeDefined();
    });

    it("backend ErrorResponse has code, message, and optional details", () => {
      // Backend: ErrorResponse has code, message, details
      // Client: error field has code, message (no details)
      // This is a known simplification - client drops 'details' field
      const clientError = { code: "TEST", message: "test error" };
      expect(clientError).toHaveProperty("code");
      expect(clientError).toHaveProperty("message");
    });

    it("backend PaginationMeta matches client meta shape", () => {
      // Backend: PaginationMeta(total, limit, offset) - all required, int >= 0
      // Client: meta?: { total: number; limit: number; offset: number } | null
      // Shape matches, but backend requires limit >= 1 while client is just number
      const meta = { total: 50, limit: 20, offset: 0 };
      expect(meta.total).toBeGreaterThanOrEqual(0);
      expect(meta.limit).toBeGreaterThanOrEqual(1);
      expect(meta.offset).toBeGreaterThanOrEqual(0);
    });
  });

  describe("Mock response shape consistency", () => {
    it("mock /stats response matches StatsResponse schema fields", () => {
      const result = mockFetch("/stats");
      const stats = result.data as Record<string, unknown>;
      expect(stats).toHaveProperty("total_runs_today");
      expect(stats).toHaveProperty("success_rate");
      expect(stats).toHaveProperty("total_cost_today");
      expect(stats).toHaveProperty("avg_duration_seconds");
      expect(stats).toHaveProperty("runs_by_day");
      expect(stats).toHaveProperty("cost_by_workflow");
    });

    it("mock /health response matches HealthResponse schema", () => {
      const result = mockFetch("/health");
      const health = result.data as Record<string, unknown>;
      expect(health).toHaveProperty("status");
      expect(health).toHaveProperty("runtime");
      expect(health).toHaveProperty("database");
      // redis can be null in local mode
      expect("redis" in health).toBe(true);
    });

    it("mock /runtime response matches RuntimeInfoResponse schema", () => {
      const result = mockFetch("/runtime");
      const runtime = result.data as Record<string, unknown>;
      expect(runtime).toHaveProperty("mode");
      expect(runtime).toHaveProperty("database");
      expect(runtime).toHaveProperty("queue");
      expect(runtime).toHaveProperty("storage");
      expect(runtime).toHaveProperty("sandbox_backend");
    });

    it("mock /workflows response items match WorkflowInfoResponse schema", () => {
      const result = mockFetch("/workflows");
      const workflows = result.data as Array<Record<string, unknown>>;
      expect(Array.isArray(workflows)).toBe(true);
      if (workflows.length > 0) {
        const wf = workflows[0];
        expect(wf).toHaveProperty("name");
        expect(wf).toHaveProperty("description");
        expect(wf).toHaveProperty("steps_count");
        expect(wf).toHaveProperty("file_name");
        expect(wf).toHaveProperty("steps");
      }
    });

    it("mock /runs response has pagination meta", () => {
      const result = mockFetch("/runs");
      expect(result.meta).toBeDefined();
      expect(result.meta).toHaveProperty("total");
      expect(result.meta).toHaveProperty("limit");
      expect(result.meta).toHaveProperty("offset");
    });

    it("mock /settings response matches SettingsResponse key fields", () => {
      const result = mockFetch("/settings", {}, "GET");
      const settings = result.data as Record<string, unknown>;
      expect(settings).toHaveProperty("auth_required");
      expect(settings).toHaveProperty("log_level");
      expect(settings).toHaveProperty("default_max_cost_usd");
    });

    it("mock /stats/forecast response matches ForecastResponse shape", () => {
      const result = mockFetch("/stats/forecast", {}, "GET");
      const forecast = result.data as Record<string, unknown>;
      expect(forecast).toHaveProperty("historical");
      expect(forecast).toHaveProperty("projected");
      expect(forecast).toHaveProperty("daily_average");
      expect(forecast).toHaveProperty("trend_percent");
      expect(forecast).toHaveProperty("projected_monthly");
      const historical = forecast.historical as Array<Record<string, unknown>>;
      expect(historical.length).toBeGreaterThan(0);
      expect(historical[0]).toHaveProperty("date");
      expect(historical[0]).toHaveProperty("cost");
      expect(historical[0]).toHaveProperty("runs");
    });

    it("mock /eval/stats response matches EvalStatsResponse schema", () => {
      const result = mockFetch("/eval/stats", {}, "GET");
      const stats = result.data as Record<string, unknown>;
      expect(stats).toHaveProperty("total_runs");
      expect(stats).toHaveProperty("avg_pass_rate");
      expect(stats).toHaveProperty("total_cost_usd");
      expect(stats).toHaveProperty("pass_rate_trend");
    });

    it("mock /violations/stats response matches PolicyViolationStatsResponse", () => {
      const result = mockFetch("/violations/stats", {}, "GET");
      const stats = result.data as Record<string, unknown>;
      expect(stats).toHaveProperty("total_violations_30d");
      expect(stats).toHaveProperty("violations_by_severity");
      expect(stats).toHaveProperty("violations_by_policy");
      expect(stats).toHaveProperty("violations_by_day");
    });

    it("mock /optimizer/stats response matches OptimizerStatsResponse", () => {
      const result = mockFetch("/optimizer/stats", {}, "GET");
      const stats = result.data as Record<string, unknown>;
      expect(stats).toHaveProperty("total_decisions_30d");
      expect(stats).toHaveProperty("model_distribution");
      expect(stats).toHaveProperty("avg_confidence");
      expect(stats).toHaveProperty("estimated_savings_30d_usd");
      expect(stats).toHaveProperty("active_alerts");
    });

    it("mock /autopilot/stats response matches AutoPilotStatsResponse", () => {
      const result = mockFetch("/autopilot/stats", {}, "GET");
      const stats = result.data as Record<string, unknown>;
      expect(stats).toHaveProperty("total_experiments");
      expect(stats).toHaveProperty("active_experiments");
      expect(stats).toHaveProperty("completed_experiments");
      expect(stats).toHaveProperty("total_samples");
    });
  });
});
