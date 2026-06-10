import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { MoreHorizontal } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * ActionMenu — the reusable "complexity at the bottom" affordance.
 *
 * The UX principle: power features live as ACTIONS ON OBJECTS, surfaced exactly
 * where they're relevant (a run, a workflow, a list row) instead of as separate
 * top-level nav destinations. This component renders an optional prominent
 * PRIMARY action plus a tidy, keyboard-accessible "•••" overflow menu for the
 * secondary actions.
 *
 * ──────────────────────────────────────────────────────────────────────────
 * EXTENSION POINT — adding a new contextual action is a one-liner.
 *
 * Anywhere you build an `ActionMenuItem[]`, just push another entry. Future
 * power features (e.g. "Heal this failure", "Improve overnight",
 * "Time-machine replay") slot in here as additional items WITHOUT touching this
 * component or adding a nav item — that is the whole point of the pattern:
 *
 *   const items: ActionMenuItem[] = [
 *     { id: "replay",      label: "Re-run",        icon: RotateCcw, onSelect: ... },
 *     // ── future actions drop in right here, one line each: ──
 *     // { id: "heal",     label: "Heal failure",  icon: HeartPulse, onSelect: ..., gated: true },
 *     // { id: "overnight",label: "Improve overnight", icon: Moon,   onSelect: ... },
 *   ];
 *
 * Use `gated: true` on advanced items and pass `density`/`atLeast` from
 * `useDensity()` at the call site to hide them below a chosen tier.
 * ──────────────────────────────────────────────────────────────────────────
 */
export interface ActionMenuItem {
  /** Stable id (used as React key + for tests). */
  id: string;
  label: string;
  icon?: React.ComponentType<{ className?: string }>;
  onSelect: () => void;
  /** Render with destructive/expensive styling (red). */
  danger?: boolean;
  disabled?: boolean;
  /** Optional helper line shown under the label (e.g. a cost hint). */
  description?: string;
  /**
   * Marks an "advanced" action. Purely advisory — the call site decides
   * whether to include it (e.g. gate behind a density tier). Kept here so the
   * extension point reads naturally.
   */
  gated?: boolean;
}

interface ActionMenuProps {
  /** Optional prominent primary action rendered as a solid button. */
  primary?: ActionMenuItem;
  /** Secondary actions shown in the "•••" overflow menu. */
  items: ActionMenuItem[];
  /** Accessible label for the overflow trigger. */
  menuLabel?: string;
  /** Compact variant for dense contexts like table rows. */
  size?: "sm" | "md";
  className?: string;
  /** Render the overflow trigger only (no primary), even if `primary` is set. */
  align?: "start" | "end";
}

