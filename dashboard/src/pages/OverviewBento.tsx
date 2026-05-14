import { useCallback } from "react";
import { Castle } from "lucide-react";
import { SectionErrorBoundary } from "@/components/shared/ErrorBoundary";
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
import { dismissRec } from "@/components/overview/dismissedRecommendations";
import { useOverviewData } from "@/components/overview/useOverviewData";
import { useAdvisorContext } from "@/hooks/useAdvisorContext";

// Re-export LayoutSwitcher so legacy importers (e.g. OverviewFocus) keep working.
export { LayoutSwitcher } from "@/components/overview/LayoutSwitcher";

export default function OverviewBento() {
  const advisor = useAdvisorContext();
  const d = useOverviewData();

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
      <div className="space-y-4 sm:space-y-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="flex items-center justify-center w-8 h-8 rounded-xl bg-accent">
              <Castle className="h-4 w-4 text-accent-foreground" />
            </div>
            <h1 className="text-xl font-bold tracking-tight text-foreground">Overview</h1>
          </div>
        </div>

        <SectionErrorBoundary section="health">
          <BentoHealthHero
            score={advisor.score}
            activeInsights={advisor.activeInsights}
            loading={advisor.loading}
            totalRuns={0}
            successRate={0}
            totalCost={0}
            avgDuration={0}
          />
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
    <div className="space-y-4 sm:space-y-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="flex items-center justify-center w-8 h-8 rounded-xl bg-accent">
            <Castle className="h-4 w-4 text-accent-foreground" />
          </div>
          <h1 className="text-xl font-bold tracking-tight text-foreground">Overview</h1>
        </div>
      </div>

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

      {/* Row 1: Health hero + Quick actions */}
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

      {/* Row 2: 4 stat cards */}
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

      {/* Row 3: Activity heatmap */}
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

      {/* Activity feed */}
      <div ref={d.activityRef}>
        <SectionErrorBoundary section="activity feed">
          <BentoActivityFeed events={d.activityEvents} loading={!d.activityLoaded} />
        </SectionErrorBoundary>
      </div>

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

      {/* Cost forecast + recent runs */}
      <div ref={d.forecastRef}>
        {!d.forecastVisible ? (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 sm:gap-5">
            <Skeleton className="h-56 rounded-2xl lg:col-span-2" />
            <Skeleton className="h-56 rounded-2xl" />
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 sm:gap-5">
            <div className="lg:col-span-2">
              <SectionErrorBoundary section="cost forecast">
                <BentoForecast />
              </SectionErrorBoundary>
            </div>
            <div>
              <SectionErrorBoundary section="recent runs">
                <BentoRecentRuns runs={d.recentRuns} />
              </SectionErrorBoundary>
            </div>
          </div>
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
  );
}
