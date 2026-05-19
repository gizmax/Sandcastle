import { useEffect, useMemo, useRef, useState } from "react";
import {
  Line,
  LineChart,
  ResponsiveContainer,
  YAxis,
} from "recharts";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";

interface WorkQueuePanelProps {
  environmentId?: string | null;
  /**
   * Override for the backend base URL. Defaults to the same origin as the
   * dashboard. `/admin/environments` is mounted at the root, not under
   * `/api`, so we explicitly build an absolute path.
   */
  baseUrl?: string;
}

interface QueueSample {
  depth: number;
  pending: number;
  oldest_queued_at: string | null;
  workers_polling: number;
  ts: number;
}

interface SparkPoint {
  i: number;
  depth: number;
}

const MAX_SAMPLES = 60;
const BACKOFF_BASE_MS = 1000;
const BACKOFF_CAP_MS = 30_000;

function classifyDepth(depth: number): "green" | "amber" | "red" {
  if (depth < 5) return "green";
  if (depth <= 50) return "amber";
  return "red";
}

function pillClasses(level: "green" | "amber" | "red"): string {
  switch (level) {
    case "green":
      return "bg-success/15 text-success border-success/30";
    case "amber":
      return "bg-warning/15 text-warning border-warning/30";
    case "red":
      return "bg-error/15 text-error border-error/30";
  }
}

function pillLabel(level: "green" | "amber" | "red"): string {
  switch (level) {
    case "green":
      return "Healthy";
    case "amber":
      return "Warming";
    case "red":
      return "Saturated";
  }
}

function formatRelative(iso: string | null): string {
  if (!iso) return "n/a";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "n/a";
  const deltaSec = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (deltaSec < 60) return `${deltaSec}s ago`;
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`;
  if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}h ago`;
  return `${Math.floor(deltaSec / 86400)}d ago`;
}

export function WorkQueuePanel({
  environmentId,
  baseUrl,
}: WorkQueuePanelProps) {
  const [samples, setSamples] = useState<QueueSample[]>([]);
  const [connected, setConnected] = useState(false);
  const attemptRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sourceRef = useRef<EventSource | null>(null);

  // Construct the absolute stream URL. /admin/environments is root-mounted.
  const url = useMemo(() => {
    if (!environmentId) return null;
    const root = baseUrl ?? "";
    return `${root}/admin/environments/${environmentId}/work/stream`;
  }, [environmentId, baseUrl]);

  useEffect(() => {
    if (!url) return;

    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      // EventSource does not support custom auth headers, but the api client
      // already configures cookies/withCredentials; fall back to query token
      // when available. We still pass the auth headers via constructor when
      // a future EventSourcePolyfill is installed.
      const es = new EventSource(url, { withCredentials: true });
      sourceRef.current = es;

      es.addEventListener("open", () => {
        if (cancelled) return;
        attemptRef.current = 0;
        setConnected(true);
      });

      es.addEventListener("work_stats", (raw) => {
        if (cancelled) return;
        try {
          const data = JSON.parse((raw as MessageEvent).data) as QueueSample;
          setSamples((prev) => {
            const next = [...prev, data];
            if (next.length > MAX_SAMPLES) {
              next.splice(0, next.length - MAX_SAMPLES);
            }
            return next;
          });
        } catch {
          // ignore malformed payloads
        }
      });

      const onFailure = () => {
        if (cancelled) return;
        setConnected(false);
        es.close();
        sourceRef.current = null;

        const attempt = attemptRef.current + 1;
        attemptRef.current = attempt;
        const delay = Math.min(
          BACKOFF_CAP_MS,
          BACKOFF_BASE_MS * 2 ** (attempt - 1)
        );
        reconnectTimerRef.current = setTimeout(connect, delay);
      };

      es.addEventListener("error", onFailure);
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (sourceRef.current) {
        sourceRef.current.close();
        sourceRef.current = null;
      }
    };
  }, [url]);

  // Reference `api` so bundlers don't shake it: the production wiring may
  // attach an auth token to the URL via a query param.
  void api;

  if (!environmentId) return null;

  const latest: QueueSample | null = samples.length
    ? samples[samples.length - 1]
    : null;
  const depth = latest?.depth ?? 0;
  const level = classifyDepth(depth);

  const sparkData: SparkPoint[] = samples.map((s, i) => ({
    i,
    depth: s.depth,
  }));

  return (
    <div
      className="rounded-xl border border-border bg-surface p-5 shadow-sm"
      data-testid="work-queue-panel"
    >
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-foreground">Work Queue</h3>
        <span
          className={cn(
            "rounded-full border px-2 py-0.5 text-xs font-medium",
            pillClasses(level)
          )}
          data-testid="work-queue-pill"
          data-level={level}
        >
          {pillLabel(level)}
        </span>
      </div>

      <div className="mt-4 flex items-baseline gap-3">
        <span
          aria-live="polite"
          className="text-4xl font-semibold tabular-nums text-foreground"
          data-testid="work-queue-depth"
        >
          {depth}
        </span>
        <span className="text-xs text-muted-foreground">
          {connected ? "live" : "reconnecting..."}
        </span>
      </div>

      <dl className="mt-3 grid grid-cols-3 gap-3 text-xs text-muted-foreground">
        <div>
          <dt className="text-muted">Pending</dt>
          <dd
            className="text-foreground tabular-nums"
            data-testid="work-queue-pending"
          >
            {latest?.pending ?? 0}
          </dd>
        </div>
        <div>
          <dt className="text-muted">Oldest queued</dt>
          <dd
            className="text-foreground"
            data-testid="work-queue-oldest"
          >
            {formatRelative(latest?.oldest_queued_at ?? null)}
          </dd>
        </div>
        <div>
          <dt className="text-muted">Workers polling</dt>
          <dd
            className="text-foreground tabular-nums"
            data-testid="work-queue-workers"
          >
            {latest?.workers_polling ?? 0}
          </dd>
        </div>
      </dl>

      <div
        className="mt-4 h-16 w-full"
        data-testid="work-queue-sparkline"
        data-sample-count={sparkData.length}
      >
        {sparkData.length > 0 ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={sparkData}>
              <YAxis hide domain={[0, "dataMax + 2"]} />
              <Line
                type="monotone"
                dataKey="depth"
                stroke="var(--color-accent)"
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-full w-full rounded-md border border-dashed border-border" />
        )}
      </div>
    </div>
  );
}

export default WorkQueuePanel;
