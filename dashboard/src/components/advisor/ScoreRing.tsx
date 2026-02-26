import { cn } from "@/lib/utils";

interface ScoreRingProps {
  score: number;
  size?: "sm" | "lg";
  className?: string;
}

const SIZES = {
  sm: { px: 56, stroke: 4, fontSize: "text-base", radius: 23 },
  lg: { px: 120, stroke: 6, fontSize: "text-3xl", radius: 50 },
} as const;

function scoreColor(score: number): string {
  if (score >= 80) return "text-emerald-500";
  if (score >= 50) return "text-amber-500";
  return "text-error";
}

function strokeColor(score: number): string {
  if (score >= 80) return "stroke-emerald-500";
  if (score >= 50) return "stroke-amber-500";
  return "stroke-error";
}

export function ScoreRing({ score, size = "sm", className }: ScoreRingProps) {
  const cfg = SIZES[size];
  const center = cfg.px / 2;
  const circumference = 2 * Math.PI * cfg.radius;
  const offset = circumference - (Math.min(Math.max(score, 0), 100) / 100) * circumference;

  return (
    <div className={cn("relative inline-flex items-center justify-center", className)} style={{ width: cfg.px, height: cfg.px }}>
      <svg width={cfg.px} height={cfg.px} className="-rotate-90">
        {/* Track */}
        <circle
          cx={center}
          cy={center}
          r={cfg.radius}
          fill="none"
          strokeWidth={cfg.stroke}
          className="stroke-border"
        />
        {/* Arc */}
        <circle
          cx={center}
          cy={center}
          r={cfg.radius}
          fill="none"
          strokeWidth={cfg.stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          className={cn(strokeColor(score), "transition-all duration-700 ease-out")}
        />
      </svg>
      <span className={cn("absolute font-semibold font-data", cfg.fontSize, scoreColor(score))}>
        {score}
      </span>
    </div>
  );
}
