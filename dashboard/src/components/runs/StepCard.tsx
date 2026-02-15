import { useState } from "react";
import { ChevronDown, ChevronRight, RotateCcw, GitFork } from "lucide-react";
import { RunStatusBadge } from "@/components/runs/RunStatusBadge";
import { cn, formatDuration, formatCost } from "@/lib/utils";

interface StepCardProps {
  stepId: string;
  status: string;
  costUsd: number;
  durationSeconds: number;
  attempt: number;
  error: string | null;
  output: unknown;
  parallelIndex: number | null;
  onReplay?: (stepId: string) => void;
  onFork?: (stepId: string) => void;
}

export function StepCard({
  stepId,
  status,
  costUsd,
  durationSeconds,
  attempt,
  error,
  output,
  parallelIndex,
  onReplay,
  onFork,
}: StepCardProps) {
  const [expanded, setExpanded] = useState(false);
  const isCompleted = status === "completed" || status === "failed";

  return (
    <div className="rounded-lg border border-border bg-surface shadow-sm">
      <button
        onClick={() => setExpanded(!expanded)}
        className={cn(
          "flex w-full items-center gap-3 px-4 py-3 text-left",
          "transition-colors duration-150 hover:bg-border/20"
        )}
      >
        {expanded ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-muted" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-muted" />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-foreground">{stepId}</span>
            {parallelIndex !== null && (
              <span className="text-xs text-muted">[{parallelIndex}]</span>
            )}
          </div>
          <div className="mt-0.5 flex items-center gap-3 text-xs text-muted">
            <span>{formatDuration(durationSeconds)}</span>
            <span>{formatCost(costUsd)}</span>
            {attempt > 1 && <span>attempt {attempt}</span>}
          </div>
        </div>
        <RunStatusBadge status={status} />
      </button>

      {expanded && (
        <div className="border-t border-border px-4 py-3">
          {error && (
            <div className="mb-3 rounded-md bg-error/10 px-3 py-2">
              <p className="text-xs font-medium text-error">Error</p>
              <p className="mt-0.5 font-mono text-xs text-error/80">{error}</p>
            </div>
          )}
          {output != null && (
            <div>
              <p className="mb-1 text-xs font-medium text-muted">Output</p>
              <pre className="max-h-64 overflow-auto rounded-md bg-background p-3 font-mono text-xs text-foreground">
                {typeof output === "string" ? output : JSON.stringify(output, null, 2)}
              </pre>
            </div>
          )}

          {/* Replay / Fork buttons for completed steps */}
          {isCompleted && (onReplay || onFork) && (
            <div className="mt-3 flex items-center gap-2 border-t border-border pt-3">
              {onReplay && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onReplay(stepId);
                  }}
                  className={cn(
                    "flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium",
                    "border border-border text-muted",
                    "hover:bg-border/40 hover:text-foreground transition-colors"
                  )}
                >
                  <RotateCcw className="h-3 w-3" />
                  Replay from here
                </button>
              )}
              {onFork && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onFork(stepId);
                  }}
                  className={cn(
                    "flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium",
                    "border border-accent/30 text-accent",
                    "hover:bg-accent/10 transition-colors"
                  )}
                >
                  <GitFork className="h-3 w-3" />
                  Fork from here
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
