import { useEffect, useState } from "react";
import {
  Bell, CheckCircle, XCircle, AlertTriangle, Info,
  TrendingUp, TrendingDown, Minus, RefreshCw, Lightbulb,
} from "lucide-react";
import { cn, formatRelativeTime } from "@/lib/utils";
import { ScoreRing } from "@/components/advisor/ScoreRing";
import { InsightCard } from "@/components/advisor/InsightCard";
import type { Insight, Deduction } from "@/lib/insights";
import {
  groupBySeverity, scoreLabel, scoreLabelColor,
  DEDUCTION_CATEGORY_COLORS, DEDUCTION_CATEGORY_LABELS, SEVERITY_LABELS,
} from "@/lib/insights";

export interface Notification {
  id: string;
  type: "success" | "error" | "warning" | "info";
  message: string;
  timestamp: Date;
  read: boolean;
  link?: string;
}

type Tab = "activity" | "health";

interface NotificationCenterProps {
  notifications: Notification[];
  onMarkAllRead: () => void;
  onClickNotification: (notification: Notification) => void;
  score: number;
  previousScore: number | null;
  deductions: Deduction[];
  insights: Insight[];
  advisorLoading: boolean;
  lastChecked: Date | null;
  onRefresh: () => void;
  onDismiss: (id: string) => void;
}

const typeIcons: Record<Notification["type"], React.ElementType> = {
  success: CheckCircle,
  error: XCircle,
  warning: AlertTriangle,
  info: Info,
};

const typeColors: Record<Notification["type"], string> = {
  success: "text-success",
  error: "text-error",
  warning: "text-warning",
  info: "text-running",
};

const typeDotColors: Record<Notification["type"], string> = {
  success: "bg-success",
  error: "bg-error",
  warning: "bg-warning",
  info: "bg-running",
};

const typeBorderColors: Record<Notification["type"], string> = {
  success: "border-l-success",
  error: "border-l-error",
  warning: "border-l-warning",
  info: "border-l-running",
};

function badgeColor(insights: Insight[], unreadCount: number): string {
  const hasCritical = insights.some((i) => i.severity === "critical");
  const hasWarning = insights.some((i) => i.severity === "warning");
  if (hasCritical) return "bg-error";
  if (hasWarning) return "bg-amber-500";
  if (unreadCount > 0) return "bg-accent";
  return "";
}

function TabButton({
  active,
  onClick,
  label,
  count,
  countColor,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  count: number;
  countColor?: string;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md transition-colors",
        active
          ? "bg-border/60 text-foreground"
          : "text-muted hover:text-foreground hover:bg-border/30",
      )}
    >
      {label}
      {count > 0 && (
        <span
          className={cn(
            "flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-bold text-white",
            countColor ?? "bg-muted-foreground",
          )}
        >
          {count > 99 ? "99+" : count}
        </span>
      )}
    </button>
  );
}

