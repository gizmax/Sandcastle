import { useCallback, useEffect, useState } from "react";
import { FlaskConical, TrendingUp, TrendingDown, Trophy, RotateCcw, Rocket } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { formatCost, formatRelativeTime, cn } from "@/lib/utils";

interface Sample {
  id: string;
  variant_id: string;
  quality_score: number | null;
  cost_usd: number;
  duration_seconds: number;
}

interface Experiment {
  id: string;
  workflow_name: string;
  step_id: string;
  status: string;
  optimize_for: string;
  config: Record<string, unknown> | null;
  deployed_variant_id: string | null;
  created_at: string | null;
  completed_at: string | null;
  samples: Sample[] | null;
  rollout_stage: string | null;
}

interface AutoPilotStats {
  total_experiments: number;
  active_experiments: number;
  deploying_experiments: number;
  completed_experiments: number;
  total_samples: number;
  avg_quality_improvement: number;
  total_cost_savings_usd: number;
}

const STATUS_MAP: Record<string, { bg: string; text: string; dot: string; label: string }> = {
  active: { bg: "bg-running/15 border-running/30", text: "text-running", dot: "bg-running animate-pulse", label: "Active" },
  running: { bg: "bg-running/15 border-running/30", text: "text-running", dot: "bg-running animate-pulse", label: "Running" },
  deploying: { bg: "bg-accent/15 border-accent/30", text: "text-accent", dot: "bg-accent animate-pulse", label: "Deploying" },
  completed: { bg: "bg-success/15 border-success/30", text: "text-success", dot: "bg-success", label: "Completed" },
  paused: { bg: "bg-warning/15 border-warning/30", text: "text-warning", dot: "bg-warning", label: "Paused" },
  cancelled: { bg: "bg-muted/15 border-muted/30", text: "text-muted", dot: "bg-muted", label: "Cancelled" },
};

function getVariantStats(samples: Sample[]) {
  const byVariant: Record<string, { scores: number[]; costs: number[]; durations: number[]; count: number }> = {};
  for (const s of samples) {
    if (!byVariant[s.variant_id]) {
      byVariant[s.variant_id] = { scores: [], costs: [], durations: [], count: 0 };
    }
    const v = byVariant[s.variant_id];
    v.count++;
    if (s.quality_score !== null) v.scores.push(s.quality_score);
    v.costs.push(s.cost_usd);
    v.durations.push(s.duration_seconds);
  }

  return Object.entries(byVariant).map(([id, data]) => ({
    id,
    count: data.count,
    avgQuality: data.scores.length > 0 ? data.scores.reduce((a, b) => a + b, 0) / data.scores.length : 0,
    avgCost: data.costs.reduce((a, b) => a + b, 0) / data.costs.length,
    avgDuration: data.durations.reduce((a, b) => a + b, 0) / data.durations.length,
  }));
}

