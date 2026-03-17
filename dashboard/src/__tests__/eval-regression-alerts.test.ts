/**
 * Comprehensive tests for eval quality regression alerts in the insights system.
 *
 * Tests cover computeScore() and generateInsights() eval-regression logic:
 *  - No eval data
 *  - Eval data without trend
 *  - Small drops (<10pp) - should NOT fire
 *  - Exact 10pp drop - should NOT fire (>0.1, not >=0.1)
 *  - Large drops (>10pp) - SHOULD fire
 *  - Improvements (negative drop) - should NOT fire
 *  - Score deduction correctness
 *  - Insight description format
 *  - Unit correctness (0..1 scale)
 */

import { describe, it, expect } from "vitest";
import {
  computeScore,
  generateInsights,
  type AdvisorData,
  type Insight,
} from "@/lib/insights";

// ── Helper ──────────────────────────────────────────────────────────────────

/** Build a fully-healthy AdvisorData, overridable per test. */
function makeData(overrides: Partial<AdvisorData> = {}): AdvisorData {
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
    workflows: [{ name: "w1" }],
    schedules: [{ id: "s1", enabled: true }],
    evalStats: { total_runs: 10 },
    apiKeys: [{ id: "k1" }],
    ...overrides,
  };
}

/** Build a pass_rate_trend array with exactly two data points. */
function twoPointTrend(previous: number, latest: number) {
  return [
    { date: "2026-03-15", avg_pass_rate: previous, runs: 5 },
    { date: "2026-03-16", avg_pass_rate: latest, runs: 5 },
  ];
}

/** Find the eval-regression insight from a list. */
function findEvalInsight(insights: Insight[]) {
  return insights.find((i) => i.id === "eval-regression");
}

/** Find the eval quality regression deduction from score result. */
function findEvalDeduction(data: AdvisorData) {
  const result = computeScore(data);
  return result.deductions.find((d) => d.label === "Eval quality regression");
}

// ==========================================================================
// computeScore - eval regression deduction
// ==========================================================================

