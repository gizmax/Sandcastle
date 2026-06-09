import { useCallback, useMemo, useRef, useState } from "react";
import { formatCost, formatDuration, cn } from "@/lib/utils";

/* ── Status colours (inline hex - no dependency on Tailwind class generation) ── */
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

interface PipelineVizProps {
  steps: Step[];
}

/* ── Tooltip ── */
interface TooltipData {
  x: number;
  y: number;
  step: Step;
}

function Tooltip({ data }: { data: TooltipData }) {
  const { step } = data;
  const color = statusColor(step.status);
  return (
    <div
      className="pointer-events-none fixed z-50 rounded-lg border border-border bg-surface px-3 py-2 shadow-lg"
      style={{ left: data.x, top: data.y, transform: "translate(-50%, -120%)" }}
    >
      <p className="text-xs font-semibold text-foreground truncate max-w-[200px]">
        {step.step_id}
      </p>
      <div className="mt-1 flex items-center gap-2 text-[10px] text-muted">
        <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
        <span>{STATUS_LABEL[step.status] ?? step.status}</span>
      </div>
      <div className="mt-1 flex gap-3 text-[10px] text-muted">
        <span>{formatCost(step.cost_usd)}</span>
        <span>{formatDuration(step.duration_seconds)}</span>
        {step.attempt > 1 && <span>attempt {step.attempt}</span>}
      </div>
      {step.error && (
        <p className="mt-1 max-w-[200px] truncate text-[10px] text-error">{step.error}</p>
      )}
    </div>
  );
}

/* ── Pipeline node ── */
function PipelineNode({
  step,
  index,
  onHover,
  onLeave,
  onClick,
}: {
  step: Step;
  index: number;
  onHover: (e: React.MouseEvent, step: Step) => void;
  onLeave: () => void;
  onClick: (stepId: string) => void;
}) {
  const color = statusColor(step.status);
  const isRunning = step.status === "running";
  const truncatedId =
    step.step_id.length > 14 ? step.step_id.slice(0, 12) + "..." : step.step_id;

  return (
    <div className="flex flex-col items-center gap-1 min-w-[56px]" style={{ animationDelay: `${index * 40}ms` }}>
      {/* Cost label */}
      <span className="font-mono text-[10px] text-muted leading-none h-3">
        {step.cost_usd > 0 ? formatCost(step.cost_usd) : ""}
      </span>

      {/* Node circle */}
      <button
        type="button"
        onClick={() => onClick(step.step_id)}
        onMouseEnter={(e) => onHover(e, step)}
        onMouseLeave={onLeave}
        className={cn(
          "relative h-7 w-7 rounded-full border-2 border-surface",
          "transition-settle cursor-pointer",
          "hover:scale-125 hover:shadow-md",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
          isRunning && "pipeline-node-pulse"
        )}
        style={{ backgroundColor: color }}
        aria-label={`Step ${step.step_id}: ${step.status}`}
      >
        {/* Status icon inside node */}
        {step.status === "completed" && (
          <svg className="absolute inset-0 m-auto h-3.5 w-3.5" viewBox="0 0 16 16" fill="none">
            <path d="M4 8.5L7 11.5L12 5" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
        {step.status === "failed" && (
          <svg className="absolute inset-0 m-auto h-3.5 w-3.5" viewBox="0 0 16 16" fill="none">
            <path d="M5 5L11 11M11 5L5 11" stroke="white" strokeWidth="2" strokeLinecap="round" />
          </svg>
        )}
        {isRunning && (
          <svg className="absolute inset-0 m-auto h-3 w-3 animate-spin" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="6" stroke="white" strokeWidth="2" strokeDasharray="28" strokeDashoffset="8" strokeLinecap="round" />
          </svg>
        )}
      </button>

      {/* Step name */}
      <span className="text-[10px] text-muted leading-tight text-center max-w-[72px] truncate">
        {truncatedId}
      </span>
    </div>
  );
}

/* ── Connector line between nodes ── */
function Connector({ fromStatus, toStatus, index }: { fromStatus: string; toStatus: string; index: number }) {
  // The connector colour follows the "from" step if completed, otherwise muted
  const fromDone = ["completed", "failed", "partial"].includes(fromStatus);
  const color = fromDone ? statusColor(fromStatus) : "var(--color-border)";
  const _toStatus = toStatus; // suppress unused warning
  void _toStatus;

  return (
    <div
      className="flex items-center self-center"
      style={{ animationDelay: `${index * 40 + 20}ms` }}
    >
      <div className="flex items-center" style={{ width: 28 }}>
        {/* Line */}
        <div
          className="h-0.5 flex-1 transition-colors duration-200"
          style={{ backgroundColor: color }}
        />
        {/* Arrow head */}
        <svg width="8" height="10" viewBox="0 0 8 10" className="flex-shrink-0">
          <path d="M0 0L8 5L0 10Z" fill={color} />
        </svg>
      </div>
    </div>
  );
}

/* ── Cost waterfall bar ── */
function CostWaterfall({ steps }: { steps: Step[] }) {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);
  const totalCost = useMemo(() => steps.reduce((s, st) => s + st.cost_usd, 0), [steps]);

  if (totalCost <= 0) return null;

  return (
    <div className="mt-3 space-y-1">
      <div className="flex items-center gap-2">
        <span className="text-[10px] font-medium text-muted-foreground">Cost breakdown</span>
        <span className="ml-auto font-mono text-[10px] text-muted">{formatCost(totalCost)}</span>
      </div>
      <div className="relative flex h-2.5 w-full overflow-hidden rounded-full bg-border/40">
        {steps.map((step, i) => {
          const pct = (step.cost_usd / totalCost) * 100;
          if (pct <= 0) return null;
          const isHovered = hoveredIdx === i;
          return (
            <div
              key={`${step.step_id}-${step.parallel_index ?? 0}-${i}`}
              className={cn(
                "h-full transition-opacity duration-200",
                isHovered ? "opacity-100" : hoveredIdx !== null ? "opacity-50" : "opacity-90"
              )}
              style={{
                width: `${pct}%`,
                backgroundColor: statusColor(step.status),
                minWidth: pct > 0 ? "2px" : undefined,
              }}
              onMouseEnter={() => setHoveredIdx(i)}
              onMouseLeave={() => setHoveredIdx(null)}
              title={`${step.step_id}: ${formatCost(step.cost_usd)}`}
            />
          );
        })}
      </div>
      {/* Hover label */}
      {hoveredIdx !== null && steps[hoveredIdx] && (
        <div className="flex items-center gap-1.5 text-[10px] text-muted">
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ backgroundColor: statusColor(steps[hoveredIdx].status) }}
          />
          <span className="font-medium text-foreground">{steps[hoveredIdx].step_id}</span>
          <span>{formatCost(steps[hoveredIdx].cost_usd)}</span>
          <span className="text-muted-foreground">
            ({((steps[hoveredIdx].cost_usd / totalCost) * 100).toFixed(1)}%)
          </span>
        </div>
      )}
    </div>
  );
}

