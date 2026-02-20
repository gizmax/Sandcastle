import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/api/client";

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

function getBackoffDelay(attempt: number): number {
  const delay = Math.min(BASE_DELAY_MS * Math.pow(2, attempt), MAX_DELAY_MS);
  // Add small jitter to avoid thundering herd
  return delay + Math.random() * 500;
}

let nextEventId = 1;

/**
 * Hook that connects to the global SSE endpoint GET /events.
 * Uses the native EventSource API with automatic reconnection and backoff.
 * Should be used via EventStreamProvider context - not directly in components.
 */
export function useEventStream() {
  const [status, setStatus] = useState<ConnectionStatus>("disconnected");
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const esRef = useRef<EventSource | null>(null);
  const attemptRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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

  const connect = useCallback(() => {
    // Clean up any existing connection
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }

    setStatus("connecting");

    const url = api.sseUrl("/events");
    const es = new EventSource(url);
    esRef.current = es;

    es.onopen = () => {
      setStatus("connected");
      attemptRef.current = 0;
      console.info("[Sandcastle] Event stream connected");
    };

    es.onerror = () => {
      es.close();
      esRef.current = null;
      setStatus("disconnected");

      // Schedule reconnect with backoff
      const delay = getBackoffDelay(attemptRef.current);
      attemptRef.current += 1;
      console.info(`[Sandcastle] Event stream disconnected, reconnecting in ${Math.round(delay)}ms`);

      reconnectTimerRef.current = setTimeout(() => {
        reconnectTimerRef.current = null;
        connect();
      }, delay);
    };

    // Listen for specific event types from the backend
    const eventTypes = ["run.started", "run.completed", "run.failed", "step.completed", "step.failed", "dlq.new"];

    for (const eventType of eventTypes) {
      es.addEventListener(eventType, (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data) as Record<string, unknown>;
          addEvent(eventType, data);
        } catch {
          // Ignore parse errors on malformed messages
        }
      });
    }

    // Also listen for generic "message" events
    es.onmessage = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data) as Record<string, unknown>;
        addEvent("message", data);
      } catch {
        // Ignore
      }
    };
  }, [addEvent]);

  useEffect(() => {
    connect();

    return () => {
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      setStatus("disconnected");
    };
  }, [connect]);

  const clearEvents = useCallback(() => setEvents([]), []);

  return { events, status, clearEvents };
}
