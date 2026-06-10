import { useCallback, useEffect, useMemo, useState } from "react";
import { Outlet, useNavigate, useLocation } from "react-router-dom";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { CommandPalette } from "@/components/layout/CommandPalette";
import type { Notification } from "@/components/layout/NotificationCenter";
import { useKeyboardShortcuts } from "@/hooks/useKeyboardShortcuts";
import { useRecentItems } from "@/hooks/useRecentItems";
import { useEventStreamContext } from "@/hooks/useEventStreamContext";
import { ToastContainer } from "@/components/shared/ToastContainer";
import { ShortcutHelpModal } from "@/components/shared/ShortcutHelpModal";
import { useToasts } from "@/hooks/useToasts";
import type { StreamEvent } from "@/hooks/useEventStream";
import { api } from "@/api/client";
import { AdvisorProvider } from "@/components/providers/AdvisorProvider";

const DEMO_NOTIFICATIONS: () => Notification[] = () => {
  const now = Date.now();
  return [
    { id: "demo-1", type: "success", message: "data-enrichment completed in 12.4s", timestamp: new Date(now - 2 * 60_000), read: false, link: "/runs" },
    { id: "demo-2", type: "error", message: "content-moderation failed: sandbox timeout", timestamp: new Date(now - 8 * 60_000), read: false, link: "/runs?status=failed" },
    { id: "demo-3", type: "success", message: "weekly-report completed in 45.1s", timestamp: new Date(now - 22 * 60_000), read: true, link: "/runs" },
    { id: "demo-4", type: "warning", message: "Approval needed: deploy-staging step gate", timestamp: new Date(now - 35 * 60_000), read: true, link: "/approvals" },
    { id: "demo-5", type: "success", message: "ticket-triage completed in 8.7s", timestamp: new Date(now - 55 * 60_000), read: true, link: "/runs" },
    { id: "demo-6", type: "info", message: "Schedule daily-digest fired", timestamp: new Date(now - 90 * 60_000), read: true, link: "/schedules" },
  ];
};

// Map SSE event types to notification types
function eventToNotificationType(eventType: string): Notification["type"] {
  switch (eventType) {
    case "run.completed":
    case "step.completed":
      return "success";
    case "run.failed":
    case "step.failed":
      return "error";
    case "dlq.new":
      return "warning";
    default:
      return "info";
  }
}

// Build a human-readable message from an SSE event
function eventToMessage(event: StreamEvent): string {
  const workflow = (event.data.workflow as string) ?? "";
  const runId = (event.data.run_id as string) ?? "";
  const shortId = runId.slice(0, 8);
  const stepName = (event.data.step_name as string) ?? "";

  switch (event.type) {
    case "run.completed": {
      const durationMs = event.data.duration_ms as number | undefined;
      const dur = durationMs != null ? ` (${(durationMs / 1000).toFixed(1)}s)` : "";
      return `Run ${workflow || shortId} completed${dur}`;
    }
    case "run.failed":
      return `Run ${workflow || shortId} failed`;
    case "run.started":
      return `Run ${workflow || shortId} started`;
    case "step.completed":
      return `Step '${stepName}' completed on run ${shortId}`;
    case "step.failed":
      return `Step '${stepName}' failed on run ${shortId}`;
    case "dlq.new":
      return `Dead letter: step '${stepName}' on run ${shortId}`;
    default:
      return `Event: ${event.type}`;
  }
}

// Build a link for the notification
function eventToLink(event: StreamEvent): string | undefined {
  const runId = event.data.run_id as string | undefined;
  if (event.type === "dlq.new") return "/dead-letter";
  if (runId) return `/runs/${runId}`;
  return undefined;
}

export function Layout() {
  return (
    <AdvisorProvider>
      <LayoutInner />
    </AdvisorProvider>
  );
}

// Map pathname to label for recent items tracking
const ROUTE_LABELS: Record<string, string> = {
  "/": "Overview",
  "/runs": "Runs",
  "/workflows": "Workflows",
  "/templates": "Template Hub",
  "/integrations": "Integrations",
  "/approvals": "Approvals",
  "/evaluations": "Evaluations",
  "/autopilot": "AutoPilot",
  "/violations": "Violations",
  "/optimizer": "Optimizer",
  "/time-machine": "Time Machine",
  "/schedules": "Schedules",
  "/dead-letter": "Dead Letter",
  "/api-keys": "API Keys",
  "/settings": "Settings",
  "/system-health": "System Health",
};

