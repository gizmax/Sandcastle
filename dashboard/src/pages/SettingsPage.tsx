import { useCallback, useEffect, useRef, useState } from "react";
import {
  Settings,
  Link,
  KeyRound,
  Shield,
  DollarSign,
  Webhook,
  Wrench,
  Server,
  Check,
  Loader2,
  AlertCircle,
  LogOut,
} from "lucide-react";
import { api } from "@/api/client";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";
import { useRuntimeInfo } from "@/hooks/useRuntimeInfo";

// -- Types ------------------------------------------------------------------

interface SettingsData {
  sandstorm_url: string;
  anthropic_api_key: string;
  e2b_api_key: string;
  auth_required: boolean;
  dashboard_origin: string;
  default_max_cost_usd: number;
  webhook_secret: string;
  log_level: string;
  max_workflow_depth: number;
  storage_backend: string;
  storage_bucket: string;
  storage_endpoint: string;
  data_dir: string;
  workflows_dir: string;
  is_local_mode: boolean;
  database_url: string;
  redis_url: string;
}

type EditableFields = Omit<
  SettingsData,
  | "storage_backend"
  | "storage_bucket"
  | "storage_endpoint"
  | "data_dir"
  | "workflows_dir"
  | "is_local_mode"
  | "database_url"
  | "redis_url"
>;

type SectionName =
  | "connections"
  | "credentials"
  | "security"
  | "budget"
  | "webhooks"
  | "system";

// -- Helpers ----------------------------------------------------------------

const LOG_LEVELS = ["debug", "info", "warning", "error"] as const;

