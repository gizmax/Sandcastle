import { useCallback, useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, XCircle, GitCompareArrows, Trash2, Download, Copy } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { RunStatusBadge } from "@/components/runs/RunStatusBadge";
import { StepTimeline } from "@/components/runs/StepTimeline";
import { LiveStream } from "@/components/runs/LiveStream";
import { RunTree } from "@/components/runs/RunTree";
import { ReplayForkModal } from "@/components/runs/ReplayForkModal";
import { BudgetBar } from "@/components/shared/BudgetBar";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
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
  started_at: string | null;
  pdf_artifact?: boolean;
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
  const [error, setError] = useState<string | null>(null);
  const [inputExpanded, setInputExpanded] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [modalMode, setModalMode] = useState<"replay" | "fork">("replay");
  const [modalStepId, setModalStepId] = useState("");

  const fetchRun = useCallback(async () => {
    if (!id) return;
    try {
      const res = await api.get<RunDetail>(`/runs/${id}`);
      if (res.data) {
        setRun(res.data);
        setError(null);
      } else if (res.error) {
        setError(res.error.message || "Failed to load run");
      } else {
        setError("Run not found");
      }
    } catch {
      setError("Could not connect to the API server");
    } finally {
      setLoading(false);
    }
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

  const handleDelete = useCallback(async () => {
    if (!id || deleting) return;
    setDeleting(true);
    const res = await api.delete(`/runs/${id}`);
    if (res.error) {
      toast.error(`Delete failed: ${res.error.message}`);
    } else {
      toast.success("Run deleted");
      navigate("/runs");
    }
    setDeleting(false);
    setDeleteConfirmOpen(false);
  }, [id, deleting, navigate]);

  const handleDownloadOutput = useCallback(() => {
    if (!run) return;
    const blob = new Blob([JSON.stringify(run.outputs ?? {}, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${run.workflow_name}-${run.run_id.slice(0, 8)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }, [run]);

  const handleCopyOutput = useCallback(async () => {
    if (!run?.outputs) return;
    await navigator.clipboard.writeText(JSON.stringify(run.outputs, null, 2));
    toast.success("Output copied to clipboard");
  }, [run]);

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
      <div className="py-16 text-center space-y-3">
        <p className="text-muted">{error || "Run not found"}</p>
        <button
          onClick={() => navigate("/runs")}
          className="inline-flex items-center gap-1 text-sm text-accent hover:text-accent/80 transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Runs
        </button>
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
            {run.parent_run_id && (
              <button
                onClick={() => navigate(`/runs/compare?run_a=${run.parent_run_id}&run_b=${run.run_id}`)}
                className={cn(
                  "flex items-center gap-1.5 rounded-lg border border-accent/30 px-3 py-1.5",
                  "text-sm font-medium text-accent",
                  "hover:bg-accent/10 transition-colors"
                )}
              >
                <GitCompareArrows className="h-4 w-4" />
                Compare with Parent
              </button>
            )}
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
            {!isRunning && (
              <button
                onClick={() => setDeleteConfirmOpen(true)}
                className={cn(
                  "flex items-center gap-1.5 rounded-lg border border-error/30 px-3 py-1.5",
                  "text-sm font-medium text-error",
                  "hover:bg-error/10 transition-colors"
                )}
              >
                <Trash2 className="h-4 w-4" />
                Delete
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
          runId={run.run_id}
          onReplay={handleReplay}
          onFork={handleFork}
        />
      </div>

      {/* Output export */}
      {run.outputs && Object.keys(run.outputs).length > 0 && (
        <div className="rounded-xl border border-border bg-surface p-3 sm:p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-foreground">Output</h2>
            <div className="flex items-center gap-2">
              <button
                onClick={handleCopyOutput}
                className={cn(
                  "flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5",
                  "text-xs font-medium text-muted",
                  "hover:bg-border/40 hover:text-foreground transition-colors"
                )}
              >
                <Copy className="h-3.5 w-3.5" />
                Copy
              </button>
              <button
                onClick={handleDownloadOutput}
                className={cn(
                  "flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5",
                  "text-xs font-medium text-muted",
                  "hover:bg-border/40 hover:text-foreground transition-colors"
                )}
              >
                <Download className="h-3.5 w-3.5" />
                Download JSON
              </button>
            </div>
          </div>
          <pre className="mt-3 max-h-64 overflow-auto rounded-lg bg-background p-3 font-mono text-xs text-foreground whitespace-pre-wrap">
            {JSON.stringify(run.outputs, null, 2)}
          </pre>
        </div>
      )}

      {/* Delete confirm */}
      <ConfirmDialog
        open={deleteConfirmOpen}
        title="Delete Run"
        description={`Are you sure you want to delete run ${run.run_id.slice(0, 8)}...? This action cannot be undone.`}
        confirmLabel={deleting ? "Deleting..." : "Delete"}
        variant="danger"
        onConfirm={handleDelete}
        onCancel={() => setDeleteConfirmOpen(false)}
      />

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
