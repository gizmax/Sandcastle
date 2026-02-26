import { cn } from "@/lib/utils";
import { ScoreRing } from "@/components/advisor/ScoreRing";
import type { Insight } from "@/lib/insights";

interface BeaconProps {
  score: number;
  insights: Insight[];
  loading: boolean;
  onClick: () => void;
}

export function Beacon({ score, insights, loading, onClick }: BeaconProps) {
  const hasCritical = insights.some((i) => i.severity === "critical");
  const hasWarning = insights.some((i) => i.severity === "warning");
  const count = insights.length;

  return (
    <button
      onClick={onClick}
      className={cn(
        "fixed bottom-20 right-4 z-40",
        "flex items-center justify-center",
        "h-14 w-14 rounded-full",
        "border border-border bg-surface shadow-lg",
        "transition-all duration-300 hover:scale-105 hover:shadow-xl",
        "focus:outline-none focus:ring-2 focus:ring-accent/40",
        hasCritical && "lighthouse-pulse-critical",
        !hasCritical && hasWarning && "lighthouse-pulse-warning",
      )}
      title="Lighthouse - System Health"
    >
      {loading ? (
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-border border-t-accent" />
      ) : (
        <ScoreRing score={score} size="sm" />
      )}

      {/* Insight count badge */}
      {count > 0 && !loading && (
        <span className={cn(
          "absolute -right-1 -top-1 flex h-5 min-w-5 items-center justify-center",
          "rounded-full px-1 text-[10px] font-bold text-white",
          hasCritical ? "bg-error" : hasWarning ? "bg-amber-500" : "bg-accent",
        )}>
          {count > 9 ? "9+" : count}
        </span>
      )}
    </button>
  );
}
