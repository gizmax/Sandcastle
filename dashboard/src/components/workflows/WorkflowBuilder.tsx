import { useCallback, useEffect, useMemo, useState } from "react";
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
import { Bell, Brain, Plus, FileText, Play, Save, Monitor, Layers, Wand2, Wrench, RefreshCw, Globe, Code, GitBranch, Tag, Repeat, MessageSquare, Zap, Radio, ShieldCheck, Shuffle, ExternalLink, Sparkles, Plug, ChevronDown, Bot, Search, BarChart, PenTool, Server, TrendingUp, Database, Download, FileEdit, DollarSign, Clipboard, GraduationCap, X, ArrowRight } from "lucide-react";
import { StepNode } from "@/components/workflows/StepNode";
import {
  StepConfigPanel,
  type StepConfig,
  type StepType,
  type CsvOutputConfig,
  type PdfReportConfig,
  type RetryConfig,
  type ApprovalConfig,
  type SloConfig,
  type AutoPilotConfig,
  type GateStepConfig,
  type BrowserStepConfig,
  type AgentStepConfig,
} from "@/components/workflows/StepConfigPanel";
import { YamlPreview } from "@/components/workflows/YamlPreview";
import { TemplateBrowser } from "@/components/workflows/TemplateBrowser";
import { GenerateChatPanel } from "@/components/workflows/GenerateChatPanel";
import { ToolSelector } from "@/components/workflows/ToolSelector";
import { cn } from "@/lib/utils";
import { ErrorBoundary } from "@/components/shared/ErrorBoundary";
import { PaletteItem } from "@/components/workflows/PaletteItem";
import { getNextStepSuggestions } from "@/components/workflows/nextStepSuggestions";
import { getStepMeta } from "@/lib/builder/stepMetadata";
import jsYaml from "js-yaml";

/** localStorage key persisting the palette "Learn mode" toggle. */
const LEARN_MODE_KEY = "sandcastle-builder-learn";

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
const DEFAULT_HTTP_CONFIG = { url: "", method: "GET", headers: {}, body: "", auth: "" };
const DEFAULT_CODE_CONFIG = { code: "", language: "python" };
const DEFAULT_CONDITION_CONFIG = { expression: "", thenSteps: [] as string[], elseSteps: [] as string[] };
const DEFAULT_CLASSIFY_CONFIG = { categories: [] as string[], input: "", model: "haiku", branches: {} as Record<string, string[]> };
const DEFAULT_LOOP_CONFIG = { over: "", stepIds: [] as string[], maxIterations: 100 };
const DEFAULT_RACE_CONFIG = { branches: "", validator: "" };
const DEFAULT_SENSOR_CONFIG = { url: "", method: "GET", headers: "", checkInterval: 30, timeout: 1800, condition: "" };
const DEFAULT_GATE_CONFIG = { strategies: [] as GateStepConfig["strategies"] };
const DEFAULT_TRANSFORM_CONFIG = { template: "" };
const DEFAULT_NOTIFY_CONFIG = { service: "", channel: "", message: "" };
const DEFAULT_DELEGATE_CONFIG = { workflow: "", taskDescription: "", timeout: 3600 };
const DEFAULT_BROWSER_CONFIG: BrowserStepConfig = { mode: "playwright", startUrl: "", viewportWidth: 1280, viewportHeight: 720, timeout: 120, waitAfterAction: 1.0, headless: true, credentials_env: "", screenshotOnError: true, max_actions: 100, capture_screenshots: false, output_schema: null, captcha_strategy: "pause" };
const DEFAULT_AGENT_CONFIG: AgentStepConfig = { template: "", runtime: "auto", message: "", describe: "", timeout: 600, outputFormat: "text", fallbackTemplate: "" };

/** Serialize a string safely for its position as a YAML scalar. */
function yamlScalar(value: string): string {
  return jsYaml.dump(value, {
    forceQuotes: true,
    quotingType: '"',
    lineWidth: -1,
    noRefs: true,
  }).trimEnd();
}

function yamlInlineList(values: string[]): string {
  return values.map(yamlScalar).join(", ");
}

