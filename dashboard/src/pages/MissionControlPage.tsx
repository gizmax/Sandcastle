import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  CheckCircle2,
  Crosshair,
  Radar,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { api } from "@/api/client";
import { useRunStream } from "@/hooks/useRunStream";
import { MissionDag } from "@/components/runs/mission/MissionDag";
import { TelemetryRail } from "@/components/runs/mission/TelemetryRail";
import { ThoughtStream } from "@/components/runs/mission/ThoughtStream";
import { RunStatusBadge } from "@/components/runs/RunStatusBadge";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import {
  aggregateTelemetry,
  buildThoughtFeed,
  computeThroughput,
  formatClock,
  initialMissionState,
  isTerminalRunStatus,
  mergeLiveSteps,
  reduceMissionEvents,
  synthesizeEventsFromSteps,
  type MissionStep,
} from "@/lib/missionControl";
import { cn, formatCost, parseUTC } from "@/lib/utils";

interface RunDetail {
  run_id: string;
  workflow_name: string;
  status: string;
  total_cost_usd: number;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  steps: MissionStep[] | null;
}

interface WorkflowStepInfo {
  id: string;
  depends_on?: string[] | null;
  model?: string | null;
}

const POLL_INTERVAL_MS = 4000;

/** Connection indicator for the top bar. */
function ConnectionPill({
  streamStatus,
  isActive,
  onRetry,
}: {
  streamStatus: string;
  isActive: boolean;
  onRetry: () => void;
}) {
  if (!isActive) {
    return (
      <span className="flex items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted">
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-muted-foreground" />
        Final
      </span>
    );
  }
  if (streamStatus === "connected") {
    return (
      <span className="flex items-center gap-1.5 rounded-full border border-success/30 bg-success/10 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-success">
        <span className="status-dot-running inline-block h-1.5 w-1.5 rounded-full bg-success" />
        Live
      </span>
    );
  }
  if (streamStatus === "connecting" || streamStatus === "reconnecting") {
    return (
      <span className="flex items-center gap-1.5 rounded-full border border-warning/30 bg-warning/10 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-warning">
        <RefreshCw className="h-2.5 w-2.5 animate-spin" />
        {streamStatus === "reconnecting" ? "Reconnecting" : "Connecting"}
      </span>
    );
  }
  return (
    <button
      onClick={onRetry}
      className="flex items-center gap-1.5 rounded-full border border-error/30 bg-error/10 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-error hover:bg-error/20 transition-colors"
    >
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-error" />
      Offline — retry
    </button>
  );
}

