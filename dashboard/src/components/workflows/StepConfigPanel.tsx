import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Gauge,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { cn } from "@/lib/utils";

export interface RetryConfig {
  enabled: boolean;
  maxAttempts: number;
  backoff: "exponential" | "fixed";
  onFailure: "abort" | "skip" | "fallback";
}

export interface ApprovalConfig {
  enabled: boolean;
  message: string;
  timeoutHours: number;
  onTimeout: "abort" | "skip";
  allowEdit: boolean;
}

export interface SloConfig {
  enabled: boolean;
  qualityMin: number;
  costMaxUsd: number;
  latencyMaxSeconds: number;
  optimizeFor: "cost" | "quality" | "latency" | "balanced";
}

export interface StepConfig {
  id: string;
  prompt: string;
  model: string;
  maxTurns: number;
  timeout: number;
  parallelOver: string;
  dependsOn: string[];
  retry: RetryConfig;
  approval: ApprovalConfig;
  policies: string[];
  slo: SloConfig;
}

interface StepConfigPanelProps {
  step: StepConfig;
  allStepIds: string[];
  onChange: (step: StepConfig) => void;
  onDelete: () => void;
}

const POLICY_OPTIONS = [
  { id: "pii-redact", label: "PII Redact" },
  { id: "secret-block", label: "Secret Block" },
  { id: "cost-guard", label: "Cost Guard" },
  { id: "length-limit", label: "Length Limit" },
];

function CollapsibleSection({
  icon: Icon,
  title,
  enabled,
  onToggle,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  enabled: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded-lg border border-border">
      <button
        type="button"
        onClick={() => {
          if (!enabled) {
            onToggle();
            setOpen(true);
          } else {
            setOpen(!open);
          }
        }}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <Icon className="h-3.5 w-3.5 text-muted" />
        <span className="flex-1 text-xs font-medium text-foreground">{title}</span>
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => {
            e.stopPropagation();
            onToggle();
            if (!enabled) setOpen(true);
          }}
          className="rounded border-border text-accent focus:ring-accent"
        />
        {enabled && (
          open
            ? <ChevronDown className="h-3.5 w-3.5 text-muted" />
            : <ChevronRight className="h-3.5 w-3.5 text-muted" />
        )}
      </button>
      {enabled && open && (
        <div className="border-t border-border px-3 py-2.5 space-y-3">
          {children}
        </div>
      )}
    </div>
  );
}

