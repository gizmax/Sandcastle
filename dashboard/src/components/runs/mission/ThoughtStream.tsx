import { useEffect, useRef, useState } from "react";
import { Pause, Terminal } from "lucide-react";
import { cn } from "@/lib/utils";
import type { FeedEntry, FeedKind } from "@/lib/missionControl";

const KIND_COLORS: Record<FeedKind, string> = {
  run: "text-accent",
  "step-start": "text-running",
  "step-done": "text-success",
  "step-fail": "text-error",
  output: "text-muted",
  error: "text-error",
};

const KIND_MARKERS: Record<FeedKind, string> = {
  run: "◆",
  "step-start": "▸",
  "step-done": "✓",
  "step-fail": "✗",
  output: "·",
  error: "!",
};

function formatTime(ts: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(ts.getHours())}:${pad(ts.getMinutes())}:${pad(ts.getSeconds())}`;
}

interface ThoughtStreamProps {
  entries: FeedEntry[];
  isLive: boolean;
}

/**
 * Scrolling live feed of step events, monospace, auto-scroll with
 * pause-on-hover so the operator can read without the log running away.
 */
export function ThoughtStream({ entries, isLive }: ThoughtStreamProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [hovered, setHovered] = useState(false);

  // Auto-scroll to the latest entry unless the operator is reading
  useEffect(() => {
    if (hovered) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries.length, hovered]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-2.5">
        <Terminal className="h-3.5 w-3.5 text-accent" />
        <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          Thought stream
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          {hovered && isLive ? (
            <span className="flex items-center gap-1 rounded-full bg-warning/15 px-2 py-0.5 text-[10px] font-medium text-warning">
              <Pause className="h-2.5 w-2.5" />
              paused
            </span>
          ) : isLive ? (
            <span className="flex items-center gap-1.5 text-[10px] font-medium text-success">
              <span className="status-dot-running inline-block h-1.5 w-1.5 rounded-full bg-success" />
              streaming
            </span>
          ) : null}
        </span>
      </div>

      <div
        ref={scrollRef}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        className="min-h-0 flex-1 overflow-y-auto px-4 py-3 font-mono text-xs leading-relaxed"
      >
        {entries.length === 0 ? (
          <p className="text-muted">Waiting for events…</p>
        ) : (
          <ol className="space-y-1.5">
            {entries.map((entry) => (
              <li key={entry.id} className="mission-feed-line">
                <div className="flex items-baseline gap-2">
                  <span className="shrink-0 text-[10px] text-muted-foreground">
                    {formatTime(entry.ts)}
                  </span>
                  <span className={cn("shrink-0", KIND_COLORS[entry.kind])}>
                    {KIND_MARKERS[entry.kind]}
                  </span>
                  <span
                    className={cn(
                      "break-words",
                      entry.kind === "output" ? "text-muted" : "text-foreground"
                    )}
                  >
                    {entry.title}
                  </span>
                </div>
                {entry.detail && (
                  <pre
                    className={cn(
                      "mt-1 ml-[4.5rem] max-h-40 overflow-hidden whitespace-pre-wrap break-words rounded-md border border-border/60 bg-background/60 px-2.5 py-1.5 text-[11px]",
                      entry.kind === "step-fail" || entry.kind === "error"
                        ? "text-error/90"
                        : "text-muted"
                    )}
                  >
                    {entry.detail}
                  </pre>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}
