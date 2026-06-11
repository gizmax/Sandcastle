import { useCallback, useEffect, useMemo, useState } from "react";
import { Brain, Search, Trash2, Tag, Clock, Workflow, TrendingDown } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { cn, buttonMd, buttonPrimary, buttonSecondary, iconMd } from "@/lib/utils";

// Memory entry matching backend MemoryEntry schema
interface MemoryEntry {
  id: string;
  memory: string;
  metadata: {
    source_workflow?: string;
    importance?: number;
    decay?: number;
    tags?: string[];
    [key: string]: unknown;
  } | null;
  created_at: string | null;
  updated_at: string | null;
}

interface MemoryListResponse {
  memories: MemoryEntry[];
  total: number;
}

// Format relative time for display
function timeAgo(iso: string | null): string {
  if (!iso) return "Unknown";
  const diff = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

// Importance score color and label
function importanceLabel(score: number): { color: string; bg: string; label: string } {
  if (score >= 0.9) return { color: "text-success", bg: "bg-success/10", label: "Critical" };
  if (score >= 0.7) return { color: "text-accent", bg: "bg-accent/10", label: "High" };
  if (score >= 0.4) return { color: "text-warning", bg: "bg-warning/10", label: "Medium" };
  return { color: "text-muted-foreground", bg: "bg-muted/30", label: "Low" };
}

// Decay status indicator
function decayStatus(decay: number): { color: string; label: string } {
  if (decay <= 0.05) return { color: "text-success", label: "Fresh" };
  if (decay <= 0.10) return { color: "text-accent", label: "Stable" };
  if (decay <= 0.15) return { color: "text-warning", label: "Fading" };
  return { color: "text-error", label: "Decaying" };
}

export default function MemoryPage() {
  const [memories, setMemories] = useState<MemoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<MemoryEntry | null>(null);
  const [deleting, setDeleting] = useState<Set<string>>(new Set());

  // Fetch all memories
  const fetchMemories = useCallback(async () => {
    try {
      setError(null);
      const res = await api.get<MemoryListResponse>("/memories", { scope_id: "global", limit: "200" });
      if (res.data) setMemories(res.data.memories ?? []);
    } catch {
      setError("Could not connect to the API server");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchMemories();
  }, [fetchMemories]);

  // Search memories semantically
  const handleSearch = useCallback(async () => {
    if (!searchQuery.trim()) {
      void fetchMemories();
      return;
    }
    setSearching(true);
    try {
      const res = await api.post<MemoryListResponse>("/memories/search", {
        scope_id: "global",
        query: searchQuery.trim(),
        limit: 50,
      });
      if (res.data) setMemories(res.data.memories ?? []);
      if (res.error) toast.error(`Search failed: ${res.error.message}`);
    } catch {
      toast.error("Search request failed");
    } finally {
      setSearching(false);
    }
  }, [searchQuery, fetchMemories]);

  // Delete a memory
  const handleDelete = useCallback(
    async (id: string) => {
      if (deleting.has(id)) return;
      setDeleting((prev) => new Set(prev).add(id));
      try {
        const res = await api.delete(`/memories/${id}`);
        if (res.error) {
          toast.error(`Delete failed: ${res.error.message}`);
          return;
        }
        toast.success("Memory deleted");
        setMemories((prev) => prev.filter((m) => m.id !== id));
      } finally {
        setDeleting((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
        setDeleteTarget(null);
      }
    },
    [deleting],
  );

  // Stats computed from current memory list
  const stats = useMemo(() => {
    if (memories.length === 0) return { total: 0, avgImportance: 0, avgDecay: 0 };
    const importances = memories
      .map((m) => m.metadata?.importance)
      .filter((v): v is number => typeof v === "number");
    const decays = memories
      .map((m) => m.metadata?.decay)
      .filter((v): v is number => typeof v === "number");
    return {
      total: memories.length,
      avgImportance: importances.length > 0 ? importances.reduce((a, b) => a + b, 0) / importances.length : 0,
      avgDecay: decays.length > 0 ? decays.reduce((a, b) => a + b, 0) / decays.length : 0,
    };
  }, [memories]);

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <h1 className="mb-4 sm:mb-6 text-xl sm:text-2xl font-semibold font-display tracking-tight text-foreground">
          Agent Memory
        </h1>
        <div className="rounded-xl border border-error/30 bg-error/5 p-4">
          <p className="text-sm text-error">{error}</p>
          <button
            onClick={() => {
              setLoading(true);
              void fetchMemories();
            }}
            className="mt-2 text-xs font-medium text-accent hover:text-accent/80 transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4 sm:space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl sm:text-2xl font-semibold font-display tracking-tight text-foreground">
            Agent Memory
          </h1>
          <p className="mt-1 text-sm text-muted">
            Semantic memory store - what your agents have learned across workflow runs.
          </p>
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div className="rounded-xl border border-border bg-surface p-4">
          <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground mb-1">
            <Brain className="h-3.5 w-3.5" />
            Total Memories
          </div>
          <p className="text-2xl font-semibold text-foreground">{stats.total}</p>
        </div>
        <div className="rounded-xl border border-border bg-surface p-4">
          <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground mb-1">
            <TrendingDown className="h-3.5 w-3.5" />
            Avg Importance
          </div>
          <p className="text-2xl font-semibold text-foreground">
            {stats.avgImportance > 0 ? (stats.avgImportance * 100).toFixed(0) + "%" : "-"}
          </p>
        </div>
        <div className="rounded-xl border border-border bg-surface p-4">
          <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground mb-1">
            <Clock className="h-3.5 w-3.5" />
            Avg Decay
          </div>
          <p className="text-2xl font-semibold text-foreground">
            {stats.avgDecay > 0 ? (stats.avgDecay * 100).toFixed(1) + "%" : "-"}
          </p>
        </div>
      </div>

      {/* Search bar */}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <input
            type="text"
            placeholder="Search memories semantically..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleSearch();
            }}
            className={cn(
              "w-full rounded-lg border border-border bg-surface py-2 pl-9 pr-3",
              "text-sm text-foreground placeholder:text-muted-foreground",
              "focus:outline-none focus:ring-2 focus:ring-accent/40 focus:border-accent",
              "transition-colors",
            )}
          />
        </div>
        <button
          onClick={() => void handleSearch()}
          disabled={searching}
          className={cn(
            "flex items-center gap-2 font-medium",
            buttonMd, buttonPrimary,
            "disabled:opacity-50 disabled:cursor-not-allowed",
          )}
        >
          {searching ? <LoadingSpinner size="sm" /> : <Search className={iconMd} />}
          Search
        </button>
        {searchQuery && (
          <button
            onClick={() => {
              setSearchQuery("");
              void fetchMemories();
            }}
            className={cn("font-medium", buttonMd, buttonSecondary)}
          >
            Clear
          </button>
        )}
      </div>

      {/* Memory list */}
      {memories.length === 0 ? (
        <EmptyState
          variant={searchQuery ? "tide" : "castle"}
          title={searchQuery ? "No matching memories" : "No memories yet"}
          description={
            searchQuery
              ? "Try a different search query or clear the filter."
              : "Agents write memories here as they learn from workflow runs."
          }
          action={
            searchQuery
              ? {
                  label: "Clear search",
                  onClick: () => {
                    setSearchQuery("");
                    void fetchMemories();
                  },
                }
              : undefined
          }
        />
      ) : (
        <div className="space-y-3">
          {memories.map((mem) => {
            const imp = mem.metadata?.importance ?? 0;
            const dec = mem.metadata?.decay ?? 0;
            const impInfo = importanceLabel(imp);
            const decInfo = decayStatus(dec);
            const isDeleting = deleting.has(mem.id);

            return (
              <div
                key={mem.id}
                className={cn(
                  "rounded-xl border border-border bg-surface p-4 sm:p-5",
                  "transition-all duration-200 hover:border-accent/30",
                  isDeleting && "opacity-50",
                )}
              >
                {/* Top row: content and delete button */}
                <div className="flex gap-3">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-foreground leading-relaxed">{mem.memory}</p>
                  </div>
                  <button
                    onClick={() => setDeleteTarget(mem)}
                    disabled={isDeleting}
                    aria-label={`Delete memory ${mem.id}`}
                    className={cn(
                      "shrink-0 rounded-lg p-2 text-muted-foreground",
                      "hover:bg-error/10 hover:text-error transition-colors",
                      "disabled:opacity-50 disabled:cursor-not-allowed",
                    )}
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>

                {/* Bottom row: metadata */}
                <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
                  {/* Importance badge */}
                  <span
                    className={cn(
                      "inline-flex items-center gap-1 rounded-md px-2 py-0.5 font-medium",
                      impInfo.bg,
                      impInfo.color,
                    )}
                  >
                    {(imp * 100).toFixed(0)}% - {impInfo.label}
                  </span>

                  {/* Decay status */}
                  <span className={cn("inline-flex items-center gap-1", decInfo.color)}>
                    <TrendingDown className="h-3 w-3" />
                    {decInfo.label} ({(dec * 100).toFixed(0)}%)
                  </span>

                  {/* Source workflow */}
                  {mem.metadata?.source_workflow && (
                    <span className="inline-flex items-center gap-1 text-muted-foreground">
                      <Workflow className="h-3 w-3" />
                      {mem.metadata.source_workflow}
                    </span>
                  )}

                  {/* Created time */}
                  <span className="inline-flex items-center gap-1 text-muted-foreground">
                    <Clock className="h-3 w-3" />
                    {timeAgo(mem.created_at)}
                  </span>

                  {/* Tags */}
                  {mem.metadata?.tags && mem.metadata.tags.length > 0 && (
                    <div className="inline-flex items-center gap-1 flex-wrap">
                      <Tag className="h-3 w-3 text-muted-foreground" />
                      {mem.metadata.tags.map((tag) => (
                        <span
                          key={tag}
                          className="rounded bg-border/50 px-1.5 py-0.5 text-[11px] text-muted-foreground"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Delete confirmation dialog */}
      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete memory?"
        description={
          deleteTarget
            ? `This will permanently remove this memory: "${deleteTarget.memory.slice(0, 80)}${deleteTarget.memory.length > 80 ? "..." : ""}"`
            : ""
        }
        confirmLabel="Delete"
        variant="danger"
        onConfirm={() => {
          if (deleteTarget) void handleDelete(deleteTarget.id);
        }}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
