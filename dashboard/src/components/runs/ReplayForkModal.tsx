import { useState } from "react";
import { X, RotateCcw, GitFork } from "lucide-react";
import { cn } from "@/lib/utils";

interface ReplayForkModalProps {
  open: boolean;
  onClose: () => void;
  runId: string;
  stepId: string;
  mode: "replay" | "fork";
  onSubmit: (data: { from_step: string; changes?: Record<string, unknown> }) => Promise<void>;
}

export function ReplayForkModal({
  open,
  onClose,
  runId,
  stepId,
  mode,
  onSubmit,
}: ReplayForkModalProps) {
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("");
  const [maxTurns, setMaxTurns] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (!open) return null;

  const isReplay = mode === "replay";
  const title = isReplay ? "Replay from Step" : "Fork from Step";
  const Icon = isReplay ? RotateCcw : GitFork;
  const buttonText = isReplay ? "Replay" : "Fork";

  async function handleSubmit() {
    setSubmitting(true);
    try {
      const changes: Record<string, unknown> = {};
      if (prompt) changes.prompt = prompt;
      if (model) changes.model = model;
      if (maxTurns) changes.max_turns = Number(maxTurns);

      await onSubmit({
        from_step: stepId,
        changes: isReplay ? undefined : Object.keys(changes).length > 0 ? changes : undefined,
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-full max-w-lg rounded-xl border border-border bg-surface p-6 shadow-lg">
        {/* Header */}
        <div className="mb-5 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Icon className="h-5 w-5 text-accent" />
            <h2 className="text-lg font-semibold text-foreground">{title}</h2>
          </div>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-muted hover:bg-border/40 hover:text-foreground transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Info */}
        <div className="mb-4 rounded-lg bg-background px-3 py-2 text-sm">
          <p className="text-muted">
            {isReplay
              ? `Re-run from step "${stepId}" using the saved checkpoint. All subsequent steps will be re-executed.`
              : `Fork from step "${stepId}" with modified parameters. Previous steps use cached outputs.`}
          </p>
          <p className="mt-1 font-mono text-xs text-muted">Run: {runId.slice(0, 8)}...</p>
        </div>

        {/* Fork-specific fields */}
        {!isReplay && (
          <div className="space-y-4">
            <div>
              <label className="mb-1 block text-xs font-medium text-muted">
                Override Prompt (optional)
              </label>
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="Leave empty to keep original prompt..."
                rows={3}
                className={cn(
                  "w-full rounded-lg border border-border bg-background px-3 py-2",
                  "text-sm text-foreground placeholder:text-muted/50",
                  "focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent",
                  "transition-colors duration-200"
                )}
              />
            </div>

            <div className="flex gap-3">
              <div className="flex-1">
                <label className="mb-1 block text-xs font-medium text-muted">
                  Model (optional)
                </label>
                <select
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  className={cn(
                    "w-full rounded-lg border border-border bg-background px-3 py-2",
                    "text-sm text-foreground",
                    "focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent",
                    "transition-colors duration-200"
                  )}
                >
                  <option value="">Keep original</option>
                  <optgroup label="Claude (Anthropic)">
                    <option value="opus">Opus</option>
                    <option value="sonnet">Sonnet</option>
                    <option value="haiku">Haiku</option>
                  </optgroup>
                  <optgroup label="OpenAI">
                    <option value="openai/codex-mini">Codex Mini</option>
                    <option value="openai/codex">Codex</option>
                  </optgroup>
                  <optgroup label="MiniMax">
                    <option value="minimax/m2.5">MiniMax M2.5</option>
                  </optgroup>
                  <optgroup label="Google">
                    <option value="google/gemini-2.5-pro">Gemini 2.5 Pro</option>
                  </optgroup>
                </select>
              </div>
              <div className="w-28">
                <label className="mb-1 block text-xs font-medium text-muted">
                  Max Turns
                </label>
                <input
                  type="number"
                  value={maxTurns}
                  onChange={(e) => setMaxTurns(e.target.value)}
                  placeholder="10"
                  min={1}
                  max={100}
                  className={cn(
                    "w-full rounded-lg border border-border bg-background px-3 py-2",
                    "text-sm text-foreground placeholder:text-muted/50",
                    "focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent",
                    "transition-colors duration-200"
                  )}
                />
              </div>
            </div>
          </div>
        )}

        {/* Actions */}
        <div className="mt-6 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-muted hover:bg-border/40 hover:text-foreground transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting}
            className={cn(
              "flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground",
              "hover:bg-accent-hover transition-all duration-200",
              "shadow-sm hover:shadow-md",
              "disabled:opacity-50 disabled:cursor-not-allowed"
            )}
          >
            <Icon className="h-4 w-4" />
            {submitting ? "Processing..." : buttonText}
          </button>
        </div>
      </div>
    </div>
  );
}
