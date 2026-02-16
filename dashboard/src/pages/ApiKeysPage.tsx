import { useCallback, useEffect, useState } from "react";
import { Key, Plus } from "lucide-react";
import { api } from "@/api/client";
import { ApiKeyTable, type ApiKeyItem } from "@/components/api-keys/ApiKeyTable";
import { CreateApiKeyModal } from "@/components/api-keys/CreateApiKeyModal";
import { KeyRevealModal } from "@/components/api-keys/KeyRevealModal";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";

export default function ApiKeysPage() {
  const [keys, setKeys] = useState<ApiKeyItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [revealKey, setRevealKey] = useState<string | null>(null);

  const fetchKeys = useCallback(async () => {
    const res = await api.get<ApiKeyItem[]>("/api-keys");
    if (res.data) setKeys(res.data);
    setLoading(false);
  }, []);

  useEffect(() => {
    void fetchKeys();
  }, [fetchKeys]);

  const handleCreate = useCallback(
    async (data: { name: string; tenant_id: string }) => {
      const res = await api.post<{ key: string; key_prefix: string; id: string }>(
        "/api-keys",
        data
      );
      setCreateModalOpen(false);
      if (res.data && "key" in res.data) {
        setRevealKey(res.data.key);
        fetchKeys();
      }
    },
    [fetchKeys]
  );

  const handleDeactivate = useCallback(
    async (id: string) => {
      await api.delete(`/api-keys/${id}`);
      setKeys((prev) => prev.filter((k) => k.id !== id));
    },
    []
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
      <div className="flex items-center justify-between">
        <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">API Keys</h1>
        <button
          onClick={() => setCreateModalOpen(true)}
          className={cn(
            "flex items-center gap-2 rounded-lg bg-accent px-3 sm:px-4 py-2 text-sm font-medium text-accent-foreground",
            "hover:bg-accent-hover transition-all duration-200",
            "shadow-sm hover:shadow-md"
          )}
        >
          <Plus className="h-4 w-4" />
          <span className="hidden sm:inline">New API Key</span>
        </button>
      </div>

      {keys.length === 0 ? (
        <EmptyState
          icon={Key}
          title="No API keys yet"
          description="Create your first API key to authenticate requests."
          action={{ label: "Create API Key", onClick: () => setCreateModalOpen(true) }}
        />
      ) : (
        <ApiKeyTable keys={keys} onDeactivate={handleDeactivate} />
      )}

      <CreateApiKeyModal
        open={createModalOpen}
        onClose={() => setCreateModalOpen(false)}
        onSubmit={handleCreate}
      />

      {revealKey && (
        <KeyRevealModal
          apiKey={revealKey}
          onClose={() => setRevealKey(null)}
        />
      )}
    </div>
  );
}