export default function MissionControlPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [wfSteps, setWfSteps] = useState<WorkflowStepInfo[] | null>(null);
  const [follow, setFollow] = useState(true);
  const [now, setNow] = useState(() => Date.now());
  const throughputSamplesRef = useRef<{ t: number; tokens: number }[]>([]);

  const fetchRun = useCallback(async () => {
    if (!id) return;
    try {
      const res = await api.get<RunDetail>(`/runs/${id}`);
      if (res.data) {
        setRun(res.data);
        setLoadError(null);
      } else if (res.error) {
        setLoadError(res.error.message || "Failed to load run");
      }
    } catch {
      setLoadError("Could not connect to the API server");
    }
  }, [id]);

  useEffect(() => {
    setRun(null);
    setLoadError(null);
    void fetchRun();
  }, [fetchRun]);

  // Workflow definition gives the DAG shape (depends_on per step)
  const workflowName = run?.workflow_name ?? null;
  useEffect(() => {
    if (!workflowName) return;
    let cancelled = false;
    (async () => {
      const res = await api.get<{ steps?: WorkflowStepInfo[] }>(
        `/workflows/${encodeURIComponent(workflowName)}`
      );
      if (cancelled) return;
      const steps = res.data?.steps;
      if (Array.isArray(steps) && steps.length > 0) {
        setWfSteps(steps.filter((s) => s?.id));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [workflowName]);

  // Live SSE stream while the run is active
  const runIsActive = run != null && !isTerminalRunStatus(run.status);
  const { events, status: streamStatus, retry } = useRunStream(id ?? null, runIsActive);

  // Fold the event log into the mission state machine
  const missionState = useMemo(() => {
    const base = initialMissionState(run ?? undefined);
    return reduceMissionEvents(base, events);
  }, [run, events]);

  const isActive = run != null && !missionState.finished && !isTerminalRunStatus(missionState.runStatus);

  // Poll REST while active (outputs, models and durable state catch up here)
  useEffect(() => {
    if (!isActive) return;
    const interval = setInterval(() => void fetchRun(), POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [isActive, fetchRun]);

  // One final fetch when the stream reports the terminal result
  const finishedRef = useRef(false);
  useEffect(() => {
    if (missionState.finished && !finishedRef.current) {
      finishedRef.current = true;
      void fetchRun();
    }
  }, [missionState.finished, fetchRun]);

  // 1s clock tick while active (elapsed time, throughput decay)
  useEffect(() => {
    if (!isActive) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [isActive]);

  // Esc returns to the run detail page
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && id) navigate(`/runs/${id}`);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [id, navigate]);

  // Merge live overrides into REST steps and attach DAG dependencies
  const liveSteps = useMemo<MissionStep[]>(() => {
    let base = run?.steps ?? [];

    // A queued run has no step rows yet - show the workflow's DAG as
    // dim pending nodes so the stage is set before the agent arrives.
    if (base.length === 0 && wfSteps) {
      base = wfSteps.map((s) => ({
        step_id: s.id,
        parallel_index: null,
        status: "pending",
        cost_usd: 0,
        duration_seconds: 0,
        model: s.model ?? null,
        output: null,
      }));
    }

    const merged = mergeLiveSteps(base, missionState);

    if (!wfSteps) {
      // Fallback: linear chain in execution order
      return merged.map((s, i) => ({
        ...s,
        depends_on: i > 0 ? [merged[i - 1].step_id] : [],
      }));
    }

    const dependsOn = new Map(wfSteps.map((s) => [s.id, s.depends_on ?? []]));
    const models = new Map(wfSteps.map((s) => [s.id, s.model ?? null]));
    return merged.map((s) => ({
      ...s,
      model: s.model ?? models.get(s.step_id) ?? null,
      depends_on: dependsOn.get(s.step_id) ?? [],
    }));
  }, [run?.steps, missionState, wfSteps]);

  const telemetry = useMemo(
    () => aggregateTelemetry(liveSteps, missionState),
    [liveSteps, missionState]
  );

  // Token throughput from cumulative-estimate samples
  const throughput = useMemo(() => {
    const samples = throughputSamplesRef.current;
    const last = samples[samples.length - 1];
    if (!last || last.tokens !== telemetry.tokensEst) {
      samples.push({ t: Date.now(), tokens: telemetry.tokensEst });
      if (samples.length > 100) samples.splice(0, samples.length - 100);
    }
    return computeThroughput(samples);
    // `now` keeps the rate decaying between samples while the clock ticks
  }, [telemetry.tokensEst, now]); // eslint-disable-line react-hooks/exhaustive-deps

  const elapsedSeconds = useMemo(() => {
    if (!run?.started_at) return 0;
    const start = parseUTC(run.started_at).getTime();
    if (run.completed_at) return (parseUTC(run.completed_at).getTime() - start) / 1000;
    if (!isActive) return 0;
    return Math.max(0, (now - start) / 1000);
  }, [run?.started_at, run?.completed_at, isActive, now]);

  // Thought stream: live events, or the recorded history for finished runs
  const feed = useMemo(() => {
    if (events.length > 0) return buildThoughtFeed(events, liveSteps);
    if (run && isTerminalRunStatus(run.status)) {
      return buildThoughtFeed(synthesizeEventsFromSteps(liveSteps, run), liveSteps);
    }
    return [];
  }, [events, liveSteps, run]);

  const displayStatus = missionState.runStatus;
  const isQueued = displayStatus === "queued" && telemetry.stepsRunning === 0;
  const finished = run != null && !isActive;

  if (!run) {
    return (
      <div className="dark fixed inset-0 z-40 flex flex-col items-center justify-center gap-4 bg-background text-foreground">
        {loadError ? (
          <>
            <p className="text-sm text-muted">{loadError}</p>
            <button
              onClick={() => navigate("/runs")}
              className="inline-flex items-center gap-1.5 text-sm font-medium text-accent hover:text-accent-hover transition-colors"
            >
              <ArrowLeft className="h-4 w-4" />
              Back to Runs
            </button>
          </>
        ) : (
          <LoadingSpinner size="lg" />
        )}
      </div>
    );
  }

  return (
    <div className="dark fixed inset-0 z-40 flex flex-col bg-background text-foreground">
      {/* Top bar */}
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-border bg-surface/80 px-4 backdrop-blur">
        <Radar className="h-4 w-4 text-accent" />
        <span className="text-[11px] font-semibold uppercase tracking-[0.2em] text-accent">
          Mission Control
        </span>
        <span className="hidden text-muted sm:inline">/</span>
        <span className="hidden truncate text-sm font-medium text-foreground sm:inline">
          {run.workflow_name}
        </span>
        <span className="hidden font-mono text-[11px] text-muted md:inline">
          {run.run_id.slice(0, 8)}
        </span>
        <RunStatusBadge status={displayStatus} />

        <div className="ml-auto flex items-center gap-2">
          <ConnectionPill streamStatus={streamStatus} isActive={isActive} onRetry={retry} />
          <button
            onClick={() => setFollow((f) => !f)}
            title={follow ? "Stop following the active step" : "Follow the active step"}
            aria-pressed={follow}
            className={cn(
              "flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[11px] font-medium transition-colors",
              follow
                ? "border-accent/40 bg-accent/10 text-accent"
                : "border-border text-muted hover:text-foreground hover:bg-border/40"
            )}
          >
            <Crosshair className="h-3 w-3" />
            Follow
          </button>
          <button
            onClick={() => navigate(`/runs/${run.run_id}`)}
            className="flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-[11px] font-medium text-muted hover:text-foreground hover:bg-border/40 transition-colors"
          >
            <ArrowLeft className="h-3 w-3" />
            Exit
            <kbd className="rounded border border-border bg-background px-1 text-[9px] text-muted-foreground">
              Esc
            </kbd>
          </button>
        </div>
      </header>

      {/* Run-complete banner */}
      {finished && (
        <div
          className={cn(
            "flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-b px-4 py-2 text-xs",
            displayStatus === "completed"
              ? "border-success/30 bg-success/10 text-success"
              : displayStatus === "failed"
                ? "border-error/30 bg-error/10 text-error"
                : "border-warning/30 bg-warning/10 text-warning"
          )}
        >
          {displayStatus === "completed" ? (
            <CheckCircle2 className="h-3.5 w-3.5" />
          ) : (
            <XCircle className="h-3.5 w-3.5" />
          )}
          <span className="font-semibold">
            {displayStatus === "completed" ? "Run complete" : `Run ${displayStatus.replace(/_/g, " ")}`}
          </span>
          <span className="font-data text-foreground/80">
            {telemetry.stepsDone}/{telemetry.stepsTotal} steps · {formatCost(missionState.totalCostUsd)} ·{" "}
            {formatClock(elapsedSeconds)}
          </span>
          {(missionState.error ?? run.error) && (
            <span className="min-w-0 truncate font-mono text-error/90">
              {missionState.error ?? run.error}
            </span>
          )}
        </div>
      )}

      {/* Stage */}
      <div className="flex min-h-0 flex-1">
        {/* DAG theater */}
        <div className="relative min-w-0 flex-1">
          {liveSteps.length > 0 ? (
            <MissionDag steps={liveSteps} activeStepId={telemetry.activeStepId} follow={follow} />
          ) : (
            <div className="bg-grid h-full w-full" />
          )}

          {/* Queued overlay */}
          {isQueued && liveSteps.every((s) => s.status !== "running") && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
              <div className="ambient-glow flex flex-col items-center gap-3 rounded-2xl border border-queued/30 bg-surface/90 px-8 py-6 shadow-lg backdrop-blur">
                <span className="relative flex h-10 w-10 items-center justify-center">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-queued/30" />
                  <Radar className="h-5 w-5 text-queued" />
                </span>
                <p className="text-sm font-medium text-foreground">Waiting for a runner…</p>
                <p className="text-xs text-muted">The agent will appear here the moment it starts.</p>
              </div>
            </div>
          )}
        </div>

        {/* Right rail: telemetry + thought stream */}
        <aside className="flex w-[360px] shrink-0 flex-col border-l border-border bg-surface/40 lg:w-[400px]">
          <TelemetryRail
            telemetry={telemetry}
            throughput={throughput}
            elapsedSeconds={elapsedSeconds}
            isLive={isActive}
          />
          <ThoughtStream entries={feed} isLive={isActive} />
        </aside>
      </div>
    </div>
  );
}
