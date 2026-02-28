import { useCallback, useEffect, useState } from "react";
import { api } from "@/api/client";
import { API_BASE_URL } from "@/lib/constants";

type AuthState = "loading" | "authenticated" | "unauthenticated";

export function useAuth() {
  const [state, setState] = useState<AuthState>("loading");

  const tryConnect = useCallback(async (key: string | null, signal?: AbortSignal): Promise<boolean> => {
    api.setApiKey(key);
    try {
      // Validate against a protected endpoint (/api/runtime) instead of the
      // public /api/health. This ensures the key is actually checked by the
      // auth middleware when AUTH_REQUIRED=true.
      const res = await fetch(`${API_BASE_URL}/runtime`, {
        headers: key ? { "X-API-Key": key } : {},
        signal: signal ?? AbortSignal.timeout(3000),
      });
      if (res.status === 401) return false;
      // 200 = key valid (or auth disabled), 404/502 = no backend (demo mode)
      return true;
    } catch (e: unknown) {
      // Abort errors should propagate so the caller knows the request was cancelled
      if (e instanceof DOMException && e.name === "AbortError") throw e;
      // Network error - backend unreachable, let the app handle it (mock mode)
      return true;
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        const savedKey = api.getStoredKey();

        // First try without any key (auth might be disabled)
        const noAuthOk = await tryConnect(null, controller.signal);
        if (noAuthOk && !savedKey) {
          setState("authenticated");
          return;
        }

        // If we have a saved key, try it
        if (savedKey) {
          const ok = await tryConnect(savedKey, controller.signal);
          if (ok) {
            setState("authenticated");
            return;
          }
          // Key is invalid, remove it
          api.clearStoredKey();
          api.setApiKey(null);
        }

        // No auth needed if first probe succeeded
        if (noAuthOk) {
          setState("authenticated");
          return;
        }

        setState("unauthenticated");
      } catch (e: unknown) {
        // Swallow AbortError on unmount
        if (e instanceof DOMException && e.name === "AbortError") return;
        setState("unauthenticated");
      }
    })();
    return () => {
      controller.abort();
    };
  }, [tryConnect]);

  const login = useCallback(async (key: string): Promise<boolean> => {
    const ok = await tryConnect(key);
    if (ok) {
      api.storeApiKey(key);
      setState("authenticated");
      return true;
    }
    api.setApiKey(null);
    return false;
  }, [tryConnect]);

  const logout = useCallback(() => {
    api.clearStoredKey();
    api.setApiKey(null);
    setState("unauthenticated");
  }, []);

  return { state, login, logout };
}
