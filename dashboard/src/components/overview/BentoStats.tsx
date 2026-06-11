import { Activity, CheckCircle, DollarSign, Timer } from "lucide-react";
import { Odometer } from "@/components/ui/Odometer";
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
  value: React.ReactNode;
  icon: React.ElementType;
  iconColor: string;
  spark?: SparklineData;
  positiveIsGood?: boolean;
}

export function BentoStatCard({
  label, value, icon: Icon, iconColor,
  spark, positiveIsGood = true,
}: StatCardProps) {
  return (
    <div className={cn(
      "bg-surface rounded-md border border-border",
      "p-4",
      "hover:border-accent/30 transition-settle",
      "flex flex-col gap-2.5",
    )}>
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1.5">
          <Icon className={cn("shrink-0", iconColor)} style={{ height: "13px", width: "13px" }} />
          <span className="panel-label text-muted-foreground truncate">{label}</span>
        </span>
        {spark && <TrendBadge percent={spark.trendPercent} positiveIsGood={positiveIsGood} />}
      </div>
      <div className="flex items-end justify-between gap-2">
        <p className="font-display text-3xl font-bold text-foreground tracking-tight leading-none">{value}</p>
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
        value={<Odometer value={totalRuns} />}
        icon={Activity}
        iconColor="text-accent"
        spark={sparklines?.runs}
        positiveIsGood={true}
      />
      <BentoStatCard
        label="Success Rate"
        value={<Odometer value={successRate} format={(v) => `${Math.round(v)}%`} />}
        icon={CheckCircle}
        iconColor="text-success"
        spark={sparklines?.rate}
        positiveIsGood={true}
      />
      <BentoStatCard
        label="Cost Today"
        value={<Odometer value={totalCost} format={formatCost} />}
        icon={DollarSign}
        iconColor="text-running"
        spark={sparklines?.cost}
        positiveIsGood={false}
      />
      <BentoStatCard
        label="Avg Duration"
        value={avgDuration > 0 ? <Odometer value={avgDuration} format={(v) => `${Math.round(v)}s`} /> : "n/a"}
        icon={Timer}
        iconColor="text-muted-foreground"
        spark={sparklines?.duration}
        positiveIsGood={false}
      />
    </div>
  );
}
