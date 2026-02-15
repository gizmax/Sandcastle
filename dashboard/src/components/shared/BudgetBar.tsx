import { cn } from "@/lib/utils";

interface BudgetBarProps {
  spent: number;
  limit: number;
}

export function BudgetBar({ spent, limit }: BudgetBarProps) {
  if (limit <= 0) return null;
  const pct = Math.min((spent / limit) * 100, 100);
  const isWarning = pct >= 80;
  const isExceeded = pct >= 100;

  return (
    <div className="w-full">
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="text-muted">Budget</span>
        <span
          className={cn(
            "font-mono font-medium",
            isExceeded ? "text-error" : isWarning ? "text-warning" : "text-muted"
          )}
        >
          ${spent.toFixed(2)} / ${limit.toFixed(2)}
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-border/50">
        <div
          className={cn(
            "h-full rounded-full transition-all duration-500",
            isExceeded ? "bg-error" : isWarning ? "bg-warning" : "bg-accent"
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
      {isWarning && !isExceeded && (
        <p className="mt-1 text-xs text-warning">Approaching budget limit ({Math.round(pct)}%)</p>
      )}
      {isExceeded && (
        <p className="mt-1 text-xs text-error">Budget exceeded</p>
      )}
    </div>
  );
}