function generateYaml(
  workflowName: string,
  steps: StepConfig[],
  edges: Edge[],
  defaultTools: string[] = []
): string {
  const depMap = new Map<string, string[]>();
  edges.forEach((e) => {
    if (!depMap.has(e.target)) depMap.set(e.target, []);
    depMap.get(e.target)!.push(e.source);
  });

  // Check if any step uses directory input
  const hasDirInput = steps.some((s) => s.directoryInput.enabled);

  // Determine the most common model across steps as default
  const modelCounts = new Map<string, number>();
  steps.forEach((s) => {
    modelCounts.set(s.model, (modelCounts.get(s.model) || 0) + 1);
  });
  const defaultModel = [...modelCounts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || "sonnet";

  let yaml = `name: ${yamlScalar(workflowName)}\n`;
  yaml += `description: ""\n`;
  yaml += `default_model: ${yamlScalar(defaultModel)}\n`;
  yaml += `default_max_turns: 10\n`;
  yaml += `default_timeout: 300\n`;
  if (defaultTools.length > 0) {
    yaml += `default_tools: [${yamlInlineList(defaultTools)}]\n`;
  }
  yaml += `\n`;

  if (hasDirInput) {
    const dirStep = steps.find((s) => s.directoryInput.enabled)!;
    yaml += `input_schema:\n`;
    yaml += `  required: ["directory"]\n`;
    yaml += `  properties:\n`;
    yaml += `    directory:\n`;
    yaml += `      type: string\n`;
    yaml += `      description: "Path to directory"\n`;
    if (dirStep.directoryInput.defaultPath) {
      yaml += `      default: ${yamlScalar(dirStep.directoryInput.defaultPath)}\n`;
    }
    yaml += `\n`;
  }

  yaml += `steps:\n`;

  for (const step of steps) {
    const sType = step.stepType || "standard";

    // Approval gate steps have a different type
    if (step.approval.enabled) {
      yaml += `  - id: ${yamlScalar(step.id)}\n`;
      yaml += `    type: approval\n`;
      yaml += `    approval_config:\n`;
      yaml += `      message: ${yamlScalar(step.approval.message)}\n`;
      yaml += `      timeout_hours: ${step.approval.timeoutHours}\n`;
      yaml += `      on_timeout: ${yamlScalar(step.approval.onTimeout)}\n`;
      yaml += `      allow_edit: ${step.approval.allowEdit}\n`;
    } else if (sType === "http") {
      yaml += `  - id: ${yamlScalar(step.id)}\n`;
      yaml += `    type: http\n`;
      yaml += `    http_config:\n`;
      yaml += `      url: ${yamlScalar(step.httpConfig.url)}\n`;
      yaml += `      method: ${yamlScalar(step.httpConfig.method)}\n`;
      if (step.httpConfig.auth) {
        yaml += `      auth: ${yamlScalar(step.httpConfig.auth)}\n`;
      }
      if (step.httpConfig.body) {
        yaml += `      body: |\n`;
        step.httpConfig.body.split("\n").forEach((line) => {
          yaml += `        ${line}\n`;
        });
      }
    } else if (sType === "code") {
      yaml += `  - id: ${yamlScalar(step.id)}\n`;
      yaml += `    type: code\n`;
      yaml += `    code_config:\n`;
      yaml += `      language: ${yamlScalar(step.codeConfig.language)}\n`;
      yaml += `      code: |\n`;
      step.codeConfig.code.split("\n").forEach((line) => {
        yaml += `        ${line}\n`;
      });
    } else if (sType === "condition") {
      yaml += `  - id: ${yamlScalar(step.id)}\n`;
      yaml += `    type: condition\n`;
      yaml += `    condition_config:\n`;
      yaml += `      expression: ${yamlScalar(step.conditionConfig.expression)}\n`;
      if (step.conditionConfig.thenSteps.length > 0) {
        yaml += `      then: [${yamlInlineList(step.conditionConfig.thenSteps)}]\n`;
      }
      if (step.conditionConfig.elseSteps.length > 0) {
        yaml += `      else: [${yamlInlineList(step.conditionConfig.elseSteps)}]\n`;
      }
    } else if (sType === "classify") {
      yaml += `  - id: ${yamlScalar(step.id)}\n`;
      yaml += `    type: classify\n`;
      yaml += `    classify_config:\n`;
      yaml += `      categories: [${yamlInlineList(step.classifyConfig.categories)}]\n`;
      yaml += `      input: ${yamlScalar(step.classifyConfig.input)}\n`;
      yaml += `      model: ${yamlScalar(step.classifyConfig.model)}\n`;
      if (Object.keys(step.classifyConfig.branches).length > 0) {
        yaml += `      branches:\n`;
        for (const [cat, branchSteps] of Object.entries(step.classifyConfig.branches)) {
          yaml += `        ${yamlScalar(cat)}: [${yamlInlineList(branchSteps)}]\n`;
        }
      }
    } else if (sType === "loop") {
      yaml += `  - id: ${yamlScalar(step.id)}\n`;
      yaml += `    type: loop\n`;
      yaml += `    loop_config:\n`;
      yaml += `      over: ${yamlScalar(step.loopConfig.over)}\n`;
      yaml += `      step_ids: [${yamlInlineList(step.loopConfig.stepIds)}]\n`;
      yaml += `      max_iterations: ${step.loopConfig.maxIterations}\n`;
    } else if (sType === "race") {
      yaml += `  - id: ${yamlScalar(step.id)}\n`;
      yaml += `    type: race\n`;
      yaml += `    race_config:\n`;
      yaml += `      branches:\n`;
      const branchLines = step.raceConfig.branches.split("\n").filter((l: string) => l.trim());
      for (const line of branchLines) {
        const stepIds = line.split(",").map((s: string) => s.trim()).filter(Boolean);
        yaml += `        - [${yamlInlineList(stepIds)}]\n`;
      }
      if (step.raceConfig.validator) {
        yaml += `      validator: ${yamlScalar(step.raceConfig.validator)}\n`;
      }
    } else if (sType === "sensor") {
      yaml += `  - id: ${yamlScalar(step.id)}\n`;
      yaml += `    type: sensor\n`;
      yaml += `    sensor_config:\n`;
      yaml += `      url: ${yamlScalar(step.sensorConfig.url)}\n`;
      yaml += `      method: ${yamlScalar(step.sensorConfig.method)}\n`;
      yaml += `      condition: ${yamlScalar(step.sensorConfig.condition)}\n`;
      yaml += `      check_interval: ${step.sensorConfig.checkInterval}\n`;
      yaml += `      timeout: ${step.sensorConfig.timeout}\n`;
      if (step.sensorConfig.headers) {
        try {
          const hdrs = JSON.parse(step.sensorConfig.headers);
          if (Object.keys(hdrs).length > 0) {
            yaml += `      headers:\n`;
            for (const [k, v] of Object.entries(hdrs)) {
              yaml += `        ${yamlScalar(k)}: ${yamlScalar(String(v))}\n`;
            }
          }
        } catch { /* skip invalid JSON */ }
      }
    } else if (sType === "gate") {
      yaml += `  - id: ${yamlScalar(step.id)}\n`;
      yaml += `    type: gate\n`;
      yaml += `    gate_config:\n`;
      yaml += `      strategies:\n`;
      for (const strategy of step.gateConfig.strategies) {
        yaml += `        - type: ${yamlScalar(strategy.type)}\n`;
        yaml += `          config:\n`;
        if (strategy.type === "llm_eval") {
          if (strategy.prompt) yaml += `            prompt: ${yamlScalar(strategy.prompt)}\n`;
          if (strategy.input) yaml += `            input: ${yamlScalar(strategy.input)}\n`;
          yaml += `            model: ${yamlScalar(strategy.model)}\n`;
        } else if (strategy.type === "human") {
          if (strategy.message) yaml += `            message: ${yamlScalar(strategy.message)}\n`;
          yaml += `            timeout_hours: ${strategy.timeoutHours}\n`;
          yaml += `            on_timeout: ${yamlScalar(strategy.onTimeout)}\n`;
        } else if (strategy.type === "timeout") {
          yaml += `            seconds: ${strategy.seconds}\n`;
          yaml += `            action: ${yamlScalar(strategy.action)}\n`;
        }
      }
    } else if (sType === "transform") {
      yaml += `  - id: ${yamlScalar(step.id)}\n`;
      yaml += `    type: transform\n`;
      yaml += `    transform_config:\n`;
      yaml += `      template: |\n`;
      step.transformConfig.template.split("\n").forEach((line) => {
        yaml += `        ${line}\n`;
      });
    } else if (sType === "notify") {
      yaml += `  - id: ${yamlScalar(step.id)}\n`;
      yaml += `    type: notify\n`;
      yaml += `    notify_config:\n`;
      yaml += `      service: ${yamlScalar(step.notifyConfig.service)}\n`;
      if (step.notifyConfig.channel) {
        yaml += `      channel: ${yamlScalar(step.notifyConfig.channel)}\n`;
      }
      yaml += `      message: |\n`;
      step.notifyConfig.message.split("\n").forEach((line) => {
        yaml += `        ${line}\n`;
      });
    } else if (sType === "delegate") {
      yaml += `  - id: ${yamlScalar(step.id)}\n`;
      yaml += `    type: delegate\n`;
      yaml += `    delegate_config:\n`;
      yaml += `      workflow: ${yamlScalar(step.delegateConfig.workflow)}\n`;
      if (step.delegateConfig.taskDescription) {
        yaml += `      task_description: |\n`;
        step.delegateConfig.taskDescription.split("\n").forEach((line) => {
          yaml += `        ${line}\n`;
        });
      }
      if (step.delegateConfig.timeout !== 3600) {
        yaml += `      timeout: ${step.delegateConfig.timeout}\n`;
      }
    } else if (sType === "browser") {
      yaml += `  - id: ${yamlScalar(step.id)}\n`;
      yaml += `    type: browser\n`;
      yaml += `    browser_config:\n`;
      yaml += `      mode: ${yamlScalar(step.browserConfig.mode)}\n`;
      if (step.browserConfig.startUrl) {
        yaml += `      start_url: ${yamlScalar(step.browserConfig.startUrl)}\n`;
      }
      if (step.browserConfig.viewportWidth !== 1280 || step.browserConfig.viewportHeight !== 720) {
        yaml += `      viewport: [${step.browserConfig.viewportWidth}, ${step.browserConfig.viewportHeight}]\n`;
      }
      if (step.browserConfig.timeout !== 120) {
        yaml += `      timeout: ${step.browserConfig.timeout}\n`;
      }
      if (step.browserConfig.waitAfterAction !== 1.0) {
        yaml += `      wait_after_action: ${step.browserConfig.waitAfterAction}\n`;
      }
      if (!step.browserConfig.headless) {
        yaml += `      headless: false\n`;
      }
      if (step.browserConfig.credentials_env) {
        yaml += `      credentials_env: ${yamlScalar(step.browserConfig.credentials_env)}\n`;
      }
      if (!step.browserConfig.screenshotOnError) {
        yaml += `      screenshot_on_error: false\n`;
      }
      if (step.browserConfig.max_actions !== 100) {
        yaml += `      max_actions: ${step.browserConfig.max_actions}\n`;
      }
      if (step.browserConfig.captcha_strategy !== "pause") {
        yaml += `      captcha_strategy: ${yamlScalar(step.browserConfig.captcha_strategy)}\n`;
      }
      if (step.browserConfig.capture_screenshots) {
        yaml += `      capture_screenshots: true\n`;
      }
      if (step.browserConfig.output_schema) {
        yaml += `      output_schema: ${JSON.stringify(step.browserConfig.output_schema)}\n`;
      }
    } else if (sType === "agent" || sType === "managed-agent") {
      const configKey = sType === "agent" ? "agent_config" : "managed_agent_config";
      yaml += `  - id: ${yamlScalar(step.id)}\n`;
      yaml += `    type: ${yamlScalar(sType)}\n`;
      yaml += `    ${configKey}:\n`;
      if (step.agentConfig?.template) {
        yaml += `      ${sType === "agent" ? "template" : "agent_template"}: ${yamlScalar(step.agentConfig.template)}\n`;
      }
      if (step.agentConfig?.describe && !step.agentConfig?.template) {
        yaml += `      describe: |\n`;
        step.agentConfig?.describe.split("\n").forEach((line) => {
          yaml += `        ${line}\n`;
        });
      }
      if (step.agentConfig?.message) {
        yaml += `      message: |\n`;
        step.agentConfig?.message.split("\n").forEach((line) => {
          yaml += `        ${line}\n`;
        });
      }
      if (sType === "agent" && step.agentConfig?.runtime !== "auto") {
        yaml += `      runtime: ${yamlScalar(step.agentConfig?.runtime ?? "auto")}\n`;
      }
      if (step.agentConfig?.timeout !== 600) {
        yaml += `      timeout: ${step.agentConfig?.timeout}\n`;
      }
      if (step.agentConfig?.outputFormat !== "text") {
        yaml += `      output_format: ${yamlScalar(step.agentConfig?.outputFormat ?? "text")}\n`;
      }
      if (step.agentConfig?.fallbackTemplate) {
        yaml += `      fallback_template: ${yamlScalar(step.agentConfig.fallbackTemplate)}\n`;
      }
    } else if (sType === "llm") {
      yaml += `  - id: ${yamlScalar(step.id)}\n`;
      yaml += `    type: llm\n`;
      yaml += `    prompt: |\n`;
      step.prompt.split("\n").forEach((line) => {
        yaml += `      ${line}\n`;
      });
      if (step.model !== defaultModel) yaml += `    model: ${yamlScalar(step.model)}\n`;
      if (step.llmSystemPrompt) {
        yaml += `    llm_config:\n`;
        yaml += `      system_prompt: ${yamlScalar(step.llmSystemPrompt)}\n`;
      }
    } else {
      yaml += `  - id: ${yamlScalar(step.id)}\n`;
      yaml += `    prompt: |\n`;
      step.prompt.split("\n").forEach((line) => {
        yaml += `      ${line}\n`;
      });
      if (step.model !== defaultModel) yaml += `    model: ${yamlScalar(step.model)}\n`;
      if (step.maxTurns !== 10) yaml += `    max_turns: ${step.maxTurns}\n`;
      if (step.timeout !== 300) yaml += `    timeout: ${step.timeout}\n`;
    }

    const deps = depMap.get(step.id) || step.dependsOn;
    if (deps && deps.length > 0) {
      yaml += `    depends_on:\n`;
      deps.forEach((d) => {
        yaml += `      - ${yamlScalar(d)}\n`;
      });
    }
    if (step.parallelOver) {
      yaml += `    parallel_over: ${yamlScalar(step.parallelOver)}\n`;
    }

    // Tools (only if step has tools different from default)
    if (step.tools.length > 0) {
      const sameAsDefault = defaultTools.length === step.tools.length &&
        defaultTools.every((t) => step.tools.includes(t));
      if (!sameAsDefault) {
        yaml += `    tools: [${yamlInlineList(step.tools)}]\n`;
      }
    }

    // Retry config
    if (step.retry.enabled) {
      yaml += `    retry:\n`;
      yaml += `      max_attempts: ${step.retry.maxAttempts}\n`;
      yaml += `      backoff: ${yamlScalar(step.retry.backoff)}\n`;
      yaml += `      on_failure: ${yamlScalar(step.retry.onFailure)}\n`;
    }

    // AutoPilot config
    if (step.autopilot.enabled && step.autopilot.variants.length >= 2) {
      yaml += `    autopilot:\n`;
      yaml += `      enabled: true\n`;
      yaml += `      optimize_for: ${yamlScalar(step.autopilot.optimizeFor)}\n`;
      yaml += `      sample_rate: ${step.autopilot.sampleRate}\n`;
      yaml += `      min_samples: ${step.autopilot.minSamples}\n`;
      yaml += `      quality_threshold: ${step.autopilot.qualityThreshold}\n`;
      yaml += `      auto_deploy: ${step.autopilot.autoDeploy}\n`;
      yaml += `      evaluation:\n`;
      yaml += `        method: ${yamlScalar(step.autopilot.evaluation)}\n`;
      yaml += `      variants:\n`;
      for (const v of step.autopilot.variants) {
        yaml += `        - id: ${yamlScalar(v.id)}\n`;
        yaml += `          model: ${yamlScalar(v.model)}\n`;
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
        yaml += `      - ${yamlScalar(p)}\n`;
      });
    }

    // SLO config
    if (step.slo.enabled) {
      yaml += `    slo:\n`;
      yaml += `      quality_min: ${step.slo.qualityMin}\n`;
      yaml += `      cost_max_usd: ${step.slo.costMaxUsd}\n`;
      yaml += `      latency_max_seconds: ${step.slo.latencyMaxSeconds}\n`;
      yaml += `      optimize_for: ${yamlScalar(step.slo.optimizeFor)}\n`;
      yaml += `      model_pool: auto\n`;
    }

    // CSV output config
    if (step.csvOutput.enabled) {
      yaml += `    csv_output:\n`;
      yaml += `      directory: ${yamlScalar(step.csvOutput.directory || "./output")}\n`;
      yaml += `      mode: ${yamlScalar(step.csvOutput.mode)}\n`;
      if (step.csvOutput.filename) {
        yaml += `      filename: ${yamlScalar(step.csvOutput.filename)}\n`;
      }
    }

    // PDF report config
    if (step.pdfReport.enabled) {
      yaml += `    pdf_report:\n`;
      yaml += `      directory: ${yamlScalar(step.pdfReport.directory || "./output")}\n`;
      yaml += `      language: ${yamlScalar(step.pdfReport.language)}\n`;
      if (step.pdfReport.filename) {
        yaml += `      filename: ${yamlScalar(step.pdfReport.filename)}\n`;
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
    tools: string[];
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

  // Tools - inline format: tools: [slack, jira]
  const toolsInline = block.match(/tools:\s*\[([^\]]+)\]/);
  if (toolsInline) {
    result.tools = toolsInline[1].split(",").map((t) => t.trim().replace(/^["']|["']$/g, "")).filter(Boolean);
  }

  return result;
}

function buildInitialState(wf: InitialWorkflow) {
  const steps: StepConfig[] = (wf.steps || []).map((s) => {
    const adv = parseAdvancedConfig(wf.yaml_content, s.id);
    const sAny = s as Record<string, unknown>;
    const rawType = (sAny.type as string) || "standard";
    const resolvedType = (rawType === "agent" || rawType === "managed-agent" ? rawType : rawType) as StepType;
    const agentTpl = (sAny.agent_template as string) || "";
    return {
      id: s.id,
      stepType: resolvedType,
      prompt: s.prompt || "",
      model: resolvedType === "managed-agent" || resolvedType === "agent"
        ? (agentTpl ? `agent:${agentTpl}` : (s.model || "managed-agent"))
        : resolvedType === "report" ? "report" : (s.model || "sonnet"),
      agentConfig: agentTpl ? { template: agentTpl, runtime: "auto" as const } : undefined,
      maxTurns: 10,
      timeout: 300,
      parallelOver: "",
      dependsOn: s.depends_on || [],
      tools: adv.tools || [],
      directoryInput: { ...DEFAULT_DIRECTORY_INPUT },
      csvOutput: adv.csvOutput || { ...DEFAULT_CSV_OUTPUT },
      pdfReport: adv.pdfReport || { ...DEFAULT_PDF_REPORT },
      autopilot: { ...DEFAULT_AUTOPILOT, variants: [], ...adv.autopilot },
      retry: adv.retry || { ...DEFAULT_RETRY },
      approval: { ...DEFAULT_APPROVAL },
      policies: adv.policies || [],
      slo: adv.slo || { ...DEFAULT_SLO },
      llmSystemPrompt: "",
      httpConfig: { ...DEFAULT_HTTP_CONFIG, headers: {} },
      codeConfig: { ...DEFAULT_CODE_CONFIG },
      conditionConfig: { ...DEFAULT_CONDITION_CONFIG, thenSteps: [], elseSteps: [] },
      classifyConfig: { ...DEFAULT_CLASSIFY_CONFIG, categories: [], branches: {} },
      loopConfig: { ...DEFAULT_LOOP_CONFIG, stepIds: [] },
      raceConfig: { ...DEFAULT_RACE_CONFIG },
      sensorConfig: { ...DEFAULT_SENSOR_CONFIG },
      gateConfig: { ...DEFAULT_GATE_CONFIG, strategies: [] },
      transformConfig: { ...DEFAULT_TRANSFORM_CONFIG },
      notifyConfig: { ...DEFAULT_NOTIFY_CONFIG },
      delegateConfig: { ...DEFAULT_DELEGATE_CONFIG },
      browserConfig: { ...DEFAULT_BROWSER_CONFIG },
    };
  });

  const nodes: Node[] = steps.map((s, i) => ({
    id: s.id,
    type: "step" as const,
    position: { x: 200 + (i % 3) * 220, y: 50 + Math.floor(i / 3) * 150 },
    data: { label: s.id, model: s.model, stepType: s.stepType, toolNames: s.tools, hasTools: s.tools.length > 0 },
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
  const [defaultTools, setDefaultTools] = useState<string[]>([]);
  const [templateBrowserOpen, setTemplateBrowserOpen] = useState(false);
  const [generateModalOpen, setGenerateModalOpen] = useState(false);
  const [editWithAiYaml, setEditWithAiYaml] = useState<string | undefined>();
  const [toolsPaletteOpen, setToolsPaletteOpen] = useState(false);
  const [collapsedCategories, setCollapsedCategories] = useState<Record<string, boolean>>({});
  // Learn mode: when on, palette items show their one-line summary inline.
  // Default ON for first-timers (no existing steps); persisted to localStorage.
  const [learnMode, setLearnMode] = useState<boolean>(() => {
    try {
      const stored = window.localStorage.getItem(LEARN_MODE_KEY);
      if (stored != null) return stored === "true";
    } catch {
      /* ignore storage errors */
    }
    return (initial?.steps?.length ?? 0) === 0;
  });
  // Dismissed "Add next:" suggestions bar (reappears for a new selection).
  const [suggestionsDismissed, setSuggestionsDismissed] = useState(false);
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

  const addStep = useCallback((stepType: StepType = "standard", agentTemplate?: string) => {
    const prefixes: Record<string, string> = {
      standard: "step", llm: "llm", http: "http", code: "code",
      condition: "check", classify: "route", loop: "loop",
      race: "race", sensor: "sensor", gate: "gate",
      transform: "transform", notify: "notify", delegate: "delegate",
      browser: "browser", parse: "parse", approval: "approval",
      sub_workflow: "sub", openclaw: "claw", composio: "composio",
      agent: "agent", "managed-agent": "magent",
    };
    const prefix = agentTemplate || prefixes[stepType] || "step";
    const id = `${prefix}_${counter}`;
    setCounter((c) => c + 1);

    const newStep: StepConfig = {
      id,
      stepType,
      prompt: "",
      model: stepType === "classify" ? "haiku" : "sonnet",
      maxTurns: 10,
      timeout: 300,
      parallelOver: "",
      dependsOn: [],
      tools: [],
      directoryInput: { ...DEFAULT_DIRECTORY_INPUT },
      csvOutput: { ...DEFAULT_CSV_OUTPUT },
      pdfReport: { ...DEFAULT_PDF_REPORT },
      autopilot: { ...DEFAULT_AUTOPILOT, variants: [] },
      retry: { ...DEFAULT_RETRY },
      // When added from the palette as an "approval" step, enable approval by default
      approval: stepType === "approval"
        ? { ...DEFAULT_APPROVAL, enabled: true }
        : { ...DEFAULT_APPROVAL },
      policies: [],
      slo: { ...DEFAULT_SLO },
      llmSystemPrompt: "",
      httpConfig: { ...DEFAULT_HTTP_CONFIG, headers: {} },
      codeConfig: { ...DEFAULT_CODE_CONFIG },
      conditionConfig: { ...DEFAULT_CONDITION_CONFIG, thenSteps: [], elseSteps: [] },
      classifyConfig: { ...DEFAULT_CLASSIFY_CONFIG, categories: [], branches: {} },
      loopConfig: { ...DEFAULT_LOOP_CONFIG, stepIds: [] },
      raceConfig: { ...DEFAULT_RACE_CONFIG },
      sensorConfig: { ...DEFAULT_SENSOR_CONFIG },
      gateConfig: { ...DEFAULT_GATE_CONFIG, strategies: [] },
      transformConfig: { ...DEFAULT_TRANSFORM_CONFIG },
      notifyConfig: { ...DEFAULT_NOTIFY_CONFIG },
      delegateConfig: { ...DEFAULT_DELEGATE_CONFIG },
      browserConfig: { ...DEFAULT_BROWSER_CONFIG },
      agentConfig: { ...DEFAULT_AGENT_CONFIG, template: agentTemplate || "" },
    };

    setSteps((prev) => [...prev, newStep]);
    setNodes((prev) => {
      const newNode: Node = {
        id,
        type: "step",
        position: { x: 200 + (prev.length % 3) * 200, y: 50 + Math.floor(prev.length / 3) * 150 },
        data: { label: id, model: stepType === "classify" ? "haiku" : "sonnet", stepType },
      };
      return [...prev, newNode];
    });
    setSelectedStepId(id);
  }, [counter, setNodes]);

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
                  stepType: updated.stepType,
                  hasRetry: updated.retry.enabled,
                  hasApproval: updated.approval.enabled,
                  hasAutoPilot: updated.autopilot.enabled,
                  hasCsvOutput: updated.csvOutput.enabled,
                  hasPdfReport: updated.pdfReport.enabled,
                  hasSlo: updated.slo.enabled,
                  hasTools: updated.tools.length > 0,
                  toolNames: updated.tools,
                  browserMode: updated.browserConfig.mode,
                  browserUrl: updated.browserConfig.startUrl,
                },
              }
            : n
        )
      );
      if (updated.id !== selectedStepId) {
        // Update edges: fix source/target AND regenerate edge IDs to prevent key conflicts
        setEdges((prev) =>
          prev.map((e) => {
            const newSource = e.source === selectedStepId ? updated.id : e.source;
            const newTarget = e.target === selectedStepId ? updated.id : e.target;
            if (newSource === e.source && newTarget === e.target) return e;
            return {
              ...e,
              id: `${newSource}-${newTarget}`,
              source: newSource,
              target: newTarget,
            };
          })
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

  // Handle AI-generated workflow selection
  const handleGenerateSelect = useCallback(
    (template: { name: string; content: string; step_count: number }) => {
      setGenerateModalOpen(false);
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

  const setAllModels = useCallback(
    (model: string) => {
      setSteps((prev) => prev.map((s) => ({ ...s, model })));
      setNodes((prev) =>
        prev.map((n) => ({ ...n, data: { ...n.data, model } }))
      );
    },
    [setNodes]
  );

  const toggleCategory = useCallback((cat: string) => {
    setCollapsedCategories((prev) => ({ ...prev, [cat]: !prev[cat] }));
  }, []);

  // Persist Learn-mode preference across sessions.
  useEffect(() => {
    try {
      window.localStorage.setItem(LEARN_MODE_KEY, String(learnMode));
    } catch {
      /* ignore storage errors */
    }
  }, [learnMode]);

  // Reset the dismissed state whenever the selection changes so suggestions
  // resurface for the newly selected step.
  useEffect(() => {
    setSuggestionsDismissed(false);
  }, [selectedStepId]);

  const stepCategories = useMemo(() => [
    {
      key: "ai",
      label: "AI",
      icon: Sparkles,
      items: [
        { type: "standard" as const, icon: Brain, label: "Agent", color: "text-accent" },
        { type: "llm" as const, icon: MessageSquare, label: "LLM", color: "text-accent" },
        { type: "classify" as const, icon: Tag, label: "Classify", color: "text-pink-400" },
      ],
    },
    {
      key: "control",
      label: "Control Flow",
      icon: GitBranch,
      items: [
        { type: "condition" as const, icon: GitBranch, label: "If/Else", color: "text-violet-400" },
        { type: "loop" as const, icon: Repeat, label: "Loop", color: "text-cyan-400" },
        { type: "race" as const, icon: Zap, label: "Race", color: "text-purple-400" },
        { type: "gate" as const, icon: ShieldCheck, label: "Gate", color: "text-red-400" },
        { type: "approval" as const, icon: ShieldCheck, label: "Approval", color: "text-yellow-400" },
      ],
    },
    {
      key: "integration",
      label: "Integration",
      icon: Plug,
      items: [
        { type: "http" as const, icon: Globe, label: "HTTP", color: "text-emerald-400" },
        { type: "code" as const, icon: Code, label: "Code", color: "text-amber-400" },
        { type: "delegate" as const, icon: ExternalLink, label: "Delegate", color: "text-indigo-400" },
        { type: "browser" as const, icon: Monitor, label: "Browser", color: "text-fuchsia-400" },
        { type: "parse" as const, icon: FileText, label: "Parse", color: "text-orange-400" },
        { type: "sub_workflow" as const, icon: Layers, label: "Sub-workflow", color: "text-blue-400" },
        { type: "openclaw" as const, icon: Zap, label: "OpenClaw", color: "text-green-400" },
        { type: "composio" as const, icon: Plug, label: "Composio", color: "text-rose-400" },
      ],
    },
    {
      key: "agents",
      label: "Agents",
      icon: Bot,
      items: [
        { type: "agent" as const, icon: Bot, label: "Agent (Auto)", color: "text-accent" },
        { type: "agent" as const, icon: Search, label: "Researcher", color: "text-blue-400", template: "researcher" },
        { type: "agent" as const, icon: Code, label: "Coder", color: "text-emerald-400", template: "coder" },
        { type: "agent" as const, icon: TrendingUp, label: "Analyst", color: "text-orange-400", template: "analyst" },
        { type: "agent" as const, icon: FileEdit, label: "Writer", color: "text-violet-400", template: "writer" },
        { type: "agent" as const, icon: ShieldCheck, label: "Reviewer", color: "text-red-400", template: "reviewer" },
        { type: "agent" as const, icon: Download, label: "Scraper", color: "text-cyan-400", template: "scraper" },
        { type: "agent" as const, icon: Wrench, label: "Tester", color: "text-green-400", template: "tester" },
        { type: "agent" as const, icon: Server, label: "DevOps", color: "text-amber-400", template: "devops" },
        { type: "agent" as const, icon: Globe, label: "Translator", color: "text-pink-400", template: "translator" },
        { type: "agent" as const, icon: PenTool, label: "Designer", color: "text-fuchsia-400", template: "designer" },
        { type: "agent" as const, icon: Database, label: "SQL Expert", color: "text-indigo-400", template: "sql_expert" },
        { type: "agent" as const, icon: BarChart, label: "SEO", color: "text-teal-400", template: "seo_specialist" },
        { type: "agent" as const, icon: DollarSign, label: "Finance", color: "text-yellow-400", template: "financial_analyst" },
        { type: "agent" as const, icon: Clipboard, label: "PM", color: "text-rose-400", template: "project_manager" },
      ],
    },
    {
      key: "output",
      label: "Output",
      icon: Bell,
      items: [
        { type: "transform" as const, icon: Shuffle, label: "Transform", color: "text-cyan-400" },
        { type: "notify" as const, icon: Bell, label: "Notify", color: "text-pink-400" },
        { type: "sensor" as const, icon: Radio, label: "Sensor", color: "text-teal-400" },
      ],
    },
  ], []);

  const selectedStep = steps.find((s) => s.id === selectedStepId);
  // "Common next steps" — suggested follow-ups for the selected step type.
  const nextStepSuggestions = useMemo<StepType[]>(
    () =>
      selectedStep
        ? getNextStepSuggestions(selectedStep.stepType || "standard")
        : [],
    [selectedStep],
  );
  const yaml = useMemo(
    () => generateYaml(workflowName, steps, edges, defaultTools),
    [workflowName, steps, edges, defaultTools]
  );

  return (
    <div className="relative flex h-[calc(100vh-10rem)] gap-0 rounded-xl border border-border bg-surface shadow-sm overflow-hidden">
      {/* Mobile notice */}
      <div className="absolute inset-x-0 top-0 z-10 flex items-center gap-2 bg-warning/10 border-b border-warning/30 px-3 py-2 text-xs text-warning lg:hidden">
        <Monitor className="h-4 w-4 shrink-0" />
        <span>Best experience on desktop. Some features are limited on smaller screens.</span>
      </div>

      {/* Left palette */}
      <div className="hidden lg:block w-48 shrink-0 border-r border-border bg-background/50 p-3">
        <div className="mb-3 flex items-center justify-between gap-2">
          <p className="text-xs font-semibold text-muted">PALETTE</p>
          <button
            type="button"
            role="switch"
            aria-checked={learnMode}
            onClick={() => setLearnMode((v) => !v)}
            title={
              learnMode
                ? "Learn mode on — showing a one-line summary under each step"
                : "Learn mode off — hover a step to learn what it does"
            }
            className={cn(
              "flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
              learnMode
                ? "border-accent/40 bg-accent/10 text-accent"
                : "border-border text-muted hover:border-accent hover:text-accent",
            )}
          >
            <GraduationCap className="h-3 w-3" />
            Learn
          </button>
        </div>
        <div className="space-y-2">
          {stepCategories.map(({ key, label, icon: CatIcon, items }) => {
            const isCollapsed = !!collapsedCategories[key];
            return (
              <div key={key}>
                <button
                  onClick={() => toggleCategory(key)}
                  className="flex w-full items-center gap-1.5 py-1 text-xs uppercase tracking-wide text-gray-500 hover:text-foreground transition-colors"
                >
                  <CatIcon className="h-3 w-3" />
                  <span className="font-semibold">{label}</span>
                  <span className="ml-auto mr-1 rounded-full bg-border px-1.5 py-px text-[10px] font-medium text-muted">
                    {items.length}
                  </span>
                  <ChevronDown className={cn("h-3 w-3 transition-transform", isCollapsed && "-rotate-90")} />
                </button>
                {!isCollapsed && (
                  <div className="mt-1 grid grid-cols-2 gap-1.5">
                    {items.map((item) => {
                      const { type, icon: Icon, label: stepLabel, color } = item;
                      const tmpl = "template" in item ? (item as { template?: string }).template : undefined;
                      return (
                        <PaletteItem
                          key={`${type}-${tmpl || stepLabel}`}
                          type={type}
                          template={tmpl}
                          icon={Icon}
                          label={stepLabel}
                          color={color}
                          showInlineSummary={learnMode}
                          onAdd={() => addStep(type, tmpl)}
                        />
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>

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

        <button
          onClick={() => {
            if (steps.length > 0) {
              setEditWithAiYaml(generateYaml(workflowName, steps, edges, defaultTools));
            } else {
              setEditWithAiYaml(undefined);
            }
            setGenerateModalOpen(true);
          }}
          className={cn(
            "mt-2 flex w-full items-center gap-2 rounded-lg border border-dashed border-accent/40 px-3 py-2.5",
            "text-xs font-medium text-accent/70 hover:border-accent hover:text-accent transition-colors"
          )}
        >
          <Wand2 className="h-3.5 w-3.5" />
          AI Assistant
        </button>

        <div className="mt-6">
          <label className="mb-1 block text-xs font-medium text-muted">Workflow Name</label>
          <input
            type="text"
            value={workflowName}
            onChange={(e) => setWorkflowName(e.target.value)}
            className={cn(
              "h-8 w-full rounded-md border border-border bg-surface px-2 text-xs",
              "focus-visible:border-accent/50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring/30"
            )}
          />
        </div>

        {steps.length > 0 && (
          <div className="mt-4">
            <label className="mb-1 flex items-center gap-1.5 text-xs font-medium text-muted">
              <RefreshCw className="h-3 w-3" />
              Set All Models
            </label>
            <select
              defaultValue=""
              onChange={(e) => {
                if (e.target.value) {
                  setAllModels(e.target.value);
                  e.target.value = "";
                }
              }}
              className={cn(
                "h-8 w-full rounded-md border border-border bg-surface px-2 text-xs",
                "focus-visible:border-accent/50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring/30"
              )}
            >
              <option value="" disabled>Select model...</option>
              <optgroup label="Claude">
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
          </div>
        )}

        <div className="mt-4">
          <button
            onClick={() => setToolsPaletteOpen(!toolsPaletteOpen)}
            className={cn(
              "flex w-full items-center gap-2 rounded-lg border px-3 py-2.5",
              "text-xs font-medium transition-colors",
              defaultTools.length > 0
                ? "border-accent/30 bg-accent/5 text-accent"
                : "border-dashed border-border text-muted hover:border-accent hover:text-accent"
            )}
          >
            <Wrench className="h-3.5 w-3.5" />
            Default Tools
            {defaultTools.length > 0 && (
              <span className="ml-auto rounded-full bg-accent/15 px-1.5 py-0.5 text-[10px] font-semibold">
                {defaultTools.length}
              </span>
            )}
          </button>
          {toolsPaletteOpen && (
            <div className="mt-2 rounded-lg border border-border bg-background/50 p-2 space-y-2">
              <p className="text-[10px] text-muted-foreground">
                Tools available to all steps by default. Override per-step in step settings.
              </p>
              <ToolSelector
                selected={defaultTools}
                onChange={setDefaultTools}
                compact
              />
              {defaultTools.length > 0 && (
                <div className="rounded-md bg-accent/5 border border-accent/20 px-2 py-1">
                  <p className="text-[10px] font-mono text-accent">
                    default_tools: [{defaultTools.join(", ")}]
                  </p>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Mobile add step button */}
      <button
        onClick={() => addStep("standard")}
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
      <button
        onClick={() => {
          if (steps.length > 0) {
            setEditWithAiYaml(generateYaml(workflowName, steps, edges, defaultTools));
          } else {
            setEditWithAiYaml(undefined);
          }
          setGenerateModalOpen(true);
        }}
        className="absolute left-[6.25rem] top-12 z-10 flex h-9 w-9 items-center justify-center rounded-lg border border-accent/40 bg-surface shadow-sm text-accent/70 hover:text-accent hover:border-accent transition-colors lg:hidden"
        title="AI Assistant"
      >
        <Wand2 className="h-4 w-4" />
      </button>

      {/* Canvas + AI Chat Panel wrapper */}
      <div className="flex flex-1 min-w-0">
        {/* Canvas */}
        <div className={cn(
          "relative flex-1 min-w-0 pt-8 lg:pt-0 transition-settle",
          generateModalOpen ? "lg:w-[60%]" : "w-full"
        )}>
          {/* Common next steps — quiet, dismissible "Add next:" affordance */}
          {selectedStep && !suggestionsDismissed && nextStepSuggestions.length > 0 && (
            <div
              className={cn(
                "absolute left-1/2 top-10 lg:top-3 z-10 -translate-x-1/2",
                "flex items-center gap-1.5 rounded-full border border-border bg-surface/95 px-2.5 py-1.5 shadow-sm backdrop-blur-sm",
              )}
              role="group"
              aria-label="Suggested next steps"
            >
              <span className="flex items-center gap-1 text-[11px] font-medium text-muted">
                <ArrowRight className="h-3 w-3" />
                Add next:
              </span>
              {nextStepSuggestions.map((sType) => {
                const meta = getStepMeta(sType);
                return (
                  <button
                    key={sType}
                    type="button"
                    onClick={() => addStep(sType)}
                    title={meta.summary}
                    aria-label={`Add ${meta.label} step. ${meta.summary}`}
                    className={cn(
                      "rounded-full border border-dashed border-border px-2 py-0.5 text-[11px] font-medium text-muted",
                      "hover:border-accent hover:text-accent transition-colors",
                    )}
                  >
                    {meta.label}
                  </button>
                );
              })}
              <button
                type="button"
                onClick={() => setSuggestionsDismissed(true)}
                aria-label="Dismiss suggestions"
                className="ml-0.5 rounded-full p-0.5 text-muted hover:text-foreground transition-colors"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          )}
          <ErrorBoundary name="WorkflowBuilder-ReactFlow">
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
          </ErrorBoundary>
        </div>

        {/* AI Chat Panel - slides in from the right */}
        {generateModalOpen && (
          <div className="w-[40%] min-w-[380px] max-w-[500px] shrink-0">
            <GenerateChatPanel
              open={generateModalOpen}
              onClose={() => { setGenerateModalOpen(false); setEditWithAiYaml(undefined); }}
              onSelect={handleGenerateSelect}
              existingYaml={editWithAiYaml}
            />
          </div>
        )}
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
              "hover:bg-accent-hover transition-settle shadow-sm"
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
                    "hover:bg-accent-hover transition-settle shadow-sm"
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
