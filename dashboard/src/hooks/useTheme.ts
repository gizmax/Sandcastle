import { useState, useEffect, useCallback } from "react";

type Theme = "light" | "dark";

/** Window event that flips the theme from anywhere (e.g. the command palette).
 *  Every mounted useTheme instance listens, so all toggles stay in sync. */
export const THEME_TOGGLE_EVENT = "sandcastle:toggle-theme";

function getInitialTheme(): Theme {
  try {
    const stored = localStorage.getItem("theme");
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // Storage unavailable (private browsing, sandboxed iframe). Fall through
    // to the default below.
  }
  // Dark-first: a stored preference always wins; new visitors default to dark
  // (the signature look for an AI/dev tool) unless the OS explicitly prefers light.
  if (window.matchMedia("(prefers-color-scheme: light)").matches) return "light";
  return "dark";
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    try {
      localStorage.setItem("theme", theme);
    } catch {
      // Best-effort: theme still applies for the session even if it can't
      // be persisted (private browsing / quota exceeded).
    }
  }, [theme]);

  // Global toggle event - lets the command palette (or anything else) flip the
  // theme without holding a reference to this hook instance.
  useEffect(() => {
    const handler = () => setTheme((prev) => (prev === "dark" ? "light" : "dark"));
    window.addEventListener(THEME_TOGGLE_EVENT, handler);
    return () => window.removeEventListener(THEME_TOGGLE_EVENT, handler);
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme((prev) => (prev === "dark" ? "light" : "dark"));
  }, []);

  return { theme, toggleTheme };
}
