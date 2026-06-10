import { useCallback, useId, useRef } from "react";
import { cn } from "@/lib/utils";

export interface TabItem {
  /** Stable id used as the `?tab=` query value and aria wiring. */
  id: string;
  /** Visible label. */
  label: string;
  /** Optional leading icon. */
  icon?: React.ComponentType<{ className?: string }>;
  /** Render the tab visually apart (e.g. the "Advanced" power-user tab). */
  separated?: boolean;
}

/**
 * Accessible, horizontally-scrollable tab bar.
 *
 * - Implements the WAI-ARIA tablist/tab/tabpanel pattern with roving focus
 *   and arrow-key navigation (Left/Right/Home/End).
 * - The active tab is the only one in the tab order (`tabIndex=0`); the rest
 *   are reachable via arrow keys, matching native tab semantics.
 * - On mobile the bar scrolls horizontally instead of wrapping.
 * - Honors prefers-reduced-motion via the `motion-reduce:` Tailwind variants.
 *
 * State lives with the parent (URL-driven here), so this is a controlled
 * component: it renders `tabs` and calls `onChange` with the chosen id.
 */
export function Tabs({
  tabs,
  active,
  onChange,
  idBase,
  "aria-label": ariaLabel = "Settings sections",
}: {
  tabs: TabItem[];
  active: string;
  onChange: (id: string) => void;
  /** Prefix for tab/panel element ids so multiple Tabs can coexist. */
  idBase?: string;
  "aria-label"?: string;
}) {
  const generatedBase = useId();
  const base = idBase ?? generatedBase;
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const focusTab = useCallback((index: number) => {
    const el = refs.current[index];
    el?.focus();
  }, []);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent, index: number) => {
      const last = tabs.length - 1;
      let next = -1;
      switch (e.key) {
        case "ArrowRight":
        case "ArrowDown":
          next = index >= last ? 0 : index + 1;
          break;
        case "ArrowLeft":
        case "ArrowUp":
          next = index <= 0 ? last : index - 1;
          break;
        case "Home":
          next = 0;
          break;
        case "End":
          next = last;
          break;
        default:
          return;
      }
      e.preventDefault();
      onChange(tabs[next].id);
      focusTab(next);
    },
    [tabs, onChange, focusTab],
  );

  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      aria-orientation="horizontal"
      className="flex items-center gap-1 overflow-x-auto border-b border-border -mx-1 px-1 scrollbar-thin"
    >
      {tabs.map((tab, i) => {
        const selected = tab.id === active;
        const Icon = tab.icon;
        return (
          <button
            key={tab.id}
            ref={(el) => {
              refs.current[i] = el;
            }}
            id={`${base}-tab-${tab.id}`}
            role="tab"
            type="button"
            aria-selected={selected}
            aria-controls={`${base}-panel-${tab.id}`}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(tab.id)}
            onKeyDown={(e) => onKeyDown(e, i)}
            className={cn(
              "relative flex shrink-0 items-center gap-1.5 whitespace-nowrap px-3 py-2.5 text-sm font-medium",
              "transition-colors motion-reduce:transition-none cursor-pointer",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 rounded-t-md",
              tab.separated && "ml-auto",
              selected
                ? "text-accent"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {Icon && <Icon className="h-4 w-4" />}
            {tab.label}
            {/* Active underline indicator */}
            <span
              aria-hidden
              className={cn(
                "absolute inset-x-2 -bottom-px h-0.5 rounded-full transition-opacity motion-reduce:transition-none",
                selected ? "bg-accent opacity-100" : "opacity-0",
              )}
            />
          </button>
        );
      })}
    </div>
  );
}

/** Wrapper that wires a panel to its controlling tab for screen readers. */
export function TabPanel({
  id,
  idBase,
  active,
  children,
}: {
  id: string;
  idBase: string;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <div
      role="tabpanel"
      id={`${idBase}-panel-${id}`}
      aria-labelledby={`${idBase}-tab-${id}`}
      hidden={!active}
      tabIndex={0}
      className="focus-visible:outline-none"
    >
      {active && children}
    </div>
  );
}
