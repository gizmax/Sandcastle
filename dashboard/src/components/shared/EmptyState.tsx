import { cn } from "@/lib/utils";
import type { LucideIcon } from "lucide-react";
import { SandcastleEmpty } from "@/components/brand/SandcastleEmpty";
import { SandcastleBuilding } from "@/components/brand/SandcastleBuilding";
import { SandcastleRuin } from "@/components/brand/SandcastleRuin";
import { TideChart } from "@/components/brand/TideChart";

/**
 * Brand illustration variants:
 * - "castle"   - proud sandcastle: generic "nothing here yet" (default)
 * - "building" - castle under construction: something is being set up
 * - "ruin"     - crumbled castle: errors and failures
 * - "tide"     - wave motif: waiting, scheduled, or filtered-to-nothing
 */
export type EmptyStateVariant = "castle" | "building" | "ruin" | "tide";

const ILLUSTRATIONS: Record<EmptyStateVariant, (props: { className?: string }) => React.JSX.Element> = {
  castle: SandcastleEmpty,
  building: SandcastleBuilding,
  ruin: SandcastleRuin,
  tide: TideChart,
};

interface EmptyStateProps {
  /** Brand illustration. Defaults to the sandcastle unless a legacy icon is given. */
  variant?: EmptyStateVariant;
  /** @deprecated Legacy lucide icon - only rendered when no variant is set. */
  icon?: LucideIcon;
  title: string;
  description: string;
  action?: {
    label: string;
    onClick: () => void;
  };
  className?: string;
}

export function EmptyState({ variant, icon: Icon, title, description, action, className }: EmptyStateProps) {
  // Brand illustration wins; the lucide icon remains only as a legacy escape
  // hatch. With neither prop, the sandcastle is the house default.
  const Illustration = variant ? ILLUSTRATIONS[variant] : Icon ? null : SandcastleEmpty;
  const isTide = variant === "tide";

  return (
    <div className={cn("flex flex-col items-center justify-center py-16 text-center", className)}>
      {Illustration ? (
        <Illustration
          className={cn(
            "text-muted-foreground",
            isTide ? "mb-4 h-12 w-48" : "mb-5 h-27 w-36"
          )}
        />
      ) : Icon ? (
        <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-accent/10">
          <Icon className="h-8 w-8 text-accent" />
        </div>
      ) : null}
      <h3 className="mb-1 font-display text-lg font-semibold tracking-tight text-foreground">{title}</h3>
      <p className="mb-6 max-w-sm text-sm text-muted">{description}</p>
      {action && (
        <button
          onClick={action.onClick}
          className={cn(
            "rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground",
            "hover:bg-accent-hover transition-settle",
            "shadow-sm hover:shadow-md"
          )}
        >
          {action.label}
        </button>
      )}
    </div>
  );
}
