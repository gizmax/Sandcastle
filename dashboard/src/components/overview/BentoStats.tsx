import { Activity, CheckCircle, DollarSign, Timer } from "lucide-react";
import { cn, formatCost } from "@/lib/utils";
import type { SparklineData } from "./bentoTypes";

export function Sparkline({ values, className }: { values: number[]; className?: string }) {
  if (values.length < 2) return null;
  const w = 80; const h = 28; const padding = 2;
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
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true" className={cn("shrink-0", className)}>
      <polyline
        points={points}
        fill="none"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="stroke-accent"
      />
    </svg>
  );
}

export function TrendBadge({ percent, positiveIsGood }: { percent: number; positiveIsGood: boolean }) {
  if (!Number.isFinite(percent) || Math.abs(percent) < 0.5) return null;
  const isUp = percent > 0;
  const isGood = positiveIsGood ? isUp : !isUp;
  return (
    <span className={cn(
      "inline-flex items-center gap-0.5 rounded-full px-2 py-0.5 text-[10px] font-semibold",
      isGood ? "bg-success/10 text-success" : "bg-error/10 text-error",
    )}>
      {isUp ? "+" : ""}{Math.abs(percent).toFixed(1)}%
    </span>
  );
}

interface StatCardProps {
  label: string;
  value: string;
  icon: React.ElementType;
  iconBg: string;
  iconColor: string;
  spark?: SparklineData;
  positiveIsGood?: boolean;
}

export function BentoStatCard({
  label, value, icon: Icon, iconBg, iconColor,
  spark, positiveIsGood = true,
}: StatCardProps) {
  return (
    <div className={cn(
      "bg-surface rounded-2xl shadow-sm border border-border",
      "p-6",
      "hover:border-accent/30 transition-all duration-300",
      "flex flex-col gap-3",
    )}>
      <div className="flex items-start justify-between">
        <div className={cn("h-9 w-9 rounded-xl flex items-center justify-center", iconBg)}>
          <Icon className={cn("shrink-0", iconColor)} style={{ height: "18px", width: "18px" }} />
        </div>
        {spark && <TrendBadge percent={spark.trendPercent} positiveIsGood={positiveIsGood} />}
      </div>
      <div className="flex items-end justify-between gap-2">
        <div>
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-0.5">{label}</p>
          <p className="text-3xl font-bold text-foreground tracking-tight leading-none">{value}</p>
        </div>
        {spark && spark.values.length >= 2 && <Sparkline values={spark.values} />}
      </div>
    </div>
  );
}

interface BentoStatsRowProps {
  totalRuns: number;
  successRate: number;
  totalCost: number;
  avgDuration: number;
  sparklines: Record<string, SparklineData> | null;
}

/** Row of 4 stat cards: runs, success rate, cost, avg duration. */
export function BentoStatsRow({
  totalRuns,
  successRate,
  totalCost,
  avgDuration,
  sparklines,
}: BentoStatsRowProps) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 sm:gap-5">
      <BentoStatCard
        label="Runs Today"
        value={String(totalRuns)}
        icon={Activity}
        iconColor="text-accent"
        iconBg="bg-accent/10"
        spark={sparklines?.runs}
        positiveIsGood={true}
      />
      <BentoStatCard
        label="Success Rate"
        value={`${successRate}%`}
        icon={CheckCircle}
        iconColor="text-success"
        iconBg="bg-success/10"
        spark={sparklines?.rate}
        positiveIsGood={true}
      />
      <BentoStatCard
        label="Cost Today"
        value={formatCost(totalCost)}
        icon={DollarSign}
        iconColor="text-running"
        iconBg="bg-running/10"
        spark={sparklines?.cost}
        positiveIsGood={false}
      />
      <BentoStatCard
        label="Avg Duration"
        value={avgDuration > 0 ? `${Math.round(avgDuration)}s` : "n/a"}
        icon={Timer}
        iconColor="text-muted-foreground"
        iconBg="bg-border"
        spark={sparklines?.duration}
        positiveIsGood={false}
      />
    </div>
  );
}
