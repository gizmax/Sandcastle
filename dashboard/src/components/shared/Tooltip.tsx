import { cn } from "@/lib/utils";

interface TooltipProps {
  content: string;
  children: React.ReactNode;
  className?: string;
}

export function Tooltip({ content, children, className }: TooltipProps) {
  if (!content) return <>{children}</>;

  return (
    <span className={cn("group/tip relative inline-flex min-w-0", className)}>
      {children}
      <span
        role="tooltip"
        className={cn(
          "pointer-events-none absolute bottom-full left-1/2 -translate-x-1/2 mb-2 z-50",
          "max-w-[300px] break-words rounded-md bg-foreground px-2 py-1 text-xs text-background shadow-lg",
          "opacity-0 transition-opacity duration-150 group-hover/tip:opacity-100"
        )}
      >
        {content}
        <span
          className="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-foreground"
          aria-hidden="true"
        />
      </span>
    </span>
  );
}
