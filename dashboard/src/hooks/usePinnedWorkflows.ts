import { useCallback, useSyncExternalStore } from "react";

const STORAGE_KEY = "sandcastle-pinned-workflows";
const MAX_PINNED = 5;

// ---------------------------------------------------------------------------
// Shared external store so every consumer sees the same snapshot and
// re-renders together when the list changes.
// ---------------------------------------------------------------------------

let listeners: Array<() => void> = [];

function emitChange() {
  for (const l of listeners) l();
}

function subscribe(listener: () => void): () => void {
  listeners = [...listeners, listener];
  return () => {
    listeners = listeners.filter((l) => l !== listener);
  };
}

function readStorage(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((v): v is string => typeof v === "string").slice(0, MAX_PINNED);
  } catch {
    return [];
  }
}

let cachedSnapshot: string[] = readStorage();

function getSnapshot(): string[] {
  return cachedSnapshot;
}

function persist(names: string[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(names.slice(0, MAX_PINNED)));
  cachedSnapshot = readStorage();
  emitChange();
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function usePinnedWorkflows() {
  const pinnedWorkflows = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  const togglePin = useCallback((name: string) => {
    const current = getSnapshot();
    if (current.includes(name)) {
      persist(current.filter((n) => n !== name));
    } else {
      if (current.length >= MAX_PINNED) {
        // Drop the oldest (first) to make room
        persist([...current.slice(1), name]);
      } else {
        persist([...current, name]);
      }
    }
  }, []);

  const isPinned = useCallback(
    (name: string) => pinnedWorkflows.includes(name),
    [pinnedWorkflows]
  );

  return { pinnedWorkflows, togglePin, isPinned } as const;
}
