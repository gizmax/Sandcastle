import { BadgeCheck } from "lucide-react";
import { cn } from "@/lib/utils";

interface ProvenBadgeProps {
  /** Opens the proof modal. Rendered as a plain span when omitted. */
  onClick?: (e: React.MouseEvent) => void;
  className?: string;
}

/** Bold "✓ Proven" badge for templates installed from a verified .sctpl
 *  bundle - the workflow ships with replayable proof-of-execution cassettes. */
export function ProvenBadge({ onClick, className }: ProvenBadgeProps) {
  return (
    <span
      data-testid="proven-badge"
      title="Cassette-verified template - click to inspect and replay the proof"
      onClick={(e) => {
        if (!onClick) return;
        e.stopPropagation();
        onClick(e);
      }}
      className={cn(
        "inline-flex items-center gap-1 rounded-full bg-success/15 border border-success/40",
        "px-1.5 py-0.5 text-[10px] font-bold text-success whitespace-nowrap",
        onClick && "cursor-pointer hover:bg-success/25 transition-colors",
        className
      )}
    >
      <BadgeCheck className="h-2.5 w-2.5" />
      Proven
    </span>
  );
}
