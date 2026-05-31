import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";

vi.mock("@/api/client", () => ({
  api: {
    authHeaders: vi.fn(() => ({ "X-API-Key": "test" })),
    setApiKey: vi.fn(),
    isMockMode: false,
    onMockChange: vi.fn(() => () => {}),
  },
}));

vi.mock("@/lib/constants", () => ({
  API_BASE_URL: "/api",
  POLL_INTERVAL: 5000,
}));

// Recharts uses ResizeObserver which jsdom does not implement.
class _ResizeObserver implements ResizeObserver {
  observe(_target: Element, _options?: ResizeObserverOptions): void {}
  unobserve(_target: Element): void {}
  disconnect(): void {}
}
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = _ResizeObserver as unknown as typeof ResizeObserver;
}

import { WorkQueuePanel } from "./WorkQueuePanel";

// ---------------------------------------------------------------------------
// Fake EventSource
// ---------------------------------------------------------------------------

type Listener = (ev: MessageEvent | Event) => void;

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  withCredentials: boolean;
  readyState = 0;
  private listeners = new Map<string, Set<Listener>>();
  onerror: ((ev: Event) => void) | null = null;
  onopen: ((ev: Event) => void) | null = null;

  constructor(url: string, init?: { withCredentials?: boolean }) {
    this.url = url;
    this.withCredentials = Boolean(init?.withCredentials);
    FakeEventSource.instances.push(this);
    // Defer "open" to the next microtask so subscribers can attach first.
    queueMicrotask(() => {
      this.readyState = 1;
      this.dispatch("open", new Event("open"));
    });
  }

  addEventListener(type: string, fn: Listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(fn);
  }

  removeEventListener(type: string, fn: Listener) {
    this.listeners.get(type)?.delete(fn);
  }

  close() {
    this.readyState = 2;
  }

  dispatch(type: string, ev: MessageEvent | Event) {
    this.listeners.get(type)?.forEach((fn) => fn(ev));
  }

  emitWorkStats(payload: Record<string, unknown>) {
    const ev = new MessageEvent("work_stats", {
      data: JSON.stringify(payload),
    });
    this.dispatch("work_stats", ev);
  }
}

beforeEach(() => {
  FakeEventSource.instances = [];
  // @ts-expect-error - install fake on global
  globalThis.EventSource = FakeEventSource;
});

afterEach(() => {
  vi.restoreAllMocks();
});

function sample(depth: number, extra: Record<string, unknown> = {}) {
  return {
    depth,
    pending: 0,
    oldest_queued_at: null,
    workers_polling: 0,
    ts: Date.now() / 1000,
    ...extra,
  };
}

async function waitForSource() {
  await waitFor(() =>
    expect(FakeEventSource.instances.length).toBeGreaterThan(0)
  );
  return FakeEventSource.instances[FakeEventSource.instances.length - 1];
}

describe("WorkQueuePanel", () => {
  it("returns null when environmentId is missing", () => {
    const { container } = render(<WorkQueuePanel environmentId={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders depth from the first work_stats event", async () => {
    render(<WorkQueuePanel environmentId="env_1" />);
    const es = await waitForSource();

    await act(async () => {
      es.emitWorkStats(sample(3));
    });

    expect(screen.getByTestId("work-queue-depth")).toHaveTextContent("3");
  });

  it("flips the status pill to amber at depth=10", async () => {
    render(<WorkQueuePanel environmentId="env_1" />);
    const es = await waitForSource();

    await act(async () => {
      es.emitWorkStats(sample(2));
    });
    expect(screen.getByTestId("work-queue-pill")).toHaveAttribute(
      "data-level",
      "green"
    );

    await act(async () => {
      es.emitWorkStats(sample(10));
    });
    expect(screen.getByTestId("work-queue-pill")).toHaveAttribute(
      "data-level",
      "amber"
    );
  });

  it("collects multiple samples for the sparkline", async () => {
    render(<WorkQueuePanel environmentId="env_1" />);
    const es = await waitForSource();

    await act(async () => {
      es.emitWorkStats(sample(1));
      es.emitWorkStats(sample(2));
      es.emitWorkStats(sample(3));
      es.emitWorkStats(sample(4));
      es.emitWorkStats(sample(5));
    });

    const spark = screen.getByTestId("work-queue-sparkline");
    expect(spark.getAttribute("data-sample-count")).toBe("5");
  });

  it("subscribes to the correct SSE endpoint", async () => {
    render(<WorkQueuePanel environmentId="env_xyz" />);
    const es = await waitForSource();
    expect(es.url).toContain("/admin/environments/env_xyz/work/stream");
  });

  it("renders secondary metrics", async () => {
    render(<WorkQueuePanel environmentId="env_1" />);
    const es = await waitForSource();

    await act(async () => {
      es.emitWorkStats(
        sample(0, {
          pending: 4,
          workers_polling: 7,
        })
      );
    });

    expect(screen.getByTestId("work-queue-pending")).toHaveTextContent("4");
    expect(screen.getByTestId("work-queue-workers")).toHaveTextContent("7");
  });

  it("renders the depth value with aria-live polite", async () => {
    render(<WorkQueuePanel environmentId="env_1" />);
    await waitForSource();
    expect(screen.getByTestId("work-queue-depth")).toHaveAttribute(
      "aria-live",
      "polite"
    );
  });
});