describe("computeScore - eval quality regression", () => {
  // ── 1. No eval data ──

  it("should NOT deduct when evalStats is null", () => {
    const data = makeData({ evalStats: null });
    const deduction = findEvalDeduction(data);
    expect(deduction).toBeUndefined();
  });

  // ── 2. Eval data without trend ──

  it("should NOT deduct when pass_rate_trend is undefined", () => {
    const data = makeData({
      evalStats: { total_runs: 10 },
    });
    const deduction = findEvalDeduction(data);
    expect(deduction).toBeUndefined();
  });

  it("should NOT deduct when pass_rate_trend is empty", () => {
    const data = makeData({
      evalStats: { total_runs: 10, pass_rate_trend: [] },
    });
    const deduction = findEvalDeduction(data);
    expect(deduction).toBeUndefined();
  });

  it("should NOT deduct when pass_rate_trend has only 1 data point", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: [{ date: "2026-03-16", avg_pass_rate: 0.5, runs: 5 }],
      },
    });
    const deduction = findEvalDeduction(data);
    expect(deduction).toBeUndefined();
  });

  // ── 3. Small drop (<10pp) - should NOT fire ──

  it("should NOT deduct for 5pp drop (0.85 -> 0.80)", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.85, 0.80),
      },
    });
    const deduction = findEvalDeduction(data);
    expect(deduction).toBeUndefined();
  });

  it("should NOT deduct for 1pp drop (0.90 -> 0.89)", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.90, 0.89),
      },
    });
    const deduction = findEvalDeduction(data);
    expect(deduction).toBeUndefined();
  });

  it("should NOT deduct for 9pp drop (0.90 -> 0.81)", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.90, 0.81),
      },
    });
    const deduction = findEvalDeduction(data);
    expect(deduction).toBeUndefined();
  });

  // ── 4. Exact 10pp drop - should NOT fire (>0.1, not >=0.1) ──

  it("should NOT deduct for exact 10pp drop (0.90 -> 0.80)", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.90, 0.80),
      },
    });
    const deduction = findEvalDeduction(data);
    // The code uses `drop > 0.1` (strict), so exactly 0.10 should NOT trigger.
    expect(deduction).toBeUndefined();
  });

  it("should NOT deduct for exact 10pp drop at lower range (0.50 -> 0.40)", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.50, 0.40),
      },
    });
    const deduction = findEvalDeduction(data);
    expect(deduction).toBeUndefined();
  });

  // ── 5. Large drop (>10pp) - SHOULD fire ──

  it("should deduct 10 points for 20pp drop (0.90 -> 0.70)", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.90, 0.70),
      },
    });
    const deduction = findEvalDeduction(data);
    expect(deduction).toBeDefined();
    expect(deduction!.points).toBe(10);
    expect(deduction!.category).toBe("quality");
  });

  it("should deduct for 11pp drop (just above threshold)", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.90, 0.79),
      },
    });
    const deduction = findEvalDeduction(data);
    expect(deduction).toBeDefined();
    expect(deduction!.points).toBe(10);
  });

  it("should deduct for catastrophic drop (0.95 -> 0.10)", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.95, 0.10),
      },
    });
    const deduction = findEvalDeduction(data);
    expect(deduction).toBeDefined();
    expect(deduction!.points).toBe(10);
  });

  it("should deduct for drop from 100% to 0% (1.0 -> 0.0)", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(1.0, 0.0),
      },
    });
    const deduction = findEvalDeduction(data);
    expect(deduction).toBeDefined();
    expect(deduction!.points).toBe(10);
  });

  // ── 6. Improvement (no drop) - should NOT fire ──

  it("should NOT deduct for improvement (0.70 -> 0.90)", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.70, 0.90),
      },
    });
    const deduction = findEvalDeduction(data);
    expect(deduction).toBeUndefined();
  });

  it("should NOT deduct for stable rate (0.85 -> 0.85)", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.85, 0.85),
      },
    });
    const deduction = findEvalDeduction(data);
    expect(deduction).toBeUndefined();
  });

  // ── 7. Score deduction amount ──

  it("should deduct exactly 10 from score for regression", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.90, 0.70),
      },
    });
    const result = computeScore(data);
    // Base is 100 for healthy system; only eval regression deduction
    expect(result.score).toBe(90);
    expect(result.deductions).toHaveLength(1);
    expect(result.deductions[0].points).toBe(10);
  });

  // ── 8. Multi-point trend - uses last two points ──

  it("should compare only last two trend points (ignoring earlier ones)", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: [
          { date: "2026-03-10", avg_pass_rate: 0.50, runs: 5 },
          { date: "2026-03-11", avg_pass_rate: 0.60, runs: 5 },
          { date: "2026-03-12", avg_pass_rate: 0.70, runs: 5 },
          { date: "2026-03-13", avg_pass_rate: 0.80, runs: 5 },
          // Last two: 0.80 -> 0.65 = 15pp drop - SHOULD fire
          { date: "2026-03-14", avg_pass_rate: 0.65, runs: 5 },
        ],
      },
    });
    const deduction = findEvalDeduction(data);
    expect(deduction).toBeDefined();
    expect(deduction!.points).toBe(10);
  });

  it("should NOT fire when last two points have small drop even if earlier had big drop", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: [
          { date: "2026-03-10", avg_pass_rate: 0.95, runs: 5 },
          // Big historical drop 0.95 -> 0.50 (not checked)
          { date: "2026-03-11", avg_pass_rate: 0.50, runs: 5 },
          // Last two: 0.50 -> 0.48 = 2pp drop - NOT fire
          { date: "2026-03-12", avg_pass_rate: 0.48, runs: 5 },
        ],
      },
    });
    const deduction = findEvalDeduction(data);
    expect(deduction).toBeUndefined();
  });
});

// ==========================================================================
// generateInsights - eval regression insight
// ==========================================================================

