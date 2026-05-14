import { Sparkles, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { AdvisorRecommendation, ProviderRecommendation } from "./bentoTypes";

interface Props {
  recommendation: ProviderRecommendation | null;
  advisorRecs: AdvisorRecommendation[];
  totalSavings: number;
  onDismiss: () => void;
}

export function BentoRecommendationBanner({
  recommendation,
  advisorRecs,
  totalSavings,
  onDismiss,
}: Props) {
  // Prefer per-workflow advisor recommendations when available
  if (advisorRecs.length > 0) {
    return (
      <div className={cn(
        "rounded-2xl border border-accent/30 bg-accent/5",
        "p-4 space-y-3",
      )}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-accent shrink-0" />
            <p className="text-sm font-semibold text-foreground">
              Switch {advisorRecs.length} workflow{advisorRecs.length > 1 ? "s" : ""} and save ${totalSavings.toFixed(0)}/month
            </p>
          </div>
          <button
            onClick={onDismiss}
            className="text-muted-foreground hover:text-foreground transition-colors shrink-0"
            aria-label="Dismiss recommendation"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          {advisorRecs.map((rec) => (
            <div
              key={rec.workflow}
              className="rounded-lg bg-background/60 border border-border/50 p-2.5 flex flex-col gap-1"
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-foreground truncate">{rec.workflow}</span>
                <span className="text-xs font-semibold text-success shrink-0">
                  -${rec.savings_monthly.toFixed(0)}/mo
                </span>
              </div>
              <p className="text-[11px] text-muted-foreground">
                {rec.current_provider} → {rec.suggested_provider}
                {rec.eu_compliant && (
                  <span className="ml-1 text-accent" title="EU data residency">EU</span>
                )}
              </p>
            </div>
          ))}
        </div>
      </div>
    );
  }

  // Fallback to legacy single recommendation
  if (!recommendation) return null;
  return (
    <div className={cn(
      "rounded-2xl border border-accent/30 bg-accent/5",
      "p-4 flex items-center gap-3",
    )}>
      <Sparkles className="h-5 w-5 text-accent shrink-0" />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-foreground truncate">{recommendation.title}</p>
        <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{recommendation.description}</p>
      </div>
      {recommendation.estimated_savings_usd > 0 && (
        <span className="text-xs font-semibold text-success shrink-0">
          Save ${recommendation.estimated_savings_usd.toFixed(0)}/mo
        </span>
      )}
      <button
        onClick={onDismiss}
        className="text-muted-foreground hover:text-foreground transition-colors shrink-0"
        aria-label="Dismiss recommendation"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}
