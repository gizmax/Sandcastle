import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import React from "react";
import {
  useEventStreamContext,
  EventStreamContext,
  type EventStreamContextValue,
} from "@/hooks/useEventStreamContext";

describe("useEventStreamContext", () => {
  // Suppress console.error for the expected throw
  beforeEach(() => {
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("throws when used outside EventStreamProvider", () => {
    expect(() => {
      renderHook(() => useEventStreamContext());
    }).toThrow("useEventStreamContext must be used within EventStreamProvider");
  });

  it("returns context value when inside provider", () => {
    const mockValue: EventStreamContextValue = {
      events: [],
      connectionStatus: "connected",
      subscribe: vi.fn(() => () => {}),
      clearEvents: vi.fn(),
    };

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <EventStreamContext.Provider value={mockValue}>
        {children}
      </EventStreamContext.Provider>
    );

    const { result } = renderHook(() => useEventStreamContext(), { wrapper });

    expect(result.current.connectionStatus).toBe("connected");
    expect(result.current.events).toEqual([]);
    expect(typeof result.current.subscribe).toBe("function");
    expect(typeof result.current.clearEvents).toBe("function");
  });
});
