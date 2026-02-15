import { formatCost } from "@/lib/utils";

interface CostDisplayProps {
  cost: number;
  className?: string;
}

export function CostDisplay({ cost, className }: CostDisplayProps) {
  return <span className={className}>{formatCost(cost)}</span>;
}
