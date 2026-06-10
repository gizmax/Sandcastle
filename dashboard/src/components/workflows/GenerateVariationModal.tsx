import { useEffect, useState } from "react";
import { X, Wand2, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";

interface GenerateVariationModalProps {
  open: boolean;
  onClose: () => void;
  workflowName: string;
  /** Called with the generated YAML so the caller can open it in the builder. */
  onGenerated: (yaml: string) => void;
}

const SUGGESTIONS = [
  "Make it faster and cheaper",
  "Add an error-handling / retry step",
  "Improve the output quality",
  "Add a validation gate before the final step",
];

/**
 * Generate a VARIATION of an existing workflow.
 *
 * Surfaces the /generate endpoint's refine capability as a contextual action on
 * the workflow object: it exports the current production YAML, then asks the
 * generator to refine it per the user's instruction (refine_from +
 * refine_instruction). The proposed variant opens in the builder for review —
 * we never overwrite the original.
 */
export function GenerateVariationModal({
  open,
  onClose,
  workflowName,
  onGenerated,
}: GenerateVariationModalProps) {
  const [instruction, setInstruction] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open) setInstruction("");
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  async function handleSubmit() {
    if (!instruction.trim()) return;
    setSubmitting(true);
    try {
      // 1. Export the current production YAML to refine from.
      const exportRes = await api.get<{ yaml_content: string }>(
        `/workflows/${workflowName}/export`
      );
      const yaml = exportRes.data?.yaml_content;
      if (!yaml) {
        toast.error("Could not load workflow YAML to refine. Promote a version first.");
        setSubmitting(false);
        return;
      }

      // 2. Ask the generator to produce a refined variant.
      const res = await api.post<{ yaml_content: string }>(
        "/generate",
        {
          description: `Refined variation of workflow "${workflowName}"`,
          refine_from: yaml,
          refine_instruction: instruction.trim(),
        },
        120000
      );

      if (res.error) {
        toast.error(`Variation failed: ${res.error.message}`);
      } else if (res.data?.yaml_content) {
        toast.success("Variation generated — review it in the builder");
        onGenerated(res.data.yaml_content);
      } else {
        toast.error("Variation failed: no workflow returned");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Generate a variation"
        className="w-full max-w-lg rounded-xl border border-border bg-surface p-6 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-5 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Wand2 className="h-5 w-5 text-accent" />
            <h2 className="text-lg font-semibold text-foreground">Generate a variation</h2>
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

        <div className="mb-4 rounded-lg bg-background px-3 py-2 text-sm text-muted">
          Describe what to change. We'll propose an improved version of{" "}
          <span className="font-medium text-foreground">{workflowName}</span> and open it in the
          builder for review — your original stays untouched.
        </div>

        <label htmlFor="variation-instruction" className="mb-1 block text-xs font-medium text-muted">
          What should change?
        </label>
        <textarea
          id="variation-instruction"
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          placeholder="e.g. Add a fact-checking step and switch the summary to a cheaper model"
          rows={4}
          autoFocus
          className={cn(
            "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground",
            "placeholder:text-muted/50 resize-y",
            "focus-visible:border-accent focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
          )}
        />

        <div className="mt-3 flex flex-wrap gap-2">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setInstruction(s)}
              className={cn(
                "rounded-full border border-border bg-background px-2.5 py-1 text-xs text-muted",
                "hover:border-accent/40 hover:text-accent transition-colors"
              )}
            >
              {s}
            </button>
          ))}
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
            onClick={handleSubmit}
            disabled={!instruction.trim() || submitting}
            className={cn(
              "flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground",
              "hover:bg-accent-hover transition-all duration-200 shadow-sm hover:shadow-md",
              "disabled:opacity-50 disabled:cursor-not-allowed"
            )}
          >
            {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
            {submitting ? "Generating..." : "Generate variation"}
          </button>
        </div>
      </div>
    </div>
  );
}
