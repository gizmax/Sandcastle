import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { GitBranch, Plus } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { WorkflowList } from "@/components/workflows/WorkflowList";
import { RunWorkflowModal } from "@/components/workflows/RunWorkflowModal";
import { DagGraph } from "@/components/workflows/DagGraph";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";

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
}

export default function Workflows() {
  const navigate = useNavigate();
  const [workflows, setWorkflows] = useState<WorkflowInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [runModal, setRunModal] = useState<WorkflowInfo | null>(null);
  const [dagWorkflow, setDagWorkflow] = useState<WorkflowInfo | null>(null);

  const fetchWorkflows = useCallback(async () => {
    const res = await api.get<WorkflowInfo[]>("/workflows");
    if (res.data) setWorkflows(res.data);
    setLoading(false);
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

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  return (
    <div className="space-y-4 sm:space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">Workflows</h1>
        <button
          onClick={() => navigate("/workflows/builder")}
          className={cn(
            "flex items-center gap-2 rounded-lg bg-accent px-3 sm:px-4 py-2 text-sm font-medium text-accent-foreground",
            "hover:bg-accent-hover transition-all duration-200 shadow-sm hover:shadow-md"
          )}
        >
          <Plus className="h-4 w-4" />
          <span className="hidden sm:inline">New Workflow</span>
        </button>
      </div>

      {workflows.length === 0 ? (
        <EmptyState
          icon={GitBranch}
          title="No workflows found"
          description="Build your first workflow!"
          action={{ label: "Create Workflow", onClick: () => navigate("/workflows/builder") }}
        />
      ) : (
        <WorkflowList
          workflows={workflows}
          onRun={setRunModal}
          onEdit={(wf) => navigate("/workflows/builder", { state: { workflow: wf } })}
          onViewDag={setDagWorkflow}
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
          onClose={() => setRunModal(null)}
          onRun={handleRun}
        />
      )}
    </div>
  );
}
