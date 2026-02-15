import { useState } from "react";
import { X, Play } from "lucide-react";
import { cn } from "@/lib/utils";
import { JsonEditor } from "@/components/shared/JsonEditor";

interface RunWorkflowModalProps {
  open: boolean;
  workflowName: string;
  onClose: () => void;
  onRun: (input: Record<string, unknown>, callbackUrl?: string) => void;
}

export function RunWorkflowModal({ open, workflowName, onClose, onRun }: RunWorkflowModalProps) {
  const [inputJson, setInputJson] = useState("{}");
  const [callbackUrl, setCallbackUrl] = useState("");

  if (!open) return null;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    let parsed = {};
    try {
      parsed = JSON.parse(inputJson);
    } catch {
      // default empty
    }
    onRun(parsed, callbackUrl || undefined);
  }

  return (
    <>
      <div className="fixed inset-0 z-50 bg-black/40" onClick={onClose} />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div className="w-full max-w-md rounded-xl border border-border bg-surface p-6 shadow-xl">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-foreground">Run {workflowName}</h2>
            <button onClick={onClose} className="rounded-lg p-1 text-muted hover:text-foreground">
              <X className="h-5 w-5" />
            </button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="mb-1 block text-xs font-medium text-muted">Input Data (JSON)</label>
              <JsonEditor value={inputJson} onChange={setInputJson} rows={6} />
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-muted">
                Callback URL (optional)
              </label>
              <input
                type="url"
                value={callbackUrl}
                onChange={(e) => setCallbackUrl(e.target.value)}
                placeholder="https://..."
                className={cn(
                  "h-9 w-full rounded-lg border border-border bg-background px-3 text-sm",
                  "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30"
                )}
              />
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={onClose}
                className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-muted hover:text-foreground transition-colors"
              >
                Cancel
              </button>
              <button
                type="submit"
                className={cn(
                  "flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground",
                  "hover:bg-accent-hover transition-all duration-200 shadow-sm hover:shadow-md"
                )}
              >
                <Play className="h-3.5 w-3.5" />
                Run Workflow
              </button>
            </div>
          </form>
        </div>
      </div>
    </>
  );
}
