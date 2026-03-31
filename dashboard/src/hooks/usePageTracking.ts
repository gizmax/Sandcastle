/**
 * Lightweight, opt-in, privacy-respecting page view tracker.
 *
 * On each route change, logs: { page: "/settings", timestamp: Date.now() }
 * Stored in localStorage under `sandcastle_usage_log` (array, max 1000 entries).
 * NO external calls. NO personal data. Just local analytics the user can see.
 */

import { useEffect, useCallback, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";

const STORAGE_KEY = "sandcastle_usage_log";
const MAX_ENTRIES = 1000;

interface PageViewEntry {
  page: string;
  timestamp: number;
}

interface PageStat {
  page: string;
  count: number;
}

interface UsageStats {
  topPages: PageStat[];
  totalViews: number;
}

// Read the log from localStorage
function readLog(): PageViewEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed;
  } catch {
    return [];
  }
}

// Write the log to localStorage (capped at MAX_ENTRIES)
function writeLog(entries: PageViewEntry[]): void {
  try {
    const capped = entries.slice(-MAX_ENTRIES);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(capped));
  } catch {
    // localStorage full or unavailable - silently ignore
  }
}

/**
 * Track page views on route change. Call at the app top level.
 * Returns nothing - tracking is a side effect.
 */
export function usePageTracking(): void {
  const location = useLocation();

  useEffect(() => {
    const entry: PageViewEntry = {
      page: location.pathname,
      timestamp: Date.now(),
    };
    const log = readLog();
    log.push(entry);
    writeLog(log);
  }, [location.pathname]);
}

/**
 * Read usage statistics from the local analytics log.
 * Returns top 5 most visited pages and total view count.
 * Also returns a clearStats function to wipe the log.
 */
export function useUsageStats(): UsageStats & { clearStats: () => void; refreshKey: number } {
  const [refreshKey, setRefreshKey] = useState(0);

  const stats = useMemo<UsageStats>(() => {
    const log = readLog();
    const totalViews = log.length;

    // Count visits per page
    const counts = new Map<string, number>();
    for (const entry of log) {
      counts.set(entry.page, (counts.get(entry.page) ?? 0) + 1);
    }

    // Sort by count descending, take top 5
    const topPages: PageStat[] = Array.from(counts.entries())
      .map(([page, count]) => ({ page, count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 5);

    return { topPages, totalViews };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  const clearStats = useCallback(() => {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // silently ignore
    }
    setRefreshKey((k) => k + 1);
  }, []);

  return { ...stats, clearStats, refreshKey };
}
