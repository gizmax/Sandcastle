import { cn } from "@/lib/utils";

interface ScoreRingProps {
  score: number;
  size?: "xs" | "sm" | "md" | "lg";
  className?: string;
}

const SIZES = {
  xs: { px: 16, stroke: 2, fontSize: "text-[7px]", radius: 6 },
  sm: { px: 56, stroke: 4, fontSize: "text-base", radius: 23 },
  md: { px: 72, stroke: 5, fontSize: "text-xl", radius: 28 },
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
      <svg width={cfg.px} height={cfg.px} className="-rotate-90" role="img" aria-label={`Health score: ${score} out of 100`}>
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
