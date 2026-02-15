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
  const sourceRef = useRef<EventSource | null>(null);

  const disconnect = useCallback(() => {
    if (sourceRef.current) {
      sourceRef.current.close();
      sourceRef.current = null;
      setConnected(false);
    }
  }, []);

  useEffect(() => {
    if (!path) return;

    const url = api.sseUrl(path);
    const source = new EventSource(url);
    sourceRef.current = source;

    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);

    const handleEvent = (type: string) => (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        setEvents((prev) => [...prev, { event: type, data, timestamp: new Date() }]);
      } catch {
        // Ignore parse errors
      }
    };

    source.addEventListener("status", handleEvent("status"));
    source.addEventListener("step", handleEvent("step"));
    source.addEventListener("result", handleEvent("result"));
    source.addEventListener("error", handleEvent("error"));

    return () => {
      source.close();
      sourceRef.current = null;
      setConnected(false);
    };
  }, [path]);

  const clear = useCallback(() => setEvents([]), []);

  return { events, connected, disconnect, clear };
}
