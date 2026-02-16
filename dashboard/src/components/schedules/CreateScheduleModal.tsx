import { useEffect, useState } from "react";
import { X, Loader2 } from "lucide-react";
import { api } from "@/api/client";
import { CronBuilder } from "@/components/schedules/CronBuilder";
import { JsonEditor } from "@/components/shared/JsonEditor";
import { cn } from "@/lib/utils";

interface WorkflowOption {
  name: string;
  description: string;
  file_name: string;
  input_schema?: {
    required?: string[];
    properties?: Record<string, { type: string; description?: string; default?: unknown }>;
  } | null;
}

interface CreateScheduleModalProps {
  open: boolean;
  onClose: () => void;
  onSubmit: (data: {
    workflow_name: string;
    cron_expression: string;
    input_data: Record<string, unknown>;
  }) => void;
}

export function CreateScheduleModal({ open, onClose, onSubmit }: CreateScheduleModalProps) {
  const [workflows, setWorkflows] = useState<WorkflowOption[]>([]);
  const [loadingWf, setLoadingWf] = useState(false);
  const [selectedWorkflow, setSelectedWorkflow] = useState<string>("");
  const [cronExpression, setCronExpression] = useState("0 9 * * *");
  const [inputJson, setInputJson] = useState("{}");
  const [schemaInputs, setSchemaInputs] = useState<Record<string, string>>({});

  // Load workflows when modal opens
  useEffect(() => {
    if (!open) return;
    setLoadingWf(true);
    api
      .get<WorkflowOption[]>("/workflows")
      .then((res) => {
        if (res.data) setWorkflows(res.data);
      })
      .finally(() => setLoadingWf(false));
  }, [open]);

  if (!open) return null;

  const selected = workflows.find(
    (w) => w.file_name.replace(".yaml", "") === selectedWorkflow
  );
  const schema = selected?.input_schema;
  const hasSchema = schema && schema.properties && Object.keys(schema.properties).length > 0;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();

    let inputData: Record<string, unknown> = {};
    if (hasSchema) {
      // Build from schema form
      for (const [key, val] of Object.entries(schemaInputs)) {
        if (val !== "") inputData[key] = val;
      }
      // Merge defaults for missing fields
      if (schema?.properties) {
        for (const [key, prop] of Object.entries(schema.properties)) {
          if (!(key in inputData) && prop.default !== undefined) {
            inputData[key] = prop.default;
          }
        }
      }
    } else {
      try {
        inputData = JSON.parse(inputJson);
      } catch {
        // default to empty
      }
    }

    onSubmit({
      workflow_name: selectedWorkflow,
      cron_expression: cronExpression,
      input_data: inputData,
    });
    setSelectedWorkflow("");
    setCronExpression("0 9 * * *");
    setInputJson("{}");
    setSchemaInputs({});
  }

  const inputClass = cn(
    "h-9 w-full rounded-lg border border-border bg-background px-3 text-sm",
    "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30"
  );

  return (
    <>
      <div className="fixed inset-0 z-50 bg-black/40" onClick={onClose} />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div className="w-full max-w-md rounded-xl border border-border bg-surface p-6 shadow-xl max-h-[85vh] overflow-y-auto">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-foreground">New Schedule</h2>
            <button
              onClick={onClose}
              className="rounded-lg p-1 text-muted hover:text-foreground"
            >
              <X className="h-5 w-5" />
            </button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Workflow picker */}
            <div>
              <label className="mb-1 block text-xs font-medium text-muted">Workflow</label>
              {loadingWf ? (
                <div className="flex items-center gap-2 h-9 px-3 text-sm text-muted">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  Loading workflows...
                </div>
              ) : workflows.length === 0 ? (
                <p className="text-sm text-muted-foreground">No workflows found. Create one first.</p>
              ) : (
                <select
                  value={selectedWorkflow}
                  onChange={(e) => {
                    setSelectedWorkflow(e.target.value);
                    setSchemaInputs({});
                  }}
                  required
                  className={inputClass}
                >
                  <option value="">Select a workflow...</option>
                  {workflows.map((w) => (
                    <option key={w.file_name} value={w.file_name.replace(".yaml", "")}>
                      {w.name || w.file_name.replace(".yaml", "")}
                    </option>
                  ))}
                </select>
              )}
              {selected?.description && (
                <p className="text-[11px] text-muted-foreground mt-0.5">{selected.description}</p>
              )}
            </div>

            {/* Schedule (CronBuilder) */}
            <CronBuilder value={cronExpression} onChange={setCronExpression} />

            {/* Input data - schema form or JSON editor */}
            {selectedWorkflow && (
              <div>
                <label className="mb-1 block text-xs font-medium text-muted">Input Data</label>
                {hasSchema ? (
                  <div className="space-y-2.5">
                    {Object.entries(schema!.properties!).map(([key, prop]) => (
                      <div key={key}>
                        <div className="flex items-center gap-1 mb-0.5">
                          <span className="text-xs text-foreground">{key}</span>
                          {schema!.required?.includes(key) && (
                            <span className="text-[10px] text-error">*</span>
                          )}
                        </div>
                        <input
                          type="text"
                          value={schemaInputs[key] ?? (prop.default !== undefined ? String(prop.default) : "")}
                          onChange={(e) =>
                            setSchemaInputs((prev) => ({ ...prev, [key]: e.target.value }))
                          }
                          placeholder={prop.description || key}
                          required={schema!.required?.includes(key)}
                          className={cn(inputClass, "text-xs")}
                        />
                        {prop.description && (
                          <p className="text-[11px] text-muted-foreground mt-0.5">{prop.description}</p>
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <>
                    <JsonEditor value={inputJson} onChange={setInputJson} rows={3} />
                    <p className="text-[11px] text-muted-foreground mt-0.5">
                      {"JSON passed to the workflow as {input.*} variables."}
                    </p>
                  </>
                )}
              </div>
            )}

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
                disabled={!selectedWorkflow}
                className={cn(
                  "rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground",
                  "hover:bg-accent-hover transition-all duration-200",
                  "shadow-sm hover:shadow-md",
                  "disabled:opacity-50 disabled:cursor-not-allowed"
                )}
              >
                Create Schedule
              </button>
            </div>
          </form>
        </div>
      </div>
    </>
  );
}
