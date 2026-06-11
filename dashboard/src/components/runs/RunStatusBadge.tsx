import { StatusLed } from "@/components/ui/StatusLed";

interface RunStatusBadgeProps {
  status: string;
  className?: string;
  size?: "sm" | "md";
}

/* Thin wrapper kept for API compatibility: every run status surface
   now renders the StatusLed indicator-light language instead of a pill. */
export function RunStatusBadge({ status, className, size = "sm" }: RunStatusBadgeProps) {
  return <StatusLed status={status} size={size} className={className} />;
}
