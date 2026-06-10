import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  Clock,
  DollarSign,
  FlaskConical,
  History,
  Layers,
  Minus,
  Play,
  Sparkles,
} from "lucide-react";
import { api } from "@/api/client";
import { Breadcrumb } from "@/components/shared/Breadcrumb";
import { EmptyState } from "@/components/shared/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { cn, formatCost } from "@/lib/utils";

interface WorkflowRow {
  workflow: string;
  runs: number;
  steps: number;
  original_cost_usd: number;
  new_cost_usd: number;
  cost_delta_usd: number;
  cost_delta_pct: number | null;
  quality_old: number | null;
  quality_new: number | null;
  quality_delta_pct: number | null;
  latency_old_seconds: number | null;
  latency_new_seconds: number | null;
  latency_delta_pct: number | null;
}

interface TimeMachineReport {
  mode: "dry_run" | "live";
  target_model: string;
  judge_model: string | null;
  selection: {
    runs: number;
    steps: number;
    workflows: string[];
    original_cost_usd: number;
    window_days: number;
  };
  cost: { original_usd: number; new_usd: number; delta_usd: number; delta_pct: number | null };
  quality: { old_avg: number | null; new_avg: number | null; delta_pct: number | null } | null;
  latency: {
    old_avg_seconds: number | null;
    new_avg_seconds: number | null;
    delta_pct: number | null;
  } | null;
  live: {
    measured_cost_usd: number;
    budget_usd: number | null;
    steps_replayed: number;
    steps_failed: number;
    truncated: boolean;
  } | null;
  extrapolation: {
    window_days: number;
    monthly_original_usd: number;
    monthly_projected_usd: number;
    monthly_savings_usd: number;
  };
  per_workflow: WorkflowRow[];
  verdict: string;
}

interface TimeMachineJob {
  job_id: string;
  status: string;
  created_at: string | null;
  completed_at: string | null;
  report: TimeMachineReport | null;
  error: string | null;
}

interface JobSummary {
  job_id: string;
  status: string;
  created_at: string | null;
  mode: string;
  target_model: string | null;
  verdict: string | null;
  error: string | null;
}

const MODEL_GROUPS: Array<{ label: string; models: string[] }> = [
  { label: "Claude", models: ["sonnet", "haiku", "opus"] },
  { label: "OpenAI", models: ["openai/codex", "openai/codex-mini"] },
  { label: "Mistral (EU)", models: ["mistral/large", "mistral/small", "mistral/codestral"] },
  { label: "Google", models: ["google/gemini-2.5-pro"] },
  { label: "MiniMax", models: ["minimax/m2.5"] },
  {
    label: "Local (free)",
    models: [
      "nim/llama-3.1-70b",
      "nim/llama-3.1-8b",
      "nim/qwen2.5-coder-32b",
      "ollama",
      "omlx/llama-4-scout",
    ],
  },
];

const RANGE_OPTIONS = [
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "90d", label: "Last 90 days" },
];

function PctChip({ pct, betterWhenNegative = true, suffix }: {
  pct: number | null | undefined;
  betterWhenNegative?: boolean;
  suffix?: string;
}) {
  if (pct === null || pct === undefined) {
    return (
      <span className="inline-flex items-center gap-0.5 text-xs text-muted">
        <Minus className="h-3 w-3" />n/a
      </span>
    );
  }
  if (Math.abs(pct) < 0.05) {
    return (
      <span className="inline-flex items-center gap-0.5 text-xs text-muted">
        <Minus className="h-3 w-3" />same
      </span>
    );
  }
  const better = betterWhenNegative ? pct < 0 : pct > 0;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-0.5 text-xs font-medium",
        better ? "text-success" : "text-error"
      )}
    >
      {pct < 0 ? <ArrowDown className="h-3 w-3" /> : <ArrowUp className="h-3 w-3" />}
      {pct > 0 ? "+" : ""}{pct.toFixed(1)}%{suffix ? ` ${suffix}` : ""}
    </span>
  );
}

