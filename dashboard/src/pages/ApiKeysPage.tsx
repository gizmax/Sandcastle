import { useCallback, useEffect, useState } from "react";
import { Plus, Loader2, Sparkles, Server } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { ApiKeyTable, type ApiKeyItem } from "@/components/api-keys/ApiKeyTable";
import { CreateApiKeyModal } from "@/components/api-keys/CreateApiKeyModal";
import { KeyRevealModal } from "@/components/api-keys/KeyRevealModal";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { SectionCard, HelperText } from "@/components/ui/SectionCard";
import { cn, inputClass, buttonMd, buttonLg, buttonSm, buttonPrimary, buttonSecondary, iconMd, iconSm } from "@/lib/utils";

// -- Types ------------------------------------------------------------------

interface SettingsData {
  anthropic_api_key: string;
  mistral_api_key: string;
  openai_api_key: string;
  minimax_api_key: string;
  openrouter_api_key: string;
  e2b_api_key: string;
  [key: string]: unknown;
}

// -- Main component ---------------------------------------------------------

export default function ApiKeysPage() {
  const [keys, setKeys] = useState<ApiKeyItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [revealKey, setRevealKey] = useState<string | null>(null);
  const [deactivateId, setDeactivateId] = useState<string | null>(null);
  const [deactivating, setDeactivating] = useState(false);
  const [creating, setCreating] = useState(false);

  // External services state - track which keys are configured (boolean)
  // and only expose an editable input when the user explicitly clicks "Change".
  // This prevents masked partial values from being written into component state
  // where they could be accidentally submitted or logged.
  type ServiceKey = "anthropic_api_key" | "mistral_api_key" | "openai_api_key" | "minimax_api_key" | "openrouter_api_key" | "e2b_api_key";
  const [configured, setConfigured] = useState<Record<ServiceKey, boolean>>({
    anthropic_api_key: false,
    mistral_api_key: false,
    openai_api_key: false,
    minimax_api_key: false,
    openrouter_api_key: false,
    e2b_api_key: false,
  });
  // editingKeys: set of keys the user has clicked "Change" on
  const [editingKeys, setEditingKeys] = useState<Set<ServiceKey>>(new Set());
  // newValues: what the user has typed for keys being edited (empty = unchanged)
  const [newValues, setNewValues] = useState<Partial<Record<ServiceKey, string>>>({});
  const [saving, setSaving] = useState(false);

  const fetchKeys = useCallback(async () => {
    try {
      setError(null);
      const res = await api.get<ApiKeyItem[]>("/api-keys");
      if (res.data) setKeys(res.data);
    } catch {
      setError("Could not connect to the API server");
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchSettings = useCallback(async () => {
    const res = await api.get<SettingsData>("/settings");
    if (res.data) {
      // Store only whether each key is configured (non-empty), not the masked value.
      // This avoids loading partial/masked secrets into editable form state.
      setConfigured({
        anthropic_api_key: Boolean(res.data.anthropic_api_key),
        mistral_api_key: Boolean(res.data.mistral_api_key),
        openai_api_key: Boolean(res.data.openai_api_key),
        minimax_api_key: Boolean(res.data.minimax_api_key),
        openrouter_api_key: Boolean(res.data.openrouter_api_key),
        e2b_api_key: Boolean(res.data.e2b_api_key),
      });
    }
  }, []);

  useEffect(() => {
    void fetchKeys();
    void fetchSettings();
  }, [fetchKeys, fetchSettings]);

  const handleCreate = useCallback(
    async (data: { name: string; tenant_id: string; max_cost_per_run_usd?: number }) => {
      setCreating(true);
      try {
        const res = await api.post<{ key: string; key_prefix: string; id: string }>(
          "/api-keys",
          data
        );
        setCreateModalOpen(false);
        if (res.error) {
          toast.error(`Failed to create key: ${res.error.message}`);
          return;
        }
        if (res.data && "key" in res.data) {
          setRevealKey(res.data.key);
          void fetchKeys();
        }
      } finally {
        setCreating(false);
      }
    },
    [fetchKeys]
  );

  const handleDeactivate = useCallback((id: string) => {
    setDeactivateId(id);
  }, []);

  const confirmDeactivate = useCallback(async () => {
    if (!deactivateId) return;
    setDeactivating(true);
    try {
      const res = await api.delete(`/api-keys/${deactivateId}`);
      if (res.error) {
        toast.error(`Failed to deactivate key: ${res.error.message}`);
        setDeactivateId(null);
        return;
      }
      toast.success("API key deactivated");
      setKeys((prev) => prev.filter((k) => k.id !== deactivateId));
      setDeactivateId(null);
    } finally {
      setDeactivating(false);
    }
  }, [deactivateId]);

  // Dirty if any editing field has a non-empty new value
  const credentialsDirty = Object.values(newValues).some((v) => v && v.length > 0);

  const handleStartEditing = useCallback((key: ServiceKey) => {
    setEditingKeys((prev) => new Set([...prev, key]));
    setNewValues((prev) => ({ ...prev, [key]: "" }));
  }, []);

  const handleCancelEditing = useCallback((key: ServiceKey) => {
    setEditingKeys((prev) => {
      const next = new Set(prev);
      next.delete(key);
      return next;
    });
    setNewValues((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }, []);

  const handleSaveCredentials = useCallback(async () => {
    const changed: Record<string, string> = {};
    for (const [key, value] of Object.entries(newValues)) {
      if (value && value.length > 0) changed[key] = value;
    }
    if (Object.keys(changed).length === 0) return;

    setSaving(true);
    const res = await api.patch<SettingsData>("/settings", changed);
    setSaving(false);

    if (res.error) {
      toast.error(`Failed to save: ${res.error.message}`);
    } else {
      // Mark saved keys as configured, clear editing state
      setConfigured((prev) => {
        const next = { ...prev };
        for (const key of Object.keys(changed) as ServiceKey[]) {
          next[key] = true;
        }
        return next;
      });
      setEditingKeys(new Set());
      setNewValues({});
      toast.success("Credentials saved");
    }
  }, [newValues]);

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
        <h1 className="mb-4 sm:mb-6 text-xl sm:text-2xl font-semibold font-display tracking-tight text-foreground">API Keys</h1>
        <div className="rounded-xl border border-error/30 bg-error/5 p-4">
          <p className="text-sm text-error">{error}</p>
          <button
            onClick={() => { setLoading(true); void fetchKeys(); }}
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
      <div className="flex items-center justify-between">
        <h1 className="text-xl sm:text-2xl font-semibold font-display tracking-tight text-foreground">API Keys</h1>
        <button
          onClick={() => setCreateModalOpen(true)}
          aria-label="New API key"
          className={cn(
            "flex items-center gap-2 font-medium",
            buttonMd, buttonPrimary,
            "shadow-sm hover:shadow-md"
          )}
        >
          <Plus className={iconMd} />
          <span className="hidden sm:inline">New API Key</span>
        </button>
      </div>

      {keys.length === 0 ? (
        <EmptyState
          variant="castle"
          title="No API keys yet"
          description="Mint your first key to authenticate API requests."
          action={{ label: "Create API Key", onClick: () => setCreateModalOpen(true) }}
        />
      ) : (
        <ApiKeyTable keys={keys} onDeactivate={handleDeactivate} />
      )}

      {/* AI Provider Keys */}
      <SectionCard
        icon={Sparkles}
        title="AI Provider Keys"
        description="API keys for LLM providers used by the universal advisor, evolution, and workflow generation."
      >
        <div className="space-y-4">
          {(
            [
              { key: "anthropic_api_key" as ServiceKey, label: "Anthropic (Claude)", placeholder: "sk-ant-...", region: "US" },
              { key: "mistral_api_key" as ServiceKey, label: "Mistral", placeholder: "mistral-...", region: "EU" },
              { key: "openai_api_key" as ServiceKey, label: "OpenAI", placeholder: "sk-...", region: "US" },
              { key: "minimax_api_key" as ServiceKey, label: "MiniMax", placeholder: "minimax-...", region: "US" },
              { key: "openrouter_api_key" as ServiceKey, label: "OpenRouter (incl. Google Gemini)", placeholder: "sk-or-...", region: "US" },
            ] as const
          ).map(({ key, label, placeholder, region }) => (
            <div key={key} className="flex items-center gap-3">
              <div className="w-40 shrink-0">
                <div className="text-sm font-medium text-foreground">{label}</div>
                <div className="text-[10px] text-muted-foreground">{region}</div>
              </div>
              <div className="flex-1">
                {editingKeys.has(key) ? (
                  <div className="flex items-center gap-2">
                    <input
                      id={key}
                      type="password"
                      className={cn(inputClass, "flex-1")}
                      value={newValues[key] ?? ""}
                      onChange={(e) => setNewValues((prev) => ({ ...prev, [key]: e.target.value }))}
                      placeholder={placeholder}
                      autoFocus
                    />
                    <button
                      type="button"
                      onClick={() => handleCancelEditing(key)}
                      className={cn("shrink-0 font-medium", buttonSm, buttonSecondary)}
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <div className="flex items-center gap-2 h-9">
                    {configured[key] ? (
                      <span className="inline-flex items-center gap-1.5 rounded-full bg-success/10 border border-success/30 px-2.5 py-0.5 text-xs font-medium text-success">
                        Configured
                      </span>
                    ) : (
                      <span className="inline-flex items-center rounded-full bg-border/40 px-2.5 py-0.5 text-xs text-muted">
                        Not set
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={() => handleStartEditing(key)}
                      className={cn(buttonSm, buttonSecondary)}
                    >
                      {configured[key] ? "Change" : "Set"}
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      </SectionCard>

      {/* Infrastructure Keys */}
      <SectionCard
        icon={Server}
        title="Infrastructure Keys"
        description="Credentials for sandbox runtimes, storage, and deployment targets."
      >
        <div className="space-y-4">
          {(
            [
              { key: "e2b_api_key" as ServiceKey, label: "E2B Sandbox", placeholder: "e2b_...", desc: "Cloud sandbox for code execution" },
            ] as const
          ).map(({ key, label, placeholder, desc }) => (
            <div key={key} className="flex items-center gap-3">
              <div className="w-40 shrink-0">
                <div className="text-sm font-medium text-foreground">{label}</div>
                <div className="text-[10px] text-muted-foreground">{desc}</div>
              </div>
              <div className="flex-1">
                {editingKeys.has(key) ? (
                  <div className="flex items-center gap-2">
                    <input
                      id={key}
                      type="password"
                      className={cn(inputClass, "flex-1")}
                      value={newValues[key] ?? ""}
                      onChange={(e) => setNewValues((prev) => ({ ...prev, [key]: e.target.value }))}
                      placeholder={placeholder}
                      autoFocus
                    />
                    <button
                      type="button"
                      onClick={() => handleCancelEditing(key)}
                      className={cn("shrink-0 font-medium", buttonSm, buttonSecondary)}
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <div className="flex items-center gap-2 h-9">
                    {configured[key] ? (
                      <span className="inline-flex items-center gap-1.5 rounded-full bg-success/10 border border-success/30 px-2.5 py-0.5 text-xs font-medium text-success">
                        Configured
                      </span>
                    ) : (
                      <span className="inline-flex items-center rounded-full bg-border/40 px-2.5 py-0.5 text-xs text-muted">
                        Not set
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={() => handleStartEditing(key)}
                      className={cn(buttonSm, buttonSecondary)}
                    >
                      {configured[key] ? "Change" : "Set"}
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}
          <HelperText>Infrastructure keys are typically set during installation via CLI. You can update them here.</HelperText>
        </div>
      </SectionCard>

      {/* Shared save button for all credential changes */}
      {credentialsDirty && (
        <div className="flex justify-end">
          <button
            disabled={saving}
            onClick={() => void handleSaveCredentials()}
            className={cn(
              "flex items-center gap-2 font-medium",
              buttonLg, buttonPrimary,
              "shadow-sm hover:shadow-md"
            )}
          >
            {saving && <Loader2 className={cn(iconSm, "animate-spin")} />}
            Save Changes
          </button>
        </div>
      )}

      <CreateApiKeyModal
        open={createModalOpen}
        loading={creating}
        onClose={() => setCreateModalOpen(false)}
        onSubmit={handleCreate}
      />

      {revealKey && (
        <KeyRevealModal
          apiKey={revealKey}
          onClose={() => setRevealKey(null)}
        />
      )}

      <ConfirmDialog
        open={!!deactivateId}
        title="Deactivate API Key"
        description="Are you sure you want to deactivate this API key? Any services using this key will lose access immediately."
        confirmLabel={deactivating ? <><Loader2 className="inline h-3.5 w-3.5 animate-spin mr-1.5" />Deactivating...</> : "Deactivate"}
        confirmDisabled={deactivating}
        onConfirm={() => void confirmDeactivate()}
        onCancel={() => setDeactivateId(null)}
      />
    </div>
  );
}
