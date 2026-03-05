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
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { SectionCard, FieldLabel, HelperText } from "@/components/ui/SectionCard";
import { cn, inputClass } from "@/lib/utils";
import { useRuntimeInfo } from "@/hooks/useRuntimeInfo";
import { useUpdateCheck } from "@/hooks/useUpdateCheck";
import { useNotifications } from "@/hooks/useNotifications";
import { useAccentColor, ACCENT_COLORS } from "@/hooks/useAccentColor";
import { useTheme } from "@/hooks/useTheme";

// -- Types ------------------------------------------------------------------

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

  // -- Connection test ------------------------------------------------------


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
              {update.releaseUrl && (
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

    </div>
  );
}
