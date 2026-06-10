import {
  Shield,
  Webhook,
  Wrench,
  Server,
  Database,
  HardDrive,
  Container,
  BarChart3,
  Trash2,
} from "lucide-react";
import { SectionCard, FieldLabel, HelperText } from "@/components/ui/SectionCard";
import { cn, inputClass } from "@/lib/utils";
import { useRuntimeInfo } from "@/hooks/useRuntimeInfo";
import { useUsageStats } from "@/hooks/usePageTracking";
import { ErrorBoundary } from "@/components/shared/ErrorBoundary";
import { SaveButton, BackendCard } from "./SettingsShared";
import { LOG_LEVELS } from "./settingsHelpers";
import { useSettingsContext } from "./settingsContext";

/**
 * Advanced tab — the "complexity at the bottom": runtime/infrastructure
 * backends, logging and execution limits, security (env-only), webhook signing,
 * database wiring, and local usage analytics. Tucked away from arrival so the
 * everyday General tab stays short.
 */
export default function AdvancedPanel() {
  const { settings, updateField, isSectionDirty, savingSections, handleSave } = useSettingsContext();
  const { info: runtimeInfo } = useRuntimeInfo();
  const { topPages, totalViews, clearStats } = useUsageStats();

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
    <div className="space-y-4 sm:space-y-6">
      {/* Lead-in note so the Advanced tab reads as the power-user zone */}
      <p className="text-sm text-muted-foreground">
        Power-user configuration. Most of these are set via environment variables and
        require a restart to take effect.
      </p>

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
                settings.auth_required ? "bg-accent" : "bg-border",
              )}
            >
              <span
                className={cn(
                  "pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow-sm transition-transform motion-reduce:transition-none",
                  settings.auth_required ? "translate-x-5" : "translate-x-0",
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
                  Math.min(20, Math.max(1, parseInt(e.target.value, 10) || 1)),
                )
              }
            />
            <HelperText>Maximum nesting depth for sub-workflows (1-20)</HelperText>
          </div>
          <div className="flex justify-end">
            <SaveButton
              dirty={isSectionDirty("system")}
              saving={savingSections.has("system")}
              onClick={() => handleSave("system")}
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

      {/* Infrastructure Backends */}
      <ErrorBoundary name="SettingsPage-Infrastructure">
        <SectionCard
          icon={Server}
          title="Infrastructure"
          description="Runtime backends and storage. Changes require a restart and are applied via environment variables."
        >
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
        </SectionCard>
      </ErrorBoundary>

      {/* Usage Stats - local analytics */}
      <SectionCard
        icon={BarChart3}
        title="Usage Stats"
        description="Local page view analytics - no data leaves your browser"
        readOnly
      >
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium text-foreground">
              Total Page Views
            </p>
            <span className="text-sm font-mono text-muted-foreground">
              {totalViews.toLocaleString()}
            </span>
          </div>

          {topPages.length > 0 ? (
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-2">
                Most Visited Pages
              </p>
              <div className="space-y-1.5">
                {topPages.map((stat, i) => {
                  const pct = totalViews > 0 ? (stat.count / totalViews) * 100 : 0;
                  return (
                    <div key={stat.page} className="flex items-center gap-2">
                      <span className="text-xs text-muted-foreground w-4 text-right shrink-0">
                        {i + 1}.
                      </span>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between mb-0.5">
                          <span className="text-xs font-mono text-foreground truncate">
                            {stat.page}
                          </span>
                          <span className="text-[10px] text-muted-foreground tabular-nums shrink-0 ml-2">
                            {stat.count} ({pct.toFixed(1)}%)
                          </span>
                        </div>
                        <div className="h-1 rounded-full bg-border/50 overflow-hidden">
                          <div
                            className="h-full rounded-full bg-accent/60 transition-all motion-reduce:transition-none"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              No page views recorded yet. Navigate around to start tracking.
            </p>
          )}

          {totalViews > 0 && (
            <div className="pt-2 border-t border-border/50">
              <button
                onClick={clearStats}
                className={cn(
                  "flex items-center gap-1.5 text-xs text-muted-foreground",
                  "hover:text-destructive transition-colors cursor-pointer",
                )}
              >
                <Trash2 className="h-3 w-3" />
                Clear analytics
              </button>
            </div>
          )}
        </div>
      </SectionCard>
    </div>
  );
}
