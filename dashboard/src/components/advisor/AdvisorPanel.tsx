import { useEffect } from "react";
import { Lightbulb, X, RefreshCw, TrendingUp, TrendingDown, Minus } from "lucide-react";
import { cn } from "@/lib/utils";
import { ScoreRing } from "@/components/advisor/ScoreRing";
import { InsightCard } from "@/components/advisor/InsightCard";
import { formatRelativeTime } from "@/lib/utils";
import type { Insight, Deduction, Severity } from "@/lib/insights";

interface AdvisorPanelProps {
  open: boolean;
  onClose: () => void;
  score: number;
  previousScore: number | null;
  deductions: Deduction[];
  insights: Insight[];
  loading: boolean;
  lastChecked: Date | null;
  onRefresh: () => void;
  onDismiss: (id: string) => void;
}

function scoreLabel(score: number): string {
  if (score >= 80) return "Healthy";
  if (score >= 50) return "Needs Attention";
  return "Critical";
}

function scoreLabelColor(score: number): string {
  if (score >= 80) return "text-emerald-500";
  if (score >= 50) return "text-amber-500";
  return "text-error";
}

const CATEGORY_COLORS: Record<Deduction["category"], string> = {
  health: "bg-error",
  operations: "bg-amber-500",
  quality: "bg-violet-500",
  adoption: "bg-muted-foreground",
};

const CATEGORY_LABELS: Record<Deduction["category"], string> = {
  health: "Health",
  operations: "Operations",
  quality: "Quality",
  adoption: "Adoption",
};

const SEVERITY_ORDER: Severity[] = ["critical", "warning", "optimize", "discover"];

const SEVERITY_LABELS: Record<Severity, string> = {
  critical: "Critical",
  warning: "Warning",
  optimize: "Optimize",
  discover: "Discover",
};

function groupBySeverity(insights: Insight[]): [Severity, Insight[]][] {
  const groups: [Severity, Insight[]][] = [];
  for (const sev of SEVERITY_ORDER) {
    const items = insights.filter((i) => i.severity === sev);
    if (items.length > 0) groups.push([sev, items]);
  }
  return groups;
}

export function AdvisorPanel({
  open,
  onClose,
  score,
  previousScore,
  deductions,
  insights,
  loading,
  lastChecked,
  onRefresh,
  onDismiss,
}: AdvisorPanelProps) {
  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  const totalDeducted = deductions.reduce((sum, d) => sum + d.points, 0);
  const categoryTotals = deductions.reduce(
    (acc, d) => {
      acc[d.category] = (acc[d.category] ?? 0) + d.points;
      return acc;
    },
    {} as Record<string, number>,
  );

  const trend = previousScore != null ? score - previousScore : 0;

  const grouped = groupBySeverity(insights);

  return (
    <>
      {/* Overlay */}
      <div
        className="fixed inset-0 z-50 bg-black/40 transition-opacity"
        onClick={onClose}
      />

      {/* Panel */}
      <div
        className={cn(
          "fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col",
          "border-l border-border bg-surface shadow-2xl",
          "animate-in slide-in-from-right duration-300",
        )}
      >
        {/* Header */}
        <div className="flex items-center gap-3 border-b border-border px-6 py-5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent/10">
            <Lightbulb className="h-5 w-5 text-accent" />
          </div>
          <div className="flex-1">
            <h2 className="text-base font-semibold text-foreground">Lighthouse</h2>
            <p className="text-xs text-muted">System health advisor</p>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-muted hover:bg-border/40 hover:text-foreground transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6">
          {/* Score hero */}
          <div className="flex flex-col items-center gap-3">
            <ScoreRing score={score} size="lg" />
            <div className="flex items-center gap-2">
              {trend !== 0 && (
                <span className={cn("flex items-center gap-0.5 text-xs font-medium", trend > 0 ? "text-emerald-500" : "text-error")}>
                  {trend > 0 ? <TrendingUp className="h-3.5 w-3.5" /> : <TrendingDown className="h-3.5 w-3.5" />}
                  {trend > 0 ? "+" : ""}{trend}
                </span>
              )}
              {trend === 0 && previousScore != null && (
                <span className="flex items-center gap-0.5 text-xs font-medium text-muted">
                  <Minus className="h-3.5 w-3.5" />
                  No change
                </span>
              )}
              <span className={cn("text-sm font-semibold", scoreLabelColor(score))}>
                {scoreLabel(score)}
              </span>
            </div>
          </div>

          {/* Score breakdown bar */}
          {totalDeducted > 0 && (
            <div className="space-y-2">
              <p className="text-xs font-medium text-muted">Score breakdown</p>
              <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-border">
                {(Object.keys(CATEGORY_COLORS) as Deduction["category"][]).map((cat) => {
                  const pts = categoryTotals[cat] ?? 0;
                  if (pts === 0) return null;
                  const pct = (pts / 100) * 100;
                  return (
                    <div
                      key={cat}
                      className={cn("h-full transition-all duration-500", CATEGORY_COLORS[cat])}
                      style={{ width: `${pct}%` }}
                      title={`${CATEGORY_LABELS[cat]}: -${pts}`}
                    />
                  );
                })}
                <div
                  className="h-full bg-emerald-500 transition-all duration-500"
                  style={{ width: `${score}%` }}
                  title={`Remaining: ${score}`}
                />
              </div>
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                {(Object.keys(CATEGORY_COLORS) as Deduction["category"][]).map((cat) => {
                  const pts = categoryTotals[cat] ?? 0;
                  if (pts === 0) return null;
                  return (
                    <div key={cat} className="flex items-center gap-1.5 text-xs text-muted">
                      <span className={cn("h-2 w-2 rounded-full", CATEGORY_COLORS[cat])} />
                      {CATEGORY_LABELS[cat]} <span className="text-foreground font-medium">-{pts}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Insights */}
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <div className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-accent" />
            </div>
          ) : insights.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-12 text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-emerald-500/10">
                <Lightbulb className="h-6 w-6 text-emerald-500" />
              </div>
              <p className="text-sm font-medium text-foreground">All clear</p>
              <p className="text-xs text-muted">No issues found. Your system is running smoothly.</p>
            </div>
          ) : (
            <div className="space-y-5">
              {grouped.map(([severity, items]) => (
                <div key={severity} className="space-y-2">
                  <p className="text-xs font-medium text-muted uppercase tracking-wider">
                    {SEVERITY_LABELS[severity]} ({items.length})
                  </p>
                  <div className="space-y-2">
                    {items.map((insight) => (
                      <InsightCard key={insight.id} insight={insight} onDismiss={onDismiss} />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-border px-6 py-3">
          <p className="text-xs text-muted">
            {lastChecked ? `Last checked ${formatRelativeTime(lastChecked)}` : "Checking..."}
          </p>
          <button
            onClick={onRefresh}
            disabled={loading}
            className={cn(
              "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium",
              "text-muted hover:text-foreground hover:bg-border/40 transition-colors",
              "disabled:opacity-50",
            )}
          >
            <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
            Refresh
          </button>
        </div>
      </div>
    </>
  );
}
