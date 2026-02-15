import { useCallback, useEffect, useState } from "react";
import { Castle } from "lucide-react";
import { api } from "@/api/client";
import { StatsCards } from "@/components/overview/StatsCards";
import { RunsChart } from "@/components/overview/RunsChart";
import { CostChart } from "@/components/overview/CostChart";
import { RecentRuns } from "@/components/overview/RecentRuns";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";

interface Stats {
  total_runs_today: number;
  success_rate: number;
  total_cost_today: number;
  avg_duration_seconds: number;
  runs_by_day: Array<{ date: string; completed: number; failed: number; total: number }>;
  cost_by_workflow: Array<{ workflow: string; cost: number }>;
}

interface RunItem {
  run_id: string;
  workflow_name: string;
  status: string;
  total_cost_usd: number;
  started_at: string | null;
}

export default function Overview() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [recentRuns, setRecentRuns] = useState<RunItem[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const [statsRes, runsRes] = await Promise.all([
        api.get<Stats>("/stats"),
        api.get<RunItem[]>("/runs", { limit: "5" }),
      ]);
      if (statsRes.data) setStats(statsRes.data);
      if (runsRes.data) setRecentRuns(runsRes.data);
    } catch {
      // API may not be available
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (!stats || stats.total_runs_today === 0) {
    return (
      <div>
        <h1 className="mb-6 text-2xl font-semibold tracking-tight text-foreground">Overview</h1>
        {stats && <StatsCards totalRuns={0} successRate={0} totalCost={0} avgDuration={0} />}
        <EmptyState
          icon={Castle}
          title="No runs yet"
          description="Create your first workflow to get started."
          action={{ label: "Create Workflow", onClick: () => window.location.assign("/workflows/builder") }}
          className="mt-8"
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight text-foreground">Overview</h1>

      <StatsCards
        totalRuns={stats.total_runs_today}
        successRate={stats.success_rate}
        totalCost={stats.total_cost_today}
        avgDuration={stats.avg_duration_seconds}
      />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <RunsChart data={stats.runs_by_day} />
        <CostChart data={stats.cost_by_workflow} />
      </div>

      <RecentRuns runs={recentRuns} />
    </div>
  );
}
