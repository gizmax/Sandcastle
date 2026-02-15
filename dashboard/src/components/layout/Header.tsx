import { Menu, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "@/components/shared/ThemeToggle";
import {
  NotificationCenter,
  type Notification,
} from "@/components/layout/NotificationCenter";

interface HeaderProps {
  onMenuToggle: () => void;
  notifications: Notification[];
  onMarkAllRead: () => void;
  onClickNotification: (notification: Notification) => void;
}

export function Header({
  onMenuToggle,
  notifications,
  onMarkAllRead,
  onClickNotification,
}: HeaderProps) {
  return (
    <header
      className={cn(
        "sticky top-0 z-30 flex h-16 items-center gap-4 border-b border-border bg-surface/80 px-6 backdrop-blur-sm"
      )}
    >
      <button
        onClick={onMenuToggle}
        className="flex h-9 w-9 items-center justify-center rounded-lg text-muted hover:bg-border/50 hover:text-foreground lg:hidden"
      >
        <Menu className="h-5 w-5" />
      </button>

      <div className="relative hidden flex-1 sm:block">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <input
          type="text"
          placeholder="Search runs, workflows..."
          className={cn(
            "h-9 w-full max-w-sm rounded-lg border border-border bg-background pl-9 pr-3 text-sm",
            "placeholder:text-muted-foreground/50",
            "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30",
            "transition-all duration-200"
          )}
        />
      </div>

      <div className="ml-auto flex items-center gap-1">
        <ThemeToggle />
        <NotificationCenter
          notifications={notifications}
          onMarkAllRead={onMarkAllRead}
          onClickNotification={onClickNotification}
        />
      </div>
    </header>
  );
}
