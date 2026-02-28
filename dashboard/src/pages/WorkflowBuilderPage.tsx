import { useCallback, useMemo, useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { toast } from "sonner";
import { api } from "@/api/client";
import { WorkflowBuilder } from "@/components/workflows/WorkflowBuilder";
import { RunWorkflowModal } from "@/components/workflows/RunWorkflowModal";
import jsYaml from "js-yaml";

interface WorkflowData {
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
}

interface WorkflowState {
  workflow?: WorkflowData;
  yaml?: string;
}

interface InputSchema {
  properties: Record<string, { type: string; description?: string; default?: unknown }>;
  required?: string[];
}

/** Parse raw YAML content into a WorkflowData object for the builder. */
function parseYamlToWorkflow(yamlContent: string): WorkflowData | undefined {
  try {
    const parsed = jsYaml.load(yamlContent) as Record<string, unknown>;
    if (!parsed || typeof parsed !== "object") return undefined;
    const rawSteps = (parsed.steps ?? []) as Array<Record<string, unknown>>;
    const steps = rawSteps.map((s) => ({
      id: String(s.id ?? ""),
      model: s.model ? String(s.model) : undefined,
      depends_on: Array.isArray(s.depends_on) ? s.depends_on.map(String) : undefined,
      prompt: s.prompt ? String(s.prompt) : undefined,
    }));
    return {
      name: String(parsed.name ?? "workflow"),
      description: String(parsed.description ?? ""),
      steps_count: steps.length,
      file_name: `${String(parsed.name ?? "workflow")}.yaml`,
      steps,
      yaml_content: yamlContent,
    };
  } catch {
    return undefined;
  }
}

export default function WorkflowBuilderPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const state = location.state as WorkflowState | null;

  // Resolve initialWorkflow from either { workflow } or { yaml } state shape
  const initialWorkflow = useMemo(() => {
    if (state?.workflow) return state.workflow;
    if (state?.yaml) return parseYamlToWorkflow(state.yaml);
    return undefined;
  }, [state]);

  const [runModal, setRunModal] = useState<{
    yaml: string;
    name: string;
    inputSchema?: InputSchema;
  } | null>(null);
  const [saving, setSaving] = useState(false);

  const handleSave = useCallback(
    async (yaml: string, name: string) => {
      if (saving) return;
      setSaving(true);
      try {
        const res = await api.post("/workflows", { name, content: yaml });
        if (res.error) {
          toast.error(`Save failed: ${res.error.message}`);
          return;
        }
        toast.success("Workflow saved");
        navigate("/workflows");
      } finally {
        setSaving(false);
      }
    },
    [navigate, saving]
  );

  const runWorkflow = useCallback(
    async (yaml: string, name: string, input: Record<string, unknown>) => {
      // Auto-save before running so the workflow persists
      const saveRes = await api.post("/workflows", { name, content: yaml });
      if (saveRes.error) {
        toast.error(`Save failed: ${saveRes.error.message}`);
        return;
      }

      const res = await api.post<{ run_id: string }>("/workflows/run", {
        workflow: yaml,
        input,
      });
      if (res.error) {
        toast.error(`Run failed: ${res.error.message}`);
        return;
      }
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
        void runWorkflow(yaml, name, {});
      }
    } catch {
      // YAML parse failed, run directly
      void runWorkflow(yaml, "workflow", {});
    }
  }, [runWorkflow]);

  return (
    <div className="space-y-3 sm:space-y-4">
      <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">Workflow Builder</h1>
      <WorkflowBuilder
        onSave={handleSave}
        onRun={handleRunClick}
        initialWorkflow={initialWorkflow}
      />

      {runModal && (
        <RunWorkflowModal
          open={true}
          workflowName={runModal.name}
          inputSchema={runModal.inputSchema}
          onClose={() => setRunModal(null)}
          onRun={(input) => {
            const { yaml, name } = runModal;
            setRunModal(null);
            void runWorkflow(yaml, name, input);
          }}
        />
      )}
    </div>
  );
}
