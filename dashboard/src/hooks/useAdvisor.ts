import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/api/client";
import { useEventStreamContext } from "@/hooks/useEventStreamContext";
import {
  computeScore,
  generateInsights,
  filterDismissed,
  dismissInsight as persistDismiss,
  type AdvisorData,
  type Deduction,
  type Insight,
} from "@/lib/insights";

const REFRESH_INTERVAL = 5 * 60 * 1000; // 5 minutes
const SSE_COOLDOWN = 2000; // 2 seconds
const DEFERRED_DELAY = 2000; // 2 seconds before fetching non-critical endpoints on mount

interface AdvisorState {
  score: number;
  previousScore: number | null;
  deductions: Deduction[];
  activeInsights: Insight[];
  loading: boolean;
  lastChecked: Date | null;
  errors: string[];
}

// Default empty data used while deferred endpoints are still loading
const EMPTY_DEFERRED: Omit<AdvisorData, "health" | "stats" | "runs" | "workflows"> = {
  tools: [],
  dlq: [],
  violationStats: null,
  optimizerStats: null,
  autopilotStats: null,
  approvals: [],
  schedules: [],
  evalStats: null,
  apiKeys: [],
};

export function useAdvisor() {
  const [state, setState] = useState<AdvisorState>({
    score: 100,
    previousScore: null,
    deductions: [],
    activeInsights: [],
    loading: true,
    lastChecked: null,
    errors: [],
  });

  const lastRefreshRef = useRef(0);
  const mountedRef = useRef(true);
  const deferredTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Latest critical-phase results so the deferred callback can merge without
  // stale closures.
  const criticalDataRef = useRef<{
    health: AdvisorData["health"];
    stats: AdvisorData["stats"];
    runs: AdvisorData["runs"];
    workflows: AdvisorData["workflows"];
    errors: string[];
  } | null>(null);
  const { subscribe } = useEventStreamContext();

  // Recompute score/insights from full AdvisorData and update state
  const applyData = useCallback((data: AdvisorData, errors: string[]) => {
    const { score, deductions } = computeScore(data);
    const allInsights = generateInsights(data);
    const activeInsights = filterDismissed(allInsights);

    setState((prev) => ({
      score,
      previousScore: prev.lastChecked ? prev.score : null,
      deductions,
      activeInsights,
      loading: false,
      lastChecked: new Date(),
      errors,
    }));

    lastRefreshRef.current = Date.now();
  }, []);

  // Phase 1: Critical endpoints - /health, /stats, /runs, /workflows
  // Updates state immediately so the page can render above-fold content.
  const fetchCritical = useCallback(async () => {
    const [healthRes, statsRes, runsRes, workflowsRes] = await Promise.all([
      api.get<AdvisorData["health"]>("/health"),
      api.get<AdvisorData["stats"]>("/stats"),
      api.get<AdvisorData["runs"][number][]>("/runs", { limit: "100" }),
      api.get<AdvisorData["workflows"][number][]>("/workflows"),
    ]);

    if (!mountedRef.current) return;

    const criticalResponses = [
      ["health", healthRes],
      ["stats", statsRes],
      ["runs", runsRes],
      ["workflows", workflowsRes],
    ] as const;

    const criticalErrors: string[] = [];
    for (const [name, res] of criticalResponses) {
      if (res.error) {
        criticalErrors.push(`${name}: ${res.error.message ?? res.error.code}`);
      }
    }

    const criticalData: AdvisorData = {
      health: healthRes.data ?? null,
      stats: statsRes.data ?? null,
      runs: runsRes.data ?? [],
      workflows: workflowsRes.data ?? [],
      ...EMPTY_DEFERRED,
    };

    criticalDataRef.current = {
      health: criticalData.health,
      stats: criticalData.stats,
      runs: criticalData.runs,
      workflows: criticalData.workflows,
      errors: criticalErrors,
    };

    // Immediately unblock the render with partial data
    applyData(criticalData, criticalErrors);
  }, [applyData]);

  // Phase 2: Deferred endpoints - /tools, /dead-letter, /violations/stats, etc.
  // Merges results with the latest critical data.
  const fetchDeferred = useCallback(async () => {
    const [
      toolsRes,
      dlqRes,
      violationsRes,
      optimizerRes,
      autopilotRes,
      approvalsRes,
      schedulesRes,
      evalRes,
      apiKeysRes,
    ] = await Promise.all([
      api.get<{ tools: AdvisorData["tools"] }>("/tools"),
      api.get<AdvisorData["dlq"][number][]>("/dead-letter"),
      api.get<AdvisorData["violationStats"]>("/violations/stats"),
      api.get<AdvisorData["optimizerStats"]>("/optimizer/stats"),
      api.get<AdvisorData["autopilotStats"]>("/autopilot/stats"),
      api.get<AdvisorData["approvals"][number][]>("/approvals"),
      api.get<AdvisorData["schedules"][number][]>("/schedules"),
      api.get<AdvisorData["evalStats"]>("/eval/stats"),
      api.get<AdvisorData["apiKeys"][number][]>("/api-keys"),
    ]);

    if (!mountedRef.current) return;

    const deferredResponses = [
      ["tools", toolsRes],
      ["dead-letter", dlqRes],
      ["violations", violationsRes],
      ["optimizer", optimizerRes],
      ["autopilot", autopilotRes],
      ["approvals", approvalsRes],
      ["schedules", schedulesRes],
      ["eval", evalRes],
      ["api-keys", apiKeysRes],
    ] as const;

    const deferredErrors: string[] = [];
    for (const [name, res] of deferredResponses) {
      if (res.error) {
        deferredErrors.push(`${name}: ${res.error.message ?? res.error.code}`);
      }
    }

    const critical = criticalDataRef.current;
    const mergedData: AdvisorData = {
      health: critical?.health ?? null,
      stats: critical?.stats ?? null,
      runs: critical?.runs ?? [],
      workflows: critical?.workflows ?? [],
      tools: toolsRes.data?.tools ?? [],
      dlq: dlqRes.data ?? [],
      violationStats: violationsRes.data ?? null,
      optimizerStats: optimizerRes.data ?? null,
      autopilotStats: autopilotRes.data ?? null,
      approvals: approvalsRes.data ?? [],
      schedules: schedulesRes.data ?? [],
      evalStats: evalRes.data ?? null,
      apiKeys: apiKeysRes.data ?? [],
    };

    const allErrors = [...(critical?.errors ?? []), ...deferredErrors];
    applyData(mergedData, allErrors);
  }, [applyData]);

  // Full refresh: runs both phases sequentially without delay.
  // Used by periodic refresh, SSE triggers, and the public refresh() API.
  const fetchAll = useCallback(async () => {
    await fetchCritical();
    await fetchDeferred();
  }, [fetchCritical, fetchDeferred]);

  // Track mounted state for cleanup
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (deferredTimerRef.current) clearTimeout(deferredTimerRef.current);
    };
  }, []);

  // Initial mount: fetch critical immediately, defer the rest by 2 seconds
  // so the main page render is not blocked by 13 parallel requests.
  useEffect(() => {
    const scheduleDeferred = () => {
      if (!mountedRef.current) return;
      deferredTimerRef.current = setTimeout(() => {
        void fetchDeferred();
      }, DEFERRED_DELAY);
    };
    // Schedule deferred phase regardless of whether critical phase succeeds or
    // fails, so the advisor still collects secondary data on network errors.
    void fetchCritical().then(scheduleDeferred, scheduleDeferred);
  }, [fetchCritical, fetchDeferred]);

  // Periodic refresh (runs both phases immediately)
  useEffect(() => {
    const interval = setInterval(fetchAll, REFRESH_INTERVAL);
    return () => clearInterval(interval);
  }, [fetchAll]);

  // SSE-triggered refresh with cooldown
  useEffect(() => {
    const unsub = subscribe("*", (event) => {
      const triggers = ["run.completed", "run.failed", "dlq.new"];
      if (!triggers.includes(event.type)) return;
      if (Date.now() - lastRefreshRef.current < SSE_COOLDOWN) return;
      void fetchAll();
    });
    return unsub;
  }, [subscribe, fetchAll]);

  const dismiss = useCallback((id: string) => {
    persistDismiss(id);
    setState((prev) => ({
      ...prev,
      activeInsights: prev.activeInsights.filter((i) => i.id !== id),
    }));
  }, []);

  return {
    ...state,
    refresh: fetchAll,
    dismiss,
  };
}
