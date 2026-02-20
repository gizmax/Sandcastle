import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/api/client";

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

    const url = api.sseUrl(path);

    (async () => {
      try {
        const res = await fetch(url, {
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

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          let currentEvent = "message";
          for (const line of lines) {
            if (line.startsWith("event: ")) {
              currentEvent = line.slice(7).trim();
            } else if (line.startsWith("data: ")) {
              const raw = line.slice(6);
              try {
                const data = JSON.parse(raw);
                const eventType = currentEvent;
                setEvents((prev) => [...prev, { event: eventType, data, timestamp: new Date() }]);
              } catch {
                // Ignore parse errors
              }
              currentEvent = "message";
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
