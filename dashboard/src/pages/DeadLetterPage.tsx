import { useCallback, useEffect, useMemo, useState } from "react";
import { Inbox, AlertTriangle, Layers, List } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { DeadLetterTable, type DLQItem } from "@/components/dead-letter/DeadLetterTable";
import { ErrorClusters } from "@/components/dead-letter/ErrorClusters";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { ContextBanner } from "@/components/shared/ContextBanner";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { cn } from "@/lib/utils";

type ViewMode = "clustered" | "list";

export default function DeadLetterPage() {
  const [items, setItems] = useState<DLQItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<Set<string>>(new Set());
  const [viewMode, setViewMode] = useState<ViewMode>("clustered");
  const [batchConfirm, setBatchConfirm] = useState<{
    action: "retry" | "resolve";
    ids: string[];
  } | null>(null);
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

  const executeBatchAction = useCallback(
    async (action: "retry" | "resolve", ids: string[]) => {
      const endpoint = action === "retry" ? "retry" : "resolve";
      const label = action === "retry" ? "retried" : "resolved";
      let successCount = 0;
      let failCount = 0;

      for (const id of ids) {
        try {
          const res = await api.post(`/dead-letter/${id}/${endpoint}`);
          if (res.error) {
            failCount++;
          } else {
            successCount++;
          }
        } catch {
          failCount++;
        }
      }

      if (failCount > 0) {
        toast.error(`${failCount} item${failCount > 1 ? "s" : ""} failed`);
      }
      if (successCount > 0) {
        toast.success(`${successCount} item${successCount > 1 ? "s" : ""} ${label}`);
      }
      void fetchItems();
    },
    [fetchItems],
  );

  const handleBatchRetry = useCallback(
    (ids: string[]) => {
      setBatchConfirm({ action: "retry", ids });
    },
    [],
  );

  const handleBatchResolve = useCallback(
    (ids: string[]) => {
      setBatchConfirm({ action: "resolve", ids });
    },
    [],
  );

  const confirmBatchAction = useCallback(() => {
    if (!batchConfirm) return;
    void executeBatchAction(batchConfirm.action, batchConfirm.ids);
    setBatchConfirm(null);
  }, [batchConfirm, executeBatchAction]);

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
        <h1 className="mb-4 sm:mb-6 text-xl sm:text-2xl font-semibold font-display tracking-tight text-foreground">Dead Letter Queue</h1>
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
      {/* Header row */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl sm:text-2xl font-semibold font-display tracking-tight text-foreground">
          Dead Letter Queue
        </h1>

        {items.length > 0 && (
          <div className="flex items-center rounded-lg border border-border bg-surface p-0.5">
            <button
              onClick={() => setViewMode("clustered")}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                viewMode === "clustered"
                  ? "bg-accent text-white"
                  : "text-muted hover:text-foreground",
              )}
            >
              <Layers className="h-3.5 w-3.5" />
              Clustered
            </button>
            <button
              onClick={() => setViewMode("list")}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                viewMode === "list"
                  ? "bg-accent text-white"
                  : "text-muted hover:text-foreground",
              )}
            >
              <List className="h-3.5 w-3.5" />
              List
            </button>
          </div>
        )}
      </div>

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
      ) : viewMode === "clustered" ? (
        <ErrorClusters
          items={items}
          actionLoading={actionLoading}
          onRetry={handleRetry}
          onResolve={handleResolve}
          onBatchRetry={handleBatchRetry}
          onBatchResolve={handleBatchResolve}
        />
      ) : (
        <DeadLetterTable items={items} actionLoading={actionLoading} onRetry={handleRetry} onResolve={handleResolve} />
      )}

      {/* Batch action confirmation dialog */}
      <ConfirmDialog
        open={batchConfirm !== null}
        title={
          batchConfirm?.action === "retry"
            ? `Retry ${batchConfirm.ids.length} item${batchConfirm.ids.length > 1 ? "s" : ""}?`
            : `Resolve ${batchConfirm?.ids.length ?? 0} item${(batchConfirm?.ids.length ?? 0) > 1 ? "s" : ""}?`
        }
        description={
          batchConfirm?.action === "retry"
            ? `This will queue ${batchConfirm.ids.length} dead letter item${batchConfirm.ids.length > 1 ? "s" : ""} for retry.`
            : `This will mark ${batchConfirm?.ids.length ?? 0} dead letter item${(batchConfirm?.ids.length ?? 0) > 1 ? "s" : ""} as resolved.`
        }
        confirmLabel={batchConfirm?.action === "retry" ? "Retry All" : "Resolve All"}
        variant="warning"
        onConfirm={confirmBatchAction}
        onCancel={() => setBatchConfirm(null)}
      />
    </div>
  );
}
