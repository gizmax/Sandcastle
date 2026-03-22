import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Play, AlertTriangle, BarChart3, Star, GitBranch,
  CheckCircle2, ArrowRight, Activity, DollarSign, CheckCircle,
  Castle, Timer, Sparkles, X,
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
import { cn, formatCost, formatRelativeTime } from "@/lib/utils";
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
          "flex items-center gap-3 rounded-xl px-4 py-3 w-full text-left",
          "bg-accent text-accent-foreground font-semibold",
          "hover:bg-accent-hover transition-all duration-200 active:scale-[0.98]"
        )}
      >
        <Play className="h-4 w-4 shrink-0" />
        <span className="text-sm font-semibold">Run Workflow</span>
      </button>

      <button
        onClick={() => navigate("/runs?status=failed")}
        className={cn(
          "flex items-center gap-3 rounded-xl px-4 py-3 w-full text-left",
          "border border-error/30 text-error",
          "hover:bg-error/10 hover:border-error/50",
          "transition-all duration-200 active:scale-[0.98]"
        )}
      >
        <AlertTriangle className="h-4 w-4 shrink-0" />
        <span className="text-sm font-semibold">View Failures</span>
      </button>

      <button
        onClick={() => navigate("/runs?sort=cost")}
        className={cn(
          "flex items-center gap-3 rounded-xl px-4 py-3 w-full text-left",
          "border border-border text-muted-foreground",
          "hover:border-accent/30 hover:text-foreground hover:bg-background",
          "transition-all duration-200 active:scale-[0.98]"
        )}
      >
        <BarChart3 className="h-4 w-4 shrink-0" />
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
  onDismiss,
}: {
  recommendation: ProviderRecommendation;
  onDismiss: () => void;
}) {
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
// OverviewBento page
// ---------------------------------------------------------------------------

const DISMISSED_REC_KEY = "sandcastle_dismissed_recommendation";

export default function OverviewBento() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [recentRuns, setRecentRuns] = useState<RunItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const [sparklines, setSparklines] = useState<Record<string, SparklineData> | null>(null);
  const [heatmapCells, setHeatmapCells] = useState<HeatmapCell[]>([]);
  const [anomalies, setAnomalies] = useState<AnomalyItem[]>([]);
  const [providerCosts, setProviderCosts] = useState<ProviderCostsData | null>(null);
  const [providerSavings, setProviderSavings] = useState<ProviderSavingsData | null>(null);
  const [topRecommendation, setTopRecommendation] = useState<ProviderRecommendation | null>(null);
  const [recDismissed, setRecDismissed] = useState<boolean>(() => {
    try { return !!localStorage.getItem(DISMISSED_REC_KEY); } catch { return false; }
  });
  const advisor = useAdvisorContext();
  const { subscribe } = useEventStreamContext();
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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

  useEffect(() => {
    let cancelled = false;
    const fetchData = async () => {
      try {
        setError(null);
        const [statsRes, runsRes, sparklinesRes, heatmapRes, anomaliesRes, costsRes, savingsRes, recRes] = await Promise.all([
          api.get<Stats>("/stats"),
          api.get<RunItem[]>("/runs", { limit: "5" }),
          api.get<Record<string, ApiSparklineData>>("/stats/sparklines"),
          api.get<HeatmapApiCell[]>("/stats/heatmap"),
          api.get<AnomalyItem[]>("/stats/anomalies"),
          api.get<ProviderCostsData>("/stats/provider-costs"),
          api.get<ProviderSavingsData>("/stats/provider-savings"),
          api.get<{ recommendations: ProviderRecommendation[] }>("/stats/provider-recommendation"),
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
        if (heatmapRes.data) {
          setHeatmapCells(heatmapRes.data.map(normaliseHeatmapCell));
        }
        if (anomaliesRes.data) setAnomalies(anomaliesRes.data);
        if (costsRes.data) setProviderCosts(costsRes.data);
        if (savingsRes.data) setProviderSavings(savingsRes.data);
        if (recRes.data) {
          const high = recRes.data.recommendations?.find((r) => r.severity === "high") ?? null;
          setTopRecommendation(high);
        }
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

  const dismissRecommendation = useCallback(() => {
    setRecDismissed(true);
    try { localStorage.setItem(DISMISSED_REC_KEY, "1"); } catch { /* ignore */ }
  }, []);

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

  const successRate = stats ? Math.round(stats.success_rate * 100) : 0;
  const totalRuns = stats?.total_runs_today ?? 0;
  const totalCost = stats?.total_cost_today ?? 0;
  const avgDuration = stats?.avg_duration_seconds ?? 0;

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

      {/* Recommendation Banner - shown only when high severity and not dismissed */}
      {topRecommendation && !recDismissed && (
        <RecommendationBanner
          recommendation={topRecommendation}
          onDismiss={dismissRecommendation}
        />
      )}

      {/* Row 1: Command Center (2/3) + Quick Actions (1/3) */}
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

      {/* Row 2: 4 stat cards */}
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

      {/* Row 3: Activity Heatmap (full width) */}
      <BentoActivityHeatmap cells={heatmapCells} />

      {/* Row 3.5: Anomalies (shown only when present) */}
      {anomalies.length > 0 && <BentoAnomalies anomalies={anomalies} />}

      {/* Row 3.6: Provider Cost card + savings (shown when cost data available) */}
      {providerCosts && (
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
      )}

      {/* Row 4: Cost Forecast (2/3) + Recent Runs (1/3) */}
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

      {/* Row 5: Runs chart + Cost chart */}
      {stats && stats.runs_by_day.length > 0 && (
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
  );
}
