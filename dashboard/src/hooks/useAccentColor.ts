import { useState, useEffect, useCallback } from "react";

// -- Types -------------------------------------------------------------------

export interface AccentColorEntry {
  id: string;
  label: string;
  accent: string;
  accentHover: string;
  accentForeground: string;
  /** Dark-mode overrides (accent is slightly lighter for contrast on dark bg) */
  darkAccent: string;
  darkAccentHover: string;
  darkAccentForeground: string;
}

// -- Palette -----------------------------------------------------------------

export const ACCENT_COLORS: readonly AccentColorEntry[] = [
  {
    id: "amber",
    label: "Amber",
    accent: "#F59E0B",
    accentHover: "#D97706",
    accentForeground: "#FFFFFF",
    darkAccent: "#FBBF24",
    darkAccentHover: "#F59E0B",
    darkAccentForeground: "#171717",
  },
  {
    id: "blue",
    label: "Blue",
    accent: "#3B82F6",
    accentHover: "#2563EB",
    accentForeground: "#FFFFFF",
    darkAccent: "#60A5FA",
    darkAccentHover: "#3B82F6",
    darkAccentForeground: "#171717",
  },
  {
    id: "green",
    label: "Green",
    accent: "#22C55E",
    accentHover: "#16A34A",
    accentForeground: "#FFFFFF",
    darkAccent: "#4ADE80",
    darkAccentHover: "#22C55E",
    darkAccentForeground: "#171717",
  },
  {
    id: "purple",
    label: "Purple",
    accent: "#8B5CF6",
    accentHover: "#7C3AED",
    accentForeground: "#FFFFFF",
    darkAccent: "#A78BFA",
    darkAccentHover: "#8B5CF6",
    darkAccentForeground: "#FFFFFF",
  },
  {
    id: "pink",
    label: "Pink",
    accent: "#EC4899",
    accentHover: "#DB2777",
    accentForeground: "#FFFFFF",
    darkAccent: "#F472B6",
    darkAccentHover: "#EC4899",
    darkAccentForeground: "#171717",
  },
  {
    id: "red",
    label: "Red",
    accent: "#EF4444",
    accentHover: "#DC2626",
    accentForeground: "#FFFFFF",
    darkAccent: "#F87171",
    darkAccentHover: "#EF4444",
    darkAccentForeground: "#171717",
  },
  {
    id: "teal",
    label: "Teal",
    accent: "#14B8A6",
    accentHover: "#0D9488",
    accentForeground: "#FFFFFF",
    darkAccent: "#2DD4BF",
    darkAccentHover: "#14B8A6",
    darkAccentForeground: "#171717",
  },
  {
    id: "orange",
    label: "Orange",
    accent: "#F97316",
    accentHover: "#EA580C",
    accentForeground: "#FFFFFF",
    darkAccent: "#FB923C",
    darkAccentHover: "#F97316",
    darkAccentForeground: "#171717",
  },
] as const;

const STORAGE_KEY = "sandcastle-accent-color";
const DEFAULT_ID = "amber";

// -- Helpers -----------------------------------------------------------------

function isDark(): boolean {
  return document.documentElement.classList.contains("dark");
}

function applyAccent(entry: AccentColorEntry): void {
  const style = document.documentElement.style;
  const dark = isDark();
  style.setProperty("--color-accent", dark ? entry.darkAccent : entry.accent);
  style.setProperty("--color-accent-hover", dark ? entry.darkAccentHover : entry.accentHover);
  style.setProperty("--color-accent-foreground", dark ? entry.darkAccentForeground : entry.accentForeground);
  style.setProperty("--color-ring", dark ? entry.darkAccent : entry.accent);
}

function resolveEntry(id: string): AccentColorEntry {
  return ACCENT_COLORS.find((c) => c.id === id) ?? ACCENT_COLORS[0];
}

function getStoredId(): string {
  return localStorage.getItem(STORAGE_KEY) ?? DEFAULT_ID;
}

// -- Hook --------------------------------------------------------------------

export function useAccentColor() {
  const [accentColor, setAccentColorState] = useState<string>(getStoredId);

  const entry = resolveEntry(accentColor);

  // Apply on mount
  useEffect(() => {
    applyAccent(entry);
  }, [entry]);

  // Re-apply when dark class toggles (observe the <html> element)
  useEffect(() => {
    const observer = new MutationObserver(() => {
      applyAccent(resolveEntry(getStoredId()));
    });
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    return () => observer.disconnect();
  }, []);

  const setAccentColor = useCallback((id: string) => {
    const next = resolveEntry(id);
    localStorage.setItem(STORAGE_KEY, next.id);
    setAccentColorState(next.id);
    applyAccent(next);
  }, []);

  return {
    accentColor,
    setAccentColor,
    ACCENT_COLORS,
  } as const;
}
