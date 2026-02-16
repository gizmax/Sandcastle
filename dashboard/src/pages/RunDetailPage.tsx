import { useCallback, useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, XCircle } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { RunStatusBadge } from "@/components/runs/RunStatusBadge";
import { StepTimeline } from "@/components/runs/StepTimeline";
import { LiveStream } from "@/components/runs/LiveStream";
import { RunTree } from "@/components/runs/RunTree";
import { ReplayForkModal } from "@/components/runs/ReplayForkModal";
import { BudgetBar } from "@/components/shared/BudgetBar";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { formatDuration, formatCost, formatRelativeTime, parseUTC, cn } from "@/lib/utils";

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
  max_cost_usd: number | null;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  steps: Step[] | null;
  parent_run_id: string | null;
  replay_from_step: string | null;
  fork_changes: Record<string, unknown> | null;
}

export default function RunDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [inputExpanded, setInputExpanded] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [modalMode, setModalMode] = useState<"replay" | "fork">("replay");
  const [modalStepId, setModalStepId] = useState("");

  const fetchRun = useCallback(async () => {
    if (!id) return;
    const res = await api.get<RunDetail>(`/runs/${id}`);
    if (res.data) setRun(res.data);
    setLoading(false);
  }, [id]);

  useEffect(() => {
    void fetchRun();
  }, [fetchRun]);

  // Poll while running
  useEffect(() => {
    if (!run || !["running", "queued"].includes(run.status)) return;
    const interval = setInterval(fetchRun, 3000);
    return () => clearInterval(interval);
  }, [run, fetchRun]);

  const handleCancel = useCallback(async () => {
    if (!id || cancelling) return;
    setCancelling(true);
    const res = await api.post(`/runs/${id}/cancel`);
    if (res.error) {
      toast.error(`Cancel failed: ${res.error.message}`);
    } else {
      toast.success("Run cancelled");
      void fetchRun();
    }
    setCancelling(false);
  }, [id, cancelling, fetchRun]);

  const handleReplay = useCallback((stepId: string) => {
    setModalStepId(stepId);
    setModalMode("replay");
    setModalOpen(true);
  }, []);

  const handleFork = useCallback((stepId: string) => {
    setModalStepId(stepId);
    setModalMode("fork");
    setModalOpen(true);
  }, []);

  const handleModalSubmit = useCallback(
    async (data: { from_step: string; changes?: Record<string, unknown> }) => {
      if (!id) return;
      const endpoint = data.changes ? `/runs/${id}/fork` : `/runs/${id}/replay`;
      const body = data.changes
        ? { from_step: data.from_step, changes: data.changes }
        : { from_step: data.from_step };

      const res = await api.post<{ new_run_id: string }>(endpoint, body);
      setModalOpen(false);

      if (res.error) {
        toast.error(`Failed: ${res.error.message}`);
      } else if (res.data) {
        const newId = (res.data as Record<string, unknown>).new_run_id as string;
        toast.success(
          data.changes ? "Fork created" : "Replay started"
        );
        if (newId) {
          navigate(`/runs/${newId}`);
        }
      }
    },
    [id, navigate]
  );

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

  const isRunning = ["running", "queued"].includes(run.status);
  const duration =
    run.started_at && run.completed_at
      ? (parseUTC(run.completed_at).getTime() - parseUTC(run.started_at).getTime()) / 1000
      : run.started_at && isRunning
        ? (Date.now() - parseUTC(run.started_at).getTime()) / 1000
        : null;

  return (
    <div className="space-y-4 sm:space-y-6">
      {/* Back button */}
      <button
        onClick={() => navigate("/runs")}
        className="flex items-center gap-1 text-sm text-muted hover:text-foreground transition-colors"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to Runs
      </button>

      {/* Header */}
      <div className="rounded-xl border border-border bg-surface p-3 sm:p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3 sm:gap-4">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-foreground">
              {run.workflow_name}
            </h1>
            <p className="mt-1 font-mono text-xs text-muted">{run.run_id}</p>
            {run.replay_from_step && (
              <p className="mt-1 text-xs text-accent">
                {run.fork_changes ? "Forked" : "Replayed"} from step: {run.replay_from_step}
              </p>
            )}
          </div>
          <div className="flex items-center gap-3">
            {isRunning && (
              <button
                onClick={handleCancel}
                disabled={cancelling}
                className={cn(
                  "flex items-center gap-1.5 rounded-lg border border-error/30 px-3 py-1.5",
                  "text-sm font-medium text-error",
                  "hover:bg-error/10 transition-colors",
                  "disabled:opacity-50 disabled:cursor-not-allowed"
                )}
              >
                <XCircle className="h-4 w-4" />
                {cancelling ? "Cancelling..." : "Cancel"}
              </button>
            )}
            <RunStatusBadge status={run.status} size="md" />
          </div>
        </div>

        <div className="mt-4 flex flex-wrap gap-4 sm:gap-6 text-sm text-muted">
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

        {/* Budget bar */}
        {run.max_cost_usd && run.max_cost_usd > 0 && (
          <div className="mt-4">
            <BudgetBar spent={run.total_cost_usd} limit={run.max_cost_usd} />
          </div>
        )}

        {run.error && (
          <div className="mt-4 rounded-md bg-error/10 px-3 py-2">
            <p className="text-xs font-medium text-error">Error</p>
            <p className="mt-0.5 font-mono text-xs text-error/80">{run.error}</p>
          </div>
        )}
      </div>

      {/* Run Tree (parent/children lineage) */}
      {run.parent_run_id && (
        <RunTree
          parentRun={{
            run_id: run.parent_run_id,
            workflow_name: run.workflow_name,
            status: "completed",
          }}
          currentRun={{
            run_id: run.run_id,
            workflow_name: run.workflow_name,
            status: run.status,
            replay_from_step: run.replay_from_step,
            fork_changes: run.fork_changes,
          }}
        />
      )}

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
      {isRunning && id && <LiveStream runId={id} />}

      {/* Step timeline */}
      <div>
        <h2 className="mb-3 text-sm font-semibold text-foreground">Steps</h2>
        <StepTimeline
          steps={run.steps || []}
          onReplay={handleReplay}
          onFork={handleFork}
        />
      </div>

      {/* Replay/Fork Modal */}
      <ReplayForkModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        runId={run.run_id}
        stepId={modalStepId}
        mode={modalMode}
        onSubmit={handleModalSubmit}
      />
    </div>
  );
}
