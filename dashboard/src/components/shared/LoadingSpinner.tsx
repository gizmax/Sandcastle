import { cn } from "@/lib/utils";

interface LoadingSpinnerProps {
  className?: string;
  size?: "sm" | "md" | "lg";
}

const sizes = {
  sm: "h-4 w-4 border-[1.5px]",
  md: "h-6 w-6 border-2",
  lg: "h-8 w-8 border-2",
};

export function LoadingSpinner({ className, size = "md" }: LoadingSpinnerProps) {
  return (
    <div
      role="status"
      aria-label="Loading"
      className={cn(
        "animate-spin rounded-full border-muted-foreground/30 border-t-accent",
        "shadow-[0_0_8px_rgba(245,158,11,0.2)]",
        sizes[size],
        className
      )}
      style={{
        animation: "spin 0.8s linear infinite, pulse-glow 2s ease-in-out infinite",
      }}
    />
  );
}
