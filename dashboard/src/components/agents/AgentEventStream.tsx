import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, Brain, ChevronRight, AlertOctagon } from "lucide-react";
import { useAgentEvents } from "@/hooks/useAgentEvents";
import { AgentEventCard } from "@/components/agents/AgentEventCard";
import { cn } from "@/lib/utils";
import type { AgentEvent } from "@/types/agentEvents";

interface AgentEventStreamProps {
  runId: string;
}

interface ThreadGroup {
  threadId: string | null;
  events: AgentEvent[];
}

const NO_THREAD_KEY = "__main__";

function groupByThread(events: AgentEvent[]): ThreadGroup[] {
  const order: string[] = [];
  const buckets = new Map<string, ThreadGroup>();
  for (const ev of events) {
    let key = NO_THREAD_KEY;
    let threadId: string | null = null;
    if ("threadId" in ev && ev.threadId) {
      key = ev.threadId;
      threadId = ev.threadId;
    }
    let bucket = buckets.get(key);
    if (!bucket) {
      bucket = { threadId, events: [] };
      buckets.set(key, bucket);
      order.push(key);
    }
    bucket.events.push(ev);
  }
  return order.map((k) => buckets.get(k) as ThreadGroup);
}

function deriveStatusLabel(
  events: AgentEvent[],
  status: ReturnType<typeof useAgentEvents>["status"]
): { label: string; tone: "running" | "idle" | "error" | "unavailable" } {
  if (status === "error") return { label: "Stream error", tone: "error" };
  if (status === "unavailable") return { label: "Stream unavailable", tone: "unavailable" };
  // Find most recent session status event to enrich the label.
  let stopReason: string | undefined;
  let lastSessionType: "session.status_running" | "session.status_idle" | null = null;
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i];
    if (ev.type === "session.status_running" || ev.type === "session.status_idle") {
      lastSessionType = ev.type;
      stopReason = ev.type === "session.status_idle" ? ev.stopReason : undefined;
      break;
    }
  }
  if (status === "running" || lastSessionType === "session.status_running") {
    return { label: "Session running", tone: "running" };
  }
  const suffix = stopReason ? ` (${stopReason})` : "";
  return { label: `Session idle${suffix}`, tone: "idle" };
}

export function AgentEventStream({ runId }: AgentEventStreamProps) {
  const { events, status, error } = useAgentEvents(runId);
  const listRef = useRef<HTMLDivElement | null>(null);
  const [collapsedThreads, setCollapsedThreads] = useState<Record<string, boolean>>({});

  const groups = useMemo(() => groupByThread(events), [events]);
  const multiThread = groups.filter((g) => g.threadId !== null).length > 1;
  const statusInfo = useMemo(() => deriveStatusLabel(events, status), [events, status]);

  useEffect(() => {
    if (!listRef.current) return;
    listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [events.length]);

  if (status === "unavailable") {
    return (
      <div className="rounded-xl border border-border bg-surface p-5 text-sm text-muted shadow-sm">
        <div className="mb-1 flex items-center gap-2 font-semibold text-foreground">
          <Brain className="h-4 w-4 text-accent" />
          Agent Reasoning
        </div>
        <p>Live agent stream is not available for this run.</p>
      </div>
    );
  }

  return (
    <section
      data-testid="agent-event-stream"
      className="overflow-hidden rounded-xl border border-border bg-surface shadow-sm"
    >
      <header
        className={cn(
          "sticky top-0 z-10 flex items-center gap-2 border-b border-border bg-surface px-4 py-2.5"
        )}
      >
        <Brain className="h-4 w-4 text-accent" />
        <h3 className="text-sm font-semibold text-foreground">Agent Reasoning</h3>
        <span
          data-testid="agent-stream-status"
          data-tone={statusInfo.tone}
          className={cn(
            "ml-auto inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium",
            statusInfo.tone === "running" &&
              "bg-accent/15 text-accent border border-accent/30",
            statusInfo.tone === "idle" &&
              "bg-background text-muted border border-border",
            statusInfo.tone === "error" &&
              "bg-error/15 text-error border border-error/30",
            statusInfo.tone === "unavailable" &&
              "bg-background text-muted border border-border"
          )}
        >
          <Activity className="h-3 w-3" />
          {statusInfo.label}
        </span>
      </header>

      {status === "error" && error && (
        <div
          data-testid="agent-stream-error-banner"
          className="flex items-start gap-2 border-b border-error/30 bg-error/10 px-4 py-2 text-xs text-error"
        >
          <AlertOctagon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span className="font-mono">{error}</span>
        </div>
      )}

      <div
        ref={listRef}
        data-testid="agent-stream-list"
        className="max-h-[28rem] overflow-y-auto px-4 py-3"
      >
        {events.length === 0 ? (
          <div
            data-testid="agent-stream-empty"
            className="py-10 text-center text-xs text-muted"
          >
            No agent events yet. Reasoning, tool calls, and thread messages
            will appear here as the session progresses.
          </div>
        ) : multiThread ? (
          <div className="space-y-4">
            {groups.map((group, idx) => {
              const key = group.threadId ?? NO_THREAD_KEY;
              const collapsed = !!collapsedThreads[key];
              const label = group.threadId
                ? `Thread ${group.threadId}`
                : "Main session";
              return (
                <div
                  key={`${key}-${idx}`}
                  data-testid="agent-stream-thread"
                  data-thread-id={group.threadId ?? ""}
                  className="rounded-lg border border-border bg-background/40"
                >
                  <button
                    type="button"
                    onClick={() =>
                      setCollapsedThreads((prev) => ({ ...prev, [key]: !prev[key] }))
                    }
                    aria-expanded={!collapsed}
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-semibold text-muted hover:text-foreground transition-colors"
                  >
                    <ChevronRight
                      className={cn(
                        "h-3.5 w-3.5 transition-transform",
                        !collapsed && "rotate-90"
                      )}
                    />
                    <span>{label}</span>
                    <span className="ml-auto rounded-full bg-surface px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground">
                      {group.events.length}
                    </span>
                  </button>
                  {!collapsed && (
                    <div className="space-y-2 border-t border-border px-3 py-2">
                      {group.events.map((ev, i) => (
                        <AgentEventCard key={`${key}-${i}`} event={ev} />
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="space-y-2">
            {events.map((ev, i) => (
              <AgentEventCard key={i} event={ev} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
