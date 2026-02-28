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

interface AdvisorState {
  score: number;
  previousScore: number | null;
  deductions: Deduction[];
  activeInsights: Insight[];
  loading: boolean;
  lastChecked: Date | null;
}

export function useAdvisor() {
  const [state, setState] = useState<AdvisorState>({
    score: 100,
    previousScore: null,
    deductions: [],
    activeInsights: [],
    loading: true,
    lastChecked: null,
  });

  const lastRefreshRef = useRef(0);
  const mountedRef = useRef(true);
  const { subscribe } = useEventStreamContext();

  const fetchAll = useCallback(async () => {
    const [
      healthRes,
      statsRes,
      runsRes,
      toolsRes,
      dlqRes,
      violationsRes,
      optimizerRes,
      autopilotRes,
      approvalsRes,
      workflowsRes,
      schedulesRes,
      evalRes,
      apiKeysRes,
    ] = await Promise.all([
      api.get<AdvisorData["health"]>("/health"),
      api.get<AdvisorData["stats"]>("/stats"),
      api.get<AdvisorData["runs"][number][]>("/runs", { limit: "100" }),
      api.get<{ tools: AdvisorData["tools"] }>("/tools"),
      api.get<AdvisorData["dlq"][number][]>("/dead-letter"),
      api.get<AdvisorData["violationStats"]>("/violations/stats"),
      api.get<AdvisorData["optimizerStats"]>("/optimizer/stats"),
      api.get<AdvisorData["autopilotStats"]>("/autopilot/stats"),
      api.get<AdvisorData["approvals"][number][]>("/approvals"),
      api.get<AdvisorData["workflows"][number][]>("/workflows"),
      api.get<AdvisorData["schedules"][number][]>("/schedules"),
      api.get<AdvisorData["evalStats"]>("/eval/stats"),
      api.get<AdvisorData["apiKeys"][number][]>("/api-keys"),
    ]);

    // Don't update state if the component unmounted during fetch
    if (!mountedRef.current) return;

    const data: AdvisorData = {
      health: healthRes.data ?? null,
      stats: statsRes.data ?? null,
      runs: runsRes.data ?? [],
      tools: toolsRes.data?.tools ?? [],
      dlq: dlqRes.data ?? [],
      violationStats: violationsRes.data ?? null,
      optimizerStats: optimizerRes.data ?? null,
      autopilotStats: autopilotRes.data ?? null,
      approvals: approvalsRes.data ?? [],
      workflows: workflowsRes.data ?? [],
      schedules: schedulesRes.data ?? [],
      evalStats: evalRes.data ?? null,
      apiKeys: apiKeysRes.data ?? [],
    };

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
    }));

    lastRefreshRef.current = Date.now();
  }, []);

  // Track mounted state for cleanup
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Initial fetch
  useEffect(() => {
    void fetchAll();
  }, [fetchAll]);

  // Periodic refresh
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
