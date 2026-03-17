/**
 * Stress tests for eval regression detection: basis-point boundary logic.
 *
 * The code uses:
 *   dropBps = Math.round((previous - latest) * 10000)
 *   fires when dropBps > 1000  (strict, NOT >=)
 *
 * This means:
 *   10.00pp  -> 1000 bps -> NOT fire
 *   10.01pp  -> 1001 bps -> SHOULD fire
 *    9.99pp  ->  999 bps -> NOT fire
 *
 * IEEE 754 traps are handled by the Math.round on bps:
 *   0.9 - 0.8  = 0.09999999999999998 -> * 10000 = 999.999... -> round = 1000 -> NOT fire
 *   0.8 - 0.7  = 0.09999999999999998 -> * 10000 = 999.999... -> round = 1000 -> NOT fire
 *   0.7 - 0.6  = 0.09999999999999998 -> * 10000 = 999.999... -> round = 1000 -> NOT fire
 *   0.91 - 0.809 = 0.10100000000000001 -> * 10000 = 1010.0... -> round = 1010 -> SHOULD fire
 *   0.3 - 0.199  = 0.10100000000000001 -> * 10000 = 1010.0... -> round = 1010 -> SHOULD fire
 */

import { describe, it, expect } from "vitest";
import {
  computeScore,
  generateInsights,
  type AdvisorData,
  type Insight,
} from "@/lib/insights";

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Minimal healthy AdvisorData. Only evalStats is interesting here. */
function makeData(previous: number, latest: number): AdvisorData {
  return {
    health: { status: "ok", runtime: true, redis: true, database: true },
    stats: { total_runs_today: 5, success_rate: 0.95, total_cost_today: 1.0 },
    runs: [],
    tools: [],
    dlq: [],
    violationStats: { total_violations_30d: 0, violations_by_severity: {} },
    optimizerStats: { total_decisions_30d: 5, estimated_savings_30d_usd: 0 },
    autopilotStats: { total_experiments: 1, active_experiments: 0 },
    approvals: [],
    workflows: [],
    schedules: [{ id: "s1", enabled: true }],
    evalStats: {
      total_runs: 10,
      pass_rate_trend: [
        { date: "2026-03-15", avg_pass_rate: previous, runs: 5 },
        { date: "2026-03-16", avg_pass_rate: latest, runs: 5 },
      ],
    },
    apiKeys: [],
  };
}

/** True when computeScore fires the eval regression deduction. */
function scoreFiresFor(previous: number, latest: number): boolean {
  const { deductions } = computeScore(makeData(previous, latest));
  return deductions.some((d) => d.label === "Eval quality regression");
}

/** True when generateInsights fires the eval-regression insight. */
function insightFiresFor(previous: number, latest: number): boolean {
  const insights = generateInsights(makeData(previous, latest));
  return insights.some((i) => i.id === "eval-regression");
}

/** Find the eval-regression insight object (or undefined). */
function getInsight(previous: number, latest: number): Insight | undefined {
  return generateInsights(makeData(previous, latest)).find(
    (i) => i.id === "eval-regression"
  );
}

/** Compute what dropBps will be for a given pair (mirrors production code). */
function expectedBps(previous: number, latest: number): number {
  return Math.round((previous - latest) * 10000);
}

// ── Section 1: Exact boundaries ──────────────────────────────────────────────

describe("BPS boundary - exact thresholds", () => {
  // 1a. Exactly 10.00pp = 1000 bps - NOT fire (strict >)
  it("1a. 10.00pp (1000 bps) should NOT fire - computeScore", () => {
    // 0.90 - 0.80 = 0.10 exactly (in IEEE 754 this is 0.09999... but Math.round
    // on 999.99... gives 1000, which is NOT > 1000)
    expect(scoreFiresFor(0.90, 0.80)).toBe(false);
  });

  it("1a. 10.00pp (1000 bps) should NOT fire - generateInsights", () => {
    expect(insightFiresFor(0.90, 0.80)).toBe(false);
  });

  // 1b. Exactly 10.01pp = 1001 bps - SHOULD fire
  it("1b. 10.01pp (1001 bps) SHOULD fire - computeScore", () => {
    // 0.9001 - 0.8000 = 0.1001 -> * 10000 = 1001 bps > 1000
    expect(scoreFiresFor(0.9001, 0.8000)).toBe(true);
  });

  it("1b. 10.01pp (1001 bps) SHOULD fire - generateInsights", () => {
    expect(insightFiresFor(0.9001, 0.8000)).toBe(true);
  });

  // 1c. Exactly 9.99pp = 999 bps - NOT fire
  it("1c. 9.99pp (999 bps) should NOT fire - computeScore", () => {
    // 0.8999 - 0.8000 = 0.0999 -> * 10000 = 999 bps, NOT > 1000
    expect(scoreFiresFor(0.8999, 0.8000)).toBe(false);
  });

  it("1c. 9.99pp (999 bps) should NOT fire - generateInsights", () => {
    expect(insightFiresFor(0.8999, 0.8000)).toBe(false);
  });

  // Sanity: verify our expectedBps helper matches the thresholds
  it("expectedBps(0.9001, 0.8000) === 1001", () => {
    expect(expectedBps(0.9001, 0.8000)).toBe(1001);
  });

  it("expectedBps(0.8999, 0.8000) === 999", () => {
    expect(expectedBps(0.8999, 0.8000)).toBe(999);
  });
});

