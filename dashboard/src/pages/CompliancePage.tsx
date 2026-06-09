import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Shield,
  ShieldCheck,
  ShieldAlert,
  RefreshCw,
  AlertOctagon,
  FileText,
  Clock,
  User,
  ExternalLink,
  CheckCircle2,
  XCircle,
} from "lucide-react";
import { api } from "@/api/client";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { RiskLevelBadge } from "@/components/runs/RiskLevelBadge";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

// -- Types ------------------------------------------------------------------

interface ComplianceFeatures {
  audit_trail: boolean;
  risk_classification: boolean;
  privacy_router: boolean;
  emergency_stop: boolean;
  input_prompt_logging: boolean;
}

interface ComplianceStatus {
  mode: string;
  active: boolean;
  features: ComplianceFeatures;
}

interface AuditEvent {
  id: string;
  event_type: string;
  actor: string;
  run_id: string | null;
  workflow_name: string | null;
  risk_level: string | null;
  metadata: Record<string, unknown>;
  timestamp: string;
  hash: string;
}

// -- Helpers ----------------------------------------------------------------

function formatEventType(type: string): string {
  return type
    .replace(/\./g, " - ")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatTimestamp(ts: string): string {
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return ts;
  }
}

function eventTypeColor(type: string): string {
  if (type.includes("emergency")) return "text-error";
  if (type.includes("failed") || type.includes("violated")) return "text-error";
  if (type.includes("risk.classified") || type.includes("pii")) return "text-warning";
  if (type.includes("completed") || type.includes("cleared")) return "text-success";
  return "text-accent";
}

const FEATURE_LABELS: Record<keyof ComplianceFeatures, string> = {
  audit_trail: "Audit Trail",
  risk_classification: "Risk Classification",
  privacy_router: "Privacy Router (PII)",
  emergency_stop: "Emergency Stop",
  input_prompt_logging: "Input/Prompt Logging",
};

// -- Component --------------------------------------------------------------

