import { useEffect, useRef, useState } from "react";
import { api } from "@/api/client";
import { API_BASE_URL } from "@/lib/constants";
import type { AgentEvent, AgentStreamStatus } from "@/types/agentEvents";

interface UseAgentEventsResult {
  events: AgentEvent[];
  status: AgentStreamStatus;
  error: string | null;
}

/**
 * Subscribes to the existing /api/runs/{runId}/stream endpoint. That stream
 * emits run and step progress, not Anthropic agent reasoning events, so run
 * detail views do not mount AgentEventStream until such events are available.
 *
 * Uses fetch + ReadableStream so that auth headers can be passed (EventSource
 * does not support custom headers). AbortController is used for cleanup.
 */
export function useAgentEvents(runId: string | null | undefined): UseAgentEventsResult {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [status, setStatus] = useState<AgentStreamStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!runId) {
      setStatus("idle");
      setEvents([]);
      setError(null);
      return;
    }

    setEvents([]);
    setError(null);
    setStatus("connecting");

    const controller = new AbortController();
    abortRef.current = controller;

    const url = `${API_BASE_URL}/runs/${runId}/stream`;

    (async () => {
      try {
        const res = await fetch(url, {
          headers: api.authHeaders(),
          signal: controller.signal,
        });

        if (res.status === 404) {
          setStatus("unavailable");
          return;
        }

        if (!res.ok || !res.body) {
          setStatus("error");
          setError(`Stream failed with status ${res.status}`);
          return;
        }

        setStatus("running");
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let currentData = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          for (const line of lines) {
            if (line.startsWith("data: ")) {
              currentData += (currentData ? "\n" : "") + line.slice(6);
            } else if (line.trim() === "" && currentData) {
              try {
                const parsed = JSON.parse(currentData) as AgentEvent;
                setEvents((prev) => {
                  const next = [...prev, parsed];
                  if (next.length > 500) return next.slice(-500);
                  return next;
                });
                if (parsed.type === "session.status_running") {
                  setStatus("running");
                } else if (parsed.type === "session.status_idle") {
                  setStatus("idle");
                } else if (parsed.type === "session.error") {
                  setStatus("error");
                  setError(parsed.error);
                }
              } catch {
                // ignore malformed payloads
              }
              currentData = "";
            }
          }
        }
        // Stream ended cleanly - mirror the session status (idle if none seen).
        setStatus((prev) => (prev === "running" ? "idle" : prev));
      } catch (e: unknown) {
        if (e instanceof DOMException && e.name === "AbortError") return;
        setStatus("error");
        setError(e instanceof Error ? e.message : "Stream connection failed");
      }
    })();

    return () => {
      controller.abort();
      abortRef.current = null;
    };
  }, [runId]);

  return { events, status, error };
}
