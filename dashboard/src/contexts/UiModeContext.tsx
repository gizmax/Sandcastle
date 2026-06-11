import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

/**
 * ============================================================================
 * DENSITY MODEL — UX "progressive disclosure" scaffolding
 * ============================================================================
 *
 * The dashboard exposes three tiers of UI density. Simple at the top, depth at
 * the bottom. Downstream agents/components consume this via `useDensity()`
 * (preferred) or the back-compat alias `useUiMode()`.
 *
 *   "Essentials"  Home + BUILD + RUN only. IMPROVE and OPERATE are hidden.
 *                 The beginner experience set by completing onboarding's guided
 *                 flow is "Standard" (not Essentials) — Essentials is an opt-in
 *                 minimal mode (migrated from the legacy "lite").
 *   "Standard"    (DEFAULT for new users) + IMPROVE visible. OPERATE present in
 *                 the sidebar but collapsed by default.
 *   "Everything"  All groups expanded; OPERATE open. (migrated from legacy "full")
 *
 * NAV VISIBILITY CONTRACT (consumed by Sidebar.tsx):
 *   - groupVisible("HOME" | "BUILD" | "RUN")   -> always true
 *   - groupVisible("IMPROVE")                  -> Standard or Everything
 *   - groupVisible("OPERATE")                  -> Standard or Everything
 *   - operateOpenByDefault                     -> true only on Everything
 *
 * ROUTE GATING CONTRACT (consumed by App.tsx <TierGuard>):
 *   - Pages in IMPROVE / OPERATE require tier >= "Standard". On "Essentials"
 *     those routes redirect to "/". `LiteGuard` remains exported as a thin
 *     back-compat alias of `<TierGuard min="Standard">`.
 *
 * STORAGE / MIGRATION:
 *   localStorage key "sandcastle-ui-mode" holds the Density value. Legacy values
 *   are migrated on read: "lite" -> "Essentials", "full" -> "Everything".
 *   A brand-new install (no stored value) is `null` until onboarding/explicit
 *   choice; treated as "Standard" for visibility/gating purposes.
 * ============================================================================
 */

/** The three UI density tiers. */
export type Density = "Essentials" | "Standard" | "Everything";

/** @deprecated legacy alias retained so older imports keep compiling. */
export type UiMode = Density;

const STORAGE_KEY = "sandcastle-ui-mode";

/** Ordering used for tier comparisons (higher = more surface area). */
const TIER_ORDER: Record<Density, number> = {
  Essentials: 0,
  Standard: 1,
  Everything: 2,
};

/** The effective tier used for visibility/gating when nothing is chosen yet. */
const DEFAULT_DENSITY: Density = "Standard";

export type NavGroupId = "HOME" | "BUILD" | "RUN" | "IMPROVE" | "OPERATE";

interface DensityContextValue {
  /** null until the user has been through onboarding / explicitly chosen. */
  density: Density | null;
  /** The density actually used for rendering decisions (null -> Standard). */
  effectiveDensity: Density;
  setDensity: (density: Density) => void;
  /** True when `min` is satisfied by the effective density. */
  atLeast: (min: Density) => boolean;
  /** Whether a nav group should be shown at the current density. */
  groupVisible: (group: NavGroupId) => boolean;
  /** OPERATE starts open only at "Everything". */
  operateOpenByDefault: boolean;

  // ---- Back-compat surface (legacy callers) --------------------------------
  /** @deprecated use `density`. */
  mode: Density | null;
  /** @deprecated use `setDensity`. */
  setMode: (mode: Density) => void;
  /** @deprecated true only when the effective density is "Essentials". */
  isLite: boolean;
}

const DensityContext = createContext<DensityContextValue | undefined>(undefined);

function readStored(): Density | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    // Migrate legacy binary values.
    if (v === "lite") return "Essentials";
    if (v === "full") return "Everything";
    if (v === "Essentials" || v === "Standard" || v === "Everything") return v;
    return null;
  } catch {
    return null;
  }
}

export function UiModeProvider({ children }: { children: ReactNode }) {
  const [density, setDensityState] = useState<Density | null>(() => readStored());

  const setDensity = useCallback((next: Density) => {
    setDensityState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Best-effort: private browsing / quota exceeded must not break switching.
    }
  }, []);

  const value = useMemo<DensityContextValue>(() => {
    const effectiveDensity = density ?? DEFAULT_DENSITY;
    const atLeast = (min: Density) =>
      TIER_ORDER[effectiveDensity] >= TIER_ORDER[min];
    const groupVisible = (group: NavGroupId) => {
      switch (group) {
        case "HOME":
        case "BUILD":
        case "RUN":
          return true;
        case "IMPROVE":
        case "OPERATE":
          return atLeast("Standard");
        default:
          return true;
      }
    };
    return {
      density,
      effectiveDensity,
      setDensity,
      atLeast,
      groupVisible,
      operateOpenByDefault: effectiveDensity === "Everything",
      // Back-compat aliases.
      mode: density,
      setMode: setDensity,
      isLite: effectiveDensity === "Essentials",
    };
  }, [density, setDensity]);

  return (
    <DensityContext.Provider value={value}>{children}</DensityContext.Provider>
  );
}

/**
 * Preferred hook for downstream agents.
 *
 * Signature:
 *   useDensity(): {
 *     density: Density | null
 *     effectiveDensity: Density
 *     setDensity: (d: Density) => void
 *     atLeast: (min: Density) => boolean
 *     groupVisible: (g: NavGroupId) => boolean
 *     operateOpenByDefault: boolean
 *     // deprecated back-compat: mode, setMode, isLite
 *   }
 */
// eslint-disable-next-line react-refresh/only-export-components
export function useDensity(): DensityContextValue {
  const ctx = useContext(DensityContext);
  if (!ctx) {
    throw new Error("useDensity must be used within a UiModeProvider");
  }
  return ctx;
}

/** @deprecated back-compat alias of {@link useDensity}. */
// eslint-disable-next-line react-refresh/only-export-components
export function useUiMode(): DensityContextValue {
  return useDensity();
}
