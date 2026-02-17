import { useState, useCallback, useEffect, useMemo } from "react";
import { PlayCircle, Trash2, XCircle, Search } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { useRuns } from "@/hooks/useRuns";
import { RunsTable } from "@/components/runs/RunsTable";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";

const STATUS_OPTIONS = ["all", "queued", "running", "completed", "failed", "partial"];

interface WorkflowItem {
  name: string;
}

export default function Runs() {
  const [statusFilter, setStatusFilter] = useState("all");
  const [workflowFilter, setWorkflowFilter] = useState("");
  const [searchTerm, setSearchTerm] = useState("");
  const [offset, setOffset] = useState(0);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkAction, setBulkAction] = useState<"delete" | "cancel" | null>(null);
  const [bulkProcessing, setBulkProcessing] = useState(false);
  const [workflows, setWorkflows] = useState<WorkflowItem[]>([]);
  const limit = 20;

  useEffect(() => {
    api.get<WorkflowItem[]>("/workflows").then((res) => {
      if (res.data) setWorkflows(res.data);
    });
  }, []);

  const { runs, total, loading, refetch } = useRuns({
    status: statusFilter === "all" ? undefined : statusFilter,
    workflow: workflowFilter || undefined,
    limit,
    offset,
  });

  const filteredRuns = useMemo(() => {
    if (!searchTerm) return runs;
    const q = searchTerm.toLowerCase();
    return runs.filter(
      (r) =>
        r.run_id.toLowerCase().includes(q) ||
        r.workflow_name.toLowerCase().includes(q)
    );
  }, [runs, searchTerm]);

  const handleBulkDelete = useCallback(async () => {
    setBulkProcessing(true);
    let ok = 0;
    let fail = 0;
    for (const id of selectedIds) {
      const res = await api.delete(`/runs/${id}`);
      if (res.error) fail++;
      else ok++;
    }
    setBulkProcessing(false);
    setBulkAction(null);
    setSelectedIds(new Set());
    if (ok > 0) toast.success(`Deleted ${ok} run${ok > 1 ? "s" : ""}`);
    if (fail > 0) toast.error(`Failed to delete ${fail} run${fail > 1 ? "s" : ""}`);
    void refetch();
  }, [selectedIds, refetch]);

  const handleBulkCancel = useCallback(async () => {
    setBulkProcessing(true);
    let ok = 0;
    let fail = 0;
    for (const id of selectedIds) {
      const res = await api.post(`/runs/${id}/cancel`);
      if (res.error) fail++;
      else ok++;
    }
    setBulkProcessing(false);
    setBulkAction(null);
    setSelectedIds(new Set());
    if (ok > 0) toast.success(`Cancelled ${ok} run${ok > 1 ? "s" : ""}`);
    if (fail > 0) toast.error(`Failed to cancel ${fail} run${fail > 1 ? "s" : ""}`);
    void refetch();
  }, [selectedIds, refetch]);

  return (
    <div className="space-y-4 sm:space-y-6">
      <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">Runs</h1>

      {/* Filters */}
      <div className="space-y-3">
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
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted" />
            <input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="Search by ID or name..."
              className={cn(
                "h-8 w-48 rounded-lg border border-border bg-background pl-8 pr-3 text-xs",
                "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30"
              )}
            />
          </div>
          <select
            value={workflowFilter}
            onChange={(e) => {
              setWorkflowFilter(e.target.value);
              setOffset(0);
            }}
            className={cn(
              "h-8 rounded-lg border border-border bg-background px-2.5 text-xs text-foreground",
              "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30"
            )}
          >
            <option value="">All workflows</option>
            {workflows.map((w) => (
              <option key={w.name} value={w.name}>
                {w.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Bulk actions bar */}
      {selectedIds.size > 0 && (
        <div className="flex items-center gap-3 rounded-lg border border-accent/30 bg-accent/5 px-4 py-2.5">
          <span className="text-sm font-medium text-foreground">
            {selectedIds.size} selected
          </span>
          <div className="flex items-center gap-2 ml-auto">
            <button
              onClick={() => setBulkAction("cancel")}
              className={cn(
                "flex items-center gap-1.5 rounded-lg border border-warning/30 px-3 py-1.5",
                "text-xs font-medium text-warning",
                "hover:bg-warning/10 transition-colors"
              )}
            >
              <XCircle className="h-3.5 w-3.5" />
              Cancel selected
            </button>
            <button
              onClick={() => setBulkAction("delete")}
              className={cn(
                "flex items-center gap-1.5 rounded-lg border border-error/30 px-3 py-1.5",
                "text-xs font-medium text-error",
                "hover:bg-error/10 transition-colors"
              )}
            >
              <Trash2 className="h-3.5 w-3.5" />
              Delete selected
            </button>
            <button
              onClick={() => setSelectedIds(new Set())}
              className="text-xs text-muted hover:text-foreground transition-colors ml-1"
            >
              Clear
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="flex h-40 items-center justify-center">
          <LoadingSpinner />
        </div>
      ) : filteredRuns.length === 0 ? (
        <EmptyState
          icon={PlayCircle}
          title={statusFilter !== "all" || workflowFilter || searchTerm ? "No runs match your filters" : "No runs yet"}
          description={
            statusFilter !== "all" || workflowFilter || searchTerm
              ? "Try adjusting your filters."
              : "Run a workflow to see execution history here."
          }
          action={
            statusFilter !== "all" || workflowFilter || searchTerm
              ? {
                  label: "Reset filters",
                  onClick: () => {
                    setStatusFilter("all");
                    setWorkflowFilter("");
                    setSearchTerm("");
                  },
                }
              : undefined
          }
        />
      ) : (
        <RunsTable
          runs={filteredRuns}
          total={total}
          limit={limit}
          offset={offset}
          onPageChange={setOffset}
          selectedIds={selectedIds}
          onSelectionChange={setSelectedIds}
        />
      )}

      {/* Bulk confirm dialogs */}
      <ConfirmDialog
        open={bulkAction === "delete"}
        title="Delete Runs"
        description={`Delete ${selectedIds.size} selected run${selectedIds.size > 1 ? "s" : ""}? This cannot be undone.`}
        confirmLabel={bulkProcessing ? "Deleting..." : "Delete All"}
        variant="danger"
        onConfirm={handleBulkDelete}
        onCancel={() => setBulkAction(null)}
      />
      <ConfirmDialog
        open={bulkAction === "cancel"}
        title="Cancel Runs"
        description={`Cancel ${selectedIds.size} selected run${selectedIds.size > 1 ? "s" : ""}?`}
        confirmLabel={bulkProcessing ? "Cancelling..." : "Cancel All"}
        variant="warning"
        onConfirm={handleBulkCancel}
        onCancel={() => setBulkAction(null)}
      />
    </div>
  );
}
