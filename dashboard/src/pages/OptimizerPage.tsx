import { useCallback, useEffect, useState } from "react";
import { Gauge, ChevronDown, ChevronUp } from "lucide-react";
import { api } from "@/api/client";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { formatCost, formatRelativeTime, cn } from "@/lib/utils";
import { useNavigate } from "react-router-dom";

interface Alternative {
  model: string;
  score: number;
}

interface SloConfig {
  max_latency_ms: number;
  min_quality: number;
  budget_per_step_usd: number;
}

interface OptimizerDecision {
  id: string;
  run_id: string;
  step_id: string;
  selected_model: string;
  confidence: number;
  reason: string;
  budget_pressure: number | null;
  alternatives: Alternative[];
  slo_config: SloConfig | null;
  created_at: string;
}

interface OptimizerStats {
  total_decisions_30d: number;
  model_distribution: Record<string, number>;
  avg_confidence: number;
  estimated_savings_30d_usd: number;
}

function confidenceColor(c: number): string {
  if (c >= 0.8) return "text-success";
  if (c >= 0.5) return "text-warning";
  return "text-error";
}

function confidenceBarColor(c: number): string {
  if (c >= 0.8) return "bg-success";
  if (c >= 0.5) return "bg-warning";
  return "bg-error";
}

const MODEL_STYLES: Record<string, string> = {
  haiku: "bg-success/10 text-success border-success/30",
  sonnet: "bg-accent/10 text-accent border-accent/30",
  opus: "bg-purple-500/10 text-purple-400 border-purple-500/30",
};

