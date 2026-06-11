import type { CSSProperties } from "react";
import { cn } from "@/lib/utils";
import { getLedConfig } from "@/lib/statusLed";

/* Control-room indicator light. Replaces pill badges app-wide:
   status reads as a physical LED + lowercase mono micro-label.
   Physics live in styles/status-lights.css; the status → state/color
   mapping lives in lib/statusLed.ts. */

interface StatusLedProps {
  status: string;
  /** Display text; defaults to the raw status string. */
  label?: string;
  /** Hide the text and show only the light. */
  showLabel?: boolean;
  /** sm for lists/tables, md for detail headers. */
  size?: "sm" | "md";
  className?: string;
}

export function StatusLed({
  status,
  label,
  showLabel = true,
  size = "sm",
  className,
}: StatusLedProps) {
  const { state, color } = getLedConfig(status);
  const text = label ?? status;

  return (
    <span
      data-status={status}
      data-led-state={state}
      className={cn(
        "inline-flex items-center",
        size === "sm" ? "gap-1.5" : "gap-2",
        className
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "led rounded-full shrink-0",
          `led--${state}`,
          size === "sm" ? "h-2 w-2" : "h-2.5 w-2.5"
        )}
        style={{ "--led-color": color } as CSSProperties}
      />
      {showLabel && (
        <span
          className={cn("led-label", size === "sm" ? "text-[10px]" : "text-xs")}
          style={{ color }}
        >
          {text}
        </span>
      )}
    </span>
  );
}
