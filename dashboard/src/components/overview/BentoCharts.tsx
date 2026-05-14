import { CostChart } from "@/components/overview/CostChart";
import { RunsChart } from "@/components/overview/RunsChart";
import { cn } from "@/lib/utils";
import type { Stats } from "./bentoTypes";

/** Two-up grid showing runs-by-day and cost-by-workflow charts. */
export function BentoCharts({ stats }: { stats: Stats }) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-5">
      <div className={cn(
        "bg-surface rounded-2xl shadow-sm border border-border",
        "hover:border-accent/30 transition-all duration-300",
        "[&>div]:rounded-2xl [&>div]:border-0 [&>div]:shadow-none [&>div]:bg-transparent",
      )}>
        <RunsChart data={stats.runs_by_day} />
      </div>
      <div className={cn(
        "bg-surface rounded-2xl shadow-sm border border-border",
        "hover:border-accent/30 transition-all duration-300",
        "[&>div]:rounded-2xl [&>div]:border-0 [&>div]:shadow-none [&>div]:bg-transparent",
      )}>
        {stats.cost_by_workflow && <CostChart data={stats.cost_by_workflow} />}
      </div>
    </div>
  );
}
