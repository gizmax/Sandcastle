import { useState } from "react";
import { Brain, Wrench, MessageCircle, AlertOctagon, ChevronRight, Activity } from "lucide-react";
import { CopyButton } from "@/components/shared/CopyButton";
import { cn } from "@/lib/utils";
import type { AgentEvent } from "@/types/agentEvents";

interface AgentEventCardProps {
  event: AgentEvent;
}

function formatTs(ts: number): string {
  try {
    return new Date(ts).toLocaleTimeString();
  } catch {
    return "";
  }
}

export function AgentEventCard({ event }: AgentEventCardProps) {
  const [thinkingOpen, setThinkingOpen] = useState(false);

  switch (event.type) {
    case "agent.message":
      return (
        <div
          data-testid="agent-event-message"
          className="rounded-lg border border-border bg-surface px-3 py-2 shadow-sm"
        >
          <div className="flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted-foreground">
            <MessageCircle className="h-3 w-3" />
            <span>Message</span>
            <span className="ml-auto font-mono text-muted">{formatTs(event.ts)}</span>
          </div>
          <p className="mt-1 whitespace-pre-wrap text-sm text-foreground">{event.text}</p>
        </div>
      );

    case "agent.thinking":
      return (
        <div
          data-testid="agent-event-thinking"
          className="rounded-lg border border-border bg-background/50 shadow-sm"
        >
          <button
            type="button"
            onClick={() => setThinkingOpen((o) => !o)}
            aria-expanded={thinkingOpen}
            className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-medium text-muted hover:text-foreground transition-colors"
          >
            <ChevronRight
              className={cn(
                "h-3.5 w-3.5 transition-transform duration-200",
                thinkingOpen && "rotate-90"
              )}
            />
            <Brain className="h-3.5 w-3.5 text-accent" />
            <span>Thinking</span>
            <span className="ml-auto font-mono text-[10px] text-muted-foreground">
              {formatTs(event.ts)}
            </span>
          </button>
          {thinkingOpen && (
            <p className="border-t border-border px-3 py-2 text-xs italic text-muted whitespace-pre-wrap">
              {event.text}
            </p>
          )}
        </div>
      );

    case "agent.tool_use": {
      const args = JSON.stringify(event.input);
      const formatted = `${event.toolName}(${args})`;
      return (
        <div
          data-testid="agent-event-tool-use"
          className="rounded-lg border border-accent/30 bg-accent/5 px-3 py-2 shadow-sm"
        >
          <div className="flex items-center gap-2 text-[10px] uppercase tracking-wider text-accent">
            <Wrench className="h-3 w-3" />
            <span>Tool use</span>
            <span className="ml-auto font-mono text-muted">{formatTs(event.ts)}</span>
            <CopyButton value={formatted} label="tool call" />
          </div>
          <pre className="mt-1 overflow-x-auto rounded bg-background px-2 py-1 font-mono text-xs text-foreground">
            <span className="text-accent">{event.toolName}</span>
            <span className="text-muted">(</span>
            <span>{args}</span>
            <span className="text-muted">)</span>
          </pre>
        </div>
      );
    }

    case "agent.tool_result":
      return (
        <div
          data-testid="agent-event-tool-result"
          data-error={event.isError ? "true" : "false"}
          className={cn(
            "rounded-lg border px-3 py-2 shadow-sm",
            event.isError
              ? "border-error/40 bg-error/10"
              : "border-success/40 bg-success/10"
          )}
        >
          <div
            className={cn(
              "flex items-center gap-2 text-[10px] uppercase tracking-wider",
              event.isError ? "text-error" : "text-success"
            )}
          >
            {event.isError ? (
              <AlertOctagon className="h-3 w-3" />
            ) : (
              <Wrench className="h-3 w-3" />
            )}
            <span>{event.isError ? "Tool error" : "Tool result"}</span>
            <span className="ml-auto font-mono text-muted">{formatTs(event.ts)}</span>
          </div>
          <pre
            className={cn(
              "mt-1 max-h-48 overflow-auto rounded bg-background px-2 py-1 font-mono text-xs whitespace-pre-wrap",
              event.isError ? "text-error" : "text-foreground"
            )}
          >
            {event.output}
          </pre>
        </div>
      );

    case "agent.thread_message_received":
      return (
        <div
          data-testid="agent-event-thread-message"
          className="flex items-start gap-2 rounded-lg border border-border bg-surface px-3 py-2 shadow-sm"
        >
          <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-accent/15 px-2 py-0.5 text-[10px] font-medium text-accent">
            <Activity className="h-3 w-3" />
            from {event.fromAgentId}
          </span>
          <p className="text-xs text-muted whitespace-pre-wrap">{event.preview}</p>
          <span className="ml-auto font-mono text-[10px] text-muted">{formatTs(event.ts)}</span>
        </div>
      );

    case "session.status_running":
    case "session.status_idle":
      return (
        <div
          data-testid="agent-event-session-status"
          className="flex items-center gap-2 text-[11px] text-muted"
        >
          <span className="rounded-full border border-border bg-background px-2 py-0.5 font-medium">
            {event.type === "session.status_running" ? "Session running" : "Session idle"}
            {event.stopReason ? ` (${event.stopReason})` : ""}
          </span>
          <span className="font-mono text-[10px]">{formatTs(event.ts)}</span>
        </div>
      );

    case "session.error":
      return (
        <div
          data-testid="agent-event-session-error"
          className="rounded-lg border border-error/40 bg-error/10 px-3 py-2 text-xs text-error"
        >
          <div className="flex items-center gap-2 text-[10px] uppercase tracking-wider">
            <AlertOctagon className="h-3 w-3" />
            <span>Session error</span>
            <span className="ml-auto font-mono text-muted">{formatTs(event.ts)}</span>
          </div>
          <p className="mt-1 font-mono">{event.error}</p>
        </div>
      );
  }
}
