/**
 * TideChart - minimal wave/tide line motif for waiting, scheduled, and
 * filtered-to-nothing contexts. Works as a divider or a light empty-state
 * illustration.
 */
interface Props {
  className?: string;
  /** Accessible label. Omit to render as purely decorative (aria-hidden). */
  title?: string;
}

export function TideChart({ className, title }: Props) {
  return (
    <svg
      viewBox="0 0 240 48"
      data-illustration="tide-chart"
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      className={className}
      fill="none"
    >
      {/* Main tide line */}
      <path
        d="M6 26 C 20 12, 36 12, 50 26 S 78 40, 92 26 S 120 12, 134 26 S 162 40, 176 26 S 204 12, 218 26"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      {/* Beaded echo wave, slightly offset */}
      <path
        d="M14 33 C 28 21, 44 21, 58 33 S 86 45, 100 33 S 128 21, 142 33 S 170 45, 184 33 S 212 21, 226 33"
        stroke="var(--color-accent)"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeDasharray="1 6"
        opacity="0.6"
      />
      {/* High-tide marker */}
      <circle cx="134" cy="26" r="2.4" fill="var(--color-accent)" />
    </svg>
  );
}
