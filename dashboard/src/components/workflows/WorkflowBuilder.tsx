import { useCallback, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  addEdge,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type Connection,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Plus, FileText, Play, Save } from "lucide-react";
import { StepNode } from "@/components/workflows/StepNode";
import { StepConfigPanel, type StepConfig } from "@/components/workflows/StepConfigPanel";
import { YamlPreview } from "@/components/workflows/YamlPreview";
import { cn } from "@/lib/utils";

const nodeTypes: NodeTypes = {
  step: StepNode,
};

const DEFAULT_RETRY = { enabled: false, maxAttempts: 3, backoff: "exponential" as const, onFailure: "abort" as const };
const DEFAULT_APPROVAL = { enabled: false, message: "", timeoutHours: 24, onTimeout: "abort" as const, allowEdit: false };
const DEFAULT_SLO = { enabled: false, qualityMin: 0.7, costMaxUsd: 0.10, latencyMaxSeconds: 30, optimizeFor: "balanced" as const };

function generateYaml(
  workflowName: string,
  steps: StepConfig[],
  edges: Edge[]
): string {
  const depMap = new Map<string, string[]>();
  edges.forEach((e) => {
    if (!depMap.has(e.target)) depMap.set(e.target, []);
    depMap.get(e.target)!.push(e.source);
  });

  let yaml = `name: "${workflowName}"\n`;
  yaml += `description: ""\n`;
  yaml += `sandstorm_url: "\${SANDSTORM_URL}"\n`;
  yaml += `default_model: sonnet\n`;
  yaml += `default_max_turns: 10\n`;
  yaml += `default_timeout: 300\n\n`;
  yaml += `steps:\n`;

  for (const step of steps) {
    // Approval gate steps have a different type
    if (step.approval.enabled) {
      yaml += `  - id: "${step.id}"\n`;
      yaml += `    type: approval\n`;
      yaml += `    approval_config:\n`;
      yaml += `      message: "${step.approval.message}"\n`;
      yaml += `      timeout_hours: ${step.approval.timeoutHours}\n`;
      yaml += `      on_timeout: ${step.approval.onTimeout}\n`;
      yaml += `      allow_edit: ${step.approval.allowEdit}\n`;
    } else {
      yaml += `  - id: "${step.id}"\n`;
      yaml += `    prompt: |\n`;
      step.prompt.split("\n").forEach((line) => {
        yaml += `      ${line}\n`;
      });
      if (step.model !== "sonnet") yaml += `    model: ${step.model}\n`;
      if (step.maxTurns !== 10) yaml += `    max_turns: ${step.maxTurns}\n`;
      if (step.timeout !== 300) yaml += `    timeout: ${step.timeout}\n`;
    }

    const deps = depMap.get(step.id) || step.dependsOn;
    if (deps && deps.length > 0) {
      yaml += `    depends_on:\n`;
      deps.forEach((d) => {
        yaml += `      - "${d}"\n`;
      });
    }
    if (step.parallelOver) {
      yaml += `    parallel_over: "${step.parallelOver}"\n`;
    }

    // Retry config
    if (step.retry.enabled) {
      yaml += `    retry:\n`;
      yaml += `      max_attempts: ${step.retry.maxAttempts}\n`;
      yaml += `      backoff: ${step.retry.backoff}\n`;
      yaml += `      on_failure: ${step.retry.onFailure}\n`;
    }

    // Policies
    if (step.policies.length > 0) {
      yaml += `    policies:\n`;
      step.policies.forEach((p) => {
        yaml += `      - "${p}"\n`;
      });
    }

    // SLO config
    if (step.slo.enabled) {
      yaml += `    slo:\n`;
      yaml += `      quality_min: ${step.slo.qualityMin}\n`;
      yaml += `      cost_max_usd: ${step.slo.costMaxUsd}\n`;
      yaml += `      latency_max_seconds: ${step.slo.latencyMaxSeconds}\n`;
      yaml += `      optimize_for: ${step.slo.optimizeFor}\n`;
      yaml += `      model_pool: auto\n`;
    }

    yaml += `\n`;
  }

  return yaml;
}

