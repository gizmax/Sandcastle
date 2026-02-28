import { Fragment, useCallback, useEffect, useState } from "react";
import {
  ClipboardCheck,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  XCircle,
  TrendingUp,
  DollarSign,
  Clock,
} from "lucide-react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { api } from "@/api/client";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { formatCost, formatRelativeTime, cn } from "@/lib/utils";

interface EvalAssertion {
  type: string;
  passed: boolean;
  expected: unknown;
  actual: unknown;
  message: string;
  score: number | null;
}

interface EvalCase {
  case_name: string;
  passed: boolean;
  run_id: string | null;
  cost_usd: number;
  duration_seconds: number;
  assertions: EvalAssertion[];
  output_summary: string | null;
  error: string | null;
}

interface EvalRun {
  id: string;
  suite_name: string;
  workflow_name: string;
  status: string;
  total_cases: number;
  passed_cases: number;
  failed_cases: number;
  pass_rate: number;
  total_cost_usd: number;
  total_duration_seconds: number;
  started_at: string | null;
  completed_at: string | null;
  created_at: string | null;
  cases: EvalCase[] | null;
}

interface EvalStats {
  total_runs: number;
  avg_pass_rate: number;
  total_cost_usd: number;
  last_run_at: string | null;
  pass_rate_trend: { date: string; avg_pass_rate: number; runs: number }[];
}

function passRateColor(rate: number): string {
  if (rate >= 0.8) return "text-success";
  if (rate >= 0.5) return "text-warning";
  return "text-error";
}

function passRateBg(rate: number): string {
  if (rate >= 0.8) return "bg-success";
  if (rate >= 0.5) return "bg-warning";
  return "bg-error";
}

