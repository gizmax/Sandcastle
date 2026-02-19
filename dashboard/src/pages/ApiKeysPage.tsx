import { useCallback, useEffect, useRef, useState } from "react";
import { Key, KeyRound, Plus, Check, AlertCircle, Loader2 } from "lucide-react";
import { api } from "@/api/client";
import { ApiKeyTable, type ApiKeyItem } from "@/components/api-keys/ApiKeyTable";
import { CreateApiKeyModal } from "@/components/api-keys/CreateApiKeyModal";
import { KeyRevealModal } from "@/components/api-keys/KeyRevealModal";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";

// -- Types ------------------------------------------------------------------

interface SettingsData {
  anthropic_api_key: string;
  e2b_api_key: string;
  [key: string]: unknown;
}

// -- Sub-components ---------------------------------------------------------

function SectionCard({
  icon: Icon,
  title,
  description,
  children,
}: {
  icon: React.ElementType;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-border bg-surface p-5 sm:p-6 shadow-sm">
      <div className="flex items-center gap-3 mb-4">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent/10">
          <Icon className="h-[18px] w-[18px] text-accent" />
        </div>
        <div>
          <h2 className="text-lg font-semibold text-foreground">{title}</h2>
          <p className="text-sm text-muted-foreground mt-0.5">{description}</p>
        </div>
      </div>
      {children}
    </div>
  );
}

function FieldLabel({ htmlFor, children }: { htmlFor: string; children: React.ReactNode }) {
  return (
    <label htmlFor={htmlFor} className="block text-sm font-medium text-foreground mb-1.5">
      {children}
    </label>
  );
}

function HelperText({ children }: { children: React.ReactNode }) {
  return <p className="text-xs text-muted-foreground mt-1">{children}</p>;
}

const inputClass = cn(
  "h-9 w-full rounded-lg border border-border bg-background px-3 text-sm text-foreground",
  "placeholder:text-muted-foreground",
  "focus:outline-none focus:ring-2 focus:ring-ring/30",
  "transition-colors"
);

function Toast({ message, type }: { message: string; type: "success" | "error" }) {
  return (
    <div
      className={cn(
        "fixed bottom-6 right-6 z-50 flex items-center gap-2 rounded-lg px-4 py-3 text-sm font-medium shadow-lg",
        "animate-[fadeIn_0.2s_ease-out]",
        type === "success"
          ? "bg-success/15 border border-success/30 text-success"
          : "bg-error/15 border border-error/30 text-error"
      )}
    >
      {type === "success" ? (
        <Check className="h-4 w-4" />
      ) : (
        <AlertCircle className="h-4 w-4" />
      )}
      {message}
    </div>
  );
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
  const originalRef = useRef<{ anthropic_api_key: string; e2b_api_key: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);

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
      originalRef.current = {
        anthropic_api_key: res.data.anthropic_api_key,
        e2b_api_key: res.data.e2b_api_key,
      };
    }
  }, []);

  useEffect(() => {
    void fetchKeys();
    void fetchSettings();
  }, [fetchKeys, fetchSettings]);

  // Toast auto-dismiss
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 3000);
    return () => clearTimeout(timer);
  }, [toast]);

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
      e2bKey !== originalRef.current.e2b_api_key);

  const handleSaveCredentials = useCallback(async () => {
    if (!originalRef.current) return;
    const changed: Record<string, string> = {};
    if (anthropicKey !== originalRef.current.anthropic_api_key) changed.anthropic_api_key = anthropicKey;
    if (e2bKey !== originalRef.current.e2b_api_key) changed.e2b_api_key = e2bKey;
    if (Object.keys(changed).length === 0) return;

    setSaving(true);
    const res = await api.patch<SettingsData>("/settings", changed);
    setSaving(false);

    if (res.error) {
      setToast({ message: `Failed to save: ${res.error.message}`, type: "error" });
    } else {
      originalRef.current = { anthropic_api_key: anthropicKey, e2b_api_key: e2bKey };
      setToast({ message: "Credentials saved successfully", type: "success" });
    }
  }, [anthropicKey, e2bKey]);

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
            <FieldLabel htmlFor="openai_api_key_hint">OpenAI API Key</FieldLabel>
            <input
              id="openai_api_key_hint"
              type="text"
              className={cn(inputClass, "bg-border/30 cursor-not-allowed")}
              readOnly
              placeholder="Set via OPENAI_API_KEY env var"
            />
          </div>
          <div>
            <FieldLabel htmlFor="minimax_api_key_hint">MiniMax API Key</FieldLabel>
            <input
              id="minimax_api_key_hint"
              type="text"
              className={cn(inputClass, "bg-border/30 cursor-not-allowed")}
              readOnly
              placeholder="Set via MINIMAX_API_KEY env var"
            />
          </div>
          <div>
            <FieldLabel htmlFor="openrouter_api_key_hint">OpenRouter API Key</FieldLabel>
            <input
              id="openrouter_api_key_hint"
              type="text"
              className={cn(inputClass, "bg-border/30 cursor-not-allowed")}
              readOnly
              placeholder="Set via OPENROUTER_API_KEY env var"
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

      {toast && <Toast message={toast.message} type={toast.type} />}
    </div>
  );
}
