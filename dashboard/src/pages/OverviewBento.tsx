import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { SectionErrorBoundary } from "@/components/shared/ErrorBoundary";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { BentoActivityFeed } from "@/components/overview/BentoActivityFeed";
import { BentoAnomalies } from "@/components/overview/BentoAnomalies";
import { BentoCharts } from "@/components/overview/BentoCharts";
import { BentoEmptyState } from "@/components/overview/BentoEmptyState";
import { BentoFailoverEvents } from "@/components/overview/BentoFailoverEvents";
import { BentoForecast } from "@/components/overview/BentoForecast";
import { BentoHealthHero } from "@/components/overview/BentoHealthHero";
import { BentoHeatmap } from "@/components/overview/BentoHeatmap";
import { BentoProviderCosts, BentoSavingsOpportunities } from "@/components/overview/BentoProviderCosts";
import { BentoProviderStatusBanner } from "@/components/overview/BentoProviderStatusBanner";
import { BentoQuickActions } from "@/components/overview/BentoQuickActions";
import { BentoRecentRuns } from "@/components/overview/BentoRecentRuns";
import { BentoRecommendationBanner } from "@/components/overview/BentoRecommendationBanner";
import { BentoLoadingSkeleton, BentoErrorState } from "@/components/overview/BentoSkeleton";
import { BentoStatsRow } from "@/components/overview/BentoStats";
import { Omnibox } from "@/components/overview/Omnibox";
import { dismissRec } from "@/components/overview/dismissedRecommendations";
import { useOverviewData } from "@/components/overview/useOverviewData";
import { useAdvisorContext } from "@/hooks/useAdvisorContext";
import { useDensity } from "@/contexts/UiModeContext";
import { cn } from "@/lib/utils";

// Re-export LayoutSwitcher so legacy importers (e.g. OverviewFocus) keep working.
export { LayoutSwitcher } from "@/components/overview/LayoutSwitcher";

/** localStorage key persisting the calm/expanded choice for the dashboard. */
const EXPANDED_KEY = "sandcastle-overview-expanded";

/**
 * Resolve the initial expanded state. Honors an explicit stored choice; falls
 * back to density (Everything => expanded, otherwise calm).
 */
function readExpanded(everything: boolean): boolean {
  try {
    const stored = localStorage.getItem(EXPANDED_KEY);
    if (stored === "true") return true;
    if (stored === "false") return false;
  } catch {
    /* ignore */
  }
  return everything;
}

