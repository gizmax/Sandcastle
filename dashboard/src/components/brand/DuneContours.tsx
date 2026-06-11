/**
 * DuneContours - abstract layered dune contour lines. Purely decorative
 * background art: always aria-hidden, very low contrast (the consumer sets
 * opacity via className, e.g. "opacity-[0.06]").
 *
 * preserveAspectRatio="none" so it stretches edge-to-edge as a backdrop;
 * give it an explicit height via className.
 */
interface Props {
  className?: string;
}

export function DuneContours({ className }: Props) {
  return (
    <svg
      viewBox="0 0 600 220"
      preserveAspectRatio="none"
      data-illustration="dune-contours"
      aria-hidden="true"
      className={`brand-backdrop ${className ?? ""}`}
      fill="none"
    >
      <g stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
        <path d="M0 40 C 90 18, 180 55, 300 38 S 510 20, 600 42" />
        <path d="M0 84 C 120 60, 240 96, 360 78 S 540 64, 600 84" opacity="0.8" />
        <path d="M0 126 C 100 104, 220 138, 340 120 S 520 108, 600 128" opacity="0.6" />
        <path d="M0 168 C 140 148, 260 180, 380 162 S 540 152, 600 170" opacity="0.45" />
        <path d="M0 204 C 110 188, 230 216, 350 200 S 530 192, 600 206" opacity="0.3" />
      </g>
      {/* One beaded accent contour between the ink lines */}
      <path
        d="M0 105 C 110 84, 230 116, 350 100 S 530 88, 600 106"
        stroke="var(--color-accent)"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeDasharray="1 7"
        opacity="0.5"
      />
    </svg>
  );
}
