import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Play, AlertTriangle, BarChart3, Star, GitBranch,
  CheckCircle2, ArrowRight, Activity, DollarSign, CheckCircle,
  Castle, Timer, Sparkles, X, Check, Shuffle, Wifi, Key, Rocket, Loader2,
} from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "@/api/client";
import { RunsChart } from "@/components/overview/RunsChart";
import { CostChart } from "@/components/overview/CostChart";
import { CostForecast } from "@/components/overview/CostForecast";
import { useAdvisorContext } from "@/hooks/useAdvisorContext";
import { usePinnedWorkflows } from "@/hooks/usePinnedWorkflows";
import { useEventStreamContext } from "@/hooks/useEventStreamContext";
import { Skeleton } from "@/components/ui/Skeleton";
import { cn, formatCost, formatRelativeTime, buttonMd, buttonPrimary, buttonDanger, buttonSecondary, iconMd } from "@/lib/utils";
import type { Insight, Severity } from "@/lib/insights";

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
  /** Backend returns trend_percent; we normalise to trendPercent in the fetch. */
  trendPercent: number;
}

interface ApiSparklineData {
  values: number[];
  trend_percent: number;
}

interface HeatmapApiCell {
  date: string;
  count: number;
  day_of_week: number;
}

interface AnomalyItem {
  type: string;
  severity: "warning" | "critical";
  workflow: string;
  message: string;
  /** May be empty string or null when anomaly is not linked to a specific run. */
  run_id: string | null;
  value: number;
  threshold: number;
}

interface ProviderCostEntry {
  provider: string;
  model: string;
  region: string;
  total_cost_usd: number;
  run_count: number;
  avg_cost_per_run: number;
  percentage: number;
}

interface ProviderSavingsAlternative {
  provider: string;
  model: string;
  region: string;
  projected_cost_usd: number;
  savings_usd: number;
  savings_percent: number;
  note: string;
}

interface ProviderCostsData {
  period_days: number;
  total_cost_usd: number;
  by_provider: ProviderCostEntry[];
  advisor_costs: {
    total_usd: number;
    by_purpose: Array<{ purpose: string; cost_usd: number; calls: number }>;
  };
}

interface ProviderSavingsData {
  current_total_usd: number;
  alternatives: ProviderSavingsAlternative[];
}

interface ProviderRecommendation {
  type: string;
  severity: "high" | "medium" | "info";
  title: string;
  description: string;
  action: string;
  provider: string;
  estimated_savings_usd: number;
  confidence: number;
}

interface AdvisorRecommendation {
  workflow: string;
  current_provider: string;
  current_cost_30d: number;
  suggested_provider: string;
  suggested_model: string;
  estimated_cost_30d: number;
  savings_monthly: number;
  savings_percent: number;
  eu_compliant: boolean;
  reason: string;
}

interface AdvisorRecommendationsData {
  recommendations: AdvisorRecommendation[];
  total_potential_savings: number;
}

interface FailoverEvent {
  timestamp: string;
  original_provider: string;
  failover_provider: string;
  reason: string;
  cost_delta: number;
}

interface FailoverEventsData {
  events: FailoverEvent[];
  total_failovers_7d: number;
  total_cost_delta_7d: number;
}

interface LayoutSwitcherProps {
  layout: string;
  setLayout: (l: string) => void;
}

interface HeatmapCell {
  date: string;
  count: number;
  /** 0=Monday ... 6=Sunday (matches backend day_of_week) */
  dayOfWeek: number;
}

// ---------------------------------------------------------------------------
// Helpers for normalising API response shapes
// ---------------------------------------------------------------------------

function normaliseSparkline(raw: ApiSparklineData): SparklineData {
  return { values: raw.values, trendPercent: raw.trend_percent };
}

function normaliseHeatmapCell(raw: HeatmapApiCell): HeatmapCell {
  return { date: raw.date, count: raw.count, dayOfWeek: raw.day_of_week };
}

// ---------------------------------------------------------------------------
// useLazyLoad - IntersectionObserver hook for below-fold widgets
// ---------------------------------------------------------------------------

/**
 * Returns a ref callback to attach to a sentinel element plus a boolean
 * indicating whether that element has ever entered the viewport. Once
 * visible the observer disconnects so the widget stays mounted permanently.
 */
function useLazyLoad(rootMargin = "500px"): [React.RefCallback<HTMLDivElement>, boolean] {
  const [visible, setVisible] = useState(false);
  const observerRef = useRef<IntersectionObserver | null>(null);

  const setRef = useCallback(
    (node: HTMLDivElement | null) => {
      // Disconnect any previous observer
      if (observerRef.current) {
        observerRef.current.disconnect();
        observerRef.current = null;
      }
      if (!node || visible) return;

      observerRef.current = new IntersectionObserver(
        ([entry]) => {
          if (entry.isIntersecting) {
            setVisible(true);
            observerRef.current?.disconnect();
            observerRef.current = null;
          }
        },
        { rootMargin },
      );
      observerRef.current.observe(node);
    },
    [rootMargin, visible],
  );

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      observerRef.current?.disconnect();
    };
  }, []);

  return [setRef, visible];
}

// ---------------------------------------------------------------------------
// Sparkline (wider, card-bottom style)
// ---------------------------------------------------------------------------

