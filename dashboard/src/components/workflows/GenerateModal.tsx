import { useState, useCallback } from "react";
import { X, Wand2, Loader2, CheckCircle, AlertTriangle, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/api/client";

interface GenerateResult {
  yaml_content: string;
  name: string;
  description: string;
  steps_count: number;
  validation_errors: string[];
  input_schema: Record<string, unknown> | null;
}

interface GenerateModalProps {
  open: boolean;
  onClose: () => void;
  onSelect: (template: { name: string; content: string; step_count: number }) => void;
}

export function GenerateModal({ open, onClose, onSelect }: GenerateModalProps) {
  const [description, setDescription] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<GenerateResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refineText, setRefineText] = useState("");
  const [refining, setRefining] = useState(false);

  const handleGenerate = useCallback(async () => {
    if (!description.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);

    const res = await api.post<GenerateResult>("/generate", {
      description: description.trim(),
    });

    setLoading(false);
    if (res.error) {
      setError(res.error.message);
      return;
    }
    if (res.data) {
      setResult(res.data);
    }
  }, [description]);

  const handleRefine = useCallback(async () => {
    if (!refineText.trim() || !result) return;
    setRefining(true);
    setError(null);

    const res = await api.post<GenerateResult>("/generate", {
      description: description.trim(),
      refine_from: result.yaml_content,
      refine_instruction: refineText.trim(),
    });

    setRefining(false);
    if (res.error) {
      setError(res.error.message);
      return;
    }
    if (res.data) {
      setResult(res.data);
      setRefineText("");
    }
  }, [refineText, result, description]);

  const handleUse = useCallback(() => {
    if (!result) return;
    onSelect({
      name: result.name,
      content: result.yaml_content,
      step_count: result.steps_count,
    });
    // Reset state
    setDescription("");
    setResult(null);
    setError(null);
    setRefineText("");
  }, [result, onSelect]);

  const handleClose = useCallback(() => {
    setDescription("");
    setResult(null);
    setError(null);
    setRefineText("");
    onClose();
  }, [onClose]);

  if (!open) return null;

  const hasErrors = result && result.validation_errors.length > 0;

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-50 bg-black/40" onClick={handleClose} />

      {/* Modal */}
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div className="w-full max-w-2xl rounded-xl border border-border bg-surface shadow-xl flex flex-col max-h-[85vh]">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-border px-6 py-4">
            <div className="flex items-center gap-2.5">
              <Wand2 className="h-5 w-5 text-accent" />
              <h2 className="text-lg font-semibold text-foreground">AI Workflow Generator</h2>
            </div>
            <button
              onClick={handleClose}
              className="rounded-lg p-1.5 text-muted hover:text-foreground hover:bg-muted/10 transition-colors"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* Body */}
          <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
            {/* Description input */}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-muted">
                Describe your workflow
              </label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="e.g. Research a topic, write a blog post, and get approval before publishing"
                rows={3}
                className={cn(
                  "w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-foreground",
                  "placeholder:text-muted/50 resize-none",
                  "focus:border-accent/50 focus:outline-none focus:ring-1 focus:ring-ring/30"
                )}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault();
                    handleGenerate();
                  }
                }}
              />
            </div>

            {/* Generate button */}
            {!result && (
              <button
                onClick={handleGenerate}
                disabled={loading || !description.trim()}
                className={cn(
                  "flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-all",
                  loading || !description.trim()
                    ? "bg-muted/20 text-muted cursor-not-allowed"
                    : "bg-accent text-accent-foreground hover:bg-accent-hover shadow-sm"
                )}
              >
                {loading ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Generating...
                  </>
                ) : (
                  <>
                    <Wand2 className="h-4 w-4" />
                    Generate
                  </>
                )}
              </button>
            )}

            {/* Error */}
            {error && (
              <div className="rounded-lg border border-error/30 bg-error/5 px-4 py-3 text-sm text-error">
                {error}
              </div>
            )}

            {/* Result */}
            {result && (
              <div className="space-y-3">
                {/* Status */}
                <div className="flex items-center gap-2 text-sm">
                  {hasErrors ? (
                    <>
                      <AlertTriangle className="h-4 w-4 text-warning" />
                      <span className="text-warning">
                        Generated with {result.validation_errors.length} issue(s)
                      </span>
                    </>
                  ) : (
                    <>
                      <CheckCircle className="h-4 w-4 text-success" />
                      <span className="text-success">
                        "{result.name}" - {result.steps_count} steps
                      </span>
                    </>
                  )}
                </div>

                {/* Validation errors */}
                {hasErrors && (
                  <div className="space-y-1">
                    {result.validation_errors.map((err, i) => (
                      <div key={i} className="text-xs text-warning pl-6">
                        - {err}
                      </div>
                    ))}
                  </div>
                )}

                {/* YAML preview */}
                <div className="rounded-lg border border-border bg-black/20 overflow-hidden">
                  <div className="flex items-center justify-between border-b border-border px-3 py-1.5">
                    <span className="text-xs font-medium text-muted">Generated YAML</span>
                    <span className="text-xs text-muted/60">{result.steps_count} steps</span>
                  </div>
                  <pre className="max-h-64 overflow-auto p-3 text-xs text-foreground/80 font-mono leading-relaxed">
                    {result.yaml_content}
                  </pre>
                </div>

                {/* Refine */}
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={refineText}
                    onChange={(e) => setRefineText(e.target.value)}
                    placeholder="Refine: e.g. add a review step, use haiku for formatting..."
                    className={cn(
                      "flex-1 rounded-lg border border-border bg-surface px-3 py-2 text-sm",
                      "placeholder:text-muted/50",
                      "focus:border-accent/50 focus:outline-none focus:ring-1 focus:ring-ring/30"
                    )}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        handleRefine();
                      }
                    }}
                  />
                  <button
                    onClick={handleRefine}
                    disabled={refining || !refineText.trim()}
                    className={cn(
                      "flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm font-medium transition-colors",
                      refining || !refineText.trim()
                        ? "text-muted cursor-not-allowed"
                        : "text-muted hover:text-foreground hover:border-accent"
                    )}
                  >
                    {refining ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <RefreshCw className="h-3.5 w-3.5" />
                    )}
                    Refine
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Footer */}
          {result && (
            <div className="flex items-center justify-end gap-2 border-t border-border px-6 py-3">
              <button
                onClick={handleClose}
                className="rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-muted hover:text-foreground transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleUse}
                className={cn(
                  "flex items-center gap-1.5 rounded-lg bg-accent px-4 py-1.5 text-xs font-medium text-accent-foreground",
                  "hover:bg-accent-hover transition-all shadow-sm"
                )}
              >
                <Wand2 className="h-3.5 w-3.5" />
                Use This Workflow
              </button>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
