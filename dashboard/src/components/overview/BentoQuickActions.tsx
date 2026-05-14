import { useNavigate } from "react-router-dom";
import { AlertTriangle, BarChart3, GitBranch, Play, Star } from "lucide-react";
import { usePinnedWorkflows } from "@/hooks/usePinnedWorkflows";
import { cn, buttonMd, buttonPrimary, buttonDanger, buttonSecondary, iconMd } from "@/lib/utils";

export function BentoQuickActions() {
  const navigate = useNavigate();
  const { pinnedWorkflows } = usePinnedWorkflows();

  return (
    <div className={cn(
      "bg-surface rounded-2xl shadow-sm border border-border",
      "hover:border-accent/30 transition-all duration-300",
      "p-6 flex flex-col gap-3 h-full",
    )}>
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">
        Quick Actions
      </p>

      <button
        onClick={() => navigate("/workflows")}
        className={cn(
          "flex items-center gap-3 rounded-xl w-full text-left font-semibold",
          buttonMd, buttonPrimary,
          "active:scale-[0.98]",
        )}
      >
        <Play className={cn(iconMd, "shrink-0")} />
        <span className="text-sm font-semibold">Run Workflow</span>
      </button>

      <button
        onClick={() => navigate("/runs?status=failed")}
        className={cn(
          "flex items-center gap-3 rounded-xl w-full text-left font-semibold",
          buttonMd, buttonDanger,
          "active:scale-[0.98]",
        )}
      >
        <AlertTriangle className={cn(iconMd, "shrink-0")} />
        <span className="text-sm font-semibold">View Failures</span>
      </button>

      <button
        onClick={() => navigate("/runs?sort=cost")}
        className={cn(
          "flex items-center gap-3 rounded-xl w-full text-left font-semibold",
          buttonMd, buttonSecondary,
          "active:scale-[0.98]",
        )}
      >
        <BarChart3 className={cn(iconMd, "shrink-0")} />
        <span className="text-sm font-semibold">Cost Report</span>
      </button>

      {pinnedWorkflows.length > 0 && (
        <div className="mt-1 pt-3 border-t border-border">
          <div className="flex items-center gap-2 mb-2">
            <Star className="h-3.5 w-3.5 text-accent fill-accent" />
            <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Pinned</span>
          </div>
          <div className="space-y-1">
            {pinnedWorkflows.slice(0, 3).map((wfName) => (
              <button
                key={wfName}
                onClick={() => navigate(`/workflows/${encodeURIComponent(wfName)}`)}
                className={cn(
                  "flex items-center gap-2 w-full rounded-lg px-3 py-2",
                  "bg-background border border-border",
                  "hover:border-accent/30 hover:bg-surface",
                  "transition-colors text-left",
                )}
              >
                <GitBranch className="h-3 w-3 text-running shrink-0" />
                <span className="text-sm text-foreground truncate">{wfName}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
