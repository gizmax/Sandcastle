import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/api/client";
import { API_BASE_URL } from "@/lib/constants";

interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
  timestamp: Date;
}

export function useSSE(path: string | null) {
  const [events, setEvents] = useState<SSEEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const disconnect = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
      setConnected(false);
    }
  }, []);

  useEffect(() => {
    if (!path) return;

    const controller = new AbortController();
    abortRef.current = controller;

    // Use fetch with auth headers instead of URL token parameter
    // to avoid leaking the API key in access logs and browser history.
    const url = `${API_BASE_URL}${path}`;

    (async () => {
      try {
        const res = await fetch(url, {
          headers: api.authHeaders(),
          signal: controller.signal,
        });
        if (!res.ok || !res.body) {
          setConnected(false);
          return;
        }

        setConnected(true);
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        // Persist event/data state across chunk boundaries so an event:
        // line in one chunk is not lost when data: arrives in the next.
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
              // Blank line = dispatch accumulated event per SSE spec
              try {
                const data = JSON.parse(currentData);
                const eventType = currentEvent;
                setEvents((prev) => {
                  const updated = [...prev, { event: eventType, data, timestamp: new Date() }];
                  if (updated.length > 500) return updated.slice(-500);
                  return updated;
                });
              } catch {
                // Ignore parse errors
              }
              // Reset only after a complete event is dispatched
              currentEvent = "message";
              currentData = "";
            }
          }
        }
      } catch (e: unknown) {
        if (e instanceof DOMException && e.name === "AbortError") return;
        setConnected(false);
      }
    })();

    return () => {
      controller.abort();
      abortRef.current = null;
      setConnected(false);
    };
  }, [path]);

  const clear = useCallback(() => setEvents([]), []);

  return { events, connected, disconnect, clear };
}
