import { useCallback, useEffect, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { ArrowLeft, ArrowDown, ArrowUp, Minus, GitCompareArrows } from "lucide-react";
import { api } from "@/api/client";
import { RunStatusBadge } from "@/components/runs/RunStatusBadge";
import { StepDiffCard } from "@/components/runs/StepDiffCard";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { EmptyState } from "@/components/shared/EmptyState";
import { formatCost, formatDuration, cn } from "@/lib/utils";

interface RunListItem {
  run_id: string;
  workflow_name: string;
  status: string;
  total_cost_usd: number;
  started_at: string | null;
  completed_at: string | null;
  parent_run_id: string | null;
}

interface StepDiff {
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

interface CompareData {
  run_a: RunListItem;
  run_b: RunListItem;
  total_cost_a: number;
  total_cost_b: number;
  total_cost_delta: number;
  total_duration_a: number | null;
  total_duration_b: number | null;
  total_duration_delta: number | null;
  same_workflow: boolean;
  steps: StepDiff[];
}

function SummaryDelta({ label, valueA, valueB, delta, format }: {
  label: string;
  valueA: number | null;
  valueB: number | null;
  delta: number | null;
  format: "cost" | "duration";
}) {
  const fmt = format === "cost" ? formatCost : formatDuration;
  const better = delta !== null && delta < 0;
  const worse = delta !== null && delta > 0;

  return (
    <div className="rounded-lg bg-background/50 p-3 text-center">
      <div className="text-xs text-muted-foreground mb-1">{label}</div>
      <div className="flex items-center justify-center gap-2 mb-1">
        <span className="text-sm font-medium">{valueA !== null ? fmt(valueA) : "-"}</span>
        <span className="text-muted text-xs">vs</span>
        <span className="text-sm font-medium">{valueB !== null ? fmt(valueB) : "-"}</span>
      </div>
      {delta !== null && (
        <span className={cn(
          "text-xs font-medium inline-flex items-center gap-0.5",
          better ? "text-success" : worse ? "text-error" : "text-muted"
        )}>
          {better ? <ArrowDown className="h-3 w-3" /> : worse ? <ArrowUp className="h-3 w-3" /> : <Minus className="h-3 w-3" />}
          {Math.abs(delta) < 0.001 ? "same" : `${delta > 0 ? "+" : ""}${fmt(Math.abs(delta))}`}
        </span>
      )}
    </div>
  );
}

export default function RunComparePage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [data, setData] = useState<CompareData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const runA = searchParams.get("run_a") || "";
  const runB = searchParams.get("run_b") || "";

  const fetchCompare = useCallback(async () => {
    if (!runA || !runB) {
      setError("Both run_a and run_b query parameters are required");
      setLoading(false);
      return;
    }
    try {
      const res = await api.get<CompareData>("/runs/compare", { run_a: runA, run_b: runB });
      if (res.data) setData(res.data);
      if (res.error) setError(res.error.message);
    } finally {
      setLoading(false);
    }
  }, [runA, runB]);

  useEffect(() => {
    void fetchCompare();
  }, [fetchCompare]);

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="py-16">
        <EmptyState
          icon={GitCompareArrows}
          title="Cannot compare runs"
          description={error || "No comparison data available"}
        />
      </div>
    );
  }

  return (
    <div className="space-y-4 sm:space-y-6">
      {/* Back button */}
      <button
        onClick={() => navigate(-1)}
        className="flex items-center gap-1 text-sm text-muted hover:text-foreground transition-colors"
      >
        <ArrowLeft className="h-4 w-4" />
        Back
      </button>

      {/* Header */}
      <div className="rounded-xl border border-border bg-surface p-4 sm:p-5 shadow-sm">
        <h1 className="text-xl font-semibold tracking-tight text-foreground mb-4">
          Replay Studio
        </h1>

        {/* Run badges */}
        <div className="grid grid-cols-2 gap-4 mb-4">
          <div
            className="rounded-lg border border-border p-3 cursor-pointer hover:border-accent/40 transition-colors"
            onClick={() => navigate(`/runs/${data.run_a.run_id}`)}
          >
            <div className="text-xs text-muted-foreground mb-1">Run A</div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-foreground truncate">{data.run_a.workflow_name}</span>
              <RunStatusBadge status={data.run_a.status} />
            </div>
            <p className="mt-1 font-mono text-xs text-muted truncate">{data.run_a.run_id}</p>
          </div>
          <div
            className="rounded-lg border border-border p-3 cursor-pointer hover:border-accent/40 transition-colors"
            onClick={() => navigate(`/runs/${data.run_b.run_id}`)}
          >
            <div className="text-xs text-muted-foreground mb-1">Run B</div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-foreground truncate">{data.run_b.workflow_name}</span>
              <RunStatusBadge status={data.run_b.status} />
            </div>
            <p className="mt-1 font-mono text-xs text-muted truncate">{data.run_b.run_id}</p>
          </div>
        </div>

        {!data.same_workflow && (
          <div className="rounded-md bg-warning/10 px-3 py-2 mb-4 text-xs text-warning">
            Different workflows - config comparison may not be meaningful
          </div>
        )}

        {/* Summary deltas */}
        <div className="grid grid-cols-2 gap-3">
          <SummaryDelta
            label="Total Cost"
            valueA={data.total_cost_a}
            valueB={data.total_cost_b}
            delta={data.total_cost_delta}
            format="cost"
          />
          <SummaryDelta
            label="Total Duration"
            valueA={data.total_duration_a}
            valueB={data.total_duration_b}
            delta={data.total_duration_delta}
            format="duration"
          />
        </div>
      </div>

      {/* Step diffs */}
      <div>
        <h2 className="mb-3 text-sm font-semibold text-foreground">
          Step Comparison ({data.steps.length} step{data.steps.length !== 1 ? "s" : ""})
        </h2>
        <div className="space-y-3">
          {data.steps.map((step, i) => (
            <StepDiffCard key={`${step.step_id}-${step.parallel_index ?? i}`} step={step} />
          ))}
        </div>
      </div>
    </div>
  );
}
