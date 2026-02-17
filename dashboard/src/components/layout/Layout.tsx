import { useCallback, useEffect, useState } from "react";
import { Outlet, useNavigate } from "react-router-dom";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import type { Notification } from "@/components/layout/NotificationCenter";
import { useKeyboardShortcuts } from "@/hooks/useKeyboardShortcuts";
import { useEventStreamContext } from "@/hooks/useEventStreamContext";
import { ToastContainer } from "@/components/shared/ToastContainer";
import { useToasts } from "@/hooks/useToasts";
import type { StreamEvent } from "@/hooks/useEventStream";
import { api } from "@/api/client";

const INITIAL_NOTIFICATIONS: Notification[] = [];

// Map SSE event types to notification types
function eventToNotificationType(eventType: string): Notification["type"] {
  switch (eventType) {
    case "run.completed":
    case "step.completed":
      return "success";
    case "run.failed":
    case "step.failed":
    case "dlq.new":
      return "error";
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
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [notifications, setNotifications] = useState<Notification[]>(INITIAL_NOTIFICATIONS);
  const [dlqCount, setDlqCount] = useState(0);
  const [isDemo, setIsDemo] = useState(api.isMockMode);
  const navigate = useNavigate();
  const { subscribe } = useEventStreamContext();
  const { toasts, addToast, dismissToast } = useToasts();

  useKeyboardShortcuts();

  // Track mock mode changes
  useEffect(() => api.onMockChange(setIsDemo), []);

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
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} dlqCount={dlqCount} />
      <div className="flex flex-1 flex-col overflow-hidden">
        {isDemo && (
          <div className="flex items-center justify-center gap-2 bg-warning/15 border-b border-warning/30 px-3 py-1.5 text-xs font-medium text-warning">
            Demo Mode - Backend unavailable, showing sample data.
            Run <code className="mx-1 rounded bg-warning/10 px-1.5 py-0.5 font-mono">sandcastle serve</code> to connect.
          </div>
        )}
        <Header
          onMenuToggle={() => setSidebarOpen(!sidebarOpen)}
          notifications={notifications}
          onMarkAllRead={handleMarkAllRead}
          onClickNotification={handleClickNotification}
        />
        <main className="flex-1 overflow-x-hidden overflow-y-auto p-3 sm:p-4 lg:p-6">
          <Outlet />
        </main>
      </div>
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
