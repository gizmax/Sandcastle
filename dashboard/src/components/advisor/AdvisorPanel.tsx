import { useEffect } from "react";
import { Lightbulb, X, RefreshCw, TrendingUp, TrendingDown, Minus } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatRelativeTime } from "@/lib/utils";
import { ScoreRing } from "@/components/advisor/ScoreRing";
import { InsightCard } from "@/components/advisor/InsightCard";
import type { Deduction, Insight, Severity } from "@/lib/insights";

interface AdvisorPanelProps {
  open: boolean;
  onClose: () => void;
  score: number;
  previousScore: number | null;
  deductions: Deduction[];
  insights: Insight[];
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
  adoption: "bg-blue-500",
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

export function AdvisorPanel({
  open,
  onClose,
  score,
  previousScore,
  deductions,
  insights,
  lastChecked,
  onRefresh,
  onDismiss,
}: AdvisorPanelProps) {
  // Escape key
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  // Group deductions by category
  const deductionsByCategory = deductions.reduce<Record<string, number>>((acc, d) => {
    acc[d.category] = (acc[d.category] ?? 0) + d.points;
    return acc;
  }, {});
  const totalDeduction = deductions.reduce((sum, d) => sum + d.points, 0);

  // Group insights by severity
  const insightsBySeverity = SEVERITY_ORDER
    .map((s) => ({ severity: s, items: insights.filter((i) => i.severity === s) }))
    .filter((g) => g.items.length > 0);

  // Trend arrow
  const diff = previousScore != null ? score - previousScore : 0;

  return (
    <>
      {/* Overlay */}
      <div className="fixed inset-0 z-50 bg-black/40 transition-opacity" onClick={onClose} />

      {/* Panel */}
      <div className={cn(
        "fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col",
        "border-l border-border bg-surface shadow-2xl",
        "animate-in slide-in-from-right duration-300",
      )}>
        {/* Header */}
        <div className="flex items-center gap-3 border-b border-border px-6 py-5">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-accent/10">
            <Lightbulb className="h-5 w-5 text-accent" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-lg font-semibold text-foreground">Lighthouse</h2>
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
          <div className="flex flex-col items-center gap-2">
            <ScoreRing score={score} size="lg" />
            <div className="flex items-center gap-2">
              <span className={cn("text-sm font-semibold", scoreLabelColor(score))}>
                {scoreLabel(score)}
              </span>
              {previousScore != null && diff !== 0 && (
                <span className={cn(
                  "flex items-center gap-0.5 text-xs font-medium",
                  diff > 0 ? "text-emerald-500" : "text-error",
                )}>
                  {diff > 0 ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
                  {diff > 0 ? "+" : ""}{diff}
                </span>
              )}
              {previousScore != null && diff === 0 && (
                <span className="flex items-center gap-0.5 text-xs font-medium text-muted">
                  <Minus className="h-3 w-3" /> No change
                </span>
              )}
            </div>
          </div>

          {/* Score breakdown bar */}
          {totalDeduction > 0 && (
            <div className="space-y-2">
              <h3 className="text-sm font-medium text-foreground">Score breakdown</h3>
              <div className="flex h-3 w-full overflow-hidden rounded-full bg-border/50">
                {(Object.keys(CATEGORY_COLORS) as Deduction["category"][]).map((cat) => {
                  const pts = deductionsByCategory[cat] ?? 0;
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
                {/* Remaining (healthy portion) */}
                <div className="h-full flex-1 bg-emerald-500/40" />
              </div>
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                {(Object.keys(CATEGORY_COLORS) as Deduction["category"][]).map((cat) => {
                  const pts = deductionsByCategory[cat] ?? 0;
                  if (pts === 0) return null;
                  return (
                    <span key={cat} className="flex items-center gap-1.5 text-xs text-muted">
                      <span className={cn("h-2 w-2 rounded-full", CATEGORY_COLORS[cat])} />
                      {CATEGORY_LABELS[cat]} <span className="font-data">-{pts}</span>
                    </span>
                  );
                })}
                <span className="flex items-center gap-1.5 text-xs text-muted">
                  <span className="h-2 w-2 rounded-full bg-emerald-500/40" />
                  Healthy <span className="font-data">{score}</span>
                </span>
              </div>
            </div>
          )}

          {/* Insights list */}
          {insightsBySeverity.length > 0 ? (
            <div className="space-y-4">
              {insightsBySeverity.map((group) => (
                <div key={group.severity} className="space-y-2">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">
                    {SEVERITY_LABELS[group.severity]}
                  </h3>
                  {group.items.map((insight) => (
                    <InsightCard key={insight.id} insight={insight} onDismiss={onDismiss} />
                  ))}
                </div>
              ))}
            </div>
          ) : (
            <div className="flex flex-col items-center gap-2 py-8 text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-emerald-500/10">
                <Lightbulb className="h-6 w-6 text-emerald-500" />
              </div>
              <p className="text-sm font-medium text-foreground">All clear!</p>
              <p className="text-xs text-muted">No actionable insights right now.</p>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-border px-6 py-3">
          <span className="text-xs text-muted">
            {lastChecked ? `Last checked ${formatRelativeTime(lastChecked)}` : "Checking..."}
          </span>
          <button
            onClick={onRefresh}
            className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/10 transition-colors"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh
          </button>
        </div>
      </div>
    </>
  );
}
