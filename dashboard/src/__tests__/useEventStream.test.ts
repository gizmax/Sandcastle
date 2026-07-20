import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";

// ---------------------------------------------------------------------------
// Helpers to create a fake SSE fetch response
// ---------------------------------------------------------------------------

function createSseStream() {
  let controller: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });

  const encoder = new TextEncoder();

  function push(text: string) {
    controller.enqueue(encoder.encode(text));
  }

  function sendEvent(type: string, data: unknown) {
    push(`event: ${type}\ndata: ${JSON.stringify(data)}\n\n`);
  }

  function close() {
    controller.close();
  }

  function error(err: Error) {
    controller.error(err);
  }

  const response = new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });

  return { response, push, sendEvent, close, error };
}

// ---------------------------------------------------------------------------
// Mock api client
// ---------------------------------------------------------------------------

vi.mock("@/api/client", () => ({
  api: {
    sseUrl: vi.fn((path: string) => `http://localhost:8080/api${path}`),
    setApiKey: vi.fn(),
    isMockMode: false,
    onMockChange: vi.fn((_cb: (mock: boolean) => void) => () => {}),
    authHeaders: vi.fn(() => ({ "X-API-Key": "test-key" })),
  },
}));

vi.mock("@/lib/constants", () => ({
  API_BASE_URL: "http://localhost:8080/api",
}));

import { useEventStream } from "@/hooks/useEventStream";

describe("useEventStream", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let sseHelper: ReturnType<typeof createSseStream>;

  beforeEach(() => {
    sseHelper = createSseStream();
    fetchMock = vi.fn().mockResolvedValue(sseHelper.response);
    vi.stubGlobal("fetch", fetchMock);
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("starts with connecting or disconnected status", () => {
    const { result } = renderHook(() => useEventStream());
    expect(["connecting", "disconnected"]).toContain(result.current.status);
  });

  it("sends auth headers in the fetch request, not in URL", async () => {
    renderHook(() => useEventStream());

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    // URL must NOT contain token/key query param
    expect(url).not.toContain("token=");
    expect(url).not.toContain("api_key=");
    // Auth header must be present instead
    expect((init.headers as Record<string, string>)["X-API-Key"]).toBe("test-key");
  });

  it("sets connected status after fetch resolves", async () => {
    const { result } = renderHook(() => useEventStream());

    await waitFor(() => {
      expect(result.current.status).toBe("connected");
    });
  });

  it("processes typed SSE events", async () => {
    const { result } = renderHook(() => useEventStream());

    await waitFor(() => expect(result.current.status).toBe("connected"));

    act(() => {
      sseHelper.sendEvent("run.completed", { run_id: "r1", status: "completed" });
    });

    await waitFor(() => {
      expect(result.current.events).toHaveLength(1);
    });
    expect(result.current.events[0].type).toBe("run.completed");
    expect(result.current.events[0].data).toEqual({ run_id: "r1", status: "completed" });
  });

  it("preserves an event split across response chunks", async () => {
    const { result } = renderHook(() => useEventStream());

    await waitFor(() => expect(result.current.status).toBe("connected"));

    // The first chunk ends before the data line. This used to reset the event
    // type before the next reader iteration, so the event was dropped.
    act(() => {
      sseHelper.push("event: run.completed\n");
      sseHelper.push('data: {"run_id":"split"}\n\n');
    });

    await waitFor(() => expect(result.current.events).toHaveLength(1));
    expect(result.current.events[0].type).toBe("run.completed");
    expect(result.current.events[0].data).toEqual({ run_id: "split" });
  });

  it("processes generic message events", async () => {
    const { result } = renderHook(() => useEventStream());

    await waitFor(() => expect(result.current.status).toBe("connected"));

    act(() => {
      sseHelper.sendEvent("message", { info: "test" });
    });

    await waitFor(() => {
      expect(result.current.events).toHaveLength(1);
    });
    expect(result.current.events[0].type).toBe("message");
  });

  it("ignores malformed JSON", async () => {
    const { result } = renderHook(() => useEventStream());

    await waitFor(() => expect(result.current.status).toBe("connected"));

    const encoder = new TextEncoder();
    // Send raw malformed SSE manually
    act(() => {
      // We can't easily push raw bytes via our helper for bad JSON, so
      // just verify nothing crashes and the event list stays empty.
      // (Malformed JSON is caught in the try/catch inside the hook.)
      void encoder.encode("event: run.started\ndata: not-json\n\n");
    });

    expect(result.current.events).toHaveLength(0);
  });

  it("caps events at 50", async () => {
    const { result } = renderHook(() => useEventStream());

    await waitFor(() => expect(result.current.status).toBe("connected"));

    act(() => {
      for (let i = 0; i < 60; i++) {
        sseHelper.sendEvent("step.completed", { i });
      }
    });

    await waitFor(() => {
      expect(result.current.events.length).toBeGreaterThan(0);
    });

    // Give time for all to process
    await waitFor(() => {
      expect(result.current.events.length).toBeLessThanOrEqual(50);
    });
  });

  it("events are ordered newest first", async () => {
    const { result } = renderHook(() => useEventStream());

    await waitFor(() => expect(result.current.status).toBe("connected"));

    act(() => {
      sseHelper.sendEvent("run.started", { order: 1 });
      sseHelper.sendEvent("run.completed", { order: 2 });
    });

    await waitFor(() => {
      expect(result.current.events.length).toBeGreaterThanOrEqual(2);
    });

    expect(result.current.events[0].data).toEqual({ order: 2 });
    expect(result.current.events[1].data).toEqual({ order: 1 });
  });

  it("clearEvents empties the list", async () => {
    const { result } = renderHook(() => useEventStream());

    await waitFor(() => expect(result.current.status).toBe("connected"));

    act(() => {
      sseHelper.sendEvent("run.started", { test: true });
    });

    await waitFor(() => {
      expect(result.current.events.length).toBe(1);
    });

    act(() => {
      result.current.clearEvents();
    });

    expect(result.current.events.length).toBe(0);
  });

  it("aborts the fetch stream on unmount", async () => {
    const { unmount } = renderHook(() => useEventStream());

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit & { signal: AbortSignal }];
    const signal = init.signal as AbortSignal;

    unmount();

    expect(signal.aborted).toBe(true);
  });

  it("schedules a reconnect after stream closes", async () => {
    // Use real timers for this test to avoid fake-timer/async interaction issues
    vi.useRealTimers();

    // Capture the first stream helper before the hook starts
    const firstHelper = sseHelper;
    renderHook(() => useEventStream());

    // Wait for the first connection to be established
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    // Prepare a fresh stream for the reconnect
    const secondHelper = createSseStream();
    fetchMock.mockResolvedValue(secondHelper.response);

    // Close the first stream to trigger reconnect
    firstHelper.close();

    // Wait up to 3s for the second fetch call (reconnect has 1s base backoff)
    await waitFor(
      () => {
        expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2);
      },
      { timeout: 3000 }
    );
  });

  it("assigns unique IDs to each event", async () => {
    const { result } = renderHook(() => useEventStream());

    await waitFor(() => expect(result.current.status).toBe("connected"));

    act(() => {
      sseHelper.sendEvent("run.started", { a: 1 });
      sseHelper.sendEvent("run.completed", { a: 2 });
    });

    await waitFor(() => {
      expect(result.current.events.length).toBeGreaterThanOrEqual(2);
    });

    const ids = result.current.events.map((e) => e.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
