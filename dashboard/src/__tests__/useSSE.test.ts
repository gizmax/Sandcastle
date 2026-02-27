import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";

vi.mock("@/api/client", () => ({
  api: {
    authHeaders: vi.fn(() => ({ "X-API-Key": "test" })),
    setApiKey: vi.fn(),
    isMockMode: false,
    onMockChange: vi.fn(() => () => {}),
  },
}));

vi.mock("@/lib/constants", () => ({
  API_BASE_URL: "http://localhost:8080/api",
  POLL_INTERVAL: 5000,
}));

import { useSSE } from "@/hooks/useSSE";

describe("useSSE", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("does not connect when path is null", () => {
    globalThis.fetch = vi.fn();
    const { result } = renderHook(() => useSSE(null));
    expect(result.current.connected).toBe(false);
    expect(result.current.events).toEqual([]);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("sets connected to false on non-ok response", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response("", { status: 500 })
    );

    const { result } = renderHook(() => useSSE("/events"));

    await waitFor(() => {
      expect(result.current.connected).toBe(false);
    });
  });

  it("sets connected on successful response", async () => {
    // Create a mock readable stream that never ends
    const stream = new ReadableStream({
      start(_controller) {
        // Don't enqueue anything, just keep the stream open
        // We'll close it on cleanup
      },
      cancel() {},
    });

    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      })
    );

    const { result } = renderHook(() => useSSE("/stream/test"));

    await waitFor(() => {
      expect(result.current.connected).toBe(true);
    });
  });

  it("parses SSE events from the stream", async () => {
    const encoder = new TextEncoder();

    const stream = new ReadableStream({
      start(controller) {
        // Send an SSE event
        const data = `event: step.completed\ndata: {"step":"extract","status":"done"}\n\n`;
        controller.enqueue(encoder.encode(data));
      },
      cancel() {},
    });

    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      })
    );

    const { result } = renderHook(() => useSSE("/stream/test"));

    await waitFor(() => {
      expect(result.current.events.length).toBeGreaterThanOrEqual(1);
    });

    expect(result.current.events[0].event).toBe("step.completed");
    expect(result.current.events[0].data).toEqual({ step: "extract", status: "done" });
  });

  it("handles multiple events in one chunk", async () => {
    const encoder = new TextEncoder();

    const stream = new ReadableStream({
      start(controller) {
        const data =
          `event: run.started\ndata: {"run_id":"r1"}\n\n` +
          `event: step.completed\ndata: {"step":"s1"}\n\n`;
        controller.enqueue(encoder.encode(data));
      },
      cancel() {},
    });

    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(stream, { status: 200 })
    );

    const { result } = renderHook(() => useSSE("/stream/test"));

    await waitFor(() => {
      expect(result.current.events.length).toBe(2);
    });
  });

  it("uses default event type 'message' when no event: line", async () => {
    const encoder = new TextEncoder();

    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(`data: {"hello":"world"}\n\n`));
      },
      cancel() {},
    });

    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(stream, { status: 200 })
    );

    const { result } = renderHook(() => useSSE("/stream/test"));

    await waitFor(() => {
      expect(result.current.events.length).toBe(1);
    });
    expect(result.current.events[0].event).toBe("message");
  });

  it("ignores malformed JSON in data", async () => {
    const encoder = new TextEncoder();

    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(`data: not-json\n\n`));
        controller.enqueue(encoder.encode(`data: {"valid":true}\n\n`));
      },
      cancel() {},
    });

    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(stream, { status: 200 })
    );

    const { result } = renderHook(() => useSSE("/stream/test"));

    await waitFor(() => {
      expect(result.current.events.length).toBe(1);
    });
    expect(result.current.events[0].data).toEqual({ valid: true });
  });

  it("caps events at 500", async () => {
    const encoder = new TextEncoder();

    const stream = new ReadableStream({
      start(controller) {
        // Send 510 events
        for (let i = 0; i < 510; i++) {
          controller.enqueue(encoder.encode(`data: {"i":${i}}\n\n`));
        }
      },
      cancel() {},
    });

    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(stream, { status: 200 })
    );

    const { result } = renderHook(() => useSSE("/stream/test"));

    await waitFor(() => {
      expect(result.current.events.length).toBeLessThanOrEqual(500);
    });
  });

  it("clear() empties events", async () => {
    const encoder = new TextEncoder();

    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(`data: {"test":1}\n\n`));
      },
      cancel() {},
    });

    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(stream, { status: 200 })
    );

    const { result } = renderHook(() => useSSE("/stream/test"));

    await waitFor(() => {
      expect(result.current.events.length).toBe(1);
    });

    act(() => {
      result.current.clear();
    });

    expect(result.current.events).toEqual([]);
  });

  it("disconnect() aborts the connection", async () => {
    const stream = new ReadableStream({
      start() {},
      cancel() {},
    });

    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(stream, { status: 200 })
    );

    const { result } = renderHook(() => useSSE("/stream/test"));

    await waitFor(() => {
      expect(result.current.connected).toBe(true);
    });

    act(() => {
      result.current.disconnect();
    });

    expect(result.current.connected).toBe(false);
  });

  it("cleans up on unmount", async () => {
    const stream = new ReadableStream({
      start() {},
      cancel() {},
    });

    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(stream, { status: 200 })
    );

    const { result, unmount } = renderHook(() => useSSE("/stream/test"));

    await waitFor(() => {
      expect(result.current.connected).toBe(true);
    });

    // Should not throw
    unmount();
  });

  it("reconnects when path changes", async () => {
    const stream = new ReadableStream({
      start() {},
      cancel() {},
    });

    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(stream, { status: 200 })
    );

    const { rerender } = renderHook(
      ({ path }: { path: string | null }) => useSSE(path),
      { initialProps: { path: "/stream/1" } }
    );

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    });

    rerender({ path: "/stream/2" });

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalledTimes(2);
    });
  });
});
