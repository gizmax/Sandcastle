import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Link } from "react-router-dom";
import { ExternalLink } from "lucide-react";
import { api } from "@/api/client";
import { useAdvisorContext } from "@/hooks/useAdvisorContext";
import { RunStatusBadge } from "@/components/runs/RunStatusBadge";
import { Skeleton } from "@/components/ui/Skeleton";
import { cn, formatCost, formatRelativeTime } from "@/lib/utils";
import type { Insight, Severity } from "@/lib/insights";
import { LayoutSwitcher } from "@/pages/OverviewBento";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Stats {
  total_runs_today: number;
  success_rate: number;
  total_cost_today: number;
  avg_duration_seconds: number;
  runs_by_day: Array<{ date: string; completed: number; failed: number; total: number }>;
  cost_by_workflow: Array<{ workflow: string; cost: number }>;
}

interface RunItem {
  run_id: string;
  workflow_name: string;
  status: string;
  total_cost_usd: number;
  started_at: string | null;
}

interface SparklineData {
  values: number[];
  trendPercent: number;
}

// ---------------------------------------------------------------------------
// Mock sparklines (same deterministic seed)
// ---------------------------------------------------------------------------

function seededRandom(seed: number): () => number {
  let s = seed | 0;
  return () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function generateMockSparklines(): Record<string, SparklineData> {
  const today = new Date();
  const seed = today.getFullYear() * 10000 + (today.getMonth() + 1) * 100 + today.getDate();
  const rng = seededRandom(seed);
  const mkSeries = (base: number, variance: number): number[] =>
    Array.from({ length: 7 }, () => Math.max(0, base + (rng() - 0.5) * 2 * variance));

  const runsValues = mkSeries(24, 12);
  const rateValues = mkSeries(0.92, 0.08);
  const costValues = mkSeries(1.5, 0.8);

  const trendPct = (vals: number[]) => {
    const yesterday = vals[vals.length - 2];
    const todayVal = vals[vals.length - 1];
    if (yesterday === 0) return 0;
    return ((todayVal - yesterday) / yesterday) * 100;
  };

  return {
    runs: { values: runsValues, trendPercent: trendPct(runsValues) },
    rate: { values: rateValues, trendPercent: trendPct(rateValues) },
    cost: { values: costValues, trendPercent: trendPct(costValues) },
  };
}

// ---------------------------------------------------------------------------
// Greeting helper
// ---------------------------------------------------------------------------

function getGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning.";
  if (hour < 17) return "Good afternoon.";
  return "Good evening.";
}

// ---------------------------------------------------------------------------
// Insight dot colors
// ---------------------------------------------------------------------------

const SEVERITY_DOT: Record<Severity, string> = {
  critical: "bg-rose-500",
  warning: "bg-amber-400",
  optimize: "bg-teal-500",
  discover: "bg-violet-400",
};

// ---------------------------------------------------------------------------
// Stat pill
// ---------------------------------------------------------------------------

interface StatPillProps {
  label: string;
  value: string;
  trend?: number;
  positiveIsGood?: boolean;
  separator?: boolean;
}

