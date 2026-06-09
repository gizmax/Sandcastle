import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { GitBranch, LayoutGrid, Network, Plus, Search, Star, Trash2, X, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { WorkflowList } from "@/components/workflows/WorkflowList";
import { WorkflowCard } from "@/components/workflows/WorkflowCard";
import { RunWorkflowModal } from "@/components/workflows/RunWorkflowModal";
import { BatchRunModal } from "@/components/workflows/BatchRunModal";
import { VersionHistoryModal } from "@/components/workflows/VersionHistoryModal";
import { DagGraph } from "@/components/workflows/DagGraph";
import { DependencyGraph } from "@/components/workflows/DependencyGraph";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { Skeleton } from "@/components/ui/Skeleton";
import { usePinnedWorkflows } from "@/hooks/usePinnedWorkflows";
import { cn, buttonMd, buttonSm, buttonPrimary, buttonDanger, iconMd, iconSm } from "@/lib/utils";
import type { InputSchema } from "@/types/inputSchema";

interface WorkflowInfo {
  name: string;
  description: string;
  steps_count: number;
  file_name: string;
  steps?: Array<{ id: string; owner?: string; [key: string]: unknown }>;
  input_schema?: InputSchema;
  version?: number | null;
  version_status?: string | null;
  total_versions?: number | null;
  yaml_content?: string;
  doctor_status?: "ok" | "warning" | "blocked" | null;
  doctor_risk?: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL" | null;
}

export default function Workflows() {
  const navigate = useNavigate();
  const [workflows, setWorkflows] = useState<WorkflowInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [runModal, setRunModal] = useState<WorkflowInfo | null>(null);
  const [batchModal, setBatchModal] = useState<WorkflowInfo | null>(null);
  const [dagWorkflow, setDagWorkflow] = useState<WorkflowInfo | null>(null);
  const [selectedNames, setSelectedNames] = useState<Set<string>>(new Set());
  const [bulkAction, setBulkAction] = useState<"delete" | null>(null);
  const [bulkProcessing, setBulkProcessing] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [viewMode, setViewMode] = useState<"grid" | "graph">("grid");
  const [versionHistoryWorkflow, setVersionHistoryWorkflow] = useState<string | null>(null);
  const { pinnedWorkflows, togglePin } = usePinnedWorkflows();

  const filteredWorkflows = useMemo(
    () => {
      if (!searchQuery) return workflows;
      const q = searchQuery.toLowerCase();
      return workflows.filter(
        (wf) =>
          wf.name.toLowerCase().includes(q) ||
          wf.file_name?.toLowerCase().includes(q)
      );
    },
    [workflows, searchQuery]
  );

  const pinnedWfs = useMemo(
    () => filteredWorkflows.filter((wf) => pinnedWorkflows.includes(wf.name)),
    [filteredWorkflows, pinnedWorkflows]
  );

  const fetchWorkflows = useCallback(async () => {
    try {
      setError(null);
      const res = await api.get<WorkflowInfo[]>("/workflows");
      if (res.data) setWorkflows(res.data);
    } catch {
      setError("Could not connect to the API server");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchWorkflows();
  }, [fetchWorkflows]);

  const handleRun = useCallback(
    async (input: Record<string, unknown>, callbackUrl?: string) => {
      if (!runModal) return;
      const res = await api.post<{ run_id: string }>("/workflows/run", {
        workflow_name: runModal.file_name.replace(".yaml", ""),
        input,
        callback_url: callbackUrl,
      });
      setRunModal(null);
      if (res.error) {
        toast.error(`Run failed: ${res.error.message}`);
        return;
      }
      const runId = res.data?.run_id;
      if (runId) {
        toast.success("Run started", {
          action: {
            label: "Mission Control",
            onClick: () => navigate(`/runs/${runId}/live`),
          },
        });
      }
      navigate("/runs");
    },
    [runModal, navigate]
  );

  const handleBulkDelete = useCallback(async () => {
    setBulkProcessing(true);
    const results = await Promise.allSettled(
      Array.from(selectedNames).map((name) => api.delete(`/workflows/${name}`))
    );
    const errors: string[] = [];
    let ok = 0;
    for (const r of results) {
      if (r.status === "fulfilled") {
        const resp = r.value as { error?: { message?: string } | null };
        if (resp.error) {
          errors.push(resp.error.message || "Unknown error");
        } else {
          ok++;
        }
      } else {
        errors.push(String(r.reason));
      }
    }
    const fail = results.length - ok;
    setBulkProcessing(false);
    setBulkAction(null);
    setSelectedNames(new Set());
    if (ok > 0) toast.success(`Deleted ${ok} workflow${ok > 1 ? "s" : ""}`);
    if (fail > 0) {
      const detail = errors[0] ? `: ${errors[0]}` : "";
      toast.error(`Failed to delete ${fail} workflow${fail > 1 ? "s" : ""}${detail}`);
    }
    setLoading(true);
    void fetchWorkflows();
  }, [selectedNames, fetchWorkflows]);

  if (loading) {
    return (
      <div className="space-y-4 sm:space-y-6" role="status" aria-label="Loading workflows">
        <div className="flex items-center justify-between">
          <Skeleton className="h-8 w-40" />
          <Skeleton className="h-9 w-36 rounded-lg" />
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="rounded-xl border border-border bg-surface p-4 space-y-3">
              <Skeleton className="h-5 w-3/4" />
              <Skeleton className="h-3 w-full" />
              <Skeleton className="h-3 w-2/3" />
              <div className="flex items-center gap-2 pt-2">
                <Skeleton className="h-7 w-16 rounded-lg" />
                <Skeleton className="h-7 w-16 rounded-lg" />
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <ErrorState
        title="Workflows"
        message={error}
        onRetry={() => { setLoading(true); void fetchWorkflows(); }}
      />
    );
  }

  return (
    <div className="space-y-4 sm:space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">Workflows</h1>
        <div className="flex flex-1 items-center gap-2 sm:flex-none">
          <div className="relative flex-1 sm:flex-none">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Filter workflows..."
              aria-label="Filter workflows by name"
              className={cn(
                "h-8 w-full sm:w-48 rounded-lg border border-border bg-background pl-8 pr-8 text-xs",
                "focus-visible:border-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
              )}
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery("")}
                aria-label="Clear search"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted hover:text-foreground transition-colors"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
          <div className="flex items-center rounded-lg border border-border bg-background p-0.5">
            <button
              type="button"
              onClick={() => setViewMode("grid")}
              aria-label="Grid view"
              className={cn(
                "flex items-center justify-center rounded-md px-2 py-1.5 text-xs transition-colors",
                viewMode === "grid"
                  ? "bg-accent text-accent-foreground"
                  : "text-muted hover:text-foreground"
              )}
            >
              <LayoutGrid className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={() => setViewMode("graph")}
              aria-label="Graph view"
              className={cn(
                "flex items-center justify-center rounded-md px-2 py-1.5 text-xs transition-colors",
                viewMode === "graph"
                  ? "bg-accent text-accent-foreground"
                  : "text-muted hover:text-foreground"
              )}
            >
              <Network className="h-3.5 w-3.5" />
            </button>
          </div>
          <button
            onClick={() => navigate("/workflows/builder")}
            aria-label="New workflow"
            className={cn(
              "flex items-center gap-2 font-medium",
              buttonMd, buttonPrimary,
              "shadow-sm hover:shadow-md"
            )}
          >
            <Plus className={iconMd} />
            <span className="hidden sm:inline">New Workflow</span>
          </button>
        </div>
      </div>
      {workflows.length > 0 && (
        <p className="text-xs text-muted" data-testid="workflow-count">
          {searchQuery
            ? `${filteredWorkflows.length} of ${workflows.length} workflow${workflows.length !== 1 ? "s" : ""}`
            : `${workflows.length} workflow${workflows.length !== 1 ? "s" : ""}`}
        </p>
      )}

      {/* Bulk actions bar */}
      {selectedNames.size > 0 && (
        <div className="flex flex-col gap-2 rounded-lg border border-accent/30 bg-accent/5 px-4 py-2.5 sm:flex-row sm:items-center sm:gap-3">
          <span className="text-sm font-medium text-foreground">
            {selectedNames.size} selected
          </span>
          <div className="flex flex-wrap items-center gap-2 sm:ml-auto">
            <button
              onClick={() => setBulkAction("delete")}
              className={cn(
                "flex items-center gap-1.5 font-medium",
                buttonSm, buttonDanger,
              )}
            >
              <Trash2 className={iconSm} />
              Delete selected
            </button>
            <button
              onClick={() => setSelectedNames(new Set())}
              className="text-xs text-muted hover:text-foreground transition-colors ml-1"
            >
              Clear
            </button>
          </div>
        </div>
      )}

      {viewMode === "graph" ? (
        workflows.length === 0 ? (
          <EmptyState
            icon={GitBranch}
            title="No workflows found"
            description="No workflows found. Add YAML files to your workflows directory to get started."
            action={{ label: "Create Workflow", onClick: () => navigate("/workflows/builder") }}
          />
        ) : (
          <DependencyGraph
            workflows={filteredWorkflows.map((wf) => ({
              name: wf.name,
              file_name: wf.file_name,
              steps_count: wf.steps_count,
              last_run_status: undefined,
            }))}
          />
        )
      ) : (
        <>
          {/* Pinned workflows section */}
          {pinnedWfs.length > 0 && workflows.length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-widest text-muted-foreground">
                <Star className="h-3 w-3 fill-current text-accent" />
                Pinned
              </div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {pinnedWfs.map((wf) => (
                  <WorkflowCard
                    key={`pinned-${wf.file_name}`}
                    name={wf.name}
                    description={wf.description}
                    stepsCount={wf.steps_count}
                    fileName={wf.file_name}
                    version={wf.version}
                    versionStatus={wf.version_status}
                    totalVersions={wf.total_versions}
                    pinned={true}
                    stats={undefined}
                    doctorStatus={wf.doctor_status}
                    doctorRisk={wf.doctor_risk}
                    onTogglePin={() => togglePin(wf.name)}
                    onRun={() => setRunModal(wf)}
                    onBatch={() => setBatchModal(wf)}
                    onEdit={() => navigate("/workflows/builder", { state: { workflow: wf } })}
                    onViewDag={() => setDagWorkflow(wf)}
                    onViewVersions={() => setVersionHistoryWorkflow(wf.file_name.replace(".yaml", ""))}
                  />
                ))}
              </div>
              <div className="border-b border-border" />
            </div>
          )}

          {workflows.length === 0 ? (
            <EmptyState
              icon={GitBranch}
              title="No workflows found"
              description="No workflows found. Add YAML files to your workflows directory to get started."
              action={{ label: "Create Workflow", onClick: () => navigate("/workflows/builder") }}
            />
          ) : filteredWorkflows.length === 0 ? (
            <EmptyState
              icon={Search}
              title="No matching workflows"
              description={`No workflows match "${searchQuery}".`}
              action={{ label: "Clear search", onClick: () => setSearchQuery("") }}
            />
          ) : (
            <WorkflowList
              workflows={filteredWorkflows}
              selectedNames={selectedNames}
              onSelectionChange={setSelectedNames}
              onRun={setRunModal}
              onBatch={setBatchModal}
              onEdit={(wf) => navigate("/workflows/builder", { state: { workflow: wf } })}
              onViewDag={setDagWorkflow}
              onViewVersions={(wf) => setVersionHistoryWorkflow(wf.file_name.replace(".yaml", ""))}
            />
          )}
        </>
      )}

      {/* DAG Viewer */}
      {dagWorkflow && (
        <div className="rounded-xl border border-border bg-surface shadow-sm overflow-hidden">
          <div className="flex items-center justify-between border-b border-border px-5 py-3">
            <h3 className="text-sm font-semibold text-foreground">
              DAG - {dagWorkflow.name}
            </h3>
            <button
              onClick={() => setDagWorkflow(null)}
              className="text-xs text-muted hover:text-foreground"
            >
              Close
            </button>
          </div>
          <DagGraph
            steps={dagWorkflow.steps || Array.from({ length: dagWorkflow.steps_count }, (_, i) => ({
              id: `step_${i + 1}`,
            }))}
          />
        </div>
      )}

      {/* Run Modal */}
      {runModal && (
        <RunWorkflowModal
          open={true}
          workflowName={runModal.name}
          inputSchema={runModal.input_schema}
          onClose={() => setRunModal(null)}
          onRun={handleRun}
        />
      )}

      {/* Batch Run Modal */}
      {batchModal && (
        <BatchRunModal
          open={true}
          workflowName={batchModal.name}
          fileName={batchModal.file_name}
          onClose={() => setBatchModal(null)}
        />
      )}

      {/* Version History Modal */}
      {versionHistoryWorkflow && (
        <VersionHistoryModal
          open={true}
          onClose={() => setVersionHistoryWorkflow(null)}
          workflowName={versionHistoryWorkflow}
        />
      )}

      {/* Delete confirmation */}
      <ConfirmDialog
        open={bulkAction === "delete"}
        title={`Delete ${selectedNames.size} workflow${selectedNames.size > 1 ? "s" : ""}?`}
        description="YAML files will be permanently removed from disk and all version records will be deleted."
        confirmLabel={bulkProcessing ? <><Loader2 className="inline h-3.5 w-3.5 animate-spin mr-1.5" />Deleting...</> : "Delete"}
        variant="danger"
        confirmDisabled={bulkProcessing}
        onConfirm={handleBulkDelete}
        onCancel={() => setBulkAction(null)}
      />
    </div>
  );
}
