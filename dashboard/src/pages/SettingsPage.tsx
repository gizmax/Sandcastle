import { useCallback, useEffect, useRef, useState } from "react";
import {
  Settings,
  Shield,
  DollarSign,
  Webhook,
  Wrench,
  Server,
  Loader2,
  AlertCircle,
  LogOut,
  BadgeCheck,
  ArrowUpCircle,
  CheckCircle2,
  Copy,
  ExternalLink,
  Bell,
  Palette,
  Check,
  Moon,
  Sun,
  Cpu,
  X,
  Wifi,
  ChevronDown,
  Database,
  HardDrive,
  Container,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { SectionCard, FieldLabel, HelperText } from "@/components/ui/SectionCard";
import { Link } from "react-router-dom";
import { cn, inputClass, isSafeUrl } from "@/lib/utils";
import { useRuntimeInfo } from "@/hooks/useRuntimeInfo";
import { useUpdateCheck } from "@/hooks/useUpdateCheck";
import { useNotifications } from "@/hooks/useNotifications";
import { useAccentColor, ACCENT_COLORS } from "@/hooks/useAccentColor";
import { useTheme } from "@/hooks/useTheme";

// -- Types ------------------------------------------------------------------

interface ProviderEntry {
  id: string;
  name: string;
  region: string;
  configured: boolean;
  status: string;
}

interface AdvisorStatus {
  current_provider: string;
  current_model: string | null;
  data_residency: string | null;
  available_providers: ProviderEntry[];
}

interface SettingsData {
  anthropic_api_key: string;
  e2b_api_key: string;
  openai_api_key: string;
  minimax_api_key: string;
  openrouter_api_key: string;
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
  | "security"
  | "budget"
  | "webhooks"
  | "system";

// -- BackendCard component --------------------------------------------------

interface BackendOption {
  id: string;
  label: string;
  desc: string;
  envHint: string;
}

function BackendCard({
  icon: Icon,
  label,
  current,
  options,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  current: string;
  options: BackendOption[];
}) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);

  const handleCopy = (text: string, optId: string) => {
    void navigator.clipboard.writeText(text);
    setCopied(optId);
    setTimeout(() => setCopied(null), 2000);
  };

  return (
    <div className="rounded-lg border border-border/70 bg-surface/50">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-3 px-4 py-3 cursor-pointer"
      >
        <Icon className="h-4 w-4 text-muted-foreground shrink-0" />
        <span className="text-sm font-medium text-foreground">{label}</span>
        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-accent/15 border border-accent/30 text-accent capitalize">
          {current}
        </span>
        <ChevronDown
          className={cn(
            "ml-auto h-3.5 w-3.5 text-muted-foreground transition-transform duration-200",
            expanded && "rotate-180"
          )}
        />
      </button>
      {expanded && (
        <div className="px-4 pb-4 pt-1 border-t border-border/50">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {options.map((opt) => {
              const isActive = opt.id === current;
              return (
                <div
                  key={opt.id}
                  className={cn(
                    "rounded-lg border p-3 transition-all",
                    isActive
                      ? "border-accent bg-accent/5"
                      : "border-border/50 bg-surface/30"
                  )}
                >
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-foreground">{opt.label}</span>
                    {isActive && (
                      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-bold bg-accent/20 text-accent uppercase tracking-wider">
                        Active
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-muted-foreground mt-0.5">{opt.desc}</p>
                  {!isActive && (
                    <div className="mt-2">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[10px] text-muted-foreground">Add to .env:</span>
                        <button
                          onClick={() => handleCopy(opt.envHint, opt.id)}
                          className="flex items-center gap-1 text-[10px] text-accent hover:text-accent-hover transition-colors cursor-pointer"
                        >
                          {copied === opt.id ? (
                            <><Check className="h-2.5 w-2.5" /> Copied</>
                          ) : (
                            <><Copy className="h-2.5 w-2.5" /> Copy</>
                          )}
                        </button>
                      </div>
                      <pre className="text-[10px] font-mono text-muted-foreground/70 bg-border/20 rounded px-2 py-1.5 whitespace-pre-wrap break-all">
                        {opt.envHint}
                      </pre>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          <p className="text-[10px] text-muted-foreground/60 mt-2">
            Changes require restarting Sandcastle to take effect.
          </p>
        </div>
      )}
    </div>
  );
}

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

// -- Main component ---------------------------------------------------------

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [savingSections, setSavingSections] = useState<Set<SectionName>>(new Set());
  const { info: runtimeInfo } = useRuntimeInfo();
  const update = useUpdateCheck();
  const {
    permission: notifPermission,
    notificationsEnabled,
    requestPermission,
    toggleNotifications,
  } = useNotifications();
  const [copied, setCopied] = useState(false);
  const { accentColor, setAccentColor } = useAccentColor();
  const { theme, toggleTheme } = useTheme();

  // Advisor provider state
  const [advisorStatus, setAdvisorStatus] = useState<AdvisorStatus | null>(null);
  const [selectedProvider, setSelectedProvider] = useState<string>("");
  const [advisorModel, setAdvisorModel] = useState<string>("");
  const [euMode, setEuMode] = useState<boolean>(false);
  const [savingAdvisor, setSavingAdvisor] = useState(false);

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

  const fetchAdvisorStatus = useCallback(async () => {
    const res = await api.get<AdvisorStatus>("/advisor/status");
    if (res.data) {
      setAdvisorStatus(res.data);
      setSelectedProvider(res.data.current_provider);
      setAdvisorModel(res.data.current_model || "");
      setEuMode(res.data.data_residency === "eu");
    }
  }, []);

  useEffect(() => {
    void fetchSettings();
    void fetchAdvisorStatus();
  }, [fetchSettings, fetchAdvisorStatus]);

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
        return false;
      case "security":
        return false;  // All security fields are immutable (env vars only)
      case "budget":
        return settings.default_max_cost_usd !== o.default_max_cost_usd;
      case "webhooks":
        return false;  // webhook_secret is immutable (env var only)
      case "system":
        return settings.log_level !== o.log_level || settings.max_workflow_depth !== o.max_workflow_depth;
    }
  }

  // -- Section save handler -------------------------------------------------

  const handleSave = useCallback(
    async (section: SectionName) => {
      if (!settings || !originalRef.current) return;
      const o = originalRef.current;

      // Compute changed fields inline to avoid stale closure on sectionFields
      let changed: Partial<EditableFields> = {};
      switch (section) {
        case "budget":
          changed = diffFields(
            { default_max_cost_usd: settings.default_max_cost_usd },
            { default_max_cost_usd: o.default_max_cost_usd }
          );
          break;
        case "system":
          changed = diffFields(
            { log_level: settings.log_level, max_workflow_depth: settings.max_workflow_depth },
            { log_level: o.log_level, max_workflow_depth: o.max_workflow_depth }
          );
          break;
        default:
          return;
      }
      if (Object.keys(changed).length === 0) return;

      setSavingSections((prev) => new Set(prev).add(section));
      const res = await api.patch<SettingsData>("/settings", changed);
      setSavingSections((prev) => {
        const next = new Set(prev);
        next.delete(section);
        return next;
      });

      if (res.error) {
        toast.error(`Failed to save: ${res.error.message}`);
      } else {
        // Update original snapshot so dirty state resets
        if (originalRef.current && settings) {
          originalRef.current = { ...settings };
        }
        toast.success("Settings saved");
      }
    },
    [settings]
  );

  // -- Advisor save handler -------------------------------------------------

  const handleSaveAdvisor = useCallback(async () => {
    setSavingAdvisor(true);
    const res = await api.post("/advisor/configure", {
      provider: selectedProvider,
      model: advisorModel || null,
      data_residency: euMode ? "eu" : null,
    });
    setSavingAdvisor(false);
    if (res.error) {
      toast.error(`Failed to save: ${res.error.message}`);
    } else {
      toast.success("AI Provider saved");
    }
  }, [selectedProvider, advisorModel, euMode]);

  // -- Connection test -------------------------------------------------------

  type TestState = "idle" | "testing" | "ok" | "error";
  const [testStates, setTestStates] = useState<Record<string, TestState>>({});
  const [testMessages, setTestMessages] = useState<Record<string, string>>({});

  const handleTestConnection = useCallback(async (providerId: string) => {
    setTestStates((prev) => ({ ...prev, [providerId]: "testing" }));
    setTestMessages((prev) => ({ ...prev, [providerId]: "" }));
    const res = await api.post<{ status: string; latency_ms?: number; message?: string }>(
      "/advisor/test-connection",
      { provider: providerId }
    );
    if (res.error) {
      setTestStates((prev) => ({ ...prev, [providerId]: "error" }));
      setTestMessages((prev) => ({ ...prev, [providerId]: res.error!.message }));
    } else if (res.data?.status === "ok") {
      setTestStates((prev) => ({ ...prev, [providerId]: "ok" }));
      setTestMessages((prev) => ({ ...prev, [providerId]: `${res.data!.latency_ms ?? "?"}ms` }));
      setTimeout(() => setTestStates((prev) => ({ ...prev, [providerId]: "idle" })), 3000);
    } else {
      setTestStates((prev) => ({ ...prev, [providerId]: "error" }));
      setTestMessages((prev) => ({ ...prev, [providerId]: res.data?.message ?? "Connection failed" }));
    }
  }, []);

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

  const REGION_LABEL: Record<string, string> = {
    us: "US",
    eu: "EU",
    local: "Local",
  };

  const PROVIDER_STATUS_LABEL: Record<string, string> = {
    ok: "Configured",
    unconfigured: "Not configured",
    running: "Running",
    not_detected: "Not detected",
  };

  const advisorDirty =
    advisorStatus !== null &&
    (selectedProvider !== advisorStatus.current_provider ||
      (advisorModel || "") !== (advisorStatus.current_model || "") ||
      euMode !== (advisorStatus.data_residency === "eu"));

  return (
    <div className="space-y-4 sm:space-y-6">
      <div className="flex items-center gap-3">
        <Settings className="h-6 w-6 text-muted" />
        <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">
          Settings
        </h1>
      </div>

      {/* AI Provider */}
      <SectionCard
        icon={Cpu}
        title="AI Provider"
        description="Choose which LLM powers workflow generation, evolution, and quality evaluation."
      >
        <div className="space-y-4">
          {advisorStatus === null ? (
            <LoadingSpinner size="sm" />
          ) : (
            <>
              {/* Provider cards */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                {advisorStatus.available_providers.map((p) => {
                  const ts = testStates[p.id] ?? "idle";
                  const tm = testMessages[p.id] ?? "";
                  return (
                    <div
                      key={p.id}
                      className={cn(
                        "rounded-lg border p-4 text-center transition-all",
                        selectedProvider === p.id
                          ? "border-accent bg-accent/10"
                          : "border-border hover:border-accent/50"
                      )}
                    >
                      <button
                        className="w-full cursor-pointer"
                        onClick={() => setSelectedProvider(p.id)}
                      >
                        <div className="text-sm font-semibold text-foreground">{p.name}</div>
                        <div className="text-xs text-muted mt-0.5">{REGION_LABEL[p.region] ?? p.region}</div>
                        <div
                          className={cn(
                            "text-xs mt-1.5 font-medium",
                            p.status === "ok" || p.status === "running"
                              ? "text-success"
                              : "text-muted-foreground"
                          )}
                        >
                          {PROVIDER_STATUS_LABEL[p.status] ?? p.status}
                        </div>
                      </button>
                      {p.configured ? (
                        <div className="mt-1 flex items-center justify-center gap-1 text-[10px] text-success">
                          <CheckCircle2 className="h-2.5 w-2.5" />
                          Key set
                        </div>
                      ) : (
                        <Link
                          to="/api-keys"
                          className="mt-1 flex items-center justify-center gap-1 text-[10px] text-accent hover:text-accent-hover transition-colors"
                        >
                          Set API key &rarr;
                        </Link>
                      )}
                      <button
                        onClick={(e) => { e.stopPropagation(); void handleTestConnection(p.id); }}
                        disabled={ts === "testing"}
                        className={cn(
                          "mt-2 flex w-full items-center justify-center gap-1 rounded px-2 py-0.5 text-[10px] font-medium transition-colors cursor-pointer",
                          ts === "ok" ? "bg-success/15 text-success" :
                          ts === "error" ? "bg-error/15 text-error" :
                          "bg-border/60 text-muted-foreground hover:bg-border"
                        )}
                        title={tm || "Test connection"}
                      >
                        {ts === "testing" && <Loader2 className="h-2.5 w-2.5 animate-spin" />}
                        {ts === "ok" && <CheckCircle2 className="h-2.5 w-2.5" />}
                        {ts === "error" && <X className="h-2.5 w-2.5" />}
                        {ts === "idle" && <Wifi className="h-2.5 w-2.5" />}
                        {ts === "ok" ? tm : ts === "error" ? "Failed" : ts === "testing" ? "Testing..." : "Test"}
                      </button>
                    </div>
                  );
                })}
              </div>

              {/* Model override */}
              <div>
                <FieldLabel htmlFor="advisor_model">Model (optional override)</FieldLabel>
                <input
                  id="advisor_model"
                  type="text"
                  className={cn(inputClass, "max-w-sm")}
                  value={advisorModel}
                  onChange={(e) => setAdvisorModel(e.target.value)}
                  placeholder="e.g. mistral-large-latest"
                />
                <HelperText>Leave empty to use the provider default model</HelperText>
              </div>

              {/* Quality routing indicator */}
              {!advisorModel && (
                <div className="rounded-lg border border-border bg-muted/30 px-3 py-2.5">
                  <p className="text-xs font-medium text-foreground mb-1">Quality Routing: Auto</p>
                  <p className="text-xs text-muted-foreground leading-relaxed">
                    generation=<span className="text-success font-medium">high</span>
                    {" - "}chat=<span className="text-success font-medium">high</span>
                    {" - "}explain=<span className="text-warning font-medium">medium</span>
                    {" - "}evolution=<span className="text-warning font-medium">medium</span>
                    {" - "}judge=<span className="text-muted-foreground font-medium">low</span>
                  </p>
                  <p className="text-xs text-muted-foreground mt-1">
                    High-stakes operations use the best model; cheap operations use the cheapest
                    model that meets the quality bar. Set a model override above to disable.
                  </p>
                </div>
              )}
              {advisorModel && (
                <div className="rounded-lg border border-accent/30 bg-accent/5 px-3 py-2.5">
                  <p className="text-xs font-medium text-foreground mb-0.5">Quality Routing: Disabled</p>
                  <p className="text-xs text-muted-foreground">
                    All advisor calls use <span className="font-mono text-accent">{advisorModel}</span> regardless of purpose.
                    Clear the override above to re-enable automatic quality routing.
                  </p>
                </div>
              )}

              {/* EU data residency toggle */}
              <div className="flex items-center gap-3">
                <input
                  id="eu_mode"
                  type="checkbox"
                  className="h-4 w-4 rounded border-border accent-[color:var(--color-accent)] cursor-pointer"
                  checked={euMode}
                  onChange={(e) => setEuMode(e.target.checked)}
                />
                <label htmlFor="eu_mode" className="text-sm text-foreground cursor-pointer select-none">
                  EU Data Residency - only use EU-based providers
                </label>
              </div>

              <div className="flex justify-end">
                <SaveButton
                  dirty={advisorDirty}
                  saving={savingAdvisor}
                  onClick={() => void handleSaveAdvisor()}
                />
              </div>
            </>
          )}
        </div>
      </SectionCard>

      {/* Software Update */}
      {!update.loading && update.currentVersion && (
        <SectionCard
          icon={update.updateAvailable ? ArrowUpCircle : CheckCircle2}
          title="Software Update"
          description="Check for new Sandcastle versions"
        >
          {update.updateAvailable ? (
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-warning/15 border border-warning/30 text-warning">
                  <ArrowUpCircle className="h-3 w-3" />
                  v{update.latestVersion} available
                </span>
                <span className="text-xs text-muted-foreground">
                  Current: v{update.currentVersion}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <code className="flex-1 overflow-x-auto rounded-lg bg-muted/50 border border-border px-3 py-2 text-xs sm:text-sm font-mono text-foreground">
                  {update.installCommand}
                </code>
                <button
                  onClick={() => {
                    void navigator.clipboard.writeText(update.installCommand!);
                    setCopied(true);
                    setTimeout(() => setCopied(false), 2000);
                  }}
                  className="rounded-lg border border-border p-2 text-muted hover:text-foreground hover:bg-border/40 transition-colors cursor-pointer"
                  title="Copy command"
                >
                  {copied ? <CheckCircle2 className="h-4 w-4 text-success" /> : <Copy className="h-4 w-4" />}
                </button>
              </div>
              {update.releaseUrl && isSafeUrl(update.releaseUrl) && (
                <a
                  href={update.releaseUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 text-sm text-accent hover:underline"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  View release notes
                </a>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <CheckCircle2 className="h-4 w-4 text-success" />
              <span className="text-sm text-foreground">
                v{update.currentVersion} - You're up to date
              </span>
            </div>
          )}
        </SectionCard>
      )}

      {/* Notifications */}
      <SectionCard
        icon={Bell}
        title="Notifications"
        description="Get notified when workflows complete or fail"
      >
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-foreground">
                Browser notifications for run completion
              </p>
              <p className="text-xs text-muted-foreground mt-0.5">
                Permission:{" "}
                <span
                  className={cn(
                    "font-medium",
                    notifPermission === "granted"
                      ? "text-success"
                      : notifPermission === "denied"
                        ? "text-error"
                        : "text-muted-foreground",
                  )}
                >
                  {notifPermission === "granted"
                    ? "Allowed"
                    : notifPermission === "denied"
                      ? "Blocked"
                      : "Not requested"}
                </span>
              </p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={notificationsEnabled}
              onClick={toggleNotifications}
              disabled={notifPermission !== "granted"}
              className={cn(
                "relative inline-flex h-6 w-11 shrink-0 rounded-full border-2 border-transparent transition-colors cursor-pointer",
                notificationsEnabled && notifPermission === "granted"
                  ? "bg-accent"
                  : "bg-border",
                notifPermission !== "granted" && "opacity-60 cursor-not-allowed",
              )}
            >
              <span
                className={cn(
                  "pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow-sm transition-transform",
                  notificationsEnabled && notifPermission === "granted"
                    ? "translate-x-5"
                    : "translate-x-0",
                )}
              />
            </button>
          </div>
          {notifPermission === "default" && (
            <button
              onClick={() => void requestPermission()}
              className={cn(
                "flex items-center gap-2 rounded-lg border border-accent/30 px-3 py-1.5",
                "text-sm font-medium text-accent",
                "hover:bg-accent/10 transition-colors cursor-pointer",
              )}
            >
              <Bell className="h-4 w-4" />
              Request Permission
            </button>
          )}
          {notifPermission === "denied" && (
            <p className="text-xs text-muted-foreground">
              Browser notifications are blocked. Update your browser settings to enable them.
            </p>
          )}
        </div>
      </SectionCard>

      {/* License */}
      <SectionCard
        icon={BadgeCheck}
        title="License"
        description="Sandcastle license status and tier"
      >
        {runtimeInfo?.license ? (
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <span className="text-sm font-medium text-foreground">Status</span>
              <span
                className={cn(
                  "inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium",
                  runtimeInfo.license.status === "valid"
                    ? "bg-success/15 border border-success/30 text-success"
                    : runtimeInfo.license.status === "expired"
                      ? "bg-warning/15 border border-warning/30 text-warning"
                      : runtimeInfo.license.status === "missing"
                        ? "bg-muted/15 border border-border text-muted-foreground"
                        : "bg-error/15 border border-error/30 text-error"
                )}
              >
                {runtimeInfo.license.status === "valid"
                  ? `${runtimeInfo.license.tier.charAt(0).toUpperCase() + runtimeInfo.license.tier.slice(1)} License`
                  : runtimeInfo.license.status === "missing"
                    ? "Community Mode"
                    : runtimeInfo.license.status.charAt(0).toUpperCase() + runtimeInfo.license.status.slice(1)}
              </span>
            </div>
            {runtimeInfo.license.status === "valid" && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-3">
                <div>
                  <p className="text-sm font-medium text-foreground">Licensee</p>
                  <p className="text-sm text-muted-foreground">{runtimeInfo.license.licensee || "-"}</p>
                </div>
                <div>
                  <p className="text-sm font-medium text-foreground">Expires</p>
                  <p className="text-sm text-muted-foreground">{runtimeInfo.license.expires || "Never"}</p>
                </div>
                <div>
                  <p className="text-sm font-medium text-foreground">Max Seats</p>
                  <p className="text-sm text-muted-foreground">{runtimeInfo.license.max_seats || "Unlimited"}</p>
                </div>
              </div>
            )}
            {runtimeInfo.license.status === "missing" && (
              <p className="text-sm text-muted-foreground">
                All features are available. Set <code className="text-xs bg-muted px-1 rounded">LICENSE_KEY</code> in your .env for production use.{" "}
                <a
                  href="mailto:tom@pflanzer.cz"
                  className="text-accent hover:underline"
                >
                  Contact sales
                </a>
              </p>
            )}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">Loading license info...</p>
        )}
      </SectionCard>

      {/* Appearance */}
      <SectionCard
        icon={Palette}
        title="Appearance"
        description="Theme and accent color preferences"
      >
        <div className="space-y-5">
          {/* Theme toggle */}
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-foreground">Theme</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                Switch between light and dark mode
              </p>
            </div>
            <button
              onClick={toggleTheme}
              className={cn(
                "flex items-center gap-2 rounded-lg border border-border px-3 py-1.5",
                "text-sm font-medium text-foreground hover:bg-border/40 transition-colors cursor-pointer",
              )}
            >
              {theme === "dark" ? (
                <><Moon className="h-4 w-4" /> Dark</>
              ) : (
                <><Sun className="h-4 w-4" /> Light</>
              )}
            </button>
          </div>

          {/* Accent color palette */}
          <div>
            <p className="text-sm font-medium text-foreground mb-3">Accent Color</p>
            <div className="flex flex-wrap gap-3">
              {ACCENT_COLORS.map((color) => {
                const isSelected = accentColor === color.id;
                return (
                  <button
                    key={color.id}
                    onClick={() => setAccentColor(color.id)}
                    className="group relative flex h-8 w-8 items-center justify-center rounded-full transition-transform duration-150 hover:scale-110 cursor-pointer"
                    style={{
                      backgroundColor: theme === "dark" ? color.darkAccent : color.accent,
                      boxShadow: isSelected
                        ? `0 0 0 2px var(--color-background), 0 0 0 4px ${theme === "dark" ? color.darkAccent : color.accent}`
                        : undefined,
                    }}
                    title={color.label}
                    aria-label={`Set accent color to ${color.label}`}
                  >
                    {isSelected && (
                      <Check
                        className="h-4 w-4"
                        style={{
                          color: theme === "dark" ? color.darkAccentForeground : color.accentForeground,
                        }}
                        strokeWidth={3}
                      />
                    )}
                  </button>
                );
              })}
            </div>
            <div className="flex items-center gap-2 mt-2">
              <p className="text-xs text-muted-foreground">
                {ACCENT_COLORS.find((c) => c.id === accentColor)?.label ?? "Amber"}
                {accentColor === "amber" ? " (default)" : ""}
              </p>
              {accentColor !== "amber" && (
                <button
                  onClick={() => setAccentColor("amber")}
                  className="text-xs text-accent hover:underline cursor-pointer"
                >
                  Reset to default
                </button>
              )}
            </div>
          </div>
        </div>
      </SectionCard>

      {/* Security */}
      <SectionCard
        icon={Shield}
        title="Security"
        description="Authentication and CORS settings"
        readOnly
      >
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-foreground">Auth Required</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                Set via <code className="text-xs bg-muted px-1 rounded">AUTH_REQUIRED</code> environment variable
              </p>
            </div>
            <div
              className={cn(
                "relative inline-flex h-6 w-11 shrink-0 rounded-full border-2 border-transparent opacity-60 cursor-not-allowed",
                settings.auth_required ? "bg-accent" : "bg-border"
              )}
            >
              <span
                className={cn(
                  "pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow-sm transition-transform",
                  settings.auth_required ? "translate-x-5" : "translate-x-0"
                )}
              />
            </div>
          </div>
          <div>
            <FieldLabel htmlFor="dashboard_origin">Dashboard Origin</FieldLabel>
            <input
              id="dashboard_origin"
              type="text"
              className={cn(inputClass, "opacity-60 cursor-not-allowed")}
              value={settings.dashboard_origin}
              readOnly
              placeholder="http://localhost:5173"
            />
            <HelperText>Set via <code className="text-xs bg-muted px-1 rounded">DASHBOARD_ORIGIN</code> env var (CORS origins are built at startup)</HelperText>
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
        readOnly
      >
        <div className="space-y-3">
          <div>
            <FieldLabel htmlFor="webhook_secret">Webhook Secret</FieldLabel>
            <input
              id="webhook_secret"
              type="password"
              className={cn(inputClass, "opacity-60 cursor-not-allowed")}
              value={settings.webhook_secret}
              readOnly
            />
            <HelperText>Set via <code className="text-xs bg-muted px-1 rounded">WEBHOOK_SECRET</code> env var (immutable at runtime)</HelperText>
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
      {api.hasStoredKey() && (
        <SectionCard
          icon={LogOut}
          title="Session"
          description="Connected via saved API key"
        >
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">
              Key: <span className="font-mono">{api.storedKeyPrefix()}...</span>
            </p>
            <button
              onClick={() => {
                api.clearStoredKey();
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

      {/* Infrastructure Backends */}
      <SectionCard
        icon={Server}
        title="Infrastructure"
        description="Runtime backends and storage. Changes require a restart and are applied via environment variables."
      >
        {(() => {
          const currentSandbox = runtimeInfo?.sandbox_backend || "e2b";
          const currentStorage = settings.storage_backend || "local";
          const currentDb = settings.database_url ? "postgresql" : "sqlite";
          const currentQueue = settings.redis_url ? "redis" : "in-process";

          const backends = [
            {
              id: "sandbox",
              label: "Sandbox",
              icon: Container,
              current: currentSandbox,
              options: [
                { id: "e2b", label: "E2B", desc: "Managed cloud sandbox - isolated, auto-scaling, no infra to maintain", envHint: "E2B_API_KEY=...\nSANDBOX_BACKEND=e2b" },
                { id: "docker", label: "Docker", desc: "Your own containers - full control, runs on your infra", envHint: "SANDBOX_BACKEND=docker\nDOCKER_IMAGE=sandcastle-runner:latest" },
                { id: "local", label: "Local", desc: "Runs code directly on the host machine - dev/testing only, no isolation", envHint: "SANDBOX_BACKEND=local" },
                { id: "cloudflare", label: "Cloudflare Workers", desc: "Edge execution - low latency, globally distributed, pay-per-request", envHint: "SANDBOX_BACKEND=cloudflare\nCLOUDFLARE_WORKER_URL=https://..." },
              ],
            },
            {
              id: "storage",
              label: "Storage",
              icon: HardDrive,
              current: currentStorage,
              options: [
                { id: "local", label: "Local Filesystem", desc: "Files stored on disk - simple, no external deps, single machine only", envHint: "STORAGE_BACKEND=local" },
                { id: "s3", label: "S3 / MinIO", desc: "Object storage - scalable, shared across instances (AWS S3, MinIO, Cloudflare R2)", envHint: "STORAGE_BACKEND=s3\nSTORAGE_BUCKET=sandcastle-data\nAWS_ACCESS_KEY_ID=...\nAWS_SECRET_ACCESS_KEY=..." },
              ],
            },
            {
              id: "database",
              label: "Database",
              icon: Database,
              current: currentDb,
              options: [
                { id: "sqlite", label: "SQLite", desc: "Embedded database - zero setup, single file, good for single instance", envHint: "# Remove DATABASE_URL to use SQLite" },
                { id: "postgresql", label: "PostgreSQL", desc: "Production database - concurrent access, backups, multi-worker support", envHint: "DATABASE_URL=postgresql://user:pass@host:5432/sandcastle" },
              ],
            },
            {
              id: "queue",
              label: "Queue",
              icon: Server,
              current: currentQueue,
              options: [
                { id: "in-process", label: "In-process", desc: "Queue runs inside Sandcastle - zero config, but jobs are lost on restart", envHint: "# Remove REDIS_URL to use in-process queue" },
                { id: "redis", label: "Redis", desc: "External queue - jobs survive restarts, supports multiple Sandcastle workers", envHint: "REDIS_URL=redis://localhost:6379" },
              ],
            },
          ];

          return (
            <div className="space-y-3">
              {backends.map((backend) => (
                <BackendCard
                  key={backend.id}
                  icon={backend.icon}
                  label={backend.label}
                  current={backend.current}
                  options={backend.options}
                />
              ))}
              <div className="pt-2 border-t border-border/50">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-2">
                  <div>
                    <p className="text-xs font-medium text-muted-foreground">Data Directory</p>
                    <p className="text-xs text-muted-foreground/70 font-mono truncate">{settings.data_dir || "-"}</p>
                  </div>
                  <div>
                    <p className="text-xs font-medium text-muted-foreground">Workflows Directory</p>
                    <p className="text-xs text-muted-foreground/70 font-mono truncate">{settings.workflows_dir || "-"}</p>
                  </div>
                </div>
              </div>
            </div>
          );
        })()}
      </SectionCard>

    </div>
  );
}
