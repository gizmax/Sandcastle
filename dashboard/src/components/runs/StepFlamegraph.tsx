import { useCallback, useMemo, useState } from "react";
import { formatCost, formatDuration, cn } from "@/lib/utils";

/* ── Status colours (same palette as PipelineViz) ── */
const STATUS_HEX: Record<string, string> = {
  completed: "#22C55E",
  failed: "#EF4444",
  running: "#3B82F6",
  queued: "#A78BFA",
  pending: "#A3A3A3",
  skipped: "#F59E0B",
  cancelled: "#A3A3A3",
  partial: "#F59E0B",
  budget_exceeded: "#F59E0B",
  awaiting_approval: "#F59E0B",
};

const STATUS_LABEL: Record<string, string> = {
  completed: "Completed",
  failed: "Failed",
  running: "Running",
  queued: "Queued",
  pending: "Pending",
  skipped: "Skipped",
  cancelled: "Cancelled",
  partial: "Partial",
  budget_exceeded: "Budget exceeded",
  awaiting_approval: "Awaiting approval",
};

function statusColor(status: string): string {
  return STATUS_HEX[status] ?? "#A3A3A3";
}

/* ── Types ── */
interface Step {
  step_id: string;
  parallel_index: number | null;
  status: string;
  cost_usd: number;
  duration_seconds: number;
  attempt: number;
  error: string | null;
  started_at: string | null;
}

interface StepFlamegraphProps {
  steps: Step[];
}

/* ── Computed bar data ── */
interface BarData {
  step: Step;
  startOffset: number;  // seconds from run start
  duration: number;     // seconds
  row: number;          // vertical row index
  parallelGroupLabel: string | null;
}

interface TooltipData {
  x: number;
  y: number;
  bar: BarData;
}

/**
 * Calculate start times and row assignments for steps.
 *
 * Uses real started_at timestamps when available (relative to first step).
 * Falls back to summing durations sequentially when timestamps are missing.
 */
function computeBars(steps: Step[]): { bars: BarData[]; totalDuration: number } {
  if (steps.length === 0) return { bars: [], totalDuration: 0 };

  // Determine if we can use real timestamps
  const firstStarted = steps.find((s) => s.started_at)?.started_at;
  const hasRealTimestamps = firstStarted != null && steps.filter((s) => s.started_at).length > steps.length / 2;
  const runStartMs = firstStarted ? new Date(firstStarted).getTime() : 0;

  const bars: BarData[] = [];
  let cursor = 0; // current time offset in seconds

  // Group consecutive steps that share a non-null parallel_index
  let i = 0;
  while (i < steps.length) {
    const step = steps[i];

    // Use real timestamp if available, otherwise fallback to cursor
    const stepStart = hasRealTimestamps && step.started_at
      ? (new Date(step.started_at).getTime() - runStartMs) / 1000
      : cursor;

    // Check if this step starts a parallel group
    if (step.parallel_index !== null) {
      // Collect consecutive steps with the SAME parallel_index value
      const group: Step[] = [step];
      const groupIndex = step.parallel_index;
      let j = i + 1;
      while (j < steps.length && steps[j].parallel_index === groupIndex) {
        group.push(steps[j]);
        j++;
      }

      // All parallel steps start at the same time, each on its own row
      const baseRow = bars.length > 0 ? bars[bars.length - 1].row + 1 : 0;
      let maxEnd = stepStart;

      for (let k = 0; k < group.length; k++) {
        const s = group[k];
        const dur = Math.max(s.duration_seconds, 0.1);
        const sStart = hasRealTimestamps && s.started_at
          ? (new Date(s.started_at).getTime() - runStartMs) / 1000
          : stepStart;
        bars.push({
          step: s,
          startOffset: sStart,
          duration: dur,
          row: baseRow + k,
          parallelGroupLabel:
            k === 0 && group.length > 1
              ? `Parallel group (${group.length} steps)`
              : null,
        });
        maxEnd = Math.max(maxEnd, sStart + dur);
      }

      cursor = maxEnd;
      i = j;
    } else {
      // Sequential step
      const dur = Math.max(step.duration_seconds, 0.1);
      const row = bars.length > 0 ? bars[bars.length - 1].row + 1 : 0;
      bars.push({
        step,
        startOffset: stepStart,
        duration: dur,
        row,
        parallelGroupLabel: null,
      });
      cursor = stepStart + dur;
      i++;
    }
  }

  return { bars, totalDuration: cursor };
}