// ── Section 2: IEEE 754 traps ─────────────────────────────────────────────────

describe("BPS boundary - IEEE 754 traps (all exactly 10pp mathematically)", () => {
  // These pairs all equal exactly 0.1 in real math but IEEE 754 gives
  // 0.09999999999999998, meaning raw float comparison > 0.1 would FAIL.
  // The Math.round bps technique rounds them to 1000, which is NOT > 1000 -> NOT fire.

  it("2a. 0.8 - 0.7 = 999 bps -> NOT fire - computeScore", () => {
    // Raw: 0.8 - 0.7 = 0.09999999999999998
    expect(expectedBps(0.8, 0.7)).toBe(1000);
    expect(scoreFiresFor(0.8, 0.7)).toBe(false);
  });

  it("2a. 0.8 - 0.7 = 1000 bps -> NOT fire - generateInsights", () => {
    expect(insightFiresFor(0.8, 0.7)).toBe(false);
  });

  it("2b. 0.9 - 0.8 = 1000 bps -> NOT fire - computeScore", () => {
    expect(expectedBps(0.9, 0.8)).toBe(1000);
    expect(scoreFiresFor(0.9, 0.8)).toBe(false);
  });

  it("2b. 0.9 - 0.8 = 1000 bps -> NOT fire - generateInsights", () => {
    expect(insightFiresFor(0.9, 0.8)).toBe(false);
  });

  it("2c. 0.7 - 0.6 = 1000 bps -> NOT fire - computeScore", () => {
    expect(expectedBps(0.7, 0.6)).toBe(1000);
    expect(scoreFiresFor(0.7, 0.6)).toBe(false);
  });

  it("2c. 0.7 - 0.6 = 1000 bps -> NOT fire - generateInsights", () => {
    expect(insightFiresFor(0.7, 0.6)).toBe(false);
  });

  // These pairs produce just above 10pp -> 1010 bps -> SHOULD fire

  it("2d. 0.91 - 0.809 = 1010 bps -> SHOULD fire - computeScore", () => {
    // 0.91 - 0.809 = 0.10100000000000001 -> * 10000 = 1010.0000000000001 -> round = 1010
    expect(expectedBps(0.91, 0.809)).toBe(1010);
    expect(scoreFiresFor(0.91, 0.809)).toBe(true);
  });

  it("2d. 0.91 - 0.809 = 1010 bps -> SHOULD fire - generateInsights", () => {
    expect(insightFiresFor(0.91, 0.809)).toBe(true);
  });

  it("2e. 0.3 - 0.199 = 1010 bps -> SHOULD fire - computeScore", () => {
    // 0.3 - 0.199 = 0.10100000000000001 -> round(1010.0...) = 1010
    expect(expectedBps(0.3, 0.199)).toBe(1010);
    expect(scoreFiresFor(0.3, 0.199)).toBe(true);
  });

  it("2e. 0.3 - 0.199 = 1010 bps -> SHOULD fire - generateInsights", () => {
    expect(insightFiresFor(0.3, 0.199)).toBe(true);
  });
});

// ── Section 3: Large drops ────────────────────────────────────────────────────

describe("BPS boundary - large drops (SHOULD fire)", () => {
  it("3a. 50pp drop (0.90 -> 0.40) SHOULD fire - computeScore", () => {
    expect(scoreFiresFor(0.90, 0.40)).toBe(true);
  });

  it("3a. 50pp drop (0.90 -> 0.40) SHOULD fire - generateInsights", () => {
    expect(insightFiresFor(0.90, 0.40)).toBe(true);
  });

  it("3b. 90pp drop (0.95 -> 0.05) SHOULD fire - computeScore", () => {
    expect(scoreFiresFor(0.95, 0.05)).toBe(true);
  });

  it("3b. 90pp drop (0.95 -> 0.05) SHOULD fire - generateInsights", () => {
    expect(insightFiresFor(0.95, 0.05)).toBe(true);
  });

  it("3c. 100pp drop (1.0 -> 0.0) SHOULD fire - computeScore", () => {
    expect(scoreFiresFor(1.0, 0.0)).toBe(true);
  });

  it("3c. 100pp drop (1.0 -> 0.0) SHOULD fire - generateInsights", () => {
    expect(insightFiresFor(1.0, 0.0)).toBe(true);
  });
});

