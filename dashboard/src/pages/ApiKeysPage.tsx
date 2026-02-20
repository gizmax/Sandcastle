import { useCallback, useEffect, useRef, useState } from "react";
import { Key, KeyRound, Plus, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { ApiKeyTable, type ApiKeyItem } from "@/components/api-keys/ApiKeyTable";
import { CreateApiKeyModal } from "@/components/api-keys/CreateApiKeyModal";
import { KeyRevealModal } from "@/components/api-keys/KeyRevealModal";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { SectionCard, FieldLabel, HelperText, inputClass } from "@/components/ui/SectionCard";
import { cn } from "@/lib/utils";

// -- Types ------------------------------------------------------------------

interface SettingsData {
  anthropic_api_key: string;
  e2b_api_key: string;
  openai_api_key: string;
  minimax_api_key: string;
  openrouter_api_key: string;
  [key: string]: unknown;
}

// -- Main component ---------------------------------------------------------

export default function ApiKeysPage() {
  const [keys, setKeys] = useState<ApiKeyItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [revealKey, setRevealKey] = useState<string | null>(null);

  // External services state
  const [anthropicKey, setAnthropicKey] = useState("");
  const [e2bKey, setE2bKey] = useState("");
  const [openaiKey, setOpenaiKey] = useState("");
  const [minimaxKey, setMinimaxKey] = useState("");
  const [openrouterKey, setOpenrouterKey] = useState("");
  const originalRef = useRef<{
    anthropic_api_key: string;
    e2b_api_key: string;
    openai_api_key: string;
    minimax_api_key: string;
    openrouter_api_key: string;
  } | null>(null);
  const [saving, setSaving] = useState(false);

  const fetchKeys = useCallback(async () => {
    try {
      const res = await api.get<ApiKeyItem[]>("/api-keys");
      if (res.data) setKeys(res.data);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchSettings = useCallback(async () => {
    const res = await api.get<SettingsData>("/settings");
    if (res.data) {
      setAnthropicKey(res.data.anthropic_api_key);
      setE2bKey(res.data.e2b_api_key);
      setOpenaiKey(res.data.openai_api_key);
      setMinimaxKey(res.data.minimax_api_key);
      setOpenrouterKey(res.data.openrouter_api_key);
      originalRef.current = {
        anthropic_api_key: res.data.anthropic_api_key,
        e2b_api_key: res.data.e2b_api_key,
        openai_api_key: res.data.openai_api_key,
        minimax_api_key: res.data.minimax_api_key,
        openrouter_api_key: res.data.openrouter_api_key,
      };
    }
  }, []);

  useEffect(() => {
    void fetchKeys();
    void fetchSettings();
  }, [fetchKeys, fetchSettings]);

  const handleCreate = useCallback(
    async (data: { name: string; tenant_id: string; max_cost_per_run_usd?: number }) => {
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

  const credentialsDirty =
    originalRef.current != null &&
    (anthropicKey !== originalRef.current.anthropic_api_key ||
      e2bKey !== originalRef.current.e2b_api_key ||
      openaiKey !== originalRef.current.openai_api_key ||
      minimaxKey !== originalRef.current.minimax_api_key ||
      openrouterKey !== originalRef.current.openrouter_api_key);

  const handleSaveCredentials = useCallback(async () => {
    if (!originalRef.current) return;
    const changed: Record<string, string> = {};
    if (anthropicKey !== originalRef.current.anthropic_api_key) changed.anthropic_api_key = anthropicKey;
    if (e2bKey !== originalRef.current.e2b_api_key) changed.e2b_api_key = e2bKey;
    if (openaiKey !== originalRef.current.openai_api_key) changed.openai_api_key = openaiKey;
    if (minimaxKey !== originalRef.current.minimax_api_key) changed.minimax_api_key = minimaxKey;
    if (openrouterKey !== originalRef.current.openrouter_api_key) changed.openrouter_api_key = openrouterKey;
    if (Object.keys(changed).length === 0) return;

    setSaving(true);
    const res = await api.patch<SettingsData>("/settings", changed);
    setSaving(false);

    if (res.error) {
      toast.error(`Failed to save: ${res.error.message}`);
    } else {
      originalRef.current = {
        anthropic_api_key: anthropicKey,
        e2b_api_key: e2bKey,
        openai_api_key: openaiKey,
        minimax_api_key: minimaxKey,
        openrouter_api_key: openrouterKey,
      };
      toast.success("Credentials saved successfully");
    }
  }, [anthropicKey, e2bKey, openaiKey, minimaxKey, openrouterKey]);

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

      {/* External Services */}
      <SectionCard
        icon={KeyRound}
        title="External Services"
        description="API keys for AI providers and sandbox runtimes"
      >
        <div className="space-y-4">
          <div>
            <FieldLabel htmlFor="anthropic_api_key">Anthropic API Key</FieldLabel>
            <input
              id="anthropic_api_key"
              type="password"
              className={inputClass}
              value={anthropicKey}
              onChange={(e) => setAnthropicKey(e.target.value)}
              placeholder={originalRef.current?.anthropic_api_key || "sk-ant-..."}
            />
          </div>
          <div>
            <FieldLabel htmlFor="e2b_api_key">E2B API Key</FieldLabel>
            <input
              id="e2b_api_key"
              type="password"
              className={inputClass}
              value={e2bKey}
              onChange={(e) => setE2bKey(e.target.value)}
              placeholder={originalRef.current?.e2b_api_key || "e2b_..."}
            />
          </div>
          <div>
            <FieldLabel htmlFor="openai_api_key">OpenAI API Key</FieldLabel>
            <input
              id="openai_api_key"
              type="password"
              className={inputClass}
              value={openaiKey}
              onChange={(e) => setOpenaiKey(e.target.value)}
              placeholder={originalRef.current?.openai_api_key || "sk-..."}
            />
          </div>
          <div>
            <FieldLabel htmlFor="minimax_api_key">MiniMax API Key</FieldLabel>
            <input
              id="minimax_api_key"
              type="password"
              className={inputClass}
              value={minimaxKey}
              onChange={(e) => setMinimaxKey(e.target.value)}
              placeholder={originalRef.current?.minimax_api_key || "minimax-..."}
            />
          </div>
          <div>
            <FieldLabel htmlFor="openrouter_api_key">OpenRouter API Key</FieldLabel>
            <input
              id="openrouter_api_key"
              type="password"
              className={inputClass}
              value={openrouterKey}
              onChange={(e) => setOpenrouterKey(e.target.value)}
              placeholder={originalRef.current?.openrouter_api_key || "sk-or-..."}
            />
          </div>
          <HelperText>Leave empty to keep the current value. Values are masked for security.</HelperText>
          <div className="flex justify-end">
            <button
              disabled={!credentialsDirty || saving}
              onClick={() => void handleSaveCredentials()}
              className={cn(
                "flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors",
                credentialsDirty
                  ? "bg-accent text-accent-foreground hover:bg-accent-hover shadow-sm hover:shadow-md cursor-pointer"
                  : "bg-border text-muted cursor-not-allowed"
              )}
            >
              {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Save
            </button>
          </div>
        </div>
      </SectionCard>

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
