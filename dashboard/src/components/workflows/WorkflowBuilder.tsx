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
import { Plus, FileText, Play, Save, Monitor, Layers } from "lucide-react";
import { StepNode } from "@/components/workflows/StepNode";
import {
  StepConfigPanel,
  type StepConfig,
  type CsvOutputConfig,
  type PdfReportConfig,
  type RetryConfig,
  type ApprovalConfig,
  type SloConfig,
  type AutoPilotConfig,
} from "@/components/workflows/StepConfigPanel";
import { YamlPreview } from "@/components/workflows/YamlPreview";
import { TemplateBrowser } from "@/components/workflows/TemplateBrowser";
import { cn } from "@/lib/utils";

const nodeTypes: NodeTypes = {
  step: StepNode,
};

const DEFAULT_RETRY = { enabled: false, maxAttempts: 3, backoff: "exponential" as const, onFailure: "abort" as const };
const DEFAULT_APPROVAL = { enabled: false, message: "", timeoutHours: 24, onTimeout: "abort" as const, allowEdit: false };
const DEFAULT_DIRECTORY_INPUT = { enabled: false, defaultPath: "" };
const DEFAULT_CSV_OUTPUT = { enabled: false, directory: "./output", mode: "new_file" as const, filename: "" };
const DEFAULT_PDF_REPORT = { enabled: false, directory: "./output", language: "en", filename: "" };
const DEFAULT_AUTOPILOT = {
  enabled: false, optimizeFor: "quality" as const, evaluation: "llm_judge" as const,
  sampleRate: 1.0, minSamples: 10, qualityThreshold: 0.7, autoDeploy: true, variants: [],
};
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

  // Check if any step uses directory input
  const hasDirInput = steps.some((s) => s.directoryInput.enabled);

  let yaml = `name: "${workflowName}"\n`;
  yaml += `description: ""\n`;
  yaml += `sandstorm_url: "\${SANDSTORM_URL}"\n`;
  yaml += `default_model: sonnet\n`;
  yaml += `default_max_turns: 10\n`;
  yaml += `default_timeout: 300\n\n`;

  if (hasDirInput) {
    const dirStep = steps.find((s) => s.directoryInput.enabled)!;
    yaml += `input_schema:\n`;
    yaml += `  required: ["directory"]\n`;
    yaml += `  properties:\n`;
    yaml += `    directory:\n`;
    yaml += `      type: string\n`;
    yaml += `      description: "Path to directory"\n`;
    if (dirStep.directoryInput.defaultPath) {
      yaml += `      default: "${dirStep.directoryInput.defaultPath}"\n`;
    }
    yaml += `\n`;
  }

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

    // AutoPilot config
    if (step.autopilot.enabled && step.autopilot.variants.length >= 2) {
      yaml += `    autopilot:\n`;
      yaml += `      enabled: true\n`;
      yaml += `      optimize_for: ${step.autopilot.optimizeFor}\n`;
      yaml += `      sample_rate: ${step.autopilot.sampleRate}\n`;
      yaml += `      min_samples: ${step.autopilot.minSamples}\n`;
      yaml += `      quality_threshold: ${step.autopilot.qualityThreshold}\n`;
      yaml += `      auto_deploy: ${step.autopilot.autoDeploy}\n`;
      yaml += `      evaluation:\n`;
      yaml += `        method: ${step.autopilot.evaluation}\n`;
      yaml += `      variants:\n`;
      for (const v of step.autopilot.variants) {
        yaml += `        - id: "${v.id}"\n`;
        yaml += `          model: ${v.model}\n`;
        if (v.prompt) {
          yaml += `          prompt: |\n`;
          v.prompt.split("\n").forEach((line) => {
            yaml += `            ${line}\n`;
          });
        }
        if (v.maxTurns) {
          yaml += `          max_turns: ${v.maxTurns}\n`;
        }
      }
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

    // CSV output config
    if (step.csvOutput.enabled) {
      yaml += `    csv_output:\n`;
      yaml += `      directory: "${step.csvOutput.directory || "./output"}"\n`;
      yaml += `      mode: ${step.csvOutput.mode}\n`;
      if (step.csvOutput.filename) {
        yaml += `      filename: "${step.csvOutput.filename}"\n`;
      }
    }

    // PDF report config
    if (step.pdfReport.enabled) {
      yaml += `    pdf_report:\n`;
      yaml += `      directory: "${step.pdfReport.directory || "./output"}"\n`;
      yaml += `      language: ${step.pdfReport.language}\n`;
      if (step.pdfReport.filename) {
        yaml += `      filename: "${step.pdfReport.filename}"\n`;
      }
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
    prompt?: string;
  }>;
  yaml_content?: string;
}

