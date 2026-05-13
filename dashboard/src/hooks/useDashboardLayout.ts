import { useCallback, useSyncExternalStore } from "react";

const STORAGE_KEY = "sandcastle-dashboard-layout";

export interface WidgetConfig {
  id: string;
  visible: boolean;
  order: number;
}

export const WIDGET_LABELS: Record<string, string> = {
  "health-hero": "Health Score",
  stats: "Stats Cards",
  "quick-actions": "Quick Actions",
  pinned: "Pinned Workflows",
  heatmap: "Activity Heatmap",
  "cost-forecast": "Cost Forecast",
  charts: "Charts",
  "recent-runs": "Recent Runs",
};

const DEFAULT_WIDGETS: WidgetConfig[] = [
  { id: "health-hero", visible: true, order: 0 },
  { id: "stats", visible: true, order: 1 },
  { id: "quick-actions", visible: true, order: 2 },
  { id: "pinned", visible: true, order: 3 },
  { id: "heatmap", visible: true, order: 4 },
  { id: "cost-forecast", visible: true, order: 5 },
  { id: "charts", visible: true, order: 6 },
  { id: "recent-runs", visible: true, order: 7 },
];

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

function readStorage(): WidgetConfig[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_WIDGETS;
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return DEFAULT_WIDGETS;
    const validIds = new Set(DEFAULT_WIDGETS.map((w) => w.id));
    const stored = parsed.filter(
      (v: unknown): v is WidgetConfig =>
        typeof v === "object" &&
        v !== null &&
        typeof (v as WidgetConfig).id === "string" &&
        validIds.has((v as WidgetConfig).id) &&
        typeof (v as WidgetConfig).visible === "boolean" &&
        typeof (v as WidgetConfig).order === "number",
    );
    if (stored.length === 0) return DEFAULT_WIDGETS;
    const storedIds = new Set(stored.map((w) => w.id));
    const missing = DEFAULT_WIDGETS.filter((w) => !storedIds.has(w.id)).map((w, i) => ({
      ...w,
      order: stored.length + i,
    }));
    return [...stored, ...missing].sort((a, b) => a.order - b.order);
  } catch {
    return DEFAULT_WIDGETS;
  }
}

let cachedSnapshot: WidgetConfig[] = readStorage();

function persist(widgets: WidgetConfig[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(widgets));
    cachedSnapshot = readStorage();
  } catch {
    // Storage unavailable - keep the change in-memory so the UI still
    // reflects the new layout for the current session.
    cachedSnapshot = widgets;
  }
  emitChange();
}

function getSnapshot(): WidgetConfig[] {
  return cachedSnapshot;
}

export function useDashboardLayout() {
  const widgets = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  const moveWidget = useCallback((dragId: string, dropId: string) => {
    if (dragId === dropId) return;
    const current = readStorage();
    const dragIdx = current.findIndex((w) => w.id === dragId);
    const dropIdx = current.findIndex((w) => w.id === dropId);
    if (dragIdx === -1 || dropIdx === -1) return;
    const updated = [...current];
    const [moved] = updated.splice(dragIdx, 1);
    updated.splice(dropIdx, 0, moved);
    persist(updated.map((w, i) => ({ ...w, order: i })));
  }, []);

  const toggleWidget = useCallback((id: string) => {
    const current = readStorage();
    persist(current.map((w) => (w.id === id ? { ...w, visible: !w.visible } : w)));
  }, []);

  const resetLayout = useCallback(() => {
    persist([...DEFAULT_WIDGETS]);
  }, []);

  return { widgets, moveWidget, toggleWidget, resetLayout } as const;
}
