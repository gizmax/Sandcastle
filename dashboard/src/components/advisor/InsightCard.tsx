import { Link } from "react-router-dom";
import {
  ServerCrash, Database, Inbox, ShieldAlert, TrendingDown,
  KeyRound, AlertTriangle, UserCheck, Clock, Shield,
  Coins, Sparkles, DollarSign, CalendarOff, Calendar,
  FlaskConical, Wand2, Gauge, X, ArrowRight,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { Insight, Severity } from "@/lib/insights";

const ICON_MAP: Record<string, React.ElementType> = {
  ServerCrash, Database, Inbox, ShieldAlert, TrendingDown,
  KeyRound, AlertTriangle, UserCheck, Clock, Shield,
  Coins, Sparkles, DollarSign, CalendarOff, Calendar,
  FlaskConical, Wand2, Gauge,
};

const SEVERITY_STYLES: Record<Severity, { border: string; bg: string; text: string }> = {
  critical: { border: "border-l-error", bg: "bg-error/10", text: "text-error" },
  warning: { border: "border-l-amber-500", bg: "bg-amber-500/10", text: "text-amber-500" },
  optimize: { border: "border-l-accent", bg: "bg-accent/10", text: "text-accent" },
  discover: { border: "border-l-violet-500", bg: "bg-violet-500/10", text: "text-violet-500" },
};

interface InsightCardProps {
  insight: Insight;
  onDismiss: (id: string) => void;
}

export function InsightCard({ insight, onDismiss }: InsightCardProps) {
  const style = SEVERITY_STYLES[insight.severity];
  const Icon = ICON_MAP[insight.icon] ?? AlertTriangle;

  return (
    <div className={cn(
      "group flex items-start gap-3 rounded-xl border border-border bg-surface p-3",
      "border-l-[3px]", style.border,
    )}>
      <div className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-lg", style.bg)}>
        <Icon className={cn("h-4 w-4", style.text)} />
      </div>

      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-foreground">{insight.title}</p>
        <p className="mt-0.5 text-xs text-muted leading-relaxed">{insight.description}</p>
        <Link
          to={insight.link}
          className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium text-accent hover:text-accent-hover transition-colors"
        >
          View <ArrowRight className="h-3 w-3" />
        </Link>
      </div>

      <button
        onClick={() => onDismiss(insight.id)}
        className="shrink-0 rounded-md p-1 text-muted opacity-0 transition-all hover:bg-border/40 hover:text-foreground group-hover:opacity-100"
        title="Dismiss"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
