import { cn } from "@/lib/utils";
import { SandcastleBuilding } from "./SandcastleBuilding";

/**
 * BuildingLoader - branded first-load moment: sandcastle under construction
 * plus a "Building..." line. Use judiciously for full-page / section first
 * loads; tables and inline refreshes keep plain skeletons.
 */
interface Props {
  label?: string;
  className?: string;
}

export function BuildingLoader({ label = "Building...", className }: Props) {
  return (
    <div
      role="status"
      aria-label={label}
      className={cn("flex flex-col items-center justify-center gap-4 py-16", className)}
    >
      <SandcastleBuilding className="h-30 w-40 text-muted-foreground" />
      <p className="font-display text-sm font-semibold tracking-wide text-muted">{label}</p>
    </div>
  );
}
