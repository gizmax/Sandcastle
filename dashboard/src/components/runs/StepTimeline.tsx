import { StepCard } from "@/components/runs/StepCard";
import { STATUS_DOT_COLORS } from "@/lib/constants";
import { cn } from "@/lib/utils";

interface Step {
  step_id: string;
  parallel_index: number | null;
  status: string;
  output: unknown;
  cost_usd: number;
  duration_seconds: number;
  attempt: number;
  error: string | null;
  started_at: string | null;
}

interface StepTimelineProps {
  steps: Step[];
  onReplay?: (stepId: string) => void;
  onFork?: (stepId: string) => void;
}

export function StepTimeline({ steps, onReplay, onFork }: StepTimelineProps) {
  if (steps.length === 0) {
    return <p className="py-4 text-sm text-muted">No steps recorded</p>;
  }

  return (
    <div className="space-y-0">
      {steps.map((step, i) => (
        <div key={`${step.step_id}-${step.parallel_index ?? 0}-${i}`} className="relative flex gap-4">
          {/* Timeline line */}
          <div className="flex flex-col items-center">
            <div
              className={cn(
                "mt-4 h-3 w-3 rounded-full border-2 border-surface",
                STATUS_DOT_COLORS[step.status] || "bg-muted"
              )}
            />
            {i < steps.length - 1 && (
              <div className="w-px flex-1 bg-border" />
            )}
          </div>

          {/* Step card */}
          <div className="flex-1 pb-4">
            <StepCard
              stepId={step.step_id}
              status={step.status}
              costUsd={step.cost_usd}
              durationSeconds={step.duration_seconds}
              attempt={step.attempt}
              error={step.error}
              output={step.output}
              parallelIndex={step.parallel_index}
              startedAt={step.started_at}
              onReplay={onReplay}
              onFork={onFork}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