export default function OverviewBento() {
  const advisor = useAdvisorContext();
  const d = useOverviewData();
  const { effectiveDensity } = useDensity();
  const isEverything = effectiveDensity === "Everything";

  // Whether the dense "more insights" region is shown. Default keyed off
  // density, overridable + persisted via the Show details toggle.
  const [expanded, setExpanded] = useState<boolean>(() => readExpanded(isEverything));

  // If density changes and the user hasn't made an explicit choice, follow it.
  useEffect(() => {
    let hasChoice = false;
    try {
      hasChoice = localStorage.getItem(EXPANDED_KEY) != null;
    } catch {
      /* ignore */
    }
    if (!hasChoice) setExpanded(isEverything);
  }, [isEverything]);

  const toggleExpanded = useCallback(() => {
    setExpanded((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(EXPANDED_KEY, String(next));
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  const dismissRecommendation = useCallback(() => {
    if (d.advisorRecs.length > 0) {
      dismissRec(`advisor-${d.advisorTotalSavings.toFixed(0)}`);
      d.setAdvisorRecs([]);
      d.setAdvisorTotalSavings(0);
    }
    if (d.topRecommendation) dismissRec(d.topRecommendation.title);
    d.setRecDismissed(true);
  }, [d]);

  if (d.loading) return <BentoLoadingSkeleton />;
  if (d.error) return <BentoErrorState message={d.error} onRetry={d.retry} />;

  const successRate = d.stats?.success_rate != null ? Math.round(d.stats.success_rate * 100) : 0;
  const totalRuns = d.stats?.total_runs_today ?? 0;
  const totalCost = d.stats?.total_cost_today ?? 0;
  const avgDuration = d.stats?.avg_duration_seconds ?? 0;

  // Empty state: no workflows and no runs recorded
  const isEmpty = d.workflowCount === 0 && totalRuns === 0 && d.recentRuns.length === 0;

  if (isEmpty) {
    return (
      <div className="space-y-4 sm:space-y-5 settle-stagger">
        <PageHeader eyebrow="Sandcastle · Command Deck" title="Overview" />

        {/* The omnibox is the hero of the empty state — describe your first agent. */}
        <SectionErrorBoundary section="omnibox">
          <Omnibox variant="empty" />
        </SectionErrorBoundary>

        <SectionErrorBoundary section="providers">
          <BentoProviderStatusBanner />
        </SectionErrorBoundary>

        <SectionErrorBoundary section="getting started">
          <BentoEmptyState />
        </SectionErrorBoundary>
      </div>
    );
  }

  return (
    <div className="space-y-4 sm:space-y-5 settle-stagger">
      <PageHeader eyebrow="Sandcastle · Command Deck" title="Overview" />

      {/* PRIMARY ACTION — the omnibox sits above the fold as the hero. */}
      <SectionErrorBoundary section="omnibox">
        <Omnibox />
      </SectionErrorBoundary>

      {!d.recDismissed && (d.advisorRecs.length > 0 || (d.topRecommendation && d.showProviderCosts)) && (
        <SectionErrorBoundary section="recommendations">
          <BentoRecommendationBanner
            recommendation={d.topRecommendation}
            advisorRecs={d.advisorRecs}
            totalSavings={d.advisorTotalSavings}
            onDismiss={dismissRecommendation}
          />
        </SectionErrorBoundary>
      )}

      {/* CALM GLANCE — Is it healthy? + Quick actions. Always above the fold. */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 sm:gap-5">
        <div className="lg:col-span-2">
          <SectionErrorBoundary section="health">
            <BentoHealthHero
              score={advisor.score}
              activeInsights={advisor.activeInsights}
              loading={advisor.loading}
              totalRuns={totalRuns}
              successRate={successRate}
              totalCost={totalCost}
              avgDuration={avgDuration}
            />
          </SectionErrorBoundary>
        </div>
        <div>
          <SectionErrorBoundary section="quick actions">
            <BentoQuickActions />
          </SectionErrorBoundary>
        </div>
      </div>

      {/* CALM GLANCE — 4 stat cards (compact at-a-glance numbers). */}
      {d.stats && (
        <SectionErrorBoundary section="stats">
          <BentoStatsRow
            totalRuns={totalRuns}
            successRate={successRate}
            totalCost={totalCost}
            avgDuration={avgDuration}
            sparklines={d.sparklines}
          />
        </SectionErrorBoundary>
      )}

      {/* CALM GLANCE — What ran recently? */}
      <div ref={d.activityRef}>
        <SectionErrorBoundary section="recent activity">
          <BentoRecentRuns runs={d.recentRuns} />
        </SectionErrorBoundary>
      </div>

      {/* Reveal control for the dense dashboard. */}
      <div className="flex justify-center pt-1">
        <button
          type="button"
          onClick={toggleExpanded}
          aria-expanded={expanded}
          aria-controls="overview-more-insights"
          className={cn(
            "inline-flex items-center gap-2 rounded-full border border-border bg-surface px-4 py-2 text-sm font-medium",
            "text-muted-foreground hover:text-foreground hover:border-accent/40 transition-colors active:scale-[0.98]",
          )}
        >
          {expanded ? (
            <>
              <ChevronUp className="h-4 w-4" />
              Hide details
            </>
          ) : (
            <>
              <ChevronDown className="h-4 w-4" />
              Show details
            </>
          )}
        </button>
      </div>

      {/* EXPANDED — the full dense bento. Preserves every existing widget. */}
      {expanded && (
        <div id="overview-more-insights" className="space-y-4 sm:space-y-5">
          {/* Activity heatmap */}
          <div ref={d.heatmapRef}>
            {!d.heatmapLoaded ? (
              <Skeleton className="h-36 rounded-2xl" />
            ) : (
              <SectionErrorBoundary section="heatmap">
                <BentoHeatmap cells={d.heatmapCells} />
              </SectionErrorBoundary>
            )}
          </div>

          {/* Anomalies (when present) */}
          {d.anomalies.length > 0 && (
            <SectionErrorBoundary section="anomalies">
              <BentoAnomalies anomalies={d.anomalies} />
            </SectionErrorBoundary>
          )}

          {/* Full activity feed */}
          <SectionErrorBoundary section="activity feed">
            <BentoActivityFeed events={d.activityEvents} loading={!d.activityLoaded} />
          </SectionErrorBoundary>

          {/* Failover events (when present) */}
          <div ref={d.failoverRef}>
            {d.failoverData && d.failoverData.total_failovers_7d > 0 && (
              <SectionErrorBoundary section="failover events">
                <BentoFailoverEvents data={d.failoverData} />
              </SectionErrorBoundary>
            )}
          </div>

          {/* Provider costs + savings */}
          <div ref={d.providerRef}>
            {!d.belowFoldLoaded ? (
              <Skeleton className="h-48 rounded-2xl" />
            ) : d.showProviderCosts && d.providerCosts ? (
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 sm:gap-5">
                <div className="lg:col-span-2">
                  <SectionErrorBoundary section="provider costs">
                    <BentoProviderCosts
                      providerCosts={d.providerCosts}
                      savings={d.providerSavings}
                    />
                  </SectionErrorBoundary>
                </div>
                {d.providerSavings && d.providerSavings.alternatives.length > 0 && (
                  <SectionErrorBoundary section="savings opportunities">
                    <BentoSavingsOpportunities savings={d.providerSavings} />
                  </SectionErrorBoundary>
                )}
              </div>
            ) : null}
          </div>

          {/* Cost forecast */}
          <div ref={d.forecastRef}>
            {!d.forecastVisible ? (
              <Skeleton className="h-56 rounded-2xl" />
            ) : (
              <SectionErrorBoundary section="cost forecast">
                <BentoForecast />
              </SectionErrorBoundary>
            )}
          </div>

          {/* Runs chart + cost chart */}
          {d.stats && d.stats.runs_by_day?.length > 0 && (
            <div ref={d.chartsRef}>
              {!d.chartsVisible ? (
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-5">
                  <Skeleton className="h-56 rounded-2xl" />
                  <Skeleton className="h-56 rounded-2xl" />
                </div>
              ) : (
                <SectionErrorBoundary section="charts">
                  <BentoCharts stats={d.stats} />
                </SectionErrorBoundary>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
