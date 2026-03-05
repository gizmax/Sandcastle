import { parseUTC } from "@/lib/utils";

export interface Anomaly {
  type: "cost_spike" | "slow_run" | "error_streak" | "retry_heavy";
  severity: "warning" | "critical";
  message: string;
  value: number;
  threshold: number;
}

interface RunLike {
  run_id: string;
  workflow_name: string;
  status: string;
  total_cost_usd: number;
  started_at: string | null;
  completed_at: string | null;
}

interface StepLike {
  attempt: number;
}

const MIN_RUNS_FOR_STATS = 3;

function getDurationSeconds(run: RunLike): number | null {
  if (!run.started_at) return null;
  const start = parseUTC(run.started_at).getTime();
  const end = run.completed_at ? parseUTC(run.completed_at).getTime() : Date.now();
  return (end - start) / 1000;
}

function getSiblingRuns(run: RunLike, allRuns: RunLike[]): RunLike[] {
  return allRuns.filter(
    (r) => r.workflow_name === run.workflow_name && r.run_id !== run.run_id
  );
}

function detectCostSpike(run: RunLike, siblings: RunLike[]): Anomaly | null {
  const costs = siblings
    .map((r) => r.total_cost_usd)
    .filter((c) => c > 0);
  if (costs.length < MIN_RUNS_FOR_STATS) return null;
  if (run.total_cost_usd <= 0) return null;

  const avg = costs.reduce((a, b) => a + b, 0) / costs.length;
  if (avg <= 0) return null;

  const ratio = run.total_cost_usd / avg;
  if (ratio >= 5) {
    return {
      type: "cost_spike",
      severity: "critical",
      message: `Cost is ${ratio.toFixed(1)}x the average ($${run.total_cost_usd.toFixed(4)} vs $${avg.toFixed(4)})`,
      value: run.total_cost_usd,
      threshold: avg * 5,
    };
  }
  if (ratio >= 2) {
    return {
      type: "cost_spike",
      severity: "warning",
      message: `Cost is ${ratio.toFixed(1)}x the average ($${run.total_cost_usd.toFixed(4)} vs $${avg.toFixed(4)})`,
      value: run.total_cost_usd,
      threshold: avg * 2,
    };
  }
  return null;
}

function detectSlowRun(run: RunLike, siblings: RunLike[]): Anomaly | null {
  const duration = getDurationSeconds(run);
  if (duration === null || duration <= 0) return null;

  const durations = siblings
    .map((r) => getDurationSeconds(r))
    .filter((d): d is number => d !== null && d > 0);
  if (durations.length < MIN_RUNS_FOR_STATS) return null;

  const avg = durations.reduce((a, b) => a + b, 0) / durations.length;
  if (avg <= 0) return null;

  const ratio = duration / avg;
  if (ratio >= 5) {
    return {
      type: "slow_run",
      severity: "critical",
      message: `Duration is ${ratio.toFixed(1)}x the average (${Math.round(duration)}s vs ${Math.round(avg)}s)`,
      value: duration,
      threshold: avg * 5,
    };
  }
  if (ratio >= 2) {
    return {
      type: "slow_run",
      severity: "warning",
      message: `Duration is ${ratio.toFixed(1)}x the average (${Math.round(duration)}s vs ${Math.round(avg)}s)`,
      value: duration,
      threshold: avg * 2,
    };
  }
  return null;
}

function detectErrorStreak(run: RunLike, allRuns: RunLike[]): Anomaly | null {
  if (run.status !== "failed") return null;

  const workflowRuns = allRuns
    .filter((r) => r.workflow_name === run.workflow_name)
    .sort((a, b) => {
      const ta = a.started_at ? parseUTC(a.started_at).getTime() : 0;
      const tb = b.started_at ? parseUTC(b.started_at).getTime() : 0;
      return tb - ta;
    });

  const runIndex = workflowRuns.findIndex((r) => r.run_id === run.run_id);
  if (runIndex === -1) return null;

  let streak = 0;
  for (let i = runIndex; i < workflowRuns.length; i++) {
    if (workflowRuns[i].status === "failed") {
      streak++;
    } else {
      break;
    }
  }

  if (streak >= 5) {
    return {
      type: "error_streak",
      severity: "critical",
      message: `${streak} consecutive failures for this workflow`,
      value: streak,
      threshold: 5,
    };
  }
  if (streak >= 3) {
    return {
      type: "error_streak",
      severity: "warning",
      message: `${streak} consecutive failures for this workflow`,
      value: streak,
      threshold: 3,
    };
  }
  return null;
}

export function detectRetryHeavy(steps: StepLike[]): Anomaly | null {
  const maxAttempt = Math.max(0, ...steps.map((s) => s.attempt));
  if (maxAttempt >= 3) {
    return {
      type: "retry_heavy",
      severity: "warning",
      message: `Step retried ${maxAttempt} times`,
      value: maxAttempt,
      threshold: 3,
    };
  }
  return null;
}

export function detectAnomalies(run: RunLike, allRuns: RunLike[]): Anomaly[] {
  const anomalies: Anomaly[] = [];
  const siblings = getSiblingRuns(run, allRuns);

  const costSpike = detectCostSpike(run, siblings);
  if (costSpike) anomalies.push(costSpike);

  const slowRun = detectSlowRun(run, siblings);
  if (slowRun) anomalies.push(slowRun);

  const errorStreak = detectErrorStreak(run, allRuns);
  if (errorStreak) anomalies.push(errorStreak);

  return anomalies;
}
