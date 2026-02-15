import { useCallback, useState } from "react";
import { Outlet, useNavigate } from "react-router-dom";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import type { Notification } from "@/components/layout/NotificationCenter";
import { useKeyboardShortcuts } from "@/hooks/useKeyboardShortcuts";

const INITIAL_NOTIFICATIONS: Notification[] = [
  {
    id: "n1",
    type: "error",
    message: "Policy violation: secret-block triggered on step 'score'",
    timestamp: new Date(Date.now() - 12 * 60000),
    read: false,
    link: "/violations",
  },
  {
    id: "n2",
    type: "success",
    message: "Run lead-enrichment completed ($1.84)",
    timestamp: new Date(Date.now() - 35 * 60000),
    read: false,
    link: "/runs/a1b2c3d4-1111-4000-8000-000000000001",
  },
  {
    id: "n3",
    type: "info",
    message: "Optimizer switched step 'analyze' to sonnet (budget pressure 75%)",
    timestamp: new Date(Date.now() - 2 * 3600000),
    read: false,
    link: "/optimizer",
  },
  {
    id: "n4",
    type: "error",
    message: "Run competitor-monitor failed: Rate limit exceeded (429)",
    timestamp: new Date(Date.now() - 5 * 3600000),
    read: true,
    link: "/runs/a1b2c3d4-9999-4000-8000-000000000009",
  },
  {
    id: "n5",
    type: "info",
    message: "Approval pending: Review Q4 competitor analysis report",
    timestamp: new Date(Date.now() - 8 * 3600000),
    read: true,
    link: "/approvals",
  },
];

export function Layout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [notifications, setNotifications] = useState<Notification[]>(INITIAL_NOTIFICATIONS);
  const navigate = useNavigate();

  useKeyboardShortcuts();

  const handleMarkAllRead = useCallback(() => {
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
  }, []);

  const handleClickNotification = useCallback(
    (notification: Notification) => {
      setNotifications((prev) =>
        prev.map((n) => (n.id === notification.id ? { ...n, read: true } : n))
      );
      if (notification.link) {
        navigate(notification.link);
      }
    },
    [navigate]
  );

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header
          onMenuToggle={() => setSidebarOpen(!sidebarOpen)}
          notifications={notifications}
          onMarkAllRead={handleMarkAllRead}
          onClickNotification={handleClickNotification}
        />
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
