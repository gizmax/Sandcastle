import { useMemo } from "react";
import { ArrowLeft, ArrowRight, FileText } from "lucide-react";
import { cn } from "@/lib/utils";
import type { SelectedTemplate } from "./OnboardingWizard";
import type { InputSchemaProperty } from "@/types/inputSchema";

interface StepConfigureInputsProps {
  template: SelectedTemplate | null;
  fieldValues: Record<string, string>;
  onFieldValuesChange: (values: Record<string, string>) => void;
  onNext: () => void;
  onBack: () => void;
}

/**
 * Step 3: Configure workflow inputs.
 * Renders a form based on the selected template's input schema, reusing
 * the same field rendering approach as RunWorkflowModal.
 */
export function StepConfigureInputs({
  template,
  fieldValues,
  onFieldValuesChange,
  onNext,
  onBack,
}: StepConfigureInputsProps) {
  const fields = useMemo(
    () =>
      template?.inputSchema?.properties
        ? (Object.entries(template.inputSchema.properties) as [string, InputSchemaProperty][])
        : ([] as [string, InputSchemaProperty][]),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [JSON.stringify(template?.inputSchema?.properties)]
  );

  const requiredFields = useMemo(
    () => new Set(template?.inputSchema?.required || []),
    [template?.inputSchema?.required]
  );

  const hasSchema = fields.length > 0;

  function handleChange(key: string, value: string) {
    onFieldValuesChange({ ...fieldValues, [key]: value });
  }

  const inputClass = cn(
    "h-9 w-full rounded-lg border border-border bg-background px-3 text-sm",
    "focus-visible:border-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30",
    "transition-all duration-200"
  );

  return (
    <div className="space-y-6">
      <div className="text-center">
        <h2 className="text-lg font-semibold text-foreground">Configure Inputs</h2>
        <p className="mt-1 text-sm text-muted">
          {hasSchema
            ? `Set the inputs for ${template?.description ? `"${template.description.split(".")[0]}"` : "your workflow"}.`
            : "This template has no required inputs. You can continue with defaults."}
        </p>
      </div>

      {/* Input fields */}
      {hasSchema ? (
        <div className="max-h-72 overflow-y-auto space-y-4 stagger-children">
          {fields.map(([key, prop]) => {
            const isTextarea =
              prop.type === "string" &&
              !prop.enum &&
              (/(text|description|content|prompt)/i.test(key) ||
                (prop.description != null && prop.description.length > 80));
            const isNumber = prop.type === "integer" || prop.type === "number";
            const isBool = prop.type === "boolean";
            const hasEnum = Array.isArray(prop.enum) && prop.enum.length > 0;

            return (
              <div key={key}>
                <label className="mb-1 block text-xs font-medium text-muted">
                  {key}
                  {requiredFields.has(key) && (
                    <span className="text-error ml-0.5">*</span>
                  )}
                </label>
                {prop.description && (
                  <p className="mb-1.5 text-xs text-muted-foreground">
                    {prop.description}
                  </p>
                )}

                {hasEnum ? (
                  <select
                    value={fieldValues[key] || ""}
                    onChange={(e) => handleChange(key, e.target.value)}
                    className={inputClass}
                  >
                    <option value="">Select {key}...</option>
                    {prop.enum!.map((opt) => (
                      <option key={opt} value={opt}>
                        {opt}
                      </option>
                    ))}
                  </select>
                ) : isBool ? (
                  <div className="flex items-center gap-2 py-1">
                    <div
                      role="checkbox"
                      aria-checked={fieldValues[key] === "true"}
                      tabIndex={0}
                      onClick={() =>
                        handleChange(
                          key,
                          fieldValues[key] === "true" ? "false" : "true"
                        )
                      }
                      onKeyDown={(e) => {
                        if (e.key === " " || e.key === "Enter") {
                          e.preventDefault();
                          handleChange(
                            key,
                            fieldValues[key] === "true" ? "false" : "true"
                          );
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
                          fieldValues[key] === "true"
                            ? "translate-x-5"
                            : "translate-x-0"
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
                    onChange={(e) => handleChange(key, e.target.value)}
                    placeholder={
                      prop.default != null ? String(prop.default) : key
                    }
                    rows={3}
                    className={cn(
                      "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm resize-y",
                      "focus-visible:border-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30",
                      "transition-all duration-200"
                    )}
                  />
                ) : isNumber ? (
                  <input
                    type="number"
                    value={fieldValues[key] || ""}
                    onChange={(e) => handleChange(key, e.target.value)}
                    placeholder={
                      prop.default != null ? String(prop.default) : key
                    }
                    className={inputClass}
                  />
                ) : (
                  <input
                    type="text"
                    value={fieldValues[key] || ""}
                    onChange={(e) => handleChange(key, e.target.value)}
                    placeholder={
                      prop.default != null ? String(prop.default) : key
                    }
                    className={inputClass}
                  />
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center py-8 text-center">
          <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-accent/10">
            <FileText className="h-6 w-6 text-accent" />
          </div>
          <p className="text-sm text-muted">
            No input fields required for this template.
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            You can configure inputs later when running from the dashboard.
          </p>
        </div>
      )}

      {/* Navigation */}
      <div className="flex items-center justify-between pt-2">
        <button
          onClick={onBack}
          className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-foreground transition-colors"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back
        </button>
        <button
          onClick={onNext}
          className={cn(
            "inline-flex items-center gap-2 rounded-lg bg-accent px-5 py-2 text-sm font-medium text-accent-foreground",
            "hover:bg-accent-hover transition-all duration-200 shadow-sm"
          )}
        >
          Continue
          <ArrowRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