export default function EvaluationsPage() {
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [stats, setStats] = useState<EvalStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [expandedCases, setExpandedCases] = useState<Set<string>>(new Set());

  const fetchData = useCallback(async (cancelled?: { current: boolean }) => {
    try {
      setError(null);
      const [runsRes, statsRes] = await Promise.all([
        api.get<EvalRun[]>("/eval/runs"),
        api.get<EvalStats>("/eval/stats"),
      ]);
      if (cancelled?.current) return;
      if (runsRes.data) setRuns(runsRes.data);
      if (statsRes.data) setStats(statsRes.data);
    } catch {
      if (cancelled?.current) return;
      setError("Could not connect to the API server");
    } finally {
      if (!cancelled?.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const cancelled = { current: false };
    void fetchData(cancelled);
    return () => { cancelled.current = true; };
  }, [fetchData]);

  const toggleCase = (runId: string, caseName: string) => {
    const key = `${runId}:${caseName}`;
    setExpandedCases((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (error) {
    return (
      <ErrorState
        title="Evaluations"
        message={error}
        onRetry={() => { setLoading(true); void fetchData(); }}
      />
    );
  }

  return (
    <div className="space-y-4 sm:space-y-6">
      <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">
        Evaluations
      </h1>

      {/* Stats cards */}
      {stats && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 lg:grid-cols-4">
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">
              Total Runs
            </p>
            <p className="mt-1 text-xl sm:text-2xl font-semibold text-foreground">
              {stats.total_runs}
            </p>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">
              Avg Pass Rate
            </p>
            <div className="mt-1 flex items-center gap-1.5">
              <TrendingUp
                className={cn(
                  "h-5 w-5",
                  passRateColor(stats.avg_pass_rate)
                )}
              />
              <p
                className={cn(
                  "text-xl sm:text-2xl font-semibold",
                  passRateColor(stats.avg_pass_rate)
                )}
              >
                {(stats.avg_pass_rate * 100).toFixed(0)}%
              </p>
            </div>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">
              Total Cost
            </p>
            <div className="mt-1 flex items-center gap-1.5">
              <DollarSign className="h-5 w-5 text-accent" />
              <p className="text-xl sm:text-2xl font-semibold text-foreground">
                {formatCost(stats.total_cost_usd)}
              </p>
            </div>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">
              Last Run
            </p>
            <div className="mt-1 flex items-center gap-1.5">
              <Clock className="h-5 w-5 text-muted" />
              <p className="text-xl sm:text-2xl font-semibold text-foreground">
                {stats.last_run_at
                  ? formatRelativeTime(stats.last_run_at)
                  : "-"}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Pass rate trend chart */}
      {stats && stats.pass_rate_trend.length > 1 && (
        <div className="rounded-xl border border-border bg-surface p-5 shadow-sm">
          <h2 className="text-sm font-medium text-muted-foreground mb-4">
            Pass Rate Trend (30 days)
          </h2>
          <div className="h-52">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={stats.pass_rate_trend}>
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="var(--color-border)"
                />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 11 }}
                  stroke="var(--color-muted)"
                  tickFormatter={(v: string) => v.slice(5)}
                />
                <YAxis
                  tick={{ fontSize: 11 }}
                  stroke="var(--color-muted)"
                  domain={[0, 1]}
                  tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
                />
                <Tooltip
                  contentStyle={{
                    background: "var(--color-surface)",
                    border: "1px solid var(--color-border)",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  formatter={(value: number | undefined) => [
                    `${((value ?? 0) * 100).toFixed(1)}%`,
                    "Pass Rate",
                  ]}
                />
                <Line
                  type="monotone"
                  dataKey="avg_pass_rate"
                  stroke="var(--color-accent)"
                  strokeWidth={2}
                  dot={{ r: 3, fill: "var(--color-accent)" }}
                  activeDot={{ r: 5 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Eval runs list */}
      {runs.length === 0 ? (
        <EmptyState
          icon={ClipboardCheck}
          title="No evaluations yet"
          description="Run your first eval suite via CLI (sandcastle eval suite.yaml) or the API."
        />
      ) : (
        <div className="space-y-3">
          {runs.map((run) => {
            const isExpanded = expandedId === run.id;

            return (
              <div
                key={run.id}
                className="rounded-xl border border-border bg-surface shadow-sm overflow-hidden"
              >
                {/* Run header */}
                <div
                  role="button"
                  tabIndex={0}
                  aria-expanded={isExpanded}
                  aria-label={`${run.suite_name} - ${isExpanded ? "collapse" : "expand"} cases`}
                  className="flex items-center gap-4 px-5 py-4 cursor-pointer hover:bg-border/10 transition-colors focus:outline-none focus:bg-border/10"
                  onClick={() => setExpandedId(isExpanded ? null : run.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setExpandedId(isExpanded ? null : run.id);
                    }
                  }}
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-border bg-background/50">
                    <ClipboardCheck className="h-5 w-5 text-accent" />
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-medium text-foreground truncate">
                        {run.suite_name}
                      </p>
                      <span className="text-xs text-muted">/</span>
                      <p className="text-sm text-accent">{run.workflow_name}</p>
                    </div>
                    <div className="flex items-center gap-3 mt-1">
                      <span className="text-xs text-muted">
                        {run.total_cases} cases
                      </span>
                      <span className="text-xs text-muted">
                        {formatCost(run.total_cost_usd)}
                      </span>
                      <span className="text-xs text-muted">
                        {run.total_duration_seconds.toFixed(1)}s
                      </span>
                      {run.created_at && (
                        <span className="text-xs text-muted">
                          {formatRelativeTime(run.created_at)}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Pass rate badge */}
                  <div className="flex items-center gap-3">
                    <div className="flex items-center gap-1.5">
                      <CheckCircle2 className="h-3.5 w-3.5 text-success" />
                      <span className="text-xs font-medium text-success">
                        {run.passed_cases}
                      </span>
                    </div>
                    {run.failed_cases > 0 && (
                      <div className="flex items-center gap-1.5">
                        <XCircle className="h-3.5 w-3.5 text-error" />
                        <span className="text-xs font-medium text-error">
                          {run.failed_cases}
                        </span>
                      </div>
                    )}
                    <div
                      className={cn(
                        "h-2 w-16 rounded-full bg-border/40 overflow-hidden"
                      )}
                    >
                      <div
                        className={cn(
                          "h-full rounded-full transition-all",
                          passRateBg(run.pass_rate)
                        )}
                        style={{ width: `${run.pass_rate * 100}%` }}
                      />
                    </div>
                    <span
                      className={cn(
                        "text-xs font-semibold min-w-[36px] text-right",
                        passRateColor(run.pass_rate)
                      )}
                    >
                      {(run.pass_rate * 100).toFixed(0)}%
                    </span>
                  </div>

                  {isExpanded ? (
                    <ChevronDown className="h-4 w-4 text-muted shrink-0" />
                  ) : (
                    <ChevronRight className="h-4 w-4 text-muted shrink-0" />
                  )}
                </div>

                {/* Expanded case details */}
                {isExpanded && run.cases && (
                  <div className="border-t border-border">
                    <table className="w-full text-sm" aria-label={`Evaluation cases for ${run.suite_name}`}>
                      <thead>
                        <tr className="border-b border-border bg-background/50">
                          <th className="px-5 py-2.5 text-left font-medium text-muted w-8" />
                          <th className="px-5 py-2.5 text-left font-medium text-muted">
                            Case
                          </th>
                          <th className="px-5 py-2.5 text-center font-medium text-muted">
                            Status
                          </th>
                          <th className="px-5 py-2.5 text-right font-medium text-muted">
                            Assertions
                          </th>
                          <th className="px-5 py-2.5 text-right font-medium text-muted">
                            Cost
                          </th>
                          <th className="px-5 py-2.5 text-right font-medium text-muted">
                            Duration
                          </th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border">
                        {run.cases.map((c) => {
                          const caseKey = `${run.id}:${c.case_name}`;
                          const isCaseExpanded = expandedCases.has(caseKey);
                          const passedCount = c.assertions.filter(
                            (a) => a.passed
                          ).length;
                          const totalCount = c.assertions.length;

                          return (
                            <Fragment key={caseKey}>
                              <tr
                                className="cursor-pointer hover:bg-border/10 transition-colors focus:outline-none focus:bg-border/10"
                                tabIndex={0}
                                aria-expanded={isCaseExpanded}
                                onClick={() =>
                                  toggleCase(run.id, c.case_name)
                                }
                                onKeyDown={(e) => {
                                  if (e.key === "Enter" || e.key === " ") {
                                    e.preventDefault();
                                    toggleCase(run.id, c.case_name);
                                  }
                                }}
                              >
                                <td className="px-5 py-3">
                                  {isCaseExpanded ? (
                                    <ChevronDown className="h-3.5 w-3.5 text-muted" />
                                  ) : (
                                    <ChevronRight className="h-3.5 w-3.5 text-muted" />
                                  )}
                                </td>
                                <td className="px-5 py-3">
                                  <span className="font-medium text-foreground">
                                    {c.case_name}
                                  </span>
                                </td>
                                <td className="px-5 py-3 text-center">
                                  {c.passed ? (
                                    <CheckCircle2 className="h-4 w-4 text-success inline-block" />
                                  ) : (
                                    <XCircle className="h-4 w-4 text-error inline-block" />
                                  )}
                                </td>
                                <td className="px-5 py-3 text-right">
                                  <span
                                    className={cn(
                                      "font-medium",
                                      passedCount === totalCount
                                        ? "text-success"
                                        : "text-error"
                                    )}
                                  >
                                    {passedCount}/{totalCount}
                                  </span>
                                </td>
                                <td className="px-5 py-3 text-right text-muted">
                                  {formatCost(c.cost_usd)}
                                </td>
                                <td className="px-5 py-3 text-right text-muted">
                                  {c.duration_seconds.toFixed(1)}s
                                </td>
                              </tr>
                              {isCaseExpanded && (
                                <tr>
                                  <td colSpan={6} className="px-10 py-3 bg-background/30">
                                    <div className="space-y-2">
                                      {c.assertions.map((a, i) => (
                                        <div
                                          key={`${a.type}-${i}`}
                                          className="flex items-center gap-2 text-xs"
                                        >
                                          {a.passed ? (
                                            <CheckCircle2 className="h-3.5 w-3.5 text-success shrink-0" />
                                          ) : (
                                            <XCircle className="h-3.5 w-3.5 text-error shrink-0" />
                                          )}
                                          <span className="font-mono font-medium text-foreground">
                                            {a.type}
                                          </span>
                                          {a.score !== null && (
                                            <span className="text-muted">
                                              score: {a.score.toFixed(2)}
                                            </span>
                                          )}
                                          {a.message && (
                                            <span className="text-muted truncate">
                                              {a.message}
                                            </span>
                                          )}
                                        </div>
                                      ))}
                                      {c.error && (
                                        <p className="text-xs text-error mt-1">
                                          Error: {c.error}
                                        </p>
                                      )}
                                    </div>
                                  </td>
                                </tr>
                              )}
                            </Fragment>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