describe("generateInsights - eval quality regression", () => {
  // ── 1. No eval data ──

  it("should NOT generate eval-regression insight when evalStats is null", () => {
    const data = makeData({ evalStats: null });
    const insight = findEvalInsight(generateInsights(data));
    expect(insight).toBeUndefined();
  });

  // ── 2. No trend data ──

  it("should NOT generate insight when pass_rate_trend is undefined", () => {
    const data = makeData({ evalStats: { total_runs: 10 } });
    const insight = findEvalInsight(generateInsights(data));
    expect(insight).toBeUndefined();
  });

  it("should NOT generate insight when pass_rate_trend is empty", () => {
    const data = makeData({
      evalStats: { total_runs: 10, pass_rate_trend: [] },
    });
    const insight = findEvalInsight(generateInsights(data));
    expect(insight).toBeUndefined();
  });

  it("should NOT generate insight when pass_rate_trend has only 1 point", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: [{ date: "2026-03-16", avg_pass_rate: 0.3, runs: 5 }],
      },
    });
    const insight = findEvalInsight(generateInsights(data));
    expect(insight).toBeUndefined();
  });

  // ── 3. Small drop (<10pp) - no alert ──

  it("should NOT generate insight for 5pp drop", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.85, 0.80),
      },
    });
    const insight = findEvalInsight(generateInsights(data));
    expect(insight).toBeUndefined();
  });

  // ── 4. Exact 10pp drop - no alert (strict >) ──

  it("should NOT generate insight for exact 10pp drop (boundary)", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.90, 0.80),
      },
    });
    const insight = findEvalInsight(generateInsights(data));
    expect(insight).toBeUndefined();
  });

  // ── 5. Large drop (>10pp) - SHOULD alert ──

  it("should generate insight for 20pp drop (0.90 -> 0.70)", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.90, 0.70),
      },
    });
    const insight = findEvalInsight(generateInsights(data));
    expect(insight).toBeDefined();
    expect(insight!.severity).toBe("warning");
    expect(insight!.id).toBe("eval-regression");
    expect(insight!.link).toBe("/evaluations");
    expect(insight!.icon).toBe("TrendingDown");
  });

  it("should generate insight for 11pp drop (just above threshold)", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.90, 0.79),
      },
    });
    const insight = findEvalInsight(generateInsights(data));
    expect(insight).toBeDefined();
  });

  // ── 6. Improvement - no alert ──

  it("should NOT generate insight for improvement (0.70 -> 0.90)", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.70, 0.90),
      },
    });
    const insight = findEvalInsight(generateInsights(data));
    expect(insight).toBeUndefined();
  });

  it("should NOT generate insight for stable rate", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.80, 0.80),
      },
    });
    const insight = findEvalInsight(generateInsights(data));
    expect(insight).toBeUndefined();
  });

  // ── 7. Insight description format ──

  it("should show correct percentages in description (90% -> 70%)", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.90, 0.70),
      },
    });
    const insight = findEvalInsight(generateInsights(data));
    expect(insight).toBeDefined();
    expect(insight!.description).toContain("90%");
    expect(insight!.description).toContain("70%");
    expect(insight!.description).toContain("20pp");
    expect(insight!.description).toContain("90% -> 70%");
  });

  it("should show correct percentages for 95% -> 80% (15pp drop)", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.95, 0.80),
      },
    });
    const insight = findEvalInsight(generateInsights(data));
    expect(insight).toBeDefined();
    expect(insight!.description).toContain("15pp");
    expect(insight!.description).toContain("95% -> 80%");
  });

  it("should show correct percentages for 100% -> 50% (50pp drop)", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(1.0, 0.50),
      },
    });
    const insight = findEvalInsight(generateInsights(data));
    expect(insight).toBeDefined();
    expect(insight!.description).toContain("50pp");
    expect(insight!.description).toContain("100% -> 50%");
  });

  it("should include review suggestion in description", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.90, 0.70),
      },
    });
    const insight = findEvalInsight(generateInsights(data));
    expect(insight).toBeDefined();
    expect(insight!.description).toContain("Review recent workflow changes");
  });

  it("should set title to 'Eval quality regression detected'", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.90, 0.70),
      },
    });
    const insight = findEvalInsight(generateInsights(data));
    expect(insight!.title).toBe("Eval quality regression detected");
  });

  // ── 8. Unit correctness (0..1 values) ──

  it("should handle values at 0..1 scale correctly (NOT 0..100)", () => {
    // If the system incorrectly expected 0..100 values,
    // a 0.90 -> 0.70 drop would be 0.20pp (not 20pp) and would NOT fire.
    // This test verifies the system correctly interprets 0..1 scale.
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.90, 0.70),
      },
    });
    const insight = findEvalInsight(generateInsights(data));
    expect(insight).toBeDefined();
    // The drop is 0.20 in 0..1 scale, which is >0.1 threshold
    expect(insight!.description).toContain("20pp");
  });

  it("should NOT treat 90 and 70 as 0..100 scale values (would be 20pp in wrong scale)", () => {
    // If someone accidentally passed 0..100 values, 90-70=20 > 0.1 => true
    // This would be a bug. With proper 0..1 values:
    // 0.90-0.70=0.20 > 0.1 => true (correct)
    // The math happens to give the same boolean result, but the description
    // would show "2000pp (9000% -> 7000%)" which is clearly wrong.
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.90, 0.70),
      },
    });
    const insight = findEvalInsight(generateInsights(data));
    expect(insight).toBeDefined();
    // Verify description shows human-readable percentages, not raw fractions
    expect(insight!.description).not.toContain("9000%");
    expect(insight!.description).not.toContain("7000%");
    expect(insight!.description).toContain("90%");
    expect(insight!.description).toContain("70%");
  });

  // ── 9. Floating point edge cases ──

  it("should fire for drop near boundary (0.91 -> 0.809 = 10.1pp)", () => {
    // 0.91 - 0.809 = 0.101 = 1010 bps > 1000 bps threshold, should fire
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.91, 0.809),
      },
    });
    const insight = findEvalInsight(generateInsights(data));
    expect(insight).toBeDefined();
  });

  it("should NOT fire for drop just barely at boundary due to floating point (0.9 - 0.8 = 0.1)", () => {
    // 0.9 - 0.8 = 0.09999999999999998 due to IEEE 754
    // This is < 0.1, so it should NOT fire (even though mathematically = 0.1)
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.9, 0.8),
      },
    });
    const insight = findEvalInsight(generateInsights(data));
    // Due to floating point, 0.9 - 0.8 = 0.09999... which is < 0.1
    // The code uses `> 0.1` (strict), so this should NOT fire
    expect(insight).toBeUndefined();
  });

  // ── 10. Edge: very small non-zero values ──

  it("should fire for drop from 0.15 to 0.01 (14pp drop)", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.15, 0.01),
      },
    });
    const insight = findEvalInsight(generateInsights(data));
    expect(insight).toBeDefined();
    expect(insight!.description).toContain("14pp");
  });
});

