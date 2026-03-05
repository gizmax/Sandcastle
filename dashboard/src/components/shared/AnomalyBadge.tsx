import { AlertTriangle, AlertOctagon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Anomaly } from "@/lib/anomalyDetection";

interface AnomalyBadgeProps {
  anomalies: Anomaly[];
  className?: string;
}

export function AnomalyBadge({ anomalies, className }: AnomalyBadgeProps) {
  if (anomalies.length === 0) return null;

  const hasCritical = anomalies.some((a) => a.severity === "critical");
  const severity = hasCritical ? "critical" : "warning";
  const Icon = hasCritical ? AlertOctagon : AlertTriangle;
  const label =
    anomalies.length === 1
      ? anomalies[0].message
      : `${anomalies.length} anomalies`;

  return (
    <span
      className={cn(
        "group/anomaly relative inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold",
        severity === "critical"
          ? "bg-error/15 text-error"
          : "bg-warning/15 text-warning",
        className
      )}
    >
      <Icon className="h-3 w-3" />
      {label}
      {anomalies.length > 1 && (
        <span
          role="tooltip"
          className={cn(
            "pointer-events-none absolute bottom-full left-1/2 -translate-x-1/2 mb-2 z-50",
            "w-max max-w-[280px] rounded-md bg-foreground px-2.5 py-2 text-[11px] text-background shadow-lg",
            "opacity-0 transition-opacity duration-150 group-hover/anomaly:opacity-100"
          )}
        >
          <ul className="space-y-1">
            {anomalies.map((a, i) => (
              <li key={i} className="flex items-start gap-1.5">
                {a.severity === "critical" ? (
                  <AlertOctagon className="mt-0.5 h-2.5 w-2.5 shrink-0 text-error" />
                ) : (
                  <AlertTriangle className="mt-0.5 h-2.5 w-2.5 shrink-0 text-warning" />
                )}
                <span>{a.message}</span>
              </li>
            ))}
          </ul>
          <span
            className="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-foreground"
            aria-hidden="true"
          />
        </span>
      )}
    </span>
  );
}
