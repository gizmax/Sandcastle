import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Link } from "react-router-dom";
import {
  Play, CheckCircle2, ArrowRight, Activity,
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
// Insight severity dot colors
// ---------------------------------------------------------------------------

const SEVERITY_DOT: Record<Severity, string> = {
  critical: "bg-error",
  warning: "bg-warning",
  optimize: "bg-accent",
  discover: "bg-running",
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
      isGood ? "bg-success/10 text-success" : "bg-error/10 text-error"
    )}>
      {isUp ? "+" : ""}{Math.abs(percent).toFixed(1)}%
    </span>
  );
}

// ---------------------------------------------------------------------------
// Sparkline SVG
// ---------------------------------------------------------------------------

function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return null;
  const w = 64; const h = 24; const padding = 2;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const points = values
    .map((v, i) => {
      const x = padding + (i / (values.length - 1)) * (w - padding * 2);
      const y = h - padding - ((v - min) / range) * (h - padding * 2);
      return `${x},${y}`;
    })
    .join(" ");
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true" className="shrink-0">
      <polyline points={points} fill="none" strokeWidth="1.5"
        strokeLinecap="round" strokeLinejoin="round" className="stroke-accent" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Focus stat card with sparkline
// ---------------------------------------------------------------------------

interface FocusStatCardProps {
  label: string;
  value: string;
  icon: React.ElementType;
  iconBg: string;
  iconColor: string;
  spark?: SparklineData;
  positiveIsGood?: boolean;
}

