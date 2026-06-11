/**
 * SandcastleBuilding - a sandcastle under construction with bucket & shovel.
 * The right tower is unfinished (dashed outline) and the flag pole is bare:
 * the flag goes up when the build is done.
 *
 * Use for loading / in-progress / first-load contexts.
 */
interface Props {
  className?: string;
  /** Accessible label. Omit to render as purely decorative (aria-hidden). */
  title?: string;
}

export function SandcastleBuilding({ className, title }: Props) {
  return (
    <svg
      viewBox="0 0 160 120"
      data-illustration="sandcastle-building"
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      className={className}
      fill="none"
    >
      <g stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        {/* Sand mound + secondary contour */}
        <path d="M12 96 C 38 89, 62 93, 80 92 C 102 91, 126 88, 148 96" />
        <path d="M30 103 C 58 99, 100 101, 130 102" opacity="0.35" />

        {/* Central keep */}
        <path d="M63 40 L67 92 M97 40 L93 92" />
        <path d="M61 40 v-8 h7.2 v5 h7.2 v-5 h7.2 v5 h7.2 v-5 h7.2 v8" />
        <path d="M75 92 v-11 a5 5.5 0 0 1 10 0 v11" />
        <path d="M68.3 64 h23.4 M69 76 h22" opacity="0.3" />

        {/* Left tower (complete) */}
        <path d="M36 62 L40 92 M58 62 L54 92" />
        <path d="M34 62 v-6 h5.2 v3.5 h5.2 v-3.5 h5.2 v3.5 h5.2 v-3.5 h5.2 v6" />
        <path d="M38 74 h17 M39 84 h15.5" opacity="0.3" />

        {/* Right tower: only the base is built... */}
        <path d="M103.5 80 L106 92 M122.5 80 L120 92" />
        <path d="M40 92 H120" />

        {/* ...the rest is still a plan (dashed) */}
        <g strokeDasharray="3 3" opacity="0.5">
          <path d="M102 68 L103.5 80 M124 68 L122.5 80" />
          <path d="M100 68 v-5.5 h5.2 v3.5 h5.2 v-3.5 h5.2 v3.5 h5.2 v-3.5 h5.2 v5.5" />
        </g>

        {/* Bare flag pole - the flag goes up when the build is done */}
        <path d="M79 32 V18" />

        {/* Bucket (left) */}
        <path d="M23 76 L26 93 L40 93 L43 76" />
        <path d="M21 76 h24" />
        <path d="M24.5 76 A 8.7 8 0 0 1 41.5 76" />

        {/* Shovel (right) */}
        <path d="M137 66 L131 92" />
        <path d="M134.5 65 l5.5 1.8" />

        {/* Fresh sand pile */}
        <path d="M112 93 q 8 -10 17 0" />
        <path d="M117 89 l-2.5 4 M122.5 88.5 l-2.5 4.5" opacity="0.4" />
      </g>

      {/* Amber details: bucket band + shovel blade */}
      <path d="M25.4 82 h13.2" stroke="var(--color-accent)" strokeWidth="2.5" strokeLinecap="round" opacity="0.9" />
      <path d="M128.5 89 L135 91.5 L130.5 98 Z" fill="var(--color-accent)" opacity="0.9" />
    </svg>
  );
}
