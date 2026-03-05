import { useEffect } from "react";
import { Loader2, Play, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { FilePathInput } from "@/components/shared/FilePathInput";
import type { InputSchema } from "@/types/inputSchema";

interface RunModalTemplate {
  name: string;
  input_schema?: InputSchema | null;
}

interface RunModalProps {
  template: RunModalTemplate;
  open: boolean;
  onClose: () => void;
  onSubmit: () => void;
  running: boolean;
  fieldValues: Record<string, string>;
  onFieldChange: (key: string, value: string) => void;
  runInput: string;
  onRunInputChange: (value: string) => void;
}

export function RunModal({
  template,
  open,
  onClose,
  onSubmit,
  running,
  fieldValues,
  onFieldChange,
  runInput,
  onRunInputChange,
}: RunModalProps) {
  // Close on Escape key
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      <div className="fixed inset-0 z-[60] bg-black/40" onClick={onClose} />
      <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
        <div role="dialog" aria-modal="true" aria-label={`Run ${template.name.replace(/_/g, " ")}`} className="w-full max-w-md rounded-xl border border-border bg-surface p-6 shadow-xl">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-foreground">
              Run {template.name.replace(/_/g, " ")}
            </h2>
            <button
              onClick={onClose}
              className="rounded-lg p-1 text-muted hover:text-foreground"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
          <div className="space-y-4">
            {template.input_schema?.properties ? (
              Object.entries(template.input_schema.properties).map(([key, prop]) => (
                <div key={key}>
                  <label className="mb-1 block text-xs font-medium text-muted">
                    {key}
                    {template.input_schema?.required?.includes(key) && (
                      <span className="text-error ml-0.5">*</span>
                    )}
                  </label>
                  {prop.description && (
                    <p className="mb-1.5 text-xs text-muted-foreground">{prop.description}</p>
                  )}
                  {prop.format === "file-path" ? (
                    <FilePathInput
                      value={fieldValues[key] || ""}
                      onChange={(v) => onFieldChange(key, v)}
                      accept={prop.type === "image" ? "image/*" : undefined}
                    />
                  ) : (
                    <input
                      type="text"
                      value={fieldValues[key] || ""}
                      onChange={(e) => onFieldChange(key, e.target.value)}
                      placeholder={prop.default != null ? String(prop.default) : key}
                      required={template.input_schema?.required?.includes(key)}
                      className={cn(
                        "h-9 w-full rounded-lg border border-border bg-background px-3 text-sm",
                        "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30"
                      )}
                    />
                  )}
                </div>
              ))
            ) : (
              <div>
                <label className="mb-1 block text-xs font-medium text-muted">
                  Input Data (JSON)
                </label>
                <textarea
                  value={runInput}
                  onChange={(e) => onRunInputChange(e.target.value)}
                  rows={6}
                  className={cn(
                    "w-full rounded-lg border border-border bg-background px-3 py-2 font-mono text-sm",
                    "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30"
                  )}
                />
              </div>
            )}
            <div className="flex justify-end gap-2">
              <button
                onClick={onClose}
                className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-muted hover:text-foreground transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={onSubmit}
                disabled={running}
                className={cn(
                  "flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground",
                  "hover:bg-accent-hover transition-all duration-200 shadow-sm",
                  running && "opacity-50 cursor-not-allowed"
                )}
              >
                {running && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                <Play className="h-3.5 w-3.5" />
                Run
              </button>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
