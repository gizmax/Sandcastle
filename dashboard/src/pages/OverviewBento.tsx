import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Play, AlertTriangle, BarChart3, Star, GitBranch,
  CheckCircle2, ArrowRight, Activity, DollarSign, CheckCircle,
} from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "@/api/client";
import { RunsChart } from "@/components/overview/RunsChart";
import { CostChart } from "@/components/overview/CostChart";
import { CostForecast } from "@/components/overview/CostForecast";
import { RecentRuns } from "@/components/overview/RecentRuns";
import { ScoreRing } from "@/components/advisor/ScoreRing";
import { useAdvisorContext } from "@/hooks/useAdvisorContext";
import { usePinnedWorkflows } from "@/hooks/usePinnedWorkflows";
import { Skeleton } from "@/components/ui/Skeleton";
import { cn, formatCost } from "@/lib/utils";
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
  trendPercent: number;
}

interface LayoutSwitcherProps {
  layout: string;
  setLayout: (l: string) => void;
}

// ---------------------------------------------------------------------------
// Mock data helpers (same deterministic seed logic as Overview.tsx)
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

interface HeatmapCell {
  date: string;
  count: number;
  dayOfWeek: number;
}

function generateMockHeatmap(): HeatmapCell[] {
  const today = new Date();
  const seed = today.getFullYear() * 10000 + (today.getMonth() + 1) * 100 + today.getDate() + 99;
  const rng = seededRandom(seed);
  const cells: HeatmapCell[] = [];
  for (let i = 83; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(d.getDate() - i);
    const dayOfWeek = d.getDay();
    const isWeekend = dayOfWeek === 0 || dayOfWeek === 6;
    const maxRuns = isWeekend ? 8 : 30;
    const count = Math.floor(rng() * maxRuns);
    cells.push({ date: d.toISOString().slice(0, 10), count, dayOfWeek });
  }
  return cells;
}

// ---------------------------------------------------------------------------
// Sparkline (inline SVG)
// ---------------------------------------------------------------------------

function Sparkline({ values, color }: { values: number[]; color: string }) {
  if (values.length < 2) return null;
  const w = 56; const h = 20; const padding = 2;
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
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true" className="shrink-0 opacity-70">
      <polyline points={points} fill="none" stroke={color} strokeWidth="1.5"
        strokeLinecap="round" strokeLinejoin="round" />
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
      "inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[10px] font-medium",
      isGood ? "bg-emerald-100 text-emerald-700" : "bg-rose-100 text-rose-600"
    )}>
      {isUp ? "+" : ""}{Math.abs(percent).toFixed(1)}%
    </span>
  );
}

// ---------------------------------------------------------------------------
// Bento stat card
// ---------------------------------------------------------------------------

interface BentoStatCardProps {
  label: string;
  value: string;
  icon: React.ElementType;
  iconColor: string;
  iconBg: string;
  spark?: SparklineData;
  sparkColor?: string;
  positiveIsGood?: boolean;
}

