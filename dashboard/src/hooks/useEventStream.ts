import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/api/client";
import { API_BASE_URL } from "@/lib/constants";

export type ConnectionStatus = "connected" | "connecting" | "disconnected";

export interface StreamEvent {
  id: string;
  type: string;
  data: Record<string, unknown>;
  timestamp: Date;
}

// Reconnect with exponential backoff - starts at 1s, maxes at 30s
const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 30000;
// In mock mode, stop retrying after this many attempts to avoid log spam
const MOCK_MAX_ATTEMPTS = 2;

function getBackoffDelay(attempt: number): number {
  const delay = Math.min(BASE_DELAY_MS * Math.pow(2, attempt), MAX_DELAY_MS);
  // Add small jitter to avoid thundering herd
  return delay + Math.random() * 500;
}

let nextEventId = 1;

/**
 * Hook that connects to the global SSE endpoint GET /events.
 * Uses fetch-based streaming with auth headers instead of native EventSource
 * to avoid leaking the API key in URL query parameters (server logs, history).
 * In mock/demo mode, skips connection entirely to avoid console spam.
 * Should be used via EventStreamProvider context - not directly in components.
 */
export function useEventStream() {
  const [status, setStatus] = useState<ConnectionStatus>("disconnected");
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const attemptRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unmountedRef = useRef(false);
  const mockModeRef = useRef(api.isMockMode);

  const addEvent = useCallback((type: string, data: Record<string, unknown>) => {
    const event: StreamEvent = {
      id: `evt-${nextEventId++}`,
      type,
      data,
      timestamp: new Date(),
    };
    setEvents((prev) => {
      const updated = [event, ...prev];
      // Keep last 50 events in memory
      if (updated.length > 50) return updated.slice(0, 50);
      return updated;
    });
  }, []);

  const scheduleReconnect = useCallback((connectFn: () => void) => {
    if (unmountedRef.current) return;

    if (attemptRef.current >= MOCK_MAX_ATTEMPTS) {
      mockModeRef.current = true;
      console.info("[Sandcastle] Event stream unavailable, stopping reconnection attempts");
      return;
    }

    const delay = getBackoffDelay(attemptRef.current);
    attemptRef.current += 1;
    console.info(`[Sandcastle] Event stream disconnected, reconnecting in ${Math.round(delay)}ms`);

    reconnectTimerRef.current = setTimeout(() => {
      reconnectTimerRef.current = null;
      if (!unmountedRef.current) connectFn();
    }, delay);
  }, []);

  const connect = useCallback(() => {
    // In mock mode, don't attempt SSE connections at all
    if (mockModeRef.current) {
      setStatus("disconnected");
      return;
    }

    // Clean up any existing connection
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }

    setStatus("connecting");

    const controller = new AbortController();
    abortRef.current = controller;

    // Use fetch with auth headers instead of EventSource with ?token= query param
    // to avoid leaking the API key in server access logs and browser history.
    const url = `${API_BASE_URL}/events`;

    (async () => {
      try {
        const res = await fetch(url, {
          headers: api.authHeaders(),
          signal: controller.signal,
        });

        if (!res.ok || !res.body) {
          if (!unmountedRef.current) {
            setStatus("disconnected");
            scheduleReconnect(connect);
          }
          return;
        }

        setStatus("connected");
        attemptRef.current = 0;
        console.info("[Sandcastle] Event stream connected");

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        const EVENT_TYPES = new Set([
          "run.started", "run.completed", "run.failed",
          "step.started", "step.completed", "step.failed",
          "dlq.new", "message",
        ]);

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          let currentEvent = "message";
          let currentData = "";

          for (const line of lines) {
            if (line.startsWith("event: ")) {
              currentEvent = line.slice(7).trim();
            } else if (line.startsWith("data: ")) {
              currentData += (currentData ? "\n" : "") + line.slice(6);
            } else if (line.trim() === "" && currentData) {
              // Blank line = dispatch accumulated event per SSE spec
              if (EVENT_TYPES.has(currentEvent)) {
                try {
                  const data = JSON.parse(currentData) as Record<string, unknown>;
                  addEvent(currentEvent, data);
                } catch {
                  // Ignore parse errors on malformed messages
                }
              }
              currentEvent = "message";
              currentData = "";
            }
          }
        }

        // Stream ended cleanly - reconnect
        if (!unmountedRef.current) {
          setStatus("disconnected");
          scheduleReconnect(connect);
        }
      } catch (e: unknown) {
        if (e instanceof DOMException && e.name === "AbortError") return;
        if (!unmountedRef.current) {
          setStatus("disconnected");
          scheduleReconnect(connect);
        }
      }
    })();
  }, [addEvent, scheduleReconnect]);

  // Track mock mode changes from the API client
  useEffect(() => {
    const unsub = api.onMockChange((mock) => {
      mockModeRef.current = mock;
      if (mock) {
        // If switching to mock mode, close any active connection
        if (abortRef.current) {
          abortRef.current.abort();
          abortRef.current = null;
        }
        if (reconnectTimerRef.current) {
          clearTimeout(reconnectTimerRef.current);
          reconnectTimerRef.current = null;
        }
        setStatus("disconnected");
      }
    });
    return unsub;
  }, []);

  useEffect(() => {
    unmountedRef.current = false;
    connect();

    return () => {
      unmountedRef.current = true;
      if (abortRef.current) {
        abortRef.current.abort();
        abortRef.current = null;
      }
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };
  }, [connect]);

  const clearEvents = useCallback(() => setEvents([]), []);

  return { events, status, clearEvents };
}
