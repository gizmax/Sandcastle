import { useCallback, useEffect, useState } from "react";
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

  const fetchRuns = useCallback(async () => {
    const params: Record<string, string> = {
      limit: String(limit),
      offset: String(offset),
    };
    if (status) params.status = status;
    if (workflow) params.workflow = workflow;

    const res = await api.get<RunItem[]>("/runs", params);
    if (res.data) {
      setRuns(res.data);
      setTotal(res.meta?.total ?? res.data.length);
    }
    setLoading(false);
  }, [status, workflow, limit, offset]);

  useEffect(() => {
    fetchRuns();
  }, [fetchRuns]);

  useEffect(() => {
    if (!autoPoll) return;
    const hasRunning = runs.some((r) => r.status === "running" || r.status === "queued");
    if (!hasRunning) return;

    const interval = setInterval(fetchRuns, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [runs, autoPoll, fetchRuns]);

  return { runs, total, loading, refetch: fetchRuns };
}