function Sparkline({ values, className }: { values: number[]; className?: string }) {
  if (values.length < 2) return null;
  const w = 80; const h = 28; const padding = 2;
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
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true" className={cn("shrink-0", className)}>
      <polyline points={points} fill="none" strokeWidth="2"
        strokeLinecap="round" strokeLinejoin="round" className="stroke-accent" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Trend badge
// ---------------------------------------------------------------------------

function TrendBadge({ percent, positiveIsGood }: { percent: number; positiveIsGood: boolean }) {
  if (!Number.isFinite(percent) || Math.abs(percent) < 0.5) return null;
  const isUp = percent > 0;
  const isGood = positiveIsGood ? isUp : !isUp;
  return (
    <span className={cn(
      "inline-flex items-center gap-0.5 rounded-full px-2 py-0.5 text-[10px] font-semibold",
      isGood
        ? "bg-success/10 text-success"
        : "bg-error/10 text-error"
    )}>
      {isUp ? "+" : ""}{Math.abs(percent).toFixed(1)}%
    </span>
  );
}

// ---------------------------------------------------------------------------
// Stat card with sparkline
// ---------------------------------------------------------------------------

interface StatCardProps {
  label: string;
  value: string;
  icon: React.ElementType;
  iconBg: string;
  iconColor: string;
  spark?: SparklineData;
  positiveIsGood?: boolean;
}

function BentoStatCard({
  label, value, icon: Icon, iconBg, iconColor,
  spark, positiveIsGood = true,
}: StatCardProps) {
  return (
    <div className={cn(
      "bg-surface rounded-2xl shadow-sm border border-border",
      "p-6",
      "hover:border-accent/30 transition-all duration-300",
      "flex flex-col gap-3"
    )}>
      <div className="flex items-start justify-between">
        <div className={cn("h-9 w-9 rounded-xl flex items-center justify-center", iconBg)}>
          <Icon className={cn("shrink-0", iconColor)} style={{ height: "18px", width: "18px" }} />
        </div>
        {spark && (
          <TrendBadge percent={spark.trendPercent} positiveIsGood={positiveIsGood} />
        )}
      </div>
      <div className="flex items-end justify-between gap-2">
        <div>
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-0.5">{label}</p>
          <p className="text-3xl font-bold text-foreground tracking-tight leading-none">{value}</p>
        </div>
        {spark && spark.values.length >= 2 && (
          <Sparkline values={spark.values} />
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Health Hero
// ---------------------------------------------------------------------------

const SEVERITY_DOT: Record<Severity, string> = {
  critical: "bg-error",
  warning: "bg-warning",
  optimize: "bg-accent",
  discover: "bg-running",
};

function BentoHealthHero({
  score, activeInsights, loading, totalRuns, successRate, totalCost, avgDuration,
}: {
  score: number; activeInsights: Insight[]; loading: boolean;
  totalRuns: number; successRate: number; totalCost: number; avgDuration: number;
}) {
  const actionable = activeInsights.filter((i) => i.severity !== "discover");
  const scoreLabel = score >= 80 ? "Healthy" : score >= 50 ? "Needs Attention" : "Critical";

  return (
    <div className={cn(
      "bg-surface rounded-2xl shadow-sm border border-border",
      "hover:border-accent/30 transition-all duration-300",
      "p-6 flex flex-col gap-4 h-full relative"
    )}>
      {/* Health badge - top right, clickable to /system-health */}
      <div className="absolute top-5 right-5">
        {loading ? (
          <div className="h-14 w-14 rounded-full bg-border animate-pulse" />
        ) : (
          <Link
            to="/system-health"
            title="View system health details"
            className={cn(
              "flex items-center justify-center w-14 h-14 rounded-full",
              "text-xl font-bold transition-opacity hover:opacity-80",
              score >= 80 ? "bg-success/20 text-success" : score >= 50 ? "bg-warning/20 text-warning" : "bg-error/20 text-error"
            )}
          >
            {score}
          </Link>
        )}
      </div>

      {/* Title area */}
      <div>
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">
          Workflow Command Center
        </p>
        <p className="text-5xl font-bold text-foreground tracking-tight leading-none">
          {totalRuns}
        </p>
        <p className="mt-1 text-sm text-muted-foreground">workflows ran today</p>
      </div>

      {/* Mini-stat pills */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="inline-flex items-center gap-1.5 rounded-full bg-success/10 border border-success/20 px-3 py-1.5 text-xs font-semibold text-success">
          <span className="h-1.5 w-1.5 rounded-full bg-success" />
          {successRate}% success
        </span>
        <span className="inline-flex items-center gap-1.5 rounded-full bg-accent/10 border border-accent/20 px-3 py-1.5 text-xs font-semibold text-accent">
          <DollarSign className="h-3 w-3" />
          {formatCost(totalCost)} cost
        </span>
        <span className="inline-flex items-center gap-1.5 rounded-full bg-running/10 border border-running/20 px-3 py-1.5 text-xs font-semibold text-running">
          <span className="h-1.5 w-1.5 rounded-full bg-running" />
          {avgDuration > 0 ? `${Math.round(avgDuration)}s` : "n/a"} avg
        </span>
      </div>

      {/* Status - healthy pill when score is good and no actionable insights */}
      {!loading && actionable.length === 0 && (
        <div className="flex items-center gap-2">
          <span className={cn(
            "inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold",
            score >= 80
              ? "bg-success/10 border border-success/20 text-success"
              : "bg-warning/10 border border-warning/20 text-warning"
          )}>
            <CheckCircle2 className="h-3.5 w-3.5" />
            {score >= 80 ? "All systems healthy" : scoreLabel}
          </span>
        </div>
      )}

      {/* Insights */}
      {!loading && actionable.length > 0 && (
        <div className="flex flex-col gap-1 flex-1">
          {actionable.slice(0, 4).map((insight) => (
            <Link
              key={insight.id}
              to={insight.link}
              className={cn(
                "flex items-center gap-2.5 rounded-xl px-3 py-2",
                "bg-background border border-border",
                "hover:border-accent/30 hover:bg-surface",
                "transition-colors duration-150 text-sm"
              )}
            >
              <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", SEVERITY_DOT[insight.severity])} />
              <span className="flex-1 text-foreground truncate">{insight.title}</span>
              <ArrowRight className="h-3 w-3 text-muted-foreground shrink-0" />
            </Link>
          ))}
          {actionable.length > 4 && (
            <p className="px-3 text-xs text-muted-foreground">+{actionable.length - 4} more</p>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Quick Actions
// ---------------------------------------------------------------------------

function BentoQuickActions() {
  const navigate = useNavigate();
  const { pinnedWorkflows } = usePinnedWorkflows();

  return (
    <div className={cn(
      "bg-surface rounded-2xl shadow-sm border border-border",
      "hover:border-accent/30 transition-all duration-300",
      "p-6 flex flex-col gap-3 h-full"
    )}>
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">
        Quick Actions
      </p>

      <button
        onClick={() => navigate("/workflows")}
        className={cn(
          "flex items-center gap-3 rounded-xl w-full text-left font-semibold",
          buttonMd, buttonPrimary,
          "active:scale-[0.98]"
        )}
      >
        <Play className={cn(iconMd, "shrink-0")} />
        <span className="text-sm font-semibold">Run Workflow</span>
      </button>

      <button
        onClick={() => navigate("/runs?status=failed")}
        className={cn(
          "flex items-center gap-3 rounded-xl w-full text-left font-semibold",
          buttonMd, buttonDanger,
          "active:scale-[0.98]"
        )}
      >
        <AlertTriangle className={cn(iconMd, "shrink-0")} />
        <span className="text-sm font-semibold">View Failures</span>
      </button>

      <button
        onClick={() => navigate("/runs?sort=cost")}
        className={cn(
          "flex items-center gap-3 rounded-xl w-full text-left font-semibold",
          buttonMd, buttonSecondary,
          "active:scale-[0.98]"
        )}
      >
        <BarChart3 className={cn(iconMd, "shrink-0")} />
        <span className="text-sm font-semibold">Cost Report</span>
      </button>

      {pinnedWorkflows.length > 0 && (
        <div className="mt-1 pt-3 border-t border-border">
          <div className="flex items-center gap-2 mb-2">
            <Star className="h-3.5 w-3.5 text-accent fill-accent" />
            <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Pinned</span>
          </div>
          <div className="space-y-1">
            {pinnedWorkflows.slice(0, 3).map((wfName) => (
              <button
                key={wfName}
                onClick={() => navigate(`/workflows/${encodeURIComponent(wfName)}`)}
                className={cn(
                  "flex items-center gap-2 w-full rounded-lg px-3 py-2",
                  "bg-background border border-border",
                  "hover:border-accent/30 hover:bg-surface",
                  "transition-colors text-left"
                )}
              >
                <GitBranch className="h-3 w-3 text-running shrink-0" />
                <span className="text-sm text-foreground truncate">{wfName}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Activity Heatmap
// ---------------------------------------------------------------------------

const DAY_LABELS = ["M", "", "W", "", "F", "", ""];

function getHeatmapIntensityClass(count: number, maxCount: number): string {
  if (count === 0) return "bg-border";
  const ratio = count / maxCount;
  if (ratio < 0.25) return "bg-accent/20";
  if (ratio < 0.5) return "bg-accent/40";
  if (ratio < 0.75) return "bg-accent/70";
  return "bg-accent";
}

function BentoActivityHeatmap({ cells }: { cells: HeatmapCell[] }) {
  const navigate = useNavigate();
  const [tooltip, setTooltip] = useState<{ x: number; y: number; text: string } | null>(null);

  const weeks: (HeatmapCell | null)[][] = [];
  let currentWeek: (HeatmapCell | null)[] = Array(7).fill(null);

  for (const cell of cells) {
    // dayOfWeek is already 0=Mon...6=Sun (matches backend weekday())
    const row = cell.dayOfWeek;
    if (row === 0 && currentWeek.some((c) => c !== null)) {
      weeks.push(currentWeek);
      currentWeek = Array(7).fill(null);
    }
    currentWeek[row] = cell;
  }
  if (currentWeek.some((c) => c !== null)) weeks.push(currentWeek);

  const maxCount = Math.max(1, ...cells.map((c) => c.count));

  const monthLabels: { col: number; label: string }[] = [];
  const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  let prevMonth = -1;
  for (let col = 0; col < weeks.length; col++) {
    const firstCell = weeks[col].find((c) => c !== null);
    if (firstCell) {
      const month = parseInt(firstCell.date.slice(5, 7), 10) - 1;
      if (month !== prevMonth) {
        monthLabels.push({ col, label: monthNames[month] });
        prevMonth = month;
      }
    }
  }

  return (
    <div className={cn(
      "bg-surface rounded-2xl shadow-sm border border-border",
      "hover:border-accent/30 transition-all duration-300",
      "p-6"
    )}>
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-foreground">Activity</h3>
        <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
          <span>Less</span>
          <div className="h-[9px] w-[9px] rounded-[2px] bg-border" />
          <div className="h-[9px] w-[9px] rounded-[2px] bg-accent/20" />
          <div className="h-[9px] w-[9px] rounded-[2px] bg-accent/40" />
          <div className="h-[9px] w-[9px] rounded-[2px] bg-accent/70" />
          <div className="h-[9px] w-[9px] rounded-[2px] bg-accent" />
          <span>More</span>
        </div>
      </div>

      <div className="relative overflow-x-auto">
        {/* Month labels */}
        <div className="flex gap-[3px]" style={{ paddingLeft: "28px" }}>
          {weeks.map((_, col) => {
            const ml = monthLabels.find((m) => m.col === col);
            return (
              <div key={col} className="flex-1 text-[10px] text-muted-foreground min-w-0">
                {ml ? ml.label : ""}
              </div>
            );
          })}
        </div>

        <div className="flex gap-0">
          {/* Day labels */}
          <div className="flex flex-col gap-[3px] pr-1.5 pt-[3px]" style={{ width: "28px", minWidth: "28px" }}>
            {DAY_LABELS.map((label, i) => (
              <div key={i} className="flex h-[14px] items-center text-[10px] leading-none text-muted-foreground">
                {label}
              </div>
            ))}
          </div>

          {/* Grid - stretches to fill card width */}
          <div className="flex gap-[3px] flex-1">
            {weeks.map((week, col) => (
              <div key={col} className="flex flex-col gap-[3px] flex-1">
                {week.map((cell, row) => (
                  <div
                    key={row}
                    className={cn(
                      "h-[14px] rounded-[3px] transition-colors duration-100",
                      cell ? "cursor-pointer" : "cursor-default",
                      cell ? getHeatmapIntensityClass(cell.count, maxCount) : "bg-transparent"
                    )}
                    onClick={() => {
                      if (cell) navigate(`/runs?date=${cell.date}`);
                    }}
                    onMouseEnter={(e) => {
                      if (!cell) return;
                      const rect = e.currentTarget.getBoundingClientRect();
                      setTooltip({
                        x: rect.left + rect.width / 2,
                        y: rect.top - 8,
                        text: `${cell.date}: ${cell.count} run${cell.count !== 1 ? "s" : ""}`,
                      });
                    }}
                    onMouseLeave={() => setTooltip(null)}
                  />
                ))}
              </div>
            ))}
          </div>
        </div>
      </div>

      {tooltip && (
        <div
          className="pointer-events-none fixed z-50 rounded-lg bg-foreground px-2.5 py-1.5 text-[11px] font-medium text-background shadow-xl"
          style={{
            left: tooltip.x, top: tooltip.y,
            transform: "translate(-50%, -100%)",
          }}
        >
          {tooltip.text}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Recent Runs - minimal table
// ---------------------------------------------------------------------------

function BentoRecentRuns({ runs }: { runs: RunItem[] }) {
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

  return (
    <div className={cn(
      "bg-surface rounded-2xl shadow-sm border border-border",
      "hover:border-accent/30 transition-all duration-300",
      "overflow-hidden"
    )}>
      <div className="px-5 py-4 border-b border-border">
        <h3 className="text-sm font-semibold text-foreground">Recent Runs</h3>
      </div>
      <div className="divide-y divide-border">
        {runs.slice(0, 5).map((run) => (
          <button
            key={run.run_id}
            onClick={() => navigate(`/runs/${run.run_id}`)}
            className="flex w-full items-center gap-3 px-5 py-3.5 text-left hover:bg-background transition-colors duration-150"
          >
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
// Anomaly badges
// ---------------------------------------------------------------------------

function BentoAnomalies({ anomalies }: { anomalies: AnomalyItem[] }) {
  const navigate = useNavigate();
  if (anomalies.length === 0) return null;
  return (
    <div className={cn(
      "bg-surface rounded-2xl shadow-sm border border-border",
      "hover:border-accent/30 transition-all duration-300",
      "p-6 flex flex-col gap-3"
    )}>
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
        Anomalies Detected
      </p>
      <div className="flex flex-col gap-2">
        {anomalies.slice(0, 5).map((a, i) => (
          <button
            key={`${a.type}-${a.workflow}-${i}`}
            onClick={() => a.run_id ? navigate(`/runs/${a.run_id}`) : undefined}
            className={cn(
              "flex items-start gap-2.5 rounded-xl px-3 py-2 text-left w-full",
              "border transition-colors duration-150",
              a.severity === "critical"
                ? "border-error/30 bg-error/5 hover:bg-error/10"
                : "border-warning/30 bg-warning/5 hover:bg-warning/10"
            )}
          >
            <AlertTriangle className={cn(
              "h-4 w-4 shrink-0 mt-0.5",
              a.severity === "critical" ? "text-error" : "text-warning"
            )} />
            <span className="flex-1 text-sm text-foreground">{a.message}</span>
            {a.run_id && (
              <ArrowRight className="h-3 w-3 text-muted-foreground shrink-0 mt-1" />
            )}
          </button>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Layout switcher (shared)
// ---------------------------------------------------------------------------

export function LayoutSwitcher({ layout, setLayout }: LayoutSwitcherProps) {
  const options = [
    { id: "bento", title: "Bento Grid", icon: (
      <svg viewBox="0 0 14 14" fill="none" className="h-3.5 w-3.5">
        <rect x="1" y="1" width="5" height="5" rx="1" fill="currentColor" />
        <rect x="8" y="1" width="5" height="5" rx="1" fill="currentColor" opacity="0.5" />
        <rect x="1" y="8" width="5" height="5" rx="1" fill="currentColor" opacity="0.5" />
        <rect x="8" y="8" width="5" height="5" rx="1" fill="currentColor" opacity="0.5" />
      </svg>
    )},
    { id: "focus", title: "Focus Mode", icon: (
      <svg viewBox="0 0 14 14" fill="none" className="h-3.5 w-3.5">
        <rect x="2" y="3" width="10" height="1.5" rx="0.75" fill="currentColor" />
        <rect x="4" y="6.25" width="6" height="1.5" rx="0.75" fill="currentColor" opacity="0.7" />
        <rect x="5" y="9.5" width="4" height="1.5" rx="0.75" fill="currentColor" opacity="0.4" />
      </svg>
    )},
    { id: "default", title: "Classic", icon: (
      <svg viewBox="0 0 14 14" fill="none" className="h-3.5 w-3.5">
        <rect x="1" y="1" width="12" height="3" rx="1" fill="currentColor" />
        <rect x="1" y="5.5" width="12" height="3" rx="1" fill="currentColor" opacity="0.6" />
        <rect x="1" y="10" width="12" height="3" rx="1" fill="currentColor" opacity="0.35" />
      </svg>
    )},
  ];

  return (
    <div className="flex items-center gap-0.5 rounded-xl border border-border bg-surface shadow-sm p-0.5">
      {options.map((opt) => (
        <button
          key={opt.id}
          title={opt.title}
          onClick={() => {
            setLayout(opt.id);
            localStorage.setItem("sandcastle_overview_layout", opt.id);
          }}
          className={cn(
            "flex items-center justify-center rounded-lg w-7 h-7 transition-all duration-150",
            layout === opt.id
              ? "bg-accent text-accent-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground"
          )}
        >
          {opt.icon}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Provider Cost Widget
// ---------------------------------------------------------------------------

const PROVIDER_COLORS: Record<string, string> = {
  claude: "bg-accent",
  anthropic: "bg-accent",
  openai: "bg-running",
  mistral: "bg-success",
  minimax: "bg-warning",
  google: "bg-error",
  ollama: "bg-muted-foreground",
};

function getProviderColor(provider: string): string {
  return PROVIDER_COLORS[provider.toLowerCase()] ?? "bg-accent";
}

function getRegionFlag(region: string): string {
  if (region === "eu") return " EU";
  if (region === "local") return " Local";
  return "";
}

function BentoProviderCosts({
  providerCosts,
  savings,
}: {
  providerCosts: ProviderCostsData | null;
  savings: ProviderSavingsData | null;
}) {
  if (!providerCosts) return null;
  const { by_provider, total_cost_usd, period_days } = providerCosts;
  const topSaving = savings?.alternatives?.[0] ?? null;

  return (
    <div className={cn(
      "rounded-2xl border border-border bg-surface shadow-sm",
      "hover:border-accent/30 transition-all duration-300",
      "p-5 flex flex-col gap-3"
    )}>
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-foreground">Cost by Provider</h3>
        <span className="text-xs text-muted-foreground">Last {period_days} days</span>
      </div>

      {by_provider.length === 0 ? (
        <p className="text-xs text-muted-foreground">No cost data available yet.</p>
      ) : (
        <div className="space-y-3">
          {by_provider.map((p) => (
            <div key={`${p.provider}:${p.model}`}>
              <div className="flex justify-between text-xs mb-1">
                <span className="text-foreground font-medium">
                  {p.provider}
                  <span className="text-muted-foreground font-normal ml-1">
                    {getRegionFlag(p.region)}
                  </span>
                </span>
                <span className="text-muted-foreground tabular-nums">
                  ${p.total_cost_usd.toFixed(2)} ({p.percentage}%)
                </span>
              </div>
              <div className="h-2 rounded-full bg-border overflow-hidden">
                <div
                  className={cn("h-full rounded-full transition-all duration-500", getProviderColor(p.provider))}
                  style={{ width: `${p.percentage}%` }}
                />
              </div>
              <p className="text-[10px] text-muted-foreground mt-0.5">
                {p.run_count.toLocaleString()} steps - ${p.avg_cost_per_run.toFixed(4)} avg
              </p>
            </div>
          ))}
        </div>
      )}

      <div className="pt-1 border-t border-border">
        <p className="text-xs text-muted-foreground">
          Total: <span className="font-semibold text-foreground">${total_cost_usd.toFixed(2)}</span>
        </p>
      </div>

      {topSaving && topSaving.savings_percent > 10 && (
        <div className="rounded-lg bg-accent/5 border border-accent/20 p-3">
          <p className="text-xs font-medium text-accent">
            {topSaving.note}
          </p>
          <p className="text-[11px] text-muted-foreground mt-1">
            Estimated savings: ${topSaving.savings_usd.toFixed(0)}/month ({topSaving.savings_percent}%)
          </p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Recommendation Banner
// ---------------------------------------------------------------------------

function RecommendationBanner({
  recommendation,
  advisorRecs,
  totalSavings,
  onDismiss,
}: {
  recommendation: ProviderRecommendation | null;
  advisorRecs: AdvisorRecommendation[];
  totalSavings: number;
  onDismiss: () => void;
}) {
  // Prefer per-workflow advisor recommendations when available
  if (advisorRecs.length > 0) {
    return (
      <div className={cn(
        "rounded-2xl border border-accent/30 bg-accent/5",
        "p-4 space-y-3"
      )}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-accent shrink-0" />
            <p className="text-sm font-semibold text-foreground">
              Switch {advisorRecs.length} workflow{advisorRecs.length > 1 ? "s" : ""} and save ${totalSavings.toFixed(0)}/month
            </p>
          </div>
          <button
            onClick={onDismiss}
            className="text-muted-foreground hover:text-foreground transition-colors shrink-0"
            aria-label="Dismiss recommendation"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          {advisorRecs.map((rec) => (
            <div
              key={rec.workflow}
              className="rounded-lg bg-background/60 border border-border/50 p-2.5 flex flex-col gap-1"
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-foreground truncate">{rec.workflow}</span>
                <span className="text-xs font-semibold text-success shrink-0">
                  -${rec.savings_monthly.toFixed(0)}/mo
                </span>
              </div>
              <p className="text-[11px] text-muted-foreground">
                {rec.current_provider} → {rec.suggested_provider}
                {rec.eu_compliant && (
                  <span className="ml-1 text-accent" title="EU data residency">EU</span>
                )}
              </p>
            </div>
          ))}
        </div>
      </div>
    );
  }

  // Fallback to legacy single recommendation
  if (!recommendation) return null;
  return (
    <div className={cn(
      "rounded-2xl border border-accent/30 bg-accent/5",
      "p-4 flex items-center gap-3"
    )}>
      <Sparkles className="h-5 w-5 text-accent shrink-0" />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-foreground truncate">{recommendation.title}</p>
        <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{recommendation.description}</p>
      </div>
      {recommendation.estimated_savings_usd > 0 && (
        <span className="text-xs font-semibold text-success shrink-0">
          Save ${recommendation.estimated_savings_usd.toFixed(0)}/mo
        </span>
      )}
      <button
        onClick={onDismiss}
        className="text-muted-foreground hover:text-foreground transition-colors shrink-0"
        aria-label="Dismiss recommendation"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Provider auto-detection banner - shown on first visit / empty state
// ---------------------------------------------------------------------------

interface ProviderInfo {
  id: string;
  name: string;
  status: "running" | "configured" | "unconfigured";
  region: string;
  latency_ms: number | null;
}

const PROVIDER_BANNER_DISMISSED_KEY = "sandcastle_provider_banner_dismissed";

function ProviderStatusBanner() {
  const navigate = useNavigate();
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [dismissed, setDismissed] = useState(() => {
    try {
      return localStorage.getItem(PROVIDER_BANNER_DISMISSED_KEY) === "true";
    } catch {
      return false;
    }
  });
  const [apiKey, setApiKey] = useState("");

  useEffect(() => {
    let cancelled = false;
    const fetchProviders = async () => {
      try {
        const res = await api.get<{ providers: ProviderInfo[] }>("/health/providers");
        if (cancelled) return;
        if (res.data?.providers) {
          setProviders(res.data.providers);
        }
      } catch {
        // Non-critical - banner simply won't show
      } finally {
        if (!cancelled) setLoaded(true);
      }
    };
    void fetchProviders();
    return () => { cancelled = true; };
  }, []);

  if (dismissed || !loaded || providers.length === 0) return null;

  const handleDismiss = () => {
    setDismissed(true);
    try {
      localStorage.setItem(PROVIDER_BANNER_DISMISSED_KEY, "true");
    } catch { /* ignore */ }
  };

  const ollamaRunning = providers.find((p) => p.id === "ollama" && p.status === "running");
  const hasAnyActive = providers.some((p) => p.status === "running" || p.status === "configured");

  const handleApiKeySubmit = () => {
    if (apiKey.trim()) {
      navigate("/settings");
    }
  };

  return (
    <div className="bg-surface rounded-2xl shadow-sm border border-border px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0 flex-1">
          <Wifi className="h-4 w-4 text-accent shrink-0" />

          {/* Provider badges */}
          <div className="flex items-center gap-2 flex-wrap">
            {providers.map((p) => {
              const isActive = p.status === "running" || p.status === "configured";
              return (
                <span
                  key={p.id}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium",
                    isActive
                      ? "bg-success/10 text-success"
                      : "bg-muted text-muted-foreground"
                  )}
                >
                  <span
                    className={cn(
                      "h-1.5 w-1.5 rounded-full shrink-0",
                      isActive ? "bg-success" : "bg-muted-foreground/40"
                    )}
                  />
                  {p.name}
                  {p.status === "running" && p.latency_ms != null && (
                    <span className="text-[10px] text-success/70">{p.latency_ms}ms</span>
                  )}
                </span>
              );
            })}
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {/* Ollama detected message */}
          {ollamaRunning && (
            <span className="text-xs font-medium text-success whitespace-nowrap">
              Ollama detected - ready to use!
            </span>
          )}

          {/* API key input when no provider is active */}
          {!hasAnyActive && (
            <div className="flex items-center gap-1.5">
              <div className="relative">
                <Key className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-muted-foreground" />
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") handleApiKeySubmit(); }}
                  placeholder="Paste any API key to start"
                  className="h-7 w-48 rounded-lg border border-border bg-background pl-7 pr-2 text-xs text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:border-accent"
                />
              </div>
              <button
                onClick={handleApiKeySubmit}
                disabled={!apiKey.trim()}
                className={cn(
                  "h-7 px-3 rounded-lg text-xs font-medium transition-colors",
                  apiKey.trim()
                    ? "bg-accent text-accent-foreground hover:bg-accent/90"
                    : "bg-muted text-muted-foreground cursor-not-allowed"
                )}
              >
                Go
              </button>
            </div>
          )}

          <button
            onClick={handleDismiss}
            className="text-muted-foreground hover:text-foreground transition-colors"
            aria-label="Dismiss provider banner"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty state CTA - shown when the user has no workflows and no runs
// ---------------------------------------------------------------------------

const CHECKLIST_KEY = "sandcastle_getting_started";

interface ChecklistState {
  dismissed: boolean;
  completed: Record<string, boolean>;
}

function getChecklistState(): ChecklistState {
  try {
    const raw = localStorage.getItem(CHECKLIST_KEY);
    return raw ? JSON.parse(raw) : { dismissed: false, completed: {} };
  } catch {
    return { dismissed: false, completed: {} };
  }
}

function saveChecklistState(state: ChecklistState) {
  localStorage.setItem(CHECKLIST_KEY, JSON.stringify(state));
}

interface DemoRunResult {
  run_id: string;
  status: string;
  outputs: { generate: string; summarize: string };
  total_cost_usd: number;
}

function EmptyStateCTA() {
  const navigate = useNavigate();
  const [cls, setCls] = useState<ChecklistState>(getChecklistState);

  // Demo workflow state
  const [demoState, setDemoState] = useState<"idle" | "step1" | "step2" | "done" | "error">("idle");
  const [demoOutput, setDemoOutput] = useState<string | null>(null);

  const missions = [
    {
      id: "first-run",
      title: "Run your first workflow",
      desc: "One click. See real output in seconds.",
      time: "2 min",
      action: () => navigate("/templates?tab=packs"),
      cta: "Pick a template",
    },
    {
      id: "api-key",
      title: "Connect an AI provider",
      desc: "Paste your API key. Test the connection.",
      time: "1 min",
      action: () => navigate("/settings"),
      cta: "Open Settings",
    },
    {
      id: "build-pipeline",
      title: "Build a multi-step pipeline",
      desc: "Chain steps together: scrape, analyze, summarize.",
      time: "5 min",
      action: () => navigate("/workflows/builder"),
      cta: "Open Builder",
    },
  ];

  const quickRuns = [
    {
      id: "summarize",
      icon: "📝",
      title: "Summarize text",
      desc: "Paste any text, get a concise summary",
      template: "text-summarizer",
    },
    {
      id: "analyze-url",
      icon: "🔍",
      title: "Analyze a URL",
      desc: "Enter a URL, get structured analysis",
      template: "url-analyzer",
    },
    {
      id: "generate-code",
      icon: "💻",
      title: "Generate code",
      desc: "Describe what you need, get working code",
      template: "code-generator",
    },
    {
      id: "research",
      icon: "📊",
      title: "Research a topic",
      desc: "Enter a topic, get a structured report",
      template: "research-report",
    },
  ];

  const completedCount = Object.values(cls.completed).filter(Boolean).length;
  const allDone = completedCount >= missions.length;

  const toggleMission = (id: string) => {
    const next = {
      ...cls,
      completed: { ...cls.completed, [id]: !cls.completed[id] },
    };
    setCls(next);
    saveChecklistState(next);
  };

  const dismissChecklist = () => {
    const next = { ...cls, dismissed: true };
    setCls(next);
    saveChecklistState(next);
  };

  const runDemo = async () => {
    setDemoState("step1");
    setDemoOutput(null);
    try {
      await new Promise((r) => setTimeout(r, 1000));
      setDemoState("step2");

      const res = await api.post<DemoRunResult>("/workflows/run", {
        workflow_name: "demo-hello-world",
        workflow: {
          name: "demo-hello-world",
          description: "Your first Sandcastle workflow",
          default_model: "sonnet",
          steps: [
            { id: "generate", prompt: "Write 3 interesting facts about sandcastles." },
            { id: "summarize", prompt: "Summarize these facts in one sentence: {steps.generate.output}", depends_on: ["generate"] },
          ],
        },
      });

      if (res.data) {
        setDemoOutput(res.data.outputs?.summarize || "Workflow completed successfully.");
        setDemoState("done");
        // Auto-check "first-run" in checklist
        const next = { ...cls, completed: { ...cls.completed, "first-run": true } };
        setCls(next);
        saveChecklistState(next);
      } else {
        setDemoState("error");
      }
    } catch {
      setDemoState("error");
    }
  };

  return (
    <div className="space-y-4">
      {/* Getting Started checklist */}
      {!cls.dismissed && (
        <div className="bg-surface rounded-2xl shadow-sm border border-border p-6">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-accent/10">
                <Castle className="h-5 w-5 text-accent" />
              </div>
              <div>
                <h2 className="text-lg font-bold text-foreground tracking-tight">
                  Getting Started
                </h2>
                <p className="text-xs text-muted-foreground">
                  {allDone ? "All done! You're ready to build." : `${completedCount}/${missions.length} completed`}
                </p>
              </div>
            </div>
            <button
              onClick={dismissChecklist}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              Dismiss
            </button>
          </div>

          <div className="space-y-2">
            {missions.map((m) => {
              const done = cls.completed[m.id];
              return (
                <div
                  key={m.id}
                  className={cn(
                    "flex items-center gap-3 rounded-xl border p-3 transition-all",
                    done
                      ? "border-success/30 bg-success/5"
                      : "border-border hover:border-accent/30"
                  )}
                >
                  <button
                    onClick={() => toggleMission(m.id)}
                    className={cn(
                      "flex items-center justify-center w-6 h-6 rounded-full border-2 shrink-0 transition-colors cursor-pointer",
                      done
                        ? "bg-success border-success text-white"
                        : "border-border hover:border-accent"
                    )}
                  >
                    {done && <Check className="h-3.5 w-3.5" />}
                  </button>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className={cn(
                        "text-sm font-medium",
                        done ? "text-muted-foreground line-through" : "text-foreground"
                      )}>
                        {m.title}
                      </span>
                      <span className="text-[10px] text-muted-foreground/60">{m.time}</span>
                    </div>
                    <p className="text-xs text-muted-foreground">{m.desc}</p>
                  </div>
                  {!done && (
                    <button
                      onClick={m.action}
                      className="shrink-0 text-xs font-medium text-accent hover:text-accent-hover transition-colors cursor-pointer"
                    >
                      {m.cta} &rarr;
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Run Demo card */}
      <div className={cn(
        "bg-surface rounded-2xl shadow-sm border p-6",
        demoState === "done" ? "border-success/30" : "border-border",
      )}>
        <div className="flex items-start gap-4">
          <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-accent/10 shrink-0">
            <Rocket className="h-5 w-5 text-accent" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-lg font-bold text-foreground tracking-tight">
              Run Your First Workflow
            </h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              See Sandcastle in action. A 2-step demo that summarizes text.
            </p>

            <div className="mt-4">
              {demoState === "idle" && (
                <div className="flex items-center gap-3">
                  <button
                    onClick={() => void runDemo()}
                    className={cn(
                      "inline-flex items-center gap-2 rounded-lg bg-accent px-5 py-2 text-sm font-medium text-accent-foreground",
                      "hover:bg-accent-hover transition-all shadow-sm hover:shadow-md active:scale-[0.98] cursor-pointer"
                    )}
                  >
                    Run Demo
                    <ArrowRight className="h-4 w-4" />
                  </button>
                  <span className="text-xs text-muted-foreground">
                    Free with Ollama &middot; ~$0.01 with Claude
                  </span>
                </div>
              )}

              {demoState === "step1" && (
                <div className="flex items-center gap-2.5">
                  <Loader2 className="h-4 w-4 text-accent animate-spin" />
                  <span className="text-sm text-foreground">Running step 1 of 2 - Generating facts...</span>
                </div>
              )}

              {demoState === "step2" && (
                <div className="flex items-center gap-2.5">
                  <Loader2 className="h-4 w-4 text-accent animate-spin" />
                  <span className="text-sm text-foreground">Running step 2 of 2 - Summarizing...</span>
                </div>
              )}

              {demoState === "done" && demoOutput && (
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <CheckCircle className="h-4 w-4 text-success" />
                    <span className="text-sm font-semibold text-success">Done!</span>
                  </div>
                  <div className="rounded-lg bg-accent/5 border border-accent/20 p-3">
                    <p className="text-sm text-foreground leading-relaxed">{demoOutput}</p>
                  </div>
                </div>
              )}

              {demoState === "error" && (
                <div className="space-y-1.5">
                  <p className="text-sm text-error">Something went wrong.</p>
                  <button
                    onClick={() => void runDemo()}
                    className="text-xs font-medium text-accent hover:text-accent-hover transition-colors cursor-pointer"
                  >
                    Retry
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Quick Run cards */}
      <div className="bg-surface rounded-2xl shadow-sm border border-border p-6">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-lg font-bold text-foreground tracking-tight">Quick Start</h2>
            <p className="text-xs text-muted-foreground">
              Jump straight in. Each runs a pre-built workflow.
            </p>
          </div>
          <button
            onClick={() => navigate("/templates")}
            className="text-xs font-medium text-accent hover:text-accent-hover transition-colors"
          >
            Browse all templates &rarr;
          </button>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {quickRuns.map((qr) => (
            <button
              key={qr.id}
              onClick={() => navigate(`/templates?search=${qr.template}`)}
              className={cn(
                "text-left rounded-xl border border-border p-4",
                "hover:border-accent/40 hover:bg-accent/5",
                "transition-all duration-200 active:scale-[0.98] cursor-pointer group"
              )}
            >
              <span className="text-2xl">{qr.icon}</span>
              <h3 className="text-sm font-semibold text-foreground mt-2 group-hover:text-accent transition-colors">
                {qr.title}
              </h3>
              <p className="text-xs text-muted-foreground mt-1">{qr.desc}</p>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Failover Events widget
// ---------------------------------------------------------------------------

function BentoFailoverEvents({ data }: { data: FailoverEventsData }) {
  if (data.total_failovers_7d === 0) return null;

  const lastEvent = data.events[0];
  const lastTimeAgo = lastEvent ? formatRelativeTime(lastEvent.timestamp) : "";

  const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

  // Extract short reason label (e.g. "429 rate-limit" from "429 rate-limit from anthropic")
  const shortReason = (reason: string) => {
    const fromIdx = reason.lastIndexOf(" from ");
    return fromIdx > 0 ? reason.slice(0, fromIdx) : reason;
  };

  return (
    <div className={cn(
      "bg-surface rounded-2xl shadow-sm border border-border",
      "hover:border-accent/30 transition-all duration-300",
      "p-5 flex flex-col gap-3"
    )}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="h-8 w-8 rounded-xl bg-warning/10 flex items-center justify-center">
            <Shuffle className="h-4 w-4 text-warning" />
          </div>
          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              Provider Failovers
            </p>
            <p className="text-2xl font-bold text-foreground leading-none mt-0.5">
              {data.total_failovers_7d}
            </p>
          </div>
        </div>
        {data.total_cost_delta_7d > 0 && (
          <span className="inline-flex items-center gap-1 rounded-full bg-warning/10 border border-warning/20 px-2.5 py-1 text-[11px] font-semibold text-warning">
            <DollarSign className="h-3 w-3" />
            +{formatCost(data.total_cost_delta_7d)} extra
          </span>
        )}
      </div>

      {lastEvent && (
        <div className="rounded-xl bg-background border border-border px-3 py-2">
          <p className="text-sm text-foreground">
            <span className="text-muted-foreground">{lastTimeAgo}</span>
            {" - "}
            {capitalize(lastEvent.original_provider)}
            <span className="text-muted-foreground mx-1">&rarr;</span>
            {capitalize(lastEvent.failover_provider)}
          </p>
          <p className="text-xs text-muted-foreground mt-0.5">
            {shortReason(lastEvent.reason)}
          </p>
        </div>
      )}

      {data.events.length > 1 && (
        <p className="text-xs text-muted-foreground">
          +{data.events.length - 1} more in the last 7 days
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// OverviewBento page
// ---------------------------------------------------------------------------

const DISMISSED_REC_KEY = "sandcastle_dismissed_recommendation";
// Dismissals expire after 7 days so new recommendations surface automatically.
const DISMISSED_REC_TTL_MS = 7 * 24 * 60 * 60 * 1000;

function isDismissed(title: string): boolean {
  try {
    const raw = localStorage.getItem(DISMISSED_REC_KEY);
    if (!raw) return false;
    const map: Record<string, number> = JSON.parse(raw);
    const ts = map[title];
    if (!ts) return false;
    return Date.now() - ts < DISMISSED_REC_TTL_MS;
  } catch {
    return false;
  }
}

function dismissRec(title: string): void {
  try {
    const raw = localStorage.getItem(DISMISSED_REC_KEY);
    const map: Record<string, number> = raw ? JSON.parse(raw) : {};
    map[title] = Date.now();
    // Prune expired entries to avoid unbounded growth
    for (const key of Object.keys(map)) {
      if (Date.now() - map[key] >= DISMISSED_REC_TTL_MS) delete map[key];
    }
    localStorage.setItem(DISMISSED_REC_KEY, JSON.stringify(map));
  } catch { /* ignore */ }
}

export default function OverviewBento() {
  // -- Above-fold state (fetched immediately on mount) --
  const [stats, setStats] = useState<Stats | null>(null);
  const [recentRuns, setRecentRuns] = useState<RunItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const [sparklines, setSparklines] = useState<Record<string, SparklineData> | null>(null);
  const [anomalies, setAnomalies] = useState<AnomalyItem[]>([]);
  const [workflowCount, setWorkflowCount] = useState<number | null>(null);

  // -- Below-fold state (fetched lazily when scrolled into view) --
  const [heatmapCells, setHeatmapCells] = useState<HeatmapCell[]>([]);
  const [heatmapLoaded, setHeatmapLoaded] = useState(false);
  const [failoverData, setFailoverData] = useState<FailoverEventsData | null>(null);
  const [providerCosts, setProviderCosts] = useState<ProviderCostsData | null>(null);
  const [providerSavings, setProviderSavings] = useState<ProviderSavingsData | null>(null);
  const [topRecommendation, setTopRecommendation] = useState<ProviderRecommendation | null>(null);
  const [advisorRecs, setAdvisorRecs] = useState<AdvisorRecommendation[]>([]);
  const [advisorTotalSavings, setAdvisorTotalSavings] = useState(0);
  const [recDismissed, setRecDismissed] = useState<boolean>(false);
  const [belowFoldLoaded, setBelowFoldLoaded] = useState(false);

  // Gate below-fold fetches behind a 1-second timer so they never block
  // the initial render, even when sections start inside the viewport.
  const [belowFoldReady, setBelowFoldReady] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setBelowFoldReady(true), 1000);
    return () => clearTimeout(t);
  }, []);

  const advisor = useAdvisorContext();
  const { subscribe } = useEventStreamContext();
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Lazy-load sentinels for below-fold sections
  const [heatmapRef, heatmapVisible] = useLazyLoad();
  const [providerRef, providerVisible] = useLazyLoad();
  const [forecastRef, forecastVisible] = useLazyLoad();
  const [chartsRef, chartsVisible] = useLazyLoad();
  const [failoverRef, failoverVisible] = useLazyLoad();

  // Count distinct providers from cost data to decide visibility
  const uniqueProviderCount = useMemo(() => {
    if (!providerCosts) return 0;
    const providers = new Set(providerCosts.by_provider.map((p) => p.provider));
    return providers.size;
  }, [providerCosts]);

  // Hide provider costs / savings / recommendation when <= 1 provider
  const showProviderCosts = uniqueProviderCount >= 2;

  const fetchSparklines = useCallback(async () => {
    const res = await api.get<Record<string, ApiSparklineData>>("/stats/sparklines");
    if (res.data) {
      const normalised: Record<string, SparklineData> = {};
      for (const [key, val] of Object.entries(res.data)) {
        normalised[key] = normaliseSparkline(val);
      }
      setSparklines(normalised);
    }
  }, []);

  const fetchStats = useCallback(async () => {
    const [statsRes, runsRes] = await Promise.all([
      api.get<Stats>("/stats"),
      api.get<RunItem[]>("/runs", { limit: "5" }),
    ]);
    if (statsRes.data) setStats(statsRes.data);
    if (runsRes.data) setRecentRuns(runsRes.data);
  }, []);

  // Above-fold fetch: stats, runs, sparklines, anomalies, workflow count
  useEffect(() => {
    let cancelled = false;
    const fetchAboveFold = async () => {
      try {
        setError(null);
        const [statsRes, runsRes, sparklinesRes, anomaliesRes, wfRes] = await Promise.all([
          api.get<Stats>("/stats"),
          api.get<RunItem[]>("/runs", { limit: "5" }),
          api.get<Record<string, ApiSparklineData>>("/stats/sparklines"),
          api.get<AnomalyItem[]>("/stats/anomalies"),
          api.get<{ name: string }[]>("/workflows"),
        ]);
        if (cancelled) return;
        if (statsRes.data) setStats(statsRes.data);
        if (runsRes.data) setRecentRuns(runsRes.data);
        if (sparklinesRes.data) {
          const normalised: Record<string, SparklineData> = {};
          for (const [key, val] of Object.entries(sparklinesRes.data)) {
            normalised[key] = normaliseSparkline(val);
          }
          setSparklines(normalised);
        }
        if (anomaliesRes.data) setAnomalies(anomaliesRes.data);
        setWorkflowCount(Array.isArray(wfRes.data) ? wfRes.data.length : 0);
      } catch {
        if (cancelled) return;
        setError("Could not connect to the API server");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void fetchAboveFold();
    return () => { cancelled = true; };
  }, [retryCount]);

  // Below-fold fetch: heatmap (triggered when heatmap sentinel enters viewport
  // AND the 1-second below-fold gate has elapsed)
  useEffect(() => {
    if (!belowFoldReady || !heatmapVisible || heatmapLoaded) return;
    let cancelled = false;
    const fetchHeatmap = async () => {
      try {
        const heatmapRes = await api.get<HeatmapApiCell[]>("/stats/heatmap");
        if (cancelled) return;
        if (heatmapRes.data) {
          setHeatmapCells(heatmapRes.data.map(normaliseHeatmapCell));
        }
      } catch {
        // Heatmap is non-critical, fail silently
      } finally {
        if (!cancelled) setHeatmapLoaded(true);
      }
    };
    void fetchHeatmap();
    return () => { cancelled = true; };
  }, [belowFoldReady, heatmapVisible, heatmapLoaded]);

  // Below-fold fetch: provider costs, savings, recommendation banner
  // Gated on belowFoldReady (1s after mount) AND visibility of provider/forecast sections
  useEffect(() => {
    if (!belowFoldReady || belowFoldLoaded) return;
    if (!providerVisible && !forecastVisible) return;
    let cancelled = false;
    const fetchProviderData = async () => {
      try {
        const [costsRes, savingsRes, recRes] = await Promise.all([
          api.get<ProviderCostsData>("/stats/provider-costs"),
          api.get<ProviderSavingsData>("/stats/provider-savings"),
          api.get<{ recommendations: ProviderRecommendation[] }>("/stats/provider-recommendation"),
        ]);
        if (cancelled) return;
        if (costsRes.data) setProviderCosts(costsRes.data);
        if (savingsRes.data) setProviderSavings(savingsRes.data);
        if (recRes.data) {
          const high = recRes.data.recommendations?.find(
            (r) => r.severity === "high" && !isDismissed(r.title)
          ) ?? null;
          setTopRecommendation(high);
          // If no advisor recs have loaded yet either, mark as dismissed
          if (high === null && advisorRecs.length === 0) setRecDismissed(true);
        }
      } catch {
        // Provider data is non-critical, fail silently
      } finally {
        if (!cancelled) setBelowFoldLoaded(true);
      }
    };
    void fetchProviderData();
    return () => { cancelled = true; };
  }, [belowFoldReady, providerVisible, forecastVisible, belowFoldLoaded, advisorRecs.length]);

  // Below-fold fetch: failover events (only when the failover section is visible)
  const [failoverLoaded, setFailoverLoaded] = useState(false);
  useEffect(() => {
    if (!belowFoldReady || !failoverVisible || failoverLoaded) return;
    let cancelled = false;
    const fetchFailover = async () => {
      try {
        const failoverRes = await api.get<FailoverEventsData>("/stats/failover-events");
        if (cancelled) return;
        if (failoverRes.data) setFailoverData(failoverRes.data);
      } catch {
        // Failover data is non-critical, fail silently
      } finally {
        if (!cancelled) setFailoverLoaded(true);
      }
    };
    void fetchFailover();
    return () => { cancelled = true; };
  }, [belowFoldReady, failoverVisible, failoverLoaded]);

  // Below-fold fetch: advisor recommendations (only when provider section is visible)
  const [advisorRecsLoaded, setAdvisorRecsLoaded] = useState(false);
  useEffect(() => {
    if (!belowFoldReady || !providerVisible || advisorRecsLoaded) return;
    let cancelled = false;
    const fetchAdvisorRecs = async () => {
      try {
        const advisorRes = await api.get<AdvisorRecommendationsData>("/advisor/recommendations");
        if (cancelled) return;
        let hasAdvisorRecs = false;
        if (advisorRes.data?.recommendations?.length) {
          const dismissKey = `advisor-${advisorRes.data.total_potential_savings.toFixed(0)}`;
          if (!isDismissed(dismissKey)) {
            setAdvisorRecs(advisorRes.data.recommendations);
            setAdvisorTotalSavings(advisorRes.data.total_potential_savings);
            hasAdvisorRecs = true;
          }
        }
        // Update dismissed state once we know about both recommendation sources
        if (!hasAdvisorRecs && !topRecommendation) {
          setRecDismissed(true);
        }
      } catch {
        // Advisor recommendations are non-critical, fail silently
      } finally {
        if (!cancelled) setAdvisorRecsLoaded(true);
      }
    };
    void fetchAdvisorRecs();
    return () => { cancelled = true; };
  }, [belowFoldReady, providerVisible, advisorRecsLoaded, topRecommendation]);

  const dismissRecommendation = useCallback(() => {
    if (advisorRecs.length > 0) {
      dismissRec(`advisor-${advisorTotalSavings.toFixed(0)}`);
      setAdvisorRecs([]);
      setAdvisorTotalSavings(0);
    }
    if (topRecommendation) dismissRec(topRecommendation.title);
    setRecDismissed(true);
  }, [topRecommendation, advisorRecs, advisorTotalSavings]);

  // Refresh sparklines + stats when run.completed or run.failed SSE events arrive
  useEffect(() => {
    const unsub = subscribe("*", (event) => {
      if (["run.completed", "run.failed"].includes(event.type)) {
        if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
        refreshTimerRef.current = setTimeout(() => {
          void fetchSparklines();
          void fetchStats();
        }, 1000);
      }
    });
    return () => {
      unsub();
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
    };
  }, [subscribe, fetchSparklines, fetchStats]);

  if (loading) {
    return (
      <div className="space-y-4 sm:space-y-5">
        <div className="flex items-center justify-between">
          <Skeleton className="h-8 w-36 rounded-xl" />
          <Skeleton className="h-8 w-28 rounded-xl" />
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 sm:gap-5">
          <Skeleton className="h-56 rounded-2xl lg:col-span-2" />
          <Skeleton className="h-56 rounded-2xl" />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 sm:gap-5">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-28 rounded-2xl" />)}
        </div>
        <Skeleton className="h-36 rounded-2xl" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-bold tracking-tight text-foreground">Overview</h1>
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

  const successRate = stats?.success_rate != null ? Math.round(stats.success_rate * 100) : 0;
  const totalRuns = stats?.total_runs_today ?? 0;
  const totalCost = stats?.total_cost_today ?? 0;
  const avgDuration = stats?.avg_duration_seconds ?? 0;

  // Detect empty state: no workflows exist and no runs have ever been recorded
  const isEmpty = workflowCount === 0 && totalRuns === 0 && recentRuns.length === 0;

  if (isEmpty) {
    return (
      <div className="space-y-4 sm:space-y-5">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="flex items-center justify-center w-8 h-8 rounded-xl bg-accent">
              <Castle className="h-4 w-4 text-accent-foreground" />
            </div>
            <h1 className="text-xl font-bold tracking-tight text-foreground">Overview</h1>
          </div>
        </div>

        {/* Health Hero - kept visible, shows "No data yet" naturally */}
        <BentoHealthHero
          score={advisor.score}
          activeInsights={advisor.activeInsights}
          loading={advisor.loading}
          totalRuns={0}
          successRate={0}
          totalCost={0}
          avgDuration={0}
        />

        {/* Provider auto-detection banner */}
        <ProviderStatusBanner />

        {/* Empty state CTA */}
        <EmptyStateCTA />
      </div>
    );
  }

  return (
    <div className="space-y-4 sm:space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="flex items-center justify-center w-8 h-8 rounded-xl bg-accent">
            <Castle className="h-4 w-4 text-accent-foreground" />
          </div>
          <h1 className="text-xl font-bold tracking-tight text-foreground">Overview</h1>
        </div>
        {/* Layout switcher removed - bento is the only layout */}
      </div>

      {/* Recommendation Banner - per-workflow advisor recs or legacy single recommendation */}
      {!recDismissed && (advisorRecs.length > 0 || (topRecommendation && showProviderCosts)) && (
        <RecommendationBanner
          recommendation={topRecommendation}
          advisorRecs={advisorRecs}
          totalSavings={advisorTotalSavings}
          onDismiss={dismissRecommendation}
        />
      )}

      {/* Row 1: Command Center (2/3) + Quick Actions (1/3) - above fold */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 sm:gap-5">
        <div className="lg:col-span-2">
          <BentoHealthHero
            score={advisor.score}
            activeInsights={advisor.activeInsights}
            loading={advisor.loading}
            totalRuns={totalRuns}
            successRate={successRate}
            totalCost={totalCost}
            avgDuration={avgDuration}
          />
        </div>
        <div>
          <BentoQuickActions />
        </div>
      </div>

      {/* Row 2: 4 stat cards - above fold */}
      {stats && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 sm:gap-5">
          <BentoStatCard
            label="Runs Today"
            value={String(totalRuns)}
            icon={Activity}
            iconColor="text-accent"
            iconBg="bg-accent/10"
            spark={sparklines?.runs}
            positiveIsGood={true}
          />
          <BentoStatCard
            label="Success Rate"
            value={`${successRate}%`}
            icon={CheckCircle}
            iconColor="text-success"
            iconBg="bg-success/10"
            spark={sparklines?.rate}
            positiveIsGood={true}
          />
          <BentoStatCard
            label="Cost Today"
            value={formatCost(totalCost)}
            icon={DollarSign}
            iconColor="text-running"
            iconBg="bg-running/10"
            spark={sparklines?.cost}
            positiveIsGood={false}
          />
          <BentoStatCard
            label="Avg Duration"
            value={avgDuration > 0 ? `${Math.round(avgDuration)}s` : "n/a"}
            icon={Timer}
            iconColor="text-muted-foreground"
            iconBg="bg-border"
            spark={sparklines?.duration}
            positiveIsGood={false}
          />
        </div>
      )}

      {/* Row 3: Activity Heatmap (full width) - lazy loaded */}
      <div ref={heatmapRef}>
        {!heatmapLoaded ? (
          <Skeleton className="h-36 rounded-2xl" />
        ) : (
          <BentoActivityHeatmap cells={heatmapCells} />
        )}
      </div>

      {/* Row 3.5: Anomalies (shown only when present) - uses above-fold data */}
      {anomalies.length > 0 && <BentoAnomalies anomalies={anomalies} />}

      {/* Row 3.55: Failover Events (shown only when failovers > 0) - lazy loaded */}
      <div ref={failoverRef}>
        {failoverData && failoverData.total_failovers_7d > 0 && (
          <BentoFailoverEvents data={failoverData} />
        )}
      </div>

      {/* Row 3.6: Provider Cost card + savings - lazy loaded, hidden when <= 1 provider */}
      <div ref={providerRef}>
        {!belowFoldLoaded ? (
          <Skeleton className="h-48 rounded-2xl" />
        ) : showProviderCosts && providerCosts ? (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 sm:gap-5">
            <div className="lg:col-span-2">
              <BentoProviderCosts
                providerCosts={providerCosts}
                savings={providerSavings}
              />
            </div>
            {providerSavings && providerSavings.alternatives.length > 0 && (
              <div className={cn(
                "rounded-2xl border border-border bg-surface shadow-sm",
                "hover:border-accent/30 transition-all duration-300",
                "p-5 flex flex-col gap-3"
              )}>
                <h3 className="text-sm font-semibold text-foreground">Savings Opportunities</h3>
                <div className="space-y-3">
                  {providerSavings.alternatives.slice(0, 3).map((alt) => (
                    <div key={alt.provider} className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="text-xs font-medium text-foreground">
                          {alt.provider.charAt(0).toUpperCase() + alt.provider.slice(1)}
                          {alt.region === "eu" && (
                            <span className="ml-1 text-[10px] font-semibold text-success bg-success/10 rounded-full px-1.5 py-0.5">EU</span>
                          )}
                          {alt.region === "local" && (
                            <span className="ml-1 text-[10px] font-semibold text-muted-foreground bg-border rounded-full px-1.5 py-0.5">Local</span>
                          )}
                        </p>
                        <p className="text-[11px] text-muted-foreground mt-0.5">
                          ${alt.projected_cost_usd.toFixed(2)} projected
                        </p>
                      </div>
                      <span className="text-xs font-semibold text-success shrink-0">
                        -{alt.savings_percent}%
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : null}
      </div>

      {/* Row 4: Cost Forecast (2/3) + Recent Runs (1/3) - lazy loaded */}
      <div ref={forecastRef}>
        {!forecastVisible ? (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 sm:gap-5">
            <Skeleton className="h-56 rounded-2xl lg:col-span-2" />
            <Skeleton className="h-56 rounded-2xl" />
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 sm:gap-5">
            <div className="lg:col-span-2">
              <div className={cn(
                "bg-surface rounded-2xl shadow-sm border border-border",
                "hover:border-accent/30 transition-all duration-300",
                "[&>div]:rounded-2xl [&>div]:border-0 [&>div]:shadow-none [&>div]:bg-transparent"
              )}>
                <CostForecast />
              </div>
            </div>
            <div>
              <BentoRecentRuns runs={recentRuns} />
            </div>
          </div>
        )}
      </div>

      {/* Row 5: Runs chart + Cost chart - lazy loaded */}
      {stats && stats.runs_by_day?.length > 0 && (
        <div ref={chartsRef}>
          {!chartsVisible ? (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-5">
              <Skeleton className="h-56 rounded-2xl" />
              <Skeleton className="h-56 rounded-2xl" />
            </div>
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-5">
              <div className={cn(
                "bg-surface rounded-2xl shadow-sm border border-border",
                "hover:border-accent/30 transition-all duration-300",
                "[&>div]:rounded-2xl [&>div]:border-0 [&>div]:shadow-none [&>div]:bg-transparent"
              )}>
                <RunsChart data={stats.runs_by_day} />
              </div>
              <div className={cn(
                "bg-surface rounded-2xl shadow-sm border border-border",
                "hover:border-accent/30 transition-all duration-300",
                "[&>div]:rounded-2xl [&>div]:border-0 [&>div]:shadow-none [&>div]:bg-transparent"
              )}>
                {stats.cost_by_workflow && (
                  <CostChart data={stats.cost_by_workflow} />
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
