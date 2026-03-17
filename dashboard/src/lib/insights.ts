/**
 * Lighthouse rule engine - pure functions for computing health score and generating insights.
 */

// ── Types ────────────────────────────────────────────────────────────────────

export type Severity = "critical" | "warning" | "optimize" | "discover";

export interface Insight {
  id: string;
  severity: Severity;
  title: string;
  description: string;
  link: string;
  icon: string; // lucide icon name
}

export interface Deduction {
  category: "health" | "operations" | "quality" | "adoption";
  label: string;
  points: number;
}

export interface ScoreResult {
  score: number;
  deductions: Deduction[];
}

export interface AdvisorData {
  health: { status: string; runtime: boolean; redis: unknown; database: boolean } | null;
  stats: { total_runs_today: number; success_rate: number; total_cost_today: number } | null;
  runs: { status: string }[];
  tools: { name: string; configured: boolean; connections: unknown[] }[];
  dlq: { id: string; resolved_at: string | null }[];
  violationStats: { total_violations_30d: number; violations_by_severity: Record<string, number> } | null;
  optimizerStats: { total_decisions_30d: number; estimated_savings_30d_usd: number } | null;
  autopilotStats: { total_experiments: number; active_experiments: number } | null;
  approvals: { id: string; status: string }[];
  workflows: { name: string }[];
  schedules: { id: string; enabled: boolean }[];
  evalStats: {
    total_runs: number;
    avg_pass_rate?: number;
    pass_rate_trend?: { date: string; avg_pass_rate: number; runs: number }[];
  } | null;
  apiKeys: { id: string; expires_at?: string | null }[];
}

// ── Score computation ────────────────────────────────────────────────────────

export function computeScore(data: AdvisorData): ScoreResult {
  const deductions: Deduction[] = [];
  let score = 100;

  function deduct(category: Deduction["category"], label: string, points: number) {
    if (points <= 0) return;
    deductions.push({ category, label, points });
    score -= points;
  }

  // Health: services unhealthy (-15 each)
  if (data.health) {
    if (!data.health.runtime) deduct("health", "Runtime unhealthy", 15);
    if (data.health.database === false) deduct("health", "Database unhealthy", 15);
    if (data.health.redis === false) deduct("health", "Redis unhealthy", 15);
  }

  // Operations: DLQ items (-10 each, max -30)
  const unresolvedDlq = data.dlq.filter((d) => !d.resolved_at);
  if (unresolvedDlq.length > 0) {
    deduct("operations", "Dead-letter queue items", Math.min(unresolvedDlq.length * 10, 30));
  }

  // Quality: critical violations (-10 each, max -30)
  const criticalViolations = data.violationStats?.violations_by_severity?.critical ?? 0;
  if (criticalViolations > 0) {
    deduct("quality", "Critical policy violations", Math.min(criticalViolations * 10, 30));
  }

  // Operations: high failure rate (-15)
  if (data.stats && data.stats.success_rate < 0.7) {
    deduct("operations", "High failure rate", 15);
  }

  // Adoption: unconfigured tools with connections (-5 each)
  const unconfiguredTools = data.tools.filter((t) => !t.configured && t.connections.length > 0);
  if (unconfiguredTools.length > 0) {
    deduct("adoption", "Unconfigured integrations", unconfiguredTools.length * 5);
  }

  // Operations: pending approvals > 5 (-5)
  const pendingApprovals = data.approvals.filter((a) => a.status === "pending");
  if (pendingApprovals.length > 5) {
    deduct("operations", "Approval backlog", 5);
  }

  // Quality: eval regression (-10)
  if (data.evalStats?.pass_rate_trend && data.evalStats.pass_rate_trend.length >= 2) {
    const trend = data.evalStats.pass_rate_trend;
    // avg_pass_rate is 0..1 from backend (raw from DB)
    const latest = trend[trend.length - 1].avg_pass_rate;
    const previous = trend[trend.length - 2].avg_pass_rate;
    // Compare in integer basis points to eliminate IEEE 754 edge cases
    const dropBps = Math.round((previous - latest) * 10000);
    if (dropBps > 1000) deduct("quality", "Eval quality regression", 10);
  }

  // Adoption: missing features (-3 each)
  if (data.schedules.length === 0) deduct("adoption", "No schedules configured", 3);
  if (!data.evalStats || data.evalStats.total_runs === 0) deduct("adoption", "Evaluations not used", 3);
  if (!data.autopilotStats || data.autopilotStats.total_experiments === 0) deduct("adoption", "Autopilot not used", 3);
  if (!data.optimizerStats || data.optimizerStats.total_decisions_30d === 0) deduct("adoption", "Optimizer not used", 3);

  return { score: Math.max(0, score), deductions };
}

