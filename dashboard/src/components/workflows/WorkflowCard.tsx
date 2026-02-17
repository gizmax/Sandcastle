import { GitBranch, Play, Pencil, Eye, History } from "lucide-react";
import { cn } from "@/lib/utils";

interface WorkflowCardProps {
  name: string;
  description: string;
  stepsCount: number;
  fileName: string;
  version?: number | null;
  versionStatus?: string | null;
  totalVersions?: number | null;
  onRun: () => void;
  onEdit: () => void;
  onViewDag: () => void;
  onViewVersions?: () => void;
}

export function WorkflowCard({
  name,
  description,
  stepsCount,
  version,
  versionStatus,
  totalVersions,
  onRun,
  onEdit,
  onViewDag,
  onViewVersions,
}: WorkflowCardProps) {
  return (
    <div
      className={cn(
        "rounded-xl border border-border bg-surface p-5 shadow-sm",
        "transition-all duration-200 hover:shadow-md hover:border-accent/30"
      )}
    >
      <div className="mb-3 flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-accent/10">
          <GitBranch className="h-5 w-5 text-accent" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-semibold text-foreground">{name}</h3>
          <p className="mt-0.5 line-clamp-2 text-xs text-muted">{description}</p>
        </div>
      </div>

      <div className="mb-4 flex items-center gap-2 text-xs text-muted-foreground">
        <span>{stepsCount} step{stepsCount !== 1 ? "s" : ""}</span>
        {version != null && (
          <>
            <span className="text-border">|</span>
            <span className="font-mono">v{version}</span>
            {versionStatus && (
              <span className={cn(
                "rounded-full px-1.5 py-0.5 text-[10px] font-medium capitalize",
                versionStatus === "production" ? "bg-success/15 text-success" :
                versionStatus === "staging" ? "bg-warning/15 text-warning" :
                "bg-muted/15 text-muted"
              )}>
                {versionStatus}
              </span>
            )}
          </>
        )}
        {totalVersions != null && totalVersions > 1 && (
          <>
            <span className="text-border">|</span>
            <span>{totalVersions} versions</span>
          </>
        )}
      </div>

      <div className="flex items-center gap-2">
        <button
          onClick={onRun}
          className={cn(
            "flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-accent-foreground",
            "hover:bg-accent-hover transition-all duration-200 shadow-sm"
          )}
        >
          <Play className="h-3 w-3" />
          Run
        </button>
        <button
          onClick={onViewDag}
          className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-muted hover:text-foreground hover:bg-border/40 transition-colors"
        >
          <Eye className="h-3 w-3" />
          DAG
        </button>
        <button
          onClick={onEdit}
          className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-muted hover:text-foreground hover:bg-border/40 transition-colors"
        >
          <Pencil className="h-3 w-3" />
          Edit
        </button>
        {onViewVersions && (
          <button
            onClick={onViewVersions}
            className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-muted hover:text-foreground hover:bg-border/40 transition-colors"
          >
            <History className="h-3 w-3" />
            Versions
          </button>
        )}
      </div>
    </div>
  );
}
