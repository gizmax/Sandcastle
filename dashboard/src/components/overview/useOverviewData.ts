import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/api/client";
import { useEventStreamContext } from "@/hooks/useEventStreamContext";
import {
  normaliseHeatmapCell,
  normaliseSparkline,
} from "./bentoTypes";
import type {
  AdvisorRecommendation,
  AdvisorRecommendationsData,
  AnomalyItem,
  ApiSparklineData,
  AuditEvent,
  FailoverEventsData,
  HeatmapApiCell,
  HeatmapCell,
  ProviderCostsData,
  ProviderRecommendation,
  ProviderSavingsData,
  RunItem,
  SparklineData,
  Stats,
} from "./bentoTypes";
import { isDismissed } from "./dismissedRecommendations";
import { useLazyLoad } from "./useLazyLoad";

/**
 * Encapsulates all of the data fetching, lazy-loading, and SSE refresh logic
 * used by the Overview page. Returns the state plus sentinel refs that the
 * orchestrator attaches to each section.
 */
export function useOverviewData() {
  // Above-fold
  const [stats, setStats] = useState<Stats | null>(null);
  const [recentRuns, setRecentRuns] = useState<RunItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const [sparklines, setSparklines] = useState<Record<string, SparklineData> | null>(null);
  const [anomalies, setAnomalies] = useState<AnomalyItem[]>([]);
  const [workflowCount, setWorkflowCount] = useState<number | null>(null);

  // Below-fold
  const [heatmapCells, setHeatmapCells] = useState<HeatmapCell[]>([]);
  const [heatmapLoaded, setHeatmapLoaded] = useState(false);
  const [failoverData, setFailoverData] = useState<FailoverEventsData | null>(null);
  const [providerCosts, setProviderCosts] = useState<ProviderCostsData | null>(null);
  const [providerSavings, setProviderSavings] = useState<ProviderSavingsData | null>(null);
  const [topRecommendation, setTopRecommendation] = useState<ProviderRecommendation | null>(null);
  const [advisorRecs, setAdvisorRecs] = useState<AdvisorRecommendation[]>([]);
  const [advisorTotalSavings, setAdvisorTotalSavings] = useState(0);
  const [recDismissed, setRecDismissed] = useState(false);
  const [belowFoldLoaded, setBelowFoldLoaded] = useState(false);
  const [activityEvents, setActivityEvents] = useState<AuditEvent[]>([]);
  const [activityLoaded, setActivityLoaded] = useState(false);
  const [failoverLoaded, setFailoverLoaded] = useState(false);
  const [advisorRecsLoaded, setAdvisorRecsLoaded] = useState(false);

  // Gate below-fold fetches behind a 1-second timer so they never block
  // the initial render, even when sections start inside the viewport.
  const [belowFoldReady, setBelowFoldReady] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setBelowFoldReady(true), 1000);
    return () => clearTimeout(t);
  }, []);

  const { subscribe } = useEventStreamContext();
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Lazy-load sentinels
  const [heatmapRef, heatmapVisible] = useLazyLoad();
  const [providerRef, providerVisible] = useLazyLoad();
  const [forecastRef, forecastVisible] = useLazyLoad();
  const [chartsRef, chartsVisible] = useLazyLoad();
  const [failoverRef, failoverVisible] = useLazyLoad();
  const [activityRef, activityVisible] = useLazyLoad();

  const uniqueProviderCount = useMemo(() => {
    if (!providerCosts) return 0;
    return new Set(providerCosts.by_provider.map((p) => p.provider)).size;
  }, [providerCosts]);
  const showProviderCosts = uniqueProviderCount >= 2;

  const fetchSparklines = useCallback(async () => {
    const res = await api.get<Record<string, ApiSparklineData>>("/stats/sparklines");
    if (res.data) {
      const normalised: Record<string, SparklineData> = {};
      for (const [k, v] of Object.entries(res.data)) normalised[k] = normaliseSparkline(v);
      setSparklines(normalised);
    }
  }, []);

  const fetchStats = useCallback(async () => {
    const [statsRes, runsRes] = await Promise.all([
      api.get<Stats>("/stats"),
      api.get<RunItem[]>("/runs", { limit: "5" }),
    ]);
    if (statsRes.data) setStats(statsRes.data);
    if (runsRes.data) setRecentRuns(runsRes.data);
  }, []);

  // Above-fold
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setError(null);
        const [statsRes, runsRes, sparkRes, anomRes, wfRes] = await Promise.all([
          api.get<Stats>("/stats"),
          api.get<RunItem[]>("/runs", { limit: "5" }),
          api.get<Record<string, ApiSparklineData>>("/stats/sparklines"),
          api.get<AnomalyItem[]>("/stats/anomalies"),
          api.get<{ name: string }[]>("/workflows"),
        ]);
        if (cancelled) return;
        if (statsRes.data) setStats(statsRes.data);
        if (runsRes.data) setRecentRuns(runsRes.data);
        if (sparkRes.data) {
          const normalised: Record<string, SparklineData> = {};
          for (const [k, v] of Object.entries(sparkRes.data)) normalised[k] = normaliseSparkline(v);
          setSparklines(normalised);
        }
        if (anomRes.data) setAnomalies(anomRes.data);
        setWorkflowCount(Array.isArray(wfRes.data) ? wfRes.data.length : 0);
      } catch {
        if (cancelled) return;
        setError("Could not connect to the API server");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [retryCount]);

  // Heatmap
  useEffect(() => {
    if (!belowFoldReady || !heatmapVisible || heatmapLoaded) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await api.get<HeatmapApiCell[]>("/stats/heatmap");
        if (cancelled) return;
        if (res.data) setHeatmapCells(res.data.map(normaliseHeatmapCell));
      } catch { /* non-critical */ }
      finally { if (!cancelled) setHeatmapLoaded(true); }
    })();
    return () => { cancelled = true; };
  }, [belowFoldReady, heatmapVisible, heatmapLoaded]);

  // Provider costs / savings / recommendation
  useEffect(() => {
    if (!belowFoldReady || belowFoldLoaded) return;
    if (!providerVisible && !forecastVisible) return;
    let cancelled = false;
    (async () => {
      try {
        const [costsRes, savingsRes, recRes] = await Promise.all([
          api.get<ProviderCostsData>("/stats/provider-costs"),
          api.get<ProviderSavingsData>("/stats/provider-savings"),
          api.get<{ recommendations: ProviderRecommendation[] }>("/stats/provider-recommendation"),
        ]);
        if (cancelled) return;
        if (costsRes.data) setProviderCosts(costsRes.data);
        if (savingsRes.data) setProviderSavings(savingsRes.data);
        if (recRes.data) {
          const high = recRes.data.recommendations?.find(
            (r) => r.severity === "high" && !isDismissed(r.title),
          ) ?? null;
          setTopRecommendation(high);
          if (high === null && advisorRecs.length === 0) setRecDismissed(true);
        }
      } catch { /* non-critical */ }
      finally { if (!cancelled) setBelowFoldLoaded(true); }
    })();
    return () => { cancelled = true; };
  }, [belowFoldReady, providerVisible, forecastVisible, belowFoldLoaded, advisorRecs.length]);

  // Failover
  useEffect(() => {
    if (!belowFoldReady || !failoverVisible || failoverLoaded) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await api.get<FailoverEventsData>("/stats/failover-events");
        if (cancelled) return;
        if (res.data) setFailoverData(res.data);
      } catch { /* non-critical */ }
      finally { if (!cancelled) setFailoverLoaded(true); }
    })();
    return () => { cancelled = true; };
  }, [belowFoldReady, failoverVisible, failoverLoaded]);

  // Activity feed
  useEffect(() => {
    if (!belowFoldReady || !activityVisible || activityLoaded) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await api.get<AuditEvent[]>("/audit", { limit: "15" });
        if (cancelled) return;
        if (res.data) setActivityEvents(res.data);
      } catch { /* non-critical */ }
      finally { if (!cancelled) setActivityLoaded(true); }
    })();
    return () => { cancelled = true; };
  }, [belowFoldReady, activityVisible, activityLoaded]);

  // Advisor recs
  useEffect(() => {
    if (!belowFoldReady || !providerVisible || advisorRecsLoaded) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await api.get<AdvisorRecommendationsData>("/advisor/recommendations");
        if (cancelled) return;
        let has = false;
        if (res.data?.recommendations?.length) {
          const dismissKey = `advisor-${res.data.total_potential_savings.toFixed(0)}`;
          if (!isDismissed(dismissKey)) {
            setAdvisorRecs(res.data.recommendations);
            setAdvisorTotalSavings(res.data.total_potential_savings);
            has = true;
          }
        }
        if (!has && !topRecommendation) setRecDismissed(true);
      } catch { /* non-critical */ }
      finally { if (!cancelled) setAdvisorRecsLoaded(true); }
    })();
    return () => { cancelled = true; };
  }, [belowFoldReady, providerVisible, advisorRecsLoaded, topRecommendation]);

  // SSE refresh: sparklines + stats on run.completed / run.failed
  useEffect(() => {
    const unsub = subscribe("*", (event) => {
      if (["run.completed", "run.failed"].includes(event.type)) {
        if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
        refreshTimerRef.current = setTimeout(() => {
          void fetchSparklines();
          void fetchStats();
        }, 1000);
      }
    });
    return () => {
      unsub();
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
    };
  }, [subscribe, fetchSparklines, fetchStats]);

  const retry = useCallback(() => {
    setLoading(true);
    setRetryCount((c) => c + 1);
  }, []);

  return {
    // state
    stats,
    recentRuns,
    loading,
    error,
    sparklines,
    anomalies,
    workflowCount,
    heatmapCells,
    heatmapLoaded,
    failoverData,
    providerCosts,
    providerSavings,
    topRecommendation,
    advisorRecs,
    advisorTotalSavings,
    recDismissed,
    belowFoldLoaded,
    activityEvents,
    activityLoaded,
    showProviderCosts,
    forecastVisible,
    chartsVisible,
    // setters used by orchestrator
    setAdvisorRecs,
    setAdvisorTotalSavings,
    setRecDismissed,
    // sentinel refs
    heatmapRef,
    providerRef,
    forecastRef,
    chartsRef,
    failoverRef,
    activityRef,
    // actions
    retry,
  };
}
