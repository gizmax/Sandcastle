import { useCallback, useEffect, useState } from "react";
import { Inbox } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { DeadLetterTable, type DLQItem } from "@/components/dead-letter/DeadLetterTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";

export default function DeadLetterPage() {
  const [items, setItems] = useState<DLQItem[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchItems = useCallback(async () => {
    try {
      const res = await api.get<DLQItem[]>("/dead-letter");
      if (res.data) setItems(res.data);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchItems();
  }, [fetchItems]);

  const handleRetry = useCallback(
    async (id: string) => {
      const res = await api.post(`/dead-letter/${id}/retry`);
      if (res.error) {
        toast.error(`Failed to retry: ${res.error.message}`);
        return;
      }
      toast.success("Item queued for retry");
      fetchItems();
    },
    [fetchItems]
  );

  const handleResolve = useCallback(
    async (id: string) => {
      const res = await api.post(`/dead-letter/${id}/resolve`);
      if (res.error) {
        toast.error(`Failed to resolve: ${res.error.message}`);
        return;
      }
      toast.success("Item resolved");
      fetchItems();
    },
    [fetchItems]
  );

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  return (
    <div className="space-y-4 sm:space-y-6">
      <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">Dead Letter Queue</h1>

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
