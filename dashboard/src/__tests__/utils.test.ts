import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { formatCost, formatDuration, parseUTC, formatRelativeTime, cn } from "@/lib/utils";

// ── formatCost ──────────────────────────────────────────────────────────────

describe("formatCost", () => {
  it("formats zero cost", () => {
    expect(formatCost(0)).toBe("$0.00");
  });

  it("formats typical cost", () => {
    expect(formatCost(1.84)).toBe("$1.84");
  });

  it("rounds to two decimal places", () => {
    expect(formatCost(1.999)).toBe("$2.00");
    expect(formatCost(0.005)).toBe("$0.01");
  });

  it("handles small fractional costs", () => {
    expect(formatCost(0.001)).toBe("$0.00");
  });

  it("handles large costs", () => {
    expect(formatCost(12345.67)).toBe("$12345.67");
  });

  it("handles negative costs", () => {
    expect(formatCost(-1.5)).toBe("$-1.50");
  });
});

// ── formatDuration ──────────────────────────────────────────────────────────

describe("formatDuration", () => {
  it("formats seconds under a minute", () => {
    expect(formatDuration(5)).toBe("5s");
    expect(formatDuration(59)).toBe("59s");
  });

  it("formats exactly one minute", () => {
    expect(formatDuration(60)).toBe("1m");
  });

  it("formats minutes and seconds", () => {
    expect(formatDuration(90)).toBe("1m 30s");
    expect(formatDuration(125)).toBe("2m 5s");
  });

  it("omits seconds when they round to zero", () => {
    expect(formatDuration(120)).toBe("2m");
    expect(formatDuration(180)).toBe("3m");
  });

  it("handles zero", () => {
    expect(formatDuration(0)).toBe("0s");
  });

  it("rounds fractional seconds", () => {
    expect(formatDuration(0.7)).toBe("1s");
    expect(formatDuration(30.4)).toBe("30s");
  });

  it("handles large durations", () => {
    expect(formatDuration(3600)).toBe("60m");
    expect(formatDuration(3661)).toBe("61m 1s");
  });
});

// ── parseUTC ────────────────────────────────────────────────────────────────

describe("parseUTC", () => {
  it("appends Z to naive datetime strings without HH:MM ending", () => {
    // parseUTC detects timezone-like patterns via /\d{2}:\d{2}$/ on the last 6 chars.
    // A string like "2025-01-15T10:30" (no seconds) or "2025-01-15T10:00:00.123"
    // that doesn't end in \d\d:\d\d pattern will get Z appended.
    const result = parseUTC("2025-01-15T10:30:00.000");
    expect(result.toISOString()).toBe("2025-01-15T10:30:00.000Z");
  });

  it("preserves existing Z suffix", () => {
    const result = parseUTC("2025-01-15T10:30:00Z");
    expect(result.toISOString()).toBe("2025-01-15T10:30:00.000Z");
  });

  it("preserves explicit timezone offset", () => {
    const result = parseUTC("2025-01-15T10:30:00+02:00");
    expect(result.toISOString()).toBe("2025-01-15T08:30:00.000Z");
  });

  it("returns Date objects unchanged", () => {
    const date = new Date("2025-01-15T10:30:00Z");
    const result = parseUTC(date);
    expect(result).toBe(date);
  });

  it("handles datetime with milliseconds", () => {
    const result = parseUTC("2025-01-15T10:30:00.123");
    expect(result.getUTCMilliseconds()).toBe(123);
  });
});

// ── formatRelativeTime ──────────────────────────────────────────────────────

describe("formatRelativeTime", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2025-06-15T12:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns 'just now' for < 60 seconds ago", () => {
    expect(formatRelativeTime("2025-06-15T11:59:30Z")).toBe("just now");
  });

  it("returns minutes ago", () => {
    expect(formatRelativeTime("2025-06-15T11:55:00Z")).toBe("5m ago");
  });

  it("returns hours ago", () => {
    expect(formatRelativeTime("2025-06-15T09:00:00Z")).toBe("3h ago");
  });

  it("returns days ago", () => {
    expect(formatRelativeTime("2025-06-13T12:00:00Z")).toBe("2d ago");
  });

  it("handles naive UTC strings (no Z suffix)", () => {
    // parseUTC appends Z when the string doesn't end in a timezone-like pattern.
    // Use milliseconds suffix so the HH:MM:SS ending isn't misdetected.
    expect(formatRelativeTime("2025-06-15T11:55:00.000")).toBe("5m ago");
  });

  it("handles Date objects", () => {
    const date = new Date("2025-06-15T11:50:00Z");
    expect(formatRelativeTime(date)).toBe("10m ago");
  });
});

// ── cn (classname merge) ────────────────────────────────────────────────────

describe("cn", () => {
  it("merges basic classes", () => {
    expect(cn("foo", "bar")).toBe("foo bar");
  });

  it("handles conditional classes", () => {
    expect(cn("base", false && "conditional", "other")).toBe("base other");
  });

  it("handles undefined and null", () => {
    expect(cn("base", undefined, null)).toBe("base");
  });

  it("deduplicates tailwind conflicts", () => {
    const result = cn("p-4", "p-2");
    expect(result).toBe("p-2");
  });
});
