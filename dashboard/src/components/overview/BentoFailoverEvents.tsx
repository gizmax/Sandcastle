import { DollarSign, Shuffle } from "lucide-react";
import { cn, formatCost, formatRelativeTime } from "@/lib/utils";
import type { FailoverEventsData } from "./bentoTypes";

export function BentoFailoverEvents({ data }: { data: FailoverEventsData }) {
  if (data.total_failovers_7d === 0) return null;

  const lastEvent = data.events[0];
  const lastTimeAgo = lastEvent ? formatRelativeTime(lastEvent.timestamp) : "";

  const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

  // Extract short reason label (e.g. "429 rate-limit" from "429 rate-limit from anthropic")
  const shortReason = (reason: string) => {
    const fromIdx = reason.lastIndexOf(" from ");
    return fromIdx > 0 ? reason.slice(0, fromIdx) : reason;
  };

  return (
    <div className={cn(
      "bg-surface rounded-2xl shadow-sm border border-border",
      "hover:border-accent/30 transition-settle",
      "p-5 flex flex-col gap-3",
    )}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="h-8 w-8 rounded-xl bg-warning/10 flex items-center justify-center">
            <Shuffle className="h-4 w-4 text-warning" />
          </div>
          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              Provider Failovers
            </p>
            <p className="text-2xl font-bold text-foreground leading-none mt-0.5">
              {data.total_failovers_7d}
            </p>
          </div>
        </div>
        {data.total_cost_delta_7d > 0 && (
          <span className="inline-flex items-center gap-1 rounded-full bg-warning/10 border border-warning/20 px-2.5 py-1 text-[11px] font-semibold text-warning">
            <DollarSign className="h-3 w-3" />
            +{formatCost(data.total_cost_delta_7d)} extra
          </span>
        )}
      </div>

      {lastEvent && (
        <div className="rounded-xl bg-background border border-border px-3 py-2">
          <p className="text-sm text-foreground">
            <span className="text-muted-foreground">{lastTimeAgo}</span>
            {" - "}
            {capitalize(lastEvent.original_provider)}
            <span className="text-muted-foreground mx-1">&rarr;</span>
            {capitalize(lastEvent.failover_provider)}
          </p>
          <p className="text-xs text-muted-foreground mt-0.5">
            {shortReason(lastEvent.reason)}
          </p>
        </div>
      )}

      {data.events.length > 1 && (
        <p className="text-xs text-muted-foreground">
          +{data.events.length - 1} more in the last 7 days
        </p>
      )}
    </div>
  );
}
