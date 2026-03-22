import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  FileSpreadsheet,
  FileText,
  FlaskConical,
  FolderOpen,
  Gauge,
  Plus,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Trash2,
  Wrench,
  X,
} from "lucide-react";
import { DirectoryBrowser } from "@/components/workflows/DirectoryBrowser";
import { ToolSelector } from "@/components/workflows/ToolSelector";
import { cn } from "@/lib/utils";

export interface RetryConfig {
  enabled: boolean;
  maxAttempts: number;
  backoff: "exponential" | "fixed";
  onFailure: "abort" | "skip" | "fallback";
}

export interface ApprovalConfig {
  enabled: boolean;
  message: string;
  timeoutHours: number;
  onTimeout: "abort" | "skip";
  allowEdit: boolean;
}

export interface SloConfig {
  enabled: boolean;
  qualityMin: number;
  costMaxUsd: number;
  latencyMaxSeconds: number;
  optimizeFor: "cost" | "quality" | "latency" | "balanced";
}

export interface DirectoryInputConfig {
  enabled: boolean;
  defaultPath: string;
}

export interface AutoPilotVariant {
  id: string;
  model: string;
  prompt: string;
  maxTurns: number | null;
}

export interface CsvOutputConfig {
  enabled: boolean;
  directory: string;
  mode: "append" | "new_file";
  filename: string;
}

export interface PdfReportConfig {
  enabled: boolean;
  directory: string;
  language: string;
  filename: string;
}

export interface AutoPilotConfig {
  enabled: boolean;
  optimizeFor: "quality" | "cost" | "latency" | "pareto";
  evaluation: "llm_judge" | "schema_completeness";
  sampleRate: number;
  minSamples: number;
  qualityThreshold: number;
  autoDeploy: boolean;
  variants: AutoPilotVariant[];
}

export type StepType = "standard" | "llm" | "http" | "code" | "condition" | "classify" | "loop" | "approval" | "sub_workflow" | "race" | "sensor" | "gate" | "transform" | "notify" | "delegate" | "browser" | "parse";

export interface HttpStepConfig {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: string;
  auth: string;
}

export interface CodeStepConfig {
  code: string;
  language: string;
}

export interface ConditionStepConfig {
  expression: string;
  thenSteps: string[];
  elseSteps: string[];
}

export interface ClassifyStepConfig {
  categories: string[];
  input: string;
  model: string;
  branches: Record<string, string[]>;
}

export interface LoopStepConfig {
  over: string;
  stepIds: string[];
  maxIterations: number;
}

export interface RaceStepConfig {
  branches: string;  // textarea: one branch per line, step IDs comma-separated
  validator: string;
}

export interface SensorStepConfig {
  url: string;
  method: string;
  headers: string;  // JSON string
  checkInterval: number;
  timeout: number;
  condition: string;
}

export interface GateStrategy {
  type: "llm_eval" | "human" | "timeout";
  prompt: string;
  input: string;
  model: string;
  message: string;
  timeoutHours: number;
  seconds: number;
  action: "approve" | "reject";
  onTimeout: string;
}

export interface GateStepConfig {
  strategies: GateStrategy[];
}

export interface TransformStepConfig {
  template: string;
}

export interface NotifyStepConfig {
  service: string;
  channel: string;
  message: string;
}

export interface DelegateStepConfig {
  workflow: string;
  taskDescription: string;
  timeout: number;
}

export interface BrowserStepConfig {
  mode: string;
  startUrl: string;
  viewportWidth: number;
  viewportHeight: number;
  timeout: number;
  waitAfterAction: number;
  headless: boolean;
  credentials_env: string;
  screenshotOnError: boolean;
  max_actions: number;
  capture_screenshots: boolean;
  output_schema: Record<string, unknown> | null;
  captcha_strategy: string;
}

export interface StepConfig {
  id: string;
  stepType: StepType;
  prompt: string;
  model: string;
  maxTurns: number;
  timeout: number;
  parallelOver: string;
  dependsOn: string[];
  tools: string[];
  directoryInput: DirectoryInputConfig;
  csvOutput: CsvOutputConfig;
  pdfReport: PdfReportConfig;
  autopilot: AutoPilotConfig;
  retry: RetryConfig;
  approval: ApprovalConfig;
  policies: string[];
  slo: SloConfig;
  llmSystemPrompt: string;
  httpConfig: HttpStepConfig;
  codeConfig: CodeStepConfig;
  conditionConfig: ConditionStepConfig;
  classifyConfig: ClassifyStepConfig;
  loopConfig: LoopStepConfig;
  raceConfig: RaceStepConfig;
  sensorConfig: SensorStepConfig;
  gateConfig: GateStepConfig;
  transformConfig: TransformStepConfig;
  notifyConfig: NotifyStepConfig;
  delegateConfig: DelegateStepConfig;
  browserConfig: BrowserStepConfig;
}

interface StepConfigPanelProps {
  step: StepConfig;
  allStepIds: string[];
  onChange: (step: StepConfig) => void;
  onDelete: () => void;
}

const POLICY_OPTIONS = [
  { id: "pii-redact", label: "PII Redact", hint: "Detects and replaces emails, phones, SSNs in output." },
  { id: "secret-block", label: "Secret Block", hint: "Blocks execution if API keys or tokens are found." },
  { id: "cost-guard", label: "Cost Guard", hint: "Stops step if cost exceeds the per-step budget." },
  { id: "length-limit", label: "Length Limit", hint: "Flags output exceeding the token length limit." },
];

function CollapsibleSection({
  icon: Icon,
  title,
  enabled,
  onToggle,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  enabled: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded-lg border border-border">
      <button
        type="button"
        onClick={() => {
          if (!enabled) {
            onToggle();
            setOpen(true);
          } else {
            setOpen(!open);
          }
        }}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <Icon className="h-3.5 w-3.5 text-muted" />
        <span className="flex-1 text-xs font-medium text-foreground">{title}</span>
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => {
            e.stopPropagation();
            onToggle();
            if (!enabled) setOpen(true);
          }}
          className="rounded border-border text-accent focus-visible:ring-accent"
        />
        {enabled && (
          open
            ? <ChevronDown className="h-3.5 w-3.5 text-muted" />
            : <ChevronRight className="h-3.5 w-3.5 text-muted" />
        )}
      </button>
      {enabled && open && (
        <div className="border-t border-border px-3 py-2.5 space-y-3">
          {children}
        </div>
      )}
    </div>
  );
}

