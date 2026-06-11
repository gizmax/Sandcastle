/**
 * Shared, provider-neutral model picker options.
 *
 * Mirrors the grouped list the run replay/fork UI already exposed so that the
 * contextual "Try another model" action and the existing ReplayForkModal stay
 * in sync. These map to the `model` field accepted by the fork endpoint's
 * `changes` payload (POST /runs/{run_id}/fork).
 */
export interface ModelOption {
  value: string;
  label: string;
}

export interface ModelGroup {
  label: string;
  options: ModelOption[];
}

export const MODEL_GROUPS: ModelGroup[] = [
  {
    label: "Claude (Anthropic)",
    options: [
      { value: "opus", label: "Opus" },
      { value: "sonnet", label: "Sonnet" },
      { value: "haiku", label: "Haiku" },
    ],
  },
  {
    label: "OpenAI",
    options: [
      { value: "openai/codex-mini", label: "Codex Mini" },
      { value: "openai/codex", label: "Codex" },
    ],
  },
  {
    label: "MiniMax",
    options: [{ value: "minimax/m2.5", label: "MiniMax M2.5" }],
  },
  {
    label: "Google",
    options: [{ value: "google/gemini-2.5-pro", label: "Gemini 2.5 Pro" }],
  },
];

/** Human-friendly label for a model value, falling back to the raw value. */
export function modelLabel(value: string): string {
  for (const g of MODEL_GROUPS) {
    const found = g.options.find((o) => o.value === value);
    if (found) return found.label;
  }
  return value;
}
