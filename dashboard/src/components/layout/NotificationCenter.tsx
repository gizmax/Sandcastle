import { useState } from "react";
import { Bell, CheckCircle, XCircle, Info } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatRelativeTime } from "@/lib/utils";

export interface Notification {
  id: string;
  type: "success" | "error" | "info";
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

const typeIcons = {
  success: CheckCircle,
  error: XCircle,
  info: Info,
};

const typeColors = {
  success: "text-success",
  error: "text-error",
  info: "text-running",
};

export function NotificationCenter({
  notifications,
  onMarkAllRead,
  onClickNotification,
}: NotificationCenterProps) {
  const [open, setOpen] = useState(false);
  const unreadCount = notifications.filter((n) => !n.read).length;

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className={cn(
          "relative flex h-9 w-9 items-center justify-center rounded-lg",
          "hover:bg-border/50 transition-all duration-200",
          "text-muted hover:text-foreground"
        )}
      >
        <Bell className="h-[18px] w-[18px]" />
        {unreadCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-error px-1 text-[10px] font-bold text-white">
            {unreadCount}
          </span>
        )}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div
            className={cn(
              "absolute right-0 top-full z-50 mt-2 w-[calc(100vw-2rem)] sm:w-80 max-w-sm rounded-xl border border-border bg-surface shadow-lg",
              "overflow-hidden"
            )}
          >
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <h3 className="text-sm font-semibold text-foreground">Notifications</h3>
              {unreadCount > 0 && (
                <button
                  onClick={onMarkAllRead}
                  className="text-xs text-accent hover:text-accent-hover"
                >
                  Mark all as read
                </button>
              )}
            </div>
            <div className="max-h-80 overflow-y-auto">
              {notifications.length === 0 ? (
                <p className="px-4 py-8 text-center text-sm text-muted">No notifications</p>
              ) : (
                notifications.map((n) => {
                  const Icon = typeIcons[n.type];
                  return (
                    <button
                      key={n.id}
                      onClick={() => {
                        onClickNotification(n);
                        setOpen(false);
                      }}
                      className={cn(
                        "flex w-full items-start gap-3 px-4 py-3 text-left",
                        "hover:bg-border/30 transition-colors duration-150",
                        !n.read && "bg-accent/5"
                      )}
                    >
                      <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", typeColors[n.type])} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm text-foreground">{n.message}</p>
                        <p className="mt-0.5 text-xs text-muted-foreground">
                          {formatRelativeTime(n.timestamp)}
                        </p>
                      </div>
                      {!n.read && (
                        <div className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-accent" />
                      )}
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
