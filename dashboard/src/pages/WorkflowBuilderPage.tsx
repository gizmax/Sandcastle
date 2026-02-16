import { useCallback } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { api } from "@/api/client";
import { WorkflowBuilder } from "@/components/workflows/WorkflowBuilder";

interface WorkflowState {
  workflow?: {
    name: string;
    description: string;
    steps_count: number;
    file_name: string;
    steps?: Array<{
      id: string;
      model?: string;
      depends_on?: string[];
    }>;
  };
}

export default function WorkflowBuilderPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const state = location.state as WorkflowState | null;

  const handleSave = useCallback(
    async (yaml: string, name: string) => {
      await api.post("/workflows", { name, content: yaml });
      navigate("/workflows");
    },
    [navigate]
  );

  const handleRun = useCallback(
    async (yaml: string) => {
      const res = await api.post<{ run_id: string }>("/workflows/run", {
        workflow: yaml,
        input: {},
      });
      if (res.data?.run_id) {
        navigate(`/runs/${res.data.run_id}`);
      }
    },
    [navigate]
  );

  return (
    <div className="space-y-3 sm:space-y-4">
      <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">Workflow Builder</h1>
      <WorkflowBuilder
        onSave={handleSave}
        onRun={handleRun}
        initialWorkflow={state?.workflow}
      />
    </div>
  );
}
