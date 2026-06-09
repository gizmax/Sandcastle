import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { RunStatusBadge } from "@/components/runs/RunStatusBadge";

describe("RunStatusBadge", () => {
  it("renders the status text", () => {
    render(<RunStatusBadge status="completed" />);
    expect(screen.getByText("completed")).toBeInTheDocument();
  });

  it("renders all known statuses", () => {
    const statuses = [
      "queued", "running", "completed", "failed",
      "partial", "cancelled", "budget_exceeded",
      "pending", "skipped", "awaiting_approval",
    ];
    for (const status of statuses) {
      const { unmount } = render(<RunStatusBadge status={status} />);
      expect(screen.getByText(status)).toBeInTheDocument();
      unmount();
    }
  });

  it("handles unknown status gracefully (falls back to pending colors)", () => {
    render(<RunStatusBadge status="unknown_status" />);
    expect(screen.getByText("unknown_status")).toBeInTheDocument();
  });

  it("renders with small size by default (small LED)", () => {
    const { container } = render(<RunStatusBadge status="completed" />);
    const led = container.querySelector(".led");
    expect(led?.classList.contains("h-2")).toBe(true);
  });

  it("renders with medium size when specified (larger LED)", () => {
    const { container } = render(<RunStatusBadge status="completed" size="md" />);
    const led = container.querySelector(".led");
    expect(led?.classList.contains("h-2.5")).toBe(true);
  });

  it("renders the indicator light", () => {
    const { container } = render(<RunStatusBadge status="running" />);
    const dot = container.querySelector("span > span");
    expect(dot).not.toBeNull();
    expect(dot?.className).toContain("rounded-full");
    expect(dot?.className).toContain("led--breathing");
  });

  it("applies custom className", () => {
    const { container } = render(
      <RunStatusBadge status="completed" className="ml-2" />
    );
    const badge = container.querySelector("span");
    expect(badge?.className).toContain("ml-2");
  });
});
