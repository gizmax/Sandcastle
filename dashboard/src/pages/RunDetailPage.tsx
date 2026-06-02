import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { XCircle, GitCompareArrows, Trash2, Download, Copy, FileDown, ChevronDown, ArrowLeft, Sparkles, AlertTriangle, AlertOctagon, Shield, ChevronRight, Share2, ExternalLink } from "lucide-react";
import { useNotifications } from "@/hooks/useNotifications";
import { useRuns } from "@/hooks/useRuns";
import { useEventStreamContext } from "@/hooks/useEventStreamContext";
import { toast } from "sonner";
import { api } from "@/api/client";
import { RunStatusBadge } from "@/components/runs/RunStatusBadge";
import { RiskLevelBadge } from "@/components/runs/RiskLevelBadge";
import { AnomalyBadge } from "@/components/shared/AnomalyBadge";
import { StepTimeline } from "@/components/runs/StepTimeline";
import { LiveStream } from "@/components/runs/LiveStream";
import { RunTree } from "@/components/runs/RunTree";
import { ReplayForkModal } from "@/components/runs/ReplayForkModal";
import { PipelineViz } from "@/components/runs/PipelineViz";
import { StepFlamegraph } from "@/components/runs/StepFlamegraph";
import { BudgetBar } from "@/components/shared/BudgetBar";
import { CopyButton } from "@/components/shared/CopyButton";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { CelebrationModal } from "@/components/shared/CelebrationModal";
import { Breadcrumb } from "@/components/shared/Breadcrumb";
import { Skeleton } from "@/components/ui/Skeleton";
import { AiChatSidebar } from "@/components/runs/AiChatSidebar";
import { AgentEventStream } from "@/components/agents/AgentEventStream";
import { fireConfetti, playCelebrationSound } from "@/lib/confetti";
import { detectAnomalies, detectRetryHeavy, type Anomaly } from "@/lib/anomalyDetection";
import { formatDuration, formatCost, formatRelativeTime, parseUTC, cn, isSafeUrl } from "@/lib/utils";

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
  responsibility?: string;
  owner?: string;
  type?: string;
  artifact_url?: string;
  model?: string | null;
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
  depth: number;
  sub_workflow_of_step: string | null;
  sub_runs: { run_id: string; workflow_name: string; status: string; sub_workflow_of_step: string | null }[] | null;
  risk_level?: string;
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
  const [celebrationOpen, setCelebrationOpen] = useState(false);
  const [celebrationMilestone, setCelebrationMilestone] = useState<number | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [vizMode, setVizMode] = useState<"pipeline" | "flamegraph">("pipeline");
  const [transparencyReportExpanded, setTransparencyReportExpanded] = useState(false);
  const [transparencyReport, setTransparencyReport] = useState<Record<string, unknown> | null>(null);
  const [loadingReport, setLoadingReport] = useState(false);
  const [flamegraphExpanded, setFlamegraphExpanded] = useState(true);
  const [anomaliesExpanded, setAnomaliesExpanded] = useState(false);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [sharing, setSharing] = useState(false);
  const { notifyRunComplete } = useNotifications();
  const prevStatusRef = useRef<string | null>(null);
  const celebratedRef = useRef(false);

  // Real-time step progress via SSE events
  const { subscribe } = useEventStreamContext();

  // Track step status overrides from SSE events (step_id -> status)
  const [stepOverrides, setStepOverrides] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!id) return;

    const handleStepStarted = subscribe("step.started", (event) => {
      const data = event.data as { run_id?: string; step_id?: string };
      if (data.run_id === id && data.step_id) {
        setStepOverrides((prev) => ({ ...prev, [data.step_id as string]: "running" }));
      }
    });

    const handleStepCompleted = subscribe("step.completed", (event) => {
      const data = event.data as { run_id?: string; step_id?: string };
      if (data.run_id === id && data.step_id) {
        setStepOverrides((prev) => ({ ...prev, [data.step_id as string]: "completed" }));
      }
    });

    const handleStepFailed = subscribe("step.failed", (event) => {
      const data = event.data as { run_id?: string; step_id?: string };
      if (data.run_id === id && data.step_id) {
        setStepOverrides((prev) => ({ ...prev, [data.step_id as string]: "failed" }));
      }
    });

    return () => {
      handleStepStarted();
      handleStepCompleted();
      handleStepFailed();
    };
  }, [id, subscribe]);

  // Clear step overrides when run data refreshes from the server
  useEffect(() => {
    if (run) {
      setStepOverrides({});
    }
  }, [run]);

  // Merge SSE step overrides into run steps for real-time updates
  const liveSteps = useMemo(() => {
    if (!run?.steps) return [];
    if (Object.keys(stepOverrides).length === 0) return run.steps;
    return run.steps.map((step) => {
      const override = stepOverrides[step.step_id];
      if (override) {
        return { ...step, status: override };
      }
      return step;
    });
  }, [run?.steps, stepOverrides]);

  // Must be called unconditionally (Rules of Hooks) - workflow is empty string until run loads
  const { runs: siblingRuns } = useRuns({
    workflow: run?.workflow_name ?? "",
    limit: 50,
    autoPoll: false,
  });

  // Must be called unconditionally (Rules of Hooks) - run may be null before data loads
  const anomalies = useMemo<Anomaly[]>(() => {
    if (!run) return [];
    const result = detectAnomalies(run, siblingRuns);
    if (liveSteps.length > 0) {
      const retryAnomaly = detectRetryHeavy(liveSteps);
      if (retryAnomaly) result.push(retryAnomaly);
    }
    return result;
  }, [run, siblingRuns, liveSteps]);

  const fetchRun = useCallback(async (cancelled?: { current: boolean }) => {
    if (!id) return;
    try {
      const res = await api.get<RunDetail>(`/runs/${id}`);
      if (cancelled?.current) return;
      if (res.data) {
        setRun(res.data);
        setError(null);
      } else if (res.error) {
        setError(res.error.message || "Failed to load run");
      } else {
        setError("Run not found");
      }
    } catch {
      if (cancelled?.current) return;
      setError("Could not connect to the API server");
    } finally {
      if (!cancelled?.current) setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    const cancelled = { current: false };
    setLoading(true);
    setRun(null);
    setError(null);
    void fetchRun(cancelled);
    return () => { cancelled.current = true; };
  }, [fetchRun]);

  const runStatus = run?.status ?? null;

  // Detect run completion: browser notification + celebration
  useEffect(() => {
    const prev = prevStatusRef.current;
    prevStatusRef.current = runStatus;

    if (!run || !runStatus) return;

    const wasActive = prev !== null && ["running", "queued"].includes(prev);
    const justCompleted = runStatus === "completed";
    const justFinished = ["completed", "failed"].includes(runStatus);

    // Browser notification (when page is hidden)
    if (wasActive && justFinished && document.hidden) {
      notifyRunComplete({
        run_id: run.run_id,
        workflow_name: run.workflow_name,
        status: run.status,
        total_cost_usd: run.total_cost_usd,
      });
    }

    // Celebration (only for "completed", not "failed")
    if (wasActive && justCompleted && !celebratedRef.current) {
      celebratedRef.current = true;

      const COUNTER_KEY = "sandcastle-successful-run-count";
      const FIRST_KEY = "sandcastle-first-run-celebrated";
      const rawCount = localStorage.getItem(COUNTER_KEY);
      const newCount = (rawCount ? parseInt(rawCount, 10) : 0) + 1;
      localStorage.setItem(COUNTER_KEY, String(newCount));

      const isFirstRun = !localStorage.getItem(FIRST_KEY);

      if (isFirstRun) {
        localStorage.setItem(FIRST_KEY, "true");
        fireConfetti(100);
        playCelebrationSound();
        setTimeout(() => {
          setCelebrationMilestone(null);
          setCelebrationOpen(true);
        }, 400);
      } else if ([10, 50, 100].includes(newCount)) {
        fireConfetti(40);
        playCelebrationSound();
        setTimeout(() => {
          setCelebrationMilestone(newCount);
          setCelebrationOpen(true);
        }, 400);
      }
    }
  }, [runStatus, run, notifyRunComplete]);

  useEffect(() => {
    if (!runStatus || !["running", "queued"].includes(runStatus)) return;
    const cancelled = { current: false };
    const interval = setInterval(() => void fetchRun(cancelled), 5000);
    return () => {
      cancelled.current = true;
      clearInterval(interval);
    };
  }, [runStatus, fetchRun]);

  const cancellingRef = useRef(false);
  const handleCancel = useCallback(async () => {
    if (!id || cancellingRef.current) return;
    cancellingRef.current = true;
    setCancelling(true);
    const res = await api.post(`/runs/${id}/cancel`);
    if (res.error) {
      toast.error(`Cancel failed: ${res.error.message}`);
    } else {
      toast.success("Run cancelled");
      void fetchRun();
    }
    cancellingRef.current = false;
    setCancelling(false);
  }, [id, fetchRun]);

  const deletingRef = useRef(false);
  const handleDelete = useCallback(async () => {
    if (!id || deletingRef.current) return;
    deletingRef.current = true;
    setDeleting(true);
    const res = await api.delete(`/runs/${id}`);
    if (res.error) {
      toast.error(`Delete failed: ${res.error.message}`);
    } else {
      toast.success("Run deleted");
      navigate("/runs");
    }
    deletingRef.current = false;
    setDeleting(false);
    setDeleteConfirmOpen(false);
  }, [id, navigate]);

  const handleShare = useCallback(async () => {
    if (!id || sharing) return;
    setSharing(true);
    const res = await api.post<{ share_token: string; share_path: string }>(
      `/runs/${id}/share`
    );
    if (res.error) {
      toast.error(`Share failed: ${res.error.message}`);
    } else if (res.data) {
      const url = `${window.location.origin}${res.data.share_path}`;
      setShareUrl(url);
      try {
        await navigator.clipboard.writeText(url);
        toast.success("Public link copied (secrets and PII scrubbed)");
      } catch {
        toast.success("Public link created (secrets and PII scrubbed)");
      }
    }
    setSharing(false);
  }, [id, sharing]);

  // Per-provider cost rollup from the run's steps - surfaces the provider-neutral story.
  const providerStats = useMemo(() => {
    const map = new Map<string, { cost: number; count: number }>();
    for (const s of run?.steps ?? []) {
      if (!s.model) continue;
      const e = map.get(s.model) ?? { cost: 0, count: 0 };
      e.cost += s.cost_usd || 0;
      e.count += 1;
      map.set(s.model, e);
    }
    return [...map.entries()]
      .map(([model, v]) => ({ model, ...v }))
      .sort((a, b) => b.cost - a.cost);
  }, [run]);

  // Filter out internal fields (prefixed with _) from outputs
  const filterOutputs = useCallback(
    (outputs: Record<string, unknown> | null): Record<string, unknown> => {
      if (!outputs) return {};
      return Object.fromEntries(
        Object.entries(outputs).filter(([k]) => !k.startsWith("_"))
      );
    },
    []
  );

  const handleDownloadOutput = useCallback(() => {
    if (!run) return;
    const clean = filterOutputs(run.outputs);
    const blob = new Blob([JSON.stringify(clean, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${run.workflow_name}-${run.run_id.slice(0, 8)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }, [run, filterOutputs]);

  const handleDownloadTxt = useCallback(() => {
    if (!run) return;
    const clean = filterOutputs(run.outputs);
    const lines: string[] = [];
    for (const [key, value] of Object.entries(clean)) {
      lines.push(`=== ${key} ===`);
      if (typeof value === "string") {
        lines.push(value);
      } else {
        lines.push(JSON.stringify(value, null, 2));
      }
      lines.push("");
    }
    const blob = new Blob([lines.join("\n")], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${run.workflow_name}-${run.run_id.slice(0, 8)}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  }, [run, filterOutputs]);

  const handleCopyOutput = useCallback(async () => {
    if (!run?.outputs) return;
    const clean = filterOutputs(run.outputs);
    await navigator.clipboard.writeText(JSON.stringify(clean, null, 2));
    toast.success("Output copied to clipboard");
  }, [run, filterOutputs]);

  const handleLoadTransparencyReport = useCallback(async () => {
    if (!id || transparencyReport) {
      setTransparencyReportExpanded((e) => !e);
      return;
    }
    setLoadingReport(true);
    setTransparencyReportExpanded(true);
    const res = await api.get<Record<string, unknown>>(`/runs/${id}/transparency-report`);
    setLoadingReport(false);
    if (res.data) {
      setTransparencyReport(res.data);
    }
  }, [id, transparencyReport]);

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
      <div className="space-y-4 sm:space-y-6">
        <div className="flex items-center gap-2">
          <Skeleton className="h-4 w-16" />
          <Skeleton className="h-4 w-4 rounded-full" />
          <Skeleton className="h-4 w-12" />
          <Skeleton className="h-4 w-4 rounded-full" />
          <Skeleton className="h-4 w-32" />
        </div>
        <div className="rounded-xl border border-border bg-surface p-4 sm:p-5 space-y-4">
          <div className="flex items-start justify-between">
            <div className="space-y-2">
              <Skeleton className="h-6 w-48" />
              <Skeleton className="h-3 w-64" />
            </div>
            <div className="flex items-center gap-3">
              <Skeleton className="h-8 w-20 rounded-lg" />
              <Skeleton className="h-6 w-16 rounded-full" />
            </div>
          </div>
          <div className="flex gap-6">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="space-y-1">
                <Skeleton className="h-3 w-12" />
                <Skeleton className="h-4 w-20" />
              </div>
            ))}
          </div>
        </div>
        <Skeleton className="h-10 w-full rounded-xl" />
        <div className="space-y-3">
          <Skeleton className="h-4 w-16" />
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="flex items-center gap-3 rounded-lg border border-border bg-surface px-4 py-3">
              <Skeleton className="h-6 w-6 rounded-full" />
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-5 w-16 rounded-full" />
              <Skeleton className="ml-auto h-4 w-20" />
            </div>
          ))}
        </div>
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
      <Breadcrumb items={[
        { label: "Overview", href: "/" },
        { label: "Runs", href: "/runs" },
        { label: `${run.workflow_name} (${run.run_id.slice(0, 8)})` },
      ]} />

      {/* Header */}
      <div className="rounded-xl border border-border bg-surface p-3 sm:p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3 sm:gap-4">
          <div className="min-w-0 flex-1">
            <h1 className="text-lg sm:text-xl font-semibold tracking-tight text-foreground">
              {run.workflow_name}
            </h1>
            <p className="mt-1 flex items-center gap-1 font-mono text-xs text-muted truncate">
              <span className="hidden sm:inline">{run.run_id}</span>
              <span className="sm:hidden">{run.run_id.slice(0, 16)}...</span>
              <CopyButton value={run.run_id} label="run ID" />
            </p>
            {run.replay_from_step && (
              <p className="mt-1 text-xs text-accent">
                {run.fork_changes ? "Forked" : "Replayed"} from step: {run.replay_from_step}
              </p>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2 sm:gap-3">
            {run.parent_run_id && (
              <button
                onClick={() => navigate(`/runs/compare?run_a=${run.parent_run_id}&run_b=${run.run_id}`)}
                className={cn(
                  "flex items-center gap-1.5 rounded-lg border border-accent/30 px-3 py-1.5",
                  "text-xs sm:text-sm font-medium text-accent",
                  "hover:bg-accent/10 transition-colors"
                )}
              >
                <GitCompareArrows className="h-4 w-4" />
                <span className="hidden sm:inline">Compare with Parent</span>
                <span className="sm:hidden">Compare</span>
              </button>
            )}
            {isRunning && (
              <button
                onClick={handleCancel}
                disabled={cancelling}
                className={cn(
                  "flex items-center gap-1.5 rounded-lg border border-error/30 px-3 py-1.5",
                  "text-xs sm:text-sm font-medium text-error",
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
                  "text-xs sm:text-sm font-medium text-error",
                  "hover:bg-error/10 transition-colors"
                )}
              >
                <Trash2 className="h-4 w-4" />
                Delete
              </button>
            )}
            <button
              onClick={handleShare}
              disabled={sharing}
              title="Create a public, scrubbed permalink for this run"
              className={cn(
                "flex items-center gap-1.5 rounded-lg border border-accent/30 px-3 py-1.5",
                "text-xs sm:text-sm font-medium text-accent",
                "hover:bg-accent/10 transition-colors",
                "disabled:opacity-50 disabled:cursor-not-allowed"
              )}
            >
              <Share2 className="h-4 w-4" />
              <span className="hidden sm:inline">{sharing ? "Sharing..." : "Share"}</span>
            </button>
            <button
              onClick={() => setChatOpen(true)}
              className={cn(
                "flex items-center gap-1.5 rounded-lg border border-accent/30 px-3 py-1.5",
                "text-xs sm:text-sm font-medium text-accent",
                "hover:bg-accent/10 transition-colors"
              )}
            >
              <Sparkles className="h-4 w-4" />
              <span className="hidden sm:inline">Ask AI</span>
            </button>
            <RunStatusBadge status={run.status} size="md" />
            <AnomalyBadge anomalies={anomalies} />
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

      {/* Anomalies detail */}
      {anomalies.length > 0 && (
        <div className="rounded-xl border border-warning/30 bg-warning/5 shadow-sm">
          <button
            onClick={() => setAnomaliesExpanded(!anomaliesExpanded)}
            aria-expanded={anomaliesExpanded}
            aria-controls="anomalies-detail"
            className="w-full px-5 py-3 text-left text-sm font-medium text-foreground hover:bg-warning/10 transition-colors flex items-center gap-2"
          >
            <AlertTriangle className="h-4 w-4 text-warning" />
            Anomalies Detected ({anomalies.length}) {anomaliesExpanded ? "[-]" : "[+]"}
          </button>
          {anomaliesExpanded && (
            <div id="anomalies-detail" className="border-t border-warning/20 px-5 py-3">
              <ul className="space-y-2">
                {anomalies.map((a, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    {a.severity === "critical" ? (
                      <AlertOctagon className="mt-0.5 h-4 w-4 shrink-0 text-error" />
                    ) : (
                      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
                    )}
                    <div>
                      <span className={cn(
                        "inline-block rounded-full px-1.5 py-0.5 text-[10px] font-semibold uppercase mr-2",
                        a.severity === "critical" ? "bg-error/15 text-error" : "bg-warning/15 text-warning"
                      )}>
                        {a.severity}
                      </span>
                      <span className="text-muted">{a.message}</span>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

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
            aria-expanded={inputExpanded}
            aria-controls="run-input-data"
            className="w-full px-5 py-3 text-left text-sm font-medium text-foreground hover:bg-border/20 transition-colors"
          >
            Input Data {inputExpanded ? "[-]" : "[+]"}
          </button>
          {inputExpanded && (
            <div id="run-input-data" className="border-t border-border px-5 py-3">
              <pre className="max-h-48 overflow-auto font-mono text-xs text-muted">
                {JSON.stringify(run.input_data, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}

      {/* Live stream for running runs */}
      {isRunning && id && <LiveStream runId={id} />}

      {/* Pipeline / Flamegraph visualization */}
      {liveSteps.length > 0 && (
        <div className="space-y-4">
          {/* Pipeline or Flamegraph based on toggle */}
          {vizMode === "pipeline" ? (
            <PipelineViz steps={liveSteps} />
          ) : null}

          {/* Execution Timeline (Flamegraph) - collapsible */}
          <div className="rounded-xl border border-border bg-surface shadow-sm">
            <div className="flex items-center justify-between px-4 py-3">
              <button
                onClick={() => setFlamegraphExpanded(!flamegraphExpanded)}
                className="flex items-center gap-2 text-xs font-semibold text-muted-foreground uppercase tracking-wider hover:text-foreground transition-colors"
              >
                <ChevronDown
                  className={cn(
                    "h-3.5 w-3.5 transition-transform duration-200",
                    !flamegraphExpanded && "-rotate-90"
                  )}
                />
                Execution Timeline
              </button>

              {/* Pipeline | Flamegraph toggle */}
              <div className="flex items-center rounded-lg border border-border bg-background p-0.5">
                <button
                  onClick={() => setVizMode("pipeline")}
                  className={cn(
                    "rounded-md px-2.5 py-1 text-[11px] font-medium transition-all duration-200",
                    vizMode === "pipeline"
                      ? "bg-accent text-accent-foreground shadow-sm"
                      : "text-muted hover:text-foreground"
                  )}
                >
                  Pipeline
                </button>
                <button
                  onClick={() => setVizMode("flamegraph")}
                  className={cn(
                    "rounded-md px-2.5 py-1 text-[11px] font-medium transition-all duration-200",
                    vizMode === "flamegraph"
                      ? "bg-accent text-accent-foreground shadow-sm"
                      : "text-muted hover:text-foreground"
                  )}
                >
                  Flamegraph
                </button>
              </div>
            </div>

            {flamegraphExpanded && (
              <div className="border-t border-border p-4 overflow-x-auto">
                {vizMode === "flamegraph" ? (
                  <StepFlamegraph steps={liveSteps} />
                ) : (
                  <StepFlamegraph steps={liveSteps} />
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Public share link (after the owner mints it) */}
      {shareUrl && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-accent/30 bg-accent/5 px-4 py-3 text-xs sm:text-sm">
          <Share2 className="h-4 w-4 shrink-0 text-accent" />
          <span className="text-muted">Public link (secrets and PII scrubbed):</span>
          <a
            href={shareUrl}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 truncate font-mono text-accent hover:underline"
          >
            {shareUrl}
            <ExternalLink className="h-3.5 w-3.5 shrink-0" />
          </a>
        </div>
      )}

      {/* Providers used - surfaces the provider-neutral cost story */}
      {providerStats.length > 0 && (
        <div className="rounded-xl border border-border bg-surface p-4 shadow-sm card-hover-lift transition-all">
          <div className="mb-3 flex items-center gap-2">
            <Shield className="h-4 w-4 text-accent" />
            <span className="text-sm font-semibold text-foreground">Providers</span>
            <span className="text-xs text-muted">
              {providerStats.length === 1
                ? "1 provider"
                : `${providerStats.length} providers, failover-ready`}
            </span>
          </div>
          <div className="flex flex-wrap gap-2">
            {providerStats.map((p) => (
              <div
                key={p.model}
                className="flex items-center gap-2 rounded-lg border border-border bg-background px-3 py-1.5"
              >
                <span className="font-mono text-xs text-foreground">{p.model}</span>
                <span className="text-[11px] text-muted">
                  {p.count} {p.count === 1 ? "step" : "steps"}
                </span>
                <span className="text-[11px] font-medium text-accent">{formatCost(p.cost)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Step timeline */}
      <div>
        <h2 className="mb-3 text-sm font-semibold text-foreground">Steps</h2>
        <StepTimeline
          steps={liveSteps}
          runId={run.run_id}
          onReplay={handleReplay}
          onFork={handleFork}
        />
      </div>

      {/* Agent Reasoning panel - live SSE stream of Anthropic agent events */}
      {id && <AgentEventStream runId={id} />}

      {/* Generated images gallery */}
      {(() => {
        const allImages = liveSteps.flatMap((s) => {
          const out = s.output as Record<string, unknown> | null;
          if (out && Array.isArray(out._images)) return out._images as Array<{ index: number; url?: string; filename?: string; error?: string }>;
          return [];
        });
        if (allImages.length === 0) return null;
        return (
          <div className="rounded-xl border border-border bg-surface p-3 sm:p-5 shadow-sm">
            <h2 className="mb-3 text-sm font-semibold text-foreground">Generated Images ({allImages.length})</h2>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-3">
              {allImages.map((img, i) => (
                <div key={i} className="rounded-lg border border-border overflow-hidden bg-background">
                  {img.error ? (
                    <div className="flex items-center justify-center h-40 text-xs text-error px-2 text-center">{img.error}</div>
                  ) : img.url && isSafeUrl(img.url) ? (
                    <a href={img.url} target="_blank" rel="noopener noreferrer">
                      <img src={img.url} alt={img.filename || `Image ${i + 1}`} className="w-full h-auto object-contain max-h-72" loading="lazy" />
                    </a>
                  ) : null}
                  <div className="px-2 py-1.5 text-xs text-muted border-t border-border">{img.filename || `Image ${i + 1}`}</div>
                </div>
              ))}
            </div>
          </div>
        );
      })()}

      {/* Output export */}
      {run.outputs && Object.keys(run.outputs).length > 0 && (
        <div className="rounded-xl border border-border bg-surface p-3 sm:p-5 shadow-sm">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <h2 className="text-sm font-semibold text-foreground">Output</h2>
            <div className="flex flex-wrap items-center gap-2">
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
                <span className="hidden sm:inline">Download</span> JSON
              </button>
              <button
                onClick={handleDownloadTxt}
                className={cn(
                  "flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5",
                  "text-xs font-medium text-muted",
                  "hover:bg-border/40 hover:text-foreground transition-colors"
                )}
              >
                <FileDown className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">Download</span> TXT
              </button>
            </div>
          </div>
          <pre className="mt-3 max-h-64 overflow-auto rounded-lg bg-background p-3 font-mono text-xs text-foreground whitespace-pre-wrap">
            {JSON.stringify(filterOutputs(run.outputs), null, 2)}
          </pre>
        </div>
      )}

      {/* EU AI Act compliance section */}
      {run.risk_level && (
        <div className="rounded-xl border border-border bg-surface shadow-sm overflow-hidden">
          <button
            onClick={handleLoadTransparencyReport}
            className="w-full flex items-center justify-between px-5 py-3.5 text-left hover:bg-border/20 transition-colors"
          >
            <div className="flex items-center gap-2">
              <Shield className="h-4 w-4 text-accent" />
              <span className="text-sm font-semibold text-foreground">EU AI Act Compliance</span>
              <RiskLevelBadge level={run.risk_level} />
            </div>
            <ChevronRight
              className={cn(
                "h-4 w-4 text-muted transition-transform duration-200",
                transparencyReportExpanded && "rotate-90"
              )}
            />
          </button>
          {transparencyReportExpanded && (
            <div className="border-t border-border px-5 py-4">
              {loadingReport ? (
                <p className="text-sm text-muted">Loading transparency report...</p>
              ) : transparencyReport ? (
                <pre className="max-h-96 overflow-auto rounded-lg bg-background p-4 font-mono text-xs text-foreground whitespace-pre-wrap">
                  {JSON.stringify(transparencyReport, null, 2)}
                </pre>
              ) : (
                <p className="text-sm text-muted">No transparency report available</p>
              )}
            </div>
          )}
        </div>
      )}

      {/* Delete confirm */}
      <ConfirmDialog
        open={deleteConfirmOpen}
        title="Delete Run"
        description={`Are you sure you want to delete run ${run.run_id.slice(0, 8)}...? This action cannot be undone.`}
        confirmLabel={deleting ? "Deleting..." : "Delete"}
        confirmDisabled={deleting}
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

      {/* Celebration Modal */}
      <CelebrationModal
        open={celebrationOpen}
        onClose={() => setCelebrationOpen(false)}
        workflowName={run.workflow_name}
        runId={run.run_id}
        costUsd={run.total_cost_usd}
        durationSeconds={duration}
        milestone={celebrationMilestone}
      />

      {/* AI Chat Sidebar */}
      <AiChatSidebar
        open={chatOpen}
        onClose={() => setChatOpen(false)}
        run={run}
      />
    </div>
  );
}
