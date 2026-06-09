/* Status → indicator-light mapping for the control-room LED language.
   Kept outside the component file so non-component consumers (StepNode,
   StepTimeline, pages) can share it without breaking fast refresh. */

export type LedState = "on" | "breathing" | "flicker" | "dim" | "hollow";

export interface LedConfig {
  state: LedState;
  color: string;
}

const LED_MAP: Record<string, LedConfig> = {
  // run / step lifecycle
  queued: { state: "dim", color: "var(--color-queued)" },
  pending: { state: "dim", color: "var(--color-muted)" },
  running: { state: "breathing", color: "var(--color-running)" },
  completed: { state: "on", color: "var(--color-success)" },
  succeeded: { state: "on", color: "var(--color-success)" },
  failed: { state: "flicker", color: "var(--color-error)" },
  partial: { state: "on", color: "var(--color-warning)" },
  cancelled: { state: "hollow", color: "var(--color-muted)" },
  skipped: { state: "hollow", color: "var(--color-muted)" },
  budget_exceeded: { state: "on", color: "var(--color-warning)" },
  awaiting_approval: { state: "breathing", color: "var(--color-warning)" },
  // approvals
  approved: { state: "on", color: "var(--color-success)" },
  rejected: { state: "flicker", color: "var(--color-error)" },
  timed_out: { state: "hollow", color: "var(--color-muted)" },
  // health / schedules / workflows
  healthy: { state: "on", color: "var(--color-success)" },
  degraded: { state: "breathing", color: "var(--color-warning)" },
  unhealthy: { state: "flicker", color: "var(--color-error)" },
  failing: { state: "flicker", color: "var(--color-error)" },
  unconfigured: { state: "hollow", color: "var(--color-muted)" },
  active: { state: "on", color: "var(--color-success)" },
  paused: { state: "hollow", color: "var(--color-muted)" },
  // runtime mode
  local: { state: "on", color: "var(--color-accent)" },
  // workflow versions
  draft: { state: "dim", color: "var(--color-muted)" },
  staging: { state: "on", color: "var(--color-warning)" },
  production: { state: "on", color: "var(--color-success)" },
  archived: { state: "hollow", color: "var(--color-muted)" },
};

const FALLBACK: LedConfig = { state: "dim", color: "var(--color-muted)" };

/** Resolve a status string to its LED state + color (fallback: dim muted). */
export function getLedConfig(status: string): LedConfig {
  return LED_MAP[status] || FALLBACK;
}
