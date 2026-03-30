import { memo, useState } from "react";
import { GitBranch, Play, Pencil, Eye, History, Check, Clock, CheckCircle, XCircle, Star, Layers } from "lucide-react";
import { cn } from "@/lib/utils";

// --- Mock workflow stats (deterministic, based on workflow name hash) ---
// TODO: Replace with real API data when per-workflow stats endpoint is available

/** Simple deterministic hash from a string, returns 0..1 float. */
function nameHash(name: string): number {
  let h = 0;
  for (let i = 0; i < name.length; i++) {
    h = ((h << 5) - h + name.charCodeAt(i)) | 0;
  }
  return Math.abs(h % 10000) / 10000;
}

export interface WorkflowStats {
  totalRuns: number;
  successRate: number; // 0-100
  avgCost: number;
  lastRunStatus: "completed" | "failed" | "running" | null;
  lastRunAgo: string | null; // e.g. "2h ago", "3d ago"
}

/** Generate deterministic mock stats from a workflow name. */
export function mockWorkflowStats(name: string): WorkflowStats {
  const h = nameHash(name);
  const h2 = nameHash(name + "_salt");
  const h3 = nameHash(name + "_cost");

  const totalRuns = Math.floor(h * 180) + 1; // 1-180
  const successRate = Math.floor(60 + h2 * 40); // 60-100
  const avgCost = Math.round((0.01 + h3 * 0.45) * 100) / 100; // $0.01-$0.46

  const statuses: Array<"completed" | "failed" | "running"> = ["completed", "completed", "completed", "failed", "running"];
  const lastRunStatus = totalRuns > 0 ? statuses[Math.floor(h * statuses.length)] : null;

  const timeUnits = ["1m ago", "12m ago", "1h ago", "2h ago", "5h ago", "1d ago", "3d ago", "1w ago"];
  const lastRunAgo = totalRuns > 0 ? timeUnits[Math.floor(h2 * timeUnits.length)] : null;

  return { totalRuns, successRate, avgCost, lastRunStatus, lastRunAgo };
}

type HealthLevel = "healthy" | "degraded" | "failing";

function deriveHealth(successRate: number): HealthLevel {
  if (successRate >= 90) return "healthy";
  if (successRate >= 70) return "degraded";
  return "failing";
}

const healthConfig: Record<HealthLevel, { dot: string; label: string; text: string }> = {
  healthy:  { dot: "bg-success",  label: "Healthy",  text: "text-success" },
  degraded: { dot: "bg-warning",  label: "Degraded", text: "text-warning" },
  failing:  { dot: "bg-error",    label: "Failing",  text: "text-error" },
};
// --- End mock stats ---

interface WorkflowCardProps {
  name: string;
  description: string;
  stepsCount: number;
  fileName: string;
  version?: number | null;
  versionStatus?: string | null;
  totalVersions?: number | null;
  selected?: boolean;
  pinned?: boolean;
  stats?: WorkflowStats;
  onSelect?: () => void;
  onTogglePin?: () => void;
  onRun: () => void;
  onBatch?: () => void;
  onEdit: () => void;
  onViewDag: () => void;
  onViewVersions?: () => void;
}

