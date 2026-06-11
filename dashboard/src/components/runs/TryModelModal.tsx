import { useEffect, useMemo, useState } from "react";
import { X, FlaskConical, AlertTriangle } from "lucide-react";
import { cn, formatCost } from "@/lib/utils";
import { MODEL_GROUPS } from "@/lib/modelOptions";

export interface TryModelStep {
  step_id: string;
  model?: string | null;
}

interface TryModelModalProps {
  open: boolean;
  onClose: () => void;
  runId: string;
  /** Steps of the run, so the user can choose where to swap the model. */
  steps: TryModelStep[];
  /** Total cost of the original run — used for a confirm/cost hint. */
  originalCostUsd?: number;
  submitting?: boolean;
  /**
   * Fork the run from `fromStep` with a model override. Wired by the caller to
   * POST /runs/{run_id}/fork with `changes: { model }`.
   */
  onSubmit: (data: { from_step: string; model: string }) => Promise<void> | void;
}

/**
 * "Try another model" — replay/fork this REAL run with a different model so you
 * can A/B a provider on actual inputs, then land on the new run (and compare).
 * Forking re-executes from the chosen step onward, which costs money, so the
 * footer carries an explicit cost hint + confirm.
 */
export function TryModelModal({
  open,
  onClose,
  runId,
  steps,
  originalCostUsd,
  submitting = false,
  onSubmit,
}: TryModelModalProps) {
  const firstStepId = steps[0]?.step_id ?? "";
  const [model, setModel] = useState("");
  const [fromStep, setFromStep] = useState(firstStepId);

  useEffect(() => {
    if (open) {
      setModel("");
      setFromStep(steps[0]?.step_id ?? "");
    }
  }, [open, steps]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  const selectedStep = useMemo(
    () => steps.find((s) => s.step_id === fromStep),
    [steps, fromStep]
  );

  if (!open) return null;

  const canSubmit = !!model && !!fromStep && !submitting;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Try another model"
        className="w-full max-w-lg rounded-xl border border-border bg-surface p-6 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-5 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <FlaskConical className="h-5 w-5 text-accent" />
            <h2 className="text-lg font-semibold text-foreground">Try another model</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-md p-1 text-muted hover:bg-border/40 hover:text-foreground transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="mb-4 rounded-lg bg-background px-3 py-2 text-sm">
          <p className="text-muted">
            Re-run this real execution with a different model to compare quality and cost on
            the same inputs. A new run is forked from the step you pick.
          </p>
          <p className="mt-1 font-mono text-xs text-muted">Run: {runId.slice(0, 8)}...</p>
        </div>

        <div className="space-y-4">
          {steps.length > 1 && (
            <div>
              <label htmlFor="try-model-step" className="mb-1 block text-xs font-medium text-muted">
                Swap model from step
              </label>
              <select
                id="try-model-step"
                value={fromStep}
                onChange={(e) => setFromStep(e.target.value)}
                className={cn(
                  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground",
                  "focus-visible:border-accent focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
                )}
              >
                {steps.map((s) => (
                  <option key={s.step_id} value={s.step_id}>
                    {s.step_id}
                    {s.model ? ` (currently ${s.model})` : ""}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div>
            <label htmlFor="try-model-select" className="mb-1 block text-xs font-medium text-muted">
              New model
            </label>
            <select
              id="try-model-select"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className={cn(
                "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground",
                "focus-visible:border-accent focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
              )}
            >
              <option value="">Select a model...</option>
              {MODEL_GROUPS.map((g) => (
                <optgroup key={g.label} label={g.label}>
                  {g.options.map((o) => (
                    <option
                      key={o.value}
                      value={o.value}
                      disabled={selectedStep?.model === o.value}
                    >
                      {o.label}
                      {selectedStep?.model === o.value ? " (current)" : ""}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>
        </div>

        {/* Cost hint — forking re-executes steps and spends real budget. */}
        <div className="mt-4 flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-xs text-warning">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            This starts a new paid run.
            {typeof originalCostUsd === "number" && originalCostUsd > 0 ? (
              <> The original run cost {formatCost(originalCostUsd)}; the fork should be similar.</>
            ) : (
              <> You'll be charged for the re-executed steps.</>
            )}
          </span>
        </div>

        <div className="mt-6 flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-muted hover:bg-border/40 hover:text-foreground transition-colors"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => model && fromStep && onSubmit({ from_step: fromStep, model })}
            disabled={!canSubmit}
            className={cn(
              "flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground",
              "hover:bg-accent-hover transition-all duration-200 shadow-sm hover:shadow-md",
              "disabled:opacity-50 disabled:cursor-not-allowed"
            )}
          >
            <FlaskConical className="h-4 w-4" />
            {submitting ? "Forking..." : "Fork & run"}
          </button>
        </div>
      </div>
    </div>
  );
}
