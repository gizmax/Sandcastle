import { useState } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { JsonEditor } from "@/components/shared/JsonEditor";

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
  const [workflowName, setWorkflowName] = useState("");
  const [cronExpression, setCronExpression] = useState("");
  const [inputJson, setInputJson] = useState("{}");

  if (!open) return null;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    let parsedInput = {};
    try {
      parsedInput = JSON.parse(inputJson);
    } catch {
      // default to empty
    }
    onSubmit({
      workflow_name: workflowName,
      cron_expression: cronExpression,
      input_data: parsedInput,
    });
    setWorkflowName("");
    setCronExpression("");
    setInputJson("{}");
  }

  return (
    <>
      <div className="fixed inset-0 z-50 bg-black/40" onClick={onClose} />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div className="w-full max-w-md rounded-xl border border-border bg-surface p-6 shadow-xl">
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
            <div>
              <label className="mb-1 block text-xs font-medium text-muted">Workflow Name</label>
              <input
                type="text"
                value={workflowName}
                onChange={(e) => setWorkflowName(e.target.value)}
                required
                className={cn(
                  "h-9 w-full rounded-lg border border-border bg-background px-3 text-sm",
                  "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30"
                )}
              />
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-muted">Cron Expression</label>
              <input
                type="text"
                value={cronExpression}
                onChange={(e) => setCronExpression(e.target.value)}
                placeholder="0 */6 * * *"
                required
                className={cn(
                  "h-9 w-full rounded-lg border border-border bg-background px-3 font-mono text-sm",
                  "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30"
                )}
              />
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-muted">Input Data (JSON)</label>
              <JsonEditor value={inputJson} onChange={setInputJson} rows={4} />
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
                  "rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground",
                  "hover:bg-accent-hover transition-all duration-200",
                  "shadow-sm hover:shadow-md"
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
