/**
 * stepValidation.ts — pure, data-driven validation for builder steps.
 *
 * `validateStep` checks a single step for missing REQUIRED fields (keyed by step
 * type) and obvious misconfiguration, such as a `dependsOn` entry that points at
 * a step id that does not exist. Rules live in a small table so they are easy to
 * extend. No React, no side effects.
 *
 * The input is intentionally loosely typed (`StepLike`) so callers can pass a
 * full `StepConfig` or any partial/legacy shape without friction.
 */

import type { StepType } from "@/components/workflows/StepConfigPanel";

export type ValidationLevel = "ok" | "warning" | "error";

export interface ValidationIssue {
  /** The config field the issue relates to, when known. */
  field?: string;
  /** Human-readable problem statement. */
  message: string;
  /** Optional actionable hint on how to fix it. */
  hint?: string;
}

export interface ValidationResult {
  level: ValidationLevel;
  issues: ValidationIssue[];
}

/** Loose step shape — a superset of the fields validators look at. */
export interface StepLike {
  id?: string;
  stepType?: StepType | string;
  prompt?: string;
  dependsOn?: string[];
  httpConfig?: { url?: string } | null;
  codeConfig?: { code?: string } | null;
  conditionConfig?: { expression?: string } | null;
  classifyConfig?: { categories?: string[]; input?: string } | null;
  loopConfig?: { over?: string; stepIds?: string[] } | null;
  raceConfig?: { branches?: string } | null;
  sensorConfig?: { url?: string; condition?: string } | null;
  gateConfig?: { strategies?: Array<{ type?: string }> } | null;
  transformConfig?: { template?: string } | null;
  notifyConfig?: { service?: string; message?: string } | null;
  delegateConfig?: { workflow?: string } | null;
  browserConfig?: { startUrl?: string } | null;
  // sub_workflow / delegate "target"-style fields vary by shape:
  [key: string]: unknown;
}

/** A single required-field rule for a step type. */
interface RequiredRule {
  field: string;
  /** Returns true when the value is present/valid. */
  present: (step: StepLike) => boolean;
  message: string;
  hint?: string;
}

const nonEmptyStr = (v: unknown): boolean =>
  typeof v === "string" && v.trim().length > 0;

/**
 * REQUIRED_RULES — per step type, the fields that must be set for the step to
 * run. Extend by adding entries; everything else flows through automatically.
 */