// ── Insight generation ───────────────────────────────────────────────────────

export function generateInsights(data: AdvisorData): Insight[] {
  const insights: Insight[] = [];

  // ── Critical ──

  if (data.health && !data.health.runtime) {
    insights.push({
      id: "health-runtime",
      severity: "critical",
      title: "Runtime is down",
      description: "The sandbox runtime is unreachable. Workflows cannot execute.",
      link: "/system",
      icon: "ServerCrash",
    });
  }

  if (data.health && data.health.database === false) {
    insights.push({
      id: "health-database",
      severity: "critical",
      title: "Database unreachable",
      description: "Cannot connect to the database. All operations are impacted.",
      link: "/system",
      icon: "Database",
    });
  }

  const unresolvedDlq = data.dlq.filter((d) => !d.resolved_at);
  if (unresolvedDlq.length > 0) {
    insights.push({
      id: "dlq-items",
      severity: "critical",
      title: `${unresolvedDlq.length} dead-letter item${unresolvedDlq.length > 1 ? "s" : ""}`,
      description: "Failed steps waiting for manual review. Retry or discard them to clear the queue.",
      link: "/dead-letter",
      icon: "Inbox",
    });
  }

  if (criticalViolationCount(data) > 0) {
    const count = criticalViolationCount(data);
    insights.push({
      id: "violations-critical",
      severity: "critical",
      title: `${count} critical policy violation${count > 1 ? "s" : ""}`,
      description: "Critical violations require immediate attention - PII leaks or security issues detected.",
      link: "/violations",
      icon: "ShieldAlert",
    });
  }

  if (data.stats && data.stats.success_rate < 0.5) {
    insights.push({
      id: "success-rate-critical",
      severity: "critical",
      title: "Success rate below 50%",
      description: `Only ${Math.round(data.stats.success_rate * 100)}% of runs succeed. Check failing workflows.`,
      link: "/runs",
      icon: "TrendingDown",
    });
  }

  // ── Warning ──

  const unconfiguredWithConnections = data.tools.filter((t) => !t.configured && t.connections.length > 0);
  if (unconfiguredWithConnections.length > 0) {
    insights.push({
      id: "tools-missing-creds",
      severity: "warning",
      title: `${unconfiguredWithConnections.length} integration${unconfiguredWithConnections.length > 1 ? "s" : ""} missing credentials`,
      description: `${unconfiguredWithConnections.map((t) => t.name).join(", ")} have connections but missing API keys.`,
      link: "/integrations",
      icon: "KeyRound",
    });
  }

  if (data.stats && data.stats.success_rate >= 0.5 && data.stats.success_rate < 0.8) {
    insights.push({
      id: "success-rate-warning",
      severity: "warning",
      title: "Elevated failure rate",
      description: `${Math.round(data.stats.success_rate * 100)}% success rate - investigate recent failures.`,
      link: "/runs?status=failed",
      icon: "AlertTriangle",
    });
  }

  const pendingApprovals = data.approvals.filter((a) => a.status === "pending");
  if (pendingApprovals.length > 0) {
    insights.push({
      id: "pending-approvals",
      severity: "warning",
      title: `${pendingApprovals.length} pending approval${pendingApprovals.length > 1 ? "s" : ""}`,
      description: "Workflows are paused waiting for human review.",
      link: "/approvals",
      icon: "UserCheck",
    });
  }

  // Expiring API keys (within 7 days)
  const now = Date.now();
  const expiringKeys = data.apiKeys.filter((k) => {
    if (!k.expires_at) return false;
    const exp = new Date(k.expires_at).getTime();
    return exp > now && exp - now < 7 * 24 * 60 * 60 * 1000;
  });
  if (expiringKeys.length > 0) {
    insights.push({
      id: "expiring-keys",
      severity: "warning",
      title: `${expiringKeys.length} API key${expiringKeys.length > 1 ? "s" : ""} expiring soon`,
      description: "Rotate keys before they expire to avoid service interruption.",
      link: "/settings",
      icon: "Clock",
    });
  }

  // High total violations
  if (data.violationStats && data.violationStats.total_violations_30d > 15) {
    const highCount = data.violationStats.violations_by_severity?.high ?? 0;
    if (highCount > 5) {
      insights.push({
        id: "violations-high",
        severity: "warning",
        title: `${highCount} high-severity violations this month`,
        description: "Review policy rules - frequent violations may indicate misconfigured policies.",
        link: "/violations",
        icon: "Shield",
      });
    }
  }

  // Eval quality regression
  if (data.evalStats?.pass_rate_trend && data.evalStats.pass_rate_trend.length >= 2) {
    const trend = data.evalStats.pass_rate_trend;
    // avg_pass_rate is 0..1 from backend (raw from DB)
    const latestPr = trend[trend.length - 1].avg_pass_rate;
    const previousPr = trend[trend.length - 2].avg_pass_rate;
    // Compare in integer basis points to eliminate IEEE 754 edge cases
    const dropBps = Math.round((previousPr - latestPr) * 10000);
    if (dropBps > 1000) {  // >10pp drop (>1000 basis points)
      insights.push({
        id: "eval-regression",
        severity: "warning",
        title: "Eval quality regression detected",
        description: `Pass rate dropped ${Math.round(dropBps / 100)}pp (${Math.round(previousPr * 100)}% -> ${Math.round(latestPr * 100)}%). Review recent workflow changes.`,
        link: "/evaluations",
        icon: "TrendingDown",
      });
    }
  }

  // ── Optimize ──

  if (data.optimizerStats && data.optimizerStats.estimated_savings_30d_usd > 1) {
    insights.push({
      id: "optimizer-savings",
      severity: "optimize",
      title: `$${data.optimizerStats.estimated_savings_30d_usd.toFixed(2)} potential savings`,
      description: "The optimizer has identified cost-saving opportunities across your workflows.",
      link: "/optimizer",
      icon: "Coins",
    });
  }

  if (data.autopilotStats && data.autopilotStats.active_experiments > 0) {
    insights.push({
      id: "autopilot-active",
      severity: "optimize",
      title: `${data.autopilotStats.active_experiments} autopilot experiment${data.autopilotStats.active_experiments > 1 ? "s" : ""} running`,
      description: "Autopilot is actively testing model variants to improve quality.",
      link: "/autopilot",
      icon: "Sparkles",
    });
  }

  if (data.stats && data.stats.total_cost_today > 10) {
    insights.push({
      id: "high-daily-spend",
      severity: "optimize",
      title: `$${data.stats.total_cost_today.toFixed(2)} spent today`,
      description: "Daily spend is elevated. Consider enabling the optimizer or adjusting models.",
      link: "/optimizer",
      icon: "DollarSign",
    });
  }

  // ── Discover ──

  if (data.schedules.length > 0) {
    const disabled = data.schedules.filter((s) => !s.enabled);
    if (disabled.length > 0) {
      insights.push({
        id: "disabled-schedules",
        severity: "discover",
        title: `${disabled.length} disabled schedule${disabled.length > 1 ? "s" : ""}`,
        description: "You have schedules that are turned off. Enable or remove them.",
        link: "/schedules",
        icon: "CalendarOff",
      });
    }
  } else {
    insights.push({
      id: "discover-schedules",
      severity: "discover",
      title: "Automate with schedules",
      description: "Run workflows on a cron schedule - no manual triggers needed.",
      link: "/schedules",
      icon: "Calendar",
    });
  }

  if (!data.evalStats || data.evalStats.total_runs === 0) {
    insights.push({
      id: "discover-evals",
      severity: "discover",
      title: "Try evaluations",
      description: "Benchmark your workflows with structured test suites to ensure quality.",
      link: "/evaluations",
      icon: "FlaskConical",
    });
  }

  if (!data.autopilotStats || data.autopilotStats.total_experiments === 0) {
    insights.push({
      id: "discover-autopilot",
      severity: "discover",
      title: "Enable autopilot",
      description: "Let autopilot A/B test model variants and auto-deploy the best performer.",
      link: "/autopilot",
      icon: "Wand2",
    });
  }

  if (!data.optimizerStats || data.optimizerStats.total_decisions_30d === 0) {
    insights.push({
      id: "discover-optimizer",
      severity: "discover",
      title: "Enable the optimizer",
      description: "Automatically select the best model for each step based on quality and cost SLOs.",
      link: "/optimizer",
      icon: "Gauge",
    });
  }

  return insights;
}

