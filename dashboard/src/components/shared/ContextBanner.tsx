import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";

type Variant = "error" | "warning" | "info";

const VARIANT_STYLES: Record<Variant, { border: string; bg: string; text: string; icon: string }> = {
  error: { border: "border-l-error", bg: "bg-error/5", text: "text-error", icon: "bg-error/10 text-error" },
  warning: { border: "border-l-amber-500", bg: "bg-amber-500/5", text: "text-amber-500", icon: "bg-amber-500/10 text-amber-500" },
  info: { border: "border-l-accent", bg: "bg-accent/5", text: "text-accent", icon: "bg-accent/10 text-accent" },
};

interface ContextBannerProps {
  variant: Variant;
  icon: React.ElementType;
  children: React.ReactNode;
  link?: { to: string; label: string };
  className?: string;
}

export function ContextBanner({ variant, icon: Icon, children, link, className }: ContextBannerProps) {
  const s = VARIANT_STYLES[variant];

  return (
    <div
      role={variant === "error" || variant === "warning" ? "alert" : undefined}
      className={cn(
        "flex items-center gap-3 rounded-lg border border-border bg-surface px-3 py-2.5",
        "border-l-[3px]", s.border, s.bg,
        className,
      )}
    >
      <div className={cn("flex h-7 w-7 shrink-0 items-center justify-center rounded-md", s.icon)}>
        <Icon className="h-3.5 w-3.5" />
      </div>
      <p className="flex-1 text-sm text-foreground">{children}</p>
      {link && (
        <Link
          to={link.to}
          className={cn("flex shrink-0 items-center gap-1 text-xs font-medium transition-colors", s.text)}
        >
          {link.label} <ArrowRight className="h-3 w-3" />
        </Link>
      )}
    </div>
  );
}
