/**
 * Sandcastle motion tokens — JS mirror of src/styles/motion.css.
 * Brand metaphor: sand settling — fast arrival, soft damped landing.
 */

/** Snappy-out, softly damped easing (CSS: var(--ease-settle)). */
export const EASE_SETTLE = "cubic-bezier(0.22, 1, 0.36, 1)";

/** Symmetric in-out variant (CSS: var(--ease-settle-inout)). */
export const EASE_SETTLE_INOUT = "cubic-bezier(0.65, 0, 0.35, 1)";

/** Duration scale in milliseconds (CSS: --motion-fast/base/slow). */
export const MOTION_FAST = 120;
export const MOTION_BASE = 180;
export const MOTION_SLOW = 320;

/** Default duration for odometer number roll-ups (ms). */
export const ODOMETER_DURATION = 640;

/**
 * Numeric approximation of the settle curve for rAF-driven animation.
 * Fast attack, long damped landing (matches EASE_SETTLE visually).
 */
export function easeSettle(t: number): number {
  const clamped = Math.min(1, Math.max(0, t));
  return 1 - Math.pow(1 - clamped, 4);
}

/** True when the user asked the OS for reduced motion. */
export function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches === true
  );
}
