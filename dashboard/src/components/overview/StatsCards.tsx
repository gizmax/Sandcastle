import { Activity, CheckCircle, DollarSign, Timer, TrendingUp, TrendingDown } from "lucide-react";
import { Odometer } from "@/components/ui/Odometer";
import { cn } from "@/lib/utils";
import { formatCost, formatDuration } from "@/lib/utils";

export interface SparklineData {
  /** Last 7 data points for the sparkline */
  values: number[];
  /** Percentage change vs yesterday (positive = increase) */
  trendPercent: number;
}

interface StatsCardsProps {
  totalRuns: number;
  successRate: number;
  totalCost: number;
  avgDuration: number;
  /** Optional sparkline + trend data for each card */
  sparklines?: {
    runs?: SparklineData;
    rate?: SparklineData;
    cost?: SparklineData;
    duration?: SparklineData;
  };
}

/**
 * Render a tiny SVG sparkline from an array of values.
 * Uses a simple polyline - no external dependencies.
 */
function Sparkline({ values, color }: { values: number[]; color: string }) {
  if (values.length < 2) return null;
  const w = 64;
  const h = 24;
  const padding = 2;

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  const points = values
    .map((v, i) => {
      const x = padding + (i / (values.length - 1)) * (w - padding * 2);
      const y = h - padding - ((v - min) / range) * (h - padding * 2);
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <svg
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      className="shrink-0"
      aria-hidden="true"
    >
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * Trend badge showing percentage change with an arrow icon.
 * `positive` indicates whether increasing the value is good (true) or bad (false).
 */
function TrendBadge({ percent, positiveIsGood }: { percent: number; positiveIsGood: boolean }) {
  if (!Number.isFinite(percent)) return null;

  const isUp = percent > 0;
  const isGood = positiveIsGood ? isUp : !isUp;
  const Icon = isUp ? TrendingUp : TrendingDown;
  const absPercent = Math.abs(percent);

  // No significant change
  if (absPercent < 0.5) return null;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[10px] font-medium leading-none",
        isGood
          ? "bg-success/10 text-success"
          : "bg-error/10 text-error"
      )}
    >
      <Icon className="h-2.5 w-2.5" />
      {absPercent.toFixed(1)}%
    </span>
  );
}

const cards = [
  {
    key: "runs" as const,
    label: "Total Runs Today",
    icon: Activity,
    format: (v: number) => String(v),
    positiveIsGood: true,
    sparkColor: "var(--color-accent)",
  },
  {
    key: "rate" as const,
    label: "Success Rate",
    icon: CheckCircle,
    format: (v: number) => `${Number.isFinite(v) ? Math.round(v * 100) : 0}%`,
    positiveIsGood: true,
    sparkColor: "var(--color-success)",
  },
  {
    key: "cost" as const,
    label: "Total Cost Today",
    icon: DollarSign,
    format: (v: number) => formatCost(v),
    positiveIsGood: false,
    sparkColor: "var(--color-warning)",
  },
  {
    key: "duration" as const,
    label: "Avg Duration",
    icon: Timer,
    format: (v: number) => formatDuration(v),
    positiveIsGood: false,
    sparkColor: "var(--color-running)",
  },
];

export function StatsCards({
  totalRuns,
  successRate,
  totalCost,
  avgDuration,
  sparklines,
}: StatsCardsProps) {
  const values = { runs: totalRuns, rate: successRate, cost: totalCost, duration: avgDuration };

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {cards.map((card) => {
        const spark = sparklines?.[card.key];
        return (
          <div
            key={card.key}
            className={cn(
              "rounded-xl border border-border bg-surface p-5",
              "shadow-sm transition-settle hover:shadow-md"
            )}
          >
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-accent/10">
                <card.icon aria-hidden="true" className="h-5 w-5 text-accent" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <p className="text-xs font-medium text-muted">{card.label}</p>
                  {spark && (
                    <TrendBadge
                      percent={spark.trendPercent}
                      positiveIsGood={card.positiveIsGood}
                    />
                  )}
                </div>
                <div className="flex items-center justify-between gap-2">
                  <p className="font-data text-xl font-semibold tracking-tight text-foreground">
                    <Odometer value={values[card.key]} format={card.format} />
                  </p>
                  {spark && spark.values.length >= 2 && (
                    <Sparkline values={spark.values} color={card.sparkColor} />
                  )}
                </div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
