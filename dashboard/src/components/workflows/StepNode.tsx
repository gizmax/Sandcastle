import { memo } from "react";
import { Handle, Position, type NodeProps, type Node } from "@xyflow/react";
import { Bell, Code, Cpu, ExternalLink, FileSpreadsheet, FileText, FlaskConical, Gauge, GitBranch, Globe, MessageSquare, Radio, RefreshCw, Repeat, ShieldCheck, Shuffle, Tag, Wrench, Zap } from "lucide-react";
import { cn } from "@/lib/utils";
import { STATUS_DOT_COLORS } from "@/lib/constants";

type StepType = "standard" | "llm" | "http" | "code" | "condition" | "classify" | "loop" | "approval" | "sub_workflow" | "race" | "sensor" | "gate" | "transform" | "notify" | "delegate";

type StepNodeData = {
  label: string;
  model?: string;
  status?: string;
  stepType?: StepType;
  hasRetry?: boolean;
  hasApproval?: boolean;
  hasAutoPilot?: boolean;
  hasCsvOutput?: boolean;
  hasPdfReport?: boolean;
  hasSlo?: boolean;
  hasTools?: boolean;
  toolNames?: string[];
};

type StepNodeType = Node<StepNodeData, "step">;

const STEP_TYPE_ICONS: Record<string, typeof Cpu> = {
  standard: Cpu,
  llm: MessageSquare,
  http: Globe,
  code: Code,
  condition: GitBranch,
  classify: Tag,
  loop: Repeat,
  approval: ShieldCheck,
  race: Zap,
  sensor: Radio,
  gate: ShieldCheck,
  transform: Shuffle,
  notify: Bell,
  delegate: ExternalLink,
};

const STEP_TYPE_COLORS: Record<string, string> = {
  standard: "text-muted",
  llm: "text-accent",
  http: "text-emerald-400",
  code: "text-amber-400",
  condition: "text-violet-400",
  classify: "text-pink-400",
  loop: "text-cyan-400",
  approval: "text-warning",
  race: "text-purple-400",
  sensor: "text-teal-400",
  gate: "text-red-400",
  transform: "text-cyan-400",
  notify: "text-pink-400",
  delegate: "text-indigo-400",
};

const STEP_TYPE_BORDER: Record<string, string> = {
  http: "border-l-emerald-400/50",
  code: "border-l-amber-400/50",
  condition: "border-l-violet-400/50",
  classify: "border-l-pink-400/50",
  loop: "border-l-cyan-400/50",
  llm: "border-l-accent/50",
  race: "border-l-purple-400/50",
  sensor: "border-l-teal-400/50",
  gate: "border-l-red-400/50",
  transform: "border-l-cyan-400/50",
  notify: "border-l-pink-400/50",
  delegate: "border-l-indigo-400/50",
};

function StepNodeComponent({ data, selected }: NodeProps<StepNodeType>) {
  const status = data.status || "pending";
  const dotColor = STATUS_DOT_COLORS[status] || "bg-muted";
  const showBadges = data.hasRetry || data.hasApproval || data.hasAutoPilot || data.hasCsvOutput || data.hasPdfReport || data.hasSlo || data.hasTools;
  const stepType = data.stepType || "standard";
  const TypeIcon = STEP_TYPE_ICONS[stepType] || Cpu;
  const iconColor = STEP_TYPE_COLORS[stepType] || "text-muted";
  const leftBorder = STEP_TYPE_BORDER[stepType] || "";

  return (
    <div
      className={cn(
        "rounded-lg border bg-surface px-4 py-3 shadow-sm min-w-[140px]",
        "transition-all duration-200",
        leftBorder && "border-l-2",
        leftBorder,
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
        <TypeIcon className={cn("h-3.5 w-3.5", iconColor)} />
        <span className="text-xs font-medium text-foreground">{data.label}</span>
      </div>

      <div className="mt-1.5 flex items-center gap-1">
        {stepType !== "standard" && (
          <span className={cn("rounded-md px-1.5 py-0.5 text-[10px] font-medium", iconColor, "bg-current/10")}>
            {stepType}
          </span>
        )}
        {data.model && stepType !== "http" && stepType !== "code" && stepType !== "condition" && stepType !== "loop" && stepType !== "race" && stepType !== "sensor" && stepType !== "transform" && stepType !== "notify" && (
          <span className="rounded-md bg-accent/10 px-1.5 py-0.5 text-[10px] font-medium text-accent">
            {data.model}
          </span>
        )}
      </div>

      {showBadges && (
        <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
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
          {data.hasPdfReport && (
            <FileText className="h-3 w-3 text-accent" />
          )}
          {data.hasSlo && (
            <Gauge className="h-3 w-3 text-success" />
          )}
          {data.hasTools && !data.toolNames?.length && (
            <Wrench className="h-3 w-3 text-blue-400" />
          )}
        </div>
      )}

      {/* Tool chips */}
      {data.toolNames && data.toolNames.length > 0 && (
        <div className="mt-1.5 flex items-center gap-1 flex-wrap">
          <Wrench className="h-2.5 w-2.5 text-blue-400 shrink-0" />
          {data.toolNames.map((name) => (
            <span
              key={name}
              className="rounded-md bg-blue-500/10 px-1.5 py-0.5 text-[9px] font-medium text-blue-400 leading-none"
            >
              {name}
            </span>
          ))}
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
