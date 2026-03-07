import { useCallback, useEffect, useState } from "react";
import { api } from "@/api/client";
import { API_BASE_URL } from "@/lib/constants";

type AuthState = "loading" | "authenticated" | "unauthenticated";

export function useAuth() {
  const [state, setState] = useState<AuthState>("loading");

  const tryConnect = useCallback(async (key: string | null, signal?: AbortSignal): Promise<boolean | "unreachable"> => {
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
      // 200 = key valid (or auth disabled), 404/502 = backend up but
      // endpoint missing - still means auth is not blocking us
      return true;
    } catch (e: unknown) {
      // Abort errors should propagate so the caller knows the request was cancelled
      if (e instanceof DOMException && e.name === "AbortError") throw e;
      // Network error - backend unreachable. Return distinct value so
      // callers can differentiate "auth OK" from "can't tell".
      return "unreachable";
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        const savedKey = api.getStoredKey();

        // First try without any key (auth might be disabled)
        const noAuthResult = await tryConnect(null, controller.signal);

        // Backend unreachable - allow access in demo/mock mode
        if (noAuthResult === "unreachable") {
          setState("authenticated");
          return;
        }

        if (noAuthResult === true && !savedKey) {
          setState("authenticated");
          return;
        }

        // If we have a saved key, try it
        if (savedKey) {
          const ok = await tryConnect(savedKey, controller.signal);
          if (ok === true) {
            setState("authenticated");
            return;
          }
          if (ok === "unreachable") {
            setState("authenticated");
            return;
          }
          // Key is invalid, remove it
          api.clearStoredKey();
          api.setApiKey(null);
        }

        // No auth needed if first probe succeeded
        if (noAuthResult === true) {
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
    const result = await tryConnect(key);
    if (result === true) {
      api.storeApiKey(key);
      setState("authenticated");
      return true;
    }
    // "unreachable" during login = can't validate, reject
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
