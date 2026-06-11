/**
 * Skeleton loader with a warm sand shimmer sweep.
 * Base tint + sweep gradient live in styles/brand.css (token-based,
 * adapts to both themes).
 */
export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      className={`skeleton-sand relative overflow-hidden rounded-md ${className}`}
      aria-hidden="true"
    >
      <div className="skeleton-sand-sweep absolute inset-0" />
    </div>
  );
}
