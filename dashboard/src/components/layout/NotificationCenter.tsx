import { useEffect, useState } from "react";
import {
  Bell, CheckCircle, XCircle, AlertTriangle, Info,
} from "lucide-react";
import { cn, formatRelativeTime } from "@/lib/utils";

export interface Notification {
  id: string;
  type: "success" | "error" | "warning" | "info";
  message: string;
  timestamp: Date;
  read: boolean;
  link?: string;
}

interface NotificationCenterProps {
  notifications: Notification[];
  onMarkAllRead: () => void;
  onClickNotification: (notification: Notification) => void;
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

export function NotificationCenter({
  notifications,
  onMarkAllRead,
  onClickNotification,
}: NotificationCenterProps) {
  const [open, setOpen] = useState(false);
  const unreadCount = notifications.filter((n) => !n.read).length;

  // Close on Escape key
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open]);

  return (
    <div className="relative">
      {/* Bell button */}
      <button
        onClick={() => setOpen(!open)}
        aria-label={`Command center${unreadCount > 0 ? ` (${unreadCount} items)` : ""}`}
        aria-expanded={open}
        aria-haspopup="true"
        className={cn(
          "relative flex h-9 w-9 items-center justify-center rounded-lg",
          "hover:bg-border/50 transition-all duration-200",
          "text-muted hover:text-foreground",
        )}
      >
        <Bell className="h-[18px] w-[18px]" />

        {/* Unread badge (top-right) */}
        {unreadCount > 0 && (
          <span
            className={cn(
              "absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-bold text-white",
              "bg-accent",
            )}
          >
            {unreadCount > 9 ? "9+" : unreadCount}
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
            {/* Header */}
            <div className="flex items-center border-b border-border px-4 py-2.5">
              <span className="text-xs font-semibold text-foreground">Activity</span>
              {unreadCount > 0 && (
                <span className="ml-2 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[10px] font-bold text-white">
                  {unreadCount}
                </span>
              )}
              <div className="ml-auto">
                {unreadCount > 0 && (
                  <button
                    onClick={onMarkAllRead}
                    className="text-[11px] text-accent hover:text-accent-hover"
                  >
                    Mark all read
                  </button>
                )}
              </div>
            </div>

            {/* Notification list */}
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
          </div>
        </>
      )}
    </div>
  );
}
