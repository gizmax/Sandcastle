import { Activity, CheckCircle, DollarSign, Timer } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatCost, formatDuration } from "@/lib/utils";

interface StatsCardsProps {
  totalRuns: number;
  successRate: number;
  totalCost: number;
  avgDuration: number;
}

const cards = [
  {
    key: "runs",
    label: "Total Runs Today",
    icon: Activity,
    format: (v: number) => String(v),
  },
  {
    key: "rate",
    label: "Success Rate",
    icon: CheckCircle,
    format: (v: number) => `${Math.round(v * 100)}%`,
  },
  {
    key: "cost",
    label: "Total Cost Today",
    icon: DollarSign,
    format: (v: number) => formatCost(v),
  },
  {
    key: "duration",
    label: "Avg Duration",
    icon: Timer,
    format: (v: number) => formatDuration(v),
  },
] as const;

export function StatsCards({ totalRuns, successRate, totalCost, avgDuration }: StatsCardsProps) {
  const values = { runs: totalRuns, rate: successRate, cost: totalCost, duration: avgDuration };

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {cards.map((card) => (
        <div
          key={card.key}
          className={cn(
            "rounded-xl border border-border bg-surface p-5",
            "shadow-sm transition-all duration-200 hover:shadow-md"
          )}
        >
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-accent/10">
              <card.icon className="h-5 w-5 text-accent" />
            </div>
            <div>
              <p className="text-xs font-medium text-muted">{card.label}</p>
              <p className="text-xl font-semibold tracking-tight text-foreground">
                {card.format(values[card.key])}
              </p>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
