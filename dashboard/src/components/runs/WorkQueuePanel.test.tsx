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

function createSseStream() {
  let controller: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  const encoder = new TextEncoder();

  return {
    response: new Response(stream, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    }),
    emitWorkStats(payload: Record<string, unknown>) {
      controller.enqueue(encoder.encode(`event: work_stats\ndata: ${JSON.stringify(payload)}\n\n`));
    },
  };
}

let fetchMock: ReturnType<typeof vi.fn>;
let stream: ReturnType<typeof createSseStream>;

beforeEach(() => {
  stream = createSseStream();
  fetchMock = vi.fn().mockResolvedValue(stream.response);
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
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

async function waitForStream() {
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  return stream;
}

describe("WorkQueuePanel", () => {
  it("returns null when environmentId is missing", () => {
    const { container } = render(<WorkQueuePanel environmentId={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders depth from the first work_stats event", async () => {
    render(<WorkQueuePanel environmentId="env_1" />);
    const sse = await waitForStream();

    await act(async () => {
      sse.emitWorkStats(sample(3));
    });

    expect(screen.getByTestId("work-queue-depth")).toHaveTextContent("3");
  });

  it("flips the status pill to amber at depth=10", async () => {
    render(<WorkQueuePanel environmentId="env_1" />);
    const sse = await waitForStream();

    await act(async () => {
      sse.emitWorkStats(sample(2));
    });
    expect(screen.getByTestId("work-queue-pill")).toHaveAttribute(
      "data-level",
      "green"
    );

    await act(async () => {
      sse.emitWorkStats(sample(10));
    });
    expect(screen.getByTestId("work-queue-pill")).toHaveAttribute(
      "data-level",
      "amber"
    );
  });

  it("collects multiple samples for the sparkline", async () => {
    render(<WorkQueuePanel environmentId="env_1" />);
    const sse = await waitForStream();

    await act(async () => {
      sse.emitWorkStats(sample(1));
      sse.emitWorkStats(sample(2));
      sse.emitWorkStats(sample(3));
      sse.emitWorkStats(sample(4));
      sse.emitWorkStats(sample(5));
    });

    const spark = screen.getByTestId("work-queue-sparkline");
    expect(spark.getAttribute("data-sample-count")).toBe("5");
  });

  it("subscribes to the correct SSE endpoint", async () => {
    render(<WorkQueuePanel environmentId="env_xyz" />);
    await waitForStream();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/admin/environments/env_xyz/work/stream");
    expect((init.headers as Record<string, string>)["X-API-Key"]).toBe("test");
  });

  it("renders secondary metrics", async () => {
    render(<WorkQueuePanel environmentId="env_1" />);
    const sse = await waitForStream();

    await act(async () => {
      sse.emitWorkStats(
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
    await waitForStream();
    expect(screen.getByTestId("work-queue-depth")).toHaveAttribute(
      "aria-live",
      "polite"
    );
  });

  it("aborts the authenticated stream on unmount", async () => {
    const { unmount } = render(<WorkQueuePanel environmentId="env_1" />);
    await waitForStream();
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];

    unmount();

    expect((init.signal as AbortSignal).aborted).toBe(true);
  });
});
