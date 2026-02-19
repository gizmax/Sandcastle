import { useCallback, useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { toast } from "sonner";
import { api } from "@/api/client";
import { WorkflowBuilder } from "@/components/workflows/WorkflowBuilder";
import { RunWorkflowModal } from "@/components/workflows/RunWorkflowModal";
import jsYaml from "js-yaml";

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
      prompt?: string;
    }>;
    yaml_content?: string;
  };
}

interface InputSchema {
  properties: Record<string, { type: string; description?: string; default?: unknown }>;
  required?: string[];
}

export default function WorkflowBuilderPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const state = location.state as WorkflowState | null;

  const [runModal, setRunModal] = useState<{
    yaml: string;
    name: string;
    inputSchema?: InputSchema;
  } | null>(null);

  const handleSave = useCallback(
    async (yaml: string, name: string) => {
      const res = await api.post("/workflows", { name, content: yaml });
      if (res.error) {
        toast.error(`Save failed: ${res.error.message}`);
        return;
      }
      toast.success("Workflow saved");
      navigate("/workflows");
    },
    [navigate]
  );

  const runWorkflow = useCallback(
    async (yaml: string, input: Record<string, unknown>) => {
      const res = await api.post<{ run_id: string }>("/workflows/run", {
        workflow: yaml,
        input,
      });
      if (res.data?.run_id) {
        navigate(`/runs/${res.data.run_id}`);
      }
    },
    [navigate]
  );

  const handleRunClick = useCallback((yaml: string) => {
    // Parse YAML to extract input_schema and name
    try {
      const parsed = jsYaml.load(yaml) as Record<string, unknown> | null;
      const name = (parsed?.name as string) || "Workflow";
      const schema = parsed?.input_schema as InputSchema | undefined;

      if (schema?.properties && Object.keys(schema.properties).length > 0) {
        // Has input schema - show modal
        setRunModal({ yaml, name, inputSchema: schema });
      } else {
        // No schema - run directly
        void runWorkflow(yaml, {});
      }
    } catch {
      // YAML parse failed, run directly
      void runWorkflow(yaml, {});
    }
  }, [runWorkflow]);

  return (
    <div className="space-y-3 sm:space-y-4">
      <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">Workflow Builder</h1>
      <WorkflowBuilder
        onSave={handleSave}
        onRun={handleRunClick}
        initialWorkflow={state?.workflow}
      />

      {runModal && (
        <RunWorkflowModal
          open={true}
          workflowName={runModal.name}
          inputSchema={runModal.inputSchema}
          onClose={() => setRunModal(null)}
          onRun={(input) => {
            setRunModal(null);
            void runWorkflow(runModal.yaml, input);
          }}
        />
      )}
    </div>
  );
}