interface InitialWorkflow {
  name: string;
  description: string;
  steps_count: number;
  file_name: string;
  steps?: Array<{
    id: string;
    model?: string;
    depends_on?: string[];
  }>;
}

interface WorkflowBuilderProps {
  onSave?: (yaml: string, name: string) => void;
  onRun?: (yaml: string) => void;
  initialWorkflow?: InitialWorkflow;
}

function buildInitialState(wf: InitialWorkflow) {
  const steps: StepConfig[] = (wf.steps || []).map((s) => ({
    id: s.id,
    prompt: "",
    model: s.model || "sonnet",
    maxTurns: 10,
    timeout: 300,
    parallelOver: "",
    dependsOn: s.depends_on || [],
    retry: { ...DEFAULT_RETRY },
    approval: { ...DEFAULT_APPROVAL },
    policies: [],
    slo: { ...DEFAULT_SLO },
  }));

  const nodes: Node[] = steps.map((s, i) => ({
    id: s.id,
    type: "step" as const,
    position: { x: 200 + (i % 3) * 220, y: 50 + Math.floor(i / 3) * 150 },
    data: { label: s.id, model: s.model },
  }));

  const edges: Edge[] = [];
  for (const s of steps) {
    for (const dep of s.dependsOn) {
      edges.push({
        id: `${dep}-${s.id}`,
        source: dep,
        target: s.id,
        style: { stroke: "var(--color-accent)", strokeWidth: 2 },
      });
    }
  }

  return { steps, nodes, edges };
}