// ── Dismiss helpers ──────────────────────────────────────────────────────────

const DISMISS_KEY = "sandcastle_lighthouse_dismissed";

export function getDismissedIds(): Set<string> {
  try {
    const raw = localStorage.getItem(DISMISS_KEY);
    if (!raw) return new Set();
    return new Set(JSON.parse(raw) as string[]);
  } catch {
    return new Set();
  }
}

export function dismissInsight(id: string): void {
  const dismissed = getDismissedIds();
  dismissed.add(id);
  localStorage.setItem(DISMISS_KEY, JSON.stringify([...dismissed]));
}

export function filterDismissed(insights: Insight[]): Insight[] {
  const dismissed = getDismissedIds();
  return insights.filter((i) => !dismissed.has(i.id));
}

// ── Shared UI utilities ──────────────────────────────────────────────────────

export const SEVERITY_ORDER: Severity[] = ["critical", "warning", "optimize", "discover"];

export const SEVERITY_LABELS: Record<Severity, string> = {
  critical: "Critical",
  warning: "Warning",
  optimize: "Optimize",
  discover: "Discover",
};

export function groupBySeverity(insights: Insight[]): [Severity, Insight[]][] {
  const groups: [Severity, Insight[]][] = [];
  for (const sev of SEVERITY_ORDER) {
    const items = insights.filter((i) => i.severity === sev);
    if (items.length > 0) groups.push([sev, items]);
  }
  return groups;
}

export function scoreLabel(score: number): string {
  if (score >= 80) return "Healthy";
  if (score >= 50) return "Needs Attention";
  return "Critical";
}

export function scoreLabelColor(score: number): string {
  if (score >= 80) return "text-emerald-500";
  if (score >= 50) return "text-amber-500";
  return "text-error";
}

export const DEDUCTION_CATEGORY_COLORS: Record<Deduction["category"], string> = {
  health: "bg-error",
  operations: "bg-amber-500",
  quality: "bg-violet-500",
  adoption: "bg-muted-foreground",
};

export const DEDUCTION_CATEGORY_LABELS: Record<Deduction["category"], string> = {
  health: "Health",
  operations: "Operations",
  quality: "Quality",
  adoption: "Adoption",
};

// ── Helpers ──────────────────────────────────────────────────────────────────

function criticalViolationCount(data: AdvisorData): number {
  return data.violationStats?.violations_by_severity?.critical ?? 0;
}