function LayoutInner() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [dlqCount, setDlqCount] = useState(0);
  const [approvalsCount, setApprovalsCount] = useState(0);
  const [isDemo, setIsDemo] = useState(api.isMockMode);
  const navigate = useNavigate();
  const location = useLocation();
  const { subscribe } = useEventStreamContext();
  const { toasts, addToast, dismissToast } = useToasts();
  const { recentItems, addRecentItem } = useRecentItems();

  const shortcutOptions = useMemo(
    () => ({ onCommandPalette: () => setPaletteOpen(true) }),
    []
  );
  useKeyboardShortcuts(shortcutOptions);

  // Track page visits for recent items
  useEffect(() => {
    const pathname = location.pathname;
    if (pathname.startsWith("/runs/") && pathname !== "/runs/compare") {
      const runId = pathname.split("/")[2];
      addRecentItem({ type: "run", id: pathname, label: `Run ${runId.slice(0, 8)}` });
    } else if (pathname.startsWith("/workflows/") && pathname !== "/workflows/builder") {
      const name = decodeURIComponent(pathname.split("/")[2]);
      addRecentItem({ type: "workflow", id: pathname, label: name });
    } else if (ROUTE_LABELS[pathname]) {
      addRecentItem({ type: "page", id: pathname, label: ROUTE_LABELS[pathname] });
    }
  }, [location.pathname, addRecentItem]);

  // Track mock mode changes
  useEffect(() => api.onMockChange(setIsDemo), []);

  // Seed demo notifications when entering mock mode
  useEffect(() => {
    if (isDemo) {
      setNotifications((prev) => prev.length === 0 ? DEMO_NOTIFICATIONS() : prev);
    }
  }, [isDemo]);

  // Fetch pending approvals count on mount
  useEffect(() => {
    async function fetchApprovalsCount() {
      try {
        const res = await api.get<unknown[]>("/approvals", { status: "pending" });
        if (res.data && Array.isArray(res.data)) {
          setApprovalsCount(res.data.length);
        } else if (res.meta?.total != null) {
          setApprovalsCount(res.meta.total);
        }
      } catch {
        // Ignore - approvals badge is non-critical
      }
    }
    void fetchApprovalsCount();
  }, []);

  // Subscribe to all events for notifications
  useEffect(() => {
    const unsubscribe = subscribe("*", (event: StreamEvent) => {
      // Add to notification center
      const notification: Notification = {
        id: event.id,
        type: eventToNotificationType(event.type),
        message: eventToMessage(event),
        timestamp: event.timestamp,
        read: false,
        link: eventToLink(event),
      };
      setNotifications((prev) => {
        const updated = [notification, ...prev];
        // Keep max 50 notifications
        if (updated.length > 50) return updated.slice(0, 50);
        return updated;
      });

      // Show toast for important events
      if (event.type === "run.completed") {
        addToast("success", eventToMessage(event));
      } else if (event.type === "run.failed") {
        addToast("error", eventToMessage(event));
      } else if (event.type === "dlq.new") {
        addToast("warning", eventToMessage(event));
      }

      // Increment DLQ badge on new dead letter events
      if (event.type === "dlq.new") {
        setDlqCount((prev) => prev + 1);
      }

      // Update approvals badge
      if (event.type === "approval.requested") {
        setApprovalsCount((prev) => prev + 1);
      } else if (event.type === "approval.resolved") {
        setApprovalsCount((prev) => Math.max(0, prev - 1));
      }
    });

    return unsubscribe;
  }, [subscribe, addToast]);

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
      <a href="#main-content" className="sr-only focus-visible:not-sr-only focus-visible:absolute focus-visible:z-[100] focus-visible:top-2 focus-visible:left-2 focus-visible:bg-accent focus-visible:text-accent-foreground focus-visible:px-4 focus-visible:py-2 focus-visible:rounded-lg focus-visible:text-sm focus-visible:font-medium">
        Skip to content
      </a>
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} dlqCount={dlqCount} approvalsCount={approvalsCount} />
      <div className="flex flex-1 flex-col overflow-hidden">
        {isDemo && (
          <div className="flex items-center justify-center gap-2 bg-warning/15 border-b border-warning/30 px-3 py-1.5 text-xs font-medium text-warning">
            Demo Mode - Backend unavailable, showing sample data.
            Run <code className="mx-1 rounded bg-warning/10 px-1.5 py-0.5 font-mono">sandcastle serve</code> to connect.
          </div>
        )}
        <Header
          onMenuToggle={() => setSidebarOpen(!sidebarOpen)}
          onOpenPalette={() => setPaletteOpen(true)}
          notifications={notifications}
          onMarkAllRead={handleMarkAllRead}
          onClickNotification={handleClickNotification}
        />
        <main id="main-content" className="flex-1 overflow-x-hidden overflow-y-auto p-3 sm:p-4 lg:p-6">
          <Outlet />
        </main>
      </div>
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        recentItems={recentItems}
      />
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
      <ShortcutHelpModal />
    </div>
  );
}
