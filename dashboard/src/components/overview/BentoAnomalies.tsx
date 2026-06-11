import { useNavigate } from "react-router-dom";
import { AlertTriangle, ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";
import type { AnomalyItem } from "./bentoTypes";

export function BentoAnomalies({ anomalies }: { anomalies: AnomalyItem[] }) {
  const navigate = useNavigate();
  if (anomalies.length === 0) return null;
  return (
    <div className={cn(
      "bg-surface rounded-2xl shadow-sm border border-border",
      "hover:border-accent/30 transition-settle",
      "p-6 flex flex-col gap-3",
    )}>
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
        Anomalies Detected
      </p>
      <div className="flex flex-col gap-2">
        {anomalies.slice(0, 5).map((a, i) => (
          <button
            key={`${a.type}-${a.workflow}-${i}`}
            onClick={() => a.run_id ? navigate(`/runs/${a.run_id}`) : undefined}
            className={cn(
              "flex items-start gap-2.5 rounded-xl px-3 py-2 text-left w-full",
              "border transition-colors duration-150",
              a.severity === "critical"
                ? "border-error/30 bg-error/5 hover:bg-error/10"
                : "border-warning/30 bg-warning/5 hover:bg-warning/10",
            )}
          >
            <AlertTriangle className={cn(
              "h-4 w-4 shrink-0 mt-0.5",
              a.severity === "critical" ? "text-error" : "text-warning",
            )} />
            <span className="flex-1 text-sm text-foreground">{a.message}</span>
            {a.run_id && (
              <ArrowRight className="h-3 w-3 text-muted-foreground shrink-0 mt-1" />
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
