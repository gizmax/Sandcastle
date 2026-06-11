/**
 * SandcastleRuin - a partially crumbled sandcastle: broken keep, collapsed
 * tower, drooping flag. For errors, failures, and 404s.
 */
interface Props {
  className?: string;
  /** Accessible label. Omit to render as purely decorative (aria-hidden). */
  title?: string;
}

export function SandcastleRuin({ className, title }: Props) {
  return (
    <svg
      viewBox="0 0 160 120"
      data-illustration="sandcastle-ruin"
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      className={className}
      fill="none"
    >
      <g stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        {/* Sand mound + receding tide line */}
        <path d="M12 96 C 38 90, 62 94, 80 93 C 102 92, 126 89, 148 96" />
        <path d="M126 102 q 5 -3 10 0 q 5 3 10 0" opacity="0.35" />
        <path d="M26 104 C 50 100, 80 102, 104 103" opacity="0.35" />

        {/* Keep: left half stands, right side broken away */}
        <path d="M61 44 v-8 h7.2 v5 h7.2 v-5 h7.2 v8 l3 9 l-6 4 l8 7 l-4 8 l4.4 20 H64 Z" />
        {/* Broken door arch */}
        <path d="M73 92 v-7 a4.5 4.5 0 0 1 4.5 -4.5" />
        {/* Crack */}
        <path d="M76 72 l2.5 -5 l-2 -4 l3 -4" opacity="0.6" />
        {/* Sand ridge */}
        <path d="M66 82 h16" opacity="0.3" />

        {/* Collapsed left tower: rubble mound + tumbled stones */}
        <path d="M28 92 q 7 -10 14 -4 q 6 -6 12 4" />
        <circle cx="38" cy="88.5" r="2" />
        <circle cx="48.5" cy="90" r="1.6" />

        {/* Right tower stub with jagged break */}
        <path d="M104 92 V78 l4.5 2 l3 -5.5 l4.5 3.5 V92" />
        <path d="M106 86 h8" opacity="0.3" />

        {/* Baseline */}
        <path d="M28 92 H120" />

        {/* Tilted flag pole */}
        <path d="M70 36 L74 20" />
      </g>

      {/* Amber details: drooping flag, half-buried shell */}
      <path d="M74 20 q 7 1.5 8.5 7 l-7.5 -2.5 Z" fill="var(--color-accent)" opacity="0.85" />
      <circle cx="118" cy="98" r="1.3" fill="var(--color-accent)" opacity="0.5" />
    </svg>
  );
}