function SummaryCard({ icon: Icon, label, children }: {
  icon: typeof Clock;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-border bg-surface p-3 shadow-sm">
      <div className="mb-2 flex items-center gap-1.5">
        <Icon className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
      </div>
      {children}
    </div>
  );
}

function ReportView({ report }: { report: TimeMachineReport }) {
  const savings = report.extrapolation.monthly_savings_usd;

  if (report.selection.runs === 0) {
    return (
      <div className="rounded-xl border border-border bg-surface py-12 shadow-sm">
        <EmptyState
          icon={History}
          title="No recorded workload in this window"
          description="The Time Machine replays your real runs, not synthetic benchmarks. Run a few workflows (or widen the date range), then come back to test any model against them."
        />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Headline verdict */}
      <div
        className={cn(
          "rounded-xl border p-4 sm:p-5 shadow-sm",
          savings >= 0 ? "border-success/40 bg-success/5" : "border-warning/40 bg-warning/5"
        )}
        data-testid="tm-verdict"
      >
        <div className="flex items-start gap-3">
          <Sparkles className={cn("mt-0.5 h-5 w-5 shrink-0", savings >= 0 ? "text-success" : "text-warning")} />
          <div>
            <div className="text-sm font-semibold text-foreground">{report.verdict}</div>
            <div className="mt-1 text-xs text-muted-foreground">
              Based on {report.selection.runs} recorded run{report.selection.runs !== 1 ? "s" : ""} /{" "}
              {report.selection.steps} LLM steps over ~{Math.round(report.selection.window_days)} day
              {Math.round(report.selection.window_days) !== 1 ? "s" : ""}
              {report.mode === "dry_run"
                ? " - projected from recorded token volume, no API calls made."
                : ` - live replay judged by ${report.judge_model ?? "LLM judge"}.`}
            </div>
          </div>
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <SummaryCard icon={DollarSign} label="Cost (selection)">
          <div className="mb-1 flex items-baseline gap-1.5">
            <span className="text-sm font-semibold text-foreground">
              {formatCost(report.cost.original_usd)}
            </span>
            <span className="text-xs text-muted">to</span>
            <span className="text-sm font-semibold text-foreground">
              {formatCost(report.cost.new_usd)}
            </span>
          </div>
          <PctChip pct={report.cost.delta_pct} />
        </SummaryCard>

        <SummaryCard icon={FlaskConical} label="Quality (judge 0-10)">
          {report.quality && report.quality.old_avg !== null ? (
            <>
              <div className="mb-1 flex items-baseline gap-1.5">
                <span className="text-sm font-semibold text-foreground">
                  {report.quality.old_avg?.toFixed(1)}
                </span>
                <span className="text-xs text-muted">to</span>
                <span className="text-sm font-semibold text-foreground">
                  {report.quality.new_avg?.toFixed(1)}
                </span>
              </div>
              <PctChip pct={report.quality.delta_pct} betterWhenNegative={false} />
            </>
          ) : (
            <div className="text-xs text-muted">
              Dry run - run a live replay to score quality
            </div>
          )}
        </SummaryCard>

        <SummaryCard icon={Clock} label="Latency (avg / step)">
          {report.latency && report.latency.old_avg_seconds !== null ? (
            <>
              <div className="mb-1 flex items-baseline gap-1.5">
                <span className="text-sm font-semibold text-foreground">
                  {report.latency.old_avg_seconds?.toFixed(1)}s
                </span>
                <span className="text-xs text-muted">to</span>
                <span className="text-sm font-semibold text-foreground">
                  {report.latency.new_avg_seconds?.toFixed(1)}s
                </span>
              </div>
              <PctChip pct={report.latency.delta_pct} />
            </>
          ) : (
            <div className="text-xs text-muted">Measured during live replay</div>
          )}
        </SummaryCard>

        <SummaryCard icon={Layers} label="Monthly projection">
          <div className="mb-1 text-sm font-semibold text-foreground">
            {formatCost(report.extrapolation.monthly_original_usd)} to{" "}
            {formatCost(report.extrapolation.monthly_projected_usd)}
          </div>
          <span className={cn("text-xs font-medium", savings >= 0 ? "text-success" : "text-error")}>
            {savings >= 0 ? "saves" : "adds"} {formatCost(Math.abs(savings))}/mo
          </span>
        </SummaryCard>
      </div>

      {/* Live replay stats */}
      {report.live && (
        <div className="rounded-xl border border-border bg-surface p-3 text-xs text-muted-foreground shadow-sm">
          Live replay: {report.live.steps_replayed} steps re-executed
          {report.live.steps_failed > 0 && (
            <span className="text-error"> ({report.live.steps_failed} failed)</span>
          )}
          , measured {formatCost(report.live.measured_cost_usd)}
          {report.live.budget_usd !== null && <> of {formatCost(report.live.budget_usd)} budget</>}
          {report.live.truncated && <span className="text-warning"> - truncated at budget cap</span>}
        </div>
      )}

      {/* Per-workflow table */}
      <div className="overflow-hidden rounded-xl border border-border bg-surface shadow-sm">
        <div className="border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold text-foreground">
            Per-workflow deltas ({report.per_workflow.length})
          </h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th className="px-4 py-2 font-medium">Workflow</th>
                <th className="px-4 py-2 text-right font-medium">Runs</th>
                <th className="px-4 py-2 text-right font-medium">Cost</th>
                <th className="px-4 py-2 text-right font-medium">Quality</th>
                <th className="px-4 py-2 text-right font-medium">Latency</th>
              </tr>
            </thead>
            <tbody>
              {report.per_workflow.map((wf) => (
                <tr key={wf.workflow} className="border-b border-border/50 last:border-0">
                  <td className="px-4 py-2.5 font-mono text-xs font-medium text-foreground">
                    {wf.workflow}
                  </td>
                  <td className="px-4 py-2.5 text-right text-xs text-muted-foreground">
                    {wf.runs}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <div className="text-xs text-muted-foreground">
                      {formatCost(wf.original_cost_usd)} to {formatCost(wf.new_cost_usd)}
                    </div>
                    <PctChip pct={wf.cost_delta_pct} />
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    {wf.quality_old !== null && wf.quality_new !== null ? (
                      <>
                        <div className="text-xs text-muted-foreground">
                          {wf.quality_old.toFixed(1)} to {wf.quality_new.toFixed(1)}
                        </div>
                        <PctChip pct={wf.quality_delta_pct} betterWhenNegative={false} />
                      </>
                    ) : (
                      <span className="text-xs text-muted">-</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    {wf.latency_old_seconds !== null && wf.latency_new_seconds !== null ? (
                      <>
                        <div className="text-xs text-muted-foreground">
                          {wf.latency_old_seconds.toFixed(1)}s to {wf.latency_new_seconds.toFixed(1)}s
                        </div>
                        <PctChip pct={wf.latency_delta_pct} />
                      </>
                    ) : (
                      <span className="text-xs text-muted">-</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export default function TimeMachinePage() {
  const [targetModel, setTargetModel] = useState("nim/llama-3.1-70b");
  const [workflow, setWorkflow] = useState("");
  const [range, setRange] = useState("30d");
  const [maxCassettes, setMaxCassettes] = useState(20);
  const [live, setLive] = useState(false);
  const [budget, setBudget] = useState("5.00");
  const [workflows, setWorkflows] = useState<string[]>([]);
  const [history, setHistory] = useState<JobSummary[]>([]);
  const [running, setRunning] = useState(false);
  const [report, setReport] = useState<TimeMachineReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const fetchHistory = useCallback(async () => {
    const res = await api.get<JobSummary[]>("/timemachine");
    if (res.data) setHistory(res.data);
  }, []);

  useEffect(() => {
    void fetchHistory();
    void (async () => {
      const res = await api.get<Array<{ name: string }>>("/workflows");
      if (res.data) setWorkflows(res.data.map((w) => w.name).filter(Boolean));
    })();
    return stopPolling;
  }, [fetchHistory, stopPolling]);

  const pollJob = useCallback(
    (jobId: string) => {
      stopPolling();
      pollRef.current = setInterval(async () => {
        const res = await api.get<TimeMachineJob>(`/timemachine/${jobId}`);
        const job = res.data;
        if (!job) return;
        if (job.status === "running") return;
        stopPolling();
        setRunning(false);
        if (job.status === "completed" && job.report) {
          setReport(job.report);
        } else {
          setError(job.error || "Time Machine job failed");
        }
        void fetchHistory();
      }, 1500);
    },
    [fetchHistory, stopPolling]
  );

  const handleRun = useCallback(async () => {
    setError(null);
    setReport(null);
    setRunning(true);
    const body: Record<string, unknown> = {
      target_model: targetModel,
      since: range,
      max_cassettes: maxCassettes,
      live,
    };
    if (workflow) body.workflow = workflow;
    if (live) body.budget_usd = parseFloat(budget) || 0;
    const res = await api.post<{ job_id: string }>("/timemachine", body);
    if (res.error || !res.data) {
      setRunning(false);
      setError(res.error?.message || "Failed to start Time Machine job");
      return;
    }
    pollJob(res.data.job_id);
  }, [budget, live, maxCassettes, pollJob, range, targetModel, workflow]);

  const loadJob = useCallback(async (jobId: string) => {
    setError(null);
    const res = await api.get<TimeMachineJob>(`/timemachine/${jobId}`);
    if (res.data?.report) setReport(res.data.report);
    else if (res.data?.error) setError(res.data.error);
  }, []);

  return (
    <div className="space-y-4 sm:space-y-6">
      <Breadcrumb items={[{ label: "Overview", href: "/" }, { label: "Time Machine" }]} />

      {/* Configuration */}
      <div className="rounded-xl border border-border bg-surface p-4 shadow-sm sm:p-5">
        <div className="mb-1 flex items-center gap-2">
          <History className="h-5 w-5 text-accent" />
          <h1 className="text-xl font-semibold tracking-tight text-foreground">Model Time Machine</h1>
        </div>
        <p className="mb-4 text-sm text-muted-foreground">
          Re-run your real recorded workload against a different model and get the cost, quality and
          latency delta - no synthetic benchmarks.
        </p>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <label htmlFor="tm-model" className="mb-1 block text-xs font-medium text-muted-foreground">
              Target model
            </label>
            <select
              id="tm-model"
              value={targetModel}
              onChange={(e) => setTargetModel(e.target.value)}
              className="h-9 w-full appearance-none rounded-lg border border-border bg-background px-3 text-sm text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
            >
              {MODEL_GROUPS.map((g) => (
                <optgroup key={g.label} label={g.label}>
                  {g.models.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="tm-workflow" className="mb-1 block text-xs font-medium text-muted-foreground">
              Workflow
            </label>
            <select
              id="tm-workflow"
              value={workflow}
              onChange={(e) => setWorkflow(e.target.value)}
              className="h-9 w-full appearance-none rounded-lg border border-border bg-background px-3 text-sm text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
            >
              <option value="">All workflows</option>
              {workflows.map((w) => (
                <option key={w} value={w}>{w}</option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="tm-range" className="mb-1 block text-xs font-medium text-muted-foreground">
              Time range
            </label>
            <select
              id="tm-range"
              value={range}
              onChange={(e) => setRange(e.target.value)}
              className="h-9 w-full appearance-none rounded-lg border border-border bg-background px-3 text-sm text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
            >
              {RANGE_OPTIONS.map((r) => (
                <option key={r.value} value={r.value}>{r.label}</option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="tm-max" className="mb-1 block text-xs font-medium text-muted-foreground">
              Max runs to replay
            </label>
            <input
              id="tm-max"
              type="number"
              min={1}
              max={500}
              value={maxCassettes}
              onChange={(e) => setMaxCassettes(Math.max(1, parseInt(e.target.value, 10) || 1))}
              className="h-9 w-full rounded-lg border border-border bg-background px-3 text-sm text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
            />
          </div>
        </div>

        <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center">
          <label className="flex cursor-pointer items-center gap-2 text-sm text-foreground">
            <input
              type="checkbox"
              checked={live}
              onChange={(e) => setLive(e.target.checked)}
              className="h-4 w-4 rounded border-border accent-accent"
            />
            Live replay
            <span className="text-xs text-muted-foreground">
              (re-executes steps with real API calls + LLM-judge quality scoring)
            </span>
          </label>

          {live && (
            <div className="flex items-center gap-2">
              <label htmlFor="tm-budget" className="text-xs font-medium text-muted-foreground">
                Budget cap (USD)
              </label>
              <input
                id="tm-budget"
                type="number"
                min={0.01}
                step={0.5}
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
                className="h-8 w-24 rounded-lg border border-border bg-background px-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
              />
            </div>
          )}

          <button
            onClick={handleRun}
            disabled={running}
            className={cn(
              "inline-flex h-9 items-center gap-2 rounded-lg bg-accent px-4 text-sm font-medium text-white",
              "transition-colors hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50",
              "sm:ml-auto"
            )}
          >
            <Play className="h-4 w-4" />
            {running ? "Replaying..." : live ? "Run live replay" : "Run dry-run estimate"}
          </button>
        </div>

        {!live && (
          <p className="mt-2 text-xs text-muted">
            Dry run is free and instant: recorded token volumes are priced against the target
            model's pricing table. No API calls are made.
          </p>
        )}
      </div>

      {error && (
        <div className="rounded-xl border border-error/30 bg-error/5 px-4 py-3 text-sm text-error">
          {error}
        </div>
      )}

      {running && (
        <div className="space-y-3" data-testid="tm-loading">
          <Skeleton className="h-20 rounded-xl" />
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-24 rounded-xl" />
            ))}
          </div>
          <Skeleton className="h-40 rounded-xl" />
        </div>
      )}

      {!running && report && <ReportView report={report} />}

      {!running && !report && !error && (
        <div className="rounded-xl border border-border bg-surface py-12 shadow-sm">
          <EmptyState
            icon={History}
            title="Test any model against your real workload"
            description="Pick a target model and a time range, then run a free dry-run estimate. The Time Machine replays last month's actual runs - your prompts, your data - and reports what would change."
          />
        </div>
      )}

      {/* Recent reports */}
      {history.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-border bg-surface shadow-sm">
          <div className="border-b border-border px-4 py-3">
            <h2 className="text-sm font-semibold text-foreground">Recent reports</h2>
          </div>
          <ul className="divide-y divide-border/50">
            {history.map((j) => (
              <li key={j.job_id}>
                <button
                  onClick={() => void loadJob(j.job_id)}
                  className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-border/10"
                >
                  <span
                    className={cn(
                      "h-1.5 w-1.5 shrink-0 rounded-full",
                      j.status === "completed" && "bg-success",
                      j.status === "failed" && "bg-error",
                      j.status === "running" && "bg-running"
                    )}
                  />
                  <span className="font-mono text-xs font-medium text-foreground">
                    {j.target_model || "?"}
                  </span>
                  <span className="rounded bg-accent/10 px-1.5 py-0.5 text-[10px] uppercase text-accent">
                    {j.mode === "live" ? "live" : "dry run"}
                  </span>
                  <span className="hidden truncate text-xs text-muted-foreground sm:inline">
                    {j.verdict || j.error || j.status}
                  </span>
                  {j.created_at && (
                    <span className="ml-auto shrink-0 text-xs text-muted">
                      {new Date(j.created_at).toLocaleString()}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
