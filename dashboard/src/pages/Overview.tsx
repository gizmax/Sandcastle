import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Castle, Play, AlertTriangle, BarChart3, Star, CheckCircle, XCircle, Clock, Settings2, GripVertical } from "lucide-react";
import { api } from "@/api/client";
import { StatsCards } from "@/components/overview/StatsCards";
import { RunsChart } from "@/components/overview/RunsChart";
import { CostChart } from "@/components/overview/CostChart";
import { RecentRuns } from "@/components/overview/RecentRuns";
import { HealthHero } from "@/components/overview/HealthHero";
import { DashboardCustomizer } from "@/components/overview/DashboardCustomizer";
import { EmptyState } from "@/components/shared/EmptyState";
import { CostForecast } from "@/components/overview/CostForecast";
import { useAdvisorContext } from "@/hooks/useAdvisorContext";
import { useDashboardLayout, WIDGET_LABELS } from "@/hooks/useDashboardLayout";
import { usePinnedWorkflows } from "@/hooks/usePinnedWorkflows";
import { mockWorkflowStats } from "@/components/workflows/WorkflowCard";
import { Skeleton } from "@/components/ui/Skeleton";
import { cn } from "@/lib/utils";

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
// MOCK DATA: Replace with real API calls when backend supports sparkline/
// heatmap endpoints. Uses a deterministic seed based on current date so
// values stay stable within a single day.
// ---------------------------------------------------------------------------

/** Simple deterministic pseudo-random number generator (mulberry32). */
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
  const durationValues = mkSeries(45, 15);

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
    duration: { values: durationValues, trendPercent: trendPct(durationValues) },
  };
}

interface HeatmapCell {
  date: string; // YYYY-MM-DD
  count: number;
  dayOfWeek: number; // 0=Sun .. 6=Sat
}

function generateMockHeatmap(): HeatmapCell[] {
  const today = new Date();
  const seed = today.getFullYear() * 10000 + (today.getMonth() + 1) * 100 + today.getDate() + 99;
  const rng = seededRandom(seed);

  const cells: HeatmapCell[] = [];
  // Go back 12 weeks (84 days)
  for (let i = 83; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(d.getDate() - i);
    const dayOfWeek = d.getDay();
    // Weekends have fewer runs
    const isWeekend = dayOfWeek === 0 || dayOfWeek === 6;
    const maxRuns = isWeekend ? 8 : 30;
    const count = Math.floor(rng() * maxRuns);
    cells.push({
      date: d.toISOString().slice(0, 10),
      count,
      dayOfWeek,
    });
  }
  return cells;
}

// ---------------------------------------------------------------------------
// ActivityHeatmap - GitHub-style contribution graph
// ---------------------------------------------------------------------------

const DAY_LABELS = ["", "M", "", "W", "", "F", ""];

function getIntensityClass(count: number, maxCount: number): string {
  if (count === 0) return "bg-surface border border-border/50";
  const ratio = count / maxCount;
  if (ratio < 0.25) return "bg-accent/20";
  if (ratio < 0.5) return "bg-accent/40";
  if (ratio < 0.75) return "bg-accent/70";
  return "bg-accent";
}

