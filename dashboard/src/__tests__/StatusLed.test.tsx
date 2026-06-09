import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusLed } from "@/components/ui/StatusLed";
import { getLedConfig } from "@/lib/statusLed";

describe("getLedConfig state mapping", () => {
  it("maps running to a breathing light in the running color", () => {
    expect(getLedConfig("running")).toEqual({
      state: "breathing",
      color: "var(--color-running)",
    });
  });

  it("maps completed/succeeded to a steady green light", () => {
    expect(getLedConfig("completed")).toEqual({
      state: "on",
      color: "var(--color-success)",
    });
    expect(getLedConfig("succeeded")).toEqual({
      state: "on",
      color: "var(--color-success)",
    });
  });

  it("maps failed to a flicker-then-steady red light", () => {
    expect(getLedConfig("failed")).toEqual({
      state: "flicker",
      color: "var(--color-error)",
    });
  });

  it("maps queued and pending to dim lights", () => {
    expect(getLedConfig("queued").state).toBe("dim");
    expect(getLedConfig("queued").color).toBe("var(--color-queued)");
    expect(getLedConfig("pending").state).toBe("dim");
  });

  it("maps cancelled and skipped to hollow rings", () => {
    expect(getLedConfig("cancelled").state).toBe("hollow");
    expect(getLedConfig("skipped").state).toBe("hollow");
  });

  it("maps awaiting_approval to a breathing warning light", () => {
    expect(getLedConfig("awaiting_approval")).toEqual({
      state: "breathing",
      color: "var(--color-warning)",
    });
  });

  it("maps health states to indicator semantics", () => {
    expect(getLedConfig("healthy").state).toBe("on");
    expect(getLedConfig("unhealthy").state).toBe("flicker");
    expect(getLedConfig("degraded").state).toBe("breathing");
    expect(getLedConfig("unconfigured").state).toBe("hollow");
  });

  it("maps version lifecycle states", () => {
    expect(getLedConfig("production").state).toBe("on");
    expect(getLedConfig("staging").state).toBe("on");
    expect(getLedConfig("staging").color).toBe("var(--color-warning)");
    expect(getLedConfig("draft").state).toBe("dim");
    expect(getLedConfig("archived").state).toBe("hollow");
  });

  it("falls back to a dim muted light for unknown statuses", () => {
    expect(getLedConfig("does_not_exist")).toEqual({
      state: "dim",
      color: "var(--color-muted)",
    });
    expect(getLedConfig("")).toEqual({
      state: "dim",
      color: "var(--color-muted)",
    });
  });
});

describe("StatusLed", () => {
  it("renders the raw status as label by default", () => {
    render(<StatusLed status="completed" />);
    expect(screen.getByText("completed")).toBeInTheDocument();
  });

  it("renders a custom label when provided", () => {
    render(<StatusLed status="failed" label="Failing" />);
    expect(screen.getByText("Failing")).toBeInTheDocument();
    expect(screen.queryByText("failed")).not.toBeInTheDocument();
  });

  it("hides the label when showLabel is false", () => {
    const { container } = render(<StatusLed status="running" showLabel={false} />);
    expect(screen.queryByText("running")).not.toBeInTheDocument();
    expect(container.querySelector(".led")).not.toBeNull();
  });

  it("applies the LED state class for each status", () => {
    const cases: Array<[string, string]> = [
      ["running", "led--breathing"],
      ["completed", "led--on"],
      ["failed", "led--flicker"],
      ["queued", "led--dim"],
      ["cancelled", "led--hollow"],
    ];
    for (const [status, cls] of cases) {
      const { container, unmount } = render(<StatusLed status={status} />);
      expect(container.querySelector(".led")?.classList.contains(cls)).toBe(true);
      unmount();
    }
  });

  it("exposes the status and led state as data attributes", () => {
    const { container } = render(<StatusLed status="running" />);
    const root = container.querySelector("span[data-status]");
    expect(root?.getAttribute("data-status")).toBe("running");
    expect(root?.getAttribute("data-led-state")).toBe("breathing");
  });

  it("renders a small light by default and a larger one for md", () => {
    const { container: sm } = render(<StatusLed status="completed" />);
    expect(sm.querySelector(".led")?.classList.contains("h-2")).toBe(true);

    const { container: md } = render(<StatusLed status="completed" size="md" />);
    expect(md.querySelector(".led")?.classList.contains("h-2.5")).toBe(true);
  });

  it("uses the mono micro label style", () => {
    render(<StatusLed status="completed" />);
    expect(screen.getByText("completed").classList.contains("led-label")).toBe(true);
  });

  it("merges a custom className on the root element", () => {
    const { container } = render(<StatusLed status="completed" className="ml-2" />);
    expect(container.querySelector("span[data-status]")?.className).toContain("ml-2");
  });
});
