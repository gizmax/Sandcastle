import { useCallback, useEffect, useState } from "react";
import { ShieldAlert, CheckCircle2, ChevronDown, ChevronUp } from "lucide-react";
import { api } from "@/api/client";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { formatRelativeTime, cn } from "@/lib/utils";
import { useNavigate } from "react-router-dom";

interface Violation {
  id: string;
  run_id: string;
  step_id: string;
  policy_id: string;
  severity: string;
  action_taken: string;
  trigger_details: string;
  output_modified: boolean;
  created_at: string;
}

interface ViolationStats {
  total_violations_30d: number;
  violations_by_severity: Record<string, number>;
  violations_by_policy: Record<string, number>;
}

const SEVERITY_STYLES: Record<string, { bg: string; text: string; dot: string; label: string }> = {
  critical: { bg: "bg-error/15 border-error/30", text: "text-error", dot: "bg-error animate-pulse", label: "Critical" },
  high: { bg: "bg-warning/15 border-warning/30", text: "text-warning", dot: "bg-warning", label: "High" },
  medium: { bg: "bg-accent/15 border-accent/30", text: "text-accent", dot: "bg-accent", label: "Medium" },
  low: { bg: "bg-muted/15 border-muted/30", text: "text-muted", dot: "bg-muted", label: "Low" },
};

const ACTION_STYLES: Record<string, string> = {
  blocked: "bg-error/10 text-error",
  redacted: "bg-warning/10 text-warning",
  flagged: "bg-accent/10 text-accent",
  logged: "bg-muted/10 text-muted",
};

export default function ViolationsPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<Violation[]>([]);
  const [stats, setStats] = useState<ViolationStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string>("all");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    const params: Record<string, string> = {};
    if (filter !== "all") params.severity = filter;
    const [itemsRes, statsRes] = await Promise.all([
      api.get<Violation[]>("/violations", params),
      api.get<ViolationStats>("/violations/stats"),
    ]);
    if (itemsRes.data) setItems(itemsRes.data);
    if (statsRes.data) setStats(statsRes.data);
    setLoading(false);
  }, [filter]);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  const filters = [
    { key: "all", label: "All" },
    { key: "critical", label: "Critical" },
    { key: "high", label: "High" },
    { key: "medium", label: "Medium" },
    { key: "low", label: "Low" },
  ];

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  return (
    <div className="space-y-4 sm:space-y-6">
      <div className="flex items-center gap-3">
        <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">Policy Violations</h1>
      </div>

      {/* Stats cards */}
      {stats && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 lg:grid-cols-4">
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">Total Violations (30d)</p>
            <p className="mt-1 text-xl sm:text-2xl font-semibold text-foreground">{stats.total_violations_30d}</p>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">Critical</p>
            <p className="mt-1 text-xl sm:text-2xl font-semibold text-error">{stats.violations_by_severity.critical || 0}</p>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">High</p>
            <p className="mt-1 text-xl sm:text-2xl font-semibold text-warning">{stats.violations_by_severity.high || 0}</p>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">Top Policy</p>
            <p className="mt-1 text-lg font-semibold text-foreground">
              {Object.entries(stats.violations_by_policy).sort(([, a], [, b]) => b - a)[0]?.[0] || "-"}
            </p>
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="flex gap-1 rounded-lg bg-background p-1">
        {filters.map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            className={cn(
              "rounded-md px-3 py-1.5 text-sm font-medium transition-all duration-200",
              filter === f.key
                ? "bg-surface text-foreground shadow-sm"
                : "text-muted hover:text-foreground"
            )}
          >
            {f.label}
          </button>
        ))}
      </div>

      {items.length === 0 ? (
        <EmptyState
          icon={ShieldAlert}
          title="No violations"
          description="Policy violations from workflow runs will appear here."
        />
      ) : (
        <div className="space-y-3">
          {items.map((item) => {
            const style = SEVERITY_STYLES[item.severity] || SEVERITY_STYLES.low;
            const actionStyle = ACTION_STYLES[item.action_taken] || ACTION_STYLES.logged;
            const isExpanded = expandedId === item.id;

            return (
              <div
                key={item.id}
                className="rounded-xl border border-border bg-surface shadow-sm overflow-hidden"
              >
                <div
                  className="flex items-center gap-4 px-5 py-4 cursor-pointer hover:bg-border/10 transition-colors"
                  onClick={() => setExpandedId(isExpanded ? null : item.id)}
                >
                  {/* Severity indicator */}
                  <div className={cn("flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border", style.bg)}>
                    <ShieldAlert className={cn("h-5 w-5", style.text)} />
                  </div>

                  {/* Info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-semibold text-foreground">{item.policy_id}</p>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          navigate(`/runs/${item.run_id}`);
                        }}
                        className="text-xs font-medium text-accent hover:text-accent-hover transition-colors"
                      >
                        {item.step_id}
                      </button>
                    </div>
                    <div className="flex items-center gap-3 mt-1">
                      <p className="text-xs text-muted truncate max-w-md">
                        {item.trigger_details}
                      </p>
                    </div>
                  </div>

                  {/* Output modified */}
                  {item.output_modified && (
                    <span className="inline-flex items-center gap-1 text-xs font-medium text-success">
                      <CheckCircle2 className="h-3.5 w-3.5" />
                      Modified
                    </span>
                  )}

                  {/* Action badge */}
                  <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium", actionStyle)}>
                    {item.action_taken}
                  </span>

                  {/* Severity badge */}
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
                  <div className="border-t border-border px-5 py-4 bg-background/50">
                    <p className="text-xs font-medium text-muted-foreground mb-2">Trigger Details</p>
                    <pre className="max-h-48 overflow-auto rounded-lg bg-surface border border-border p-3 font-mono text-xs text-muted whitespace-pre-wrap">
                      {item.trigger_details}
                    </pre>
                    <div className="mt-3 flex items-center gap-4 text-xs text-muted">
                      <span>Run: <span className="font-mono text-foreground">{item.run_id.slice(0, 8)}</span></span>
                      <span>Step: <span className="font-medium text-foreground">{item.step_id}</span></span>
                      <span>Action: <span className="font-medium text-foreground">{item.action_taken}</span></span>
                      {item.output_modified && (
                        <span className="text-success font-medium">Output was modified</span>
                      )}
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
