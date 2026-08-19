import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { Odometer } from "@/components/ui/Odometer";
import { easeSettle } from "@/lib/motion";

/** Manually steppable rAF + performance.now mock. */
function installRafMock() {
  let now = 0;
  let nextId = 1;
  const queue = new Map<number, FrameRequestCallback>();

  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    const id = nextId++;
    queue.set(id, cb);
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => {
    queue.delete(id);
  });
  vi.spyOn(performance, "now").mockImplementation(() => now);

  return {
    /** Advance the clock and flush one frame batch. */
    step(ms: number) {
      now += ms;
      const callbacks = [...queue.values()];
      queue.clear();
      for (const cb of callbacks) cb(now);
    },
    /** Run frames until the queue drains (animation finished). */
    finish(frameMs = 16, maxFrames = 200) {
      let frames = 0;
      while (queue.size > 0 && frames < maxFrames) {
        this.step(frameMs);
        frames++;
      }
    },
    get pending() {
      return queue.size;
    },
  };
}

function setReducedMotion(matches: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("prefers-reduced-motion") ? matches : false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }));
}

/** The visible (animated overlay) text node — the second aria-hidden span. */
function visibleText(container: HTMLElement): string {
  const spans = container.querySelectorAll("span[aria-hidden]");
  return spans[spans.length - 1].textContent ?? "";
}

describe("Odometer", () => {
  let raf: ReturnType<typeof installRafMock>;

  beforeEach(() => {
    setReducedMotion(false);
    raf = installRafMock();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders the final value immediately for width reservation (no layout shift)", () => {
    const { container } = render(<Odometer value={1234} duration={400} />);
    // First aria-hidden span is the invisible width reserver with the final value.
    const reserver = container.querySelector("span.invisible");
    expect(reserver).not.toBeNull();
    expect(reserver!.textContent).toBe((1234).toLocaleString());
  });

  it("starts at 0 on first mount and eases up to the target", () => {
    const { container } = render(<Odometer value={100} duration={400} />);
    expect(visibleText(container)).toBe("0");

    // Halfway: value follows the settle curve (monotonic, past linear midpoint).
    act(() => raf.step(200));
    const mid = Number(visibleText(container).replace(/,/g, ""));
    expect(mid).toBeGreaterThan(50);
    expect(mid).toBeLessThan(100);
    expect(mid).toBe(Math.round(100 * easeSettle(0.5)));

    act(() => raf.finish());
    expect(visibleText(container)).toBe("100");
  });

  it("eases from the previous value on update, not from 0", () => {
    const { container, rerender } = render(<Odometer value={100} duration={400} />);
    act(() => raf.finish());
    expect(visibleText(container)).toBe("100");

    rerender(<Odometer value={200} duration={400} />);
    act(() => raf.step(40)); // 10% in
    const v = Number(visibleText(container).replace(/,/g, ""));
    expect(v).toBeGreaterThanOrEqual(100); // never dips back toward 0
    expect(v).toBeLessThan(200);

    act(() => raf.finish());
    expect(visibleText(container)).toBe("200");
  });

  it("applies the format function every frame", () => {
    const format = (v: number) => `$${v.toFixed(2)}`;
    const { container } = render(<Odometer value={5} format={format} duration={400} />);
    expect(visibleText(container)).toBe("$0.00");
    act(() => raf.finish());
    expect(visibleText(container)).toBe("$5.00");
  });

  it("jumps instantly when prefers-reduced-motion is set", () => {
    setReducedMotion(true);
    const { container } = render(<Odometer value={4321} duration={400} />);
    expect(visibleText(container)).toBe((4321).toLocaleString());
    expect(raf.pending).toBe(0); // no animation scheduled
  });

  it("jumps instantly on update under reduced motion", () => {
    setReducedMotion(true);
    const { container, rerender } = render(<Odometer value={10} duration={400} />);
    rerender(<Odometer value={99} duration={400} />);
    expect(visibleText(container)).toBe("99");
    expect(raf.pending).toBe(0);
  });

  it("exposes the final value as an accessible label", () => {
    render(<Odometer value={777} duration={400} />);
    expect(screen.getByLabelText("777")).toBeInTheDocument();
  });
});
