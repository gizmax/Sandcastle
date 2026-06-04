import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from "react";

/**
 * UI complexity mode.
 *   "lite" - beginner experience: trimmed sidebar, advanced pages hidden,
 *            simpler overview. Set after the guided onboarding wizard.
 *   "full" - the complete dashboard (default for existing users).
 *   null   - never chosen yet (a brand-new install before onboarding).
 */
export type UiMode = "lite" | "full";

const STORAGE_KEY = "sandcastle-ui-mode";

interface UiModeContextValue {
  /** null until the user has been through onboarding / explicitly chosen. */
  mode: UiMode | null;
  setMode: (mode: UiMode) => void;
  /** Convenience: true only when the active mode is "lite". null counts as full. */
  isLite: boolean;
}

const UiModeContext = createContext<UiModeContextValue | undefined>(undefined);

function readStored(): UiMode | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    return v === "lite" || v === "full" ? v : null;
  } catch {
    return null;
  }
}

export function UiModeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<UiMode | null>(() => readStored());

  const setMode = useCallback((next: UiMode) => {
    setModeState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Best-effort: private browsing / quota exceeded must not break mode switching.
    }
  }, []);

  return (
    <UiModeContext.Provider value={{ mode, setMode, isLite: mode === "lite" }}>
      {children}
    </UiModeContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useUiMode(): UiModeContextValue {
  const ctx = useContext(UiModeContext);
  if (!ctx) {
    throw new Error("useUiMode must be used within a UiModeProvider");
  }
  return ctx;
}
