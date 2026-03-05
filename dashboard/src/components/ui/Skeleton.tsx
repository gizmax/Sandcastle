/**
 * Skeleton loader with animated shimmer effect.
 * Uses the skeleton-shimmer keyframe defined in index.css.
 */
export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      className={`relative overflow-hidden rounded-md bg-border/50 ${className}`}
      aria-hidden="true"
    >
      <div
        className="absolute inset-0"
        style={{
          background:
            "linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.08) 50%, transparent 100%)",
          animation: "skeleton-shimmer 1.8s ease-in-out infinite",
        }}
      />
    </div>
  );
}