export default function OptimizerPage() {
  const navigate = useNavigate();
  const [decisions, setDecisions] = useState<OptimizerDecision[]>([]);
  const [stats, setStats] = useState<OptimizerStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    const [decRes, statsRes] = await Promise.all([
      api.get<OptimizerDecision[]>("/optimizer/decisions"),
      api.get<OptimizerStats>("/optimizer/stats"),
    ]);
    if (decRes.data) setDecisions(decRes.data);
    if (statsRes.data) setStats(statsRes.data);
    setLoading(false);
  }, []);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  const topModel = stats
    ? Object.entries(stats.model_distribution).sort(([, a], [, b]) => b - a)[0]
    : null;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight text-foreground">Cost-Latency Optimizer</h1>

      {/* Stats cards */}
      {stats && (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">Total Decisions (30d)</p>
            <p className="mt-1 text-2xl font-semibold text-foreground">{stats.total_decisions_30d}</p>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">Avg Confidence</p>
            <p className={cn("mt-1 text-2xl font-semibold", confidenceColor(stats.avg_confidence))}>
              {(stats.avg_confidence * 100).toFixed(0)}%
            </p>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">Top Model</p>
            <div className="mt-1 flex items-center gap-2">
              <p className="text-2xl font-semibold text-foreground capitalize">{topModel?.[0] || "-"}</p>
              {topModel && (
                <span className="text-sm text-muted">{(topModel[1] * 100).toFixed(0)}%</span>
              )}
            </div>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">Estimated Savings</p>
            <p className="mt-1 text-2xl font-semibold text-accent">{formatCost(stats.estimated_savings_30d_usd)}</p>
          </div>
        </div>
      )}

      {/* Decisions */}
      {decisions.length === 0 ? (
        <EmptyState
          icon={Gauge}
          title="No optimizer decisions"
          description="When the optimizer selects models for workflow steps, decisions will appear here."
        />
      ) : (
        <div className="space-y-3">
          {decisions.map((item) => {
            const isExpanded = expandedId === item.id;
            const modelStyle = MODEL_STYLES[item.selected_model] || MODEL_STYLES.sonnet;

            return (
              <div
                key={item.id}
                className="rounded-xl border border-border bg-surface shadow-sm overflow-hidden"
              >
                <div
                  className="flex items-center gap-4 px-5 py-4 cursor-pointer hover:bg-border/10 transition-colors"
                  onClick={() => setExpandedId(isExpanded ? null : item.id)}
                >
                  {/* Model badge */}
                  <div className={cn("flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border", modelStyle)}>
                    <Gauge className="h-5 w-5" />
                  </div>

                  {/* Info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className={cn("rounded-full border px-2.5 py-0.5 text-xs font-semibold capitalize", modelStyle)}>
                        {item.selected_model}
                      </span>
                      <span className="text-sm font-medium text-foreground">{item.step_id}</span>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          navigate(`/runs/${item.run_id}`);
                        }}
                        className="text-xs font-medium text-accent hover:text-accent-hover transition-colors"
                      >
                        run
                      </button>
                    </div>
                    <div className="flex items-center gap-3 mt-1.5">
                      {/* Confidence bar */}
                      <div className="flex items-center gap-2 min-w-[120px]">
                        <div className="h-1.5 w-16 rounded-full bg-border overflow-hidden">
                          <div
                            className={cn("h-full rounded-full transition-all", confidenceBarColor(item.confidence))}
                            style={{ width: `${item.confidence * 100}%` }}
                          />
                        </div>
                        <span className={cn("text-xs font-medium", confidenceColor(item.confidence))}>
                          {(item.confidence * 100).toFixed(0)}%
                        </span>
                      </div>
                      <p className="text-xs text-muted truncate max-w-sm">{item.reason}</p>
                    </div>
                  </div>

                  {/* Budget pressure */}
                  {item.budget_pressure != null && item.budget_pressure >= 0.7 && (
                    <span className={cn(
                      "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium",
                      item.budget_pressure >= 0.9
                        ? "bg-error/15 border-error/30 text-error"
                        : "bg-warning/15 border-warning/30 text-warning"
                    )}>
                      <span className={cn(
                        "h-1.5 w-1.5 rounded-full",
                        item.budget_pressure >= 0.9 ? "bg-error animate-pulse" : "bg-warning"
                      )} />
                      {(item.budget_pressure * 100).toFixed(0)}% budget
                    </span>
                  )}

                  {/* Time + expand */}
                  <span className="text-xs text-muted shrink-0">{formatRelativeTime(item.created_at)}</span>
                  {isExpanded ? (
                    <ChevronUp className="h-4 w-4 text-muted shrink-0" />
                  ) : (
                    <ChevronDown className="h-4 w-4 text-muted shrink-0" />
                  )}
                </div>

                {/* Expanded detail */}
                {isExpanded && (
                  <div className="border-t border-border">
                    {/* Alternatives table */}
                    {item.alternatives.length > 0 && (
                      <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                          <thead>
                            <tr className="border-b border-border bg-background/50">
                              <th className="px-5 py-2.5 text-left font-medium text-muted">Model</th>
                              <th className="px-5 py-2.5 text-right font-medium text-muted">Score</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-border">
                            {item.alternatives.map((alt) => (
                              <tr key={alt.model} className={cn(alt.model === item.selected_model && "bg-accent/5")}>
                                <td className="px-5 py-3">
                                  <div className="flex items-center gap-2">
                                    <span className="font-medium text-foreground capitalize">{alt.model}</span>
                                    {alt.model === item.selected_model && (
                                      <span className="rounded bg-accent/15 px-1.5 py-0.5 text-[10px] font-semibold text-accent">
                                        SELECTED
                                      </span>
                                    )}
                                  </div>
                                </td>
                                <td className="px-5 py-3 text-right font-mono text-muted">{alt.score.toFixed(3)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}

                    {/* SLO config */}
                    {item.slo_config && (
                      <div className="px-5 py-4 bg-background/30">
                        <p className="text-xs font-medium text-muted-foreground mb-2">SLO Configuration</p>
                        <pre className="max-h-32 overflow-auto rounded-lg bg-surface border border-border p-3 font-mono text-xs text-muted">
                          {JSON.stringify(item.slo_config, null, 2)}
                        </pre>
                      </div>
                    )}
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
