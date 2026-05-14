import { useNavigate } from "react-router-dom";
import { Activity, AlertTriangle, CheckCircle, Play, Sparkles, Timer } from "lucide-react";
import { Skeleton } from "@/components/ui/Skeleton";
import { cn, formatCost, formatRelativeTime } from "@/lib/utils";
import type { AuditEvent } from "./bentoTypes";

const AUDIT_ICONS: Record<string, { icon: React.ElementType; color: string }> = {
  "workflow.completed": { icon: CheckCircle, color: "text-success" },
  "workflow.started": { icon: Play, color: "text-running" },
  "workflow.failed": { icon: AlertTriangle, color: "text-error" },
  "step.executed": { icon: Activity, color: "text-accent" },
  "privacy.pii_redacted": { icon: Sparkles, color: "text-warning" },
  "schedule.triggered": { icon: Timer, color: "text-running" },
};

export function BentoActivityFeed({ events, loading }: { events: AuditEvent[]; loading: boolean }) {
  const navigate = useNavigate();

  if (loading) {
    return (
      <div className={cn(
        "bg-surface rounded-2xl shadow-sm border border-border",
        "hover:border-accent/30 transition-all duration-300",
        "overflow-hidden",
      )}>
        <div className="px-5 py-4 border-b border-border">
          <h3 className="text-sm font-semibold text-foreground">Activity Feed</h3>
        </div>
        <div className="flex items-center justify-center h-40">
          <Skeleton className="h-4 w-32 rounded-lg" />
        </div>
      </div>
    );
  }

  if (events.length === 0) {
    return (
      <div className={cn(
        "bg-surface rounded-2xl shadow-sm border border-border",
        "hover:border-accent/30 transition-all duration-300",
        "overflow-hidden",
      )}>
        <div className="px-5 py-4 border-b border-border">
          <h3 className="text-sm font-semibold text-foreground">Activity Feed</h3>
        </div>
        <div className="flex items-center justify-center h-24">
          <p className="text-xs text-muted-foreground">No recent events</p>
        </div>
      </div>
    );
  }

  return (
    <div className={cn(
      "bg-surface rounded-2xl shadow-sm border border-border",
      "hover:border-accent/30 transition-all duration-300",
      "overflow-hidden",
    )}>
      <div className="px-5 py-4 border-b border-border flex items-center justify-between">
        <h3 className="text-sm font-semibold text-foreground">Activity Feed</h3>
        <span className="text-[10px] text-muted-foreground">{events.length} events</span>
      </div>
      <div className="max-h-80 overflow-y-auto divide-y divide-border">
        {events.map((event) => {
          const entry = AUDIT_ICONS[event.event_type] ?? { icon: Activity, color: "text-muted-foreground" };
          const Icon = entry.icon;
          const costUsd = (event.metadata?.total_cost_usd ?? event.metadata?.cost_usd) as number | undefined;
          const typeLabel = event.event_type.split(".").pop() ?? event.event_type;

          return (
            <button
              key={event.id}
              onClick={() => event.run_id ? navigate(`/runs/${event.run_id}`) : undefined}
              className={cn(
                "flex w-full items-center gap-3 px-5 py-3 text-left",
                "hover:bg-background transition-colors duration-150",
                event.run_id ? "cursor-pointer" : "cursor-default",
              )}
            >
              <Icon className={cn("h-3.5 w-3.5 shrink-0", entry.color)} />
              <div className="flex-1 min-w-0">
                <p className="text-xs text-foreground truncate">
                  {event.workflow_name && (
                    <span className="font-semibold">{event.workflow_name}</span>
                  )}
                  {event.workflow_name && " "}
                  <span className="text-muted-foreground">{typeLabel}</span>
                </p>
              </div>
              {costUsd != null && costUsd > 0 && (
                <span className="text-[10px] font-mono text-muted-foreground shrink-0">
                  {formatCost(costUsd)}
                </span>
              )}
              <span className="text-[10px] text-muted-foreground shrink-0 whitespace-nowrap">
                {formatRelativeTime(event.timestamp)}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
