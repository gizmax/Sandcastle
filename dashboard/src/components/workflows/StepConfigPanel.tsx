import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  FolderOpen,
  Gauge,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { DirectoryBrowser } from "@/components/workflows/DirectoryBrowser";
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

export interface DirectoryInputConfig {
  enabled: boolean;
  defaultPath: string;
}

export interface StepConfig {
  id: string;
  prompt: string;
  model: string;
  maxTurns: number;
  timeout: number;
  parallelOver: string;
  dependsOn: string[];
  directoryInput: DirectoryInputConfig;
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
  { id: "pii-redact", label: "PII Redact", hint: "Detects and replaces emails, phones, SSNs in output." },
  { id: "secret-block", label: "Secret Block", hint: "Blocks execution if API keys or tokens are found." },
  { id: "cost-guard", label: "Cost Guard", hint: "Stops step if cost exceeds the per-step budget." },
  { id: "length-limit", label: "Length Limit", hint: "Flags output exceeding the token length limit." },
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
  const [browseOpen, setBrowseOpen] = useState(false);

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
        <p className="text-[11px] text-muted-foreground mt-0.5">Unique identifier used in depends_on and YAML output.</p>
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-muted">Prompt</label>
        <textarea
          value={step.prompt}
          onChange={(e) => onChange({ ...step, prompt: e.target.value })}
          rows={6}
          className={cn(inputClass, "h-auto py-2 resize-y")}
        />
        <p className="text-[11px] text-muted-foreground mt-0.5">{"Use {input.field} for workflow input or {steps.id.output} for previous step data."}</p>
      </div>

      {/* Directory Input */}
      <div className="rounded-lg border border-border">
        <button
          type="button"
          onClick={() => {
            const next = !step.directoryInput.enabled;
            onChange({
              ...step,
              directoryInput: { ...step.directoryInput, enabled: next },
            });
          }}
          className="flex w-full items-center gap-2 px-3 py-2 text-left"
        >
          <FolderOpen className="h-3.5 w-3.5 text-muted" />
          <span className="flex-1 text-xs font-medium text-foreground">Directory Input</span>
          <input
            type="checkbox"
            checked={step.directoryInput.enabled}
            onChange={(e) => {
              e.stopPropagation();
              onChange({
                ...step,
                directoryInput: { ...step.directoryInput, enabled: e.target.checked },
              });
            }}
            className="rounded border-border text-accent focus:ring-accent"
          />
        </button>
        {step.directoryInput.enabled && (
          <div className="border-t border-border px-3 py-2.5 space-y-2">
            <p className="text-[11px] text-muted-foreground">
              This step expects a directory path as input. Use <code className="text-[10px] bg-background px-1 rounded">{"{"} input.directory {"}"}</code> in your prompt.
            </p>
            <div className="flex gap-2">
              <input
                type="text"
                value={step.directoryInput.defaultPath}
                onChange={(e) =>
                  onChange({
                    ...step,
                    directoryInput: { ...step.directoryInput, defaultPath: e.target.value },
                  })
                }
                placeholder="~/Desktop"
                className={cn(inputClass, "text-xs")}
              />
              <button
                type="button"
                onClick={() => setBrowseOpen(true)}
                className="shrink-0 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium text-muted hover:text-foreground hover:border-accent transition-colors"
              >
                Browse
              </button>
            </div>
          </div>
        )}
      </div>

      <DirectoryBrowser
        open={browseOpen}
        initialPath={step.directoryInput.defaultPath || "~"}
        onSelect={(path) => {
          onChange({
            ...step,
            directoryInput: { ...step.directoryInput, defaultPath: path },
          });
          setBrowseOpen(false);
        }}
        onClose={() => setBrowseOpen(false)}
      />

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
        <p className="text-[11px] text-muted-foreground mt-0.5">Sonnet is balanced, Opus for complex reasoning, Haiku for fast/cheap tasks.</p>
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
          <p className="text-[11px] text-muted-foreground mt-0.5">Maximum agent conversation turns before timeout.</p>
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
          <p className="text-[11px] text-muted-foreground mt-0.5">Hard time limit in seconds for this step.</p>
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
        <p className="text-[11px] text-muted-foreground mt-0.5">JSONPath to a list. Step runs once per item in parallel.</p>
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
            <p className="text-[11px] text-muted-foreground mt-0.5">Total tries including the first attempt.</p>
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
            <p className="text-[11px] text-muted-foreground mt-0.5">Exponential doubles delay each retry. Fixed waits the same interval.</p>
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
            <p className="text-[11px] text-muted-foreground mt-0.5">Abort stops the run. Skip continues to next step. Fallback uses a simpler model.</p>
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
            <p className="text-[11px] text-muted-foreground mt-0.5">Shown to reviewer in the Approvals dashboard.</p>
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
            <p className="text-[11px] text-muted-foreground mt-0.5">Auto-resolves if nobody responds within this time.</p>
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
            <p className="text-[11px] text-muted-foreground mt-0.5">What happens when approval times out.</p>
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
          <p className="text-[11px] text-muted-foreground mt-0.5">Reviewer can modify the step's output data before approving.</p>
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
          <p className="text-[11px] text-muted-foreground">Rules evaluated against step output. Violations are logged.</p>
          <div className="space-y-2">
            {POLICY_OPTIONS.map((p) => (
              <div key={p.id}>
                <label className="flex items-center gap-2 text-sm text-foreground">
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
                <p className="text-[11px] text-muted-foreground ml-6">{p.hint}</p>
              </div>
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
          <p className="text-[11px] text-muted-foreground">Automatically selects the best model based on constraints.</p>
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
            <p className="text-[11px] text-muted-foreground mt-0.5">Minimum acceptable quality score (0 = any, 1 = perfect).</p>
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
            <p className="text-[11px] text-muted-foreground mt-0.5">Per-step spending limit in USD.</p>
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
            <p className="text-[11px] text-muted-foreground mt-0.5">Maximum allowed execution time.</p>
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
            <p className="text-[11px] text-muted-foreground mt-0.5">Primary objective when multiple models meet constraints.</p>
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