/* ── Main component ── */
export function PipelineViz({ steps }: PipelineVizProps) {
  const [tooltip, setTooltip] = useState<TooltipData | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const handleHover = useCallback((e: React.MouseEvent, step: Step) => {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setTooltip({
      x: rect.left + rect.width / 2,
      y: rect.top,
      step,
    });
  }, []);

  const handleLeave = useCallback(() => {
    setTooltip(null);
  }, []);

  const handleClick = useCallback((stepId: string) => {
    // Find the step card in the timeline below and scroll to it
    const el = document.querySelector(`[data-step-id="${stepId}"]`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      // Brief highlight flash
      el.classList.add("ring-2", "ring-accent/50");
      setTimeout(() => el.classList.remove("ring-2", "ring-accent/50"), 1500);
    }
  }, []);

  if (!steps || steps.length === 0) return null;

  return (
    <div className="rounded-xl border border-border bg-surface p-3 sm:p-4 shadow-sm">
      <h2 className="mb-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
        Pipeline
      </h2>

      {/* Scrollable horizontal node chain */}
      <div
        ref={scrollRef}
        className="overflow-x-auto pb-1"
      >
        <div className="flex items-start gap-0 min-w-max px-1">
          {steps.map((step, i) => (
            <div key={`${step.step_id}-${step.parallel_index ?? 0}-${i}`} className="flex items-start">
              <PipelineNode
                step={step}
                index={i}
                onHover={handleHover}
                onLeave={handleLeave}
                onClick={handleClick}
              />
              {i < steps.length - 1 && (
                <Connector
                  fromStatus={step.status}
                  toStatus={steps[i + 1].status}
                  index={i}
                />
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Cost waterfall */}
      <CostWaterfall steps={steps} />

      {/* Tooltip */}
      {tooltip && <Tooltip data={tooltip} />}
    </div>
  );
}