export function NotificationCenter({
  notifications,
  onMarkAllRead,
  onClickNotification,
  score,
  previousScore,
  deductions,
  insights,
  advisorLoading,
  lastChecked,
  onRefresh,
  onDismiss,
}: NotificationCenterProps) {
  const [open, setOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<Tab>("activity");
  const unreadCount = notifications.filter((n) => !n.read).length;
  const hasCritical = insights.some((i) => i.severity === "critical");
  const hasWarning = insights.some((i) => i.severity === "warning");
  const totalBadge = unreadCount + insights.length;
  const showMicroRing = score < 80 || insights.length > 0;

  // Auto-switch to health tab on critical insights
  useEffect(() => {
    if (hasCritical && open) {
      setActiveTab("health");
    }
  }, [hasCritical, open]);

  // Close on Escape key
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open]);

  const insightCountColor = hasCritical ? "bg-error" : hasWarning ? "bg-amber-500" : "bg-muted-foreground";

  const trend = previousScore != null ? score - previousScore : 0;
  const totalDeducted = deductions.reduce((sum, d) => sum + d.points, 0);
  const categoryTotals = deductions.reduce(
    (acc, d) => {
      acc[d.category] = (acc[d.category] ?? 0) + d.points;
      return acc;
    },
    {} as Record<string, number>,
  );
  const grouped = groupBySeverity(insights);

  return (
    <div className="relative">
      {/* Bell button with composite badge */}
      <button
        onClick={() => setOpen(!open)}
        aria-label={`Command center${totalBadge > 0 ? ` (${totalBadge} items)` : ""}`}
        aria-expanded={open}
        aria-haspopup="true"
        className={cn(
          "relative flex h-9 w-9 items-center justify-center rounded-lg",
          "hover:bg-border/50 transition-all duration-200",
          "text-muted hover:text-foreground",
          hasCritical && "bell-pulse-critical",
        )}
      >
        <Bell className="h-[18px] w-[18px]" />

        {/* Combined badge (top-right) */}
        {totalBadge > 0 && (
          <span
            className={cn(
              "absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-bold text-white",
              badgeColor(insights, unreadCount),
            )}
          >
            {totalBadge > 9 ? "9+" : totalBadge}
          </span>
        )}

        {/* Micro ScoreRing (bottom-left) */}
        {showMicroRing && (
          <span className="absolute -bottom-1 -left-1">
            <ScoreRing score={score} size="xs" />
          </span>
        )}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div
            className={cn(
              "absolute right-0 top-full z-50 mt-2 w-[calc(100vw-2rem)] sm:w-96 max-w-md rounded-xl border border-border bg-surface shadow-lg",
              "overflow-hidden",
            )}
          >
            {/* Tab bar */}
            <div className="flex items-center gap-1 border-b border-border px-3 py-2">
              <TabButton
                active={activeTab === "activity"}
                onClick={() => setActiveTab("activity")}
                label="Activity"
                count={unreadCount}
                countColor="bg-accent"
              />
              <TabButton
                active={activeTab === "health"}
                onClick={() => setActiveTab("health")}
                label="Health"
                count={insights.length}
                countColor={insightCountColor}
              />
              <div className="ml-auto">
                {activeTab === "activity" && unreadCount > 0 && (
                  <button
                    onClick={onMarkAllRead}
                    className="text-[11px] text-accent hover:text-accent-hover"
                  >
                    Mark all read
                  </button>
                )}
                {activeTab === "health" && (
                  <ScoreRing score={score} size="xs" />
                )}
              </div>
            </div>

            {/* Activity tab */}
            {activeTab === "activity" && (
              <div className="max-h-80 overflow-y-auto" role="list" aria-label="Notification list">
                {notifications.length === 0 ? (
                  <p className="px-4 py-8 text-center text-sm text-muted">No notifications</p>
                ) : (
                  notifications.map((n) => {
                    const Icon = typeIcons[n.type];
                    return (
                      <button
                        key={n.id}
                        role="listitem"
                        onClick={() => {
                          onClickNotification(n);
                          setOpen(false);
                        }}
                        className={cn(
                          "flex w-full items-start gap-3 pl-3 pr-4 py-3 text-left",
                          "border-l-2",
                          typeBorderColors[n.type],
                          "hover:bg-border/30 transition-colors duration-150",
                          !n.read && "bg-accent/5",
                        )}
                      >
                        <span className={cn(
                          "mt-1.5 h-2 w-2 shrink-0 rounded-full",
                          typeDotColors[n.type],
                          n.read && "opacity-40"
                        )} />
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm text-foreground">{n.message}</p>
                          <p className="mt-0.5 text-xs text-muted-foreground">
                            {formatRelativeTime(n.timestamp)}
                          </p>
                        </div>
                        <div className="flex flex-col items-end gap-1 shrink-0">
                          <Icon className={cn("h-3.5 w-3.5", typeColors[n.type])} />
                          {!n.read && (
                            <div className="h-1.5 w-1.5 rounded-full bg-accent" />
                          )}
                        </div>
                      </button>
                    );
                  })
                )}
              </div>
            )}

            {/* Health tab */}
            {activeTab === "health" && (
              <div className="max-h-[28rem] overflow-y-auto">
                <div className="px-4 py-4 space-y-4">
                  {/* Score hero */}
                  <div className="flex flex-col items-center gap-2">
                    <ScoreRing score={score} size="md" />
                    <div className="flex items-center gap-2">
                      {trend !== 0 && (
                        <span className={cn("flex items-center gap-0.5 text-xs font-medium", trend > 0 ? "text-emerald-500" : "text-error")}>
                          {trend > 0 ? <TrendingUp className="h-3.5 w-3.5" /> : <TrendingDown className="h-3.5 w-3.5" />}
                          {trend > 0 ? "+" : ""}{trend}
                        </span>
                      )}
                      {trend === 0 && previousScore != null && (
                        <span className="flex items-center gap-0.5 text-xs font-medium text-muted">
                          <Minus className="h-3.5 w-3.5" />
                          No change
                        </span>
                      )}
                      <span className={cn("text-sm font-semibold", scoreLabelColor(score))}>
                        {scoreLabel(score)}
                      </span>
                    </div>
                  </div>

                  {/* Deduction bar */}
                  {totalDeducted > 0 && (
                    <div className="space-y-1.5">
                      <div className="flex h-2 w-full overflow-hidden rounded-full bg-border">
                        {(Object.keys(DEDUCTION_CATEGORY_COLORS) as Deduction["category"][]).map((cat) => {
                          const pts = categoryTotals[cat] ?? 0;
                          if (pts === 0) return null;
                          const pct = (pts / 100) * 100;
                          return (
                            <div
                              key={cat}
                              className={cn("h-full transition-all duration-500", DEDUCTION_CATEGORY_COLORS[cat])}
                              style={{ width: `${pct}%` }}
                              title={`${DEDUCTION_CATEGORY_LABELS[cat]}: -${pts}`}
                            />
                          );
                        })}
                        <div
                          className="h-full bg-emerald-500 transition-all duration-500"
                          style={{ width: `${score}%` }}
                          title={`Remaining: ${score}`}
                        />
                      </div>
                      <div className="flex flex-wrap gap-x-3 gap-y-0.5">
                        {(Object.keys(DEDUCTION_CATEGORY_COLORS) as Deduction["category"][]).map((cat) => {
                          const pts = categoryTotals[cat] ?? 0;
                          if (pts === 0) return null;
                          return (
                            <div key={cat} className="flex items-center gap-1 text-[10px] text-muted">
                              <span className={cn("h-1.5 w-1.5 rounded-full", DEDUCTION_CATEGORY_COLORS[cat])} />
                              {DEDUCTION_CATEGORY_LABELS[cat]} <span className="text-foreground font-medium">-{pts}</span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {/* Insights */}
                  {advisorLoading ? (
                    <div className="flex items-center justify-center py-8">
                      <div className="h-5 w-5 animate-spin rounded-full border-2 border-border border-t-accent" />
                    </div>
                  ) : insights.length === 0 ? (
                    <div className="flex flex-col items-center gap-1.5 py-8 text-center">
                      <div className="flex h-10 w-10 items-center justify-center rounded-full bg-emerald-500/10">
                        <Lightbulb className="h-5 w-5 text-emerald-500" />
                      </div>
                      <p className="text-sm font-medium text-foreground">All clear</p>
                      <p className="text-xs text-muted">No issues found.</p>
                    </div>
                  ) : (
                    <div className="space-y-3">
                      {grouped.map(([severity, items]) => (
                        <div key={severity} className="space-y-1.5">
                          <p className="text-[10px] font-medium text-muted uppercase tracking-wider">
                            {SEVERITY_LABELS[severity]} ({items.length})
                          </p>
                          <div className="space-y-1.5">
                            {items.map((insight) => (
                              <InsightCard key={insight.id} insight={insight} onDismiss={onDismiss} />
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Refresh footer */}
                <div className="flex items-center justify-between border-t border-border px-4 py-2">
                  <p className="text-[10px] text-muted">
                    {lastChecked ? `Checked ${formatRelativeTime(lastChecked)}` : "Checking..."}
                  </p>
                  <button
                    onClick={onRefresh}
                    disabled={advisorLoading}
                    className={cn(
                      "flex items-center gap-1 rounded-md px-2 py-1 text-[10px] font-medium",
                      "text-muted hover:text-foreground hover:bg-border/40 transition-colors",
                      "disabled:opacity-50",
                    )}
                  >
                    <RefreshCw className={cn("h-3 w-3", advisorLoading && "animate-spin")} />
                    Refresh
                  </button>
                </div>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
