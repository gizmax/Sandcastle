import { memo } from "react";
import { ShieldCheck, ShieldAlert, ShieldX } from "lucide-react";
import { cn } from "@/lib/utils";

interface DoctorBadgeProps {
  status: "ok" | "warning" | "blocked" | null | undefined;
  risk?: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL" | null;
  className?: string;
  showLabel?: boolean;
}

const config = {
  ok: {
    icon: ShieldCheck,
    label: "Ready",
    colors: "text-success",
    bg: "bg-success/10",
  },
  warning: {
    icon: ShieldAlert,
    label: "Warnings",
    colors: "text-warning",
    bg: "bg-warning/10",
  },
  blocked: {
    icon: ShieldX,
    label: "Blocked",
    colors: "text-error",
    bg: "bg-error/10",
  },
} as const;

export const DoctorBadge = memo(function DoctorBadge({
  status,
  risk,
  className,
  showLabel = false,
}: DoctorBadgeProps) {
  if (!status) return null;
  const c = config[status];
  if (!c) return null;
  const Icon = c.icon;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
        c.bg,
        c.colors,
        className,
      )}
      title={`Doctor: ${c.label}${risk ? ` (${risk})` : ""}`}
    >
      <Icon className="h-3 w-3" />
      {showLabel && <span>{c.label}</span>}
    </span>
  );
});
