export const API_BASE_URL = import.meta.env.VITE_API_URL || "/api";

export const STATUS_COLORS: Record<string, string> = {
  queued: "bg-queued/15 text-queued border-queued/30",
  running: "bg-running/15 text-running border-running/30",
  completed: "bg-success/15 text-success border-success/30",
  failed: "bg-error/15 text-error border-error/30",
  partial: "bg-warning/15 text-warning border-warning/30",
  cancelled: "bg-muted/15 text-muted border-muted/30",
  budget_exceeded: "bg-warning/15 text-warning border-warning/30",
  pending: "bg-muted/15 text-muted border-muted/30",
  skipped: "bg-muted/15 text-muted border-muted/30",
};

export const STATUS_DOT_COLORS: Record<string, string> = {
  queued: "bg-queued",
  running: "bg-running animate-pulse",
  completed: "bg-success",
  failed: "bg-error",
  partial: "bg-warning",
  cancelled: "bg-muted",
  budget_exceeded: "bg-warning",
  pending: "bg-muted",
  skipped: "bg-muted",
};

export const POLL_INTERVAL = 5000;
