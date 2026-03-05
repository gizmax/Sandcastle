import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { PlayCircle, Trash2, XCircle, Search, AlertTriangle, BookmarkPlus, X, Bookmark, Zap, Download } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { useRuns } from "@/hooks/useRuns";
import { useSavedFilters, type SavedFilterCriteria } from "@/hooks/useSavedFilters";
import { RunsTable } from "@/components/runs/RunsTable";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { EmptyState } from "@/components/shared/EmptyState";
import { ContextBanner } from "@/components/shared/ContextBanner";
import { Skeleton } from "@/components/ui/Skeleton";
import { cn, formatCost, formatDuration, parseUTC } from "@/lib/utils";
import { exportToCsv } from "@/lib/exportCsv";

const STATUS_OPTIONS = ["all", "queued", "running", "completed", "failed", "partial"];

interface WorkflowItem {
  name: string;
}

export default function Runs() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [statusFilter, setStatusFilter] = useState(() => searchParams.get("status") || "all");
  const [workflowFilter, setWorkflowFilter] = useState(() => searchParams.get("workflow") || "");
  const [searchTerm, setSearchTerm] = useState(() => searchParams.get("search") || "");
  const [offset, setOffset] = useState(0);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkAction, setBulkAction] = useState<"delete" | "cancel" | null>(null);
  const [bulkProcessing, setBulkProcessing] = useState(false);
  const [workflows, setWorkflows] = useState<WorkflowItem[]>([]);
  const [successRate, setSuccessRate] = useState<number | null>(null);
  const [showSavePopover, setShowSavePopover] = useState(false);
  const [filterName, setFilterName] = useState("");
  const [activeFilterId, setActiveFilterId] = useState<string | null>(null);
  const [filterToDelete, setFilterToDelete] = useState<string | null>(null);
  const savePopoverRef = useRef<HTMLDivElement>(null);
  const limit = 20;

  const { savedFilters, saveCurrentFilter, deleteFilter, getFilter } = useSavedFilters();

  useEffect(() => {
    const params = new URLSearchParams();
    if (statusFilter && statusFilter !== "all") params.set("status", statusFilter);
    if (searchTerm) params.set("search", searchTerm);
    if (workflowFilter) params.set("workflow", workflowFilter);
    setSearchParams(params, { replace: true });
  }, [statusFilter, searchTerm, workflowFilter, setSearchParams]);

  useEffect(() => {
    let cancelled = false;
    api.get<WorkflowItem[]>("/workflows").then((res) => {
      if (!cancelled && res.data) setWorkflows(res.data);
    });
    api.get<{ success_rate: number }>("/stats").then((res) => {
      if (!cancelled && res.data) setSuccessRate(res.data.success_rate);
    });
    return () => { cancelled = true; };
  }, []);

  const hasActiveFilter = statusFilter !== "all" || searchTerm !== "" || workflowFilter !== "";

  const currentFilterCriteria: SavedFilterCriteria = useMemo(() => ({
    ...(statusFilter !== "all" ? { status: statusFilter } : {}),
    ...(searchTerm ? { search: searchTerm } : {}),
    ...(workflowFilter ? { workflow: workflowFilter } : {}),
  }), [statusFilter, searchTerm, workflowFilter]);

  const applyFilter = useCallback((id: string) => {
    const filter = getFilter(id);
    if (!filter) return;
    setStatusFilter(filter.filters.status ?? "all");
    setSearchTerm(filter.filters.search ?? "");
    setWorkflowFilter(filter.filters.workflow ?? "");
    setActiveFilterId(id);
    setOffset(0);
    setSelectedIds(new Set());
  }, [getFilter]);

  const applyQuickFilter = useCallback((criteria: SavedFilterCriteria) => {
    setStatusFilter(criteria.status ?? "all");
    setSearchTerm(criteria.search ?? "");
    setWorkflowFilter(criteria.workflow ?? "");
    setActiveFilterId(null);
    setOffset(0);
    setSelectedIds(new Set());
  }, []);

  const handleSaveFilter = useCallback(() => {
    const result = saveCurrentFilter(filterName, currentFilterCriteria);
    if (result) {
      toast.success("Filter saved");
      setActiveFilterId(result.id);
      setShowSavePopover(false);
      setFilterName("");
    }
  }, [filterName, currentFilterCriteria, saveCurrentFilter]);

  const handleDeleteFilter = useCallback((id: string) => {
    deleteFilter(id);
    if (activeFilterId === id) setActiveFilterId(null);
    setFilterToDelete(null);
    toast.success("Filter deleted");
  }, [deleteFilter, activeFilterId]);

  // Close popover on outside click
  useEffect(() => {
    if (!showSavePopover) return;
    function handleClick(e: MouseEvent) {
      if (savePopoverRef.current && !savePopoverRef.current.contains(e.target as Node)) {
        setShowSavePopover(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [showSavePopover]);

  // Clear active filter ID when filters change manually
  useEffect(() => {
    if (!activeFilterId) return;
    const filter = getFilter(activeFilterId);
    if (!filter) { setActiveFilterId(null); return; }
    const matches =
      (filter.filters.status ?? "all") === statusFilter &&
      (filter.filters.search ?? "") === searchTerm &&
      (filter.filters.workflow ?? "") === workflowFilter;
    if (!matches) setActiveFilterId(null);
  }, [statusFilter, searchTerm, workflowFilter, activeFilterId, getFilter]);

  const { runs, total, loading, error: runsError, refetch } = useRuns({
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
    const results = await Promise.allSettled(
      Array.from(selectedIds).map((id) => api.delete(`/runs/${id}`))
    );
    const ok = results.filter((r) => r.status === "fulfilled" && !(r.value as { error?: unknown }).error).length;
    const fail = results.length - ok;
    setBulkProcessing(false);
    setBulkAction(null);
    setSelectedIds(new Set());
    if (ok > 0) toast.success(`Deleted ${ok} run${ok > 1 ? "s" : ""}`);
    if (fail > 0) toast.error(`Failed to delete ${fail} run${fail > 1 ? "s" : ""}`);
    void refetch();
  }, [selectedIds, refetch]);

  const handleBulkCancel = useCallback(async () => {
    setBulkProcessing(true);
    const results = await Promise.allSettled(
      Array.from(selectedIds).map((id) => api.post(`/runs/${id}/cancel`))
    );
    const ok = results.filter((r) => r.status === "fulfilled" && !(r.value as { error?: unknown }).error).length;
    const fail = results.length - ok;
    setBulkProcessing(false);
    setBulkAction(null);
    setSelectedIds(new Set());
    if (ok > 0) toast.success(`Cancelled ${ok} run${ok > 1 ? "s" : ""}`);
    if (fail > 0) toast.error(`Failed to cancel ${fail} run${fail > 1 ? "s" : ""}`);
    void refetch();
  }, [selectedIds, refetch]);

  const handleExportCsv = useCallback(() => {
    const headers = ["Run ID", "Workflow", "Status", "Started At", "Duration", "Cost"];
    const rows = filteredRuns.map((r) => {
      const startedAt = r.started_at ?? "";
      let duration = "";
      if (r.started_at) {
        const start = parseUTC(r.started_at).getTime();
        const end = r.completed_at ? parseUTC(r.completed_at).getTime() : Date.now();
        duration = formatDuration((end - start) / 1000);
      }
      return [
        r.run_id,
        r.workflow_name,
        r.status,
        startedAt,
        duration,
        formatCost(r.total_cost_usd),
      ];
    });
    exportToCsv("runs.csv", headers, rows);
  }, [filteredRuns]);

  return (
    <div className="space-y-4 sm:space-y-6">
      <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">Runs</h1>

      {/* Failure rate banner */}
      {successRate != null && successRate < 0.9 && (
        <ContextBanner variant={successRate < 0.5 ? "error" : "warning"} icon={AlertTriangle}>
          {Math.round(successRate * 100)}% success rate today - filter by "failed" to investigate.
        </ContextBanner>
      )}

      {/* Filters */}
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          {STATUS_OPTIONS.map((s) => (
            <button
              key={s}
              onClick={() => {
                setStatusFilter(s);
                setOffset(0);
                setSelectedIds(new Set());
              }}
              aria-pressed={statusFilter === s}
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
              aria-label="Search runs by ID or name"
              className={cn(
                "h-8 w-full sm:w-48 rounded-lg border border-border bg-background pl-8 pr-3 text-xs",
                "focus-visible:border-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
              )}
            />
          </div>
          <select
            value={workflowFilter}
            onChange={(e) => {
              setWorkflowFilter(e.target.value);
              setOffset(0);
              setSelectedIds(new Set());
            }}
            aria-label="Filter by workflow"
            className={cn(
              "h-8 rounded-lg border border-border bg-background px-2.5 text-xs text-foreground",
              "focus-visible:border-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
            )}
          >
            <option value="">All workflows</option>
            {workflows.map((w) => (
              <option key={w.name} value={w.name}>
                {w.name}
              </option>
            ))}
          </select>

          <button
            onClick={handleExportCsv}
            disabled={filteredRuns.length === 0}
            aria-label="Export runs to CSV"
            className={cn(
              "flex h-8 items-center gap-1.5 rounded-lg border border-border px-3 text-xs font-medium",
              "text-muted hover:text-foreground hover:border-accent/40 transition-colors",
              "disabled:opacity-40 disabled:cursor-not-allowed"
            )}
          >
            <Download className="h-3.5 w-3.5" />
            Export
          </button>

          {/* Save filter button */}
          {hasActiveFilter && (
            <div className="relative" ref={savePopoverRef}>
              <button
                onClick={() => setShowSavePopover((p) => !p)}
                aria-label="Save current filter"
                className={cn(
                  "flex h-8 items-center gap-1.5 rounded-lg border border-border px-2.5 text-xs font-medium",
                  "text-muted hover:text-foreground hover:border-accent/40 transition-colors",
                  showSavePopover && "border-accent/40 text-accent"
                )}
              >
                <BookmarkPlus className="h-3.5 w-3.5" />
                Save
              </button>
              {showSavePopover && (
                <div
                  className={cn(
                    "absolute left-0 top-full z-50 mt-2 w-64 rounded-lg border border-border",
                    "bg-surface shadow-lg p-3 space-y-2"
                  )}
                >
                  <label className="text-xs font-medium text-foreground">Filter name</label>
                  <input
                    type="text"
                    value={filterName}
                    onChange={(e) => setFilterName(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && filterName.trim()) handleSaveFilter(); }}
                    placeholder="e.g. Failed production runs"
                    autoFocus
                    className={cn(
                      "h-8 w-full rounded-lg border border-border bg-background px-3 text-xs",
                      "focus-visible:border-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
                    )}
                  />
                  <div className="flex items-center gap-2">
                    <button
                      onClick={handleSaveFilter}
                      disabled={!filterName.trim()}
                      className={cn(
                        "flex-1 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-accent-foreground",
                        "hover:bg-accent/90 transition-colors",
                        "disabled:opacity-40 disabled:cursor-not-allowed"
                      )}
                    >
                      Save
                    </button>
                    <button
                      onClick={() => { setShowSavePopover(false); setFilterName(""); }}
                      className="text-xs text-muted hover:text-foreground transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                  {savedFilters.length >= 10 && (
                    <p className="text-[10px] text-muted">Max 10 filters. Oldest will be removed.</p>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Saved filters pills */}
        {savedFilters.length > 0 ? (
          <div className="flex flex-wrap items-center gap-2">
            <Bookmark className="h-3.5 w-3.5 text-muted shrink-0" />
            {savedFilters.map((sf) => {
              const isActive = activeFilterId === sf.id;
              const details = [
                sf.filters.status && `status: ${sf.filters.status}`,
                sf.filters.search && `search: "${sf.filters.search}"`,
                sf.filters.workflow && `workflow: ${sf.filters.workflow}`,
              ].filter(Boolean).join(", ");
              return (
                <span
                  key={sf.id}
                  className={cn(
                    "group relative inline-flex items-center gap-1 rounded-full px-3 py-1 text-xs",
                    "border cursor-pointer transition-all duration-200",
                    isActive
                      ? "bg-accent/15 border-accent text-accent glow-accent"
                      : "bg-surface border-border text-foreground hover:border-accent/30 hover:bg-accent/5"
                  )}
                  title={details}
                  onClick={() => applyFilter(sf.id)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => { if (e.key === "Enter") applyFilter(sf.id); }}
                >
                  {sf.name}
                  <button
                    onClick={(e) => { e.stopPropagation(); setFilterToDelete(sf.id); }}
                    aria-label={`Delete filter "${sf.name}"`}
                    className={cn(
                      "ml-0.5 rounded-full p-0.5 transition-colors",
                      "text-muted hover:text-error hover:bg-error/10",
                      "opacity-0 group-hover:opacity-100"
                    )}
                  >
                    <X className="h-3 w-3" />
                  </button>
                </span>
              );
            })}
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <Zap className="h-3.5 w-3.5 text-muted shrink-0" />
            <span className="text-xs text-muted mr-1">Quick filters:</span>
            <button
              onClick={() => applyQuickFilter({ status: "failed" })}
              className={cn(
                "rounded-full border border-border bg-surface px-3 py-1 text-xs",
                "text-foreground hover:border-accent/30 hover:bg-accent/5 transition-all duration-200"
              )}
            >
              Failed Today
            </button>
            <button
              onClick={() => applyQuickFilter({ status: "running" })}
              className={cn(
                "rounded-full border border-border bg-surface px-3 py-1 text-xs",
                "text-foreground hover:border-accent/30 hover:bg-accent/5 transition-all duration-200"
              )}
            >
              Running Now
            </button>
            <button
              onClick={() => applyQuickFilter({ status: "completed" })}
              className={cn(
                "rounded-full border border-border bg-surface px-3 py-1 text-xs",
                "text-foreground hover:border-accent/30 hover:bg-accent/5 transition-all duration-200"
              )}
            >
              Expensive Runs
            </button>
          </div>
        )}
      </div>

      {/* Bulk actions bar */}
      {selectedIds.size > 0 && (
        <div className="flex flex-col gap-2 rounded-lg border border-accent/30 bg-accent/5 px-4 py-2.5 sm:flex-row sm:items-center sm:gap-3">
          <span className="text-sm font-medium text-foreground">
            {selectedIds.size} selected
          </span>
          <div className="flex flex-wrap items-center gap-2 sm:ml-auto">
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
        <div className="space-y-3">
          <div className="flex items-center gap-3 rounded-lg border border-border bg-surface px-4 py-3">
            <Skeleton className="h-4 w-16" />
            <Skeleton className="h-4 w-24" />
            <Skeleton className="ml-auto h-4 w-20" />
          </div>
          {Array.from({ length: 7 }).map((_, i) => (
            <div key={i} className="flex items-center gap-4 rounded-lg border border-border bg-surface px-4 py-3">
              <Skeleton className="h-4 w-4 rounded" />
              <Skeleton className="h-4 w-48" />
              <Skeleton className="h-5 w-16 rounded-full" />
              <Skeleton className="ml-auto h-4 w-20" />
              <Skeleton className="h-4 w-16" />
            </div>
          ))}
        </div>
      ) : runsError ? (
        <div className="rounded-xl border border-error/30 bg-error/5 p-4">
          <p className="text-sm text-error">{runsError}</p>
          <button
            onClick={() => void refetch()}
            className="mt-2 text-xs font-medium text-accent hover:text-accent/80 transition-colors"
          >
            Retry
          </button>
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
          total={searchTerm ? filteredRuns.length : total}
          limit={limit}
          offset={searchTerm ? 0 : offset}
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
        confirmDisabled={bulkProcessing}
        variant="danger"
        onConfirm={handleBulkDelete}
        onCancel={() => setBulkAction(null)}
      />
      <ConfirmDialog
        open={bulkAction === "cancel"}
        title="Cancel Runs"
        description={`Cancel ${selectedIds.size} selected run${selectedIds.size > 1 ? "s" : ""}?`}
        confirmLabel={bulkProcessing ? "Cancelling..." : "Cancel All"}
        confirmDisabled={bulkProcessing}
        variant="warning"
        onConfirm={handleBulkCancel}
        onCancel={() => setBulkAction(null)}
      />
      <ConfirmDialog
        open={filterToDelete !== null}
        title="Delete Saved Filter"
        description="Remove this saved filter? This cannot be undone."
        confirmLabel="Delete"
        variant="danger"
        onConfirm={() => { if (filterToDelete) handleDeleteFilter(filterToDelete); }}
        onCancel={() => setFilterToDelete(null)}
      />
    </div>
  );
}