export default function AutoPilotPage() {
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [stats, setStats] = useState<AutoPilotStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      setError(null);
      const [expRes, statsRes] = await Promise.all([
        api.get<Experiment[]>("/autopilot/experiments"),
        api.get<AutoPilotStats>("/autopilot/stats"),
      ]);
      if (expRes.data) setExperiments(expRes.data);
      if (statsRes.data) setStats(statsRes.data);
    } catch {
      setError("Could not connect to the API server");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  const handleDeploy = useCallback(
    async (id: string, variantId: string) => {
      const res = await api.post(`/autopilot/experiments/${id}/deploy`, { variant_id: variantId });
      if (res.error) {
        toast.error(`Failed to deploy variant: ${res.error.message}`);
        return;
      }
      toast.success("Variant deployed successfully");
      void fetchData();
    },
    [fetchData]
  );

  const handleReset = useCallback(
    async (id: string) => {
      const res = await api.post(`/autopilot/experiments/${id}/reset`);
      if (res.error) {
        toast.error(`Failed to reset experiment: ${res.error.message}`);
        return;
      }
      toast.success("Experiment reset");
      void fetchData();
    },
    [fetchData]
  );

  const handleAdvanceRollout = useCallback(
    async (id: string) => {
      const res = await api.post(`/autopilot/experiments/${id}/advance-rollout`);
      if (res.error) {
        toast.error(`Failed to advance rollout: ${res.error.message}`);
        return;
      }
      toast.success("Rollout advanced to next stage");
      void fetchData();
    },
    [fetchData]
  );

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <h1 className="mb-4 sm:mb-6 text-xl sm:text-2xl font-semibold tracking-tight text-foreground">AutoPilot</h1>
        <div className="rounded-xl border border-error/30 bg-error/5 p-4">
          <p className="text-sm text-error">{error}</p>
          <button
            onClick={() => { setLoading(true); void fetchData(); }}
            className="mt-2 text-xs font-medium text-accent hover:text-accent/80 transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4 sm:space-y-6">
      <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">AutoPilot</h1>

      {/* Stats cards */}
      {stats && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 lg:grid-cols-4">
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">Active Experiments</p>
            <p className="mt-1 text-xl sm:text-2xl font-semibold text-foreground">{stats.active_experiments}</p>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">Total Samples</p>
            <p className="mt-1 text-xl sm:text-2xl font-semibold text-foreground">{stats.total_samples}</p>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">Avg Quality Improvement</p>
            <div className="mt-1 flex items-center gap-1.5">
              <TrendingUp className="h-5 w-5 text-success" />
              <p className="text-xl sm:text-2xl font-semibold text-success">+{(stats.avg_quality_improvement * 100).toFixed(0)}%</p>
            </div>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">Cost Savings</p>
            <div className="mt-1 flex items-center gap-1.5">
              <TrendingDown className="h-5 w-5 text-accent" />
              <p className="text-xl sm:text-2xl font-semibold text-accent">{formatCost(stats.total_cost_savings_usd)}</p>
            </div>
          </div>
        </div>
      )}

      {/* Experiments */}
      {experiments.length === 0 ? (
        <EmptyState
          icon={FlaskConical}
          title="No experiments yet"
          description="Add autopilot config to any workflow step to start optimizing automatically."
        />
      ) : (
        <div className="space-y-4">
          {experiments.map((exp) => {
            const style = STATUS_MAP[exp.status] || STATUS_MAP.active;
            const isExpanded = expandedId === exp.id;
            const variantStats = exp.samples ? getVariantStats(exp.samples) : [];
            const bestVariant = variantStats.length > 0
              ? variantStats.reduce((a, b) => (a.avgQuality > b.avgQuality ? a : b))
              : null;

            return (
              <div
                key={exp.id}
                className="rounded-xl border border-border bg-surface shadow-sm overflow-hidden"
              >
                {/* Header */}
                <div
                  className="flex items-center gap-4 px-5 py-4 cursor-pointer hover:bg-border/10 transition-colors"
                  onClick={() => setExpandedId(isExpanded ? null : exp.id)}
                >
                  <div className={cn("flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border", style.bg)}>
                    <FlaskConical className={cn("h-5 w-5", style.text)} />
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-medium text-foreground">{exp.workflow_name}</p>
                      <span className="text-xs text-muted">/</span>
                      <p className="text-sm text-accent">{exp.step_id}</p>
                    </div>
                    <div className="flex items-center gap-3 mt-1">
                      <span className="text-xs text-muted">
                        Optimize: <span className="font-medium text-foreground capitalize">{exp.optimize_for}</span>
                      </span>
                      <span className="text-xs text-muted">
                        {exp.samples?.length || 0} samples
                      </span>
                      {exp.created_at && (
                        <span className="text-xs text-muted">{formatRelativeTime(exp.created_at)}</span>
                      )}
                    </div>
                  </div>

                  {/* Winner badge */}
                  {exp.deployed_variant_id && (
                    <span className="inline-flex items-center gap-1 rounded-full bg-accent/15 border border-accent/30 px-2.5 py-0.5 text-xs font-medium text-accent">
                      <Trophy className="h-3 w-3" />
                      {exp.deployed_variant_id}
                    </span>
                  )}

                  {/* Status badge */}
                  <span
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium",
                      style.bg,
                      style.text
                    )}
                  >
                    <span className={cn("h-1.5 w-1.5 rounded-full", style.dot)} />
                    {style.label}
                  </span>
                </div>

                {/* Expanded detail */}
                {isExpanded && (
                  <div className="border-t border-border">
                    {/* Variant comparison table */}
                    {variantStats.length > 0 && (
                      <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                          <thead>
                            <tr className="border-b border-border bg-background/50">
                              <th className="px-5 py-2.5 text-left font-medium text-muted">Variant</th>
                              <th className="px-5 py-2.5 text-right font-medium text-muted">Samples</th>
                              <th className="px-5 py-2.5 text-right font-medium text-muted">Avg Quality</th>
                              <th className="px-5 py-2.5 text-right font-medium text-muted">Avg Cost</th>
                              <th className="px-5 py-2.5 text-right font-medium text-muted">Avg Duration</th>
                              <th className="px-5 py-2.5 text-right font-medium text-muted">Actions</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-border">
                            {variantStats.map((v) => {
                              const isBest = bestVariant?.id === v.id;
                              const isDeployed = exp.deployed_variant_id === v.id;

                              return (
                                <tr key={v.id} className={cn(isBest && "bg-accent/5")}>
                                  <td className="px-5 py-3">
                                    <div className="flex items-center gap-2">
                                      <span className="font-medium text-foreground">{v.id}</span>
                                      {isBest && !isDeployed && (
                                        <span className="rounded bg-accent/15 px-1.5 py-0.5 text-[10px] font-semibold text-accent">
                                          BEST
                                        </span>
                                      )}
                                      {isDeployed && (
                                        <span className="rounded bg-success/15 px-1.5 py-0.5 text-[10px] font-semibold text-success">
                                          DEPLOYED
                                        </span>
                                      )}
                                    </div>
                                  </td>
                                  <td className="px-5 py-3 text-right text-muted">{v.count}</td>
                                  <td className="px-5 py-3 text-right">
                                    <span className={cn("font-medium", v.avgQuality >= 7 ? "text-success" : v.avgQuality >= 5 ? "text-warning" : "text-error")}>
                                      {v.avgQuality.toFixed(1)}/10
                                    </span>
                                  </td>
                                  <td className="px-5 py-3 text-right text-muted">{formatCost(v.avgCost)}</td>
                                  <td className="px-5 py-3 text-right text-muted">{v.avgDuration.toFixed(1)}s</td>
                                  <td className="px-5 py-3 text-right">
                                    {!isDeployed && exp.status !== "completed" && (
                                      <button
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          handleDeploy(exp.id, v.id);
                                        }}
                                        className={cn(
                                          "inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium",
                                          "text-accent hover:bg-accent/10 transition-colors"
                                        )}
                                      >
                                        <Rocket className="h-3 w-3" />
                                        Deploy
                                      </button>
                                    )}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    )}

                    {/* Progressive Rollout Status */}
                    {exp.status === "deploying" && exp.rollout_stage && (
                      <div className="px-5 py-4 border-t border-border bg-accent/5">
                        <div className="flex items-center justify-between mb-3">
                          <p className="text-sm font-medium text-foreground">Progressive Rollout</p>
                          <span className="text-xs font-mono text-accent capitalize">{exp.rollout_stage}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          {["canary", "partial", "full"].map((stage, idx) => {
                            const stages = ["canary", "partial", "full"];
                            const currentIdx = stages.indexOf(exp.rollout_stage || "canary");
                            const isActive = idx <= currentIdx;
                            const isCurrent = idx === currentIdx;
                            return (
                              <div key={stage} className="flex-1">
                                <div className="flex items-center gap-2">
                                  <div className={cn(
                                    "h-2 flex-1 rounded-full transition-all",
                                    isActive ? "bg-accent" : "bg-border",
                                    isCurrent && "animate-pulse"
                                  )} />
                                  {idx < stages.length - 1 && (
                                    <div className={cn("h-px w-4", isActive ? "bg-accent" : "bg-border")} />
                                  )}
                                </div>
                                <div className="flex justify-between mt-1">
                                  <span className={cn(
                                    "text-[10px] font-medium capitalize",
                                    isActive ? "text-accent" : "text-muted"
                                  )}>
                                    {stage}
                                  </span>
                                  <span className={cn(
                                    "text-[10px] font-mono",
                                    isActive ? "text-accent" : "text-muted"
                                  )}>
                                    {stage === "canary" ? "10%" : stage === "partial" ? "50%" : "100%"}
                                  </span>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleAdvanceRollout(exp.id);
                          }}
                          className="mt-3 inline-flex items-center gap-1.5 rounded-md bg-accent/10 border border-accent/30 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/20 transition-colors"
                        >
                          <Rocket className="h-3 w-3" />
                          Advance to Next Stage
                        </button>
                      </div>
                    )}

                    {/* Actions */}
                    <div className="flex items-center justify-end gap-2 px-5 py-3 bg-background/30">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleReset(exp.id);
                        }}
                        className={cn(
                          "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium",
                          "border border-border text-muted hover:text-foreground hover:bg-border/40 transition-colors"
                        )}
                      >
                        <RotateCcw className="h-3 w-3" />
                        Reset Experiment
                      </button>
                    </div>
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
