import { cn } from "@/lib/utils";

interface PageHeaderProps {
  /** Mono uppercase micro-label rendered above the title, e.g. "OPERATIONS · RUN HISTORY". */
  eyebrow: string;
  title: string;
  /** Right-aligned slot for status badges, refresh buttons, primary actions. */
  actions?: React.ReactNode;
  className?: string;
}

/** Compact instrument-panel page header: mono eyebrow over a display-face
 *  title, optional right-aligned status/actions, minimal dead vertical space. */
export function PageHeader({ eyebrow, title, actions, className }: PageHeaderProps) {
  return (
    <div className={cn("flex flex-wrap items-end justify-between gap-x-3 gap-y-2", className)}>
      <div className="min-w-0">
        <p className="panel-label text-muted-foreground">{eyebrow}</p>
        <h1 className="mt-0.5 text-xl sm:text-2xl font-semibold font-display tracking-tight leading-none text-foreground">
          {title}
        </h1>
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}
