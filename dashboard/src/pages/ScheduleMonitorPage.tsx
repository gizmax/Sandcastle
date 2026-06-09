import { useCallback, useEffect, useState } from "react";
import {
  Clock, Play, Pause, AlertTriangle, CheckCircle2,
  Loader2, RefreshCw, RotateCcw,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { cronToHuman, getNextRuns } from "@/lib/cron";
import { cn, formatRelativeTime } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ScheduleMonitorItem {
  id: string;
  workflow_name: string;
  cron_expression: string;
  enabled: boolean;
  last_run_id: string | null;
  last_run_at: string | null;
  last_run_status: string | null;
  next_run_at: string | null;
  success_rate: number;
  status: string; // "active" | "paused" | "failing"
  created_at: string | null;
}

// ---------------------------------------------------------------------------
// Status badge helper
// ---------------------------------------------------------------------------

function statusBadge(status: string) {
  switch (status) {
    case "active":
      return { label: "Active", bg: "bg-success/10 border-success/30 text-success" };
    case "paused":
      return { label: "Paused", bg: "bg-muted-foreground/10 border-border text-muted-foreground" };
    case "failing":
      return { label: "Failing", bg: "bg-error/10 border-error/30 text-error" };
    default:
      return { label: status, bg: "bg-border text-muted-foreground" };
  }
}

function runStatusIcon(status: string | null) {
  switch (status) {
    case "completed":
      return <CheckCircle2 className="h-3.5 w-3.5 text-success" />;
    case "failed":
      return <AlertTriangle className="h-3.5 w-3.5 text-error" />;
    case "running":
      return <Loader2 className="h-3.5 w-3.5 text-running animate-spin" />;
    default:
      return <Clock className="h-3.5 w-3.5 text-muted-foreground" />;
  }
}

// ---------------------------------------------------------------------------
// Countdown helper
// ---------------------------------------------------------------------------

function formatCountdown(isoDate: string | null): string {
  if (!isoDate) return "-";
  const diff = new Date(isoDate).getTime() - Date.now();
  if (diff <= 0) return "imminent";
  const minutes = Math.floor(diff / 60000);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  if (days > 0) return `${days}d ${hours % 24}h`;
  if (hours > 0) return `${hours}h ${minutes % 60}m`;
  return `${minutes}m`;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function ScheduleMonitorPage() {
  const [schedules, setSchedules] = useState<ScheduleMonitorItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [triggeringId, setTriggeringId] = useState<string | null>(null);

  const fetchSchedules = useCallback(async () => {
    try {
      setError(null);
      const res = await api.get<ScheduleMonitorItem[]>("/schedules");
      if (res.data) {
        // Compute next_run_at from cron if backend did not provide it
        const enriched = res.data.map((s) => {
          if (!s.next_run_at && s.enabled) {
            const nextRuns = getNextRuns(s.cron_expression, 1);
            if (nextRuns.length > 0) {
              return { ...s, next_run_at: nextRuns[0].toISOString() };
            }
          }
          return s;
        });
        setSchedules(enriched);
      }
    } catch {
      setError("Could not connect to the API server");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchSchedules();
  }, [fetchSchedules]);

  // Refresh countdown every 30 seconds
  const [, setTick] = useState(0);
  useEffect(() => {
    const interval = setInterval(() => setTick((t) => t + 1), 30_000);
    return () => clearInterval(interval);
  }, []);

  const handleToggle = useCallback(async (id: string, enabled: boolean) => {
    setTogglingId(id);
    try {
      const res = await api.patch(`/schedules/${id}`, { enabled });
      if (res.error) {
        toast.error(`Failed to toggle schedule: ${res.error.message}`);
        return;
      }
      toast.success(`Schedule ${enabled ? "resumed" : "paused"}`);
      setSchedules((prev) =>
        prev.map((s) =>
          s.id === id
            ? { ...s, enabled, status: enabled ? (s.last_run_status === "failed" ? "failing" : "active") : "paused" }
            : s
        )
      );
    } finally {
      setTogglingId(null);
    }
  }, []);

  const handleTrigger = useCallback(async (id: string) => {
    setTriggeringId(id);
    try {
      const res = await api.post(`/schedules/${id}/trigger`);
      if (res.error) {
        toast.error(`Failed to trigger schedule: ${res.error.message}`);
        return;
      }
      toast.success("Workflow triggered");
      // Optimistically update last run status
      setSchedules((prev) =>
        prev.map((s) =>
          s.id === id
            ? { ...s, last_run_at: new Date().toISOString(), last_run_status: "running" }
            : s
        )
      );
    } finally {
      setTriggeringId(null);
    }
  }, []);

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
        <h1 className="mb-4 sm:mb-6 text-xl sm:text-2xl font-semibold font-display tracking-tight text-foreground">
          Schedule Monitor
        </h1>
        <div className="rounded-xl border border-error/30 bg-error/5 p-4">
          <p className="text-sm text-error">{error}</p>
          <button
            onClick={() => { setLoading(true); void fetchSchedules(); }}
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
        <h1 className="text-xl sm:text-2xl font-semibold font-display tracking-tight text-foreground">
          Schedule Monitor
        </h1>
        <button
          onClick={() => { setLoading(true); void fetchSchedules(); }}
          aria-label="Refresh schedules"
          className={cn(
            "flex items-center gap-2 rounded-lg bg-surface border border-border px-3 py-2 text-sm font-medium text-foreground",
            "hover:bg-background transition-all duration-200"
          )}
        >
          <RefreshCw className="h-4 w-4" />
          <span className="hidden sm:inline">Refresh</span>
        </button>
      </div>

      {schedules.length === 0 ? (
        <EmptyState
          variant="tide"
          title="No schedules configured"
          description="Create a schedule from the Schedules page to start monitoring automated workflow runs."
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 sm:gap-5">
          {schedules.map((schedule) => {
            const badge = statusBadge(schedule.status ?? (schedule.enabled ? "active" : "paused"));
            const successPct = Math.round((schedule.success_rate ?? 0) * 100);
            const isToggling = togglingId === schedule.id;
            const isTriggering = triggeringId === schedule.id;

            return (
              <div
                key={schedule.id}
                className={cn(
                  "bg-surface rounded-2xl shadow-sm border border-border",
                  "hover:border-accent/30 transition-all duration-300",
                  "p-5 flex flex-col gap-4"
                )}
              >
                {/* Header: workflow name + status badge */}
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-foreground truncate">
                      {schedule.workflow_name}
                    </p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {cronToHuman(schedule.cron_expression)}
                    </p>
                  </div>
                  <span className={cn(
                    "shrink-0 text-[10px] font-semibold rounded-full px-2.5 py-1 border",
                    badge.bg
                  )}>
                    {badge.label}
                  </span>
                </div>

                {/* Stats row */}
                <div className="grid grid-cols-2 gap-3">
                  {/* Last run */}
                  <div className="flex items-center gap-2">
                    {runStatusIcon(schedule.last_run_status)}
                    <div className="min-w-0">
                      <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
                        Last Run
                      </p>
                      <p className="text-xs font-medium text-foreground truncate">
                        {schedule.last_run_at
                          ? formatRelativeTime(schedule.last_run_at)
                          : "Never"}
                      </p>
                    </div>
                  </div>

                  {/* Next run */}
                  <div className="flex items-center gap-2">
                    <Clock className="h-3.5 w-3.5 text-running" />
                    <div className="min-w-0">
                      <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
                        Next Run
                      </p>
                      <p className="text-xs font-medium text-foreground truncate">
                        {schedule.enabled
                          ? formatCountdown(schedule.next_run_at)
                          : "-"}
                      </p>
                    </div>
                  </div>
                </div>

                {/* Success rate bar */}
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
                      Success Rate
                    </p>
                    <p className={cn(
                      "text-xs font-semibold",
                      successPct >= 80 ? "text-success" : successPct >= 50 ? "text-warning" : "text-error"
                    )}>
                      {successPct}%
                    </p>
                  </div>
                  <div className="h-1.5 w-full rounded-full bg-border overflow-hidden">
                    <div
                      className={cn(
                        "h-full rounded-full transition-all duration-500",
                        successPct >= 80 ? "bg-success" : successPct >= 50 ? "bg-warning" : "bg-error"
                      )}
                      style={{ width: `${successPct}%` }}
                    />
                  </div>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-2 pt-1 border-t border-border">
                  <button
                    onClick={() => handleToggle(schedule.id, !schedule.enabled)}
                    disabled={isToggling}
                    className={cn(
                      "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium",
                      "border transition-all duration-200",
                      schedule.enabled
                        ? "border-warning/30 bg-warning/5 text-warning hover:bg-warning/10"
                        : "border-success/30 bg-success/5 text-success hover:bg-success/10",
                      isToggling && "opacity-50 cursor-not-allowed"
                    )}
                  >
                    {isToggling ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : schedule.enabled ? (
                      <Pause className="h-3 w-3" />
                    ) : (
                      <RotateCcw className="h-3 w-3" />
                    )}
                    {schedule.enabled ? "Pause" : "Resume"}
                  </button>
                  <button
                    onClick={() => handleTrigger(schedule.id)}
                    disabled={isTriggering || !schedule.enabled}
                    className={cn(
                      "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium",
                      "border border-accent/30 bg-accent/5 text-accent hover:bg-accent/10",
                      "transition-all duration-200",
                      (isTriggering || !schedule.enabled) && "opacity-50 cursor-not-allowed"
                    )}
                  >
                    {isTriggering ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <Play className="h-3 w-3" />
                    )}
                    Run Now
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