export default function CompliancePage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<ComplianceStatus | null>(null);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [auditTotal, setAuditTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [emergencyConfirmOpen, setEmergencyConfirmOpen] = useState(false);
  const [emergencyStopping, setEmergencyStopping] = useState(false);
  const [lastChecked, setLastChecked] = useState<Date>(new Date());

  const fetchData = useCallback(async (isRefresh = false, cancelled?: { current: boolean }) => {
    if (isRefresh) setRefreshing(true);
    setError(null);
    try {
      const [statusRes, auditRes] = await Promise.all([
        api.get<ComplianceStatus>("/compliance/status"),
        api.get<AuditEvent[]>("/audit", { limit: "10", offset: "0" }),
      ]);
      if (cancelled?.current) return;
      if (statusRes.data) setStatus(statusRes.data);
      if (auditRes.data) {
        setAuditEvents(auditRes.data as unknown as AuditEvent[]);
      }
      if (auditRes.meta) setAuditTotal(auditRes.meta.total);
      setLastChecked(new Date());
    } catch {
      if (cancelled?.current) return;
      setError("Could not load compliance data");
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

  const handleEmergencyStop = useCallback(async () => {
    setEmergencyStopping(true);
    const res = await api.post("/admin/emergency-stop", {
      action: "stop",
      reason: "Manual emergency stop triggered from dashboard",
    });
    setEmergencyStopping(false);
    setEmergencyConfirmOpen(false);
    if (res.error) {
      toast.error("Emergency stop failed: " + res.error.message);
    } else {
      toast.success("Emergency stop triggered - all high-risk runs halted");
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
        <h1 className="mb-4 sm:mb-6 text-xl sm:text-2xl font-semibold font-display tracking-tight text-foreground">Compliance</h1>
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

  const complianceActive = status?.active ?? false;

  return (
    <div className="space-y-4 sm:space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Shield className="h-6 w-6 text-muted" />
          <h1 className="text-xl sm:text-2xl font-semibold font-display tracking-tight text-foreground">
            Compliance
          </h1>
          <span
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
              complianceActive
                ? "bg-success/15 border-success/30 text-success"
                : "bg-muted/30 border-border text-muted-foreground"
            )}
          >
            <span className={cn("h-1.5 w-1.5 rounded-full", complianceActive ? "bg-success" : "bg-muted-foreground")} />
            {complianceActive ? "Active" : "Inactive"}
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

      {/* Compliance Status card */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="rounded-xl border border-border bg-surface p-5 sm:p-6 shadow-sm">
          <div className="flex items-center gap-3 mb-4">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent/10">
              <ShieldCheck className="h-[18px] w-[18px] text-accent" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-foreground">Compliance Status</h2>
              <p className="text-sm text-muted-foreground mt-0.5">EU AI Act configuration</p>
            </div>
          </div>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Mode</span>
              <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-accent/15 border border-accent/30 text-accent font-mono uppercase">
                {status?.mode || "unknown"}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Status</span>
              <span className={cn(
                "text-sm font-medium",
                complianceActive ? "text-success" : "text-muted"
              )}>
                {complianceActive ? "Enabled" : "Disabled"}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Last Checked</span>
              <span className="font-mono text-xs text-muted">
                {lastChecked.toLocaleTimeString()}
              </span>
            </div>
          </div>
        </div>

        {/* Feature flags */}
        <div className="rounded-xl border border-border bg-surface p-5 sm:p-6 shadow-sm">
          <div className="flex items-center gap-3 mb-4">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent/10">
              <ShieldAlert className="h-[18px] w-[18px] text-accent" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-foreground">Feature Flags</h2>
              <p className="text-sm text-muted-foreground mt-0.5">Active compliance controls</p>
            </div>
          </div>
          <div className="space-y-2.5">
            {status && Object.entries(status.features).map(([key, enabled]) => (
              <div key={key} className="flex items-center gap-3">
                {enabled ? (
                  <CheckCircle2 className="h-4 w-4 shrink-0 text-success" />
                ) : (
                  <XCircle className="h-4 w-4 shrink-0 text-muted" />
                )}
                <span className={cn(
                  "text-sm",
                  enabled ? "text-foreground" : "text-muted-foreground"
                )}>
                  {FEATURE_LABELS[key as keyof ComplianceFeatures] || key}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Quick Actions */}
      <div className="rounded-xl border border-border bg-surface p-5 sm:p-6 shadow-sm">
        <h2 className="text-sm font-semibold text-foreground uppercase tracking-wider mb-4">
          Quick Actions
        </h2>
        <div className="flex flex-wrap gap-3">
          <button
            onClick={() => navigate("/runs/a1b2c3d4-1111-4000-8000-000000000001")}
            className={cn(
              "inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium",
              "border border-border text-foreground bg-transparent",
              "transition-all duration-200 hover:bg-surface hover:border-border/80 hover:shadow-sm",
              "active:scale-[0.98]"
            )}
          >
            <FileText className="h-4 w-4 text-accent" />
            View Transparency Report
          </button>
          <button
            onClick={() => navigate("/workflows/lead-enrichment")}
            className={cn(
              "inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium",
              "border border-border text-foreground bg-transparent",
              "transition-all duration-200 hover:bg-surface hover:border-border/80 hover:shadow-sm",
              "active:scale-[0.98]"
            )}
          >
            <ExternalLink className="h-4 w-4 text-accent" />
            Generate Annex IV
          </button>
          <button
            onClick={() => setEmergencyConfirmOpen(true)}
            className={cn(
              "inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium",
              "bg-error text-white border border-error",
              "transition-all duration-200 hover:bg-error/90 hover:shadow-md",
              "active:scale-[0.98]"
            )}
          >
            <AlertOctagon className="h-4 w-4" />
            Emergency Stop
          </button>
        </div>
      </div>

      {/* Audit Trail */}
      <div className="rounded-xl border border-border bg-surface shadow-sm overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <h2 className="text-sm font-semibold text-foreground uppercase tracking-wider">
            Audit Trail
          </h2>
          <span className="text-xs text-muted-foreground font-mono">
            {auditTotal} total events
          </span>
        </div>
        <div className="divide-y divide-border">
          {auditEvents.length === 0 ? (
            <div className="flex items-center justify-center py-12">
              <p className="text-sm text-muted">No audit events</p>
            </div>
          ) : (
            auditEvents.map((event) => (
              <div
                key={event.id}
                className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4 px-5 py-3.5 hover:bg-border/10 transition-colors"
              >
                {/* Event type */}
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <span className={cn(
                    "text-xs font-semibold font-mono shrink-0",
                    eventTypeColor(event.event_type)
                  )}>
                    {formatEventType(event.event_type)}
                  </span>
                </div>

                {/* Run link */}
                {event.run_id ? (
                  <button
                    onClick={() => navigate(`/runs/${event.run_id}`)}
                    className="hidden sm:inline-flex items-center gap-1 font-mono text-xs text-accent hover:text-accent/80 transition-colors shrink-0"
                  >
                    {event.run_id.slice(0, 8)}
                    <ExternalLink className="h-3 w-3" />
                  </button>
                ) : (
                  <span className="hidden sm:block font-mono text-xs text-muted shrink-0">-</span>
                )}

                {/* Risk level */}
                <div className="hidden md:block shrink-0">
                  {event.risk_level ? (
                    <RiskLevelBadge level={event.risk_level} />
                  ) : (
                    <span className="text-xs text-muted">-</span>
                  )}
                </div>

                {/* Actor */}
                <div className="hidden lg:flex items-center gap-1.5 shrink-0 min-w-[120px]">
                  <User className="h-3.5 w-3.5 text-muted shrink-0" />
                  <span className="font-mono text-xs text-muted truncate max-w-[100px]" title={event.actor}>
                    {event.actor}
                  </span>
                </div>

                {/* Timestamp */}
                <div className="flex items-center gap-1.5 shrink-0">
                  <Clock className="h-3.5 w-3.5 text-muted shrink-0" />
                  <span className="font-mono text-xs text-muted">
                    {formatTimestamp(event.timestamp)}
                  </span>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Hash chain note */}
      <div className="rounded-xl border border-border/50 bg-background/50 px-5 py-3 flex items-start gap-3">
        <ShieldCheck className="h-4 w-4 text-success mt-0.5 shrink-0" />
        <p className="text-xs text-muted-foreground">
          All audit events are protected by a SHA-256 tamper-evident hash chain. Each event hash
          incorporates the previous event hash, ensuring integrity cannot be silently broken.
        </p>
      </div>

      {/* Footer timestamp */}
      <p className="text-xs text-muted text-right">
        Last checked: {lastChecked.toLocaleString()}
      </p>

      {/* Emergency Stop confirmation */}
      <ConfirmDialog
        open={emergencyConfirmOpen}
        title="Emergency Stop"
        description="This will immediately halt all running high-risk AI workflows and prevent new ones from starting. This action is logged in the audit trail."
        confirmLabel={emergencyStopping ? "Stopping..." : "Trigger Emergency Stop"}
        confirmDisabled={emergencyStopping}
        variant="danger"
        onConfirm={handleEmergencyStop}
        onCancel={() => setEmergencyConfirmOpen(false)}
      />
    </div>
  );
}