/**
 * Generate nice time ruler ticks.
 */
function generateTicks(totalDuration: number): number[] {
  if (totalDuration <= 0) return [0];

  // Pick a sensible interval
  let interval: number;
  if (totalDuration <= 10) interval = 1;
  else if (totalDuration <= 30) interval = 5;
  else if (totalDuration <= 60) interval = 10;
  else if (totalDuration <= 300) interval = 30;
  else if (totalDuration <= 600) interval = 60;
  else interval = Math.ceil(totalDuration / 10 / 60) * 60;

  const ticks: number[] = [];
  for (let t = 0; t <= totalDuration; t += interval) {
    ticks.push(t);
  }
  // Always include the last tick if not already there
  if (ticks[ticks.length - 1] < totalDuration) {
    ticks.push(totalDuration);
  }
  return ticks;
}

function formatTickLabel(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return s > 0 ? `${m}m${s}s` : `${m}m`;
}

/* ── Constants ── */
const BAR_HEIGHT = 28;
const BAR_GAP = 4;
const RULER_HEIGHT = 24;
const MIN_CHART_WIDTH = 400;
const MIN_TEXT_WIDTH = 60; // minimum bar width to show text inside

/* ── Tooltip component ── */
function FlamegraphTooltip({ data }: { data: TooltipData }) {
  const { bar } = data;
  const color = statusColor(bar.step.status);

  return (
    <div
      className="pointer-events-none fixed z-50 rounded-lg border border-border bg-surface px-3 py-2 shadow-lg"
      style={{ left: data.x, top: data.y, transform: "translate(-50%, -120%)" }}
    >
      <p className="text-xs font-semibold text-foreground truncate max-w-[220px]">
        {bar.step.step_id}
      </p>
      <div className="mt-1 flex items-center gap-2 text-[10px] text-muted">
        <span
          className="inline-block h-2 w-2 rounded-full"
          style={{ backgroundColor: color }}
        />
        <span>{STATUS_LABEL[bar.step.status] ?? bar.step.status}</span>
      </div>
      <div className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-0.5 text-[10px]">
        <span className="text-muted-foreground">Start</span>
        <span className="text-foreground font-mono">
          {formatTickLabel(bar.startOffset)}
        </span>
        <span className="text-muted-foreground">Duration</span>
        <span className="text-foreground font-mono">
          {formatDuration(bar.duration)}
        </span>
        <span className="text-muted-foreground">Cost</span>
        <span className="text-foreground font-mono">
          {formatCost(bar.step.cost_usd)}
        </span>
        {bar.step.attempt > 1 && (
          <>
            <span className="text-muted-foreground">Attempt</span>
            <span className="text-foreground font-mono">{bar.step.attempt}</span>
          </>
        )}
      </div>
      {bar.step.error && (
        <p className="mt-1 max-w-[220px] truncate text-[10px] text-error">
          {bar.step.error}
        </p>
      )}
    </div>
  );
}

