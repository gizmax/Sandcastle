import { useState } from "react";
import { X, Play, FileText, FormInput, Braces } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { JsonEditor } from "@/components/shared/JsonEditor";

interface InputSchemaProperty {
  type: string;
  description?: string;
  default?: unknown;
  enum?: string[];
}

interface InputSchema {
  properties: Record<string, InputSchemaProperty>;
  required?: string[];
}

interface RunWorkflowModalProps {
  open: boolean;
  workflowName: string;
  inputSchema?: InputSchema;
  onClose: () => void;
  onRun: (input: Record<string, unknown>, callbackUrl?: string) => void;
}

type InputMode = "form" | "text" | "json";

const MODE_OPTIONS: { value: InputMode; label: string; icon: typeof FileText }[] = [
  { value: "form", label: "Form", icon: FormInput },
  { value: "text", label: "Text", icon: FileText },
  { value: "json", label: "JSON", icon: Braces },
];

export function RunWorkflowModal({ open, workflowName, inputSchema, onClose, onRun }: RunWorkflowModalProps) {
  const fields = inputSchema?.properties ? Object.entries(inputSchema.properties) : [];
  const hasSchema = fields.length > 0;
  const requiredFields = new Set(inputSchema?.required || []);

  const [mode, setMode] = useState<InputMode>(hasSchema ? "form" : "text");
  const [fieldValues, setFieldValues] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    for (const [key, prop] of fields) {
      if (prop.default != null) {
        if (typeof prop.default === "boolean") {
          init[key] = prop.default ? "true" : "false";
        } else {
          init[key] = String(prop.default);
        }
      } else if (prop.type === "boolean") {
        init[key] = "false";
      } else {
        init[key] = "";
      }
    }
    return init;
  });
  const [inputJson, setInputJson] = useState("{}");
  const [inputText, setInputText] = useState("");
  const [callbackUrl, setCallbackUrl] = useState("");

  if (!open) return null;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    let parsed: Record<string, unknown> = {};
    if (mode === "form" && hasSchema) {
      // Validate required fields
      const missing = (inputSchema?.required || []).filter(
        (key) => !fieldValues[key] || fieldValues[key].trim() === ""
      );
      if (missing.length > 0) {
        toast.error(`Missing required fields: ${missing.join(", ")}`);
        return;
      }
      for (const [key, val] of Object.entries(fieldValues)) {
        if (val !== undefined && val !== null) parsed[key] = val;
      }
    } else if (mode === "text") {
      parsed = { text: inputText };
    } else {
      try {
        parsed = JSON.parse(inputJson);
      } catch {
        // default empty
      }
    }
    onRun(parsed, callbackUrl || undefined);
  }

  // Filter mode options - show Form only if schema exists
  const availableModes = hasSchema ? MODE_OPTIONS : MODE_OPTIONS.filter((m) => m.value !== "form");

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

          {/* Mode selector */}
          <div className="mb-4 flex gap-1 rounded-lg border border-border bg-background p-1">
            {availableModes.map((opt) => {
              const Icon = opt.icon;
              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setMode(opt.value)}
                  className={cn(
                    "flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-all",
                    mode === opt.value
                      ? "bg-accent text-accent-foreground shadow-sm"
                      : "text-muted hover:text-foreground"
                  )}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {opt.label}
                </button>
              );
            })}
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Form mode */}
            {mode === "form" && hasSchema && (
              fields.map(([key, prop]) => {
                const isTextarea =
                  prop.type === "string" &&
                  !prop.enum &&
                  (/(text|description|content|prompt)/i.test(key) ||
                    (prop.description && prop.description.length > 80));
                const isNumber = prop.type === "integer" || prop.type === "number";
                const isBool = prop.type === "boolean";
                const hasEnum = Array.isArray(prop.enum) && prop.enum.length > 0;

                const inputClass = cn(
                  "h-9 w-full rounded-lg border border-border bg-background px-3 text-sm",
                  "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30"
                );

                return (
                  <div key={key}>
                    <label className="mb-1 block text-xs font-medium text-muted">
                      {key}
                      {requiredFields.has(key) && <span className="text-error ml-0.5">*</span>}
                    </label>
                    {prop.description && (
                      <p className="mb-1.5 text-xs text-muted-foreground">{prop.description}</p>
                    )}

                    {hasEnum ? (
                      <select
                        value={fieldValues[key] || ""}
                        onChange={(e) => setFieldValues((prev) => ({ ...prev, [key]: e.target.value }))}
                        className={inputClass}
                      >
                        <option value="">Select {key}...</option>
                        {prop.enum!.map((opt) => (
                          <option key={opt} value={opt}>{opt}</option>
                        ))}
                      </select>
                    ) : isBool ? (
                      <div className="flex items-center gap-2 py-1">
                        <div
                          role="checkbox"
                          aria-checked={fieldValues[key] === "true"}
                          tabIndex={0}
                          onClick={() =>
                            setFieldValues((prev) => ({
                              ...prev,
                              [key]: prev[key] === "true" ? "false" : "true",
                            }))
                          }
                          onKeyDown={(e) => {
                            if (e.key === " " || e.key === "Enter") {
                              e.preventDefault();
                              setFieldValues((prev) => ({
                                ...prev,
                                [key]: prev[key] === "true" ? "false" : "true",
                              }));
                            }
                          }}
                          className={cn(
                            "relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors",
                            fieldValues[key] === "true" ? "bg-accent" : "bg-border"
                          )}
                        >
                          <span
                            className={cn(
                              "pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow-sm transition-transform",
                              fieldValues[key] === "true" ? "translate-x-5" : "translate-x-0"
                            )}
                          />
                        </div>
                        <span className="text-sm text-foreground">
                          {fieldValues[key] === "true" ? "Yes" : "No"}
                        </span>
                      </div>
                    ) : isTextarea ? (
                      <textarea
                        value={fieldValues[key] || ""}
                        onChange={(e) => setFieldValues((prev) => ({ ...prev, [key]: e.target.value }))}
                        placeholder={prop.default != null ? String(prop.default) : key}
                        required={requiredFields.has(key)}
                        rows={3}
                        className={cn(
                          "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm resize-y",
                          "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30"
                        )}
                      />
                    ) : isNumber ? (
                      <input
                        type="number"
                        value={fieldValues[key] || ""}
                        onChange={(e) => setFieldValues((prev) => ({ ...prev, [key]: e.target.value }))}
                        placeholder={prop.default != null ? String(prop.default) : key}
                        required={requiredFields.has(key)}
                        className={inputClass}
                      />
                    ) : (
                      <input
                        type="text"
                        value={fieldValues[key] || ""}
                        onChange={(e) => setFieldValues((prev) => ({ ...prev, [key]: e.target.value }))}
                        placeholder={prop.default != null ? String(prop.default) : key}
                        required={requiredFields.has(key)}
                        className={inputClass}
                      />
                    )}
                  </div>
                );
              })
            )}

            {/* Text mode */}
            {mode === "text" && (
              <div>
                <label className="mb-1 block text-xs font-medium text-muted">
                  Your input
                </label>
                <p className="mb-1.5 text-xs text-muted-foreground">
                  Write anything - accessible in workflow as <code className="rounded bg-background px-1 font-mono text-accent">{"{input.text}"}</code>
                </p>
                <textarea
                  value={inputText}
                  onChange={(e) => setInputText(e.target.value)}
                  placeholder="Describe what you need..."
                  rows={5}
                  className={cn(
                    "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm resize-y",
                    "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30",
                    "placeholder:text-muted-foreground/50"
                  )}
                />
              </div>
            )}

            {/* JSON mode */}
            {mode === "json" && (
              <div>
                <label className="mb-1 block text-xs font-medium text-muted">Input Data (JSON)</label>
                <JsonEditor value={inputJson} onChange={setInputJson} rows={6} />
              </div>
            )}

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
