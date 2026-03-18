import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Link } from "react-router-dom";
import {
  Castle, Play, CheckCircle2, ArrowRight, Activity,
  DollarSign, CheckCircle, AlertTriangle, TrendingUp,
} from "lucide-react";
import { api } from "@/api/client";
import { useAdvisorContext } from "@/hooks/useAdvisorContext";
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
// Insight severity dot colors - light theme
// ---------------------------------------------------------------------------

const SEVERITY_DOT: Record<Severity, string> = {
  critical: "bg-red-500",
  warning: "bg-amber-400",
  optimize: "bg-[#C8FF00]",
  discover: "bg-violet-400",
};

// ---------------------------------------------------------------------------
// Trend indicator
// ---------------------------------------------------------------------------

function TrendPill({ percent, positiveIsGood }: { percent: number; positiveIsGood: boolean }) {
  if (!Number.isFinite(percent) || Math.abs(percent) < 0.5) return null;
  const isUp = percent > 0;
  const isGood = positiveIsGood ? isUp : !isUp;
  return (
    <span className={cn(
      "inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[10px] font-semibold",
      isGood ? "bg-emerald-50 text-emerald-600" : "bg-red-50 text-red-500"
    )}>
      {isUp ? "+" : ""}{Math.abs(percent).toFixed(1)}%
    </span>
  );
}

// ---------------------------------------------------------------------------
// Focus stat card
// ---------------------------------------------------------------------------

interface FocusStatCardProps {
  label: string;
  value: string;
  icon: React.ElementType;
  iconBg: string;
  iconColor: string;
  trend?: number;
  positiveIsGood?: boolean;
}