const REQUIRED_RULES: Partial<Record<string, RequiredRule[]>> = {
  standard: [
    {
      field: "prompt",
      present: (s) => nonEmptyStr(s.prompt),
      message: "Agent step has no prompt.",
      hint: "Tell the agent what to do in the Prompt field.",
    },
  ],
  llm: [
    {
      field: "prompt",
      present: (s) => nonEmptyStr(s.prompt),
      message: "LLM step has no prompt.",
      hint: "Add the prompt to send to the model.",
    },
  ],
  "managed-agent": [
    {
      field: "prompt",
      present: (s) => nonEmptyStr(s.prompt),
      message: "Managed agent has no prompt.",
      hint: "Describe the task for the managed agent.",
    },
  ],
  http: [
    {
      field: "httpConfig.url",
      present: (s) => nonEmptyStr(s.httpConfig?.url),
      message: "HTTP step has no URL.",
      hint: "Set the request URL.",
    },
  ],
  code: [
    {
      field: "codeConfig.code",
      present: (s) => nonEmptyStr(s.codeConfig?.code),
      message: "Code step has no code.",
      hint: "Add the Python code to run.",
    },
  ],
  condition: [
    {
      field: "conditionConfig.expression",
      present: (s) => nonEmptyStr(s.conditionConfig?.expression),
      message: "Condition step has no expression.",
      hint: "Add an expression that evaluates to true or false.",
    },
  ],
  classify: [
    {
      field: "classifyConfig.categories",
      present: (s) => (s.classifyConfig?.categories?.length ?? 0) > 0,
      message: "Classify step has no categories.",
      hint: "Add at least one category to route into.",
    },
  ],
  loop: [
    {
      field: "loopConfig.over",
      present: (s) => nonEmptyStr(s.loopConfig?.over),
      message: "Loop step has nothing to iterate over.",
      hint: "Point the loop at a list, e.g. {input.items}.",
    },
  ],
  race: [
    {
      field: "raceConfig.branches",
      present: (s) => nonEmptyStr(s.raceConfig?.branches),
      message: "Race step has no branches.",
      hint: "Add at least one branch (one per line) to race.",
    },
  ],
  gate: [
    {
      field: "gateConfig.strategies",
      present: (s) =>
        (s.gateConfig?.strategies?.length ?? 0) > 0 &&
        nonEmptyStr(s.gateConfig?.strategies?.[0]?.type),
      message: "Gate step has no strategy.",
      hint: "Add a strategy (LLM eval, human, or timeout).",
    },
  ],
  sensor: [
    {
      field: "sensorConfig.url",
      present: (s) => nonEmptyStr(s.sensorConfig?.url),
      message: "Sensor step has no URL to poll.",
      hint: "Set the URL the sensor should poll.",
    },
    {
      field: "sensorConfig.condition",
      present: (s) => nonEmptyStr(s.sensorConfig?.condition),
      message: "Sensor step has no stop condition.",
      hint: "Add the condition that ends polling.",
    },
  ],
  transform: [
    {
      field: "transformConfig.template",
      present: (s) => nonEmptyStr(s.transformConfig?.template),
      message: "Transform step has no template.",
      hint: "Add the template that produces the output.",
    },
  ],
  notify: [
    {
      field: "notifyConfig.message",
      present: (s) => nonEmptyStr(s.notifyConfig?.message),
      message: "Notify step has no message.",
      hint: "Add the message body to send.",
    },
  ],
  delegate: [
    {
      field: "delegateConfig.workflow",
      present: (s) => nonEmptyStr(s.delegateConfig?.workflow),
      message: "Delegate step has no target workflow.",
      hint: "Pick the workflow to delegate to.",
    },
  ],
  sub_workflow: [
    {
      field: "target",
      // sub_workflow target can live under a few shapes; accept any of them.
      present: (s) =>
        nonEmptyStr(s.delegateConfig?.workflow) ||
        nonEmptyStr(s.target as string) ||
        nonEmptyStr(s.workflow as string),
      message: "Sub-workflow step has no target.",
      hint: "Choose the workflow to run as a sub-step.",
    },
  ],
  browser: [
    {
      field: "browserConfig.startUrl",
      present: (s) => nonEmptyStr(s.browserConfig?.startUrl),
      message: "Browser step has no start URL.",
      hint: "Set the page the browser should open first.",
    },
  ],
};

/**
 * validateStep — pure validator for one step.
 *
 * @param step      the step to check (full StepConfig or any partial shape).
 * @param allSteps  optional list of all steps in the workflow; when given,
 *                  `dependsOn` ids are cross-checked against existing step ids.
 * @returns the worst level found and the list of issues.
 */
export function validateStep(
  step: StepLike,
  allSteps?: StepLike[],
): ValidationResult {
  const issues: ValidationIssue[] = [];

  const type = (step?.stepType as string) ?? "";
  const rules = REQUIRED_RULES[type] ?? [];
  for (const rule of rules) {
    if (!rule.present(step)) {
      issues.push({ field: rule.field, message: rule.message, hint: rule.hint });
    }
  }

  // Cross-step check: dependsOn must reference real step ids.
  if (allSteps && Array.isArray(step?.dependsOn) && step.dependsOn.length > 0) {
    const known = new Set(
      allSteps.map((s) => s?.id).filter((id): id is string => Boolean(id)),
    );
    for (const dep of step.dependsOn) {
      if (dep && dep !== step.id && !known.has(dep)) {
        issues.push({
          field: "dependsOn",
          message: `Depends on "${dep}", which is not a step in this workflow.`,
          hint: "Remove it or point it at an existing step id.",
        });
      }
      if (dep && dep === step.id) {
        issues.push({
          field: "dependsOn",
          message: "Step depends on itself.",
          hint: "Remove the self-dependency.",
        });
      }
    }
  }

  const level: ValidationLevel = issues.length === 0 ? "ok" : "error";
  return { level, issues };
}

/** Returns true when the step has no validation issues. */
export function isStepValid(step: StepLike, allSteps?: StepLike[]): boolean {
  return validateStep(step, allSteps).level === "ok";
}
