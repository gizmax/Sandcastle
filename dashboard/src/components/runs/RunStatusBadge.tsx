import { cn } from "@/lib/utils";
import { STATUS_COLORS, STATUS_DOT_COLORS } from "@/lib/constants";

interface RunStatusBadgeProps {
  status: string;
  className?: string;
  size?: "sm" | "md";
}

export function RunStatusBadge({ status, className, size = "sm" }: RunStatusBadgeProps) {
  const colors = STATUS_COLORS[status] || STATUS_COLORS.pending;
  const dotColor = STATUS_DOT_COLORS[status] || STATUS_DOT_COLORS.pending;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border font-medium capitalize",
        colors,
        size === "sm" ? "px-2 py-0.5 text-xs" : "px-3 py-1 text-sm",
        className
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", dotColor)} />
      {status}
    </span>
  );
}