function FocusStatCard({
  label, value, icon: Icon, iconBg, iconColor, trend, positiveIsGood = true,
}: FocusStatCardProps) {
  return (
    <div className={cn(
      "bg-white rounded-2xl shadow-[0_2px_12px_rgba(0,0,0,0.04)] border border-black/[0.04]",
      "hover:shadow-[0_4px_20px_rgba(0,0,0,0.06)] transition-all duration-300",
      "p-5 flex flex-col gap-3"
    )}>
      <div className="flex items-start justify-between">
        <div className={cn("h-9 w-9 rounded-xl flex items-center justify-center", iconBg)}>
          <Icon className={cn("h-4 w-4 shrink-0", iconColor)} />
        </div>
        {trend !== undefined && (
          <TrendPill percent={trend} positiveIsGood={positiveIsGood} />
        )}
      </div>
      <div>
        <p className="text-xs font-medium text-[#9ca3af] uppercase tracking-wider mb-1">{label}</p>
        <p className="text-3xl font-bold text-[#1a1a1a] tracking-tight leading-none">{value}</p>
      </div>
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

function LimeCostBar({ spent, projected }: CostBarProps) {
  const pct = projected > 0 ? Math.min((spent / projected) * 100, 100) : 0;
  const isNearBudget = pct > 80;

  return (
    <div className={cn(
      "bg-white rounded-2xl shadow-[0_2px_12px_rgba(0,0,0,0.04)] border border-black/[0.04]",
      "hover:shadow-[0_4px_20px_rgba(0,0,0,0.06)] transition-all duration-300",
      "p-5"
    )}>
      <div className="flex items-center gap-2 mb-3">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-violet-50">
          <TrendingUp className="h-3.5 w-3.5 text-[#8B5CF6]" />
        </div>
        <p className="text-sm font-semibold text-[#1a1a1a]">Monthly Cost</p>
      </div>
      <div className="flex items-center justify-between text-xs text-[#6b7280] mb-2">
        <span className="font-semibold text-[#1a1a1a]">{formatCost(spent)} spent</span>
        <span>Projected: {formatCost(projected)}</span>
      </div>
      <div className="h-2 bg-[#f0f0f2] rounded-full overflow-hidden">
        <div
          className={cn(
            "h-full rounded-full transition-all duration-700",
            isNearBudget ? "bg-amber-400" : "bg-[#C8FF00]"
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="mt-1.5 text-[11px] text-[#9ca3af] text-right">{pct.toFixed(0)}% of projected</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Recent activity - minimal light rows
// ---------------------------------------------------------------------------

function FocusRecentRuns({ runs }: { runs: RunItem[] }) {
  const navigate = useNavigate();

  if (runs.length === 0) return null;

  const statusStyle = (status: string) => {
    switch (status) {
      case "completed": return "bg-[#C8FF00]/20 text-[#4a7c00]";
      case "failed": return "bg-red-50 text-red-600";
      case "running": return "bg-blue-50 text-blue-600";
      default: return "bg-gray-100 text-[#6b7280]";
    }
  };

  const statusIcon = (status: string) => {
    switch (status) {
      case "completed": return <CheckCircle className="h-3.5 w-3.5 text-[#4a7c00]" />;
      case "failed": return <AlertTriangle className="h-3.5 w-3.5 text-red-500" />;
      default: return <Activity className="h-3.5 w-3.5 text-blue-500" />;
    }
  };

  return (
    <div className={cn(
      "bg-white rounded-2xl shadow-[0_2px_12px_rgba(0,0,0,0.04)] border border-black/[0.04]",
      "hover:shadow-[0_4px_20px_rgba(0,0,0,0.06)] transition-all duration-300",
      "overflow-hidden"
    )}>
      <div className="flex items-center justify-between px-5 py-4 border-b border-black/[0.04]">
        <p className="text-sm font-semibold text-[#1a1a1a]">Recent Activity</p>
        <Link
          to="/runs"
          className="text-xs font-medium text-[#818CF8] hover:text-[#6366f1] transition-colors"
        >
          View all
        </Link>
      </div>
      <div className="divide-y divide-black/[0.04]">
        {runs.slice(0, 5).map((run) => (
          <button
            key={run.run_id}
            onClick={() => navigate(`/runs/${run.run_id}`)}
            className="flex w-full items-center gap-3 px-5 py-3.5 text-left hover:bg-[#f5f5f7] transition-colors duration-150"
          >
            <div className="shrink-0">
              {statusIcon(run.status)}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-[#1a1a1a]">{run.workflow_name}</p>
              <p className="text-xs text-[#9ca3af] mt-0.5">
                {run.started_at ? formatRelativeTime(run.started_at) : "queued"}
              </p>
            </div>
            <span className="text-xs font-mono text-[#6b7280] shrink-0">
              {formatCost(run.total_cost_usd)}
            </span>
            <span className={cn(
              "text-[10px] font-semibold rounded-full px-2 py-0.5 shrink-0 capitalize",
              statusStyle(run.status)
            )}>
              {run.status}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// OverviewFocus page - Apple-style light theme
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
          api.get<RunItem[]>("/runs", { limit: "5" }),
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

  const pageClass = "min-h-screen bg-[#f5f5f7] -m-4 sm:-m-6 p-4 sm:p-6";

  if (loading) {
    return (
      <div className={pageClass}>
        <div className="max-w-3xl mx-auto space-y-4 pt-4">
          <div className="flex items-center justify-end">
            <Skeleton className="h-8 w-28 rounded-xl bg-black/[0.04]" />
          </div>
          <Skeleton className="h-44 rounded-3xl bg-white shadow-[0_2px_12px_rgba(0,0,0,0.04)]" />
          <div className="grid grid-cols-3 gap-4">
            {[0, 1, 2].map((i) => <Skeleton key={i} className="h-28 rounded-2xl bg-white shadow-[0_2px_12px_rgba(0,0,0,0.04)]" />)}
          </div>
          <Skeleton className="h-32 rounded-2xl bg-white shadow-[0_2px_12px_rgba(0,0,0,0.04)]" />
          <Skeleton className="h-10 rounded-2xl bg-white shadow-[0_2px_12px_rgba(0,0,0,0.04)]" />
          {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-14 rounded-xl bg-white shadow-[0_2px_12px_rgba(0,0,0,0.04)]" />)}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className={pageClass}>
        <div className="max-w-3xl mx-auto pt-8">
          <div className="rounded-2xl border border-red-200 bg-red-50 p-5">
            <p className="text-sm text-red-600">{error}</p>
            <button
              onClick={() => { setLoading(true); setRetryCount((c) => c + 1); }}
              className="mt-2 text-xs font-semibold text-[#4a7c00] hover:text-[#3a6000] transition-colors"
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

  // Hero message based on score
  const heroTitle = advisor.score >= 80
    ? "All systems running smoothly"
    : advisor.score >= 50
    ? "A few things need attention"
    : "Several issues require action";

  // Projected monthly cost
  const historicalDays = stats?.runs_by_day ?? [];
  const avgDailyCost = historicalDays.length > 0
    ? totalCost / Math.max(historicalDays.length, 1)
    : totalCost;
  const projectedMonthly = avgDailyCost * 30;

  // Actionable insights
  const actionableInsights = advisor.activeInsights.filter((i) => i.severity !== "discover");

  return (
    <div className={pageClass}>
      <div className="max-w-3xl mx-auto">
        {/* Switcher - top right */}
        <div className="flex items-center justify-end mb-5">
          <LayoutSwitcher layout={layout} setLayout={setLayout} />
        </div>

        {/* Section 1: Greeting Hero */}
        <div className={cn(
          "bg-white rounded-3xl shadow-[0_2px_12px_rgba(0,0,0,0.04)] border border-black/[0.04]",
          "p-8 sm:p-10 text-center mb-5"
        )}>
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-[#C8FF00] mb-4">
            <Castle className="h-8 w-8 text-[#1a1a1a]" />
          </div>
          {advisor.loading ? (
            <>
              <Skeleton className="h-8 w-72 rounded-xl mx-auto mb-2 bg-black/[0.04]" />
              <Skeleton className="h-5 w-56 rounded-lg mx-auto bg-black/[0.04]" />
            </>
          ) : (
            <>
              <h1 className="text-2xl sm:text-3xl font-bold text-[#1a1a1a] tracking-tight">
                {heroTitle}
              </h1>
              <p className="mt-2 text-base text-[#6b7280]">
                {stats
                  ? `${totalRuns} workflow${totalRuns !== 1 ? "s" : ""} completed today with ${successRate}% success rate`
                  : "Loading statistics..."}
              </p>
              <div className="mt-5 inline-flex items-center gap-2 bg-[#C8FF00]/15 rounded-full px-4 py-1.5">
                {advisor.score >= 80 ? (
                  <CheckCircle2 className="h-3.5 w-3.5 text-[#4a7c00]" />
                ) : (
                  <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
                )}
                <span className="text-sm font-medium text-[#4a7c00]">
                  Health Score: {advisor.score}
                </span>
              </div>
            </>
          )}
        </div>

        {/* Section 2: Stats Row */}
        {stats && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 sm:gap-4 mb-5">
            <FocusStatCard
              label="Runs Today"
              value={String(totalRuns)}
              icon={Activity}
              iconBg="bg-[#C8FF00]/20"
              iconColor="text-[#4a7c00]"
              trend={mockSparklines.runs?.trendPercent}
              positiveIsGood={true}
            />
            <FocusStatCard
              label="Success Rate"
              value={`${successRate}%`}
              icon={CheckCircle}
              iconBg="bg-emerald-50"
              iconColor="text-emerald-600"
              trend={mockSparklines.rate?.trendPercent}
              positiveIsGood={true}
            />
            <FocusStatCard
              label="Cost Today"
              value={formatCost(totalCost)}
              icon={DollarSign}
              iconBg="bg-violet-50"
              iconColor="text-[#8B5CF6]"
              trend={mockSparklines.cost?.trendPercent}
              positiveIsGood={false}
            />
          </div>
        )}

        {/* Section 3: Insights (only if any) */}
        {!advisor.loading && actionableInsights.length > 0 && (
          <div className={cn(
            "bg-white rounded-2xl shadow-[0_2px_12px_rgba(0,0,0,0.04)] border border-black/[0.04]",
            "hover:shadow-[0_4px_20px_rgba(0,0,0,0.06)] transition-all duration-300",
            "overflow-hidden mb-5"
          )}>
            <div className="px-5 py-4 border-b border-black/[0.04] flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-amber-400" />
              <p className="text-sm font-semibold text-[#1a1a1a]">
                {actionableInsights.length} item{actionableInsights.length > 1 ? "s" : ""} need attention
              </p>
            </div>
            <div className="divide-y divide-black/[0.04]">
              {actionableInsights.slice(0, 6).map((insight: Insight) => (
                <Link
                  key={insight.id}
                  to={insight.link}
                  className="flex items-center gap-3 px-5 py-3.5 hover:bg-[#f5f5f7] transition-colors duration-150 group"
                >
                  <span className={cn("h-2 w-2 shrink-0 rounded-full", SEVERITY_DOT[insight.severity])} />
                  <span className="flex-1 text-sm text-[#6b7280] group-hover:text-[#1a1a1a] transition-colors truncate">
                    {insight.title}
                  </span>
                  <ArrowRight className="h-3.5 w-3.5 text-[#9ca3af] shrink-0 group-hover:text-[#6b7280] transition-colors" />
                </Link>
              ))}
              {actionableInsights.length > 6 && (
                <p className="px-5 py-3 text-xs text-[#9ca3af]">
                  +{actionableInsights.length - 6} more items
                </p>
              )}
            </div>
          </div>
        )}

        {/* Section 4: Cost Bar */}
        {stats && projectedMonthly > 0 && (
          <div className="mb-5">
            <LimeCostBar spent={totalCost} projected={projectedMonthly} />
          </div>
        )}

        {/* Section 5: Recent Activity */}
        {recentRuns.length > 0 && (
          <div className="mb-6">
            <FocusRecentRuns runs={recentRuns} />
          </div>
        )}

        {/* Section 6: Quick Action CTA */}
        <div className="flex justify-center pb-4">
          <button
            onClick={() => { window.location.href = "/workflows"; }}
            className={cn(
              "bg-[#C8FF00] text-[#1a1a1a] font-semibold rounded-xl px-6 py-3",
              "shadow-[0_2px_8px_rgba(200,255,0,0.3)]",
              "hover:bg-[#d4ff33] transition-all duration-200 active:scale-[0.98]",
              "flex items-center gap-2"
            )}
          >
            <Play className="h-4 w-4" />
            Run a Workflow
          </button>
        </div>
      </div>
    </div>
  );
}
