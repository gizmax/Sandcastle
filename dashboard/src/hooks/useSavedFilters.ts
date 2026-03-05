import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "sandcastle-saved-filters";
const MAX_FILTERS = 10;

export interface SavedFilterCriteria {
  status?: string;
  search?: string;
  workflow?: string;
}

export interface SavedFilter {
  id: string;
  name: string;
  filters: SavedFilterCriteria;
  createdAt: number;
}

function loadFilters(): SavedFilter[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as SavedFilter[];
    if (!Array.isArray(parsed)) return [];
    return parsed.slice(0, MAX_FILTERS);
  } catch {
    return [];
  }
}

function persistFilters(filters: SavedFilter[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(filters.slice(0, MAX_FILTERS)));
  } catch {
    // Storage full or unavailable - ignore
  }
}

function generateId(): string {
  return `sf_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

export function useSavedFilters() {
  const [savedFilters, setSavedFilters] = useState<SavedFilter[]>(loadFilters);

  // Sync across tabs
  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key === STORAGE_KEY) {
        setSavedFilters(loadFilters());
      }
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const saveCurrentFilter = useCallback(
    (name: string, filters: SavedFilterCriteria): SavedFilter | null => {
      const trimmed = name.trim();
      if (!trimmed) return null;

      const newFilter: SavedFilter = {
        id: generateId(),
        name: trimmed,
        filters,
        createdAt: Date.now(),
      };

      setSavedFilters((prev) => {
        const next = [newFilter, ...prev].slice(0, MAX_FILTERS);
        persistFilters(next);
        return next;
      });

      return newFilter;
    },
    [],
  );

  const deleteFilter = useCallback((id: string) => {
    setSavedFilters((prev) => {
      const next = prev.filter((f) => f.id !== id);
      persistFilters(next);
      return next;
    });
  }, []);

  const getFilter = useCallback(
    (id: string): SavedFilter | undefined => {
      return savedFilters.find((f) => f.id === id);
    },
    [savedFilters],
  );

  return { savedFilters, saveCurrentFilter, deleteFilter, getFilter };
}
