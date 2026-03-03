import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { GitBranch, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { WorkflowList } from "@/components/workflows/WorkflowList";
import { RunWorkflowModal } from "@/components/workflows/RunWorkflowModal";
import { DagGraph } from "@/components/workflows/DagGraph";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";
import type { InputSchema } from "@/types/inputSchema";

interface WorkflowStep {
  id: string;
  model?: string;
  depends_on?: string[];
  prompt?: string;
}

interface WorkflowInfo {
  name: string;
  description: string;
  steps_count: number;
  file_name: string;
  steps?: WorkflowStep[];
  input_schema?: InputSchema;
  version?: number | null;
  version_status?: string | null;
  total_versions?: number | null;
  yaml_content?: string;
}

export default function Workflows() {
  const navigate = useNavigate();
  const [workflows, setWorkflows] = useState<WorkflowInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [runModal, setRunModal] = useState<WorkflowInfo | null>(null);
  const [dagWorkflow, setDagWorkflow] = useState<WorkflowInfo | null>(null);
  const [selectedNames, setSelectedNames] = useState<Set<string>>(new Set());
  const [bulkAction, setBulkAction] = useState<"delete" | null>(null);
  const [bulkProcessing, setBulkProcessing] = useState(false);

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
      const res = await api.post("/workflows/run", {
        workflow_name: runModal.file_name.replace(".yaml", ""),
        input,
        callback_url: callbackUrl,
      });
      setRunModal(null);
      if (res.error) {
        toast.error(`Run failed: ${res.error.message}`);
        return;
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
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner size="lg" />
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
      <div className="flex items-center justify-between">
        <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">Workflows</h1>
        <button
          onClick={() => navigate("/workflows/builder")}
          aria-label="New workflow"
          className={cn(
            "flex items-center gap-2 rounded-lg bg-accent px-3 sm:px-4 py-2 text-sm font-medium text-accent-foreground",
            "hover:bg-accent-hover transition-all duration-200 shadow-sm hover:shadow-md"
          )}
        >
          <Plus className="h-4 w-4" />
          <span className="hidden sm:inline">New Workflow</span>
        </button>
      </div>

      {/* Bulk actions bar */}
      {selectedNames.size > 0 && (
        <div className="flex items-center gap-3 rounded-lg border border-accent/30 bg-accent/5 px-4 py-2.5">
          <span className="text-sm font-medium text-foreground">
            {selectedNames.size} selected
          </span>
          <div className="flex items-center gap-2 ml-auto">
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
              onClick={() => setSelectedNames(new Set())}
              className="text-xs text-muted hover:text-foreground transition-colors ml-1"
            >
              Clear
            </button>
          </div>
        </div>
      )}

      {workflows.length === 0 ? (
        <EmptyState
          icon={GitBranch}
          title="No workflows found"
          description="No workflows found. Add YAML files to your workflows directory to get started."
          action={{ label: "Create Workflow", onClick: () => navigate("/workflows/builder") }}
        />
      ) : (
        <WorkflowList
          workflows={workflows}
          selectedNames={selectedNames}
          onSelectionChange={setSelectedNames}
          onRun={setRunModal}
          onEdit={(wf) => navigate("/workflows/builder", { state: { workflow: wf } })}
          onViewDag={setDagWorkflow}
          onViewVersions={(wf) => navigate(`/workflows/${wf.file_name.replace(".yaml", "")}`)}
        />
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

      {/* Delete confirmation */}
      <ConfirmDialog
        open={bulkAction === "delete"}
        title={`Delete ${selectedNames.size} workflow${selectedNames.size > 1 ? "s" : ""}?`}
        description="YAML files will be permanently removed from disk and all version records will be deleted."
        confirmLabel={bulkProcessing ? "Deleting..." : "Delete"}
        variant="danger"
        confirmDisabled={bulkProcessing}
        onConfirm={handleBulkDelete}
        onCancel={() => setBulkAction(null)}
      />
    </div>
  );
}