export function StepConfigPanel({ step, allStepIds, onChange, onDelete }: StepConfigPanelProps) {
  const [customPolicy, setCustomPolicy] = useState("");
  const [browseOpen, setBrowseOpen] = useState(false);
  const [csvBrowseOpen, setCsvBrowseOpen] = useState(false);
  const [pdfBrowseOpen, setPdfBrowseOpen] = useState(false);

  const inputClass = cn(
    "h-9 w-full rounded-lg border border-border bg-background px-3 text-sm",
    "focus-visible:border-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
  );

  return (
    <div className="space-y-4 p-4">
      <h3 className="text-sm font-semibold text-foreground">Step Configuration</h3>

      <div>
        <label className="mb-1 block text-xs font-medium text-muted">Step ID</label>
        <input
          type="text"
          value={step.id}
          onChange={(e) => onChange({ ...step, id: e.target.value })}
          className={inputClass}
        />
        <p className="text-[11px] text-muted-foreground mt-0.5">Unique identifier used in depends_on and YAML output.</p>
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-muted">Step Type</label>
        <select
          value={step.stepType}
          onChange={(e) => onChange({ ...step, stepType: e.target.value as StepType })}
          className={inputClass}
        >
          <option value="standard">Standard (Agent)</option>
          <option value="llm">LLM (Single Call)</option>
          <option value="http">HTTP Request</option>
          <option value="code">Code (Python)</option>
          <option value="condition">Condition (If/Else)</option>
          <option value="classify">Classify (Route)</option>
          <option value="loop">Loop (For Each)</option>
          <option value="race">Race (Parallel)</option>
          <option value="sensor">Sensor (Poll)</option>
          <option value="gate">Gate (Approval)</option>
          <option value="transform">Transform (Template)</option>
          <option value="notify">Notify (Alert)</option>
          <option value="delegate">Delegate (Sub-workflow)</option>
          <option value="browser">Browser (RPA)</option>
          <option value="parse">Parse Document</option>
        </select>
        <p className="text-[11px] text-muted-foreground mt-0.5">
          {step.stepType === "standard" && "Full agent with sandbox - multi-turn conversation with tools."}
          {step.stepType === "llm" && "Single LLM API call - no sandbox, no tools. Fast and cheap."}
          {step.stepType === "http" && "Direct HTTP request - $0 cost, no LLM involved."}
          {step.stepType === "code" && "Inline Python code execution - $0 cost."}
          {step.stepType === "condition" && "If/else branching based on expression evaluation."}
          {step.stepType === "classify" && "LLM-based classification into categories, routes to branches."}
          {step.stepType === "loop" && "Iterate over a list, running sub-steps for each item."}
          {step.stepType === "race" && "Run branches in parallel - first valid result wins."}
          {step.stepType === "sensor" && "Poll an external URL until a condition is met."}
          {step.stepType === "gate" && "Multi-strategy approval gate (LLM eval, human, timeout)."}
          {step.stepType === "transform" && "Template-based data transformation - $0 cost, no LLM."}
          {step.stepType === "notify" && "Send notifications via Slack, Teams, Gmail, or webhook - $0 cost."}
          {step.stepType === "delegate" && "Delegate work to another workflow as a sub-task."}
          {step.stepType === "browser" && "Browser automation via Playwright selectors or Computer Use visual AI."}
          {step.stepType === "parse" && "Extract text from PDF, DOCX, XLSX, PPTX, CSV - $0 cost, no LLM."}
        </p>
      </div>

      {/* Prompt - shown for standard, llm, classify */}
      {(step.stepType === "standard" || step.stepType === "llm") && (
        <div>
          <label className="mb-1 block text-xs font-medium text-muted">Prompt</label>
          <textarea
            value={step.prompt}
            onChange={(e) => onChange({ ...step, prompt: e.target.value })}
            rows={6}
            className={cn(inputClass, "h-auto py-2 resize-y")}
          />
          <p className="text-[11px] text-muted-foreground mt-0.5">{"Use {input.field} for workflow input or {steps.id.output} for previous step data."}</p>
        </div>
      )}

      {/* LLM System Prompt */}
      {step.stepType === "llm" && (
        <div>
          <label className="mb-1 block text-xs font-medium text-muted">System Prompt</label>
          <textarea
            value={step.llmSystemPrompt}
            onChange={(e) => onChange({ ...step, llmSystemPrompt: e.target.value })}
            rows={3}
            placeholder="Optional system instructions..."
            className={cn(inputClass, "h-auto py-2 resize-y")}
          />
        </div>
      )}

      {/* HTTP Config */}
      {step.stepType === "http" && (
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">URL</label>
            <input
              type="text"
              value={step.httpConfig.url}
              onChange={(e) => onChange({ ...step, httpConfig: { ...step.httpConfig, url: e.target.value } })}
              placeholder="https://api.example.com/data/{input.id}"
              className={inputClass}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Method</label>
            <select
              value={step.httpConfig.method}
              onChange={(e) => onChange({ ...step, httpConfig: { ...step.httpConfig, method: e.target.value } })}
              className={inputClass}
            >
              <option value="GET">GET</option>
              <option value="POST">POST</option>
              <option value="PUT">PUT</option>
              <option value="PATCH">PATCH</option>
              <option value="DELETE">DELETE</option>
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Auth</label>
            <input
              type="text"
              value={step.httpConfig.auth}
              onChange={(e) => onChange({ ...step, httpConfig: { ...step.httpConfig, auth: e.target.value } })}
              placeholder="bearer:{input.token} or ENV_VAR_NAME"
              className={inputClass}
            />
          </div>
          {(step.httpConfig.method === "POST" || step.httpConfig.method === "PUT" || step.httpConfig.method === "PATCH") && (
            <div>
              <label className="mb-1 block text-xs font-medium text-muted">Body</label>
              <textarea
                value={step.httpConfig.body}
                onChange={(e) => onChange({ ...step, httpConfig: { ...step.httpConfig, body: e.target.value } })}
                rows={4}
                placeholder='{"key": "value"}'
                className={cn(inputClass, "h-auto py-2 resize-y font-mono text-xs")}
              />
            </div>
          )}
        </div>
      )}

      {/* Code Config */}
      {step.stepType === "code" && (
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Python Code</label>
            <textarea
              value={step.codeConfig.code}
              onChange={(e) => onChange({ ...step, codeConfig: { ...step.codeConfig, code: e.target.value } })}
              rows={10}
              placeholder={'data = _steps["prev-step"]\nresult = [item["name"] for item in data]'}
              className={cn(inputClass, "h-auto py-2 resize-y font-mono text-xs")}
            />
            <p className="text-[11px] text-muted-foreground mt-0.5">
              {"Available: _input (workflow input), _steps (previous outputs), json module. Set 'result' variable for output."}
            </p>
          </div>
        </div>
      )}

      {/* Condition Config */}
      {step.stepType === "condition" && (
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Expression</label>
            <input
              type="text"
              value={step.conditionConfig.expression}
              onChange={(e) => onChange({ ...step, conditionConfig: { ...step.conditionConfig, expression: e.target.value } })}
              placeholder="steps['score']['value'] > 80"
              className={cn(inputClass, "font-mono text-xs")}
            />
            <p className="text-[11px] text-muted-foreground mt-0.5">
              {"Python expression. Available: steps (outputs dict), input (workflow input)."}
            </p>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Then (true) - Step IDs</label>
            <input
              type="text"
              value={step.conditionConfig.thenSteps.join(", ")}
              onChange={(e) => onChange({ ...step, conditionConfig: { ...step.conditionConfig, thenSteps: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) } })}
              placeholder="step-a, step-b"
              className={inputClass}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Else (false) - Step IDs</label>
            <input
              type="text"
              value={step.conditionConfig.elseSteps.join(", ")}
              onChange={(e) => onChange({ ...step, conditionConfig: { ...step.conditionConfig, elseSteps: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) } })}
              placeholder="step-c, step-d"
              className={inputClass}
            />
          </div>
        </div>
      )}

      {/* Classify Config */}
      {step.stepType === "classify" && (
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Input Text</label>
            <input
              type="text"
              value={step.classifyConfig.input}
              onChange={(e) => onChange({ ...step, classifyConfig: { ...step.classifyConfig, input: e.target.value } })}
              placeholder="{steps.parse.output.text}"
              className={inputClass}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Categories</label>
            <input
              type="text"
              value={step.classifyConfig.categories.join(", ")}
              onChange={(e) => onChange({ ...step, classifyConfig: { ...step.classifyConfig, categories: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) } })}
              placeholder="billing, technical, general"
              className={inputClass}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Model</label>
            <select
              value={step.classifyConfig.model}
              onChange={(e) => onChange({ ...step, classifyConfig: { ...step.classifyConfig, model: e.target.value } })}
              className={inputClass}
            >
              <option value="haiku">Haiku (cheapest)</option>
              <option value="sonnet">Sonnet</option>
            </select>
          </div>
        </div>
      )}

      {/* Loop Config */}
      {step.stepType === "loop" && (
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Iterate Over</label>
            <input
              type="text"
              value={step.loopConfig.over}
              onChange={(e) => onChange({ ...step, loopConfig: { ...step.loopConfig, over: e.target.value } })}
              placeholder="{steps.fetch.output.items}"
              className={inputClass}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Sub-Step IDs</label>
            <input
              type="text"
              value={step.loopConfig.stepIds.join(", ")}
              onChange={(e) => onChange({ ...step, loopConfig: { ...step.loopConfig, stepIds: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) } })}
              placeholder="enrich, score"
              className={inputClass}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Max Iterations</label>
            <input
              type="number"
              value={step.loopConfig.maxIterations}
              onChange={(e) => onChange({ ...step, loopConfig: { ...step.loopConfig, maxIterations: Number(e.target.value) } })}
              min={1}
              max={1000}
              className={inputClass}
            />
          </div>
        </div>
      )}

      {/* Race Config */}
      {step.stepType === "race" && (
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Branches (one per line, step IDs comma-separated)</label>
            <textarea
              value={step.raceConfig.branches}
              onChange={(e) => onChange({ ...step, raceConfig: { ...step.raceConfig, branches: e.target.value } })}
              rows={4}
              placeholder={"step-a, step-b\nstep-c, step-d"}
              className={cn(inputClass, "h-auto py-2 resize-y font-mono text-xs")}
            />
            <p className="text-[11px] text-muted-foreground mt-0.5">
              Each line is a branch of step IDs to execute sequentially. All branches run in parallel.
            </p>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Validator Expression</label>
            <input
              type="text"
              value={step.raceConfig.validator}
              onChange={(e) => onChange({ ...step, raceConfig: { ...step.raceConfig, validator: e.target.value } })}
              placeholder="len(output) > 0"
              className={cn(inputClass, "font-mono text-xs")}
            />
            <p className="text-[11px] text-muted-foreground mt-0.5">
              {"Optional. Python expression to validate branch output. Available: output."}
            </p>
          </div>
        </div>
      )}

      {/* Sensor Config */}
      {step.stepType === "sensor" && (
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">URL</label>
            <input
              type="text"
              value={step.sensorConfig.url}
              onChange={(e) => onChange({ ...step, sensorConfig: { ...step.sensorConfig, url: e.target.value } })}
              placeholder="https://api.example.com/status"
              className={inputClass}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Method</label>
            <select
              value={step.sensorConfig.method}
              onChange={(e) => onChange({ ...step, sensorConfig: { ...step.sensorConfig, method: e.target.value } })}
              className={inputClass}
            >
              <option value="GET">GET</option>
              <option value="POST">POST</option>
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Condition</label>
            <input
              type="text"
              value={step.sensorConfig.condition}
              onChange={(e) => onChange({ ...step, sensorConfig: { ...step.sensorConfig, condition: e.target.value } })}
              placeholder="response.get('status') == 'ready'"
              className={cn(inputClass, "font-mono text-xs")}
            />
            <p className="text-[11px] text-muted-foreground mt-0.5">
              {"Python expression. Available: response (parsed JSON), status_code."}
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-muted">Poll Interval (s)</label>
              <input
                type="number"
                value={step.sensorConfig.checkInterval}
                onChange={(e) => onChange({ ...step, sensorConfig: { ...step.sensorConfig, checkInterval: Number(e.target.value) } })}
                min={1}
                className={inputClass}
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted">Timeout (s)</label>
              <input
                type="number"
                value={step.sensorConfig.timeout}
                onChange={(e) => onChange({ ...step, sensorConfig: { ...step.sensorConfig, timeout: Number(e.target.value) } })}
                min={1}
                className={inputClass}
              />
            </div>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Headers (JSON)</label>
            <input
              type="text"
              value={step.sensorConfig.headers}
              onChange={(e) => onChange({ ...step, sensorConfig: { ...step.sensorConfig, headers: e.target.value } })}
              placeholder='{"Authorization": "Bearer ..."}'
              className={cn(inputClass, "font-mono text-xs")}
            />
          </div>
        </div>
      )}

      {/* Gate Config */}
      {step.stepType === "gate" && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <label className="text-xs font-medium text-muted">Strategies</label>
            <button
              type="button"
              onClick={() => {
                const newStrategy: GateStrategy = {
                  type: "llm_eval",
                  prompt: "",
                  input: "",
                  model: "haiku",
                  message: "",
                  timeoutHours: 24,
                  seconds: 60,
                  action: "approve",
                  onTimeout: "abort",
                };
                onChange({
                  ...step,
                  gateConfig: {
                    ...step.gateConfig,
                    strategies: [...step.gateConfig.strategies, newStrategy],
                  },
                });
              }}
              className="flex items-center gap-1 text-[11px] text-accent hover:text-accent-hover"
            >
              <Plus className="h-3 w-3" /> Add Strategy
            </button>
          </div>
          {step.gateConfig.strategies.map((strategy, idx) => (
            <div key={idx} className="rounded-lg border border-border p-2.5 space-y-2">
              <div className="flex items-center gap-2">
                <select
                  value={strategy.type}
                  onChange={(e) => {
                    const updated = [...step.gateConfig.strategies];
                    updated[idx] = { ...updated[idx], type: e.target.value as GateStrategy["type"] };
                    onChange({ ...step, gateConfig: { ...step.gateConfig, strategies: updated } });
                  }}
                  className={cn(inputClass, "flex-1")}
                >
                  <option value="llm_eval">LLM Eval</option>
                  <option value="human">Human Approval</option>
                  <option value="timeout">Timeout</option>
                </select>
                <button
                  type="button"
                  onClick={() => {
                    const updated = step.gateConfig.strategies.filter((_, i) => i !== idx);
                    onChange({ ...step, gateConfig: { ...step.gateConfig, strategies: updated } });
                  }}
                  className="text-muted hover:text-error"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>

              {strategy.type === "llm_eval" && (
                <>
                  <input
                    type="text"
                    value={strategy.prompt}
                    onChange={(e) => {
                      const updated = [...step.gateConfig.strategies];
                      updated[idx] = { ...updated[idx], prompt: e.target.value };
                      onChange({ ...step, gateConfig: { ...step.gateConfig, strategies: updated } });
                    }}
                    placeholder="Evaluation prompt..."
                    className={inputClass}
                  />
                  <input
                    type="text"
                    value={strategy.input}
                    onChange={(e) => {
                      const updated = [...step.gateConfig.strategies];
                      updated[idx] = { ...updated[idx], input: e.target.value };
                      onChange({ ...step, gateConfig: { ...step.gateConfig, strategies: updated } });
                    }}
                    placeholder="{steps.prev.output}"
                    className={inputClass}
                  />
                  <select
                    value={strategy.model}
                    onChange={(e) => {
                      const updated = [...step.gateConfig.strategies];
                      updated[idx] = { ...updated[idx], model: e.target.value };
                      onChange({ ...step, gateConfig: { ...step.gateConfig, strategies: updated } });
                    }}
                    className={inputClass}
                  >
                    <option value="haiku">Haiku</option>
                    <option value="sonnet">Sonnet</option>
                  </select>
                </>
              )}

              {strategy.type === "human" && (
                <>
                  <input
                    type="text"
                    value={strategy.message}
                    onChange={(e) => {
                      const updated = [...step.gateConfig.strategies];
                      updated[idx] = { ...updated[idx], message: e.target.value };
                      onChange({ ...step, gateConfig: { ...step.gateConfig, strategies: updated } });
                    }}
                    placeholder="Approval message..."
                    className={inputClass}
                  />
                  <input
                    type="number"
                    value={strategy.timeoutHours}
                    onChange={(e) => {
                      const updated = [...step.gateConfig.strategies];
                      updated[idx] = { ...updated[idx], timeoutHours: Number(e.target.value) };
                      onChange({ ...step, gateConfig: { ...step.gateConfig, strategies: updated } });
                    }}
                    min={1}
                    className={inputClass}
                  />
                </>
              )}

              {strategy.type === "timeout" && (
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="mb-0.5 block text-[11px] text-muted">Seconds</label>
                    <input
                      type="number"
                      value={strategy.seconds}
                      onChange={(e) => {
                        const updated = [...step.gateConfig.strategies];
                        updated[idx] = { ...updated[idx], seconds: Number(e.target.value) };
                        onChange({ ...step, gateConfig: { ...step.gateConfig, strategies: updated } });
                      }}
                      min={1}
                      className={inputClass}
                    />
                  </div>
                  <div>
                    <label className="mb-0.5 block text-[11px] text-muted">Action</label>
                    <select
                      value={strategy.action}
                      onChange={(e) => {
                        const updated = [...step.gateConfig.strategies];
                        updated[idx] = { ...updated[idx], action: e.target.value as "approve" | "reject" };
                        onChange({ ...step, gateConfig: { ...step.gateConfig, strategies: updated } });
                      }}
                      className={inputClass}
                    >
                      <option value="approve">Auto-approve</option>
                      <option value="reject">Auto-reject</option>
                    </select>
                  </div>
                </div>
              )}
            </div>
          ))}
          {step.gateConfig.strategies.length === 0 && (
            <p className="text-[11px] text-muted-foreground">No strategies yet. Add at least one.</p>
          )}
        </div>
      )}

      {/* Transform Config */}
      {step.stepType === "transform" && (
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Template</label>
            <textarea
              value={step.transformConfig.template}
              onChange={(e) => onChange({ ...step, transformConfig: { ...step.transformConfig, template: e.target.value } })}
              rows={10}
              placeholder={'{"summary": "{steps.analyze.output.summary}", "count": {steps.fetch.output.count}}'}
              className={cn(inputClass, "h-auto py-2 resize-y font-mono text-xs")}
            />
            <p className="text-[11px] text-muted-foreground mt-0.5">
              {"Use {steps.id.output} for previous step data, {input.field} for workflow input. Supports {{ var | tojson }} syntax."}
            </p>
          </div>
        </div>
      )}

      {/* Notify Config */}
      {step.stepType === "notify" && (
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Service</label>
            <select
              value={step.notifyConfig.service}
              onChange={(e) => onChange({ ...step, notifyConfig: { ...step.notifyConfig, service: e.target.value } })}
              className={inputClass}
            >
              <option value="">Select service...</option>
              <option value="slack">Slack</option>
              <option value="teams">Teams</option>
              <option value="gmail">Gmail</option>
              <option value="webhook">Webhook</option>
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Channel / Recipient</label>
            <input
              type="text"
              value={step.notifyConfig.channel}
              onChange={(e) => onChange({ ...step, notifyConfig: { ...step.notifyConfig, channel: e.target.value } })}
              placeholder="#alerts or user@example.com"
              className={inputClass}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Message</label>
            <textarea
              value={step.notifyConfig.message}
              onChange={(e) => onChange({ ...step, notifyConfig: { ...step.notifyConfig, message: e.target.value } })}
              rows={4}
              placeholder={"Workflow completed. Result: {steps.analyze.output.summary}"}
              className={cn(inputClass, "h-auto py-2 resize-y")}
            />
            <p className="text-[11px] text-muted-foreground mt-0.5">
              {"Use {steps.id.output} for previous step data, {input.field} for workflow input."}
            </p>
          </div>
        </div>
      )}

      {/* Delegate Config */}
      {step.stepType === "delegate" && (
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Target Workflow</label>
            <input
              type="text"
              value={step.delegateConfig.workflow}
              onChange={(e) => onChange({ ...step, delegateConfig: { ...step.delegateConfig, workflow: e.target.value } })}
              placeholder="data-enrichment"
              className={inputClass}
            />
            <p className="text-[11px] text-muted-foreground mt-0.5">
              Name of the workflow YAML file (without .yaml extension).
            </p>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Task Description</label>
            <textarea
              value={step.delegateConfig.taskDescription}
              onChange={(e) => onChange({ ...step, delegateConfig: { ...step.delegateConfig, taskDescription: e.target.value } })}
              rows={4}
              placeholder={"Enrich the data from {steps.fetch.output} with additional context."}
              className={cn(inputClass, "h-auto py-2 resize-y")}
            />
            <p className="text-[11px] text-muted-foreground mt-0.5">
              {"Natural language description. Use {steps.id.output} and {input.field} variables."}
            </p>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Timeout (seconds)</label>
            <input
              type="number"
              value={step.delegateConfig.timeout}
              onChange={(e) => onChange({ ...step, delegateConfig: { ...step.delegateConfig, timeout: Number(e.target.value) } })}
              min={60}
              max={86400}
              className={inputClass}
            />
          </div>
        </div>
      )}

      {/* Browser Config */}
      {step.stepType === "browser" && (
        <div className="space-y-3">
          {/* Mode-specific info box */}
          <div className={cn(
            "rounded-lg border p-3",
            step.browserConfig.mode === "dom"
              ? "border-success/30 bg-success/5"
              : step.browserConfig.mode === "computer_use"
              ? "border-warning/30 bg-warning/5"
              : "border-running/30 bg-running/5"
          )}>
            {step.browserConfig.mode === "playwright" && (
              <p className="text-xs text-muted">
                <span className="font-medium text-running">Playwright mode</span> uses CSS/XPath selectors for fast, reliable automation. Best for known page structures. Supports action caching for repeat visits.
              </p>
            )}
            {step.browserConfig.mode === "computer_use" && (
              <p className="text-xs text-muted">
                <span className="font-medium text-warning">Computer Use mode</span> uses AI vision to interact with pages via screenshots. Best for legacy systems without stable DOM. Includes CAPTCHA detection and post-action validation.
              </p>
            )}
            {step.browserConfig.mode === "dom" && (
              <p className="text-xs text-muted">
                <span className="font-medium text-success">DOM Extraction mode</span> uses the accessibility tree for fast, structured data extraction. 10x cheaper than vision. Best for scraping tables, forms, and structured content.
              </p>
            )}
          </div>

          {/* Mode selection - 3 options */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted">Mode</label>
            <div className="grid grid-cols-3 gap-2">
              {[
                { value: "playwright", label: "Playwright", desc: "Selector-based, fast" },
                { value: "computer_use", label: "Computer Use", desc: "Visual AI, screenshots" },
                { value: "dom", label: "DOM Extract", desc: "Accessibility tree, structured" },
              ].map((m) => (
                <button
                  key={m.value}
                  type="button"
                  onClick={() => onChange({ ...step, browserConfig: { ...step.browserConfig, mode: m.value } })}
                  className={cn(
                    "flex flex-col items-center gap-1 rounded-lg border p-2.5 text-xs transition-colors",
                    step.browserConfig.mode === m.value
                      ? "border-accent bg-accent/10 text-accent"
                      : "border-border text-muted hover:border-accent/50"
                  )}
                >
                  <span className="font-medium">{m.label}</span>
                  <span className="text-[10px] text-muted">{m.desc}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Start URL */}
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Start URL</label>
            <input
              type="text"
              value={step.browserConfig.startUrl}
              onChange={(e) => onChange({ ...step, browserConfig: { ...step.browserConfig, startUrl: e.target.value } })}
              placeholder="https://example.com/login"
              className={inputClass}
            />
            <p className="text-[11px] text-muted-foreground mt-0.5">
              {"Initial URL the browser navigates to. Supports {input.url} variables."}
            </p>
          </div>

          {/* Viewport */}
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Viewport</label>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="mb-0.5 block text-[11px] text-muted">Width</label>
                <input
                  type="number"
                  value={step.browserConfig.viewportWidth}
                  onChange={(e) => onChange({ ...step, browserConfig: { ...step.browserConfig, viewportWidth: Number(e.target.value) } })}
                  min={320}
                  max={3840}
                  className={inputClass}
                />
              </div>
              <div>
                <label className="mb-0.5 block text-[11px] text-muted">Height</label>
                <input
                  type="number"
                  value={step.browserConfig.viewportHeight}
                  onChange={(e) => onChange({ ...step, browserConfig: { ...step.browserConfig, viewportHeight: Number(e.target.value) } })}
                  min={240}
                  max={2160}
                  className={inputClass}
                />
              </div>
            </div>
          </div>

          {/* Timeout */}
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Timeout (seconds)</label>
            <input
              type="number"
              value={step.browserConfig.timeout}
              onChange={(e) => onChange({ ...step, browserConfig: { ...step.browserConfig, timeout: Number(e.target.value) } })}
              min={10}
              max={3600}
              className={inputClass}
            />
            <p className="text-[11px] text-muted-foreground mt-0.5">Maximum total time for the browser step to complete.</p>
          </div>

          {/* Wait after action */}
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Wait After Action (seconds)</label>
            <input
              type="number"
              value={step.browserConfig.waitAfterAction}
              onChange={(e) => onChange({ ...step, browserConfig: { ...step.browserConfig, waitAfterAction: Number(e.target.value) } })}
              min={0}
              max={30}
              step={0.1}
              className={inputClass}
            />
            <p className="text-[11px] text-muted-foreground mt-0.5">Delay between browser actions to let pages load.</p>
          </div>

          {/* Headless */}
          <label className="flex items-center gap-2 text-xs text-foreground">
            <input
              type="checkbox"
              checked={step.browserConfig.headless}
              onChange={(e) => onChange({ ...step, browserConfig: { ...step.browserConfig, headless: e.target.checked } })}
              className="rounded border-border text-accent focus-visible:ring-accent"
            />
            <span className="font-medium">Headless</span>
            <span className="text-muted-foreground">- Run browser without visible window</span>
          </label>

          {/* Credentials env var */}
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Credentials Env Var</label>
            <input
              type="text"
              value={step.browserConfig.credentials_env}
              onChange={(e) => onChange({ ...step, browserConfig: { ...step.browserConfig, credentials_env: e.target.value } })}
              placeholder="BROWSER_CREDENTIALS"
              className={cn(inputClass, "font-mono text-xs")}
            />
            <p className="text-[11px] text-muted-foreground mt-0.5">
              {"Environment variable name containing login credentials (JSON)."}
            </p>
          </div>

          {/* Screenshot on error */}
          <label className="flex items-center gap-2 text-xs text-foreground">
            <input
              type="checkbox"
              checked={step.browserConfig.screenshotOnError}
              onChange={(e) => onChange({ ...step, browserConfig: { ...step.browserConfig, screenshotOnError: e.target.checked } })}
              className="rounded border-border text-accent focus-visible:ring-accent"
            />
            <span className="font-medium">Screenshot on Error</span>
            <span className="text-muted-foreground">- Capture screenshot when step fails</span>
          </label>

          {/* Max Actions (Computer Use mode) */}
          {step.browserConfig.mode === "computer_use" && (
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted">Max Actions</label>
              <input
                type="number"
                value={step.browserConfig.max_actions || 100}
                onChange={(e) => onChange({ ...step, browserConfig: { ...step.browserConfig, max_actions: parseInt(e.target.value) || 100 } })}
                min={10}
                max={500}
                className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
              />
              <p className="text-[10px] text-muted">Safety limit for screenshot-action cycles</p>
            </div>
          )}

          {/* CAPTCHA Strategy */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted">CAPTCHA Strategy</label>
            <select
              value={step.browserConfig.captcha_strategy || "pause"}
              onChange={(e) => onChange({ ...step, browserConfig: { ...step.browserConfig, captcha_strategy: e.target.value } })}
              className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
            >
              <option value="pause">Pause for human (HITL)</option>
              <option value="skip">Skip and continue</option>
              <option value="fail">Fail step</option>
            </select>
            <p className="text-[10px] text-muted">What to do when CAPTCHA is detected</p>
          </div>

          {/* Capture Screenshots (for execution replay) */}
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-muted">Execution Replay</p>
              <p className="text-[10px] text-muted">Save screenshots after each action</p>
            </div>
            <button
              type="button"
              onClick={() => onChange({ ...step, browserConfig: { ...step.browserConfig, capture_screenshots: !step.browserConfig.capture_screenshots } })}
              className={cn(
                "relative h-5 w-9 rounded-full transition-colors",
                step.browserConfig.capture_screenshots ? "bg-accent" : "bg-border"
              )}
            >
              <span className={cn(
                "absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform",
                step.browserConfig.capture_screenshots ? "translate-x-4" : "translate-x-0.5"
              )} />
            </button>
          </div>

          {/* Output Schema (DOM mode) */}
          {step.browserConfig.mode === "dom" && (
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted">Output Schema (JSON)</label>
              <textarea
                value={step.browserConfig.output_schema ? JSON.stringify(step.browserConfig.output_schema, null, 2) : ""}
                onChange={(e) => {
                  try {
                    const schema = e.target.value ? JSON.parse(e.target.value) : null;
                    onChange({ ...step, browserConfig: { ...step.browserConfig, output_schema: schema } });
                  } catch {
                    // Invalid JSON, don't update
                  }
                }}
                placeholder='{"type": "object", "properties": {"invoices": {"type": "array"}}}'
                rows={4}
                className="w-full rounded-md border border-border bg-background px-3 py-1.5 font-mono text-xs"
              />
              <p className="text-[10px] text-muted">Define expected output structure for data extraction</p>
            </div>
          )}
        </div>
      )}

      {/* Standard agent settings - only shown for standard/llm/classify */}
      {(step.stepType === "standard" || step.stepType === "llm" || step.stepType === "classify") && (
        <>
      {/* Directory Input */}
      <div className="rounded-lg border border-border">
        <button
          type="button"
          onClick={() => {
            const next = !step.directoryInput.enabled;
            onChange({
              ...step,
              directoryInput: { ...step.directoryInput, enabled: next },
            });
          }}
          className="flex w-full items-center gap-2 px-3 py-2 text-left"
        >
          <FolderOpen className="h-3.5 w-3.5 text-muted" />
          <span className="flex-1 text-xs font-medium text-foreground">Directory Input</span>
          <input
            type="checkbox"
            checked={step.directoryInput.enabled}
            onChange={(e) => {
              e.stopPropagation();
              onChange({
                ...step,
                directoryInput: { ...step.directoryInput, enabled: e.target.checked },
              });
            }}
            className="rounded border-border text-accent focus-visible:ring-accent"
          />
        </button>
        {step.directoryInput.enabled && (
          <div className="border-t border-border px-3 py-2.5 space-y-2">
            <p className="text-[11px] text-muted-foreground">
              This step expects a directory path as input. Use <code className="text-[10px] bg-background px-1 rounded">{"{"} input.directory {"}"}</code> in your prompt.
            </p>
            <div className="flex gap-2">
              <input
                type="text"
                value={step.directoryInput.defaultPath}
                onChange={(e) =>
                  onChange({
                    ...step,
                    directoryInput: { ...step.directoryInput, defaultPath: e.target.value },
                  })
                }
                placeholder="~/Desktop"
                className={cn(inputClass, "text-xs")}
              />
              <button
                type="button"
                onClick={() => setBrowseOpen(true)}
                className="shrink-0 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium text-muted hover:text-foreground hover:border-accent transition-colors"
              >
                Browse
              </button>
            </div>
          </div>
        )}
      </div>

      <DirectoryBrowser
        open={browseOpen}
        initialPath={step.directoryInput.defaultPath || "~"}
        onSelect={(path) => {
          onChange({
            ...step,
            directoryInput: { ...step.directoryInput, defaultPath: path },
          });
          setBrowseOpen(false);
        }}
        onClose={() => setBrowseOpen(false)}
      />

      <div>
        <label className="mb-1 block text-xs font-medium text-muted">Model</label>
        <select
          value={step.model}
          onChange={(e) => onChange({ ...step, model: e.target.value })}
          className={inputClass}
        >
          <optgroup label="Claude (Anthropic)">
            <option value="sonnet">Sonnet</option>
            <option value="opus">Opus</option>
            <option value="haiku">Haiku</option>
          </optgroup>
          <optgroup label="OpenAI">
            <option value="openai/codex-mini">Codex Mini</option>
            <option value="openai/codex">Codex</option>
          </optgroup>
          <optgroup label="MiniMax">
            <option value="minimax/m2.5">MiniMax M2.5</option>
          </optgroup>
          <optgroup label="Google">
            <option value="google/gemini-2.5-pro">Gemini 2.5 Pro</option>
          </optgroup>
        </select>
        <p className="text-[11px] text-muted-foreground mt-0.5">Claude is default. Multi-provider routing supports OpenAI, MiniMax, and Google models.</p>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="mb-1 block text-xs font-medium text-muted">Max Turns</label>
          <input
            type="number"
            value={step.maxTurns}
            onChange={(e) => onChange({ ...step, maxTurns: Number(e.target.value) })}
            min={1}
            className={inputClass}
          />
          <p className="text-[11px] text-muted-foreground mt-0.5">Maximum agent conversation turns before timeout.</p>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-muted">Timeout (s)</label>
          <input
            type="number"
            value={step.timeout}
            onChange={(e) => onChange({ ...step, timeout: Number(e.target.value) })}
            min={1}
            className={inputClass}
          />
          <p className="text-[11px] text-muted-foreground mt-0.5">Hard time limit in seconds for this step.</p>
        </div>
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-muted">Parallel Over</label>
        <input
          type="text"
          value={step.parallelOver}
          onChange={(e) => onChange({ ...step, parallelOver: e.target.value })}
          placeholder="e.g. steps.scrape.output"
          className={inputClass}
        />
        <p className="text-[11px] text-muted-foreground mt-0.5">JSONPath to a list. Step runs once per item in parallel.</p>
      </div>
        </>
      )}

      <div>
        <label className="mb-1 block text-xs font-medium text-muted">Depends On</label>
        <div className="space-y-1">
          {allStepIds
            .filter((sid) => sid !== step.id)
            .map((sid) => (
              <label key={sid} className="flex items-center gap-2 text-sm text-foreground">
                <input
                  type="checkbox"
                  checked={step.dependsOn.includes(sid)}
                  onChange={(e) => {
                    const deps = e.target.checked
                      ? [...step.dependsOn, sid]
                      : step.dependsOn.filter((d) => d !== sid);
                    onChange({ ...step, dependsOn: deps });
                  }}
                  className="rounded border-border text-accent focus-visible:ring-accent"
                />
                {sid}
              </label>
            ))}
          {allStepIds.filter((sid) => sid !== step.id).length === 0 && (
            <p className="text-xs text-muted-foreground">No other steps to depend on</p>
          )}
        </div>
      </div>

      {/* Advanced sections */}
      <div className="space-y-2 pt-2">
        <p className="text-xs font-semibold text-muted">ADVANCED</p>

        {/* CSV Output */}
        <CollapsibleSection
          icon={FileSpreadsheet}
          title="CSV Export"
          enabled={step.csvOutput.enabled}
          onToggle={() =>
            onChange({ ...step, csvOutput: { ...step.csvOutput, enabled: !step.csvOutput.enabled } })
          }
        >
          <p className="text-[11px] text-muted-foreground">
            Export step output to a CSV file. Works with dicts, lists, or plain text.
          </p>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Directory</label>
            <div className="flex gap-2">
              <input
                type="text"
                value={step.csvOutput.directory}
                onChange={(e) =>
                  onChange({
                    ...step,
                    csvOutput: { ...step.csvOutput, directory: e.target.value },
                  })
                }
                placeholder="./output"
                className={cn(inputClass, "text-xs")}
              />
              <button
                type="button"
                onClick={() => setCsvBrowseOpen(true)}
                className="shrink-0 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium text-muted hover:text-foreground hover:border-accent transition-colors"
              >
                Browse
              </button>
            </div>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Mode</label>
            <select
              value={step.csvOutput.mode}
              onChange={(e) =>
                onChange({
                  ...step,
                  csvOutput: { ...step.csvOutput, mode: e.target.value as "append" | "new_file" },
                })
              }
              className={inputClass}
            >
              <option value="new_file">New file per run</option>
              <option value="append">Append to one file</option>
            </select>
            <p className="text-[11px] text-muted-foreground mt-0.5">
              {step.csvOutput.mode === "append"
                ? "All runs append rows to a single CSV file."
                : "Each run creates a new file with a timestamp in the name."}
            </p>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Filename</label>
            <input
              type="text"
              value={step.csvOutput.filename}
              onChange={(e) =>
                onChange({
                  ...step,
                  csvOutput: { ...step.csvOutput, filename: e.target.value },
                })
              }
              placeholder={step.id || "step-id"}
              className={cn(inputClass, "text-xs")}
            />
            <p className="text-[11px] text-muted-foreground mt-0.5">
              Without extension. Leave empty to use step ID.
            </p>
          </div>
        </CollapsibleSection>

        <DirectoryBrowser
          open={csvBrowseOpen}
          initialPath={step.csvOutput.directory || "~"}
          onSelect={(path) => {
            onChange({
              ...step,
              csvOutput: { ...step.csvOutput, directory: path },
            });
            setCsvBrowseOpen(false);
          }}
          onClose={() => setCsvBrowseOpen(false)}
        />

        {/* PDF Report */}
        <CollapsibleSection
          icon={FileText}
          title="PDF Report"
          enabled={step.pdfReport.enabled}
          onToggle={() =>
            onChange({ ...step, pdfReport: { ...step.pdfReport, enabled: !step.pdfReport.enabled } })
          }
        >
          <p className="text-[11px] text-muted-foreground">
            Generate a branded PDF report from the step output. The agent will format its response as structured markdown.
          </p>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Language</label>
            <select
              value={step.pdfReport.language}
              onChange={(e) =>
                onChange({
                  ...step,
                  pdfReport: { ...step.pdfReport, language: e.target.value },
                })
              }
              className={inputClass}
            >
              <option value="en">English</option>
              <option value="cs">Czech</option>
              <option value="de">German</option>
              <option value="es">Spanish</option>
              <option value="fr">French</option>
              <option value="ja">Japanese</option>
              <option value="zh">Chinese</option>
            </select>
            <p className="text-[11px] text-muted-foreground mt-0.5">The agent will write the report in this language.</p>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Directory</label>
            <div className="flex gap-2">
              <input
                type="text"
                value={step.pdfReport.directory}
                onChange={(e) =>
                  onChange({
                    ...step,
                    pdfReport: { ...step.pdfReport, directory: e.target.value },
                  })
                }
                placeholder="./output"
                className={cn(inputClass, "text-xs")}
              />
              <button
                type="button"
                onClick={() => setPdfBrowseOpen(true)}
                className="shrink-0 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium text-muted hover:text-foreground hover:border-accent transition-colors"
              >
                Browse
              </button>
            </div>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Filename</label>
            <input
              type="text"
              value={step.pdfReport.filename}
              onChange={(e) =>
                onChange({
                  ...step,
                  pdfReport: { ...step.pdfReport, filename: e.target.value },
                })
              }
              placeholder={step.id || "step-id"}
              className={cn(inputClass, "text-xs")}
            />
            <p className="text-[11px] text-muted-foreground mt-0.5">
              Without extension. Leave empty to use step ID.
            </p>
          </div>
        </CollapsibleSection>

        <DirectoryBrowser
          open={pdfBrowseOpen}
          initialPath={step.pdfReport.directory || "~"}
          onSelect={(path) => {
            onChange({
              ...step,
              pdfReport: { ...step.pdfReport, directory: path },
            });
            setPdfBrowseOpen(false);
          }}
          onClose={() => setPdfBrowseOpen(false)}
        />

        {/* Retry */}
        <CollapsibleSection
          icon={RefreshCw}
          title="Retry"
          enabled={step.retry.enabled}
          onToggle={() =>
            onChange({ ...step, retry: { ...step.retry, enabled: !step.retry.enabled } })
          }
        >
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Max Attempts</label>
            <input
              type="number"
              value={step.retry.maxAttempts}
              onChange={(e) =>
                onChange({ ...step, retry: { ...step.retry, maxAttempts: Number(e.target.value) } })
              }
              min={1}
              max={10}
              className={inputClass}
            />
            <p className="text-[11px] text-muted-foreground mt-0.5">Total tries including the first attempt.</p>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Backoff</label>
            <select
              value={step.retry.backoff}
              onChange={(e) =>
                onChange({
                  ...step,
                  retry: { ...step.retry, backoff: e.target.value as "exponential" | "fixed" },
                })
              }
              className={inputClass}
            >
              <option value="exponential">Exponential</option>
              <option value="fixed">Fixed</option>
            </select>
            <p className="text-[11px] text-muted-foreground mt-0.5">Exponential doubles delay each retry. Fixed waits the same interval.</p>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">On Failure</label>
            <select
              value={step.retry.onFailure}
              onChange={(e) =>
                onChange({
                  ...step,
                  retry: {
                    ...step.retry,
                    onFailure: e.target.value as "abort" | "skip" | "fallback",
                  },
                })
              }
              className={inputClass}
            >
              <option value="abort">Abort</option>
              <option value="skip">Skip</option>
              <option value="fallback">Fallback</option>
            </select>
            <p className="text-[11px] text-muted-foreground mt-0.5">Abort stops the run. Skip continues to next step. Fallback uses a simpler model.</p>
          </div>
        </CollapsibleSection>

        {/* AutoPilot */}
        <CollapsibleSection
          icon={FlaskConical}
          title="AutoPilot"
          enabled={step.autopilot.enabled}
          onToggle={() =>
            onChange({ ...step, autopilot: { ...step.autopilot, enabled: !step.autopilot.enabled } })
          }
        >
          <p className="text-[11px] text-muted-foreground">
            A/B test model variants automatically. Each run picks a variant, evaluates quality, and picks a winner after enough samples.
          </p>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Optimize For</label>
            <select
              value={step.autopilot.optimizeFor}
              onChange={(e) =>
                onChange({
                  ...step,
                  autopilot: {
                    ...step.autopilot,
                    optimizeFor: e.target.value as AutoPilotConfig["optimizeFor"],
                  },
                })
              }
              className={inputClass}
            >
              <option value="quality">Quality</option>
              <option value="cost">Cost</option>
              <option value="latency">Latency</option>
              <option value="pareto">Pareto (balanced)</option>
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Evaluation Method</label>
            <select
              value={step.autopilot.evaluation}
              onChange={(e) =>
                onChange({
                  ...step,
                  autopilot: {
                    ...step.autopilot,
                    evaluation: e.target.value as AutoPilotConfig["evaluation"],
                  },
                })
              }
              className={inputClass}
            >
              <option value="llm_judge">LLM Judge</option>
              <option value="schema_completeness">Schema Completeness</option>
            </select>
            <p className="text-[11px] text-muted-foreground mt-0.5">LLM Judge uses Haiku to score output quality. Schema checks field completeness.</p>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-muted">Min Samples</label>
              <input
                type="number"
                value={step.autopilot.minSamples}
                onChange={(e) =>
                  onChange({
                    ...step,
                    autopilot: { ...step.autopilot, minSamples: Number(e.target.value) },
                  })
                }
                min={2}
                className={inputClass}
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted">Sample Rate</label>
              <input
                type="number"
                value={step.autopilot.sampleRate}
                onChange={(e) =>
                  onChange({
                    ...step,
                    autopilot: { ...step.autopilot, sampleRate: Number(e.target.value) },
                  })
                }
                min={0}
                max={1}
                step={0.1}
                className={inputClass}
              />
              <p className="text-[11px] text-muted-foreground mt-0.5">Fraction of runs to test (1.0 = all).</p>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-muted">Quality Threshold</label>
              <input
                type="number"
                value={step.autopilot.qualityThreshold}
                onChange={(e) =>
                  onChange({
                    ...step,
                    autopilot: { ...step.autopilot, qualityThreshold: Number(e.target.value) },
                  })
                }
                min={0}
                max={1}
                step={0.1}
                className={inputClass}
              />
            </div>
            <div className="flex items-end pb-1">
              <label className="flex items-center gap-2 text-sm text-foreground">
                <input
                  type="checkbox"
                  checked={step.autopilot.autoDeploy}
                  onChange={(e) =>
                    onChange({
                      ...step,
                      autopilot: { ...step.autopilot, autoDeploy: e.target.checked },
                    })
                  }
                  className="rounded border-border text-accent focus-visible:ring-accent"
                />
                <span className="text-xs">Auto-deploy winner</span>
              </label>
            </div>
          </div>

          {/* Variants */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="text-xs font-medium text-muted">Variants</label>
              <button
                type="button"
                onClick={() => {
                  const idx = step.autopilot.variants.length + 1;
                  const newVariant: AutoPilotVariant = {
                    id: `variant-${idx}`,
                    model: idx === 1 ? "haiku" : idx === 2 ? "opus" : "sonnet",
                    prompt: "",
                    maxTurns: null,
                  };
                  onChange({
                    ...step,
                    autopilot: {
                      ...step.autopilot,
                      variants: [...step.autopilot.variants, newVariant],
                    },
                  });
                }}
                className="flex items-center gap-1 text-[11px] text-accent hover:text-accent-hover transition-colors"
              >
                <Plus className="h-3 w-3" />
                Add
              </button>
            </div>
            {step.autopilot.variants.length === 0 && (
              <p className="text-[11px] text-muted-foreground">Add at least 2 variants to compare.</p>
            )}
            <div className="space-y-2">
              {step.autopilot.variants.map((v, idx) => (
                <div key={idx} className="rounded-md border border-border p-2 space-y-1.5">
                  <div className="flex items-center gap-2">
                    <input
                      type="text"
                      value={v.id}
                      onChange={(e) => {
                        const variants = [...step.autopilot.variants];
                        variants[idx] = { ...v, id: e.target.value };
                        onChange({ ...step, autopilot: { ...step.autopilot, variants } });
                      }}
                      placeholder="variant-id"
                      className={cn(inputClass, "h-7 text-xs")}
                    />
                    <select
                      value={v.model}
                      onChange={(e) => {
                        const variants = [...step.autopilot.variants];
                        variants[idx] = { ...v, model: e.target.value };
                        onChange({ ...step, autopilot: { ...step.autopilot, variants } });
                      }}
                      className={cn(inputClass, "h-7 text-xs w-32 shrink-0")}
                    >
                      <optgroup label="Claude">
                        <option value="haiku">Haiku</option>
                        <option value="sonnet">Sonnet</option>
                        <option value="opus">Opus</option>
                      </optgroup>
                      <optgroup label="OpenAI">
                        <option value="openai/codex-mini">Codex Mini</option>
                        <option value="openai/codex">Codex</option>
                      </optgroup>
                      <optgroup label="MiniMax">
                        <option value="minimax/m2.5">M2.5</option>
                      </optgroup>
                      <optgroup label="Google">
                        <option value="google/gemini-2.5-pro">Gemini 2.5</option>
                      </optgroup>
                    </select>
                    <button
                      type="button"
                      onClick={() => {
                        const variants = step.autopilot.variants.filter((_, i) => i !== idx);
                        onChange({ ...step, autopilot: { ...step.autopilot, variants } });
                      }}
                      className="shrink-0 p-0.5 text-muted hover:text-error transition-colors"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                  <textarea
                    value={v.prompt}
                    onChange={(e) => {
                      const variants = [...step.autopilot.variants];
                      variants[idx] = { ...v, prompt: e.target.value };
                      onChange({ ...step, autopilot: { ...step.autopilot, variants } });
                    }}
                    placeholder="Custom prompt (leave empty to use step prompt)"
                    rows={2}
                    className={cn(inputClass, "h-auto py-1 text-xs resize-y")}
                  />
                </div>
              ))}
            </div>
          </div>
        </CollapsibleSection>

        {/* Approval Gate */}
        <CollapsibleSection
          icon={ShieldCheck}
          title="Approval Gate"
          enabled={step.approval.enabled}
          onToggle={() =>
            onChange({
              ...step,
              approval: { ...step.approval, enabled: !step.approval.enabled },
            })
          }
        >
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Message</label>
            <input
              type="text"
              value={step.approval.message}
              onChange={(e) =>
                onChange({
                  ...step,
                  approval: { ...step.approval, message: e.target.value },
                })
              }
              placeholder="Review before proceeding"
              className={inputClass}
            />
            <p className="text-[11px] text-muted-foreground mt-0.5">Shown to reviewer in the Approvals dashboard.</p>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Timeout (hours)</label>
            <input
              type="number"
              value={step.approval.timeoutHours}
              onChange={(e) =>
                onChange({
                  ...step,
                  approval: { ...step.approval, timeoutHours: Number(e.target.value) },
                })
              }
              min={1}
              className={inputClass}
            />
            <p className="text-[11px] text-muted-foreground mt-0.5">Auto-resolves if nobody responds within this time.</p>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">On Timeout</label>
            <select
              value={step.approval.onTimeout}
              onChange={(e) =>
                onChange({
                  ...step,
                  approval: {
                    ...step.approval,
                    onTimeout: e.target.value as "abort" | "skip",
                  },
                })
              }
              className={inputClass}
            >
              <option value="abort">Abort</option>
              <option value="skip">Skip</option>
            </select>
            <p className="text-[11px] text-muted-foreground mt-0.5">What happens when approval times out.</p>
          </div>
          <label className="flex items-center gap-2 text-sm text-foreground">
            <input
              type="checkbox"
              checked={step.approval.allowEdit}
              onChange={(e) =>
                onChange({
                  ...step,
                  approval: { ...step.approval, allowEdit: e.target.checked },
                })
              }
              className="rounded border-border text-accent focus-visible:ring-accent"
            />
            <span className="text-xs">Allow reviewer edits</span>
          </label>
          <p className="text-[11px] text-muted-foreground mt-0.5">Reviewer can modify the step's output data before approving.</p>
        </CollapsibleSection>

        {/* Tool Connectors - always openable, checkbox clears selection */}
        <CollapsibleSection
          icon={Wrench}
          title={`Tool Connectors${step.tools.length > 0 ? ` (${step.tools.length})` : ""}`}
          enabled
          onToggle={() => {
            if (step.tools.length > 0) {
              onChange({ ...step, tools: [] });
            }
          }}
        >
          <p className="text-[11px] text-muted-foreground mb-1">
            Click tools below to give this step access to external integrations.
            The agent can call their functions during execution.
          </p>
          {step.tools.length > 0 && (
            <div className="rounded-md bg-accent/5 border border-accent/20 px-2.5 py-1.5 mb-2">
              <p className="text-[10px] font-mono text-accent">
                tools: [{step.tools.join(", ")}]
              </p>
            </div>
          )}
          <ToolSelector
            selected={step.tools}
            onChange={(tools) => onChange({ ...step, tools })}
            compact
          />
          <p className="text-[10px] text-muted-foreground/60 mt-1.5">
            Use <code className="text-accent/70">tool:connection</code> for named connections (e.g. postgresql:analytics).
            Manage credentials on the Integrations page.
          </p>
        </CollapsibleSection>

        {/* Policies */}
        <CollapsibleSection
          icon={ShieldAlert}
          title="Policies"
          enabled={step.policies.length > 0}
          onToggle={() => {
            if (step.policies.length > 0) {
              onChange({ ...step, policies: [] });
            } else {
              onChange({ ...step, policies: ["pii-redact"] });
            }
          }}
        >
          <p className="text-[11px] text-muted-foreground">Rules evaluated against step output. Violations are logged.</p>
          <div className="space-y-2">
            {POLICY_OPTIONS.map((p) => (
              <div key={p.id}>
                <label className="flex items-center gap-2 text-sm text-foreground">
                  <input
                    type="checkbox"
                    checked={step.policies.includes(p.id)}
                    onChange={(e) => {
                      const policies = e.target.checked
                        ? [...step.policies, p.id]
                        : step.policies.filter((x) => x !== p.id);
                      onChange({ ...step, policies });
                    }}
                    className="rounded border-border text-accent focus-visible:ring-accent"
                  />
                  <span className="text-xs">{p.label}</span>
                </label>
                <p className="text-[11px] text-muted-foreground ml-6">{p.hint}</p>
              </div>
            ))}
          </div>
          <div className="flex gap-2">
            <input
              type="text"
              value={customPolicy}
              onChange={(e) => setCustomPolicy(e.target.value)}
              placeholder="Custom policy..."
              className={cn(inputClass, "text-xs")}
            />
            <button
              type="button"
              onClick={() => {
                if (customPolicy.trim() && !step.policies.includes(customPolicy.trim())) {
                  onChange({ ...step, policies: [...step.policies, customPolicy.trim()] });
                  setCustomPolicy("");
                }
              }}
              className="shrink-0 rounded-lg border border-border px-2 py-1 text-xs font-medium text-muted hover:text-foreground transition-colors"
            >
              Add
            </button>
          </div>
          {step.policies
            .filter((p) => !POLICY_OPTIONS.some((o) => o.id === p))
            .map((p) => (
              <div key={p} className="flex items-center justify-between">
                <span className="text-xs text-foreground">{p}</span>
                <button
                  type="button"
                  onClick={() =>
                    onChange({ ...step, policies: step.policies.filter((x) => x !== p) })
                  }
                  className="text-xs text-error/70 hover:text-error"
                >
                  Remove
                </button>
              </div>
            ))}
        </CollapsibleSection>

        {/* SLO Optimizer */}
        <CollapsibleSection
          icon={Gauge}
          title="SLO Optimizer"
          enabled={step.slo.enabled}
          onToggle={() =>
            onChange({ ...step, slo: { ...step.slo, enabled: !step.slo.enabled } })
          }
        >
          <p className="text-[11px] text-muted-foreground">Automatically selects the best model based on constraints.</p>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">
              Min Quality (0-1)
            </label>
            <input
              type="number"
              value={step.slo.qualityMin}
              onChange={(e) =>
                onChange({
                  ...step,
                  slo: { ...step.slo, qualityMin: Number(e.target.value) },
                })
              }
              min={0}
              max={1}
              step={0.1}
              className={inputClass}
            />
            <p className="text-[11px] text-muted-foreground mt-0.5">Minimum acceptable quality score (0 = any, 1 = perfect).</p>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">
              Max Cost (USD)
            </label>
            <input
              type="number"
              value={step.slo.costMaxUsd}
              onChange={(e) =>
                onChange({
                  ...step,
                  slo: { ...step.slo, costMaxUsd: Number(e.target.value) },
                })
              }
              min={0}
              step={0.01}
              className={inputClass}
            />
            <p className="text-[11px] text-muted-foreground mt-0.5">Per-step spending limit in USD.</p>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">
              Max Latency (s)
            </label>
            <input
              type="number"
              value={step.slo.latencyMaxSeconds}
              onChange={(e) =>
                onChange({
                  ...step,
                  slo: { ...step.slo, latencyMaxSeconds: Number(e.target.value) },
                })
              }
              min={1}
              className={inputClass}
            />
            <p className="text-[11px] text-muted-foreground mt-0.5">Maximum allowed execution time.</p>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Optimize For</label>
            <select
              value={step.slo.optimizeFor}
              onChange={(e) =>
                onChange({
                  ...step,
                  slo: {
                    ...step.slo,
                    optimizeFor: e.target.value as "cost" | "quality" | "latency" | "balanced",
                  },
                })
              }
              className={inputClass}
            >
              <option value="balanced">Balanced</option>
              <option value="cost">Cost</option>
              <option value="quality">Quality</option>
              <option value="latency">Latency</option>
            </select>
            <p className="text-[11px] text-muted-foreground mt-0.5">Primary objective when multiple models meet constraints.</p>
          </div>
        </CollapsibleSection>
      </div>

      <button
        onClick={onDelete}
        className="flex w-full items-center justify-center gap-2 rounded-lg border border-error/30 px-3 py-2 text-xs font-medium text-error hover:bg-error/10 transition-colors"
      >
        <Trash2 className="h-3.5 w-3.5" />
        Delete Step
      </button>
    </div>
  );
}
