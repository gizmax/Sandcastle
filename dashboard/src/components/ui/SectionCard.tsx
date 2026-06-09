import { Lock } from "lucide-react";
import { cn } from "@/lib/utils";

export function SectionCard({
  icon: Icon,
  title,
  description,
  readOnly,
  children,
}: {
  icon: React.ElementType;
  title: string;
  description: string;
  readOnly?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "rounded-md border overflow-hidden",
        readOnly
          ? "border-dashed border-border/70 bg-surface/60"
          : "border-border bg-surface"
      )}
    >
      <div className="flex items-start gap-2.5 border-b border-border px-4 py-3 sm:px-5">
        <Icon
          className={cn(
            "mt-px h-3.5 w-3.5 shrink-0",
            readOnly ? "text-muted-foreground" : "text-accent"
          )}
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="panel-label text-foreground">{title}</h2>
            {readOnly && (
              <span className="inline-flex items-center gap-1 rounded-sm border border-border bg-muted/50 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                <Lock className="h-2.5 w-2.5" />
                Read-only
              </span>
            )}
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
          {readOnly && (
            <p className="text-xs text-muted-foreground/70 mt-0.5">
              Configured via environment variables
            </p>
          )}
        </div>
      </div>
      <div className="p-4 sm:p-5">{children}</div>
    </div>
  );
}

export function FieldLabel({ htmlFor, children }: { htmlFor: string; children: React.ReactNode }) {
  return (
    <label htmlFor={htmlFor} className="block text-sm font-medium text-foreground mb-1.5">
      {children}
    </label>
  );
}

export function HelperText({ children }: { children: React.ReactNode }) {
  return <p className="text-xs text-muted-foreground mt-1">{children}</p>;
}