export function WorkflowBuilder({ onSave, onRun, initialWorkflow }: WorkflowBuilderProps) {
  const initial = initialWorkflow ? buildInitialState(initialWorkflow) : null;
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(initial?.nodes || []);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initial?.edges || []);
  const [steps, setSteps] = useState<StepConfig[]>(initial?.steps || []);
  const [selectedStepId, setSelectedStepId] = useState<string | null>(null);
  const [workflowName, setWorkflowName] = useState(
    initialWorkflow?.file_name.replace(".yaml", "") || "my-workflow"
  );
  const [yamlOpen, setYamlOpen] = useState(false);
  const [counter, setCounter] = useState(initial ? initial.steps.length + 1 : 1);

  const onConnect = useCallback(
    (connection: Connection) => {
      setEdges((eds) => addEdge({ ...connection, style: { stroke: "var(--color-accent)", strokeWidth: 2 } }, eds));
    },
    [setEdges]
  );

  const addStep = useCallback(() => {
    const id = `step_${counter}`;
    setCounter((c) => c + 1);

    const newStep: StepConfig = {
      id,
      prompt: "",
      model: "sonnet",
      maxTurns: 10,
      timeout: 300,
      parallelOver: "",
      dependsOn: [],
      retry: { ...DEFAULT_RETRY },
      approval: { ...DEFAULT_APPROVAL },
      policies: [],
      slo: { ...DEFAULT_SLO },
    };

    const newNode: Node = {
      id,
      type: "step",
      position: { x: 200 + (nodes.length % 3) * 200, y: 50 + Math.floor(nodes.length / 3) * 150 },
      data: { label: id, model: "sonnet" },
    };

    setSteps((prev) => [...prev, newStep]);
    setNodes((prev) => [...prev, newNode]);
    setSelectedStepId(id);
  }, [counter, nodes.length, setNodes]);

  const updateStep = useCallback(
    (updated: StepConfig) => {
      setSteps((prev) =>
        prev.map((s) => (s.id === selectedStepId ? updated : s))
      );
      // Update node label and feature badges
      setNodes((prev) =>
        prev.map((n) =>
          n.id === selectedStepId
            ? {
                ...n,
                id: updated.id,
                data: {
                  ...n.data,
                  label: updated.id,
                  model: updated.model,
                  hasRetry: updated.retry.enabled,
                  hasApproval: updated.approval.enabled,
                  hasSlo: updated.slo.enabled,
                },
              }
            : n
        )
      );
      if (updated.id !== selectedStepId) {
        // Update edges
        setEdges((prev) =>
          prev.map((e) => ({
            ...e,
            source: e.source === selectedStepId ? updated.id : e.source,
            target: e.target === selectedStepId ? updated.id : e.target,
          }))
        );
        setSelectedStepId(updated.id);
      }
    },
    [selectedStepId, setNodes, setEdges]
  );

  const deleteStep = useCallback(() => {
    if (!selectedStepId) return;
    setSteps((prev) => prev.filter((s) => s.id !== selectedStepId));
    setNodes((prev) => prev.filter((n) => n.id !== selectedStepId));
    setEdges((prev) =>
      prev.filter((e) => e.source !== selectedStepId && e.target !== selectedStepId)
    );
    setSelectedStepId(null);
  }, [selectedStepId, setNodes, setEdges]);

  const selectedStep = steps.find((s) => s.id === selectedStepId);
  const yaml = generateYaml(workflowName, steps, edges);

  return (
    <div className="flex h-[calc(100vh-10rem)] gap-0 rounded-xl border border-border bg-surface shadow-sm overflow-hidden">
      {/* Left palette */}
      <div className="w-48 shrink-0 border-r border-border bg-background/50 p-3">
        <p className="mb-3 text-xs font-semibold text-muted">PALETTE</p>
        <button
          onClick={addStep}
          className={cn(
            "flex w-full items-center gap-2 rounded-lg border border-dashed border-border px-3 py-2.5",
            "text-xs font-medium text-muted hover:border-accent hover:text-accent transition-colors"
          )}
        >
          <Plus className="h-3.5 w-3.5" />
          Agent Step
        </button>

        <div className="mt-6">
          <label className="mb-1 block text-xs font-medium text-muted">Workflow Name</label>
          <input
            type="text"
            value={workflowName}
            onChange={(e) => setWorkflowName(e.target.value)}
            className={cn(
              "h-8 w-full rounded-md border border-border bg-surface px-2 text-xs",
              "focus:border-accent/50 focus:outline-none focus:ring-1 focus:ring-ring/30"
            )}
          />
        </div>
      </div>

      {/* Canvas */}
      <div className="flex-1">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          nodeTypes={nodeTypes}
          onNodeClick={(_, node) => setSelectedStepId(node.id)}
          onPaneClick={() => setSelectedStepId(null)}
          fitView
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={16} size={1} color="var(--color-border)" />
          <Controls showInteractive={false} className="!bg-surface !border-border !shadow-sm" />
        </ReactFlow>
      </div>

      {/* Right config panel */}
      {selectedStep && (
        <div className="w-72 shrink-0 overflow-y-auto border-l border-border">
          <StepConfigPanel
            step={selectedStep}
            allStepIds={steps.map((s) => s.id)}
            onChange={updateStep}
            onDelete={deleteStep}
          />
        </div>
      )}

      {/* Bottom bar */}
      <div className="absolute bottom-0 left-0 right-0 flex items-center justify-end gap-2 border-t border-border bg-surface/95 px-4 py-2 backdrop-blur-sm">
        <button
          onClick={() => setYamlOpen(true)}
          className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-muted hover:text-foreground transition-colors"
        >
          <FileText className="h-3.5 w-3.5" />
          Preview YAML
        </button>
        {onSave && (
          <button
            onClick={() => onSave(yaml, workflowName)}
            className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-muted hover:text-foreground transition-colors"
          >
            <Save className="h-3.5 w-3.5" />
            Save
          </button>
        )}
        {onRun && (
          <button
            onClick={() => onRun(yaml)}
            className={cn(
              "flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-accent-foreground",
              "hover:bg-accent-hover transition-all duration-200 shadow-sm"
            )}
          >
            <Play className="h-3.5 w-3.5" />
            Run
          </button>
        )}
      </div>

      {/* YAML Preview Drawer */}
      <YamlPreview yaml={yaml} open={yamlOpen} onClose={() => setYamlOpen(false)} />
    </div>
  );
}