/* ── Main component ── */
export function StepFlamegraph({ steps }: StepFlamegraphProps) {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);
  const [tooltip, setTooltip] = useState<TooltipData | null>(null);

  const { bars, totalDuration } = useMemo(() => computeBars(steps), [steps]);
  const ticks = useMemo(() => generateTicks(totalDuration), [totalDuration]);

  const maxRow = useMemo(
    () => (bars.length > 0 ? Math.max(...bars.map((b) => b.row)) : 0),
    [bars]
  );

  const chartHeight = (maxRow + 1) * (BAR_HEIGHT + BAR_GAP) + RULER_HEIGHT + 8;

  const handleBarHover = useCallback(
    (e: React.MouseEvent, bar: BarData, idx: number) => {
      const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
      setTooltip({
        x: rect.left + rect.width / 2,
        y: rect.top,
        bar,
      });
      setHoveredIdx(idx);
    },
    []
  );

  const handleBarLeave = useCallback(() => {
    setTooltip(null);
    setHoveredIdx(null);
  }, []);

  const handleBarClick = useCallback((stepId: string) => {
    // Scroll to the step card in the StepTimeline below
    const el = document.querySelector(`[data-step-id="${stepId}"]`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("ring-2", "ring-accent/50");
      setTimeout(() => el.classList.remove("ring-2", "ring-accent/50"), 1500);
    }
  }, []);

  if (bars.length === 0) return null;

  // Collect parallel group labels by row for rendering
  const groupLabels = bars
    .filter((b) => b.parallelGroupLabel)
    .map((b) => ({ row: b.row, label: b.parallelGroupLabel! }));

  return (
    <div className="relative overflow-x-auto">
      <div
        className="relative"
        style={{
          minWidth: MIN_CHART_WIDTH,
          height: chartHeight,
        }}
      >
        {/* ── Time ruler (top) ── */}
        <div
          className="relative"
          style={{ height: RULER_HEIGHT }}
        >
          {ticks.map((t) => {
            const pct = totalDuration > 0 ? (t / totalDuration) * 100 : 0;
            return (
              <span
                key={t}
                className="absolute text-[10px] font-mono text-muted-foreground select-none"
                style={{
                  left: `${pct}%`,
                  top: 4,
                  transform: pct > 95 ? "translateX(-100%)" : pct > 5 ? "translateX(-50%)" : "none",
                }}
              >
                {formatTickLabel(t)}
              </span>
            );
          })}
        </div>

        {/* ── Grid lines ── */}
        {ticks.map((t) => {
          const pct = totalDuration > 0 ? (t / totalDuration) * 100 : 0;
          return (
            <div
              key={`grid-${t}`}
              className="absolute top-0 border-l border-border/30"
              style={{
                left: `${pct}%`,
                height: chartHeight,
              }}
            />
          );
        })}

        {/* ── Parallel group labels ── */}
        {groupLabels.map(({ row, label }) => {
          const yPos = RULER_HEIGHT + row * (BAR_HEIGHT + BAR_GAP) - 2;
          return (
            <span
              key={`group-${row}`}
              className="absolute text-[9px] font-medium text-accent select-none"
              style={{
                left: 0,
                top: yPos - 10,
              }}
            >
              {label}
            </span>
          );
        })}

        {/* ── Bars ── */}
        {bars.map((bar, idx) => {
          const leftPct =
            totalDuration > 0
              ? (bar.startOffset / totalDuration) * 100
              : 0;
          const widthPct =
            totalDuration > 0
              ? (bar.duration / totalDuration) * 100
              : 100;
          const yPos =
            RULER_HEIGHT + bar.row * (BAR_HEIGHT + BAR_GAP);

          const color = statusColor(bar.step.status);
          const isHovered = hoveredIdx === idx;
          const isDimmed = hoveredIdx !== null && hoveredIdx !== idx;

          // Check if bar is wide enough for text
          // We approximate: if widthPct > some threshold
          const showText = widthPct > (MIN_TEXT_WIDTH / MIN_CHART_WIDTH) * 100;
          const showCost =
            bar.step.cost_usd > 0 &&
            widthPct > (MIN_TEXT_WIDTH * 2) / MIN_CHART_WIDTH * 100;

          const truncatedId =
            bar.step.step_id.length > 20
              ? bar.step.step_id.slice(0, 18) + "..."
              : bar.step.step_id;

          return (
            <div
              key={`${bar.step.step_id}-${bar.step.parallel_index ?? 0}-${idx}`}
              className={cn(
                "absolute flex items-center justify-between rounded-md cursor-pointer",
                "transition-settle",
                isHovered && "brightness-125 shadow-md z-10",
                isDimmed && "opacity-50"
              )}
              style={{
                left: `${leftPct}%`,
                width: `${Math.max(widthPct, 0.3)}%`,
                top: yPos,
                height: BAR_HEIGHT,
                backgroundColor: color,
                minWidth: 4,
              }}
              onMouseEnter={(e) => handleBarHover(e, bar, idx)}
              onMouseLeave={handleBarLeave}
              onClick={() => handleBarClick(bar.step.step_id)}
              title={
                !showText
                  ? `${bar.step.step_id} - ${formatDuration(bar.duration)}`
                  : undefined
              }
            >
              {showText && (
                <span className="px-2 text-[11px] font-medium text-white truncate leading-none">
                  {truncatedId}
                </span>
              )}
              {showCost && (
                <span className="px-2 text-[10px] font-mono text-white/80 leading-none flex-shrink-0">
                  {formatCost(bar.step.cost_usd)}
                </span>
              )}
            </div>
          );
        })}

        {/* ── Total duration label ── */}
        <div
          className="absolute text-[10px] font-mono text-muted-foreground select-none"
          style={{
            right: 0,
            top: chartHeight - 16,
          }}
        >
          Total: {formatDuration(totalDuration)}
        </div>
      </div>

      {/* ── Tooltip ── */}
      {tooltip && <FlamegraphTooltip data={tooltip} />}
    </div>
  );
}
