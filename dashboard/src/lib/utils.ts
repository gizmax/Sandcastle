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

// Button size utilities
export const buttonSm = "px-2.5 py-1.5 text-xs rounded-lg";
export const buttonMd = "px-4 py-2 text-sm rounded-lg";
export const buttonLg = "px-5 py-2.5 text-sm rounded-lg";

// Button variant utilities
export const buttonPrimary = "bg-accent text-accent-foreground hover:bg-accent-hover transition-colors cursor-pointer";
export const buttonSecondary = "border border-border text-muted-foreground hover:text-foreground hover:bg-border/40 transition-colors cursor-pointer";
export const buttonDanger = "border border-error/30 text-error hover:bg-error/10 transition-colors cursor-pointer";
export const buttonGhost = "text-accent hover:text-accent-hover transition-colors cursor-pointer";

// Icon size utilities
export const iconSm = "h-3.5 w-3.5";
export const iconMd = "h-4 w-4";
export const iconLg = "h-5 w-5";

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

/**
 * Mask credentials in a connection string URL before displaying in the DOM.
 * Strips username and password from URLs like postgresql://user:pass@host/db.
 * For non-URL strings (e.g. SQLite file paths) falls back to a regex scrub.
 */
export function maskConnectionString(url: string): string {
  if (!url) return "";
  try {
    const parsed = new URL(url);
    if (parsed.password) parsed.password = "****";
    if (parsed.username && parsed.username !== "") parsed.username = "****";
    return parsed.toString();
  } catch {
    // Not a valid URL (e.g. sqlite path) - show as-is unless it looks like
    // it has embedded credentials in the form :password@
    return url.replace(/:[^:@/\\]+@/, ":****@");
  }
}

/**
 * Returns true if the given URL uses http: or https: protocol.
 * Use this before placing untrusted strings into href or src attributes
 * to prevent javascript: and data: URI injection.
 */
export function isSafeUrl(url: string): boolean {
  try {
    const parsed = new URL(url, window.location.origin);
    return parsed.protocol === "https:" || parsed.protocol === "http:";
  } catch {
    return false;
  }
}

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