export function ActionMenu({
  primary,
  items,
  menuLabel = "More actions",
  size = "md",
  className,
  align = "end",
}: ActionMenuProps) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [coords, setCoords] = useState<{ top: number; left: number; width: number } | null>(null);
  const menuId = useId();

  const enabledItems = items.filter((i) => !i.disabled);

  const close = useCallback((focusTrigger = false) => {
    setOpen(false);
    if (focusTrigger) triggerRef.current?.focus();
  }, []);

  // Position the portal-rendered menu relative to the trigger so it escapes
  // overflow:hidden / table clipping while staying anchored on scroll.
  const reposition = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setCoords({ top: r.bottom + 4, left: r.left, width: r.width });
  }, []);

  useEffect(() => {
    if (!open) return;
    reposition();
    setActiveIndex(0);
    const onScrollOrResize = () => reposition();
    window.addEventListener("scroll", onScrollOrResize, true);
    window.addEventListener("resize", onScrollOrResize);
    return () => {
      window.removeEventListener("scroll", onScrollOrResize, true);
      window.removeEventListener("resize", onScrollOrResize);
    };
  }, [open, reposition]);

  // Close on outside click / Escape.
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      const t = e.target as Node;
      if (menuRef.current?.contains(t) || triggerRef.current?.contains(t)) return;
      close();
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open, close]);

  const handleTriggerKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      setOpen(true);
    }
  };

  const handleMenuKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      close(true);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => (i + 1) % enabledItems.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => (i - 1 + enabledItems.length) % enabledItems.length);
    } else if (e.key === "Home") {
      e.preventDefault();
      setActiveIndex(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setActiveIndex(enabledItems.length - 1);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      const item = enabledItems[activeIndex];
      if (item) {
        close(true);
        item.onSelect();
      }
    }
  };

  // Focus the active item when the menu opens / arrows move.
  useEffect(() => {
    if (!open) return;
    const node = menuRef.current?.querySelector<HTMLButtonElement>(
      `[data-index="${activeIndex}"]`
    );
    node?.focus();
  }, [open, activeIndex]);

  const compact = size === "sm";

  const PrimaryIcon = primary?.icon;

  return (
    <div className={cn("flex items-center gap-2", className)}>
      {primary && (
        <button
          type="button"
          onClick={primary.onSelect}
          disabled={primary.disabled}
          className={cn(
            "flex items-center gap-1.5 rounded-lg bg-accent font-medium text-accent-foreground",
            "shadow-sm hover:bg-accent-hover hover:shadow-md transition-all duration-200",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
            "disabled:opacity-50 disabled:cursor-not-allowed",
            compact ? "px-2.5 py-1.5 text-xs" : "px-3 py-1.5 text-xs sm:text-sm"
          )}
        >
          {PrimaryIcon && <PrimaryIcon className="h-4 w-4" />}
          {primary.label}
        </button>
      )}

      {items.length > 0 && (
        <>
          <button
            ref={triggerRef}
            type="button"
            aria-label={menuLabel}
            aria-haspopup="menu"
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
            onKeyDown={handleTriggerKeyDown}
            className={cn(
              "flex items-center justify-center rounded-lg border border-border text-muted",
              "hover:bg-border/40 hover:text-foreground transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
              open && "bg-border/40 text-foreground",
              compact ? "h-7 w-7" : "h-8 w-8"
            )}
          >
            <MoreHorizontal className={compact ? "h-4 w-4" : "h-4 w-4"} />
          </button>

          {open &&
            coords &&
            createPortal(
              <div
                ref={menuRef}
                id={menuId}
                role="menu"
                aria-label={menuLabel}
                onKeyDown={handleMenuKeyDown}
                style={{
                  position: "fixed",
                  top: coords.top,
                  left: align === "end" ? undefined : coords.left,
                  // anchor right edge to the trigger's right edge when align=end
                  right: align === "end" ? `calc(100vw - ${coords.left + coords.width}px)` : undefined,
                }}
                className={cn(
                  "z-[60] min-w-[13rem] max-w-[18rem] overflow-hidden rounded-lg border border-border",
                  "bg-surface shadow-lg py-1",
                  // Dropdown entrance; the design system disables this under
                  // prefers-reduced-motion (see index.css).
                  "origin-top animate-scale-in"
                )}
              >
                {enabledItems.map((item, idx) => {
                  const Icon = item.icon;
                  return (
                    <button
                      key={item.id}
                      type="button"
                      role="menuitem"
                      data-index={idx}
                      data-action-id={item.id}
                      tabIndex={-1}
                      onClick={() => {
                        close(true);
                        item.onSelect();
                      }}
                      className={cn(
                        "flex w-full items-start gap-2.5 px-3 py-2 text-left text-sm transition-colors",
                        "focus-visible:outline-none",
                        item.danger
                          ? "text-error hover:bg-error/10 focus:bg-error/10"
                          : "text-foreground hover:bg-border/40 focus:bg-border/40"
                      )}
                    >
                      {Icon && (
                        <Icon
                          className={cn(
                            "mt-0.5 h-4 w-4 shrink-0",
                            item.danger ? "text-error" : "text-muted"
                          )}
                        />
                      )}
                      <span className="min-w-0">
                        <span className="block font-medium leading-tight">{item.label}</span>
                        {item.description && (
                          <span className="mt-0.5 block text-xs text-muted">
                            {item.description}
                          </span>
                        )}
                      </span>
                    </button>
                  );
                })}
              </div>,
              document.body
            )}
        </>
      )}
    </div>
  );
}

export type { ReactNode };
