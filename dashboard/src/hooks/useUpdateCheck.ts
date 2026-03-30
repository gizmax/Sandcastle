import { useCallback, useEffect, useState } from "react";
import { api } from "@/api/client";

interface UpdateCheckData {
  current_version: string;
  latest_version: string;
  update_available: boolean;
  release_url: string;
  install_command: string;
  changelog_url?: string;
  highlights?: string[];
}

interface UpdateResponse {
  status: string;
  new_version: string;
  previous_version: string;
  restart_required: boolean;
  error?: string;
}

interface RollbackResponse {
  status: string;
  rolled_back_to: string;
  previous_version: string;
  restart_required: boolean;
  error?: string;
}

export type UpdateStatus = "idle" | "updating" | "success" | "error";

export function useUpdateCheck() {
  const [data, setData] = useState<UpdateCheckData | null>(null);
  const [loading, setLoading] = useState(true);
  const [updateStatus, setUpdateStatus] = useState<UpdateStatus>("idle");
  const [updateError, setUpdateError] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<string | null>(null);

  useEffect(() => {
    let aborted = false;
    api
      .get<UpdateCheckData>("/check-update")
      .then((res) => {
        if (!aborted && res.data) {
          setData(res.data);
          setLastChecked(new Date().toISOString());
        }
      })
      .catch(() => {
        // Network error - ignore if aborted
      })
      .finally(() => {
        if (!aborted) setLoading(false);
      });
    return () => {
      aborted = true;
    };
  }, []);

  // Trigger a software update via POST /admin/update
  const triggerUpdate = useCallback(async () => {
    setUpdateStatus("updating");
    setUpdateError(null);
    try {
      const res = await api.post<UpdateResponse>("/admin/update");
      if (res.data && res.data.status === "success") {
        setUpdateStatus("success");
        // Update local data to reflect the new version
        setData((prev) =>
          prev
            ? {
                ...prev,
                current_version: res.data!.new_version,
                update_available: false,
              }
            : prev
        );
      } else {
        // Prefer the error field from UpdateResponse (backend-provided), then
        // fall back to the API client error wrapper, then a generic message
        const msg = res.data?.error || res.error?.message || "Update failed";
        setUpdateStatus("error");
        setUpdateError(msg);
      }
    } catch {
      setUpdateStatus("error");
      setUpdateError("Network error during update");
    }
  }, []);

  // Trigger a rollback via POST /admin/rollback
  const triggerRollback = useCallback(async () => {
    setUpdateStatus("updating");
    setUpdateError(null);
    try {
      const res = await api.post<RollbackResponse>("/admin/rollback");
      if (res.data && res.data.status === "success") {
        setUpdateStatus("idle");
        setData((prev) =>
          prev
            ? {
                ...prev,
                current_version: res.data!.rolled_back_to,
                update_available: true,
              }
            : prev
        );
      } else {
        const msg = res.data?.error || res.error?.message || "Rollback failed";
        setUpdateStatus("error");
        setUpdateError(msg);
      }
    } catch {
      setUpdateStatus("error");
      setUpdateError("Network error during rollback");
    }
  }, []);

  return {
    updateAvailable: data?.update_available ?? false,
    currentVersion: data?.current_version ?? null,
    latestVersion: data?.latest_version ?? null,
    installCommand: data?.install_command ?? null,
    releaseUrl: data?.release_url ?? null,
    changelogUrl: data?.changelog_url ?? null,
    highlights: data?.highlights ?? [],
    loading,
    lastChecked,
    updateStatus,
    updateError,
    triggerUpdate,
    triggerRollback,
  };
}