function ActivityHeatmap({ cells }: { cells: HeatmapCell[] }) {
  const [tooltip, setTooltip] = useState<{ x: number; y: number; text: string } | null>(null);

  // Organize into weeks (columns). Each column has 7 rows (Sun=0 .. Sat=6).
  // We remap to Mon=0 .. Sun=6 for display: row = (dayOfWeek + 6) % 7
  const weeks: (HeatmapCell | null)[][] = [];
  let currentWeek: (HeatmapCell | null)[] = Array(7).fill(null);

  for (const cell of cells) {
    const row = (cell.dayOfWeek + 6) % 7; // Mon=0, Tue=1, ..., Sun=6
    if (row === 0 && currentWeek.some((c) => c !== null)) {
      weeks.push(currentWeek);
      currentWeek = Array(7).fill(null);
    }
    currentWeek[row] = cell;
  }
  if (currentWeek.some((c) => c !== null)) {
    weeks.push(currentWeek);
  }

  const maxCount = Math.max(1, ...cells.map((c) => c.count));

  // Month labels: find the first cell in each month transition
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
    <div className="rounded-xl border border-border bg-surface p-4 sm:p-5 shadow-sm">
      <h3 className="mb-3 text-sm font-medium text-foreground">Activity</h3>
      <div className="relative overflow-x-auto">
        {/* Month labels row */}
        <div className="flex" style={{ paddingLeft: "28px" }}>
          {weeks.map((_, col) => {
            const ml = monthLabels.find((m) => m.col === col);
            return (
              <div key={col} className="text-[10px] text-muted" style={{ width: "14px", minWidth: "14px" }}>
                {ml ? ml.label : ""}
              </div>
            );
          })}
        </div>

        <div className="flex gap-0">
          {/* Day labels */}
          <div className="flex flex-col gap-[2px] pr-1 pt-[2px]" style={{ width: "24px", minWidth: "24px" }}>
            {DAY_LABELS.map((label, i) => (
              <div
                key={i}
                className="flex h-[12px] items-center text-[10px] leading-none text-muted"
              >
                {label}
              </div>
            ))}
          </div>

          {/* Grid */}
          <div className="flex gap-[2px]">
            {weeks.map((week, col) => (
              <div key={col} className="flex flex-col gap-[2px]">
                {week.map((cell, row) => (
                  <div
                    key={row}
                    className={cn(
                      "h-[12px] w-[12px] rounded-[2px] transition-colors duration-100",
                      cell ? getIntensityClass(cell.count, maxCount) : "bg-transparent"
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

        {/* Legend */}
        <div className="mt-3 flex items-center gap-1.5 text-[10px] text-muted">
          <span>Less</span>
          <div className="h-[10px] w-[10px] rounded-[2px] bg-surface border border-border/50" />
          <div className="h-[10px] w-[10px] rounded-[2px] bg-accent/20" />
          <div className="h-[10px] w-[10px] rounded-[2px] bg-accent/40" />
          <div className="h-[10px] w-[10px] rounded-[2px] bg-accent/70" />
          <div className="h-[10px] w-[10px] rounded-[2px] bg-accent" />
          <span>More</span>
        </div>
      </div>

      {/* Tooltip (portal-free, fixed position) */}
      {tooltip && (
        <div
          className="pointer-events-none fixed z-50 rounded-md bg-foreground px-2 py-1 text-[11px] font-medium text-background shadow-lg"
          style={{
            left: tooltip.x,
            top: tooltip.y,
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
// QuickActions row
// ---------------------------------------------------------------------------

function QuickActions() {
  const navigate = useNavigate();

  return (
    <div className="flex flex-wrap gap-3">
      <button
        onClick={() => navigate("/workflows")}
        className={cn(
          "inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium",
          "bg-accent text-accent-foreground",
          "shadow-sm transition-all duration-200",
          "hover:bg-accent-hover hover:shadow-md",
          "active:scale-[0.98]"
        )}
      >
        <Play className="h-4 w-4" />
        Run Workflow
      </button>

      <button
        onClick={() => navigate("/runs?status=failed")}
        className={cn(
          "inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium",
          "border border-error/40 text-error bg-transparent",
          "transition-all duration-200",
          "hover:bg-error/10 hover:border-error/60",
          "active:scale-[0.98]"
        )}
      >
        <AlertTriangle className="h-4 w-4" />
        View Failures
      </button>

      <button
        onClick={() => navigate("/runs?sort=cost")}
        className={cn(
          "inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium",
          "border border-border text-foreground bg-transparent",
          "transition-all duration-200",
          "hover:bg-surface hover:border-border/80 hover:shadow-sm",
          "active:scale-[0.98]"
        )}
      >
        <BarChart3 className="h-4 w-4" />
        Cost Report
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pinned Workflows Widget
// ---------------------------------------------------------------------------

function PinnedWorkflowsWidget() {
  const navigate = useNavigate();
  const { pinnedWorkflows } = usePinnedWorkflows();

  if (pinnedWorkflows.length === 0) return null;

  return (
    <div className="rounded-xl border border-border bg-surface p-4 sm:p-5 shadow-sm">
      <div className="mb-3 flex items-center gap-2">
        <Star className="h-4 w-4 fill-current text-accent" />
        <h3 className="text-sm font-medium text-foreground">Pinned Workflows</h3>
      </div>
      <div className="space-y-2">
        {pinnedWorkflows.map((wfName) => {
          const stats = mockWorkflowStats(wfName);
          const healthColor =
            stats.successRate >= 90 ? "bg-success" :
            stats.successRate >= 70 ? "bg-warning" :
            "bg-error";
          return (
            <div
              key={wfName}
              className="flex items-center gap-3 rounded-lg border border-border/50 bg-background px-3 py-2.5"
            >
              <span className={cn("h-2 w-2 shrink-0 rounded-full", healthColor)} />
              <button
                onClick={() => navigate(`/workflows/${encodeURIComponent(wfName)}`)}
                className="min-w-0 flex-1 truncate text-left text-sm font-medium text-foreground hover:text-accent transition-colors"
              >
                {wfName}
              </button>
              <span className="flex items-center gap-1 text-xs text-muted shrink-0">
                {stats.lastRunStatus === "completed" && (
                  <CheckCircle className="h-3 w-3 text-success" />
                )}
                {stats.lastRunStatus === "failed" && (
                  <XCircle className="h-3 w-3 text-error" />
                )}
                {stats.lastRunStatus === "running" && (
                  <Clock className="h-3 w-3 text-running" />
                )}
                {!stats.lastRunStatus && (
                  <Clock className="h-3 w-3" />
                )}
                <span className="hidden sm:inline">
                  {stats.lastRunAgo ?? "Never"}
                </span>
              </span>
              <button
                onClick={() => navigate(`/workflows/${encodeURIComponent(wfName)}`)}
                className={cn(
                  "flex items-center gap-1 rounded-lg bg-accent px-2 py-1 text-xs font-medium text-accent-foreground",
                  "hover:bg-accent-hover transition-all duration-200 shadow-sm shrink-0"
                )}
              >
                <Play className="h-3 w-3" />
                Run
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Overview page
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// WidgetWrapper - draggable container for each widget in customize mode
// ---------------------------------------------------------------------------

function WidgetWrapper({
  id,
  customizing,
  onDragStart,
  onDragOver,
  onDrop,
  onDragEnd,
  isDragging,
  isOver,
  overEdge,
  children,
}: {
  id: string;
  customizing: boolean;
  onDragStart: (e: React.DragEvent, id: string) => void;
  onDragOver: (e: React.DragEvent, id: string) => void;
  onDrop: (e: React.DragEvent, id: string) => void;
  onDragEnd: () => void;
  isDragging: boolean;
  isOver: boolean;
  overEdge: "top" | "bottom" | null;
  children: React.ReactNode;
}) {
  return (
    <div
      data-widget-id={id}
      draggable={customizing}
      onDragStart={(e) => onDragStart(e, id)}
      onDragOver={(e) => onDragOver(e, id)}
      onDrop={(e) => onDrop(e, id)}
      onDragEnd={onDragEnd}
      className={cn(
        "relative transition-all",
        customizing && "rounded-xl ring-1 ring-border/60 ring-dashed",
        customizing && "cursor-grab active:cursor-grabbing",
        isDragging && "opacity-40",
        isOver && overEdge === "top" && "ring-t-2 border-t-2 border-t-accent",
        isOver && overEdge === "bottom" && "ring-b-2 border-b-2 border-b-accent",
      )}
    >
      {customizing && (
        <div className="absolute -top-2.5 left-3 z-10 flex items-center gap-1 rounded-md bg-surface border border-border px-1.5 py-0.5 text-[10px] font-medium text-muted shadow-sm">
          <GripVertical className="h-3 w-3" />
          {WIDGET_LABELS[id] ?? id}
        </div>
      )}
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Overview page
// ---------------------------------------------------------------------------

export default function Overview() {
  const navigate = useNavigate();
  const [stats, setStats] = useState<Stats | null>(null);
  const [recentRuns, setRecentRuns] = useState<RunItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const advisor = useAdvisorContext();
  const { widgets, moveWidget, toggleWidget, resetLayout } = useDashboardLayout();
  const [customizing, setCustomizing] = useState(false);
  const [showPanel, setShowPanel] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);

  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);
  const [overEdge, setOverEdge] = useState<"top" | "bottom" | null>(null);

  const mockSparklines = useMemo(() => generateMockSparklines(), []);
  const mockHeatmap = useMemo(() => generateMockHeatmap(), []);
  const costForecastRng = useMemo(() => {
    const today = new Date();
    const seed = today.getFullYear() * 10000 + (today.getMonth() + 1) * 100 + today.getDate() + 777;
    return seededRandom(seed);
  }, []);

  useEffect(() => {
    if (!showPanel) return;
    function handleClick(e: MouseEvent) {
      if (
        panelRef.current && !panelRef.current.contains(e.target as Node) &&
        btnRef.current && !btnRef.current.contains(e.target as Node)
      ) {
        setShowPanel(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [showPanel]);

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

  function handleDragStart(e: React.DragEvent, id: string) {
    setDragId(id);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", id);
  }

  function handleDragOver(e: React.DragEvent, id: string) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (id === dragId) { setOverId(null); setOverEdge(null); return; }
    const rect = e.currentTarget.getBoundingClientRect();
    const midY = rect.top + rect.height / 2;
    setOverId(id);
    setOverEdge(e.clientY < midY ? "top" : "bottom");
  }

  function handleDrop(e: React.DragEvent, id: string) {
    e.preventDefault();
    const srcId = e.dataTransfer.getData("text/plain");
    if (srcId && srcId !== id) moveWidget(srcId, id);
    setDragId(null); setOverId(null); setOverEdge(null);
  }

  function handleDragEnd() {
    setDragId(null); setOverId(null); setOverEdge(null);
  }

  if (loading) {
    return (
      <div className="space-y-4 sm:space-y-6">
        <Skeleton className="h-8 w-32" />
        <Skeleton className="h-20 w-full rounded-xl" />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24 rounded-xl" />
          ))}
        </div>
        <div className="flex gap-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-9 w-32 rounded-lg" />
          ))}
        </div>
        <Skeleton className="h-40 w-full rounded-xl" />
        <div className="grid grid-cols-1 gap-4 sm:gap-6 lg:grid-cols-2">
          <Skeleton className="h-56 rounded-xl" />
          <Skeleton className="h-56 rounded-xl" />
        </div>
        <Skeleton className="h-48 w-full rounded-xl" />
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <h1 className="mb-4 sm:mb-6 text-xl sm:text-2xl font-semibold tracking-tight text-foreground">Overview</h1>
        <div className="rounded-xl border border-error/30 bg-error/5 p-4">
          <p className="text-sm text-error">{error}</p>
          <button
            onClick={() => { setLoading(true); setRetryCount((c) => c + 1); }}
            className="mt-2 text-xs font-medium text-accent hover:text-accent/80 transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const hasHistoricalRuns = stats && (
    stats.total_runs_today > 0
    || stats.runs_by_day.length > 0
    || recentRuns.length > 0
  );

  if (!stats || !hasHistoricalRuns) {
    return (
      <div>
        <h1 className="mb-4 sm:mb-6 text-xl sm:text-2xl font-semibold tracking-tight text-foreground">Overview</h1>
        {stats && <StatsCards totalRuns={0} successRate={0} totalCost={0} avgDuration={0} />}
        <EmptyState
          icon={Castle}
          title="No runs yet"
          description="Create your first workflow to get started."
          action={{ label: "Create Workflow", onClick: () => navigate("/workflows/builder") }}
          className="mt-8"
        />
      </div>
    );
  }

  const sorted = [...widgets].sort((a, b) => a.order - b.order);

  const widgetRenderers: Record<string, () => React.ReactNode> = {
    "health-hero": () => (
      <HealthHero score={advisor.score} activeInsights={advisor.activeInsights} loading={advisor.loading} />
    ),
    stats: () => (
      <StatsCards
        totalRuns={stats.total_runs_today}
        successRate={stats.success_rate}
        totalCost={stats.total_cost_today}
        avgDuration={stats.avg_duration_seconds}
        sparklines={mockSparklines}
      />
    ),
    "quick-actions": () => <QuickActions />,
    pinned: () => <PinnedWorkflowsWidget />,
    heatmap: () => <ActivityHeatmap cells={mockHeatmap} />,
    "cost-forecast": () => <CostForecast rng={costForecastRng} />,
    charts: () => (
      <div className="grid grid-cols-1 gap-4 sm:gap-6 lg:grid-cols-2">
        <RunsChart data={stats.runs_by_day} />
        <CostChart data={stats.cost_by_workflow} />
      </div>
    ),
    "recent-runs": () => <RecentRuns runs={recentRuns} />,
  };

  return (
    <div className="space-y-4 sm:space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">Overview</h1>
        <div className="relative">
          <button
            ref={btnRef}
            onClick={() => {
              const next = !showPanel;
              setShowPanel(next);
              if (next) setCustomizing(true);
              else setCustomizing(false);
            }}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-all",
              customizing
                ? "bg-accent text-accent-foreground shadow-sm"
                : "text-muted hover:text-foreground hover:bg-surface border border-transparent hover:border-border",
            )}
            aria-label="Customize dashboard"
          >
            <Settings2 className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">{customizing ? "Done" : "Customize"}</span>
          </button>
          {showPanel && (
            <div ref={panelRef} className="absolute right-0 top-full z-40 mt-2">
              <DashboardCustomizer
                widgets={widgets}
                onMove={moveWidget}
                onToggle={toggleWidget}
                onReset={resetLayout}
              />
            </div>
          )}
        </div>
      </div>

      {sorted.map((w) => {
        if (!w.visible) return null;
        const render = widgetRenderers[w.id];
        if (!render) return null;
        return (
          <WidgetWrapper
            key={w.id}
            id={w.id}
            customizing={customizing}
            onDragStart={handleDragStart}
            onDragOver={handleDragOver}
            onDrop={handleDrop}
            onDragEnd={handleDragEnd}
            isDragging={dragId === w.id}
            isOver={overId === w.id}
            overEdge={overId === w.id ? overEdge : null}
          >
            {render()}
          </WidgetWrapper>
        );
      })}
    </div>
  );
}
