import { useCallback, useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { api } from "@/api/client";
import { RunStatusBadge } from "@/components/runs/RunStatusBadge";
import { StepTimeline } from "@/components/runs/StepTimeline";
import { LiveStream } from "@/components/runs/LiveStream";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { formatDuration, formatCost, formatRelativeTime } from "@/lib/utils";

interface Step {
  step_id: string;
  parallel_index: number | null;
  status: string;
  output: unknown;
  cost_usd: number;
  duration_seconds: number;
  attempt: number;
  error: string | null;
}

interface RunDetail {
  run_id: string;
  workflow_name: string;
  status: string;
  input_data: Record<string, unknown> | null;
  outputs: Record<string, unknown> | null;
  total_cost_usd: number;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  steps: Step[] | null;
}

export default function RunDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [inputExpanded, setInputExpanded] = useState(false);

  const fetchRun = useCallback(async () => {
    if (!id) return;
    const res = await api.get<RunDetail>(`/runs/${id}`);
    if (res.data) setRun(res.data);
    setLoading(false);
  }, [id]);

  useEffect(() => {
    fetchRun();
  }, [fetchRun]);

  // Poll while running
  useEffect(() => {
    if (!run || !["running", "queued"].includes(run.status)) return;
    const interval = setInterval(fetchRun, 3000);
    return () => clearInterval(interval);
  }, [run, fetchRun]);

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (!run) {
    return (
      <div className="py-16 text-center">
        <p className="text-muted">Run not found</p>
      </div>
    );
  }

  const duration =
    run.started_at && run.completed_at
      ? (new Date(run.completed_at).getTime() - new Date(run.started_at).getTime()) / 1000
      : null;

  return (
    <div className="space-y-6">
      {/* Back button */}
      <button
        onClick={() => navigate("/runs")}
        className="flex items-center gap-1 text-sm text-muted hover:text-foreground transition-colors"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to Runs
      </button>

      {/* Header */}
      <div className="rounded-xl border border-border bg-surface p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-foreground">
              {run.workflow_name}
            </h1>
            <p className="mt-1 font-mono text-xs text-muted">{run.run_id}</p>
          </div>
          <RunStatusBadge status={run.status} size="md" />
        </div>

        <div className="mt-4 flex flex-wrap gap-6 text-sm text-muted">
          {run.started_at && (
            <div>
              <span className="text-xs font-medium text-muted-foreground">Started</span>
              <p>{formatRelativeTime(run.started_at)}</p>
            </div>
          )}
          {duration !== null && (
            <div>
              <span className="text-xs font-medium text-muted-foreground">Duration</span>
              <p>{formatDuration(duration)}</p>
            </div>
          )}
          <div>
            <span className="text-xs font-medium text-muted-foreground">Cost</span>
            <p>{formatCost(run.total_cost_usd)}</p>
          </div>
        </div>

        {run.error && (
          <div className="mt-4 rounded-md bg-error/10 px-3 py-2">
            <p className="text-xs font-medium text-error">Error</p>
            <p className="mt-0.5 font-mono text-xs text-error/80">{run.error}</p>
          </div>
        )}
      </div>

      {/* Input data */}
      {run.input_data && Object.keys(run.input_data).length > 0 && (
        <div className="rounded-xl border border-border bg-surface shadow-sm">
          <button
            onClick={() => setInputExpanded(!inputExpanded)}
            className="w-full px-5 py-3 text-left text-sm font-medium text-foreground hover:bg-border/20 transition-colors"
          >
            Input Data {inputExpanded ? "[-]" : "[+]"}
          </button>
          {inputExpanded && (
            <div className="border-t border-border px-5 py-3">
              <pre className="max-h-48 overflow-auto font-mono text-xs text-muted">
                {JSON.stringify(run.input_data, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}

      {/* Live stream for running runs */}
      {["running", "queued"].includes(run.status) && id && <LiveStream runId={id} />}

      {/* Step timeline */}
      <div>
        <h2 className="mb-3 text-sm font-semibold text-foreground">Steps</h2>
        <StepTimeline steps={run.steps || []} />
      </div>
    </div>
  );
}
