import { useNavigate } from "react-router-dom";
import { GitBranch, ArrowRight } from "lucide-react";
import { RunStatusBadge } from "@/components/runs/RunStatusBadge";
import { cn } from "@/lib/utils";

interface RunTreeNode {
  run_id: string;
  workflow_name: string;
  status: string;
  replay_from_step?: string | null;
  fork_changes?: Record<string, unknown> | null;
}

interface RunTreeProps {
  parentRun?: RunTreeNode | null;
  currentRun: RunTreeNode;
  childRuns?: RunTreeNode[];
}

function RunTreeCard({
  run,
  isCurrent,
}: {
  run: RunTreeNode;
  isCurrent?: boolean;
}) {
  const navigate = useNavigate();

  return (
    <button
      onClick={() => !isCurrent && navigate(`/runs/${run.run_id}`)}
      className={cn(
        "flex items-center gap-3 rounded-lg border px-3 py-2 text-left text-sm transition-all duration-200",
        isCurrent
          ? "border-accent bg-accent/10 cursor-default"
          : "border-border bg-surface hover:bg-border/20 hover:shadow-sm cursor-pointer"
      )}
    >
      <div className="min-w-0 flex-1">
        <p
          className={cn(
            "truncate font-medium",
            isCurrent ? "text-accent" : "text-foreground"
          )}
        >
          {run.workflow_name}
        </p>
        <p className="truncate font-mono text-xs text-muted">
          {run.run_id.slice(0, 8)}...
          {run.replay_from_step && (
            <span className="ml-1 text-accent">from {run.replay_from_step}</span>
          )}
        </p>
      </div>
      <RunStatusBadge status={run.status} />
    </button>
  );
}

export function RunTree({ parentRun, currentRun, childRuns }: RunTreeProps) {
  const hasTree = parentRun || (childRuns && childRuns.length > 0);
  if (!hasTree) return null;

  return (
    <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
      <div className="mb-3 flex items-center gap-2 text-xs font-medium text-muted">
        <GitBranch className="h-3.5 w-3.5" />
        Run Lineage
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {parentRun && (
          <>
            <RunTreeCard run={parentRun} />
            <ArrowRight className="h-4 w-4 shrink-0 text-muted" />
          </>
        )}

        <RunTreeCard run={currentRun} isCurrent />

        {childRuns && childRuns.length > 0 && (
          <>
            <ArrowRight className="h-4 w-4 shrink-0 text-muted" />
            <div className="flex flex-col gap-1">
              {childRuns.map((child) => (
                <RunTreeCard key={child.run_id} run={child} />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