// ==========================================================================
// computeScore + generateInsights consistency
// ==========================================================================

describe("eval regression - score and insight consistency", () => {
  it("score deduction and insight should both fire for >10pp drop", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.90, 0.70),
      },
    });
    const deduction = findEvalDeduction(data);
    const insight = findEvalInsight(generateInsights(data));
    expect(deduction).toBeDefined();
    expect(insight).toBeDefined();
  });

  it("neither score deduction nor insight should fire for <10pp drop", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.85, 0.80),
      },
    });
    const deduction = findEvalDeduction(data);
    const insight = findEvalInsight(generateInsights(data));
    expect(deduction).toBeUndefined();
    expect(insight).toBeUndefined();
  });

  it("neither should fire for exact 10pp drop", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.90, 0.80),
      },
    });
    const deduction = findEvalDeduction(data);
    const insight = findEvalInsight(generateInsights(data));
    expect(deduction).toBeUndefined();
    expect(insight).toBeUndefined();
  });

  it("neither should fire for improvement", () => {
    const data = makeData({
      evalStats: {
        total_runs: 10,
        pass_rate_trend: twoPointTrend(0.60, 0.90),
      },
    });
    const deduction = findEvalDeduction(data);
    const insight = findEvalInsight(generateInsights(data));
    expect(deduction).toBeUndefined();
    expect(insight).toBeUndefined();
  });

  it("neither should fire for null evalStats", () => {
    const data = makeData({ evalStats: null });
    const deduction = findEvalDeduction(data);
    const insight = findEvalInsight(generateInsights(data));
    expect(deduction).toBeUndefined();
    expect(insight).toBeUndefined();
  });
});
