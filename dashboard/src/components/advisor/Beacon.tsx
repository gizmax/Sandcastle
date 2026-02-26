import { cn } from "@/lib/utils";
import { ScoreRing } from "@/components/advisor/ScoreRing";
import type { Insight } from "@/lib/insights";

interface BeaconProps {
  score: number;
  insights: Insight[];
  loading: boolean;
  onClick: () => void;
}

function hasSeverity(insights: Insight[], severity: string): boolean {
  return insights.some((i) => i.severity === severity);
}

export function Beacon({ score, insights, loading, onClick }: BeaconProps) {
  const hasCritical = hasSeverity(insights, "critical");
  const hasWarning = hasSeverity(insights, "warning");
  const count = insights.length;

  return (
    <button
      onClick={onClick}
      className={cn(
        "fixed bottom-20 right-4 z-40",
        "flex items-center justify-center",
        "rounded-full bg-surface border border-border shadow-lg",
        "transition-shadow duration-300 hover:shadow-xl",
        "focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-background",
        hasCritical && "lighthouse-pulse-critical",
        !hasCritical && hasWarning && "lighthouse-pulse-warning",
      )}
      style={{ width: 56, height: 56 }}
      title="Lighthouse - System Health"
      aria-label={`System health score: ${score}. ${count} insight${count !== 1 ? "s" : ""}.`}
    >
      {loading ? (
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-border border-t-accent" />
      ) : (
        <ScoreRing score={score} size="sm" />
      )}

      {count > 0 && !loading && (
        <span
          className={cn(
            "absolute -top-1 -right-1 flex h-5 min-w-5 items-center justify-center",
            "rounded-full px-1 text-[10px] font-bold leading-none",
            hasCritical
              ? "bg-error text-white"
              : hasWarning
                ? "bg-amber-500 text-white"
                : "bg-accent text-accent-foreground",
          )}
        >
          {count > 9 ? "9+" : count}
        </span>
      )}
    </button>
  );
}
