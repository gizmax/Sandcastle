import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/api/client";
import { POLL_INTERVAL } from "@/lib/constants";

interface RunItem {
  run_id: string;
  workflow_name: string;
  status: string;
  total_cost_usd: number;
  started_at: string | null;
  completed_at: string | null;
}

interface UseRunsOptions {
  status?: string;
  workflow?: string;
  limit?: number;
  offset?: number;
  autoPoll?: boolean;
}

export function useRuns(options: UseRunsOptions = {}) {
  const { status, workflow, limit = 50, offset = 0, autoPoll = true } = options;
  const [runs, setRuns] = useState<RunItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(true);
  // Guard against overlapping fetches from polling + manual refetch
  const fetchInFlightRef = useRef(false);
  // Track the latest fetch ID to discard stale responses when params change rapidly
  const fetchIdRef = useRef(0);

  const fetchRuns = useCallback(async () => {
    // Skip if another fetch is already in progress (prevents overlapping polls)
    if (fetchInFlightRef.current) return;
    fetchInFlightRef.current = true;
    const currentFetchId = ++fetchIdRef.current;

    const params: Record<string, string> = {
      limit: String(limit),
      offset: String(offset),
    };
    if (status) params.status = status;
    if (workflow) params.workflow = workflow;

    try {
      const res = await api.get<RunItem[]>("/runs", params);
      if (!mountedRef.current) return;
      // Discard if a newer fetch was started while this one was in flight
      if (currentFetchId !== fetchIdRef.current) return;
      if (res.data) {
        setRuns(res.data);
        setTotal(res.meta?.total ?? res.data.length);
        setError(null);
      } else if (res.error) {
        setError(res.error.message);
      }
    } catch {
      if (!mountedRef.current) return;
      if (currentFetchId !== fetchIdRef.current) return;
      setError("Failed to fetch runs");
    } finally {
      fetchInFlightRef.current = false;
      if (mountedRef.current && currentFetchId === fetchIdRef.current) {
        setLoading(false);
      }
    }
  }, [status, workflow, limit, offset]);

  useEffect(() => {
    mountedRef.current = true;
    // Reset in-flight guard on param changes so the new fetch is not blocked
    fetchInFlightRef.current = false;
    setLoading(true);
    void fetchRuns();
    return () => {
      mountedRef.current = false;
    };
  }, [fetchRuns]);

  const hasRunningRef = useRef(false);
  useEffect(() => {
    hasRunningRef.current = runs.some((r) => r.status === "running" || r.status === "queued");
  }, [runs]);

  useEffect(() => {
    if (!autoPoll) return;

    const interval = setInterval(() => {
      if (hasRunningRef.current) void fetchRuns();
    }, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [autoPoll, fetchRuns]);

  return { runs, total, loading, error, refetch: fetchRuns };
}
