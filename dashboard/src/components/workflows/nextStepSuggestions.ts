/**
 * nextStepSuggestions.ts — "Common next steps" map for the Workflow Builder.
 *
 * A small, hand-curated map of "what usually comes next" after a given step
 * type. Powers the dismissible "Add next:" affordance in WorkflowBuilder once a
 * step is selected. Framework-agnostic and side-effect free.
 */
import type { StepType } from "@/components/workflows/StepConfigPanel";

/** Step type -> 2-3 sensible follow-up step types. */
export const NEXT_STEP_SUGGESTIONS: Record<string, StepType[]> = {
  standard: ["transform", "notify", "condition"],
  llm: ["transform", "notify", "condition"],
  agent: ["transform", "notify", "condition"],
  "managed-agent": ["transform", "notify", "condition"],
  classify: ["condition", "notify"],
  http: ["parse", "transform", "code"],
  browser: ["parse", "transform"],
  openclaw: ["parse", "transform"],
  parse: ["llm", "transform"],
  code: ["transform", "condition"],
  transform: ["notify", "report"],
  condition: ["notify", "transform"],
  loop: ["transform", "notify"],
  race: ["transform", "notify"],
  gate: ["notify", "transform"],
  approval: ["notify", "transform"],
  sensor: ["notify", "condition"],
  delegate: ["transform", "notify"],
  sub_workflow: ["transform", "notify"],
  composio: ["transform", "notify"],
  notify: ["report"],
  report: ["notify"],
};

/** Returns up to 3 suggested follow-up step types for a given step type. */
export function getNextStepSuggestions(type: string): StepType[] {
  return (NEXT_STEP_SUGGESTIONS[type] ?? ["transform", "notify"]).slice(0, 3);
}
