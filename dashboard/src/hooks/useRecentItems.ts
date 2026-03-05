import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "sandcastle-recent-items";
const MAX_ITEMS = 5;

export interface RecentItem {
  type: "run" | "workflow" | "page";
  id: string;
  label: string;
  timestamp: number;
}

function loadItems(): RecentItem[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as RecentItem[];
    if (!Array.isArray(parsed)) return [];
    return parsed.slice(0, MAX_ITEMS);
  } catch {
    return [];
  }
}

function saveItems(items: RecentItem[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items.slice(0, MAX_ITEMS)));
  } catch {
    // Storage full or unavailable - ignore
  }
}

export function useRecentItems() {
  const [items, setItems] = useState<RecentItem[]>(loadItems);

  // Sync across tabs
  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key === STORAGE_KEY) {
        setItems(loadItems());
      }
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const addItem = useCallback((item: Omit<RecentItem, "timestamp">) => {
    setItems((prev) => {
      const filtered = prev.filter((p) => p.id !== item.id);
      const next = [{ ...item, timestamp: Date.now() }, ...filtered].slice(0, MAX_ITEMS);
      saveItems(next);
      return next;
    });
  }, []);

  return { recentItems: items, addRecentItem: addItem };
}