function StatPill({ label, value, trend, positiveIsGood = true, separator = false }: StatPillProps) {
  const showTrend = trend !== undefined && Number.isFinite(trend) && Math.abs(trend) >= 0.5;
  const isUp = (trend ?? 0) > 0;
  const isGood = showTrend ? (positiveIsGood ? isUp : !isUp) : null;

  return (
    <div className={cn("flex items-center gap-2", separator && "pl-4 border-l border-stone-200")}>
      <span className="text-sm font-semibold text-stone-800">{value}</span>
      {showTrend && (
        <span className={cn(
          "text-xs font-medium",
          isGood ? "text-emerald-600" : "text-rose-500"
        )}>
          {isUp ? "+" : ""}{Math.abs(trend ?? 0).toFixed(0)}%
        </span>
      )}
      <span className="text-sm text-stone-400">{label}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Cost progress bar
// ---------------------------------------------------------------------------

interface CostBarProps {
  spent: number;
  projected: number;
}

function CostProgressBar({ spent, projected }: CostBarProps) {
  const pct = projected > 0 ? Math.min((spent / projected) * 100, 100) : 0;
  const isOverHalf = pct > 50;
  const isNearBudget = pct > 80;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs text-stone-500">
        <span className="font-medium text-stone-700">{formatCost(spent)} spent</span>
        <span>{formatCost(projected)} projected</span>
      </div>
      <div className="h-1.5 bg-stone-100 rounded-full overflow-hidden">
        <div
          className={cn(
            "h-full rounded-full transition-all duration-700",
            isNearBudget ? "bg-amber-400" : isOverHalf ? "bg-teal-500" : "bg-teal-400"
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="flex items-center justify-between text-[11px] text-stone-400">
        <span>This month</span>
        <span>{pct.toFixed(0)}% of projected</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Minimal runs table
// ---------------------------------------------------------------------------

function MinimalRunsTable({ runs }: { runs: RunItem[] }) {
  const navigate = useNavigate();

  if (runs.length === 0) return null;

  return (
    <div className="space-y-0">
      {runs.slice(0, 6).map((run, i) => (
        <button
          key={run.run_id}
          onClick={() => navigate(`/runs/${run.run_id}`)}
          className={cn(
            "flex w-full items-center gap-3 py-3 text-left",
            "transition-colors duration-100 hover:bg-stone-50/80 -mx-2 px-2 rounded-lg",
            i < runs.length - 1 && "border-b border-stone-100"
          )}
        >
          <RunStatusBadge status={run.status} />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-stone-700">{run.workflow_name}</p>
          </div>
          <span className="text-xs text-stone-400 shrink-0 font-mono">
            {formatCost(run.total_cost_usd)}
          </span>
          <span className="text-xs text-stone-400 shrink-0">
            {run.started_at ? formatRelativeTime(run.started_at) : "queued"}
          </span>
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section label
// ---------------------------------------------------------------------------

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[11px] font-semibold uppercase tracking-wider text-stone-400 mb-3">
      {children}
    </p>
  );
}

// ---------------------------------------------------------------------------
// OverviewFocus page
// ---------------------------------------------------------------------------

export default function OverviewFocus({
  layout, setLayout,
}: {
  layout: string;
  setLayout: (l: string) => void;
}) {
  const [stats, setStats] = useState<Stats | null>(null);
  const [recentRuns, setRecentRuns] = useState<RunItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const advisor = useAdvisorContext();
  const mockSparklines = useMemo(() => generateMockSparklines(), []);

  useEffect(() => {
    let cancelled = false;
    const fetchData = async () => {
      try {
        setError(null);
        const [statsRes, runsRes] = await Promise.all([
          api.get<Stats>("/stats"),
          api.get<RunItem[]>("/runs", { limit: "6" }),
        ]);
        if (cancelled) return;
        if (statsRes.data) setStats(statsRes.data);
        if (runsRes.data) setRecentRuns(runsRes.data);
      } catch {
        if (cancelled) return;
        setError("Could not connect to the API server");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void fetchData();
    return () => { cancelled = true; };
  }, [retryCount]);

  const pageClass = "min-h-full bg-gradient-to-br from-slate-50 to-stone-100 -m-4 sm:-m-6 p-4 sm:p-6";

  if (loading) {
    return (
      <div className={pageClass}>
        <div className="max-w-2xl mx-auto space-y-6 pt-4">
          <div className="flex items-center justify-between">
            <Skeleton className="h-9 w-48 rounded-xl" />
            <Skeleton className="h-8 w-28 rounded-lg" />
          </div>
          <Skeleton className="h-7 w-72 rounded-lg mx-auto" />
          <Skeleton className="h-6 w-64 rounded-lg mx-auto" />
          <Skeleton className="h-4 w-full rounded-full" />
          <div className="space-y-2">
            {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-12 rounded-lg" />)}
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className={pageClass}>
        <div className="max-w-2xl mx-auto pt-8">
          <div className="rounded-2xl border border-rose-200 bg-rose-50 p-5">
            <p className="text-sm text-rose-600">{error}</p>
            <button
              onClick={() => { setLoading(true); setRetryCount((c) => c + 1); }}
              className="mt-2 text-xs font-semibold text-teal-600 hover:text-teal-700 transition-colors"
            >
              Retry
            </button>
          </div>
        </div>
      </div>
    );
  }

  const totalRuns = stats?.total_runs_today ?? 0;
  const successRate = stats ? Math.round(stats.success_rate * 100) : 0;
  const totalCost = stats?.total_cost_today ?? 0;

  // Advisor insights - actionable only
  const actionableInsights = advisor.activeInsights.filter((i) => i.severity !== "discover");

  // Projected monthly cost (approximate from avg daily rate * 30)
  const historicalDays = stats?.runs_by_day ?? [];
  const avgDailyCost = historicalDays.length > 0
    ? historicalDays.reduce((s, _) => {
        // We don't have per-day cost in runs_by_day, so derive from total_cost_today
        return s;
      }, totalCost) / Math.max(historicalDays.length, 1)
    : totalCost;
  const projectedMonthly = avgDailyCost * 30;

  return (
    <div className={pageClass}>
      <div className="max-w-2xl mx-auto">
        {/* Header with switcher */}
        <div className="flex items-center justify-between mb-8">
          <div className="w-8" /> {/* spacer */}
          <div className="text-center">
            {/* Intentionally empty - greeting below is the "header" */}
          </div>
          <LayoutSwitcher layout={layout} setLayout={setLayout} />
        </div>

        {/* Greeting + score */}
        <div className="text-center mb-6">
          <p className="text-2xl font-bold text-stone-800 mb-1">
            {getGreeting()}{" "}
            {stats && (
              <span className="text-stone-500 font-normal">
                {totalRuns} workflow{totalRuns !== 1 ? "s" : ""} ran today.
              </span>
            )}
          </p>
          {!advisor.loading && (
            <div className="flex items-center justify-center gap-2 mt-2">
              <span className="text-4xl font-bold text-stone-800">{advisor.score}</span>
              <span className={cn(
                "text-sm font-semibold",
                advisor.score >= 80 ? "text-emerald-600" : advisor.score >= 50 ? "text-amber-500" : "text-rose-500"
              )}>
                {advisor.score >= 80 ? "healthy" : advisor.score >= 50 ? "needs attention" : "critical"}
              </span>
            </div>
          )}
        </div>

        {/* Stat pills - centered horizontal */}
        {stats && (
          <div className="flex items-center justify-center gap-0 mb-8 flex-wrap gap-y-2">
            <StatPill
              value={String(totalRuns)}
              label="runs"
              trend={mockSparklines.runs?.trendPercent}
              positiveIsGood={true}
            />
            <StatPill
              value={`${successRate}%`}
              label="success"
              trend={mockSparklines.rate?.trendPercent}
              positiveIsGood={true}
              separator={true}
            />
            <StatPill
              value={formatCost(totalCost)}
              label="cost"
              trend={mockSparklines.cost?.trendPercent}
              positiveIsGood={false}
              separator={true}
            />
          </div>
        )}

        {/* Insights as plain text list */}
        {actionableInsights.length > 0 && (
          <div className="mb-8">
            <SectionLabel>Needs attention</SectionLabel>
            <div className="space-y-2">
              {actionableInsights.slice(0, 6).map((insight: Insight) => (
                <Link
                  key={insight.id}
                  to={insight.link}
                  className={cn(
                    "flex items-start gap-3 py-2 group",
                    "hover:bg-stone-100/60 -mx-3 px-3 rounded-xl transition-colors duration-150"
                  )}
                >
                  <span className={cn("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", SEVERITY_DOT[insight.severity])} />
                  <span className="flex-1 text-sm text-stone-600 group-hover:text-stone-800 transition-colors">
                    {insight.title}
                  </span>
                  <ExternalLink className="h-3 w-3 mt-1 text-stone-300 group-hover:text-stone-400 shrink-0 transition-colors" />
                </Link>
              ))}
              {actionableInsights.length > 6 && (
                <p className="text-xs text-stone-400 pl-4">+{actionableInsights.length - 6} more items</p>
              )}
            </div>
          </div>
        )}

        {/* Cost progress bar */}
        {stats && projectedMonthly > 0 && (
          <div className="mb-8">
            <SectionLabel>Monthly cost</SectionLabel>
            <CostProgressBar spent={totalCost} projected={projectedMonthly} />
          </div>
        )}

        {/* Recent runs - minimal table */}
        {recentRuns.length > 0 && (
          <div className="mb-6">
            <div className="flex items-center justify-between mb-1">
              <SectionLabel>Recent runs</SectionLabel>
              <Link
                to="/runs"
                className="text-xs text-teal-600 hover:text-teal-700 font-medium mb-3 inline-block transition-colors"
              >
                View all
              </Link>
            </div>
            <MinimalRunsTable runs={recentRuns} />
          </div>
        )}
      </div>
    </div>
  );
}
