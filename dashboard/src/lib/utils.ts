import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const inputClass = cn(
  "h-9 w-full rounded-lg border border-border bg-background px-3 text-sm text-foreground",
  "placeholder:text-muted-foreground",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30",
  "transition-colors"
);

export function formatCost(cost: number): string {
  if (!Number.isFinite(cost)) return "$0.00";
  return `$${cost.toFixed(2)}`;
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0s";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return secs > 0 ? `${minutes}m ${secs}s` : `${minutes}m`;
}

/**
 * Parse a timestamp from the API as UTC.
 * Backend stores naive UTC datetimes without "Z" suffix,
 * which browsers would otherwise interpret as local time.
 */
export function parseUTC(date: string | Date): Date {
  if (date instanceof Date) return date;
  // Append "Z" if the string has no timezone indicator
  if (!date.endsWith("Z") && !date.includes("+") && !/\d{2}:\d{2}$/.test(date.slice(-6))) {
    return new Date(date + "Z");
  }
  return new Date(date);
}

declare const __GITHUB_PAGES__: boolean;

/** Repo URL for the current deployment platform. */
export const REPO_URL = typeof __GITHUB_PAGES__ !== "undefined" && __GITHUB_PAGES__
  ? "https://github.com/gizmax/Sandcastle"
  : "https://gitlab.com/gizmax-group/sandcastle";

/** Hub contribution URL for the current deployment platform. */
export const HUB_CONTRIB_URL = typeof __GITHUB_PAGES__ !== "undefined" && __GITHUB_PAGES__
  ? "https://github.com/gizmax/Sandcastle/tree/main/hub"
  : "https://gitlab.com/gizmax-group/sandcastle/-/tree/main/hub";

export function formatRelativeTime(date: string | Date): string {
  const now = new Date();
  const then = parseUTC(date);
  const diffMs = now.getTime() - then.getTime();

  // Handle future dates gracefully
  if (diffMs < 0) return "just now";

  const diffSec = Math.floor(diffMs / 1000);

  if (diffSec < 60) return "just now";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return `${Math.floor(diffSec / 86400)}d ago`;
}