// ── Section 4: Small drops ────────────────────────────────────────────────────

describe("BPS boundary - small drops (should NOT fire)", () => {
  it("4a. 1pp drop (0.90 -> 0.89) should NOT fire - computeScore", () => {
    expect(scoreFiresFor(0.90, 0.89)).toBe(false);
  });

  it("4a. 1pp drop (0.90 -> 0.89) should NOT fire - generateInsights", () => {
    expect(insightFiresFor(0.90, 0.89)).toBe(false);
  });

  it("4b. 5pp drop (0.85 -> 0.80) should NOT fire - computeScore", () => {
    expect(scoreFiresFor(0.85, 0.80)).toBe(false);
  });

  it("4b. 5pp drop (0.85 -> 0.80) should NOT fire - generateInsights", () => {
    expect(insightFiresFor(0.85, 0.80)).toBe(false);
  });

  it("4c. 9pp drop (0.90 -> 0.81) should NOT fire - computeScore", () => {
    expect(scoreFiresFor(0.90, 0.81)).toBe(false);
  });

  it("4c. 9pp drop (0.90 -> 0.81) should NOT fire - generateInsights", () => {
    expect(insightFiresFor(0.90, 0.81)).toBe(false);
  });
});

// ── Section 5: No drop / improvement ─────────────────────────────────────────

describe("BPS boundary - no drop or improvement (should NOT fire)", () => {
  it("5a. 0pp drop (0.80 -> 0.80) should NOT fire - computeScore", () => {
    expect(scoreFiresFor(0.80, 0.80)).toBe(false);
  });

  it("5a. 0pp drop (0.80 -> 0.80) should NOT fire - generateInsights", () => {
    expect(insightFiresFor(0.80, 0.80)).toBe(false);
  });

  it("5b. Improvement (0.70 -> 0.90) should NOT fire - computeScore", () => {
    expect(scoreFiresFor(0.70, 0.90)).toBe(false);
  });

  it("5b. Improvement (0.70 -> 0.90) should NOT fire - generateInsights", () => {
    expect(insightFiresFor(0.70, 0.90)).toBe(false);
  });

  it("5c. Large improvement (0.10 -> 0.95) should NOT fire - computeScore", () => {
    expect(scoreFiresFor(0.10, 0.95)).toBe(false);
  });

  it("5c. Large improvement (0.10 -> 0.95) should NOT fire - generateInsights", () => {
    expect(insightFiresFor(0.10, 0.95)).toBe(false);
  });
});

// ── Section 6: Consistency - both functions must agree on every case ──────────

describe("Consistency - computeScore and generateInsights agree on every case", () => {
  const CASES: Array<{ label: string; prev: number; curr: number; shouldFire: boolean }> = [
    // Exact boundaries
    { label: "10.00pp (0.90->0.80)", prev: 0.90, curr: 0.80, shouldFire: false },
    { label: "10.01pp (0.9001->0.80)", prev: 0.9001, curr: 0.80, shouldFire: true },
    { label: "9.99pp (0.8999->0.80)", prev: 0.8999, curr: 0.80, shouldFire: false },
    // IEEE 754 exact-10pp traps
    { label: "IEEE 0.8-0.7", prev: 0.8, curr: 0.7, shouldFire: false },
    { label: "IEEE 0.9-0.8", prev: 0.9, curr: 0.8, shouldFire: false },
    { label: "IEEE 0.7-0.6", prev: 0.7, curr: 0.6, shouldFire: false },
    // IEEE 754 just-over-10pp
    { label: "IEEE 0.91-0.809", prev: 0.91, curr: 0.809, shouldFire: true },
    { label: "IEEE 0.3-0.199", prev: 0.3, curr: 0.199, shouldFire: true },
    // Large drops
    { label: "50pp (0.90->0.40)", prev: 0.90, curr: 0.40, shouldFire: true },
    { label: "90pp (0.95->0.05)", prev: 0.95, curr: 0.05, shouldFire: true },
    { label: "100pp (1.0->0.0)", prev: 1.0, curr: 0.0, shouldFire: true },
    // Small drops
    { label: "1pp (0.90->0.89)", prev: 0.90, curr: 0.89, shouldFire: false },
    { label: "5pp (0.85->0.80)", prev: 0.85, curr: 0.80, shouldFire: false },
    { label: "9pp (0.90->0.81)", prev: 0.90, curr: 0.81, shouldFire: false },
    // No drop / improvement
    { label: "0pp (0.80->0.80)", prev: 0.80, curr: 0.80, shouldFire: false },
    { label: "improvement (0.70->0.90)", prev: 0.70, curr: 0.90, shouldFire: false },
    { label: "large improvement (0.10->0.95)", prev: 0.10, curr: 0.95, shouldFire: false },
  ];

  for (const { label, prev, curr, shouldFire } of CASES) {
    it(`computeScore and generateInsights both ${shouldFire ? "FIRE" : "silent"} for ${label}`, () => {
      const scoreFires = scoreFiresFor(prev, curr);
      const insightFires = insightFiresFor(prev, curr);

      expect(scoreFires).toBe(shouldFire);
      expect(insightFires).toBe(shouldFire);
      // Critical: both functions must agree
      expect(scoreFires).toBe(insightFires);
    });
  }
});

