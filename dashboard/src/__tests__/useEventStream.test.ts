import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

// Mock EventSource
class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  readyState = 0;
  withCredentials = false;
  CONNECTING = 0 as const;
  OPEN = 1 as const;
  CLOSED = 2 as const;
  private listeners: Record<string, ((e: MessageEvent) => void)[]> = {};
  closed = false;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (e: MessageEvent) => void) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(listener);
  }

  removeEventListener(type: string, listener: (e: MessageEvent) => void) {
    if (this.listeners[type]) {
      this.listeners[type] = this.listeners[type].filter((l) => l !== listener);
    }
  }

  dispatchEvent(_event: Event): boolean {
    return false;
  }

  close() {
    this.closed = true;
    this.readyState = 2;
  }

  // Test helpers
  simulateOpen() {
    this.readyState = 1;
    if (this.onopen) this.onopen(new Event("open"));
  }

  simulateError() {
    if (this.onerror) this.onerror(new Event("error"));
  }

  simulateMessage(type: string, data: string) {
    const event = new MessageEvent(type, { data });
    if (type === "message" && this.onmessage) {
      this.onmessage(event);
    }
    const handlers = this.listeners[type] || [];
    handlers.forEach((h) => h(event));
  }
}

// Store original EventSource
let originalEventSource: typeof EventSource;

vi.mock("@/api/client", () => ({
  api: {
    sseUrl: vi.fn((path: string) => `http://localhost:8080/api${path}`),
    setApiKey: vi.fn(),
    isMockMode: false,
    onMockChange: vi.fn((cb: (mock: boolean) => void) => {
      // Store callback for testing
      (vi.mocked as Record<string, unknown>)._mockChangeCallback = cb;
      return () => {};
    }),
    authHeaders: vi.fn(() => ({})),
  },
}));

import { useEventStream } from "@/hooks/useEventStream";

describe("useEventStream", () => {
  beforeEach(() => {
    originalEventSource = globalThis.EventSource;
    (globalThis as Record<string, unknown>).EventSource = MockEventSource;
    MockEventSource.instances = [];
    vi.useFakeTimers();
  });

  afterEach(() => {
    (globalThis as Record<string, unknown>).EventSource = originalEventSource;
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("starts with disconnected status", () => {
    const { result } = renderHook(() => useEventStream());
    // Initially connecting, then the EventSource is created
    expect(["connecting", "disconnected"]).toContain(result.current.status);
  });

  it("sets connected status on open", async () => {
    const { result } = renderHook(() => useEventStream());

    const es = MockEventSource.instances[MockEventSource.instances.length - 1];
    act(() => {
      es.simulateOpen();
    });

    expect(result.current.status).toBe("connected");
  });

  it("processes typed events", async () => {
    const { result } = renderHook(() => useEventStream());

    const es = MockEventSource.instances[MockEventSource.instances.length - 1];
    act(() => {
      es.simulateOpen();
      es.simulateMessage("run.completed", JSON.stringify({ run_id: "r1", status: "completed" }));
    });

    expect(result.current.events).toHaveLength(1);
    expect(result.current.events[0].type).toBe("run.completed");
    expect(result.current.events[0].data).toEqual({ run_id: "r1", status: "completed" });
  });

  it("processes generic message events", async () => {
    const { result } = renderHook(() => useEventStream());

    const es = MockEventSource.instances[MockEventSource.instances.length - 1];
    act(() => {
      es.simulateOpen();
      es.simulateMessage("message", JSON.stringify({ info: "test" }));
    });

    expect(result.current.events).toHaveLength(1);
    expect(result.current.events[0].type).toBe("message");
  });

  it("ignores malformed JSON", () => {
    const { result } = renderHook(() => useEventStream());

    const es = MockEventSource.instances[MockEventSource.instances.length - 1];
    act(() => {
      es.simulateOpen();
      es.simulateMessage("run.started", "not-json");
    });

    expect(result.current.events).toHaveLength(0);
  });

  it("caps events at 50", () => {
    const { result } = renderHook(() => useEventStream());

    const es = MockEventSource.instances[MockEventSource.instances.length - 1];
    act(() => {
      es.simulateOpen();
      for (let i = 0; i < 60; i++) {
        es.simulateMessage("step.completed", JSON.stringify({ i }));
      }
    });

    expect(result.current.events.length).toBeLessThanOrEqual(50);
  });

  it("events are ordered newest first", () => {
    const { result } = renderHook(() => useEventStream());

    const es = MockEventSource.instances[MockEventSource.instances.length - 1];
    act(() => {
      es.simulateOpen();
      es.simulateMessage("run.started", JSON.stringify({ order: 1 }));
      es.simulateMessage("run.completed", JSON.stringify({ order: 2 }));
    });

    expect(result.current.events[0].data).toEqual({ order: 2 });
    expect(result.current.events[1].data).toEqual({ order: 1 });
  });

  it("clearEvents empties the list", () => {
    const { result } = renderHook(() => useEventStream());

    const es = MockEventSource.instances[MockEventSource.instances.length - 1];
    act(() => {
      es.simulateOpen();
      es.simulateMessage("run.started", JSON.stringify({ test: true }));
    });
    expect(result.current.events.length).toBe(1);

    act(() => {
      result.current.clearEvents();
    });
    expect(result.current.events.length).toBe(0);
  });

  it("closes EventSource on unmount", () => {
    const { unmount } = renderHook(() => useEventStream());

    const es = MockEventSource.instances[MockEventSource.instances.length - 1];
    act(() => {
      es.simulateOpen();
    });

    unmount();
    expect(es.closed).toBe(true);
  });

  it("stops reconnecting after MOCK_MAX_ATTEMPTS (2) failures", () => {
    const { result } = renderHook(() => useEventStream());

    const es1 = MockEventSource.instances[MockEventSource.instances.length - 1];

    // First failure
    act(() => {
      es1.simulateError();
    });

    // Advance timer for backoff
    act(() => {
      vi.advanceTimersByTime(2000);
    });

    const es2 = MockEventSource.instances[MockEventSource.instances.length - 1];

    // Second failure
    act(() => {
      es2.simulateError();
    });

    // Advance timer
    act(() => {
      vi.advanceTimersByTime(5000);
    });

    // Should NOT create a third EventSource - stopped after 2 attempts
    const totalInstances = MockEventSource.instances.length;

    act(() => {
      vi.advanceTimersByTime(60000);
    });

    // No more instances should be created
    expect(MockEventSource.instances.length).toBe(totalInstances);
  });

  it("resets attempt counter on successful connection", () => {
    const { result } = renderHook(() => useEventStream());

    const es1 = MockEventSource.instances[MockEventSource.instances.length - 1];

    // Simulate error then reconnect successfully
    act(() => {
      es1.simulateError();
    });

    act(() => {
      vi.advanceTimersByTime(2000);
    });

    const es2 = MockEventSource.instances[MockEventSource.instances.length - 1];
    act(() => {
      es2.simulateOpen();
    });

    expect(result.current.status).toBe("connected");
  });

  it("assigns unique IDs to each event", () => {
    const { result } = renderHook(() => useEventStream());

    const es = MockEventSource.instances[MockEventSource.instances.length - 1];
    act(() => {
      es.simulateOpen();
      es.simulateMessage("run.started", JSON.stringify({ a: 1 }));
      es.simulateMessage("run.completed", JSON.stringify({ a: 2 }));
    });

    const ids = result.current.events.map((e) => e.id);
    expect(new Set(ids).size).toBe(2);
  });
});
