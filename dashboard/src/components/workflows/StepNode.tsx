import { memo } from "react";
import { Handle, Position, type NodeProps, type Node } from "@xyflow/react";
import { Cpu, FileSpreadsheet, FlaskConical, Gauge, RefreshCw, ShieldCheck } from "lucide-react";
import { cn } from "@/lib/utils";
import { STATUS_DOT_COLORS } from "@/lib/constants";

type StepNodeData = {
  label: string;
  model?: string;
  status?: string;
  hasRetry?: boolean;
  hasApproval?: boolean;
  hasAutoPilot?: boolean;
  hasCsvOutput?: boolean;
  hasSlo?: boolean;
};

type StepNodeType = Node<StepNodeData, "step">;

function StepNodeComponent({ data, selected }: NodeProps<StepNodeType>) {
  const status = data.status || "pending";
  const dotColor = STATUS_DOT_COLORS[status] || "bg-muted";
  const showBadges = data.hasRetry || data.hasApproval || data.hasAutoPilot || data.hasCsvOutput || data.hasSlo;

  return (
    <div
      className={cn(
        "rounded-lg border bg-surface px-4 py-3 shadow-sm min-w-[140px]",
        "transition-all duration-200",
        selected ? "border-accent shadow-md ring-2 ring-accent/20" : "border-border"
      )}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!bg-accent !border-surface !w-2.5 !h-2.5"
      />

      <div className="flex items-center gap-2">
        <div className={cn("h-2 w-2 rounded-full", dotColor)} />
        <Cpu className="h-3.5 w-3.5 text-muted" />
        <span className="text-xs font-medium text-foreground">{data.label}</span>
      </div>

      {data.model && (
        <div className="mt-1.5">
          <span className="rounded-md bg-accent/10 px-1.5 py-0.5 text-[10px] font-medium text-accent">
            {data.model}
          </span>
        </div>
      )}

      {showBadges && (
        <div className="mt-1.5 flex items-center gap-1.5">
          {data.hasRetry && (
            <RefreshCw className="h-3 w-3 text-running" />
          )}
          {data.hasApproval && (
            <ShieldCheck className="h-3 w-3 text-warning" />
          )}
          {data.hasAutoPilot && (
            <FlaskConical className="h-3 w-3 text-accent" />
          )}
          {data.hasCsvOutput && (
            <FileSpreadsheet className="h-3 w-3 text-running" />
          )}
          {data.hasSlo && (
            <Gauge className="h-3 w-3 text-success" />
          )}
        </div>
      )}

      <Handle
        type="source"
        position={Position.Bottom}
        className="!bg-accent !border-surface !w-2.5 !h-2.5"
      />
    </div>
  );
}

export const StepNode = memo(StepNodeComponent);