function BentoStatCard({
  label, value, icon: Icon, iconColor, iconBg,
  spark, sparkColor = "#0d9488", positiveIsGood = true,
}: BentoStatCardProps) {
  return (
    <div className={cn(
      "bg-white rounded-2xl border border-black/[0.04]",
      "shadow-[0_1px_3px_rgba(0,0,0,0.04)] p-6",
      "hover:shadow-[0_4px_12px_rgba(0,0,0,0.06)] transition-all duration-300",
      "flex flex-col gap-3"
    )}>
      <div className="flex items-start justify-between">
        <div className={cn("h-9 w-9 rounded-xl flex items-center justify-center", iconBg)}>
          <Icon className={cn("h-4.5 w-4.5", iconColor)} style={{ height: "18px", width: "18px" }} />
        </div>
        {spark && (
          <TrendBadge percent={spark.trendPercent} positiveIsGood={positiveIsGood} />
        )}
      </div>
      <div className="flex items-end justify-between gap-2">
        <div>
          <p className="text-[11px] font-medium text-stone-400 uppercase tracking-wide mb-0.5">{label}</p>
          <p className="text-3xl font-bold text-stone-800 tracking-tight leading-none">{value}</p>
        </div>
        {spark && spark.values.length >= 2 && (
          <Sparkline values={spark.values} color={sparkColor} />
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Health Hero - large bento cell
// ---------------------------------------------------------------------------

const SEVERITY_DOT: Record<Severity, string> = {
  critical: "bg-rose-500",
  warning: "bg-amber-400",
  optimize: "bg-teal-500",
  discover: "bg-violet-400",
};

function BentoHealthHero({
  score, activeInsights, loading,
}: {
  score: number; activeInsights: Insight[]; loading: boolean;
}) {
  const actionable = activeInsights.filter((i) => i.severity !== "discover");
  const scoreColor = score >= 80 ? "text-emerald-600" : score >= 50 ? "text-amber-500" : "text-rose-500";
  const scoreLabel = score >= 80 ? "Healthy" : score >= 50 ? "Needs attention" : "Critical";

  return (
    <div className={cn(
      "bg-gradient-to-br from-teal-50 to-emerald-50 rounded-2xl",
      "border border-black/[0.04] ring-1 ring-teal-200/50",
      "shadow-[0_1px_3px_rgba(0,0,0,0.04)]",
      "hover:shadow-[0_4px_12px_rgba(0,0,0,0.06)] transition-all duration-300",
      "p-6 flex flex-col gap-4 h-full"
    )}>
      {/* Score row */}
      <div className="flex items-center gap-4">
        {loading ? (
          <div className="h-[120px] w-[120px] rounded-full bg-white/60 animate-pulse" />
        ) : (
          <ScoreRing score={score} size="lg" />
        )}
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wider text-teal-600 mb-1">
            Health Score
          </p>
          {loading ? (
            <div className="h-6 w-28 bg-white/60 rounded animate-pulse" />
          ) : (
            <p className={cn("text-xl font-bold", scoreColor)}>{scoreLabel}</p>
          )}
          {!loading && actionable.length === 0 && (
            <div className="mt-2 flex items-center gap-1.5 text-emerald-600">
              <CheckCircle2 className="h-4 w-4" />
              <span className="text-sm font-medium">All systems healthy</span>
            </div>
          )}
          {!loading && actionable.length > 0 && (
            <p className="mt-1 text-xs text-stone-500">
              {actionable.length} item{actionable.length > 1 ? "s" : ""} need attention
            </p>
          )}
        </div>
      </div>

      {/* Insights */}
      {!loading && actionable.length > 0 && (
        <div className="flex flex-col gap-1 flex-1">
          {actionable.slice(0, 4).map((insight) => (
            <Link
              key={insight.id}
              to={insight.link}
              className={cn(
                "flex items-center gap-2.5 rounded-xl px-3 py-2",
                "bg-white/60 hover:bg-white/90 transition-colors duration-150 text-sm"
              )}
            >
              <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", SEVERITY_DOT[insight.severity])} />
              <span className="flex-1 text-stone-700 truncate">{insight.title}</span>
              <ArrowRight className="h-3 w-3 text-stone-400 shrink-0" />
            </Link>
          ))}
          {actionable.length > 4 && (
            <p className="px-3 text-xs text-stone-400">+{actionable.length - 4} more</p>
          )}
        </div>
      )}

      {/* All healthy message */}
      {!loading && actionable.length === 0 && (
        <div className="flex-1 flex flex-col gap-2">
          {activeInsights.filter((i) => i.severity === "optimize").slice(0, 3).map((insight) => (
            <Link
              key={insight.id}
              to={insight.link}
              className={cn(
                "flex items-center gap-2.5 rounded-xl px-3 py-2",
                "bg-white/50 hover:bg-white/80 transition-colors duration-150 text-sm"
              )}
            >
              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-teal-400" />
              <span className="flex-1 text-stone-600 truncate">{insight.title}</span>
              <ArrowRight className="h-3 w-3 text-stone-400 shrink-0" />
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Quick Actions - bento cell
// ---------------------------------------------------------------------------

function BentoQuickActions() {
  const navigate = useNavigate();
  const { pinnedWorkflows } = usePinnedWorkflows();

  return (
    <div className={cn(
      "bg-white rounded-2xl border border-black/[0.04]",
      "shadow-[0_1px_3px_rgba(0,0,0,0.04)]",
      "hover:shadow-[0_4px_12px_rgba(0,0,0,0.06)] transition-all duration-300",
      "p-6 flex flex-col gap-3 h-full"
    )}>
      <p className="text-[11px] font-semibold uppercase tracking-wider text-stone-400 mb-1">
        Quick Actions
      </p>

      <button
        onClick={() => navigate("/workflows")}
        className={cn(
          "flex items-center gap-3 rounded-xl px-4 py-3 w-full text-left",
          "bg-teal-500 hover:bg-teal-600 text-white",
          "transition-all duration-200 active:scale-[0.98]",
          "shadow-[0_2px_8px_rgba(13,148,136,0.3)]"
        )}
      >
        <Play className="h-4 w-4 shrink-0" />
        <span className="text-sm font-semibold">Run Workflow</span>
      </button>

      <button
        onClick={() => navigate("/runs?status=failed")}
        className={cn(
          "flex items-center gap-3 rounded-xl px-4 py-3 w-full text-left",
          "bg-rose-50 hover:bg-rose-100 text-rose-600 border border-rose-100",
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
          "bg-stone-50 hover:bg-stone-100 text-stone-600 border border-stone-100",
          "transition-all duration-200 active:scale-[0.98]"
        )}
      >
        <BarChart3 className="h-4 w-4 shrink-0" />
        <span className="text-sm font-semibold">Cost Report</span>
      </button>

      {pinnedWorkflows.length > 0 && (
        <div className="mt-1 pt-3 border-t border-stone-100">
          <div className="flex items-center gap-2 mb-2">
            <Star className="h-3.5 w-3.5 text-amber-400 fill-amber-400" />
            <span className="text-[11px] font-semibold uppercase tracking-wide text-stone-400">Pinned</span>
          </div>
          <div className="space-y-1">
            {pinnedWorkflows.slice(0, 3).map((wfName) => (
              <button
                key={wfName}
                onClick={() => navigate(`/workflows/${encodeURIComponent(wfName)}`)}
                className={cn(
                  "flex items-center gap-2 w-full rounded-lg px-3 py-2",
                  "hover:bg-stone-50 transition-colors text-left"
                )}
              >
                <GitBranch className="h-3 w-3 text-stone-400 shrink-0" />
                <span className="text-sm text-stone-600 truncate">{wfName}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Activity Heatmap - bento style
// ---------------------------------------------------------------------------

const DAY_LABELS = ["", "M", "", "W", "", "F", ""];

function getBentoIntensityClass(count: number, maxCount: number): string {
  if (count === 0) return "bg-stone-100";
  const ratio = count / maxCount;
  if (ratio < 0.25) return "bg-teal-200";
  if (ratio < 0.5) return "bg-teal-400";
  if (ratio < 0.75) return "bg-teal-500";
  return "bg-teal-600";
}

function BentoActivityHeatmap({ cells }: { cells: HeatmapCell[] }) {
  const [tooltip, setTooltip] = useState<{ x: number; y: number; text: string } | null>(null);

  const weeks: (HeatmapCell | null)[][] = [];
  let currentWeek: (HeatmapCell | null)[] = Array(7).fill(null);

  for (const cell of cells) {
    const row = (cell.dayOfWeek + 6) % 7;
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
      "bg-white rounded-2xl border border-black/[0.04]",
      "shadow-[0_1px_3px_rgba(0,0,0,0.04)]",
      "hover:shadow-[0_4px_12px_rgba(0,0,0,0.06)] transition-all duration-300",
      "p-6"
    )}>
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-stone-700">Activity</h3>
        <div className="flex items-center gap-1.5 text-[10px] text-stone-400">
          <span>Less</span>
          <div className="h-[9px] w-[9px] rounded-[2px] bg-stone-100" />
          <div className="h-[9px] w-[9px] rounded-[2px] bg-teal-200" />
          <div className="h-[9px] w-[9px] rounded-[2px] bg-teal-400" />
          <div className="h-[9px] w-[9px] rounded-[2px] bg-teal-500" />
          <div className="h-[9px] w-[9px] rounded-[2px] bg-teal-600" />
          <span>More</span>
        </div>
      </div>

      <div className="relative overflow-x-auto">
        <div className="flex" style={{ paddingLeft: "28px" }}>
          {weeks.map((_, col) => {
            const ml = monthLabels.find((m) => m.col === col);
            return (
              <div key={col} className="text-[10px] text-stone-400" style={{ width: "14px", minWidth: "14px" }}>
                {ml ? ml.label : ""}
              </div>
            );
          })}
        </div>

        <div className="flex gap-0">
          <div className="flex flex-col gap-[2px] pr-1 pt-[2px]" style={{ width: "24px", minWidth: "24px" }}>
            {DAY_LABELS.map((label, i) => (
              <div key={i} className="flex h-[12px] items-center text-[10px] leading-none text-stone-400">
                {label}
              </div>
            ))}
          </div>

          <div className="flex gap-[2px]">
            {weeks.map((week, col) => (
              <div key={col} className="flex flex-col gap-[2px]">
                {week.map((cell, row) => (
                  <div
                    key={row}
                    className={cn(
                      "h-[12px] w-[12px] rounded-[2px] transition-colors duration-100 cursor-default",
                      cell ? getBentoIntensityClass(cell.count, maxCount) : "bg-transparent"
                    )}
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
          className="pointer-events-none fixed z-50 rounded-lg bg-stone-800 px-2.5 py-1.5 text-[11px] font-medium text-white shadow-xl"
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
// Layout switcher (shared between variants)
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
    <div className="flex items-center gap-0.5 rounded-lg border border-stone-200 bg-stone-50 p-0.5">
      {options.map((opt) => (
        <button
          key={opt.id}
          title={opt.title}
          onClick={() => {
            setLayout(opt.id);
            localStorage.setItem("sandcastle_overview_layout", opt.id);
          }}
          className={cn(
            "flex items-center justify-center rounded-md w-7 h-7 transition-all duration-150",
            layout === opt.id
              ? "bg-white shadow-sm text-teal-600"
              : "text-stone-400 hover:text-stone-600"
          )}
        >
          {opt.icon}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// OverviewBento page
// ---------------------------------------------------------------------------

export default function OverviewBento({
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
  const mockHeatmap = useMemo(() => generateMockHeatmap(), []);

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

  // Page wrapper - light bento gradient
  const pageClass = "min-h-full bg-gradient-to-br from-slate-50 to-stone-100 -m-4 sm:-m-6 p-4 sm:p-6";

  if (loading) {
    return (
      <div className={pageClass}>
        <div className="space-y-4 sm:space-y-5">
          <div className="flex items-center justify-between">
            <Skeleton className="h-8 w-36 rounded-xl" />
            <Skeleton className="h-8 w-28 rounded-lg" />
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
      </div>
    );
  }

  if (error) {
    return (
      <div className={pageClass}>
        <h1 className="mb-4 text-2xl font-bold tracking-tight text-stone-800">Overview</h1>
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
    );
  }

  const successRate = stats ? Math.round(stats.success_rate * 100) : 0;
  const totalRuns = stats?.total_runs_today ?? 0;
  const totalCost = stats?.total_cost_today ?? 0;

  return (
    <div className={pageClass}>
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <h1 className="text-2xl font-bold tracking-tight text-stone-800">Overview</h1>
        <LayoutSwitcher layout={layout} setLayout={setLayout} />
      </div>

      {/* Row 1: Health Hero (2/3) + Quick Actions (1/3) */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 sm:gap-5 mb-4 sm:mb-5">
        <div className="lg:col-span-2">
          <BentoHealthHero
            score={advisor.score}
            activeInsights={advisor.activeInsights}
            loading={advisor.loading}
          />
        </div>
        <div>
          <BentoQuickActions />
        </div>
      </div>

      {/* Row 2: 3 stat cards */}
      {stats && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 sm:gap-5 mb-4 sm:mb-5">
          <BentoStatCard
            label="Runs Today"
            value={String(totalRuns)}
            icon={Activity}
            iconColor="text-teal-600"
            iconBg="bg-teal-50"
            spark={mockSparklines.runs}
            sparkColor="#0d9488"
            positiveIsGood={true}
          />
          <BentoStatCard
            label="Success Rate"
            value={`${successRate}%`}
            icon={CheckCircle}
            iconColor="text-emerald-600"
            iconBg="bg-emerald-50"
            spark={mockSparklines.rate}
            sparkColor="#059669"
            positiveIsGood={true}
          />
          <BentoStatCard
            label="Cost Today"
            value={formatCost(totalCost)}
            icon={DollarSign}
            iconColor="text-amber-600"
            iconBg="bg-amber-50"
            spark={mockSparklines.cost}
            sparkColor="#f59e0b"
            positiveIsGood={false}
          />
        </div>
      )}

      {/* Row 3: Activity Heatmap (full width) */}
      <div className="mb-4 sm:mb-5">
        <BentoActivityHeatmap cells={mockHeatmap} />
      </div>

      {/* Row 4: Cost Forecast (2/3) + Recent Runs (1/3) */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 sm:gap-5 mb-4 sm:mb-5">
        <div className="lg:col-span-2">
          <div className={cn(
            "bg-white rounded-2xl border border-black/[0.04]",
            "shadow-[0_1px_3px_rgba(0,0,0,0.04)]",
            "hover:shadow-[0_4px_12px_rgba(0,0,0,0.06)] transition-all duration-300",
            "[&>div]:rounded-2xl [&>div]:border-0 [&>div]:shadow-none [&>div]:bg-transparent"
          )}>
            <CostForecast />
          </div>
        </div>
        <div className={cn(
          "bg-white rounded-2xl border border-black/[0.04]",
          "shadow-[0_1px_3px_rgba(0,0,0,0.04)]",
          "hover:shadow-[0_4px_12px_rgba(0,0,0,0.06)] transition-all duration-300",
          "overflow-hidden",
          "[&>div]:rounded-none [&>div]:border-0 [&>div]:shadow-none"
        )}>
          <RecentRuns runs={recentRuns} />
        </div>
      </div>

      {/* Row 5: Runs chart + Cost chart */}
      {stats && stats.runs_by_day.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-5">
          <div className={cn(
            "bg-white rounded-2xl border border-black/[0.04]",
            "shadow-[0_1px_3px_rgba(0,0,0,0.04)]",
            "hover:shadow-[0_4px_12px_rgba(0,0,0,0.06)] transition-all duration-300",
            "[&>div]:rounded-2xl [&>div]:border-0 [&>div]:shadow-none [&>div]:bg-transparent"
          )}>
            <RunsChart data={stats.runs_by_day} />
          </div>
          <div className={cn(
            "bg-white rounded-2xl border border-black/[0.04]",
            "shadow-[0_1px_3px_rgba(0,0,0,0.04)]",
            "hover:shadow-[0_4px_12px_rgba(0,0,0,0.06)] transition-all duration-300",
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
