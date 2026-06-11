import { Link } from "react-router-dom";
import { ArrowRight, DollarSign } from "lucide-react";
import { Odometer } from "@/components/ui/Odometer";
import { cn, formatCost } from "@/lib/utils";
import { StatusLed } from "@/components/ui/StatusLed";
import type { Insight, Severity } from "@/lib/insights";

const SEVERITY_DOT: Record<Severity, string> = {
  critical: "bg-error",
  warning: "bg-warning",
  optimize: "bg-accent",
  discover: "bg-running",
};

interface Props {
  score: number;
  activeInsights: Insight[];
  loading: boolean;
  totalRuns: number;
  successRate: number;
  totalCost: number;
  avgDuration: number;
}

export function BentoHealthHero({
  score, activeInsights, loading, totalRuns, successRate, totalCost, avgDuration,
}: Props) {
  const actionable = activeInsights.filter((i) => i.severity !== "discover");
  const scoreLabel = score >= 80 ? "Healthy" : score >= 50 ? "Needs Attention" : "Critical";

  return (
    <div className={cn(
      "bg-surface rounded-md border border-border",
      "hover:border-accent/30 transition-settle",
      "p-5 flex flex-col gap-3.5 h-full relative",
    )}>
      <div className="absolute top-5 right-5">
        {loading ? (
          <div className="h-14 w-14 rounded-full bg-border animate-pulse" />
        ) : (
          <Link
            to="/system-health"
            title="View system health details"
            className={cn(
              "flex items-center justify-center w-14 h-14 rounded-full",
              "text-xl font-bold transition-opacity hover:opacity-80",
              score >= 80 ? "bg-success/20 text-success" : score >= 50 ? "bg-warning/20 text-warning" : "bg-error/20 text-error",
            )}
          >
            {score}
          </Link>
        )}
      </div>

      <div>
        <p className="panel-label text-muted-foreground mb-1.5">
          Command Center · Today
        </p>
        <p className="font-display text-5xl font-bold text-foreground tracking-tight leading-none">
          <Odometer value={totalRuns} />
        </p>
        <p className="mt-1 text-sm text-muted-foreground">workflows ran today</p>
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <span className="inline-flex items-center gap-1.5 rounded-full bg-success/10 border border-success/20 px-3 py-1.5 text-xs font-semibold text-success">
          <span className="h-1.5 w-1.5 rounded-full bg-success" />
          {successRate}% success
        </span>
        <span className="inline-flex items-center gap-1.5 rounded-full bg-accent/10 border border-accent/20 px-3 py-1.5 text-xs font-semibold text-accent">
          <DollarSign className="h-3 w-3" />
          <Odometer value={totalCost} format={formatCost} /> cost
        </span>
        <span className="inline-flex items-center gap-1.5 rounded-full bg-running/10 border border-running/20 px-3 py-1.5 text-xs font-semibold text-running">
          <span className="h-1.5 w-1.5 rounded-full bg-running" />
          {avgDuration > 0 ? `${Math.round(avgDuration)}s` : "n/a"} avg
        </span>
      </div>

      {!loading && actionable.length === 0 && (
        <div className="flex items-center gap-2">
          <StatusLed
            status={score >= 80 ? "healthy" : "degraded"}
            label={score >= 80 ? "All systems healthy" : scoreLabel}
            size="md"
          />
        </div>
      )}

      {!loading && actionable.length > 0 && (
        <div className="flex flex-col gap-1 flex-1">
          {actionable.slice(0, 4).map((insight) => (
            <Link
              key={insight.id}
              to={insight.link}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-3 py-1.5",
                "bg-background border border-border",
                "hover:border-accent/30 hover:bg-surface",
                "transition-colors duration-150 text-sm",
              )}
            >
              <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", SEVERITY_DOT[insight.severity])} />
              <span className="flex-1 text-foreground truncate">{insight.title}</span>
              <ArrowRight className="h-3 w-3 text-muted-foreground shrink-0" />
            </Link>
          ))}
          {actionable.length > 4 && (
            <p className="px-3 text-xs text-muted-foreground">+{actionable.length - 4} more</p>
          )}
        </div>
      )}
    </div>
  );
}
