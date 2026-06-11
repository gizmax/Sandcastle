import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  CheckCircle2,
  Server,
  Database,
  Radio,
  Container,
  HardDrive,
  RefreshCw,
} from "lucide-react";
import { api } from "@/api/client";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusLed } from "@/components/ui/StatusLed";
import { getLedConfig } from "@/lib/statusLed";
import { Odometer } from "@/components/ui/Odometer";
import { cn } from "@/lib/utils";

// -- Types ------------------------------------------------------------------

interface HealthData {
  status: "ok" | "degraded";
  runtime: boolean;
  redis: boolean | null;
  database: boolean;
}

interface RuntimeData {
  mode: "local" | "production";
  database: string;
  queue: string;
  storage: string;
  sandbox_backend: string;
  data_dir: string | null;
  version: string | null;
}

interface StatsData {
  total_runs_today: number;
  success_rate: number;
  total_cost_today: number;
  avg_duration_seconds: number;
}

interface QuickStats {
  workflows: number;
  runs: number;
  templates: number;
  apiKeys: number;
}

type ServiceStatus = "healthy" | "unhealthy" | "degraded" | "unconfigured";

interface ServiceCheck {
  name: string;
  status: ServiceStatus;
  detail: string;
  icon: React.ElementType;
}

// -- Helpers ----------------------------------------------------------------

function statusLabel(status: ServiceStatus): string {
  switch (status) {
    case "healthy":
      return "Healthy";
    case "unhealthy":
      return "Unhealthy";
    case "degraded":
      return "Degraded";
    case "unconfigured":
      return "Not Configured";
  }
}

function formatUptime(startedAt: Date, currentTime?: number): string {
  const diffMs = (currentTime ?? Date.now()) - startedAt.getTime();
  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m`;
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h`;
}

// -- Component --------------------------------------------------------------