export function StepConfigPanel({ step, allStepIds, onChange, onDelete }: StepConfigPanelProps) {
  const [customPolicy, setCustomPolicy] = useState("");

  const inputClass = cn(
    "h-9 w-full rounded-lg border border-border bg-background px-3 text-sm",
    "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30"
  );

  return (
    <div className="space-y-4 p-4">
      <h3 className="text-sm font-semibold text-foreground">Step Configuration</h3>

      <div>
        <label className="mb-1 block text-xs font-medium text-muted">Step ID</label>
        <input
          type="text"
          value={step.id}
          onChange={(e) => onChange({ ...step, id: e.target.value })}
          className={inputClass}
        />
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-muted">Prompt</label>
        <textarea
          value={step.prompt}
          onChange={(e) => onChange({ ...step, prompt: e.target.value })}
          rows={6}
          className={cn(inputClass, "h-auto py-2 resize-y")}
        />
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-muted">Model</label>
        <select
          value={step.model}
          onChange={(e) => onChange({ ...step, model: e.target.value })}
          className={inputClass}
        >
          <option value="sonnet">Sonnet</option>
          <option value="opus">Opus</option>
          <option value="haiku">Haiku</option>
        </select>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="mb-1 block text-xs font-medium text-muted">Max Turns</label>
          <input
            type="number"
            value={step.maxTurns}
            onChange={(e) => onChange({ ...step, maxTurns: Number(e.target.value) })}
            min={1}
            className={inputClass}
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-muted">Timeout (s)</label>
          <input
            type="number"
            value={step.timeout}
            onChange={(e) => onChange({ ...step, timeout: Number(e.target.value) })}
            min={1}
            className={inputClass}
          />
        </div>
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-muted">Parallel Over</label>
        <input
          type="text"
          value={step.parallelOver}
          onChange={(e) => onChange({ ...step, parallelOver: e.target.value })}
          placeholder="e.g. steps.scrape.output"
          className={inputClass}
        />
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-muted">Depends On</label>
        <div className="space-y-1">
          {allStepIds
            .filter((sid) => sid !== step.id)
            .map((sid) => (
              <label key={sid} className="flex items-center gap-2 text-sm text-foreground">
                <input
                  type="checkbox"
                  checked={step.dependsOn.includes(sid)}
                  onChange={(e) => {
                    const deps = e.target.checked
                      ? [...step.dependsOn, sid]
                      : step.dependsOn.filter((d) => d !== sid);
                    onChange({ ...step, dependsOn: deps });
                  }}
                  className="rounded border-border text-accent focus:ring-accent"
                />
                {sid}
              </label>
            ))}
          {allStepIds.filter((sid) => sid !== step.id).length === 0 && (
            <p className="text-xs text-muted-foreground">No other steps to depend on</p>
          )}
        </div>
      </div>

      {/* Advanced sections */}
      <div className="space-y-2 pt-2">
        <p className="text-xs font-semibold text-muted">ADVANCED</p>

        {/* Retry */}
        <CollapsibleSection
          icon={RefreshCw}
          title="Retry"
          enabled={step.retry.enabled}
          onToggle={() =>
            onChange({ ...step, retry: { ...step.retry, enabled: !step.retry.enabled } })
          }
        >
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Max Attempts</label>
            <input
              type="number"
              value={step.retry.maxAttempts}
              onChange={(e) =>
                onChange({ ...step, retry: { ...step.retry, maxAttempts: Number(e.target.value) } })
              }
              min={1}
              max={10}
              className={inputClass}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Backoff</label>
            <select
              value={step.retry.backoff}
              onChange={(e) =>
                onChange({
                  ...step,
                  retry: { ...step.retry, backoff: e.target.value as "exponential" | "fixed" },
                })
              }
              className={inputClass}
            >
              <option value="exponential">Exponential</option>
              <option value="fixed">Fixed</option>
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">On Failure</label>
            <select
              value={step.retry.onFailure}
              onChange={(e) =>
                onChange({
                  ...step,
                  retry: {
                    ...step.retry,
                    onFailure: e.target.value as "abort" | "skip" | "fallback",
                  },
                })
              }
              className={inputClass}
            >
              <option value="abort">Abort</option>
              <option value="skip">Skip</option>
              <option value="fallback">Fallback</option>
            </select>
          </div>
        </CollapsibleSection>

        {/* Approval Gate */}
        <CollapsibleSection
          icon={ShieldCheck}
          title="Approval Gate"
          enabled={step.approval.enabled}
          onToggle={() =>
            onChange({
              ...step,
              approval: { ...step.approval, enabled: !step.approval.enabled },
            })
          }
        >
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Message</label>
            <input
              type="text"
              value={step.approval.message}
              onChange={(e) =>
                onChange({
                  ...step,
                  approval: { ...step.approval, message: e.target.value },
                })
              }
              placeholder="Review before proceeding"
              className={inputClass}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Timeout (hours)</label>
            <input
              type="number"
              value={step.approval.timeoutHours}
              onChange={(e) =>
                onChange({
                  ...step,
                  approval: { ...step.approval, timeoutHours: Number(e.target.value) },
                })
              }
              min={1}
              className={inputClass}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">On Timeout</label>
            <select
              value={step.approval.onTimeout}
              onChange={(e) =>
                onChange({
                  ...step,
                  approval: {
                    ...step.approval,
                    onTimeout: e.target.value as "abort" | "skip",
                  },
                })
              }
              className={inputClass}
            >
              <option value="abort">Abort</option>
              <option value="skip">Skip</option>
            </select>
          </div>
          <label className="flex items-center gap-2 text-sm text-foreground">
            <input
              type="checkbox"
              checked={step.approval.allowEdit}
              onChange={(e) =>
                onChange({
                  ...step,
                  approval: { ...step.approval, allowEdit: e.target.checked },
                })
              }
              className="rounded border-border text-accent focus:ring-accent"
            />
            <span className="text-xs">Allow reviewer edits</span>
          </label>
        </CollapsibleSection>

        {/* Policies */}
        <CollapsibleSection
          icon={ShieldAlert}
          title="Policies"
          enabled={step.policies.length > 0}
          onToggle={() => {
            if (step.policies.length > 0) {
              onChange({ ...step, policies: [] });
            } else {
              onChange({ ...step, policies: ["pii-redact"] });
            }
          }}
        >
          <div className="space-y-1">
            {POLICY_OPTIONS.map((p) => (
              <label key={p.id} className="flex items-center gap-2 text-sm text-foreground">
                <input
                  type="checkbox"
                  checked={step.policies.includes(p.id)}
                  onChange={(e) => {
                    const policies = e.target.checked
                      ? [...step.policies, p.id]
                      : step.policies.filter((x) => x !== p.id);
                    onChange({ ...step, policies });
                  }}
                  className="rounded border-border text-accent focus:ring-accent"
                />
                <span className="text-xs">{p.label}</span>
              </label>
            ))}
          </div>
          <div className="flex gap-2">
            <input
              type="text"
              value={customPolicy}
              onChange={(e) => setCustomPolicy(e.target.value)}
              placeholder="Custom policy..."
              className={cn(inputClass, "text-xs")}
            />
            <button
              type="button"
              onClick={() => {
                if (customPolicy.trim() && !step.policies.includes(customPolicy.trim())) {
                  onChange({ ...step, policies: [...step.policies, customPolicy.trim()] });
                  setCustomPolicy("");
                }
              }}
              className="shrink-0 rounded-lg border border-border px-2 py-1 text-xs font-medium text-muted hover:text-foreground transition-colors"
            >
              Add
            </button>
          </div>
          {step.policies
            .filter((p) => !POLICY_OPTIONS.some((o) => o.id === p))
            .map((p) => (
              <div key={p} className="flex items-center justify-between">
                <span className="text-xs text-foreground">{p}</span>
                <button
                  type="button"
                  onClick={() =>
                    onChange({ ...step, policies: step.policies.filter((x) => x !== p) })
                  }
                  className="text-xs text-error/70 hover:text-error"
                >
                  Remove
                </button>
              </div>
            ))}
        </CollapsibleSection>

        {/* SLO Optimizer */}
        <CollapsibleSection
          icon={Gauge}
          title="SLO Optimizer"
          enabled={step.slo.enabled}
          onToggle={() =>
            onChange({ ...step, slo: { ...step.slo, enabled: !step.slo.enabled } })
          }
        >
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">
              Min Quality (0-1)
            </label>
            <input
              type="number"
              value={step.slo.qualityMin}
              onChange={(e) =>
                onChange({
                  ...step,
                  slo: { ...step.slo, qualityMin: Number(e.target.value) },
                })
              }
              min={0}
              max={1}
              step={0.1}
              className={inputClass}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">
              Max Cost (USD)
            </label>
            <input
              type="number"
              value={step.slo.costMaxUsd}
              onChange={(e) =>
                onChange({
                  ...step,
                  slo: { ...step.slo, costMaxUsd: Number(e.target.value) },
                })
              }
              min={0}
              step={0.01}
              className={inputClass}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">
              Max Latency (s)
            </label>
            <input
              type="number"
              value={step.slo.latencyMaxSeconds}
              onChange={(e) =>
                onChange({
                  ...step,
                  slo: { ...step.slo, latencyMaxSeconds: Number(e.target.value) },
                })
              }
              min={1}
              className={inputClass}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">Optimize For</label>
            <select
              value={step.slo.optimizeFor}
              onChange={(e) =>
                onChange({
                  ...step,
                  slo: {
                    ...step.slo,
                    optimizeFor: e.target.value as "cost" | "quality" | "latency" | "balanced",
                  },
                })
              }
              className={inputClass}
            >
              <option value="balanced">Balanced</option>
              <option value="cost">Cost</option>
              <option value="quality">Quality</option>
              <option value="latency">Latency</option>
            </select>
          </div>
        </CollapsibleSection>
      </div>

      <button
        onClick={onDelete}
        className="flex w-full items-center justify-center gap-2 rounded-lg border border-error/30 px-3 py-2 text-xs font-medium text-error hover:bg-error/10 transition-colors"
      >
        <Trash2 className="h-3.5 w-3.5" />
        Delete Step
      </button>
    </div>
  );
}
