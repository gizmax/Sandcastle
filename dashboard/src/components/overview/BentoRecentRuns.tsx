import { useNavigate } from "react-router-dom";
import { cn, formatCost, formatRelativeTime } from "@/lib/utils";
import type { RunItem } from "./bentoTypes";

export function BentoRecentRuns({ runs }: { runs: RunItem[] }) {
  const navigate = useNavigate();

  if (runs.length === 0) return null;

  const statusStyle = (status: string) => {
    switch (status) {
      case "completed": return "bg-success/10 text-success";
      case "failed": return "bg-error/10 text-error";
      case "running": return "bg-running/10 text-running";
      default: return "bg-border text-muted-foreground";
    }
  };

  return (
    <div className={cn(
      "bg-surface rounded-2xl shadow-sm border border-border",
      "hover:border-accent/30 transition-all duration-300",
      "overflow-hidden",
    )}>
      <div className="px-5 py-4 border-b border-border">
        <h3 className="text-sm font-semibold text-foreground">Recent Runs</h3>
      </div>
      <div className="divide-y divide-border">
        {runs.slice(0, 5).map((run) => (
          <button
            key={run.run_id}
            onClick={() => navigate(`/runs/${run.run_id}`)}
            className="flex w-full items-center gap-3 px-5 py-3.5 text-left hover:bg-background transition-colors duration-150"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-foreground">{run.workflow_name}</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                {run.started_at ? formatRelativeTime(run.started_at) : "queued"}
              </p>
            </div>
            <span className="text-xs font-mono text-muted-foreground shrink-0">
              {formatCost(run.total_cost_usd)}
            </span>
            <span className={cn(
              "text-[10px] font-semibold rounded-full px-2 py-0.5 shrink-0 capitalize",
              statusStyle(run.status),
            )}>
              {run.status}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
