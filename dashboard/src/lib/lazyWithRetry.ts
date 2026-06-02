import { lazy, type ComponentType, type LazyExoticComponent } from "react";

/**
 * True when an error is a failed dynamic-import / chunk fetch. This is the
 * classic symptom of a fresh deploy: an open tab still references the old
 * hashed asset name, the new deploy removed it, so the import 404s. Browsers
 * surface this as a TypeError whose message mentions the module, or as a named
 * ChunkLoadError. We keep the matcher broad across engines (Chrome/Firefox/
 * Safari word it differently) and also cover Vite's per-chunk CSS preload
 * failure ("Unable to preload CSS for ..."), which rejects the same import.
 *
 * Note: a plain "Failed to fetch" / "Load failed" (no "module"/"css" qualifier)
 * is deliberately NOT matched - that is a real api/network failure and must stay
 * a connection error, not a "reload for the new build" prompt.
 */
export function isChunkLoadError(err: unknown): boolean {
  if (!(err instanceof Error)) return false;
  const msg = err.message || "";
  return (
    err.name === "ChunkLoadError" ||
    /failed to fetch dynamically imported module/i.test(msg) ||
    /error loading dynamically imported module/i.test(msg) ||
    /importing a module script failed/i.test(msg) ||
    /unable to preload css/i.test(msg) ||
    /dynamically imported module/i.test(msg)
  );
}

/** sessionStorage that never throws (private mode, disabled storage, SSR). */
function safeSession(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.sessionStorage : null;
  } catch {
    return null;
  }
}

function getFlag(key: string): boolean {
  try {
    return safeSession()?.getItem(key) === "1";
  } catch {
    return false;
  }
}

/**
 * Set/clear the guard and report whether it actually persisted. Some browser
 * modes (storage-partitioned iframes, "block all storage", Safari private mode)
 * let getItem return null yet throw - or silently no-op - on setItem. We must
 * know the guard is durable before trusting it to make the reload one-shot, so
 * we read it back. A non-durable guard means the caller must NOT auto-reload
 * (it would loop forever), so it returns false.
 */
function setFlag(key: string, value: boolean): boolean {
  try {
    const s = safeSession();
    if (!s) return false;
    if (value) s.setItem(key, "1");
    else s.removeItem(key);
    // Verify it stuck - this is what makes the one-shot guarantee real.
    return value ? s.getItem(key) === "1" : s.getItem(key) === null;
  } catch {
    // Storage unavailable - report failure so the caller skips the auto-reload.
    return false;
  }
}

/**
 * Decide whether to recover from a chunk miss by reloading once, and do it.
 * Returns true if a reload was triggered (caller keeps the loading UI up),
 * false if the reload was suppressed - in which case the caller should let the
 * error reach the boundary for a bounded, manual recovery. Reload is suppressed
 * when:
 *   - the browser is known-offline (a reload would not bring the chunk back), or
 *   - this route already auto-reloaded once this session (the guard is set), or
 *   - the guard could not be durably persisted (auto-reload would loop forever).
 */
function tryChunkReload(guardKey: string): boolean {
  const online = typeof navigator === "undefined" || navigator.onLine;
  if (!online) return false;
  if (getFlag(guardKey)) return false;
  if (!setFlag(guardKey, true)) return false;
  // Pull the fresh index.html + the new chunk hashes it points at.
  window.location.reload();
  return true;
}

/**
 * Drop-in replacement for React.lazy that recovers from a stale-deploy chunk
 * miss by reloading the page once to pull the fresh index.html, instead of
 * dead-ending in the error boundary with a misleading "connection error".
 *
 * The reload is strictly one-shot and only fires when it can actually help
 * (online, durable guard): if the chunk is still missing afterwards (a broken
 * deploy) or recovery is impossible (offline, storage blocked), the error is
 * rethrown so the boundary can show a real, actionable "Update available"
 * card rather than reload-looping forever.
 *
 * @param factory the dynamic import, e.g. `() => import("@/pages/Foo")`
 * @param key     stable route key used to namespace the reload guard
 */
export function lazyWithRetry<T extends ComponentType<unknown>>(
  factory: () => Promise<{ default: T }>,
  key: string,
): LazyExoticComponent<T> {
  const flag = `sc:chunk-reload:${key}`;
  return lazy(async () => {
    try {
      const mod = await factory();
      setFlag(flag, false); // loaded cleanly - clear any prior guard
      return mod;
    } catch (err) {
      if (isChunkLoadError(err) && tryChunkReload(flag)) {
        // Keep Suspense's fallback up during the reload instead of flashing the
        // error boundary; this promise never settles before navigation.
        return new Promise<{ default: T }>(() => {});
      }
      // Offline, already retried, storage blocked, or not a chunk error -
      // let the boundary handle it.
      throw err;
    }
  });
}
