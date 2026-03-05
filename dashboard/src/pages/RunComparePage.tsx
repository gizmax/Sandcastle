import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import {
  ArrowDown,
  ArrowUp,
  Minus,
  GitCompareArrows,
  ArrowLeftRight,
  ChevronDown,
  Clock,
  DollarSign,
  Layers,
  Activity,
} from "lucide-react";
import { api } from "@/api/client";
import { RunStatusBadge } from "@/components/runs/RunStatusBadge";
import { Breadcrumb } from "@/components/shared/Breadcrumb";
import { EmptyState } from "@/components/shared/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { formatCost, formatDuration, formatRelativeTime, parseUTC, cn } from "@/lib/utils";

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

function DeltaChip({ delta, format, invert }: {
  delta: number | null;
  format: "cost" | "duration";
  invert?: boolean;
}) {
  if (delta === null || Math.abs(delta) < 0.001) {
    return (
      <span className="text-xs text-muted inline-flex items-center gap-0.5">
        <Minus className="h-3 w-3" />same
      </span>
    );
  }
  const fmt = format === "cost" ? formatCost : formatDuration;
  const better = invert ? delta > 0 : delta < 0;
  return (
    <span className={cn(
      "text-xs font-medium inline-flex items-center gap-0.5",
      better ? "text-success" : "text-error"
    )}>
      {better ? <ArrowDown className="h-3 w-3" /> : <ArrowUp className="h-3 w-3" />}
      {delta > 0 ? "+" : "-"}{fmt(Math.abs(delta))}
      {format === "duration" && (better ? " faster" : " slower")}
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

function diffLines(textA: string, textB: string): Array<{ type: "same" | "add" | "remove"; text: string }> {
  const linesA = textA.split("\n");
  const linesB = textB.split("\n");
  const result: Array<{ type: "same" | "add" | "remove"; text: string }> = [];

  const setA = new Set(linesA);
  const setB = new Set(linesB);

  let idxA = 0;
  let idxB = 0;
  while (idxA < linesA.length || idxB < linesB.length) {
    if (idxA < linesA.length && idxB < linesB.length && linesA[idxA] === linesB[idxB]) {
      result.push({ type: "same", text: linesA[idxA] });
      idxA++;
      idxB++;
    } else if (idxA < linesA.length && !setB.has(linesA[idxA])) {
      result.push({ type: "remove", text: linesA[idxA] });
      idxA++;
    } else if (idxB < linesB.length && !setA.has(linesB[idxB])) {
      result.push({ type: "add", text: linesB[idxB] });
      idxB++;
    } else if (idxA < linesA.length) {
      result.push({ type: "remove", text: linesA[idxA] });
      idxA++;
    } else {
      result.push({ type: "add", text: linesB[idxB] });
      idxB++;
    }
  }
  return result;
}

function StepRow({ step, expanded, onToggle }: {
  step: StepDiff;
  expanded: boolean;
  onToggle: () => void;
}) {
  const isOnlyA = step.presence === "only_a";
  const isOnlyB = step.presence === "only_b";
  const statusChanged = step.status_a !== step.status_b;
  const failedInAPassedInB = step.status_a === "failed" && step.status_b === "completed";
  const passedInAFailedInB = step.status_a === "completed" && step.status_b === "failed";
  const slower = step.duration_delta > 0.5;
  const costlier = step.cost_delta > 0.005;

  const hasExpandableContent = step.output_changed || step.config_changed || step.error_a || step.error_b;

  const outputA = step.output_a ? JSON.stringify(step.output_a, null, 2) : "";
  const outputB = step.output_b ? JSON.stringify(step.output_b, null, 2) : "";
  const lines = useMemo(
    () => (expanded && step.output_changed ? diffLines(outputA, outputB) : []),
    [expanded, step.output_changed, outputA, outputB]
  );

  return (
    <div className={cn(
      "rounded-xl border bg-surface shadow-sm overflow-hidden transition-colors",
      isOnlyA && "border-error/30 bg-error/5",
      isOnlyB && "border-success/30 bg-success/5",
      failedInAPassedInB && "border-success/30",
      passedInAFailedInB && "border-error/30",
      !isOnlyA && !isOnlyB && !statusChanged && "border-border"
    )}>
      <button
        onClick={hasExpandableContent ? onToggle : undefined}
        className={cn(
          "w-full text-left px-4 py-3 transition-colors",
          hasExpandableContent && "hover:bg-border/10 cursor-pointer",
          !hasExpandableContent && "cursor-default"
        )}
      >
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-0">
          {/* Step name */}
          <div className="flex items-center gap-2 sm:w-[200px] sm:min-w-[200px]">
            {hasExpandableContent && (
              <ChevronDown className={cn(
                "h-3.5 w-3.5 text-muted transition-transform duration-200 shrink-0",
                !expanded && "-rotate-90"
              )} />
            )}
            {!hasExpandableContent && <div className="w-3.5" />}
            <span className="font-mono text-sm font-semibold text-foreground truncate">{step.step_id}</span>
            {step.parallel_index !== null && (
              <span className="rounded bg-accent/10 px-1.5 py-0.5 text-xs text-accent shrink-0">
                [{step.parallel_index}]
              </span>
            )}
            {isOnlyA && <span className="rounded bg-error/15 px-1.5 py-0.5 text-xs text-error shrink-0">Only A</span>}
            {isOnlyB && <span className="rounded bg-success/15 px-1.5 py-0.5 text-xs text-success shrink-0">Only B</span>}
          </div>

          {/* Run A metrics */}
          <div className="flex items-center gap-3 text-xs sm:flex-1 pl-6 sm:pl-0">
            <div className="flex items-center gap-3 sm:w-1/3">
              <StatusDot status={step.status_a} />
              <span className="text-muted-foreground">{formatDuration(step.duration_a)}</span>
              <span className="text-muted-foreground">{formatCost(step.cost_a)}</span>
            </div>

            {/* Separator */}
            <span className="text-muted text-xs hidden sm:inline">vs</span>

            {/* Run B metrics */}
            <div className="flex items-center gap-3 sm:w-1/3">
              <StatusDot status={step.status_b} />
              <span className="text-muted-foreground">{formatDuration(step.duration_b)}</span>
              <span className="text-muted-foreground">{formatCost(step.cost_b)}</span>
            </div>

            {/* Delta */}
            <div className={cn(
              "flex items-center gap-2 sm:w-1/4 sm:justify-end",
              slower && "text-warning",
              costlier && "text-error"
            )}>
              <DeltaChip delta={step.duration_delta} format="duration" />
              <DeltaChip delta={step.cost_delta} format="cost" />
            </div>
          </div>
        </div>
      </button>

      {/* Expanded content */}
      {expanded && (
        <div className="border-t border-border px-4 py-3 space-y-3">
          {/* Config diff */}
          {step.config_changed && step.config_a && step.config_b && (
            <div>
              <div className="text-xs font-medium text-warning mb-1.5">Config Changed</div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <div className="rounded-lg bg-error/5 p-2.5">
                  <div className="text-xs text-muted-foreground mb-1">Run A</div>
                  <pre className="text-xs font-mono text-foreground overflow-auto max-h-32 whitespace-pre-wrap">
                    {JSON.stringify(step.config_a, null, 2)}
                  </pre>
                </div>
                <div className="rounded-lg bg-success/5 p-2.5">
                  <div className="text-xs text-muted-foreground mb-1">Run B</div>
                  <pre className="text-xs font-mono text-foreground overflow-auto max-h-32 whitespace-pre-wrap">
                    {JSON.stringify(step.config_b, null, 2)}
                  </pre>
                </div>
              </div>
            </div>
          )}

          {/* Output diff */}
          {step.output_changed && step.presence === "both" && (
            <div>
              <div className="text-xs font-medium text-accent mb-1.5">Output Diff</div>
              <div className="rounded-lg bg-background/80 p-2.5 overflow-auto max-h-64">
                <pre className="text-xs font-mono whitespace-pre-wrap">
                  {lines.map((line, i) => (
                    <div
                      key={i}
                      className={cn(
                        "px-1",
                        line.type === "remove" && "bg-error/10 text-error",
                        line.type === "add" && "bg-success/10 text-success",
                        line.type === "same" && "text-muted"
                      )}
                    >
                      {line.type === "remove" ? "- " : line.type === "add" ? "+ " : "  "}
                      {line.text}
                    </div>
                  ))}
                </pre>
              </div>
            </div>
          )}

          {/* Errors */}
          {(step.error_a || step.error_b) && (
            <div className="space-y-1">
              {step.error_a && (
                <div className="rounded-lg bg-error/10 px-2.5 py-1.5 text-xs text-error">
                  <span className="font-medium">A:</span> {step.error_a}
                </div>
              )}
              {step.error_b && (
                <div className="rounded-lg bg-error/10 px-2.5 py-1.5 text-xs text-error">
                  <span className="font-medium">B:</span> {step.error_b}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TimelineBar({ steps, totalDuration, label, color }: {
  steps: Array<{ step_id: string; duration: number; status: string | null }>;
  totalDuration: number;
  label: string;
  color: "accent" | "success";
}) {
  if (totalDuration <= 0) return null;
  const statusBarColors: Record<string, string> = {
    completed: color === "accent" ? "bg-accent" : "bg-success",
    failed: "bg-error",
    running: "bg-running",
    pending: "bg-muted/40",
    skipped: "bg-muted/40",
  };

  return (
    <div>
      <div className="text-xs text-muted-foreground mb-1">{label}</div>
      <div className="flex h-6 rounded-lg overflow-hidden bg-background/50 border border-border">
        {steps.map((s, i) => {
          const pct = (s.duration / totalDuration) * 100;
          if (pct < 0.5) return null;
          return (
            <div
              key={`${s.step_id}-${i}`}
              className={cn(
                "h-full relative group transition-opacity hover:opacity-80",
                statusBarColors[s.status || "pending"] || "bg-muted/40"
              )}
              style={{ width: `${pct}%` }}
              title={`${s.step_id}: ${formatDuration(s.duration)}`}
            >
              {pct > 8 && (
                <span className="absolute inset-0 flex items-center justify-center text-[10px] font-medium text-white/80 truncate px-1">
                  {s.step_id}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div className="space-y-4 sm:space-y-6">
      <div className="flex items-center gap-2">
        <Skeleton className="h-4 w-16" />
        <Skeleton className="h-4 w-4 rounded-full" />
        <Skeleton className="h-4 w-12" />
        <Skeleton className="h-4 w-4 rounded-full" />
        <Skeleton className="h-4 w-20" />
      </div>
      <div className="rounded-xl border border-border bg-surface p-4 sm:p-5 space-y-4">
        <Skeleton className="h-6 w-40" />
        <div className="grid grid-cols-2 gap-4">
          <Skeleton className="h-20 rounded-lg" />
          <Skeleton className="h-20 rounded-lg" />
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24 rounded-lg" />
          ))}
        </div>
      </div>
      <Skeleton className="h-16 rounded-xl" />
      <div className="space-y-3">
        <Skeleton className="h-4 w-32" />
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-14 rounded-xl" />
        ))}
      </div>
    </div>
  );
}

export default function RunComparePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const [data, setData] = useState<CompareData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [recentRuns, setRecentRuns] = useState<RunListItem[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());

  const runA = searchParams.get("run_a") || "";
  const runB = searchParams.get("run_b") || "";

  const fetchRecentRuns = useCallback(async () => {
    setRunsLoading(true);
    try {
      const res = await api.get<RunListItem[]>("/runs", { limit: "50" });
      if (res.data) setRecentRuns(res.data);
    } finally {
      setRunsLoading(false);
    }
  }, []);

  const fetchCompare = useCallback(async () => {
    if (!runA || !runB) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const res = await api.get<CompareData>("/runs/compare", { run_a: runA, run_b: runB });
      if (res.data) {
        setData(res.data);
        setError(null);
      }
      if (res.error) setError(res.error.message);
    } finally {
      setLoading(false);
    }
  }, [runA, runB]);

  useEffect(() => {
    void fetchRecentRuns();
  }, [fetchRecentRuns]);

  useEffect(() => {
    void fetchCompare();
  }, [fetchCompare]);

  const handleSelectA = useCallback((id: string) => {
    const params = new URLSearchParams(searchParams);
    params.set("run_a", id);
    setSearchParams(params);
  }, [searchParams, setSearchParams]);

  const handleSelectB = useCallback((id: string) => {
    const params = new URLSearchParams(searchParams);
    params.set("run_b", id);
    setSearchParams(params);
  }, [searchParams, setSearchParams]);

  const handleSwap = useCallback(() => {
    if (!runA && !runB) return;
    const params = new URLSearchParams();
    if (runB) params.set("run_a", runB);
    if (runA) params.set("run_b", runA);
    setSearchParams(params);
  }, [runA, runB, setSearchParams]);

  const toggleStep = useCallback((key: string) => {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const totalDuration = useMemo(() => {
    if (!data) return 0;
    const dA = data.total_duration_a ?? 0;
    const dB = data.total_duration_b ?? 0;
    return Math.max(dA, dB);
  }, [data]);

  const stepsA = useMemo(() => {
    if (!data) return [];
    return data.steps
      .filter((s) => s.presence !== "only_b")
      .map((s) => ({ step_id: s.step_id, duration: s.duration_a, status: s.status_a }));
  }, [data]);

  const stepsB = useMemo(() => {
    if (!data) return [];
    return data.steps
      .filter((s) => s.presence !== "only_a")
      .map((s) => ({ step_id: s.step_id, duration: s.duration_b, status: s.status_b }));
  }, [data]);

  const completedStepsA = data?.steps.filter((s) => s.status_a === "completed").length ?? 0;
  const completedStepsB = data?.steps.filter((s) => s.status_b === "completed").length ?? 0;
  const failedStepsA = data?.steps.filter((s) => s.status_a === "failed").length ?? 0;
  const failedStepsB = data?.steps.filter((s) => s.status_b === "failed").length ?? 0;

  const breadcrumb = (
    <Breadcrumb items={[
      { label: "Overview", href: "/" },
      { label: "Runs", href: "/runs" },
      { label: "Compare" },
    ]} />
  );

  if (loading && runA && runB) {
    return (
      <div className="space-y-4 sm:space-y-6">
        {breadcrumb}
        <LoadingSkeleton />
      </div>
    );
  }

  return (
    <div className="space-y-4 sm:space-y-6">
      {breadcrumb}

      {/* Header + Run Selectors */}
      <div className="rounded-xl border border-border bg-surface p-4 sm:p-5 shadow-sm">
        <h1 className="text-xl font-semibold tracking-tight text-foreground mb-4">
          Replay Studio
        </h1>

        {/* Run selectors */}
        <div className="grid grid-cols-[1fr_auto_1fr] gap-2 sm:gap-3 items-end mb-4">
          <div>
            <label className="block text-xs font-medium text-muted-foreground mb-1">Run A</label>
            <select
              value={runA}
              onChange={(e) => handleSelectA(e.target.value)}
              disabled={runsLoading}
              className={cn(
                "h-9 w-full rounded-lg border border-border bg-background px-3 text-sm text-foreground",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30",
                "transition-colors appearance-none truncate"
              )}
            >
              <option value="">Select a run...</option>
              {recentRuns.map((r) => (
                <option key={r.run_id} value={r.run_id}>
                  {r.workflow_name} ({r.run_id.slice(0, 8)}) - {r.status}
                </option>
              ))}
            </select>
          </div>

          <button
            onClick={handleSwap}
            disabled={!runA && !runB}
            className={cn(
              "flex items-center justify-center h-9 w-9 rounded-lg border border-border",
              "text-muted hover:text-foreground hover:bg-border/40 transition-colors",
              "disabled:opacity-30 disabled:cursor-not-allowed"
            )}
            title="Swap runs"
          >
            <ArrowLeftRight className="h-4 w-4" />
          </button>

          <div>
            <label className="block text-xs font-medium text-muted-foreground mb-1">Run B</label>
            <select
              value={runB}
              onChange={(e) => handleSelectB(e.target.value)}
              disabled={runsLoading}
              className={cn(
                "h-9 w-full rounded-lg border border-border bg-background px-3 text-sm text-foreground",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30",
                "transition-colors appearance-none truncate"
              )}
            >
              <option value="">Select a run...</option>
              {recentRuns.map((r) => (
                <option key={r.run_id} value={r.run_id}>
                  {r.workflow_name} ({r.run_id.slice(0, 8)}) - {r.status}
                </option>
              ))}
            </select>
          </div>
        </div>

        {(!runA || !runB) && !data && (
          <div className="py-12">
            <EmptyState
              icon={GitCompareArrows}
              title="Select two runs to compare"
              description="Choose Run A and Run B from the dropdowns above to see a side-by-side comparison."
            />
          </div>
        )}

        {error && (
          <div className="py-12">
            <EmptyState
              icon={GitCompareArrows}
              title="Cannot compare runs"
              description={error}
            />
          </div>
        )}
      </div>

      {data && (
        <>
          {/* Run badges */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div
              className="rounded-xl border border-border bg-surface p-4 cursor-pointer hover:border-accent/40 transition-colors shadow-sm"
              onClick={() => navigate(`/runs/${data.run_a.run_id}`)}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-medium text-muted-foreground">Run A</span>
                <RunStatusBadge status={data.run_a.status} />
              </div>
              <div className="text-sm font-medium text-foreground truncate">{data.run_a.workflow_name}</div>
              <p className="mt-1 font-mono text-xs text-muted truncate">{data.run_a.run_id}</p>
              {data.run_a.started_at && (
                <p className="mt-1 text-xs text-muted">{formatRelativeTime(data.run_a.started_at)}</p>
              )}
            </div>
            <div
              className="rounded-xl border border-border bg-surface p-4 cursor-pointer hover:border-accent/40 transition-colors shadow-sm"
              onClick={() => navigate(`/runs/${data.run_b.run_id}`)}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-medium text-muted-foreground">Run B</span>
                <RunStatusBadge status={data.run_b.status} />
              </div>
              <div className="text-sm font-medium text-foreground truncate">{data.run_b.workflow_name}</div>
              <p className="mt-1 font-mono text-xs text-muted truncate">{data.run_b.run_id}</p>
              {data.run_b.started_at && (
                <p className="mt-1 text-xs text-muted">{formatRelativeTime(data.run_b.started_at)}</p>
              )}
            </div>
          </div>

          {!data.same_workflow && (
            <div className="rounded-md bg-warning/10 px-3 py-2 text-xs text-warning">
              These runs use different workflows, so step-level comparisons may not be directly comparable.
            </div>
          )}

          {/* Summary comparison cards */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="rounded-xl border border-border bg-surface p-3 shadow-sm">
              <div className="flex items-center gap-1.5 mb-2">
                <Activity className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="text-xs font-medium text-muted-foreground">Status</span>
              </div>
              <div className="flex items-center gap-2">
                <RunStatusBadge status={data.run_a.status} />
                <span className="text-xs text-muted">vs</span>
                <RunStatusBadge status={data.run_b.status} />
              </div>
            </div>

            <div className="rounded-xl border border-border bg-surface p-3 shadow-sm">
              <div className="flex items-center gap-1.5 mb-2">
                <Clock className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="text-xs font-medium text-muted-foreground">Duration</span>
              </div>
              <div className="flex items-baseline gap-1.5 mb-1">
                <span className="text-sm font-semibold text-foreground">
                  {data.total_duration_a !== null ? formatDuration(data.total_duration_a) : "-"}
                </span>
                <span className="text-xs text-muted">vs</span>
                <span className="text-sm font-semibold text-foreground">
                  {data.total_duration_b !== null ? formatDuration(data.total_duration_b) : "-"}
                </span>
              </div>
              <DeltaChip delta={data.total_duration_delta} format="duration" />
            </div>

            <div className="rounded-xl border border-border bg-surface p-3 shadow-sm">
              <div className="flex items-center gap-1.5 mb-2">
                <DollarSign className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="text-xs font-medium text-muted-foreground">Cost</span>
              </div>
              <div className="flex items-baseline gap-1.5 mb-1">
                <span className="text-sm font-semibold text-foreground">{formatCost(data.total_cost_a)}</span>
                <span className="text-xs text-muted">vs</span>
                <span className="text-sm font-semibold text-foreground">{formatCost(data.total_cost_b)}</span>
              </div>
              <DeltaChip delta={data.total_cost_delta} format="cost" />
            </div>

            <div className="rounded-xl border border-border bg-surface p-3 shadow-sm">
              <div className="flex items-center gap-1.5 mb-2">
                <Layers className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="text-xs font-medium text-muted-foreground">Steps</span>
              </div>
              <div className="text-sm font-semibold text-foreground mb-1">
                {data.steps.length} total
              </div>
              <div className="flex flex-wrap gap-x-2 text-xs text-muted">
                <span>{completedStepsA}/{completedStepsB} passed</span>
                {(failedStepsA > 0 || failedStepsB > 0) && (
                  <span className="text-error">{failedStepsA}/{failedStepsB} failed</span>
                )}
              </div>
            </div>
          </div>

          {/* Timestamps */}
          {(data.run_a.started_at || data.run_b.started_at) && (
            <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
                <div>
                  <div className="text-xs font-medium text-muted-foreground mb-1">Started</div>
                  <div className="flex items-center gap-3">
                    <span className="text-foreground">{data.run_a.started_at ? new Date(parseUTC(data.run_a.started_at)).toLocaleString() : "-"}</span>
                    <span className="text-xs text-muted">vs</span>
                    <span className="text-foreground">{data.run_b.started_at ? new Date(parseUTC(data.run_b.started_at)).toLocaleString() : "-"}</span>
                  </div>
                </div>
                <div>
                  <div className="text-xs font-medium text-muted-foreground mb-1">Completed</div>
                  <div className="flex items-center gap-3">
                    <span className="text-foreground">{data.run_a.completed_at ? new Date(parseUTC(data.run_a.completed_at)).toLocaleString() : "-"}</span>
                    <span className="text-xs text-muted">vs</span>
                    <span className="text-foreground">{data.run_b.completed_at ? new Date(parseUTC(data.run_b.completed_at)).toLocaleString() : "-"}</span>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Visual timeline comparison */}
          {totalDuration > 0 && (
            <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
              <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">
                Execution Timeline
              </h2>
              <div className="space-y-3">
                <TimelineBar
                  steps={stepsA}
                  totalDuration={totalDuration}
                  label="Run A"
                  color="accent"
                />
                <TimelineBar
                  steps={stepsB}
                  totalDuration={totalDuration}
                  label="Run B"
                  color="success"
                />
                <div className="flex justify-between text-[10px] text-muted pt-1">
                  <span>0s</span>
                  <span>{formatDuration(totalDuration / 4)}</span>
                  <span>{formatDuration(totalDuration / 2)}</span>
                  <span>{formatDuration((totalDuration * 3) / 4)}</span>
                  <span>{formatDuration(totalDuration)}</span>
                </div>
              </div>
            </div>
          )}

          {/* Step-by-step comparison */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-foreground">
                Step Comparison ({data.steps.length} step{data.steps.length !== 1 ? "s" : ""})
              </h2>
              {/* Column legend */}
              <div className="hidden sm:flex items-center gap-4 text-[10px] text-muted-foreground">
                <span>Step Name</span>
                <span>Run A (Status / Duration / Cost)</span>
                <span>Run B (Status / Duration / Cost)</span>
                <span>Delta</span>
              </div>
            </div>
            <div className="space-y-2">
              {data.steps.map((step, i) => {
                const key = `${step.step_id}-${step.parallel_index ?? i}`;
                return (
                  <StepRow
                    key={key}
                    step={step}
                    expanded={expandedSteps.has(key)}
                    onToggle={() => toggleStep(key)}
                  />
                );
              })}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