function FocusStatCard({
  label, value, icon: Icon, iconBg, iconColor, spark, positiveIsGood = true,
}: FocusStatCardProps) {
  return (
    <div className={cn(
      "bg-surface rounded-2xl shadow-sm border border-border",
      "hover:border-accent/30 transition-settle",
      "p-5 flex flex-col gap-3"
    )}>
      <div className="flex items-start justify-between">
        <div className={cn("h-9 w-9 rounded-xl flex items-center justify-center", iconBg)}>
          <Icon className={cn("h-4 w-4 shrink-0", iconColor)} />
        </div>
        {spark && (
          <TrendPill percent={spark.trendPercent} positiveIsGood={positiveIsGood} />
        )}
      </div>
      <div className="flex items-end justify-between gap-2">
        <div>
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">{label}</p>
          <p className="font-display text-3xl font-bold text-foreground tracking-tight leading-none">{value}</p>
        </div>
        {spark && spark.values.length >= 2 && (
          <Sparkline values={spark.values} />
        )}
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

function FocusCostBar({ spent, projected }: CostBarProps) {
  const pct = projected > 0 ? Math.min((spent / projected) * 100, 100) : 0;
  const isNearBudget = pct > 80;

  return (
    <div className={cn(
      "bg-surface rounded-2xl shadow-sm border border-border",
      "hover:border-accent/30 transition-settle",
      "p-5"
    )}>
      <div className="flex items-center gap-2 mb-3">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-running/10">
          <TrendingUp className="h-3.5 w-3.5 text-running" />
        </div>
        <p className="text-sm font-semibold text-foreground">Monthly Cost</p>
      </div>
      <div className="flex items-center justify-between text-xs text-muted-foreground mb-2">
        <span className="font-semibold text-foreground">{formatCost(spent)} spent</span>
        <span>Projected: {formatCost(projected)}</span>
      </div>
      <div className="h-2 bg-border rounded-full overflow-hidden">
        <div
          className={cn(
            "h-full rounded-full transition-all duration-700",
            isNearBudget ? "bg-warning" : "bg-accent"
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="mt-1.5 text-[11px] text-muted-foreground text-right">{pct.toFixed(0)}% of projected</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Recent activity - compact rows
// ---------------------------------------------------------------------------

function FocusRecentRuns({ runs }: { runs: RunItem[] }) {
  const navigate = useNavigate();

  if (runs.length === 0) return null;

  const statusStyle = (status: string) => {
    switch (status) {
      case "completed": return "bg-success/10 text-success";
      case "failed": return "bg-error/10 text-error";
      case "running": return "bg-running/10 text-running";
      default: return "bg-border text-muted-foreground";
    }
  };

  const statusIcon = (status: string) => {
    switch (status) {
      case "completed": return <CheckCircle className="h-3.5 w-3.5 text-success" />;
      case "failed": return <AlertTriangle className="h-3.5 w-3.5 text-error" />;
      default: return <Activity className="h-3.5 w-3.5 text-running" />;
    }
  };

  return (
    <div className={cn(
      "bg-surface rounded-2xl shadow-sm border border-border",
      "hover:border-accent/30 transition-settle",
      "overflow-hidden"
    )}>
      <div className="flex items-center justify-between px-5 py-4 border-b border-border">
        <p className="text-sm font-semibold text-foreground">Recent Activity</p>
        <Link
          to="/runs"
          className="text-xs font-medium text-accent hover:text-accent-hover transition-colors"
        >
          View all
        </Link>
      </div>
      <div className="divide-y divide-border">
        {runs.slice(0, 5).map((run) => (
          <button
            key={run.run_id}
            onClick={() => navigate(`/runs/${run.run_id}`)}
            className="flex w-full items-center gap-3 px-5 py-3.5 text-left hover:bg-background transition-colors duration-150"
          >
            <div className="shrink-0">
              {statusIcon(run.status)}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-foreground">{run.workflow_name}</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                {run.started_at ? formatRelativeTime(run.started_at) : "queued"}
              </p>
            </div>
            <span className="text-xs font-mono text-muted-foreground shrink-0">
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
// OverviewFocus page - focused command dashboard
// ---------------------------------------------------------------------------

export default function OverviewFocus({
  layout, setLayout,
}: {
  layout: string;
  setLayout: (l: string) => void;
}) {
  const navigate = useNavigate();
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

  if (loading) {
    return (
      <div className="max-w-3xl mx-auto space-y-4 pt-2">
        <div className="flex items-center justify-end">
          <Skeleton className="h-8 w-28 rounded-xl" />
        </div>
        <Skeleton className="h-24 rounded-2xl" />
        <div className="grid grid-cols-3 gap-4">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-28 rounded-2xl" />)}
        </div>
        <Skeleton className="h-32 rounded-2xl" />
        <Skeleton className="h-48 rounded-2xl" />
        <Skeleton className="h-20 rounded-2xl" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="max-w-3xl mx-auto pt-8">
        <div className="rounded-2xl border border-error/30 bg-error/5 p-5">
          <p className="text-sm text-error">{error}</p>
          <button
            onClick={() => { setLoading(true); setRetryCount((c) => c + 1); }}
            className="mt-2 text-xs font-semibold text-accent hover:text-accent-hover transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const totalRuns = stats?.total_runs_today ?? 0;
  const successRate = stats ? Math.round(stats.success_rate * 100) : 0;
  const totalCost = stats?.total_cost_today ?? 0;

  // Projected monthly cost
  const historicalDays = stats?.runs_by_day ?? [];
  const avgDailyCost = historicalDays.length > 0
    ? totalCost / Math.max(historicalDays.length, 1)
    : totalCost;
  const projectedMonthly = avgDailyCost * 30;

  // Actionable insights
  const actionableInsights = advisor.activeInsights.filter((i) => i.severity !== "discover");

  // Health label
  const healthLabel = advisor.score >= 80 ? "Healthy" : advisor.score >= 50 ? "Needs Attention" : "Critical";
  const healthBadgeClass = advisor.score >= 80
    ? "bg-success/10 text-success border-success/20"
    : advisor.score >= 50
    ? "bg-warning/10 text-warning border-warning/20"
    : "bg-error/10 text-error border-error/20";

  return (
    <div className="max-w-3xl mx-auto space-y-4">
      {/* Switcher - top right */}
      <div className="flex items-center justify-end">
        <LayoutSwitcher layout={layout} setLayout={setLayout} />
      </div>

      {/* Section 1: Compact hero with inline CTA - PRIMARY ACTION ABOVE FOLD */}
      <div className={cn(
        "bg-surface rounded-2xl shadow-sm border border-border",
        "hover:border-accent/30 transition-settle",
        "p-5"
      )}>
        <div className="flex items-center justify-between gap-4">
          {/* Left: key metrics inline */}
          <div className="flex items-center gap-3 flex-wrap min-w-0">
            {advisor.loading ? (
              <Skeleton className="h-5 w-48 rounded-lg" />
            ) : (
              <>
                <div className="flex items-center gap-1.5">
                  <Activity className="h-4 w-4 text-accent shrink-0" />
                  <span className="text-sm font-semibold text-foreground">{totalRuns} runs today</span>
                </div>
                <span className="text-border hidden sm:block">|</span>
                <div className="flex items-center gap-1.5">
                  <CheckCircle2 className="h-4 w-4 text-success shrink-0" />
                  <span className="text-sm font-medium text-muted-foreground">{successRate}% success</span>
                </div>
                <span className="text-border hidden sm:block">|</span>
                <div className="flex items-center gap-1.5">
                  <DollarSign className="h-4 w-4 text-running shrink-0" />
                  <span className="text-sm font-medium text-muted-foreground">{formatCost(totalCost)} cost</span>
                </div>
              </>
            )}
          </div>

          {/* Right: CTA + health badge */}
          <div className="flex items-center gap-2 shrink-0">
            {!advisor.loading && (
              <span className={cn(
                "hidden sm:inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold",
                healthBadgeClass
              )}>
                {advisor.score >= 80 ? (
                  <CheckCircle2 className="h-3 w-3" />
                ) : (
                  <AlertTriangle className="h-3 w-3" />
                )}
                {healthLabel}
              </span>
            )}
            <button
              onClick={() => navigate("/workflows")}
              className={cn(
                "flex items-center gap-2 rounded-xl px-4 py-2",
                "bg-accent text-accent-foreground font-semibold text-sm",
                "hover:bg-accent-hover transition-settle active:scale-[0.98]",
                "whitespace-nowrap"
              )}
            >
              <Play className="h-3.5 w-3.5" />
              Run Workflow
            </button>
          </div>
        </div>
      </div>

      {/* Section 2: Insights (if any) - actionable items near the top */}
      {!advisor.loading && actionableInsights.length > 0 && (
        <div className={cn(
          "bg-surface rounded-2xl shadow-sm border border-border",
          "hover:border-accent/30 transition-settle",
          "overflow-hidden"
        )}>
          <div className="px-5 py-4 border-b border-border flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-warning" />
            <p className="text-sm font-semibold text-foreground">
              {actionableInsights.length} item{actionableInsights.length > 1 ? "s" : ""} need attention
            </p>
          </div>
          <div className="divide-y divide-border">
            {actionableInsights.slice(0, 6).map((insight: Insight) => (
              <Link
                key={insight.id}
                to={insight.link}
                className="flex items-center gap-3 px-5 py-3.5 hover:bg-background transition-colors duration-150 group"
              >
                <span className={cn("h-2 w-2 shrink-0 rounded-full", SEVERITY_DOT[insight.severity])} />
                <span className="flex-1 text-sm text-muted-foreground group-hover:text-foreground transition-colors truncate">
                  {insight.title}
                </span>
                <ArrowRight className="h-3.5 w-3.5 text-muted-foreground shrink-0 group-hover:text-accent transition-colors" />
              </Link>
            ))}
            {actionableInsights.length > 6 && (
              <p className="px-5 py-3 text-xs text-muted-foreground">
                +{actionableInsights.length - 6} more items
              </p>
            )}
          </div>
        </div>
      )}

      {/* Section 3: Stats cards in a row with sparklines */}
      {stats && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 sm:gap-4">
          <FocusStatCard
            label="Runs Today"
            value={String(totalRuns)}
            icon={Activity}
            iconBg="bg-accent/10"
            iconColor="text-accent"
            spark={mockSparklines.runs}
            positiveIsGood={true}
          />
          <FocusStatCard
            label="Success Rate"
            value={`${successRate}%`}
            icon={CheckCircle}
            iconBg="bg-success/10"
            iconColor="text-success"
            spark={mockSparklines.rate}
            positiveIsGood={true}
          />
          <FocusStatCard
            label="Cost Today"
            value={formatCost(totalCost)}
            icon={DollarSign}
            iconBg="bg-running/10"
            iconColor="text-running"
            spark={mockSparklines.cost}
            positiveIsGood={false}
          />
        </div>
      )}

      {/* Section 4: Recent Runs */}
      {recentRuns.length > 0 && (
        <FocusRecentRuns runs={recentRuns} />
      )}

      {/* Section 5: Cost bar + empty state nudge */}
      {stats && projectedMonthly > 0 && (
        <FocusCostBar spent={totalCost} projected={projectedMonthly} />
      )}
    </div>
  );
}
