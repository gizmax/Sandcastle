import { useCallback, useEffect, useMemo, useState } from "react";
import { Inbox, AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { DeadLetterTable, type DLQItem } from "@/components/dead-letter/DeadLetterTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { ContextBanner } from "@/components/shared/ContextBanner";

export default function DeadLetterPage() {
  const [items, setItems] = useState<DLQItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<Set<string>>(new Set());
  const unresolvedItems = useMemo(() => items.filter((i) => !i.resolved_at), [items]);

  const fetchItems = useCallback(async () => {
    try {
      setError(null);
      const res = await api.get<DLQItem[]>("/dead-letter");
      if (res.data) setItems(res.data);
    } catch {
      setError("Could not connect to the API server");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchItems();
  }, [fetchItems]);

  const handleRetry = useCallback(
    async (id: string) => {
      if (actionLoading.has(id)) return;
      setActionLoading((prev) => new Set(prev).add(id));
      try {
        const res = await api.post(`/dead-letter/${id}/retry`);
        if (res.error) {
          toast.error(`Failed to retry: ${res.error.message}`);
          return;
        }
        toast.success("Item queued for retry");
        void fetchItems();
      } finally {
        setActionLoading((prev) => { const next = new Set(prev); next.delete(id); return next; });
      }
    },
    [fetchItems, actionLoading]
  );

  const handleResolve = useCallback(
    async (id: string) => {
      if (actionLoading.has(id)) return;
      setActionLoading((prev) => new Set(prev).add(id));
      try {
        const res = await api.post(`/dead-letter/${id}/resolve`);
        if (res.error) {
          toast.error(`Failed to resolve: ${res.error.message}`);
          return;
        }
        toast.success("Item resolved");
        void fetchItems();
      } finally {
        setActionLoading((prev) => { const next = new Set(prev); next.delete(id); return next; });
      }
    },
    [fetchItems, actionLoading]
  );

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
        <h1 className="mb-4 sm:mb-6 text-xl sm:text-2xl font-semibold tracking-tight text-foreground">Dead Letter Queue</h1>
        <div className="rounded-xl border border-error/30 bg-error/5 p-4">
          <p className="text-sm text-error">{error}</p>
          <button
            onClick={() => { setLoading(true); void fetchItems(); }}
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
      <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">Dead Letter Queue</h1>

      {unresolvedItems.length > 0 && (
        <ContextBanner variant="error" icon={AlertTriangle}>
          {unresolvedItems.length} unresolved item{unresolvedItems.length > 1 ? "s" : ""} - retry or resolve to clear the queue.
        </ContextBanner>
      )}

      {items.length === 0 ? (
        <EmptyState
          icon={Inbox}
          title="No dead letters"
          description="Failed steps that exceed retry limits will appear here."
        />
      ) : (
        <DeadLetterTable items={items} onRetry={handleRetry} onResolve={handleResolve} />
      )}
    </div>
  );
}
