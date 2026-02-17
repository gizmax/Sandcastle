import { cn } from "@/lib/utils";

const STATUS_STYLES: Record<string, string> = {
  draft: "bg-muted/15 text-muted border-muted/30",
  staging: "bg-warning/15 text-warning border-warning/30",
  production: "bg-success/15 text-success border-success/30",
  archived: "bg-muted/10 text-muted/60 border-muted/20",
};

export function VersionStatusBadge({ status, className }: { status: string; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium capitalize",
        STATUS_STYLES[status] || STATUS_STYLES.draft,
        className
      )}
    >
      {status}
    </span>
  );
}