export default function SystemHealthPage() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [runtime, setRuntime] = useState<RuntimeData | null>(null);
  const [quickStats, setQuickStats] = useState<QuickStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [lastChecked, setLastChecked] = useState<Date>(new Date());

  // Use page load time as a rough "started_at" for uptime display
  const [pageLoadTime] = useState(() => new Date());
  const [now, setNow] = useState(Date.now());

  const fetchData = useCallback(async (isRefresh = false, cancelled?: { current: boolean }) => {
    if (isRefresh) setRefreshing(true);
    setError(null);
    try {
      const [healthRes, runtimeRes, statsRes] =
        await Promise.all([
          api.get<HealthData>("/health"),
          api.get<RuntimeData>("/runtime"),
          api.get<StatsData>("/stats"),
        ]);

      if (cancelled?.current) return;
      if (healthRes.data) setHealth(healthRes.data);
      if (runtimeRes.data) setRuntime(runtimeRes.data);

      const runs = (statsRes.data as StatsData | null)?.total_runs_today ?? 0;

      const [workflowsRes, templatesRes, keysRes] = await Promise.all([
        api.get<{ name: string }[]>("/workflows"),
        api.get<{ id: string }[]>("/templates"),
        api.get<{ id: string }[]>("/api-keys"),
      ]);

      if (cancelled?.current) return;
      const workflows = Array.isArray(workflowsRes.data) ? workflowsRes.data.length : 0;
      const templates = Array.isArray(templatesRes.data) ? templatesRes.data.length : 0;
      const apiKeys = Array.isArray(keysRes.data) ? keysRes.data.length : 0;

      setQuickStats({ runs, workflows, templates, apiKeys });
      setLastChecked(new Date());
    } catch {
      if (cancelled?.current) return;
      setError("Could not connect to the API server");
    } finally {
      if (!cancelled?.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    const cancelled = { current: false };
    void fetchData(false, cancelled);
    return () => { cancelled.current = true; };
  }, [fetchData]);

  // Update session duration every minute
  useEffect(() => {
    const interval = setInterval(() => setNow(Date.now()), 60000);
    return () => clearInterval(interval);
  }, []);

  // Build service checks from health + runtime data
  const serviceChecks: ServiceCheck[] = [];

  // API Server is always healthy if the page loaded
  serviceChecks.push({
    name: "API Server",
    status: health ? "healthy" : "unhealthy",
    detail: health ? `Status: ${health.status}` : "Unreachable",
    icon: Server,
  });

  // Database
  serviceChecks.push({
    name: "Database",
    status: health?.database ? "healthy" : "unhealthy",
    detail: runtime?.database
      ? `${runtime.database.toUpperCase()}${runtime.mode === "local" ? " (local)" : ""}`
      : "Unknown",
    icon: Database,
  });

  // Redis
  if (health?.redis === null) {
    serviceChecks.push({
      name: "Redis",
      status: "unconfigured",
      detail: "Using in-process queue (local mode)",
      icon: Radio,
    });
  } else {
    serviceChecks.push({
      name: "Redis",
      status: health?.redis ? "healthy" : "unhealthy",
      detail: runtime?.queue === "redis" ? "Connected" : "Disconnected",
      icon: Radio,
    });
  }

  // Sandbox backend
  serviceChecks.push({
    name: "Sandbox Backend",
    status: health?.runtime ? "healthy" : "unhealthy",
    detail: runtime?.sandbox_backend
      ? runtime.sandbox_backend.toUpperCase()
      : "Unknown",
    icon: Container,
  });

  // Storage
  serviceChecks.push({
    name: "Storage Backend",
    status: "healthy", // If API is up, storage is accessible
    detail: runtime?.storage
      ? `${runtime.storage}${runtime.data_dir ? ` (${runtime.data_dir})` : ""}`
      : "Unknown",
    icon: HardDrive,
  });

  // Overall status
  const overallStatus: ServiceStatus =
    health?.status === "ok"
      ? "healthy"
      : health?.status === "degraded"
        ? "degraded"
        : "unhealthy";

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
        <h1 className="mb-4 sm:mb-6 text-xl sm:text-2xl font-semibold font-display tracking-tight text-foreground">System Health</h1>
        <div className="rounded-xl border border-error/30 bg-error/5 p-4">
          <p className="text-sm text-error">{error}</p>
          <button
            onClick={() => { setLoading(true); void fetchData(); }}
            className="mt-2 text-xs font-medium text-accent hover:text-accent/80 transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4 sm:space-y-5">
      {/* Header */}
      <PageHeader
        eyebrow="Diagnostics · Live"
        title="System Health"
        actions={
          <>
            {/* Overall status light (control-room indicator instead of a pill) */}
            <StatusLed
              status={overallStatus}
              size="md"
              label={overallStatus === "healthy" ? "All Systems Operational" : overallStatus === "degraded" ? "Degraded" : "Issues Detected"}
            />
            <button
              onClick={() => void fetchData(true)}
              disabled={refreshing}
              className={cn(
                "flex items-center gap-2 rounded-md border border-border px-3 py-1.5 text-sm font-medium",
                "text-foreground hover:bg-border/40 transition-colors",
                refreshing && "opacity-50 cursor-not-allowed"
              )}
            >
              <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} />
              Refresh
            </button>
          </>
        }
      />

      {/* Top row: Server Status + Runtime Info */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Server Status card */}
        <div className="rounded-md border border-border bg-surface overflow-hidden">
          <div className="flex items-center gap-2 border-b border-border px-4 py-2.5 sm:px-5">
            <Activity className="h-3.5 w-3.5 text-accent shrink-0" />
            <h2 className="panel-label text-foreground">Server Status</h2>
            <span className="panel-label text-muted-foreground/70 ml-auto">API health check</span>
          </div>
          <div className="space-y-2.5 p-4 sm:p-5">
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Status</span>
              <span
                className={cn(
                  "font-mono text-sm font-medium",
                  health?.status === "ok" ? "text-success" : "text-warning"
                )}
              >
                {health?.status?.toUpperCase() || "UNKNOWN"}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Version</span>
              <span className="font-mono text-sm text-foreground">
                {runtime?.version || "unknown"}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Session Duration</span>
              <span className="font-mono text-sm text-foreground">
                {formatUptime(pageLoadTime, now)}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Last Checked</span>
              <span className="font-mono text-sm text-muted">
                {lastChecked.toLocaleTimeString()}
              </span>
            </div>
          </div>
        </div>

        {/* Runtime Info card */}
        <div className="rounded-md border border-border bg-surface overflow-hidden">
          <div className="flex items-center gap-2 border-b border-border px-4 py-2.5 sm:px-5">
            <Server className="h-3.5 w-3.5 text-accent shrink-0" />
            <h2 className="panel-label text-foreground">Runtime Info</h2>
            <span className="panel-label text-muted-foreground/70 ml-auto">Sandbox and infrastructure</span>
          </div>
          <div className="space-y-2.5 p-4 sm:p-5">
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Mode</span>
              <StatusLed
                status={runtime?.mode === "local" ? "local" : "production"}
                label={runtime?.mode === "local" ? "Local" : "Production"}
              />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Sandbox Backend</span>
              <span className="font-mono text-sm text-foreground uppercase">
                {runtime?.sandbox_backend || "unknown"}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Database</span>
              <span className="font-mono text-sm text-foreground">
                {runtime?.database || "unknown"}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Queue</span>
              <span className="font-mono text-sm text-foreground">
                {runtime?.queue || "unknown"}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Storage</span>
              <span className="font-mono text-sm text-foreground">
                {runtime?.storage || "unknown"}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Service Checks: indicator board */}
      <div className="rounded-md border border-border bg-surface overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border flex items-center justify-between">
          <h2 className="panel-label text-muted-foreground">
            Service Checks
          </h2>
          <span className="font-mono text-[10px] tracking-widest text-muted uppercase">
            {serviceChecks.filter((c) => c.status === "healthy").length}/{serviceChecks.length} nominal
          </span>
        </div>
        <div className="divide-y divide-border">
          {serviceChecks.map((check) => (
            <div
              key={check.name}
              className="indicator-board-row flex items-center gap-4 px-4 py-2.5"
            >
              {/* Indicator light */}
              <StatusLed status={check.status} showLabel={false} size="md" />

              {/* Icon */}
              <check.icon className="h-4 w-4 text-muted shrink-0" />

              {/* Name */}
              <span className="font-mono text-xs tracking-wide text-foreground min-w-[140px]">
                {check.name}
              </span>

              {/* Detail */}
              <span className="text-sm font-mono text-muted flex-1 truncate">
                {check.detail}
              </span>

              {/* Status readout */}
              <span
                className="led-label text-[10px] shrink-0"
                style={{ color: getLedConfig(check.status).color }}
              >
                {statusLabel(check.status)}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Quick Stats */}
      {quickStats && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <div className="rounded-md border border-border bg-surface p-4">
            <p className="panel-label text-muted-foreground">Total Runs · Today</p>
            <p className="mt-1.5 font-display text-2xl sm:text-3xl font-bold tracking-tight leading-none text-foreground">
              <Odometer value={quickStats.runs} />
            </p>
          </div>
          <div className="rounded-md border border-border bg-surface p-4">
            <p className="panel-label text-muted-foreground">Workflows</p>
            <p className="mt-1.5 font-display text-2xl sm:text-3xl font-bold tracking-tight leading-none text-foreground">
              <Odometer value={quickStats.workflows} />
            </p>
          </div>
          <div className="rounded-md border border-border bg-surface p-4">
            <p className="panel-label text-muted-foreground">Templates</p>
            <p className="mt-1.5 font-display text-2xl sm:text-3xl font-bold tracking-tight leading-none text-foreground">
              <Odometer value={quickStats.templates} />
            </p>
          </div>
          <div className="rounded-md border border-border bg-surface p-4">
            <p className="panel-label text-muted-foreground">API Keys</p>
            <p className="mt-1.5 font-display text-2xl sm:text-3xl font-bold tracking-tight leading-none text-foreground">
              <Odometer value={quickStats.apiKeys} />
            </p>
          </div>
        </div>
      )}

      {/* Environment card */}
      <div className="rounded-md border border-border bg-surface overflow-hidden">
        <div className="flex items-center gap-2 border-b border-border px-4 py-2.5 sm:px-5">
          <CheckCircle2 className="h-3.5 w-3.5 text-accent shrink-0" />
          <h2 className="panel-label text-foreground">Environment</h2>
          <span className="panel-label text-muted-foreground/70 ml-auto">System configuration</span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-8 gap-y-4 p-4 sm:p-5">
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1">Sandcastle Version</p>
            <p className="text-sm font-mono text-foreground">
              {runtime?.version || "unknown"}
            </p>
          </div>
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1">Runtime Mode</p>
            <p className="text-sm font-mono text-foreground capitalize">
              {runtime?.mode || "unknown"}
            </p>
          </div>
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1">Sandbox Backend</p>
            <p className="text-sm font-mono text-foreground uppercase">
              {runtime?.sandbox_backend || "unknown"}
            </p>
          </div>
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1">Database Engine</p>
            <p className="text-sm font-mono text-foreground">
              {runtime?.database || "unknown"}
            </p>
          </div>
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1">Queue Backend</p>
            <p className="text-sm font-mono text-foreground">
              {runtime?.queue || "unknown"}
            </p>
          </div>
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1">Storage Backend</p>
            <p className="text-sm font-mono text-foreground">
              {runtime?.storage || "unknown"}
            </p>
          </div>
          {runtime?.data_dir && (
            <div className="sm:col-span-2 lg:col-span-3">
              <p className="text-xs font-medium text-muted-foreground mb-1">Data Directory</p>
              <p className="text-sm font-mono text-foreground">{runtime.data_dir}</p>
            </div>
          )}
        </div>
      </div>

      {/* Footer timestamp */}
      <p className="text-xs text-muted text-right">
        Last checked: {lastChecked.toLocaleString()}
      </p>
    </div>
  );
}
