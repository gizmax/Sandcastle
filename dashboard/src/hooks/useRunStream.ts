import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/api/client";
import { API_BASE_URL } from "@/lib/constants";
import type { MissionEvent } from "@/lib/missionControl";

export type RunStreamStatus = "idle" | "connecting" | "connected" | "reconnecting" | "done" | "offline";

// Reconnect with exponential backoff - starts at 1s, maxes at 15s
const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 15000;
const MAX_ATTEMPTS = 8;

function getBackoffDelay(attempt: number): number {
  const delay = Math.min(BASE_DELAY_MS * Math.pow(2, attempt), MAX_DELAY_MS);
  return delay + Math.random() * 400;
}

/**
 * Per-run SSE stream (GET /runs/{id}/stream) with automatic reconnection.
 *
 * Unlike useSSE this hook reconnects with backoff on connection loss and
 * stops cleanly once the terminal `result` event arrives. Uses fetch with
 * auth headers instead of native EventSource so the API key never appears
 * in URL query parameters.
 */
export function useRunStream(runId: string | null, enabled: boolean) {
  const [events, setEvents] = useState<MissionEvent[]>([]);
  const [status, setStatus] = useState<RunStreamStatus>("idle");
  const abortRef = useRef<AbortController | null>(null);
  const attemptRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unmountedRef = useRef(false);
  const doneRef = useRef(false);
  // Read via ref inside connect so the callback identity stays stable.
  const runIdRef = useRef(runId);
  // Latest connect function for reconnection timers (avoids a self-reference
  // inside the useCallback body, which react-hooks forbids).
  const connectRef = useRef<() => void>(() => {});

  // Same idiom as useEventStream: the reconnect scheduler receives the
  // connect function instead of closing over it directly.
  const scheduleReconnect = useCallback((connectFn: () => void) => {
    if (unmountedRef.current || doneRef.current) return;
    if (attemptRef.current >= MAX_ATTEMPTS) {
      setStatus("offline");
      return;
    }
    const delay = getBackoffDelay(attemptRef.current);
    attemptRef.current += 1;
    setStatus("reconnecting");
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      connectFn();
    }, delay);
  }, []);

  const connect = useCallback(() => {
    const currentRunId = runIdRef.current;
    if (!currentRunId || unmountedRef.current || doneRef.current) return;
    // In mock/demo mode there is no backend to stream from
    if (api.isMockMode) {
      setStatus("offline");
      return;
    }

    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }

    setStatus(attemptRef.current > 0 ? "reconnecting" : "connecting");

    const controller = new AbortController();
    abortRef.current = controller;
    const url = `${API_BASE_URL}/runs/${currentRunId}/stream`;

    (async () => {
      try {
        const res = await fetch(url, {
          headers: api.authHeaders(),
          signal: controller.signal,
        });

        if (!res.ok || !res.body) {
          scheduleReconnect(() => connectRef.current());
          return;
        }

        setStatus("connected");
        attemptRef.current = 0;

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let currentEvent = "message";
        let currentData = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          for (const line of lines) {
            if (line.startsWith("event: ")) {
              currentEvent = line.slice(7).trim();
            } else if (line.startsWith("data: ")) {
              currentData += (currentData ? "\n" : "") + line.slice(6);
            } else if (line.trim() === "" && currentData) {
              try {
                const data = JSON.parse(currentData) as Record<string, unknown>;
                const eventType = currentEvent;
                if (eventType === "result") doneRef.current = true;
                setEvents((prev) => {
                  const updated = [...prev, { event: eventType, data, timestamp: new Date() }];
                  if (updated.length > 1000) return updated.slice(-1000);
                  return updated;
                });
              } catch {
                // Ignore parse errors on malformed messages
              }
              currentEvent = "message";
              currentData = "";
            }
          }
        }

        // Stream ended. Terminal `result` => done; otherwise reconnect.
        if (unmountedRef.current) return;
        if (doneRef.current) {
          setStatus("done");
        } else {
          scheduleReconnect(() => connectRef.current());
        }
      } catch (e: unknown) {
        if (e instanceof DOMException && e.name === "AbortError") return;
        scheduleReconnect(() => connectRef.current());
      }
    })();
  }, [scheduleReconnect]);

  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  // Manual retry after the hook gave up (status === "offline")
  const retry = useCallback(() => {
    attemptRef.current = 0;
    connect();
  }, [connect]);

  useEffect(() => {
    unmountedRef.current = false;
    doneRef.current = false;
    attemptRef.current = 0;
    runIdRef.current = runId;
    setEvents([]);

    if (enabled && runId) {
      connect();
    } else {
      setStatus("idle");
    }

    return () => {
      unmountedRef.current = true;
      if (abortRef.current) {
        abortRef.current.abort();
        abortRef.current = null;
      }
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [runId, enabled, connect]);

  return { events, status, retry };
}
