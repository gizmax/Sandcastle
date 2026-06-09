import { memo } from "react";
import { Handle, Position, type NodeProps, type Node } from "@xyflow/react";
import { Bell, Brain, Code, Cpu, ExternalLink, FileSpreadsheet, FileText, FlaskConical, Gauge, GitBranch, Globe, MessageSquare, Monitor, Radio, RefreshCw, Repeat, ShieldCheck, Shuffle, Tag, Wrench, Zap } from "lucide-react";
import type { CSSProperties } from "react";
import { cn } from "@/lib/utils";
import { getLedConfig } from "@/lib/statusLed";
import type { StepType } from "@/components/workflows/StepConfigPanel";

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
  browserMode?: string;
  browserUrl?: string;
};

type StepNodeType = Node<StepNodeData, "step">;

const STEP_TYPE_ICONS: Record<string, typeof Cpu> = {
  standard: Brain,
  llm: MessageSquare,
  http: Globe,
  code: Code,
  condition: GitBranch,
  classify: Tag,
  loop: Repeat,
  approval: ShieldCheck,
  sub_workflow: ExternalLink,
  race: Zap,
  sensor: Radio,
  gate: ShieldCheck,
  transform: Shuffle,
  notify: Bell,
  delegate: ExternalLink,
  browser: Monitor,
};

const STEP_TYPE_COLORS: Record<string, string> = {
  standard: "text-violet-400",
  llm: "text-accent",
  http: "text-emerald-400",
  code: "text-amber-400",
  condition: "text-violet-400",
  classify: "text-pink-400",
  loop: "text-cyan-400",
  approval: "text-warning",
  sub_workflow: "text-indigo-400",
  race: "text-purple-400",
  sensor: "text-teal-400",
  gate: "text-red-400",
  transform: "text-cyan-400",
  notify: "text-pink-400",
  delegate: "text-indigo-400",
  browser: "text-fuchsia-400",
};

// Raw color values for left border and background tint
const STEP_TYPE_RAW_COLORS: Record<string, string> = {
  standard: "167 139 250",
  llm: "245 158 11",
  http: "52 211 153",
  code: "251 191 36",
  condition: "167 139 250",
  classify: "244 114 182",
  loop: "34 211 238",
  approval: "245 158 11",
  sub_workflow: "129 140 248",
  race: "192 132 252",
  sensor: "45 212 191",
  gate: "248 113 113",
  transform: "34 211 238",
  notify: "244 114 182",
  delegate: "129 140 248",
  browser: "217 70 239",
};

function StepNodeComponent({ data, selected }: NodeProps<StepNodeType>) {
  const status = data.status || "pending";
  const led = getLedConfig(status);
  const showBadges = data.hasRetry || data.hasApproval || data.hasAutoPilot || data.hasCsvOutput || data.hasPdfReport || data.hasSlo || data.hasTools;
  const stepType = data.stepType || "standard";
  const TypeIcon = STEP_TYPE_ICONS[stepType] || Cpu;
  const iconColor = STEP_TYPE_COLORS[stepType] || "text-muted";
  const rawColor = STEP_TYPE_RAW_COLORS[stepType] || "115 115 115";
  const isRunning = status === "running";

  return (
    <div
      className={cn(
        "rounded-lg border bg-surface px-4 py-3 shadow-sm min-w-[140px]",
        "transition-all duration-200",
        isRunning && "step-running-glow border-accent/60",
        selected
          ? "border-accent ring-2 ring-accent/30 shadow-[0_0_16px_rgba(245,158,11,0.2)]"
          : !isRunning && "border-border"
      )}
      style={{
        borderLeftWidth: "3px",
        borderLeftColor: `rgb(${rawColor})`,
        backgroundColor: `rgba(${rawColor}, 0.04)`,
      }}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!bg-accent !border-surface !w-2.5 !h-2.5"
      />

      <div className="flex items-center gap-2">
        <div
          className={cn("led h-2 w-2 rounded-full shrink-0", `led--${led.state}`)}
          style={{ "--led-color": led.color } as CSSProperties}
        />
        <TypeIcon className={cn("h-3.5 w-3.5", iconColor)} />
        <span className="font-mono text-xs font-medium text-foreground">{data.label}</span>
      </div>

      <div className="mt-1.5 flex items-center gap-1">
        {stepType !== "standard" && (
          <span className={cn("rounded-md px-1.5 py-0.5 text-[10px] font-medium", iconColor, "bg-current/10")}>
            {stepType}
          </span>
        )}
        {data.model && stepType !== "http" && stepType !== "code" && stepType !== "condition" && stepType !== "loop" && stepType !== "race" && stepType !== "sensor" && stepType !== "transform" && stepType !== "notify" && stepType !== "browser" && (
          <span className="rounded-md bg-accent/10 px-1.5 py-0.5 text-[10px] font-medium text-accent">
            {data.model}
          </span>
        )}
      </div>

      {/* Browser step subtitle: mode + start URL */}
      {stepType === "browser" && (data.browserMode || data.browserUrl) && (
        <div className="mt-1 flex flex-col gap-0.5">
          {data.browserMode && (
            <span className={cn(
              "text-[10px] truncate max-w-[160px]",
              data.browserMode === "computer_use"
                ? "text-warning"
                : data.browserMode === "dom"
                ? "text-success"
                : "text-running"
            )}>
              {data.browserMode === "computer_use"
                ? "Computer Use"
                : data.browserMode === "dom"
                ? "DOM Extract"
                : "Playwright"}
            </span>
          )}
          {data.browserUrl && (
            <span className="text-[10px] text-muted truncate max-w-[160px]" title={data.browserUrl}>
              {data.browserUrl}
            </span>
          )}
        </div>
      )}

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
