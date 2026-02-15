import { Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";

export interface StepConfig {
  id: string;
  prompt: string;
  model: string;
  maxTurns: number;
  timeout: number;
  parallelOver: string;
  dependsOn: string[];
}

interface StepConfigPanelProps {
  step: StepConfig;
  allStepIds: string[];
  onChange: (step: StepConfig) => void;
  onDelete: () => void;
}

export function StepConfigPanel({ step, allStepIds, onChange, onDelete }: StepConfigPanelProps) {
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