// ── Section 7: Description format when insight fires ─────────────────────────

describe("Description format - pp value shown correctly when insight fires", () => {
  it("7a. 20pp drop (0.90 -> 0.70): description shows '20pp', '90% -> 70%'", () => {
    const insight = getInsight(0.90, 0.70);
    expect(insight).toBeDefined();
    expect(insight!.description).toMatch(/20pp/);
    expect(insight!.description).toMatch(/90%/);
    expect(insight!.description).toMatch(/70%/);
    expect(insight!.description).toMatch(/90% -> 70%/);
  });

  it("7b. 50pp drop (0.90 -> 0.40): description shows '50pp', '90% -> 40%'", () => {
    const insight = getInsight(0.90, 0.40);
    expect(insight).toBeDefined();
    expect(insight!.description).toMatch(/50pp/);
    expect(insight!.description).toMatch(/90% -> 40%/);
  });

  it("7c. 100pp drop (1.0 -> 0.0): description shows '100pp', '100% -> 0%'", () => {
    const insight = getInsight(1.0, 0.0);
    expect(insight).toBeDefined();
    expect(insight!.description).toMatch(/100pp/);
    expect(insight!.description).toMatch(/100% -> 0%/);
  });

  it("7d. 90pp drop (0.95 -> 0.05): description shows '90pp', '95% -> 5%'", () => {
    const insight = getInsight(0.95, 0.05);
    expect(insight).toBeDefined();
    expect(insight!.description).toMatch(/90pp/);
    expect(insight!.description).toMatch(/95% -> 5%/);
  });

  it("7e. 10.01pp drop (0.9001 -> 0.80): description shows '10pp' (Math.round of 10.01)", () => {
    // dropBps = 1001; Math.round(1001/100) = 10pp in description
    const insight = getInsight(0.9001, 0.80);
    expect(insight).toBeDefined();
    // Math.round(1001 / 100) = 10
    expect(insight!.description).toMatch(/10pp/);
  });

  it("7f. IEEE 0.91 - 0.809 = 10.1pp: description shows '10pp' (Math.round(1010/100)=10)", () => {
    // dropBps = 1010; Math.round(1010/100) = 10
    const insight = getInsight(0.91, 0.809);
    expect(insight).toBeDefined();
    expect(insight!.description).toMatch(/10pp/);
  });

  it("7g. 0.3 - 0.199 = 10.1pp: description shows '10pp'", () => {
    const insight = getInsight(0.3, 0.199);
    expect(insight).toBeDefined();
    expect(insight!.description).toMatch(/10pp/);
  });

  it("7h. Description does NOT show raw fractional values (no 9000% or 0.7)", () => {
    const insight = getInsight(0.90, 0.70);
    expect(insight).toBeDefined();
    expect(insight!.description).not.toMatch(/9000%/);
    expect(insight!.description).not.toMatch(/0\.9/);
    expect(insight!.description).not.toMatch(/0\.7[^0-9]/);
  });

  it("7i. Description contains 'Review recent workflow changes'", () => {
    const insight = getInsight(0.90, 0.70);
    expect(insight).toBeDefined();
    expect(insight!.description).toContain("Review recent workflow changes");
  });

  it("7j. Insight has correct metadata: severity=warning, icon=TrendingDown, link=/evaluations", () => {
    const insight = getInsight(0.90, 0.70);
    expect(insight).toBeDefined();
    expect(insight!.severity).toBe("warning");
    expect(insight!.icon).toBe("TrendingDown");
    expect(insight!.link).toBe("/evaluations");
    expect(insight!.title).toBe("Eval quality regression detected");
  });
});
