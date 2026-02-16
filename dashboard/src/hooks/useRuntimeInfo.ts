import { useEffect, useState } from "react";
import { api } from "@/api/client";

export interface RuntimeInfo {
  mode: "local" | "production";
  database: string;
  queue: string;
  storage: string;
  data_dir: string | null;
}

export function useRuntimeInfo() {
  const [info, setInfo] = useState<RuntimeInfo | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<RuntimeInfo>("/runtime")
      .then((res) => {
        if (res.data) setInfo(res.data);
      })
      .finally(() => setLoading(false));
  }, []);

  return { info, loading };
}
