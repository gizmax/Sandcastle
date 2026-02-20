import { cn } from "@/lib/utils";

const STATUS_STYLES: Record<string, { badge: string; dot: string }> = {
  draft: { badge: "bg-muted/15 text-muted border-muted/30", dot: "bg-muted" },
  staging: { badge: "bg-warning/15 text-warning border-warning/30", dot: "bg-warning" },
  production: { badge: "bg-success/15 text-success border-success/30", dot: "bg-success" },
  archived: { badge: "bg-muted/10 text-muted/60 border-muted/20", dot: "bg-muted/60" },
};

export function VersionStatusBadge({ status, className }: { status: string; className?: string }) {
  const style = STATUS_STYLES[status] || STATUS_STYLES.draft;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium capitalize",
        style.badge,
        className
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", style.dot)} />
      {status}
    </span>
  );
}
