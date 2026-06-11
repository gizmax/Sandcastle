import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";

/**
 * HoverCard — a richer hover/focus popover than the simple `Tooltip`.
 *
 * Where `Tooltip` shows a single line, HoverCard shows a structured card with a
 * title, body lines, and an optional footer (cost note / "Use when…"). It is
 * designed for the step palette and config-panel "rich step cards".
 *
 * Behaviour:
 *  - Opens on hover OR keyboard focus of the trigger (focusable wrapper).
 *  - Rendered in a portal and clamped to the viewport so it never clips.
 *  - Dismisses on Escape, blur, mouse-leave, and outside interaction.
 *  - Honours `prefers-reduced-motion` (no transition when reduced).
 *  - Accessible: card has role="tooltip" and the trigger is aria-describedby it.
 *
 * Keep the simple `Tooltip` for one-liners; reach for HoverCard when you need
 * structure.
 */
export interface HoverCardProps {
  /** Bold heading at the top of the card. */
  title: ReactNode;
  /**
   * Body content. A string renders as a paragraph; a string[] renders as
   * separate lines; any ReactNode is rendered as-is.
   */
  body?: ReactNode | string[];
  /** Optional footer line(s) — e.g. cost note or "Use when…". */
  footer?: ReactNode | string[];
  /** The trigger element the card is anchored to. */
  children: ReactNode;
  /** Preferred side; flips automatically if it would clip. */
  side?: "top" | "bottom";
  /** Extra classes for the trigger wrapper. */
  className?: string;
  /** Extra classes for the card surface. */
  cardClassName?: string;
  /** Open delay in ms (hover only). Default 120. */
  openDelay?: number;
}

const VIEWPORT_MARGIN = 8;
const CARD_WIDTH = 280;

function renderLines(content: ReactNode | string[] | undefined): ReactNode {
  if (content == null) return null;
  if (Array.isArray(content)) {
    return content.map((line, i) => <p key={i}>{line}</p>);
  }
  return content;
}

export function HoverCard({
  title,
  body,
  footer,
  children,
  side = "top",
  className,
  cardClassName,
  openDelay = 120,
}: HoverCardProps) {
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState<{ top: number; left: number } | null>(
    null,
  );
  const triggerRef = useRef<HTMLSpanElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const openTimer = useRef<number | null>(null);
  const cardId = useId();

  const clearTimer = useCallback(() => {
    if (openTimer.current != null) {
      window.clearTimeout(openTimer.current);
      openTimer.current = null;
    }
  }, []);

  // Position the portal card, flipping side and clamping to the viewport so it
  // is never clipped by overflow:hidden ancestors or the window edges.
  const reposition = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const cardH = cardRef.current?.offsetHeight ?? 120;
    const cardW = cardRef.current?.offsetWidth ?? CARD_WIDTH;

    const spaceAbove = r.top;
    const spaceBelow = window.innerHeight - r.bottom;
    const placeBelow =
      side === "bottom"
        ? spaceBelow >= cardH + VIEWPORT_MARGIN || spaceBelow >= spaceAbove
        : !(spaceAbove >= cardH + VIEWPORT_MARGIN || spaceAbove >= spaceBelow);

    let top = placeBelow ? r.bottom + 6 : r.top - cardH - 6;
    top = Math.max(
      VIEWPORT_MARGIN,
      Math.min(top, window.innerHeight - cardH - VIEWPORT_MARGIN),
    );

    let left = r.left + r.width / 2 - cardW / 2;
    left = Math.max(
      VIEWPORT_MARGIN,
      Math.min(left, window.innerWidth - cardW - VIEWPORT_MARGIN),
    );

    setCoords({ top, left });
  }, [side]);

  const show = useCallback(() => {
    clearTimer();
    openTimer.current = window.setTimeout(() => setOpen(true), openDelay);
  }, [clearTimer, openDelay]);

  const hide = useCallback(() => {
    clearTimer();
    setOpen(false);
  }, [clearTimer]);

  // Reposition while open; track scroll/resize.
  useEffect(() => {
    if (!open) return;
    reposition();
    const onMove = () => reposition();
    window.addEventListener("scroll", onMove, true);
    window.addEventListener("resize", onMove);
    return () => {
      window.removeEventListener("scroll", onMove, true);
      window.removeEventListener("resize", onMove);
    };
  }, [open, reposition]);

  // Escape closes and returns focus to the trigger.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        hide();
        triggerRef.current?.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, hide]);

  useEffect(() => clearTimer, [clearTimer]);

  const hasBody = body != null;
  const hasFooter = footer != null;

  return (
    <span
      ref={triggerRef}
      tabIndex={0}
      aria-describedby={open ? cardId : undefined}
      className={cn(
        "relative inline-flex min-w-0 outline-none",
        "focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:rounded-md",
        className,
      )}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={() => setOpen(true)}
      onBlur={hide}
    >
      {children}
      {open &&
        createPortal(
          <div
            ref={cardRef}
            id={cardId}
            role="tooltip"
            style={{
              position: "fixed",
              top: coords?.top ?? -9999,
              left: coords?.left ?? -9999,
              width: CARD_WIDTH,
            }}
            className={cn(
              "z-[80] rounded-lg border border-border bg-surface text-foreground shadow-xl",
              "p-3 text-xs leading-relaxed",
              "motion-safe:transition-opacity motion-safe:duration-150",
              coords ? "opacity-100" : "opacity-0",
              cardClassName,
            )}
            onMouseEnter={clearTimer}
            onMouseLeave={hide}
          >
            <p className="text-sm font-semibold text-foreground">{title}</p>
            {hasBody && (
              <div className="mt-1 space-y-1 text-muted-foreground">
                {renderLines(body)}
              </div>
            )}
            {hasFooter && (
              <div className="mt-2 border-t border-border pt-2 space-y-0.5 text-[11px] text-muted-foreground">
                {renderLines(footer)}
              </div>
            )}
          </div>,
          document.body,
        )}
    </span>
  );
}
