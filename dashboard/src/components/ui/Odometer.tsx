import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { easeSettle, ODOMETER_DURATION, prefersReducedMotion } from "@/lib/motion";

interface OdometerProps {
  /** Target numeric value. On first mount the number rolls up from 0;
   *  on later changes it eases from the previous value. */
  value: number;
  /** Formatter applied every frame (e.g. formatCost). Defaults to a
   *  rounded, locale-formatted integer. */
  format?: (v: number) => string;
  /** Roll-up duration in ms. */
  duration?: number;
  className?: string;
}

const defaultFormat = (v: number) => Math.round(v).toLocaleString();

/**
 * Animated number roll-up with the "settle" easing.
 *
 * Zero layout shift by design: the final formatted value is rendered
 * immediately (invisible) to reserve its width, while the eased value
 * is painted on top. Uses tabular-nums so digits do not wobble.
 * Respects prefers-reduced-motion (jumps straight to the value).
 */
export function Odometer({
  value,
  format = defaultFormat,
  duration = ODOMETER_DURATION,
  className,
}: OdometerProps) {
  const [displayValue, setDisplayValue] = useState<number>(() =>
    prefersReducedMotion() ? value : 0
  );
  // Previous *settled or in-flight* value: the next animation starts here.
  const currentRef = useRef(displayValue);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (prefersReducedMotion() || duration <= 0) {
      currentRef.current = value;
      setDisplayValue(value);
      return;
    }
    const from = currentRef.current;
    if (from === value) return;

    const start = performance.now();
    const tick = (now: number) => {
      const t = (now - start) / duration;
      if (t >= 1) {
        currentRef.current = value;
        setDisplayValue(value);
        rafRef.current = null;
        return;
      }
      const eased = from + (value - from) * easeSettle(t);
      currentRef.current = eased;
      setDisplayValue(eased);
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [value, duration]);

  const finalText = format(value);

  return (
    <span
      className={cn("relative inline-block tabular-nums whitespace-nowrap", className)}
      aria-label={finalText}
    >
      {/* Invisible final value reserves the final width immediately —
          the layout never shifts while the number eases. */}
      <span aria-hidden="true" className="invisible">
        {finalText}
      </span>
      <span aria-hidden="true" className="absolute inset-0 text-right">
        {format(displayValue)}
      </span>
    </span>
  );
}
