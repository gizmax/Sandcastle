/**
 * SandcastleEmpty - a small proud sandcastle with a tiny amber flag.
 * Fine line-art in currentColor so it adapts to both themes; amber
 * accent details come from the --color-accent token.
 *
 * Default usage is the generic "nothing here yet" empty state.
 */
interface Props {
  className?: string;
  /** Accessible label. Omit to render as purely decorative (aria-hidden). */
  title?: string;
}

export function SandcastleEmpty({ className, title }: Props) {
  return (
    <svg
      viewBox="0 0 160 120"
      data-illustration="sandcastle-empty"
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

        {/* Central keep (bucket taper: wider at top) */}
        <path d="M63 40 L67 92 M97 40 L93 92" />
        <path d="M61 40 v-8 h7.2 v5 h7.2 v-5 h7.2 v5 h7.2 v-5 h7.2 v8" />
        {/* Door arch */}
        <path d="M75 92 v-11 a5 5.5 0 0 1 10 0 v11" />
        {/* Sand ridges (texture) */}
        <path d="M68.3 64 h23.4 M69 76 h22" opacity="0.3" />

        {/* Left tower */}
        <path d="M36 62 L40 92 M58 62 L54 92" />
        <path d="M34 62 v-6 h5.2 v3.5 h5.2 v-3.5 h5.2 v3.5 h5.2 v-3.5 h5.2 v6" />
        <path d="M38 74 h17 M39 84 h15.5" opacity="0.3" />

        {/* Right tower */}
        <path d="M102 68 L106 92 M124 68 L120 92" />
        <path d="M100 68 v-5.5 h5.2 v3.5 h5.2 v-3.5 h5.2 v3.5 h5.2 v-3.5 h5.2 v5.5" />
        <path d="M104 78 h18 M105 86 h16" opacity="0.3" />

        {/* Baseline between towers and keep */}
        <path d="M40 92 H120" />

        {/* Flag pole */}
        <path d="M79 32 V15" />
      </g>

      {/* Amber details: pennant, window, tiny shells */}
      <path
        d="M79 15 L93 19 L79 23.5 Z"
        fill="var(--color-accent)"
        className="brand-flag"
      />
      <path d="M77.5 56 v-2.5 a2.5 2.5 0 0 1 5 0 V56 Z" fill="var(--color-accent)" opacity="0.7" />
      <circle cx="40" cy="99" r="1.3" fill="var(--color-accent)" opacity="0.5" />
      <circle cx="122" cy="98.5" r="1.3" fill="var(--color-accent)" opacity="0.5" />
    </svg>
  );
}
