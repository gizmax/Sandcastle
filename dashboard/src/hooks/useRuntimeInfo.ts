import { useEffect, useState } from "react";
import { api } from "@/api/client";

export interface LicenseInfo {
  status: "valid" | "expired" | "invalid" | "missing";
  tier: "community" | "pro" | "enterprise";
  licensee: string;
  max_seats: number;
  expires: string;
}

export interface RuntimeInfo {
  mode: "local" | "production";
  database: string;
  queue: string;
  storage: string;
  data_dir: string | null;
  version: string | null;
  sandbox_backend: "e2b" | "docker" | "local" | "cloudflare";
  license: LicenseInfo | null;
}

export function useRuntimeInfo() {
  const [info, setInfo] = useState<RuntimeInfo | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let aborted = false;
    api
      .get<RuntimeInfo>("/runtime")
      .then((res) => {
        if (!aborted && res.data) setInfo(res.data);
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

  return { info, loading };
}
