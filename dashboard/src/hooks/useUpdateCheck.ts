import { useEffect, useState } from "react";
import { api } from "@/api/client";

interface UpdateCheckData {
  current_version: string;
  latest_version: string;
  update_available: boolean;
  release_url: string;
  install_command: string;
}

export function useUpdateCheck() {
  const [data, setData] = useState<UpdateCheckData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let aborted = false;
    api
      .get<UpdateCheckData>("/check-update")
      .then((res) => {
        if (!aborted && res.data) setData(res.data);
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

  return {
    updateAvailable: data?.update_available ?? false,
    currentVersion: data?.current_version ?? null,
    latestVersion: data?.latest_version ?? null,
    installCommand: data?.install_command ?? null,
    releaseUrl: data?.release_url ?? null,
    loading,
  };
}
