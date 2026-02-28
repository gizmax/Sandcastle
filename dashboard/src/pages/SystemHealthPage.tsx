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
  HeartPulse,
} from "lucide-react";
import { api } from "@/api/client";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
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

function statusDot(status: ServiceStatus) {
  switch (status) {
    case "healthy":
      return "bg-success";
    case "unhealthy":
      return "bg-error animate-pulse";
    case "degraded":
      return "bg-warning animate-pulse";
    case "unconfigured":
      return "bg-muted";
  }
}

function statusLabel(status: ServiceStatus) {
  switch (status) {
    case "healthy":
      return { text: "Healthy", className: "text-success" };
    case "unhealthy":
      return { text: "Unhealthy", className: "text-error" };
    case "degraded":
      return { text: "Degraded", className: "text-warning" };
    case "unconfigured":
      return { text: "Not Configured", className: "text-muted" };
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

  const fetchData = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    setError(null);
    try {
      const [healthRes, runtimeRes, statsRes] =
        await Promise.all([
          api.get<HealthData>("/health"),
          api.get<RuntimeData>("/runtime"),
          api.get<StatsData>("/stats"),
        ]);

      if (healthRes.data) setHealth(healthRes.data);
      if (runtimeRes.data) setRuntime(runtimeRes.data);

      const runs = (statsRes.data as StatsData | null)?.total_runs_today ?? 0;

      const [workflowsRes, templatesRes, keysRes] = await Promise.all([
        api.get<{ name: string }[]>("/workflows"),
        api.get<{ id: string }[]>("/templates"),
        api.get<{ id: string }[]>("/api-keys"),
      ]);
      const workflows = Array.isArray(workflowsRes.data) ? workflowsRes.data.length : 0;
      const templates = Array.isArray(templatesRes.data) ? templatesRes.data.length : 0;
      const apiKeys = Array.isArray(keysRes.data) ? keysRes.data.length : 0;

      setQuickStats({ runs, workflows, templates, apiKeys });
      setLastChecked(new Date());
    } catch {
      setError("Could not connect to the API server");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void fetchData();
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
        <h1 className="mb-4 sm:mb-6 text-xl sm:text-2xl font-semibold tracking-tight text-foreground">System Health</h1>
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
    <div className="space-y-4 sm:space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <HeartPulse className="h-6 w-6 text-muted" />
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">
            System Health
          </h1>
          {/* Overall status badge */}
          <span
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
              overallStatus === "healthy" && "bg-success/15 border-success/30 text-success",
              overallStatus === "degraded" && "bg-warning/15 border-warning/30 text-warning",
              overallStatus === "unhealthy" && "bg-error/15 border-error/30 text-error"
            )}
          >
            <span className={cn("h-1.5 w-1.5 rounded-full", statusDot(overallStatus))} />
            {overallStatus === "healthy" ? "All Systems Operational" : overallStatus === "degraded" ? "Degraded" : "Issues Detected"}
          </span>
        </div>
        <button
          onClick={() => void fetchData(true)}
          disabled={refreshing}
          className={cn(
            "flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-sm font-medium",
            "text-foreground hover:bg-border/40 transition-colors",
            refreshing && "opacity-50 cursor-not-allowed"
          )}
        >
          <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} />
          Refresh
        </button>
      </div>

      {/* Top row: Server Status + Runtime Info */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Server Status card */}
        <div className="rounded-xl border border-border bg-surface p-5 sm:p-6 shadow-sm">
          <div className="flex items-center gap-3 mb-4">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent/10">
              <Activity className="h-[18px] w-[18px] text-accent" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-foreground">Server Status</h2>
              <p className="text-sm text-muted-foreground mt-0.5">API health check</p>
            </div>
          </div>
          <div className="space-y-3">
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
        <div className="rounded-xl border border-border bg-surface p-5 sm:p-6 shadow-sm">
          <div className="flex items-center gap-3 mb-4">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent/10">
              <Server className="h-[18px] w-[18px] text-accent" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-foreground">Runtime Info</h2>
              <p className="text-sm text-muted-foreground mt-0.5">Sandbox and infrastructure</p>
            </div>
          </div>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Mode</span>
              <span
                className={cn(
                  "inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium",
                  runtime?.mode === "local"
                    ? "bg-accent/15 border border-accent/30 text-accent"
                    : "bg-success/15 border border-success/30 text-success"
                )}
              >
                {runtime?.mode === "local" ? "Local" : "Production"}
              </span>
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

      {/* Service Checks */}
      <div className="rounded-xl border border-border bg-surface shadow-sm overflow-hidden">
        <div className="px-5 py-4 border-b border-border">
          <h2 className="text-sm font-semibold text-foreground uppercase tracking-wider">
            Service Checks
          </h2>
        </div>
        <div className="divide-y divide-border">
          {serviceChecks.map((check) => {
            const label = statusLabel(check.status);
            return (
              <div
                key={check.name}
                className="flex items-center gap-4 px-5 py-3.5 hover:bg-border/10 transition-colors"
              >
                {/* Status dot */}
                <span className={cn("h-2.5 w-2.5 rounded-full shrink-0", statusDot(check.status))} />

                {/* Icon */}
                <check.icon className="h-4 w-4 text-muted shrink-0" />

                {/* Name */}
                <span className="text-sm font-medium text-foreground min-w-[140px]">
                  {check.name}
                </span>

                {/* Detail */}
                <span className="text-sm font-mono text-muted flex-1 truncate">
                  {check.detail}
                </span>

                {/* Status label */}
                <span className={cn("text-xs font-medium shrink-0", label.className)}>
                  {label.text}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Quick Stats */}
      {quickStats && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">Total Runs (today)</p>
            <p className="mt-1 text-xl sm:text-2xl font-semibold text-foreground font-mono">
              {quickStats.runs}
            </p>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">Workflows</p>
            <p className="mt-1 text-xl sm:text-2xl font-semibold text-foreground font-mono">
              {quickStats.workflows}
            </p>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">Templates</p>
            <p className="mt-1 text-xl sm:text-2xl font-semibold text-foreground font-mono">
              {quickStats.templates}
            </p>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">API Keys</p>
            <p className="mt-1 text-xl sm:text-2xl font-semibold text-foreground font-mono">
              {quickStats.apiKeys}
            </p>
          </div>
        </div>
      )}

      {/* Environment card */}
      <div className="rounded-xl border border-border bg-surface p-5 sm:p-6 shadow-sm">
        <div className="flex items-center gap-3 mb-4">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent/10">
            <CheckCircle2 className="h-[18px] w-[18px] text-accent" />
          </div>
          <div>
            <h2 className="text-lg font-semibold text-foreground">Environment</h2>
            <p className="text-sm text-muted-foreground mt-0.5">
              System configuration overview
            </p>
          </div>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-8 gap-y-4">
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