/** Return only keys whose values differ between two objects. */
function diffFields(
  current: Partial<EditableFields>,
  original: Partial<EditableFields>
): Partial<EditableFields> {
  const changed: Record<string, unknown> = {};
  for (const key of Object.keys(current) as (keyof EditableFields)[]) {
    if (current[key] !== original[key]) {
      changed[key] = current[key];
    }
  }
  return changed as Partial<EditableFields>;
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

function SaveButton({
  dirty,
  saving,
  onClick,
}: {
  dirty: boolean;
  saving: boolean;
  onClick: () => void;
}) {
  return (
    <button
      disabled={!dirty || saving}
      onClick={onClick}
      className={cn(
        "flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors",
        dirty
          ? "bg-accent text-accent-foreground hover:bg-accent-hover shadow-sm hover:shadow-md cursor-pointer"
          : "bg-border text-muted cursor-not-allowed"
      )}
    >
      {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
      Save
    </button>
  );
}

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

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [savingSections, setSavingSections] = useState<Set<SectionName>>(new Set());
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);
  const [testingConnection, setTestingConnection] = useState(false);
  const { info: runtimeInfo } = useRuntimeInfo();

  // Keep a snapshot of the original values for dirty checking
  const originalRef = useRef<SettingsData | null>(null);

  const fetchSettings = useCallback(async () => {
    setFetchError(null);
    const res = await api.get<SettingsData>("/settings");
    if (res.data) {
      setSettings(res.data);
      originalRef.current = { ...res.data };
    } else if (res.error) {
      setFetchError(res.error.message || "Failed to load settings");
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void fetchSettings();
  }, [fetchSettings]);

  // Toast auto-dismiss
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 3000);
    return () => clearTimeout(timer);
  }, [toast]);

  // -- Field updaters -------------------------------------------------------

  function updateField<K extends keyof SettingsData>(key: K, value: SettingsData[K]) {
    setSettings((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  // -- Dirty checks per section ---------------------------------------------

  function isSectionDirty(section: SectionName): boolean {
    if (!settings || !originalRef.current) return false;
    const o = originalRef.current;
    switch (section) {
      case "connections":
        return settings.sandstorm_url !== o.sandstorm_url;
      case "credentials":
        return settings.anthropic_api_key !== o.anthropic_api_key || settings.e2b_api_key !== o.e2b_api_key;
      case "security":
        return settings.auth_required !== o.auth_required || settings.dashboard_origin !== o.dashboard_origin;
      case "budget":
        return settings.default_max_cost_usd !== o.default_max_cost_usd;
      case "webhooks":
        return settings.webhook_secret !== o.webhook_secret;
      case "system":
        return settings.log_level !== o.log_level || settings.max_workflow_depth !== o.max_workflow_depth;
    }
  }

  // -- Section save handler -------------------------------------------------

  function sectionFields(section: SectionName): Partial<EditableFields> {
    if (!settings || !originalRef.current) return {};
    const o = originalRef.current;
    switch (section) {
      case "connections":
        return diffFields({ sandstorm_url: settings.sandstorm_url }, { sandstorm_url: o.sandstorm_url });
      case "credentials":
        return diffFields(
          { anthropic_api_key: settings.anthropic_api_key, e2b_api_key: settings.e2b_api_key },
          { anthropic_api_key: o.anthropic_api_key, e2b_api_key: o.e2b_api_key }
        );
      case "security":
        return diffFields(
          { auth_required: settings.auth_required, dashboard_origin: settings.dashboard_origin },
          { auth_required: o.auth_required, dashboard_origin: o.dashboard_origin }
        );
      case "budget":
        return diffFields(
          { default_max_cost_usd: settings.default_max_cost_usd },
          { default_max_cost_usd: o.default_max_cost_usd }
        );
      case "webhooks":
        return diffFields(
          { webhook_secret: settings.webhook_secret },
          { webhook_secret: o.webhook_secret }
        );
      case "system":
        return diffFields(
          { log_level: settings.log_level, max_workflow_depth: settings.max_workflow_depth },
          { log_level: o.log_level, max_workflow_depth: o.max_workflow_depth }
        );
    }
  }

  const handleSave = useCallback(
    async (section: SectionName) => {
      const changed = sectionFields(section);
      if (Object.keys(changed).length === 0) return;

      setSavingSections((prev) => new Set(prev).add(section));
      const res = await api.patch<SettingsData>("/settings", changed);
      setSavingSections((prev) => {
        const next = new Set(prev);
        next.delete(section);
        return next;
      });

      if (res.error) {
        setToast({ message: `Failed to save: ${res.error.message}`, type: "error" });
      } else {
        // Update original snapshot so dirty state resets
        if (originalRef.current && settings) {
          originalRef.current = { ...settings };
        }
        setToast({ message: "Settings saved successfully", type: "success" });
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [settings]
  );

  // -- Connection test ------------------------------------------------------

  const handleTestConnection = useCallback(async () => {
    if (!settings) return;
    setTestingConnection(true);
    try {
      const res = await fetch(`${settings.sandstorm_url}/health`, {
        signal: AbortSignal.timeout(5000),
      });
      if (res.ok) {
        setToast({ message: "Connection to Sandshore successful", type: "success" });
      } else {
        setToast({ message: `Sandshore returned HTTP ${res.status}`, type: "error" });
      }
    } catch {
      setToast({ message: "Could not reach Sandshore at this URL", type: "error" });
    }
    setTestingConnection(false);
  }, [settings]);

  // -- Render ---------------------------------------------------------------

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (fetchError || !settings) {
    return (
      <div className="flex h-64 flex-col items-center justify-center gap-3">
        <AlertCircle className="h-8 w-8 text-error" />
        <p className="text-sm text-muted">{fetchError || "Could not load settings"}</p>
        <button
          onClick={() => { setLoading(true); void fetchSettings(); }}
          className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-foreground hover:bg-border/40 transition-colors"
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4 sm:space-y-6">
      <div className="flex items-center gap-3">
        <Settings className="h-6 w-6 text-muted" />
        <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">
          Settings
        </h1>
      </div>

      {/* Connections */}
      <SectionCard
        icon={Link}
        title="Connections"
        description="Configure the upstream Sandshore runtime URL"
      >
        <div className="space-y-3">
          <div>
            <FieldLabel htmlFor="sandstorm_url">Sandshore URL</FieldLabel>
            <div className="flex gap-2">
              <input
                id="sandstorm_url"
                type="text"
                className={inputClass}
                value={settings.sandstorm_url}
                onChange={(e) => updateField("sandstorm_url", e.target.value)}
                placeholder="http://localhost:8080"
              />
              <button
                onClick={handleTestConnection}
                disabled={testingConnection}
                className={cn(
                  "shrink-0 rounded-lg border border-border px-3 py-2 text-sm font-medium",
                  "text-muted hover:text-foreground hover:bg-border/40 transition-colors",
                  testingConnection && "opacity-50 cursor-not-allowed"
                )}
              >
                {testingConnection ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  "Test"
                )}
              </button>
            </div>
          </div>
          <div className="flex justify-end">
            <SaveButton
              dirty={isSectionDirty("connections")}
              saving={savingSections.has("connections")}
              onClick={() => void handleSave("connections")}
            />
          </div>
        </div>
      </SectionCard>

      {/* API Credentials */}
      <SectionCard
        icon={KeyRound}
        title="API Credentials"
        description="Manage API keys for external services"
      >
        <div className="space-y-4">
          <div>
            <FieldLabel htmlFor="anthropic_api_key">Anthropic API Key</FieldLabel>
            <input
              id="anthropic_api_key"
              type="password"
              className={inputClass}
              value={settings.anthropic_api_key}
              onChange={(e) => updateField("anthropic_api_key", e.target.value)}
              placeholder={originalRef.current?.anthropic_api_key || "sk-ant-..."}
            />
          </div>
          <div>
            <FieldLabel htmlFor="e2b_api_key">E2B API Key</FieldLabel>
            <input
              id="e2b_api_key"
              type="password"
              className={inputClass}
              value={settings.e2b_api_key}
              onChange={(e) => updateField("e2b_api_key", e.target.value)}
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
            <SaveButton
              dirty={isSectionDirty("credentials")}
              saving={savingSections.has("credentials")}
              onClick={() => void handleSave("credentials")}
            />
          </div>
        </div>
      </SectionCard>

      {/* Security */}
      <SectionCard
        icon={Shield}
        title="Security"
        description="Authentication and CORS settings"
      >
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-foreground">Auth Required</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                Require API key authentication for all requests
              </p>
            </div>
            <button
              role="switch"
              aria-checked={settings.auth_required}
              onClick={() => updateField("auth_required", !settings.auth_required)}
              className={cn(
                "relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors",
                settings.auth_required ? "bg-accent" : "bg-border"
              )}
            >
              <span
                className={cn(
                  "pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow-sm transition-transform",
                  settings.auth_required ? "translate-x-5" : "translate-x-0"
                )}
              />
            </button>
          </div>
          <div>
            <FieldLabel htmlFor="dashboard_origin">Dashboard Origin</FieldLabel>
            <input
              id="dashboard_origin"
              type="text"
              className={inputClass}
              value={settings.dashboard_origin}
              onChange={(e) => updateField("dashboard_origin", e.target.value)}
              placeholder="http://localhost:5173"
            />
            <HelperText>Allowed CORS origin for the dashboard</HelperText>
          </div>
          <div className="flex justify-end">
            <SaveButton
              dirty={isSectionDirty("security")}
              saving={savingSections.has("security")}
              onClick={() => void handleSave("security")}
            />
          </div>
        </div>
      </SectionCard>

      {/* Budget & Costs */}
      <SectionCard
        icon={DollarSign}
        title="Budget & Costs"
        description="Default cost limits for workflow runs"
      >
        <div className="space-y-3">
          <div>
            <FieldLabel htmlFor="default_max_cost_usd">Default Max Cost per Run (USD)</FieldLabel>
            <input
              id="default_max_cost_usd"
              type="number"
              min={0}
              step={0.01}
              className={cn(inputClass, "max-w-xs")}
              value={settings.default_max_cost_usd}
              onChange={(e) => updateField("default_max_cost_usd", parseFloat(e.target.value) || 0)}
            />
            <HelperText>0 = unlimited</HelperText>
          </div>
          <div className="flex justify-end">
            <SaveButton
              dirty={isSectionDirty("budget")}
              saving={savingSections.has("budget")}
              onClick={() => void handleSave("budget")}
            />
          </div>
        </div>
      </SectionCard>

      {/* Webhooks */}
      <SectionCard
        icon={Webhook}
        title="Webhooks"
        description="Webhook HMAC signing configuration"
      >
        <div className="space-y-3">
          <div>
            <FieldLabel htmlFor="webhook_secret">Webhook Secret</FieldLabel>
            <input
              id="webhook_secret"
              type="password"
              className={inputClass}
              value={settings.webhook_secret}
              onChange={(e) => updateField("webhook_secret", e.target.value)}
              placeholder={originalRef.current?.webhook_secret || "whsec_..."}
            />
            <HelperText>Used to sign outgoing webhook payloads (HMAC-SHA256)</HelperText>
          </div>
          <div className="flex justify-end">
            <SaveButton
              dirty={isSectionDirty("webhooks")}
              saving={savingSections.has("webhooks")}
              onClick={() => void handleSave("webhooks")}
            />
          </div>
        </div>
      </SectionCard>

      {/* System */}
      <SectionCard
        icon={Wrench}
        title="System"
        description="Logging and execution limits"
      >
        <div className="space-y-4">
          <div>
            <FieldLabel htmlFor="log_level">Log Level</FieldLabel>
            <select
              id="log_level"
              className={cn(inputClass, "max-w-xs cursor-pointer")}
              value={settings.log_level}
              onChange={(e) => updateField("log_level", e.target.value)}
            >
              {LOG_LEVELS.map((level) => (
                <option key={level} value={level}>
                  {level.charAt(0).toUpperCase() + level.slice(1)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <FieldLabel htmlFor="max_workflow_depth">Max Workflow Depth</FieldLabel>
            <input
              id="max_workflow_depth"
              type="number"
              min={1}
              max={20}
              className={cn(inputClass, "max-w-xs")}
              value={settings.max_workflow_depth}
              onChange={(e) =>
                updateField(
                  "max_workflow_depth",
                  Math.min(20, Math.max(1, parseInt(e.target.value, 10) || 1))
                )
              }
            />
            <HelperText>Maximum nesting depth for sub-workflows (1-20)</HelperText>
          </div>
          <div className="flex justify-end">
            <SaveButton
              dirty={isSectionDirty("system")}
              saving={savingSections.has("system")}
              onClick={() => void handleSave("system")}
            />
          </div>
        </div>
      </SectionCard>

      {/* API Key / Session */}
      {localStorage.getItem("sandcastle_api_key") && (
        <SectionCard
          icon={LogOut}
          title="Session"
          description="Connected via saved API key"
        >
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">
              Key: <span className="font-mono">{localStorage.getItem("sandcastle_api_key")?.slice(0, 8)}...</span>
            </p>
            <button
              onClick={() => {
                localStorage.removeItem("sandcastle_api_key");
                window.location.reload();
              }}
              className="flex items-center gap-2 rounded-lg border border-error/30 px-3 py-1.5 text-sm font-medium text-error hover:bg-error/10 transition-colors cursor-pointer"
            >
              <LogOut className="h-3.5 w-3.5" />
              Disconnect
            </button>
          </div>
        </SectionCard>
      )}

      {/* Infrastructure (read-only) */}
      <SectionCard
        icon={Server}
        title="Infrastructure"
        description="Runtime environment and storage configuration (read-only)"
      >
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-foreground">Mode</span>
            <span
              className={cn(
                "inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium",
                settings.is_local_mode
                  ? "bg-accent/15 border border-accent/30 text-accent"
                  : "bg-success/15 border border-success/30 text-success"
              )}
            >
              {settings.is_local_mode ? "Local" : "Production"}
            </span>
          </div>

          {runtimeInfo && (
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-foreground">Sandbox Backend</span>
              <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-accent/15 border border-accent/30 text-accent capitalize">
                {runtimeInfo.sandbox_backend}
              </span>
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-3">
            <div>
              <p className="text-sm font-medium text-foreground">Database</p>
              <p className="text-sm text-muted-foreground font-mono truncate">
                {settings.database_url || "Not configured"}
              </p>
            </div>
            <div>
              <p className="text-sm font-medium text-foreground">Queue</p>
              <p className="text-sm text-muted-foreground font-mono truncate">
                {settings.redis_url || "In-process"}
              </p>
            </div>
            <div>
              <p className="text-sm font-medium text-foreground">Storage</p>
              <p className="text-sm text-muted-foreground font-mono truncate">
                {settings.storage_backend}
                {settings.storage_bucket ? ` / ${settings.storage_bucket}` : ""}
                {settings.storage_endpoint ? ` (${settings.storage_endpoint})` : ""}
              </p>
            </div>
            <div>
              <p className="text-sm font-medium text-foreground">Data Directory</p>
              <p className="text-sm text-muted-foreground font-mono truncate">
                {settings.data_dir || "-"}
              </p>
            </div>
            <div>
              <p className="text-sm font-medium text-foreground">Workflows Directory</p>
              <p className="text-sm text-muted-foreground font-mono truncate">
                {settings.workflows_dir || "-"}
              </p>
            </div>
          </div>
        </div>
      </SectionCard>

      {/* Toast notification */}
      {toast && <Toast message={toast.message} type={toast.type} />}
    </div>
  );
}
