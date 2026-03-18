import { cn } from "@/lib/utils";

type RiskLevel = "minimal" | "limited" | "high" | "unacceptable";

interface RiskLevelBadgeProps {
  level: RiskLevel | string;
  className?: string;
}

const RISK_COLORS: Record<string, string> = {
  minimal: "bg-muted/30 border-border text-muted-foreground",
  limited: "bg-accent/10 border-accent/30 text-accent",
  high: "bg-warning/10 border-warning/30 text-warning",
  unacceptable: "bg-error/10 border-error/30 text-error",
};

const RISK_DOT_COLORS: Record<string, string> = {
  minimal: "bg-muted-foreground",
  limited: "bg-accent",
  high: "bg-warning",
  unacceptable: "bg-error",
};

export function RiskLevelBadge({ level, className }: RiskLevelBadgeProps) {
  const colors = RISK_COLORS[level] || RISK_COLORS.minimal;
  const dotColor = RISK_DOT_COLORS[level] || RISK_DOT_COLORS.minimal;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium capitalize",
        colors,
        className
      )}
      title={`EU AI Act risk level: ${level}`}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full shrink-0", dotColor)} />
      {level}
    </span>
  );
}
