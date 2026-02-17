import { cn, formatCost, formatDuration } from "@/lib/utils";
import { ArrowDown, ArrowUp, Minus } from "lucide-react";

interface StepDiffProps {
  step_id: string;
  parallel_index: number | null;
  presence: string;
  config_a: Record<string, unknown> | null;
  config_b: Record<string, unknown> | null;
  config_changed: boolean;
  output_a: unknown;
  output_b: unknown;
  output_changed: boolean;
  cost_a: number;
  cost_b: number;
  cost_delta: number;
  duration_a: number;
  duration_b: number;
  duration_delta: number;
  status_a: string | null;
  status_b: string | null;
  error_a: string | null;
  error_b: string | null;
}

function DeltaIndicator({ delta, unit, invert }: { delta: number; unit: string; invert?: boolean }) {
  if (Math.abs(delta) < 0.001) {
    return <span className="text-muted text-xs flex items-center gap-0.5"><Minus className="h-3 w-3" />same</span>;
  }
  // For cost/duration: negative = better (less cost/time), positive = worse
  const better = invert ? delta > 0 : delta < 0;
  return (
    <span className={cn("text-xs font-medium flex items-center gap-0.5", better ? "text-success" : "text-error")}>
      {better ? <ArrowDown className="h-3 w-3" /> : <ArrowUp className="h-3 w-3" />}
      {delta > 0 ? "+" : ""}{unit === "$" ? formatCost(Math.abs(delta)) : formatDuration(Math.abs(delta))}
    </span>
  );
}

function StatusDot({ status }: { status: string | null }) {
  if (!status) return <span className="text-xs text-muted">-</span>;
  const colors: Record<string, string> = {
    completed: "bg-success",
    failed: "bg-error",
    running: "bg-running",
    pending: "bg-muted",
    skipped: "bg-muted",
  };
  return (
    <span className="inline-flex items-center gap-1 text-xs capitalize">
      <span className={cn("h-1.5 w-1.5 rounded-full", colors[status] || "bg-muted")} />
      {status}
    </span>
  );
}

export function StepDiffCard({ step }: { step: StepDiffProps }) {
  const isOnlyA = step.presence === "only_a";
  const isOnlyB = step.presence === "only_b";

  return (
    <div
      className={cn(
        "rounded-xl border bg-surface p-4 shadow-sm",
        isOnlyA ? "border-error/30 bg-error/5" : isOnlyB ? "border-success/30 bg-success/5" : "border-border"
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-semibold text-foreground">{step.step_id}</span>
          {step.parallel_index !== null && (
            <span className="rounded bg-accent/10 px-1.5 py-0.5 text-xs text-accent">
              [{step.parallel_index}]
            </span>
          )}
          {isOnlyA && <span className="rounded bg-error/15 px-1.5 py-0.5 text-xs text-error">Only in Run A</span>}
          {isOnlyB && <span className="rounded bg-success/15 px-1.5 py-0.5 text-xs text-success">Only in Run B</span>}
        </div>
        <div className="flex items-center gap-3">
          <StatusDot status={step.status_a} />
          <span className="text-muted text-xs">vs</span>
          <StatusDot status={step.status_b} />
        </div>
      </div>

      {/* Metrics */}
      <div className="grid grid-cols-2 gap-4 mb-3">
        <div className="rounded-lg bg-background/50 p-3">
          <div className="text-xs text-muted-foreground mb-1">Cost</div>
          <div className="flex items-center justify-between">
            <div className="flex items-baseline gap-2">
              <span className="text-sm font-medium">{formatCost(step.cost_a)}</span>
              <span className="text-muted text-xs">vs</span>
              <span className="text-sm font-medium">{formatCost(step.cost_b)}</span>
            </div>
            <DeltaIndicator delta={step.cost_delta} unit="$" />
          </div>
        </div>
        <div className="rounded-lg bg-background/50 p-3">
          <div className="text-xs text-muted-foreground mb-1">Duration</div>
          <div className="flex items-center justify-between">
            <div className="flex items-baseline gap-2">
              <span className="text-sm font-medium">{formatDuration(step.duration_a)}</span>
              <span className="text-muted text-xs">vs</span>
              <span className="text-sm font-medium">{formatDuration(step.duration_b)}</span>
            </div>
            <DeltaIndicator delta={step.duration_delta} unit="s" />
          </div>
        </div>
      </div>

      {/* Config diff */}
      {step.config_changed && step.config_a && step.config_b && (
        <div className="mb-3">
          <div className="text-xs font-medium text-warning mb-1">Config Changed</div>
          <div className="grid grid-cols-2 gap-2">
            <div className="rounded bg-error/5 p-2">
              <div className="text-xs text-muted-foreground mb-0.5">Run A</div>
              <pre className="text-xs font-mono text-foreground overflow-auto max-h-24">
                {JSON.stringify(step.config_a, null, 2)}
              </pre>
            </div>
            <div className="rounded bg-success/5 p-2">
              <div className="text-xs text-muted-foreground mb-0.5">Run B</div>
              <pre className="text-xs font-mono text-foreground overflow-auto max-h-24">
                {JSON.stringify(step.config_b, null, 2)}
              </pre>
            </div>
          </div>
        </div>
      )}

      {/* Output diff (collapsible) */}
      {step.output_changed && step.presence === "both" && (
        <details className="group">
          <summary className="cursor-pointer text-xs font-medium text-accent hover:text-accent-hover">
            Output differs - click to expand
          </summary>
          <div className="mt-2 grid grid-cols-2 gap-2">
            <div className="rounded bg-background/80 p-2">
              <div className="text-xs text-muted-foreground mb-0.5">Run A</div>
              <pre className="text-xs font-mono text-foreground overflow-auto max-h-40 whitespace-pre-wrap">
                {step.output_a ? JSON.stringify(step.output_a, null, 2) : "-"}
              </pre>
            </div>
            <div className="rounded bg-background/80 p-2">
              <div className="text-xs text-muted-foreground mb-0.5">Run B</div>
              <pre className="text-xs font-mono text-foreground overflow-auto max-h-40 whitespace-pre-wrap">
                {step.output_b ? JSON.stringify(step.output_b, null, 2) : "-"}
              </pre>
            </div>
          </div>
        </details>
      )}

      {/* Errors */}
      {(step.error_a || step.error_b) && (
        <div className="mt-2 space-y-1">
          {step.error_a && (
            <div className="rounded bg-error/10 px-2 py-1 text-xs text-error">
              <span className="font-medium">A:</span> {step.error_a}
            </div>
          )}
          {step.error_b && (
            <div className="rounded bg-error/10 px-2 py-1 text-xs text-error">
              <span className="font-medium">B:</span> {step.error_b}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