interface WorkflowBuilderProps {
  onSave?: (yaml: string, name: string) => void;
  onRun?: (yaml: string) => void;
  initialWorkflow?: InitialWorkflow;
}

/**
 * Extract a YAML block for a specific step from raw YAML content.
 * Returns the text between `- id: "stepId"` and the next `- id:` or end of steps.
 */
function extractStepBlock(yamlContent: string, stepId: string): string {
  // Match the block for this step ID (quoted or unquoted)
  const pattern = new RegExp(
    `- id:\\s*["']?${stepId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}["']?\\s*\\n([\\s\\S]*?)(?=\\n\\s*- id:|$)`
  );
  const m = yamlContent.match(pattern);
  return m ? m[1] : "";
}

/** Parse simple YAML key: value from a block, supporting nested keys like `csv_output.directory`. */
function yamlValue(block: string, key: string): string | undefined {
  const re = new RegExp(`${key}:\\s*["']?([^"'\\n]+)["']?`);
  const m = block.match(re);
  return m ? m[1].trim() : undefined;
}

/** Check if a top-level key exists in a step's YAML block. */
function hasYamlKey(block: string, key: string): boolean {
  return new RegExp(`^\\s+${key}:`, "m").test(block);
}

/** Parse advanced step config from raw YAML content. */
function parseAdvancedConfig(yamlContent: string | undefined, stepId: string) {
  if (!yamlContent) return {};

  const block = extractStepBlock(yamlContent, stepId);
  if (!block) return {};

  const result: Partial<{
    csvOutput: CsvOutputConfig;
    pdfReport: PdfReportConfig;
    retry: RetryConfig;
    approval: ApprovalConfig;
    slo: SloConfig;
    autopilot: Partial<AutoPilotConfig>;
    policies: string[];
  }> = {};

  // CSV output
  if (hasYamlKey(block, "csv_output")) {
    // Extract the csv_output sub-block
    const csvBlock = block.match(/csv_output:\s*\n((?:\s{6,}.*\n)*)/)?.[1] || "";
    result.csvOutput = {
      enabled: true,
      directory: yamlValue(csvBlock, "directory") || "./output",
      mode: (yamlValue(csvBlock, "mode") as "append" | "new_file") || "new_file",
      filename: yamlValue(csvBlock, "filename") || "",
    };
  }

  // PDF report
  if (hasYamlKey(block, "pdf_report")) {
    const pdfBlock = block.match(/pdf_report:\s*\n((?:\s{6,}.*\n)*)/)?.[1] || "";
    result.pdfReport = {
      enabled: true,
      directory: yamlValue(pdfBlock, "directory") || "./output",
      language: yamlValue(pdfBlock, "language") || "en",
      filename: yamlValue(pdfBlock, "filename") || "",
    };
  }

  // Retry
  if (hasYamlKey(block, "retry")) {
    const retryBlock = block.match(/retry:\s*\n((?:\s{6,}.*\n)*)/)?.[1] || "";
    result.retry = {
      enabled: true,
      maxAttempts: parseInt(yamlValue(retryBlock, "max_attempts") || "3", 10),
      backoff: (yamlValue(retryBlock, "backoff") as "exponential" | "fixed") || "exponential",
      onFailure: (yamlValue(retryBlock, "on_failure") as "abort" | "skip" | "fallback") || "abort",
    };
  }

  // SLO
  if (hasYamlKey(block, "slo")) {
    const sloBlock = block.match(/slo:\s*\n((?:\s{6,}.*\n)*)/)?.[1] || "";
    result.slo = {
      enabled: true,
      qualityMin: parseFloat(yamlValue(sloBlock, "quality_min") || "0.7"),
      costMaxUsd: parseFloat(yamlValue(sloBlock, "cost_max_usd") || "0.10"),
      latencyMaxSeconds: parseInt(yamlValue(sloBlock, "latency_max_seconds") || "30", 10),
      optimizeFor: (yamlValue(sloBlock, "optimize_for") as "cost" | "quality" | "latency" | "balanced") || "balanced",
    };
  }

  // Policies
  const policiesMatch = block.match(/policies:\s*\n((?:\s+-\s+.*\n)*)/);
  if (policiesMatch) {
    const policyLines = policiesMatch[1].matchAll(/-\s+["']?([^"'\n]+)["']?/g);
    result.policies = [...policyLines].map((m) => m[1].trim());
  }

  return result;
}