export const WorkflowCard = memo(function WorkflowCard({
  name,
  description,
  stepsCount,
  version,
  versionStatus,
  totalVersions,
  selected,
  pinned,
  stats,
  onSelect,
  onTogglePin,
  onRun,
  onBatch,
  onEdit,
  onViewDag,
  onViewVersions,
}: WorkflowCardProps) {
  const health = stats ? deriveHealth(stats.successRate) : null;
  const hc = health ? healthConfig[health] : null;
  const [pinBounce, setPinBounce] = useState(false);

  const handlePinClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!onTogglePin) return;
    setPinBounce(true);
    onTogglePin();
    setTimeout(() => setPinBounce(false), 200);
  };

  return (
    <div
      className={cn(
        "group/card relative rounded-xl border border-border bg-surface p-5 shadow-sm",
        "transition-all duration-200",
        "hover:shadow-md hover:border-accent/40 hover:-translate-y-0.5",
        selected && "border-accent ring-2 ring-accent/20"
      )}
    >
      {/* Pin / star button */}
      {onTogglePin && (
        <button
          type="button"
          onClick={handlePinClick}
          aria-label={pinned ? `Unpin ${name}` : `Pin ${name}`}
          className={cn(
            "absolute left-3 top-3 flex h-5 w-5 items-center justify-center rounded transition-all duration-200",
            pinned
              ? "text-accent"
              : "text-muted opacity-0 group-hover/card:opacity-100 hover:opacity-100 hover:text-accent/70"
          )}
          style={{ transform: pinBounce ? "scale(1.2)" : "scale(1)", transition: "transform 200ms ease" }}
        >
          <Star className={cn("h-3.5 w-3.5", pinned && "fill-current")} />
        </button>
      )}

      {/* Selection checkbox */}
      {onSelect && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onSelect(); }}
          aria-label={selected ? `Deselect ${name}` : `Select ${name}`}
          className={cn(
            "absolute right-3 top-3 flex h-5 w-5 items-center justify-center rounded border transition-colors",
            selected
              ? "border-accent bg-accent text-accent-foreground"
              : "border-border bg-background text-transparent hover:border-muted"
          )}
        >
          <Check className="h-3 w-3" />
        </button>
      )}

      {/* Health badge - top right, offset from checkbox */}
      {hc && (
        <div className={cn(
          "absolute top-3 flex items-center gap-1.5",
          onSelect ? "right-10" : "right-3"
        )}>
          <span className={cn("h-2 w-2 rounded-full", hc.dot)} />
          <span className={cn("text-[10px] font-medium", hc.text)}>{hc.label}</span>
        </div>
      )}

      <div className="mb-2 flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-accent/10">
          <GitBranch className="h-5 w-5 text-accent" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-semibold text-foreground" title={name}>{name}</h3>
          <p className="mt-0.5 line-clamp-2 text-xs text-muted" title={description}>{description}</p>
        </div>
      </div>

      {/* Last run status */}
      <div className="mb-3 flex items-center gap-1.5 text-xs text-muted">
        {stats && stats.lastRunStatus ? (
          <>
            {stats.lastRunStatus === "completed" && (
              <CheckCircle className="h-3 w-3 text-success" />
            )}
            {stats.lastRunStatus === "failed" && (
              <XCircle className="h-3 w-3 text-error" />
            )}
            {stats.lastRunStatus === "running" && (
              <Clock className="h-3 w-3 text-running" />
            )}
            <span>
              Last run: {stats.lastRunAgo} -{" "}
              <span className={cn(
                stats.lastRunStatus === "completed" && "text-success",
                stats.lastRunStatus === "failed" && "text-error",
                stats.lastRunStatus === "running" && "text-running",
              )}>
                {stats.lastRunStatus}
              </span>
            </span>
          </>
        ) : (
          <>
            <Clock className="h-3 w-3" />
            <span>Never run</span>
          </>
        )}
      </div>

      <div className="mb-3 flex items-center gap-2 text-xs text-muted-foreground">
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

      {/* Mini stats row */}
      {stats && (
        <div className="mb-4 flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-0 text-xs text-muted font-data">
          <span>Runs: {stats.totalRuns}</span>
          <span className="hidden sm:inline mx-2 text-border">|</span>
          <span>Avg cost: ${stats.avgCost.toFixed(2)}</span>
          <span className="hidden sm:inline mx-2 text-border">|</span>
          <span className={cn(
            stats.successRate >= 90 ? "text-success" :
            stats.successRate >= 70 ? "text-warning" :
            "text-error"
          )}>
            Success: {stats.successRate}%
          </span>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-1.5">
        <button
          onClick={onRun}
          className={cn(
            "flex items-center gap-1.5 rounded-lg bg-accent px-2.5 py-1.5 text-xs font-medium text-accent-foreground",
            "hover:bg-accent-hover transition-all duration-200 shadow-sm"
          )}
        >
          <Play className="h-3 w-3" />
          Run
        </button>
        {onBatch && (
          <button
            onClick={onBatch}
            className="flex items-center gap-1.5 rounded-lg border border-accent/50 bg-accent/10 px-2.5 py-1.5 text-xs font-medium text-accent hover:bg-accent/20 transition-colors"
          >
            <Layers className="h-3 w-3" />
            Batch
          </button>
        )}
        <button
          onClick={onViewDag}
          className="flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium text-muted hover:text-foreground hover:bg-border/40 transition-colors"
        >
          <Eye className="h-3 w-3" />
          DAG
        </button>
        <button
          onClick={onEdit}
          className="flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium text-muted hover:text-foreground hover:bg-border/40 transition-colors"
        >
          <Pencil className="h-3 w-3" />
          Edit
        </button>
        {onViewVersions && (
          <button
            onClick={onViewVersions}
            title="View versions"
            aria-label="View versions"
            className="flex items-center justify-center rounded-lg border border-border px-2 py-1.5 text-xs text-muted hover:text-foreground hover:bg-border/40 transition-colors"
          >
            <History aria-hidden="true" className="h-3 w-3" />
          </button>
        )}
      </div>
    </div>
  );
});
