import { useNavigate } from "react-router-dom";
import { RunStatusBadge } from "@/components/runs/RunStatusBadge";
import { formatRelativeTime } from "@/lib/utils";
import { cn } from "@/lib/utils";

interface RunItem {
  run_id: string;
  workflow_name: string;
  status: string;
  total_cost_usd: number;
  started_at: string | null;
}

interface RecentRunsProps {
  runs: RunItem[];
}

export function RecentRuns({ runs }: RecentRunsProps) {
  const navigate = useNavigate();

  if (runs.length === 0) return null;

  return (
    <div className="rounded-xl border border-border bg-surface shadow-sm">
      <div className="border-b border-border px-5 py-3">
        <h3 className="text-sm font-semibold text-foreground">Recent Runs</h3>
      </div>
      <div className="divide-y divide-border">
        {runs.slice(0, 5).map((run) => (
          <button
            key={run.run_id}
            onClick={() => navigate(`/runs/${run.run_id}`)}
            className={cn(
              "flex w-full items-center gap-4 px-5 py-3 text-left",
              "transition-colors duration-150 hover:bg-border/20"
            )}
          >
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-foreground">
                {run.workflow_name}
              </p>
              <p className="text-xs text-muted">
                {run.started_at ? formatRelativeTime(run.started_at) : "queued"}
              </p>
            </div>
            <RunStatusBadge status={run.status} />
          </button>
        ))}
      </div>
    </div>
  );
}
