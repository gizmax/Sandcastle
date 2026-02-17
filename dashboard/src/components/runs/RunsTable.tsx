import { useNavigate } from "react-router-dom";
import { RunStatusBadge } from "@/components/runs/RunStatusBadge";
import { formatRelativeTime, formatDuration, formatCost, parseUTC, cn } from "@/lib/utils";

interface RunItem {
  run_id: string;
  workflow_name: string;
  status: string;
  total_cost_usd: number;
  started_at: string | null;
  completed_at: string | null;
}

interface RunsTableProps {
  runs: RunItem[];
  total: number;
  limit: number;
  offset: number;
  onPageChange: (offset: number) => void;
  selectedIds?: Set<string>;
  onSelectionChange?: (ids: Set<string>) => void;
}

export function RunsTable({ runs, total, limit, offset, onPageChange, selectedIds, onSelectionChange }: RunsTableProps) {
  const navigate = useNavigate();
  const selectable = !!onSelectionChange;
  const allSelected = selectable && runs.length > 0 && runs.every((r) => selectedIds?.has(r.run_id));
  const totalPages = Math.ceil(total / limit);
  const currentPage = Math.floor(offset / limit) + 1;
  function getDuration(run: RunItem): string {
    if (!run.started_at) return "-";
    const start = parseUTC(run.started_at).getTime();
    const end = run.completed_at ? parseUTC(run.completed_at).getTime() : Date.now();
    return formatDuration((end - start) / 1000);
  }

  return (
    <div className="rounded-xl border border-border bg-surface shadow-sm overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-background/50">
              {selectable && (
                <th className="w-10 px-3 py-3">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={() => {
                      if (allSelected) {
                        onSelectionChange?.(new Set());
                      } else {
                        onSelectionChange?.(new Set(runs.map((r) => r.run_id)));
                      }
                    }}
                    className="h-4 w-4 rounded border-border accent-accent"
                  />
                </th>
              )}
              <th className="hidden sm:table-cell px-3 sm:px-5 py-3 text-left font-medium text-muted">Workflow</th>
              <th className="px-3 sm:px-5 py-3 text-left font-medium text-muted">Status</th>
              <th className="px-3 sm:px-5 py-3 text-left font-medium text-muted">Started</th>
              <th className="hidden md:table-cell px-3 sm:px-5 py-3 text-left font-medium text-muted">Duration</th>
              <th className="px-3 sm:px-5 py-3 text-right font-medium text-muted">Cost</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {runs.map((run) => (
              <tr
                key={run.run_id}
                onClick={() => navigate(`/runs/${run.run_id}`)}
                className={cn(
                  "cursor-pointer transition-colors duration-150 hover:bg-border/20",
                  selectedIds?.has(run.run_id) && "bg-accent/5"
                )}
              >
                {selectable && (
                  <td className="w-10 px-3 py-3" onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selectedIds?.has(run.run_id) ?? false}
                      onChange={() => {
                        const next = new Set(selectedIds);
                        if (next.has(run.run_id)) {
                          next.delete(run.run_id);
                        } else {
                          next.add(run.run_id);
                        }
                        onSelectionChange?.(next);
                      }}
                      className="h-4 w-4 rounded border-border accent-accent"
                    />
                  </td>
                )}
                <td className="hidden sm:table-cell px-3 sm:px-5 py-3 font-medium text-foreground">{run.workflow_name}</td>
                <td className="px-3 sm:px-5 py-3">
                  <RunStatusBadge status={run.status} />
                </td>
                <td className="px-3 sm:px-5 py-3 text-muted">
                  {run.started_at ? formatRelativeTime(run.started_at) : "queued"}
                </td>
                <td className="hidden md:table-cell px-3 sm:px-5 py-3 text-muted">{getDuration(run)}</td>
                <td className="px-3 sm:px-5 py-3 text-right font-mono text-muted">
                  {formatCost(run.total_cost_usd)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between border-t border-border px-3 sm:px-5 py-3">
          <p className="text-xs text-muted">
            Showing {offset + 1}-{Math.min(offset + limit, total)} of {total}
          </p>
          <div className="flex items-center gap-1">
            <button
              disabled={currentPage <= 1}
              onClick={() => onPageChange(Math.max(0, offset - limit))}
              className={cn(
                "rounded-md px-3 py-1 text-xs font-medium",
                "transition-colors duration-150",
                currentPage <= 1
                  ? "text-muted-foreground/40 cursor-not-allowed"
                  : "text-muted hover:bg-border/40 hover:text-foreground"
              )}
            >
              Previous
            </button>
            <span className="px-2 text-xs text-muted">
              {currentPage} / {totalPages}
            </span>
            <button
              disabled={currentPage >= totalPages}
              onClick={() => onPageChange(offset + limit)}
              className={cn(
                "rounded-md px-3 py-1 text-xs font-medium",
                "transition-colors duration-150",
                currentPage >= totalPages
                  ? "text-muted-foreground/40 cursor-not-allowed"
                  : "text-muted hover:bg-border/40 hover:text-foreground"
              )}
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
