import { useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  RotateCcw,
  CheckCircle,
  Layers,
  Clock,
  Hash,
} from "lucide-react";
import { formatRelativeTime, cn } from "@/lib/utils";
import type { DLQItem } from "./DeadLetterTable";

/* ------------------------------------------------------------------ */
/* Error normalization                                                 */
/* ------------------------------------------------------------------ */

const UUID_RE =
  /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;
const TIMESTAMP_RE =
  /\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?/g;
const BIG_NUMBER_RE = /\b\d{4,}\b/g;
const URL_RE = /https?:\/\/[^\s"')]+/g;

function normalizeError(raw: string): string {
  return raw
    .replace(UUID_RE, "<ID>")
    .replace(TIMESTAMP_RE, "<TS>")
    .replace(URL_RE, "<URL>")
    .replace(BIG_NUMBER_RE, "<N>")
    .slice(0, 80);
}

/* ------------------------------------------------------------------ */
/* Types                                                               */
/* ------------------------------------------------------------------ */

export interface ErrorCluster {
  pattern: string;
  items: DLQItem[];
  uniqueSteps: Set<string>;
  uniqueRuns: Set<string>;
  oldest: string | null;
}

/* ------------------------------------------------------------------ */
/* Build clusters                                                      */
/* ------------------------------------------------------------------ */

export function buildClusters(items: DLQItem[]): ErrorCluster[] {
  const map = new Map<string, ErrorCluster>();

  for (const item of items) {
    const raw = item.error ?? "(no error message)";
    const key = normalizeError(raw);

    let cluster = map.get(key);
    if (!cluster) {
      cluster = {
        pattern: key,
        items: [],
        uniqueSteps: new Set(),
        uniqueRuns: new Set(),
        oldest: null,
      };
      map.set(key, cluster);
    }

    cluster.items.push(item);
    cluster.uniqueSteps.add(item.step_id);
    cluster.uniqueRuns.add(item.run_id);

    if (item.created_at) {
      if (!cluster.oldest || item.created_at < cluster.oldest) {
        cluster.oldest = item.created_at;
      }
    }
  }

  return Array.from(map.values()).sort(
    (a, b) => b.items.length - a.items.length,
  );
}

/* ------------------------------------------------------------------ */
/* Summary stats                                                       */
/* ------------------------------------------------------------------ */

interface SummaryStatsProps {
  items: DLQItem[];
  clusters: ErrorCluster[];
}

function SummaryStats({ items, clusters }: SummaryStatsProps) {
  const oldest = useMemo(() => {
    let o: string | null = null;
    for (const item of items) {
      if (item.created_at && !item.resolved_at) {
        if (!o || item.created_at < o) o = item.created_at;
      }
    }
    return o;
  }, [items]);

  const stats = [
    {
      label: "Total items",
      value: items.length,
      icon: Hash,
    },
    {
      label: "Unique patterns",
      value: clusters.length,
      icon: Layers,
    },
    {
      label: "Most common",
      value:
        clusters.length > 0
          ? `${clusters[0].items.length}x`
          : "-",
      sub: clusters.length > 0 ? clusters[0].pattern : undefined,
      icon: AlertTriangle,
    },
    {
      label: "Oldest unresolved",
      value: oldest ? formatRelativeTime(oldest) : "-",
      icon: Clock,
    },
  ];

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      {stats.map((s) => (
        <div
          key={s.label}
          className="rounded-xl border border-border bg-surface p-3 sm:p-4"
        >
          <div className="flex items-center gap-2 text-muted mb-1">
            <s.icon className="h-3.5 w-3.5" />
            <span className="text-xs font-medium">{s.label}</span>
          </div>
          <p className="text-lg font-semibold text-foreground">{s.value}</p>
          {s.sub && (
            <p className="mt-0.5 truncate text-xs text-muted font-mono">
              {s.sub}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Single cluster card                                                 */
/* ------------------------------------------------------------------ */

interface ClusterCardProps {
  cluster: ErrorCluster;
  onRetry: (id: string) => void;
  onResolve: (id: string) => void;
  onBatchRetry: (ids: string[]) => void;
  onBatchResolve: (ids: string[]) => void;
}

function severityColor(count: number): string {
  if (count >= 5) return "border-l-error";
  if (count >= 2) return "border-l-accent";
  return "border-l-muted";
}

function ClusterCard({
  cluster,
  onRetry,
  onResolve,
  onBatchRetry,
  onBatchResolve,
}: ClusterCardProps) {
  const [expanded, setExpanded] = useState(false);
  const navigate = useNavigate();
  const count = cluster.items.length;
  const unresolvedIds = cluster.items
    .filter((i) => !i.resolved_at)
    .map((i) => i.id);

  return (
    <div
      className={cn(
        "rounded-xl border border-border bg-surface overflow-hidden",
        "border-l-4",
        severityColor(count),
      )}
    >
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-start gap-3 p-3 sm:p-4 text-left hover:bg-background/50 transition-colors"
      >
        <div className="mt-0.5 shrink-0 text-muted">
          {expanded ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
        </div>
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-error" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate font-mono text-xs text-foreground">
              {cluster.pattern}
            </span>
            <span className="inline-flex items-center rounded-full bg-error/15 text-error text-xs px-2 py-0.5 font-medium">
              {count} occurrence{count > 1 ? "s" : ""}
            </span>
          </div>
          <p className="mt-1 text-xs text-muted">
            Across {cluster.uniqueRuns.size} workflow run
            {cluster.uniqueRuns.size > 1 ? "s" : ""},{" "}
            {cluster.uniqueSteps.size} step
            {cluster.uniqueSteps.size > 1 ? "s" : ""}
          </p>
        </div>
      </button>

      {/* Expanded body */}
      {expanded && (
        <div className="border-t border-border">
          {/* Batch actions */}
          {unresolvedIds.length > 0 && (
            <div className="flex items-center gap-2 px-4 py-2 border-b border-border bg-background/30">
              <button
                onClick={() => onBatchRetry(unresolvedIds)}
                className={cn(
                  "flex items-center gap-1 rounded-md border border-border px-2.5 py-1 text-xs font-medium",
                  "text-running hover:bg-running/10 transition-colors",
                )}
              >
                <RotateCcw className="h-3 w-3" />
                Retry All ({unresolvedIds.length})
              </button>
              <button
                onClick={() => onBatchResolve(unresolvedIds)}
                className={cn(
                  "flex items-center gap-1 rounded-md border border-border px-2.5 py-1 text-xs font-medium",
                  "text-success hover:bg-success/10 transition-colors",
                )}
              >
                <CheckCircle className="h-3 w-3" />
                Resolve All ({unresolvedIds.length})
              </button>
            </div>
          )}

          {/* Individual items */}
          <div className="divide-y divide-border">
            {cluster.items.map((item) => (
              <div
                key={item.id}
                className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5 text-xs"
              >
                <button
                  onClick={() => navigate(`/runs/${item.run_id}`)}
                  className="font-medium text-accent hover:text-accent-hover transition-colors"
                >
                  {item.step_id}
                </button>
                <span className="text-muted">
                  {item.created_at
                    ? formatRelativeTime(item.created_at)
                    : "-"}
                </span>
                <span className="text-muted">
                  {item.attempts} attempt{item.attempts !== 1 ? "s" : ""}
                </span>
                {item.resolved_at ? (
                  <span className="text-success font-medium">Resolved</span>
                ) : (
                  <div className="ml-auto flex items-center gap-2">
                    <button
                      onClick={() => onRetry(item.id)}
                      className="flex items-center gap-1 text-running hover:text-running/80 transition-colors"
                    >
                      <RotateCcw className="h-3 w-3" />
                      Retry
                    </button>
                    <button
                      onClick={() => onResolve(item.id)}
                      className="flex items-center gap-1 text-success hover:text-success/80 transition-colors"
                    >
                      <CheckCircle className="h-3 w-3" />
                      Resolve
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Main export                                                         */
/* ------------------------------------------------------------------ */

interface ErrorClustersProps {
  items: DLQItem[];
  onRetry: (id: string) => void;
  onResolve: (id: string) => void;
  onBatchRetry: (ids: string[]) => void;
  onBatchResolve: (ids: string[]) => void;
}

export function ErrorClusters({
  items,
  onRetry,
  onResolve,
  onBatchRetry,
  onBatchResolve,
}: ErrorClustersProps) {
  const clusters = useMemo(() => buildClusters(items), [items]);

  return (
    <div className="space-y-4 sm:space-y-6">
      <SummaryStats items={items} clusters={clusters} />

      <div className="stagger-children space-y-3">
        {clusters.map((cluster) => (
          <ClusterCard
            key={cluster.pattern}
            cluster={cluster}
            onRetry={onRetry}
            onResolve={onResolve}
            onBatchRetry={onBatchRetry}
            onBatchResolve={onBatchResolve}
          />
        ))}
      </div>
    </div>
  );
}
