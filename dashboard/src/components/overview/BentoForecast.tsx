import { CostForecast } from "@/components/overview/CostForecast";
import { cn } from "@/lib/utils";

/** Wraps the existing CostForecast in the bento card styling. */
export function BentoForecast() {
  return (
    <div className={cn(
      "bg-surface rounded-2xl shadow-sm border border-border",
      "hover:border-accent/30 transition-all duration-300",
      "[&>div]:rounded-2xl [&>div]:border-0 [&>div]:shadow-none [&>div]:bg-transparent",
    )}>
      <CostForecast />
    </div>
  );
}
