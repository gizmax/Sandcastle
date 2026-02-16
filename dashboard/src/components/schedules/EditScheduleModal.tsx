import { useState } from "react";
import { X } from "lucide-react";
import { CronBuilder } from "@/components/schedules/CronBuilder";
import { cn } from "@/lib/utils";

interface ScheduleItem {
  id: string;
  workflow_name: string;
  cron_expression: string;
  enabled: boolean;
}

interface EditScheduleModalProps {
  open: boolean;
  schedule: ScheduleItem;
  onClose: () => void;
  onSubmit: (id: string, data: { cron_expression: string; enabled: boolean }) => void;
}

export function EditScheduleModal({ open, schedule, onClose, onSubmit }: EditScheduleModalProps) {
  const [cronExpression, setCronExpression] = useState(schedule.cron_expression);
  const [enabled, setEnabled] = useState(schedule.enabled);

  if (!open) return null;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    onSubmit(schedule.id, { cron_expression: cronExpression, enabled });
  }

  return (
    <>
      <div className="fixed inset-0 z-50 bg-black/40" onClick={onClose} />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div className="w-full max-w-md rounded-xl border border-border bg-surface p-6 shadow-xl max-h-[85vh] overflow-y-auto">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-foreground">Edit Schedule</h2>
            <button
              onClick={onClose}
              className="rounded-lg p-1 text-muted hover:text-foreground"
            >
              <X className="h-5 w-5" />
            </button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="mb-1 block text-xs font-medium text-muted">Workflow</label>
              <p className="text-sm text-foreground">{schedule.workflow_name}</p>
            </div>

            <CronBuilder value={cronExpression} onChange={setCronExpression} />

            <div className="flex items-center justify-between">
              <label className="text-xs font-medium text-muted">Enabled</label>
              <button
                type="button"
                onClick={() => setEnabled(!enabled)}
                className={cn(
                  "relative inline-flex h-5 w-9 items-center rounded-full transition-colors duration-200",
                  enabled ? "bg-success" : "bg-border"
                )}
              >
                <span
                  className={cn(
                    "inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform duration-200",
                    enabled ? "translate-x-[18px]" : "translate-x-[3px]"
                  )}
                />
              </button>
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
                Save Changes
              </button>
            </div>
          </form>
        </div>
      </div>
    </>
  );
}
