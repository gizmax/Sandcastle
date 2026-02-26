import { Link } from "react-router-dom";
import {
  ServerCrash, Database, Inbox, ShieldAlert, TrendingDown,
  KeyRound, AlertTriangle, UserCheck, Clock, Shield,
  Coins, Sparkles, DollarSign, ArrowRight,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { ScoreRing } from "@/components/advisor/ScoreRing";
import { useAdvisor } from "@/hooks/useAdvisor";
import type { Insight, Severity } from "@/lib/insights";

const ICON_MAP: Record<string, React.ElementType> = {
  ServerCrash, Database, Inbox, ShieldAlert, TrendingDown,
  KeyRound, AlertTriangle, UserCheck, Clock, Shield,
  Coins, Sparkles, DollarSign,
};

const SEVERITY_DOT: Record<Severity, string> = {
  critical: "bg-error",
  warning: "bg-amber-500",
  optimize: "bg-accent",
  discover: "bg-violet-500",
};

function scoreLabel(score: number): { text: string; color: string } {
  if (score >= 80) return { text: "Healthy", color: "text-emerald-500" };
  if (score >= 50) return { text: "Needs Attention", color: "text-amber-500" };
  return { text: "Critical", color: "text-error" };
}

function InsightRow({ insight }: { insight: Insight }) {
  const Icon = ICON_MAP[insight.icon] ?? AlertTriangle;
  return (
    <Link
      to={insight.link}
      className="flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors hover:bg-border/30"
    >
      <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", SEVERITY_DOT[insight.severity])} />
      <Icon className="h-3.5 w-3.5 shrink-0 text-muted" />
      <span className="flex-1 text-foreground">{insight.title}</span>
      <ArrowRight className="h-3 w-3 text-muted" />
    </Link>
  );
}

export function HealthHero() {
  const { score, activeInsights, loading } = useAdvisor();

  // Only show actionable insights (not discover tier)
  const actionable = activeInsights.filter((i) => i.severity !== "discover");

  if (loading || actionable.length === 0) return null;

  const label = scoreLabel(score);

  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <div className="flex items-start gap-4">
        {/* Score */}
        <div className="flex flex-col items-center gap-1">
          <ScoreRing score={score} size="sm" />
          <span className={cn("text-xs font-medium", label.color)}>{label.text}</span>
        </div>

        {/* Insights */}
        <div className="min-w-0 flex-1">
          <h3 className="mb-1 text-sm font-medium text-foreground">
            {actionable.length} item{actionable.length > 1 ? "s" : ""} need{actionable.length === 1 ? "s" : ""} attention
          </h3>
          <div className="-mx-2.5">
            {actionable.slice(0, 4).map((insight) => (
              <InsightRow key={insight.id} insight={insight} />
            ))}
          </div>
          {actionable.length > 4 && (
            <p className="mt-1 px-2.5 text-xs text-muted">
              +{actionable.length - 4} more
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
