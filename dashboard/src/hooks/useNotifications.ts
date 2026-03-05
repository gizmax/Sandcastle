import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "sandcastle-notifications-enabled";

interface RunInfo {
  run_id: string;
  workflow_name: string;
  status: string;
  total_cost_usd?: number;
}

type PermissionState = "default" | "granted" | "denied";

function getStoredPreference(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

function setStoredPreference(enabled: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, String(enabled));
  } catch {
    // localStorage unavailable
  }
}

function getBrowserPermission(): PermissionState {
  if (typeof Notification === "undefined") return "denied";
  return Notification.permission as PermissionState;
}

export function useNotifications() {
  const [permission, setPermission] = useState<PermissionState>(getBrowserPermission);
  const [notificationsEnabled, setNotificationsEnabled] = useState(getStoredPreference);

  // Sync permission state on mount
  useEffect(() => {
    setPermission(getBrowserPermission());
  }, []);

  const requestPermission = useCallback(async () => {
    if (typeof Notification === "undefined") return;
    const result = await Notification.requestPermission();
    setPermission(result as PermissionState);
    if (result === "granted") {
      setNotificationsEnabled(true);
      setStoredPreference(true);
    }
  }, []);

  const toggleNotifications = useCallback(() => {
    setNotificationsEnabled((prev) => {
      const next = !prev;
      setStoredPreference(next);
      return next;
    });
  }, []);

  const notifyRunComplete = useCallback(
    (run: RunInfo) => {
      if (!notificationsEnabled) return;
      if (permission !== "granted") return;
      if (typeof Notification === "undefined") return;

      const isFailed = run.status === "failed";
      const title = isFailed ? "Run Failed" : "Run Completed";
      const costStr =
        run.total_cost_usd != null && Number.isFinite(run.total_cost_usd)
          ? ` - Cost: $${run.total_cost_usd.toFixed(2)}`
          : "";
      const body = `Workflow: ${run.workflow_name}${costStr}`;

      const basePath = import.meta.env.BASE_URL ?? "/";
      const iconUrl = `${basePath}sandcastle-logo.svg`.replace(/\/\//g, "/");

      const notification = new Notification(title, {
        body,
        icon: iconUrl,
        tag: `run-${run.run_id}`,
      });

      notification.onclick = () => {
        window.focus();
        const targetPath = `${basePath}runs/${run.run_id}`.replace(/\/\//g, "/");
        window.location.href = targetPath;
        notification.close();
      };
    },
    [notificationsEnabled, permission],
  );

  return {
    permission,
    notificationsEnabled,
    requestPermission,
    toggleNotifications,
    notifyRunComplete,
  };
}
