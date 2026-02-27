import { useEffect, useRef } from "react";
import { useSSE } from "@/hooks/useSSE";
import { cn } from "@/lib/utils";

interface LiveStreamProps {
  runId: string;
}

export function LiveStream({ runId }: LiveStreamProps) {
  const { events, connected } = useSSE(`/runs/${runId}/stream`);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [events]);

  return (
    <div className="rounded-xl border border-border bg-background shadow-sm overflow-hidden">
      <div className="flex items-center gap-2 border-b border-border px-4 py-2">
        <div
          className={cn(
            "h-2 w-2 rounded-full",
            connected ? "bg-success animate-pulse" : "bg-error"
          )}
        />
        <span className="text-xs text-muted-foreground">
          {connected ? "streaming..." : "disconnected"}
        </span>
      </div>
      <div
        ref={containerRef}
        className="max-h-96 overflow-y-auto p-4 font-mono text-xs leading-relaxed"
      >
        {events.length === 0 ? (
          <p className="text-muted">Waiting for events...</p>
        ) : (
          events.map((event, i) => (
            <div key={`${event.event}-${event.timestamp.getTime()}-${i}`} className="mb-1">
              <span className="text-accent">[{event.event}]</span>{" "}
              <span className="text-foreground/80">
                {JSON.stringify(event.data)}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
