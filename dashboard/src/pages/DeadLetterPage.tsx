import { useCallback, useEffect, useState } from "react";
import { Inbox } from "lucide-react";
import { api } from "@/api/client";
import { DeadLetterTable, type DLQItem } from "@/components/dead-letter/DeadLetterTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";

export default function DeadLetterPage() {
  const [items, setItems] = useState<DLQItem[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchItems = useCallback(async () => {
    const res = await api.get<DLQItem[]>("/dead-letter");
    if (res.data) setItems(res.data);
    setLoading(false);
  }, []);

  useEffect(() => {
    void fetchItems();
  }, [fetchItems]);

  const handleRetry = useCallback(
    async (id: string) => {
      await api.post(`/dead-letter/${id}/retry`);
      fetchItems();
    },
    [fetchItems]
  );

  const handleResolve = useCallback(
    async (id: string) => {
      await api.post(`/dead-letter/${id}/resolve`);
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
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight text-foreground">Dead Letter Queue</h1>

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
