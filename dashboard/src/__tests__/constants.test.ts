import { describe, it, expect } from "vitest";
import { STATUS_COLORS, STATUS_DOT_COLORS, POLL_INTERVAL } from "@/lib/constants";

describe("STATUS_COLORS", () => {
  const allStatuses = [
    "queued", "running", "completed", "failed",
    "partial", "cancelled", "budget_exceeded",
    "pending", "skipped", "awaiting_approval",
  ];

  it("has entries for all known statuses", () => {
    for (const status of allStatuses) {
      expect(STATUS_COLORS[status]).toBeDefined();
    }
  });

  it("each entry contains bg, text, and border classes", () => {
    for (const status of allStatuses) {
      expect(STATUS_COLORS[status]).toMatch(/bg-/);
      expect(STATUS_COLORS[status]).toMatch(/text-/);
      expect(STATUS_COLORS[status]).toMatch(/border-/);
    }
  });
});

describe("STATUS_DOT_COLORS", () => {
  it("has matching keys with STATUS_COLORS", () => {
    const colorKeys = Object.keys(STATUS_COLORS).sort();
    const dotKeys = Object.keys(STATUS_DOT_COLORS).sort();
    expect(dotKeys).toEqual(colorKeys);
  });

  it("running status has animate-pulse", () => {
    expect(STATUS_DOT_COLORS.running).toContain("animate-pulse");
  });

  it("awaiting_approval status has animate-pulse", () => {
    expect(STATUS_DOT_COLORS.awaiting_approval).toContain("animate-pulse");
  });
});

describe("POLL_INTERVAL", () => {
  it("is a positive number", () => {
    expect(POLL_INTERVAL).toBeGreaterThan(0);
  });

  it("is 5 seconds", () => {
    expect(POLL_INTERVAL).toBe(5000);
  });
});
