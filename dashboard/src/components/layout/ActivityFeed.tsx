import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Bell,
  PlayCircle,
  CheckCircle,
  XCircle,
  ShieldCheck,
  Calendar,
  Key,
  GitBranch,
} from "lucide-react";
import { cn, formatRelativeTime } from "@/lib/utils";
import {
  useActivityFeed,
  type ActivityEvent,
  type ActivityEventType,
  type ActivityFilter,
} from "@/hooks/useActivityFeed";

const EVENT_ICONS: Record<ActivityEventType, React.ElementType> = {
  run_started: PlayCircle,
  run_completed: CheckCircle,
  run_failed: XCircle,
  approval_needed: ShieldCheck,
  schedule_fired: Calendar,
  key_created: Key,
  workflow_updated: GitBranch,
};

const EVENT_COLORS: Record<ActivityEventType, string> = {
  run_started: "text-blue-500",
  run_completed: "text-emerald-500",
  run_failed: "text-red-500",
  approval_needed: "text-amber-500",
  schedule_fired: "text-blue-400",
  key_created: "text-muted-foreground",
  workflow_updated: "text-purple-500",
};

const FILTERS: { key: ActivityFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "runs", label: "Runs" },
  { key: "system", label: "System" },
];

function EventItem({
  event,
  onClickEvent,
}: {
  event: ActivityEvent;
  onClickEvent: (event: ActivityEvent) => void;
}) {
  const Icon = EVENT_ICONS[event.type];
  const color = EVENT_COLORS[event.type];

  return (
    <button
      onClick={() => onClickEvent(event)}
      className={cn(
        "flex w-full items-start gap-3 px-4 py-3 text-left transition-colors duration-150",
        "hover:bg-border/20",
        !event.read && "border-l-2 border-l-accent bg-accent/5",
        event.read && "border-l-2 border-l-transparent",
      )}
    >
      <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", color)} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-foreground">{event.title}</p>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">{event.description}</p>
      </div>
      <span className="shrink-0 text-[10px] text-muted-foreground whitespace-nowrap mt-0.5">
        {formatRelativeTime(event.timestamp)}
      </span>
    </button>
  );
}

export function ActivityFeed() {
  const { unreadCount, markAsRead, markAllRead, filterEvents, newEventFlag, isDemo } = useActivityFeed();
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState<ActivityFilter>("all");
  const containerRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const [shaking, setShaking] = useState(false);
  const prevFlag = useRef(newEventFlag);

  useEffect(() => {
    if (newEventFlag !== prevFlag.current) {
      prevFlag.current = newEventFlag;
      setShaking(true);
      const timer = setTimeout(() => setShaking(false), 600);
      return () => clearTimeout(timer);
    }
  }, [newEventFlag]);

  useEffect(() => {
    if (!open) return;
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function handleEscape(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [open]);

  const handleClickEvent = (event: ActivityEvent) => {
    markAsRead(event.id);
    if (event.link) {
      navigate(event.link);
    }
    setOpen(false);
  };

  const filtered = filterEvents(filter);
  const badgeText = unreadCount > 9 ? "9+" : unreadCount.toString();

  return (
    <div className="relative" ref={containerRef}>
      <button
        onClick={() => setOpen(!open)}
        aria-label={`Activity feed${unreadCount > 0 ? ` (${unreadCount} unread)` : ""}`}
        aria-expanded={open}
        aria-haspopup="true"
        className={cn(
          "relative flex h-9 w-9 items-center justify-center rounded-lg",
          "hover:bg-border/50 transition-all duration-200",
          "text-muted hover:text-foreground",
          shaking && "animate-bell-shake",
        )}
      >
        <Bell className="h-[18px] w-[18px]" />
        {unreadCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold text-white">
            {badgeText}
          </span>
        )}
      </button>

      {open && (
        <div
          className={cn(
            "absolute right-0 top-full z-50 mt-2 w-96 max-h-[500px] rounded-xl border border-border bg-surface shadow-2xl",
            "overflow-hidden",
            "origin-top-right animate-scale-in",
          )}
        >
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-foreground">Activity</h3>
              {isDemo && (
                <span className="rounded-full bg-warning/15 px-1.5 py-0.5 text-[9px] font-semibold uppercase text-warning">
                  Demo
                </span>
              )}
            </div>
            {unreadCount > 0 && (
              <button
                onClick={markAllRead}
                className="text-[11px] font-medium text-accent hover:text-accent-hover transition-colors"
              >
                Mark all read
              </button>
            )}
          </div>

          <div className="flex items-center gap-1 border-b border-border px-3 py-2">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                onClick={() => setFilter(f.key)}
                className={cn(
                  "px-3 py-1.5 text-xs font-medium rounded-md transition-colors",
                  filter === f.key
                    ? "bg-border/60 text-foreground"
                    : "text-muted hover:text-foreground hover:bg-border/30",
                )}
              >
                {f.label}
              </button>
            ))}
          </div>

          <div className="max-h-[370px] overflow-y-auto" role="list" aria-label="Activity events">
            {filtered.length === 0 ? (
              <p className="px-4 py-8 text-center text-sm text-muted">No events</p>
            ) : (
              filtered.map((event) => (
                <EventItem key={event.id} event={event} onClickEvent={handleClickEvent} />
              ))
            )}
          </div>

          <div className="border-t border-border px-4 py-2">
            <button
              onClick={() => {
                navigate("/runs");
                setOpen(false);
              }}
              className="text-xs font-medium text-accent hover:text-accent-hover transition-colors"
            >
              View all activity
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
