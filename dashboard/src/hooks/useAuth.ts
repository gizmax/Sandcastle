import { useCallback, useEffect, useState } from "react";
import { api } from "@/api/client";
import { API_BASE_URL } from "@/lib/constants";

const STORAGE_KEY = "sandcastle_api_key";

type AuthState = "loading" | "authenticated" | "unauthenticated";

export function useAuth() {
  const [state, setState] = useState<AuthState>("loading");

  const tryConnect = useCallback(async (key: string | null): Promise<boolean> => {
    api.setApiKey(key);
    try {
      // Validate against a protected endpoint (/api/runtime) instead of the
      // public /api/health. This ensures the key is actually checked by the
      // auth middleware when AUTH_REQUIRED=true.
      const res = await fetch(`${API_BASE_URL}/runtime`, {
        headers: key ? { "X-API-Key": key } : {},
        signal: AbortSignal.timeout(3000),
      });
      if (res.status === 401) return false;
      // 200 = key valid (or auth disabled), 404/502 = no backend (demo mode)
      return true;
    } catch {
      // Network error - backend unreachable, let the app handle it (mock mode)
      return true;
    }
  }, []);

  useEffect(() => {
    (async () => {
      const savedKey = localStorage.getItem(STORAGE_KEY);

      // First try without any key (auth might be disabled)
      const noAuthOk = await tryConnect(null);
      if (noAuthOk && !savedKey) {
        setState("authenticated");
        return;
      }

      // If we have a saved key, try it
      if (savedKey) {
        const ok = await tryConnect(savedKey);
        if (ok) {
          setState("authenticated");
          return;
        }
        // Key is invalid, remove it
        localStorage.removeItem(STORAGE_KEY);
        api.setApiKey(null);
      }

      // No auth needed if first probe succeeded
      if (noAuthOk) {
        setState("authenticated");
        return;
      }

      setState("unauthenticated");
    })();
  }, [tryConnect]);

  const login = useCallback(async (key: string): Promise<boolean> => {
    const ok = await tryConnect(key);
    if (ok) {
      localStorage.setItem(STORAGE_KEY, key);
      setState("authenticated");
      return true;
    }
    api.setApiKey(null);
    return false;
  }, [tryConnect]);

  const logout = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY);
    api.setApiKey(null);
    setState("unauthenticated");
  }, []);

  return { state, login, logout };
}
