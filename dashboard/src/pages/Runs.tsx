import { useState } from "react";
import { PlayCircle } from "lucide-react";
import { useRuns } from "@/hooks/useRuns";
import { RunsTable } from "@/components/runs/RunsTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";

const STATUS_OPTIONS = ["all", "queued", "running", "completed", "failed", "partial"];

export default function Runs() {
  const [statusFilter, setStatusFilter] = useState("all");
  const [offset, setOffset] = useState(0);
  const limit = 20;

  const { runs, total, loading } = useRuns({
    status: statusFilter === "all" ? undefined : statusFilter,
    limit,
    offset,
  });

  return (
    <div className="space-y-4 sm:space-y-6">
      <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">Runs</h1>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        {STATUS_OPTIONS.map((s) => (
          <button
            key={s}
            onClick={() => {
              setStatusFilter(s);
              setOffset(0);
            }}
            className={cn(
              "rounded-lg px-3 py-1.5 text-xs font-medium capitalize transition-all duration-200",
              statusFilter === s
                ? "bg-accent text-accent-foreground shadow-sm"
                : "bg-border/40 text-muted hover:bg-border hover:text-foreground"
            )}
          >
            {s}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="flex h-40 items-center justify-center">
          <LoadingSpinner />
        </div>
      ) : runs.length === 0 ? (
        <EmptyState
          icon={PlayCircle}
          title={statusFilter !== "all" ? "No runs match your filters" : "No runs yet"}
          description={
            statusFilter !== "all"
              ? "Try changing the status filter."
              : "Run a workflow to see execution history here."
          }
          action={
            statusFilter !== "all"
              ? { label: "Reset filters", onClick: () => setStatusFilter("all") }
              : undefined
          }
        />
      ) : (
        <RunsTable
          runs={runs}
          total={total}
          limit={limit}
          offset={offset}
          onPageChange={setOffset}
        />
      )}
    </div>
  );
}
