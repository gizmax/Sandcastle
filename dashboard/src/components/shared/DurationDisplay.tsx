import { formatDuration } from "@/lib/utils";

interface DurationDisplayProps {
  seconds: number;
  className?: string;
}

export function DurationDisplay({ seconds, className }: DurationDisplayProps) {
  return <span className={className}>{formatDuration(seconds)}</span>;
}