function buildInitialState(wf: InitialWorkflow) {
  const steps: StepConfig[] = (wf.steps || []).map((s) => {
    const adv = parseAdvancedConfig(wf.yaml_content, s.id);
    return {
      id: s.id,
      prompt: s.prompt || "",
      model: s.model || "sonnet",
      maxTurns: 10,
      timeout: 300,
      parallelOver: "",
      dependsOn: s.depends_on || [],
      directoryInput: { ...DEFAULT_DIRECTORY_INPUT },
      csvOutput: adv.csvOutput || { ...DEFAULT_CSV_OUTPUT },
      pdfReport: adv.pdfReport || { ...DEFAULT_PDF_REPORT },
      autopilot: { ...DEFAULT_AUTOPILOT, variants: [], ...adv.autopilot },
      retry: adv.retry || { ...DEFAULT_RETRY },
      approval: { ...DEFAULT_APPROVAL },
      policies: adv.policies || [],
      slo: adv.slo || { ...DEFAULT_SLO },
    };
  });

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
  const [templateBrowserOpen, setTemplateBrowserOpen] = useState(false);
  const [confirmReplace, setConfirmReplace] = useState(false);
  const [pendingTemplate, setPendingTemplate] = useState<{
    name: string;
    content: string;
    steps: Array<{ id: string; model?: string; depends_on?: string[] }>;
  } | null>(null);
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
      directoryInput: { ...DEFAULT_DIRECTORY_INPUT },
      csvOutput: { ...DEFAULT_CSV_OUTPUT },
      pdfReport: { ...DEFAULT_PDF_REPORT },
      autopilot: { ...DEFAULT_AUTOPILOT, variants: [] },
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
                  hasAutoPilot: updated.autopilot.enabled,
                  hasCsvOutput: updated.csvOutput.enabled,
                  hasPdfReport: updated.pdfReport.enabled,
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

  // Parse template YAML to extract step definitions
  const parseTemplateSteps = useCallback(
    (yamlStr: string): Array<{ id: string; model?: string; depends_on?: string[]; prompt?: string }> => {
      const parsed: Array<{ id: string; model?: string; depends_on?: string[]; prompt?: string }> = [];
      const stepBlocks = yamlStr.split(/\n\s+-\s+id:\s+/).slice(1);
      for (const block of stepBlocks) {
        const idMatch = block.match(/^"?([^"\n]+)"?/);
        if (!idMatch) continue;
        const id = idMatch[1];
        const modelMatch = block.match(/model:\s+(\w+)/);
        const model = modelMatch ? modelMatch[1] : undefined;
        // Extract prompt (block scalar with | or > or inline)
        let prompt: string | undefined;
        const blockPrompt = block.match(/prompt:\s*[|>]-?\s*\n((?:[ \t]+[^\n]*\n?)*)/);
        if (blockPrompt) {
          prompt = blockPrompt[1].replace(/^[ \t]{2,}/gm, "").trim();
        } else {
          const inlinePrompt = block.match(/prompt:\s*"([^"]+)"|prompt:\s+'([^']+)'|prompt:\s+([^\n]+)/);
          if (inlinePrompt) {
            prompt = (inlinePrompt[1] || inlinePrompt[2] || inlinePrompt[3] || "").trim();
          }
        }
        const deps: string[] = [];
        // Inline format: depends_on: [step1, step2] or depends_on: ["step1", "step2"]
        const inlineDeps = block.match(/depends_on:\s*\[([^\]]+)\]/);
        if (inlineDeps) {
          for (const d of inlineDeps[1].split(",")) {
            const cleaned = d.trim().replace(/^["']|["']$/g, "");
            if (cleaned) deps.push(cleaned);
          }
        } else {
          // Multiline format: depends_on:\n  - "step1"\n  - "step2"
          const depsSection = block.match(/depends_on:\s*\n((?:\s+-\s+"?[^"\n]+"?\n?)*)/);
          if (depsSection) {
            const depMatches = depsSection[1].matchAll(/-\s+"?([^"\n]+)"?/g);
            for (const dm of depMatches) {
              deps.push(dm[1]);
            }
          }
        }
        parsed.push({ id, model, prompt, depends_on: deps.length > 0 ? deps : undefined });
      }
      return parsed;
    },
    []
  );

  // Apply a template to the canvas
  const applyTemplate = useCallback(
    (templateData: { name: string; content: string; steps: Array<{ id: string; model?: string; depends_on?: string[]; prompt?: string }> }) => {
      const built = buildInitialState({
        name: templateData.name,
        description: "",
        steps_count: templateData.steps.length,
        file_name: `${templateData.name}.yaml`,
        steps: templateData.steps,
      });

      setWorkflowName(templateData.name);
      setSteps(built.steps);
      setNodes(built.nodes);
      setEdges(built.edges);
      setCounter(built.steps.length + 1);
      setSelectedStepId(null);
      setPendingTemplate(null);
      setConfirmReplace(false);
    },
    [setNodes, setEdges]
  );

  // Handle template selection from the browser
  const handleTemplateSelect = useCallback(
    (template: { name: string; content: string; step_count: number }) => {
      setTemplateBrowserOpen(false);
      const parsedSteps = parseTemplateSteps(template.content);
      const templateData = { name: template.name, content: template.content, steps: parsedSteps };

      if (nodes.length > 0) {
        setPendingTemplate(templateData);
        setConfirmReplace(true);
      } else {
        applyTemplate(templateData);
      }
    },
    [nodes.length, parseTemplateSteps, applyTemplate]
  );

  const selectedStep = steps.find((s) => s.id === selectedStepId);
  const yaml = generateYaml(workflowName, steps, edges);

  return (
    <div className="relative flex h-[calc(100vh-10rem)] gap-0 rounded-xl border border-border bg-surface shadow-sm overflow-hidden">
      {/* Mobile notice */}
      <div className="absolute inset-x-0 top-0 z-10 flex items-center gap-2 bg-warning/10 border-b border-warning/30 px-3 py-2 text-xs text-warning lg:hidden">
        <Monitor className="h-4 w-4 shrink-0" />
        <span>Best experience on desktop. Some features are limited on smaller screens.</span>
      </div>

      {/* Left palette */}
      <div className="hidden lg:block w-48 shrink-0 border-r border-border bg-background/50 p-3">
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

        <button
          onClick={() => setTemplateBrowserOpen(true)}
          className={cn(
            "mt-2 flex w-full items-center gap-2 rounded-lg border border-dashed border-border px-3 py-2.5",
            "text-xs font-medium text-muted hover:border-accent hover:text-accent transition-colors"
          )}
        >
          <Layers className="h-3.5 w-3.5" />
          From Template
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

      {/* Mobile add step button */}
      <button
        onClick={addStep}
        className="absolute left-3 top-12 z-10 flex h-9 w-9 items-center justify-center rounded-lg border border-border bg-surface shadow-sm text-muted hover:text-accent hover:border-accent transition-colors lg:hidden"
      >
        <Plus className="h-4 w-4" />
      </button>
      <button
        onClick={() => setTemplateBrowserOpen(true)}
        className="absolute left-14 top-12 z-10 flex h-9 w-9 items-center justify-center rounded-lg border border-border bg-surface shadow-sm text-muted hover:text-accent hover:border-accent transition-colors lg:hidden"
      >
        <Layers className="h-4 w-4" />
      </button>

      {/* Canvas */}
      <div className="flex-1 pt-8 lg:pt-0">
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

      {/* Right config panel - sidebar on desktop, overlay on mobile */}
      {selectedStep && (
        <>
          <div
            className="fixed inset-0 z-20 bg-black/30 lg:hidden"
            onClick={() => setSelectedStepId(null)}
          />
          <div className="fixed inset-y-0 right-0 z-30 w-72 shrink-0 overflow-y-auto border-l border-border bg-surface lg:static lg:z-auto">
            <StepConfigPanel
              step={selectedStep}
              allStepIds={steps.map((s) => s.id)}
              onChange={updateStep}
              onDelete={deleteStep}
            />
          </div>
        </>
      )}

      {/* Bottom bar */}
      <div className="absolute bottom-0 left-0 right-0 flex items-center justify-end gap-2 border-t border-border bg-surface/95 px-3 sm:px-4 py-2 backdrop-blur-sm">
        <button
          onClick={() => setYamlOpen(true)}
          className="flex items-center gap-1.5 rounded-lg border border-border px-2 sm:px-3 py-1.5 text-xs font-medium text-muted hover:text-foreground transition-colors"
        >
          <FileText className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">Preview YAML</span>
        </button>
        {onSave && (
          <button
            onClick={() => onSave(yaml, workflowName)}
            className="flex items-center gap-1.5 rounded-lg border border-border px-2 sm:px-3 py-1.5 text-xs font-medium text-muted hover:text-foreground transition-colors"
          >
            <Save className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Save</span>
          </button>
        )}
        {onRun && (
          <button
            onClick={() => onRun(yaml)}
            className={cn(
              "flex items-center gap-1.5 rounded-lg bg-accent px-2 sm:px-3 py-1.5 text-xs font-medium text-accent-foreground",
              "hover:bg-accent-hover transition-all duration-200 shadow-sm"
            )}
          >
            <Play className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Run</span>
          </button>
        )}
      </div>

      {/* YAML Preview Drawer */}
      <YamlPreview yaml={yaml} open={yamlOpen} onClose={() => setYamlOpen(false)} />

      {/* Template Browser */}
      <TemplateBrowser
        open={templateBrowserOpen}
        onClose={() => setTemplateBrowserOpen(false)}
        onSelect={handleTemplateSelect}
      />

      {/* Confirm replace dialog */}
      {confirmReplace && (
        <>
          <div className="fixed inset-0 z-[60] bg-black/40" onClick={() => setConfirmReplace(false)} />
          <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
            <div className="w-full max-w-sm rounded-xl border border-border bg-surface p-6 shadow-xl">
              <h3 className="text-sm font-semibold text-foreground mb-2">Replace current workflow?</h3>
              <p className="text-xs text-muted leading-relaxed mb-5">
                This will replace all existing steps with the template. This action cannot be undone.
              </p>
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => {
                    setPendingTemplate(null);
                    setConfirmReplace(false);
                  }}
                  className="rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-muted hover:text-foreground transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={() => {
                    if (pendingTemplate) applyTemplate(pendingTemplate);
                  }}
                  className={cn(
                    "rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-accent-foreground",
                    "hover:bg-accent-hover transition-all duration-200 shadow-sm"
                  )}
                >
                  Replace
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
