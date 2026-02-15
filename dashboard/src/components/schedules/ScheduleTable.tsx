import { cn } from "@/lib/utils";
import { formatRelativeTime } from "@/lib/utils";

interface ScheduleItem {
  id: string;
  workflow_name: string;
  cron_expression: string;
  enabled: boolean;
  last_run_id: string | null;
  created_at: string | null;
}

interface ScheduleTableProps {
  schedules: ScheduleItem[];
  onToggle: (id: string, enabled: boolean) => void;
  onDelete: (id: string) => void;
  onEdit: (schedule: ScheduleItem) => void;
}

export function ScheduleTable({ schedules, onToggle, onDelete, onEdit }: ScheduleTableProps) {
  return (
    <div className="rounded-xl border border-border bg-surface shadow-sm overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-background/50">
              <th className="px-5 py-3 text-left font-medium text-muted">Workflow</th>
              <th className="px-5 py-3 text-left font-medium text-muted">Schedule</th>
              <th className="px-5 py-3 text-left font-medium text-muted">Created</th>
              <th className="px-5 py-3 text-center font-medium text-muted">Enabled</th>
              <th className="px-5 py-3 text-right font-medium text-muted">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {schedules.map((schedule) => (
              <tr key={schedule.id}>
                <td className="px-5 py-3 font-medium text-foreground">
                  {schedule.workflow_name}
                </td>
                <td className="px-5 py-3 font-mono text-xs text-muted">
                  {schedule.cron_expression}
                </td>
                <td className="px-5 py-3 text-muted">
                  {schedule.created_at ? formatRelativeTime(schedule.created_at) : "-"}
                </td>
                <td className="px-5 py-3 text-center">
                  <button
                    onClick={() => onToggle(schedule.id, !schedule.enabled)}
                    className={cn(
                      "relative inline-flex h-5 w-9 items-center rounded-full transition-colors duration-200",
                      schedule.enabled ? "bg-success" : "bg-border"
                    )}
                  >
                    <span
                      className={cn(
                        "inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform duration-200",
                        schedule.enabled ? "translate-x-[18px]" : "translate-x-[3px]"
                      )}
                    />
                  </button>
                </td>
                <td className="px-5 py-3 text-right">
                  <div className="flex items-center justify-end gap-3">
                    <button
                      onClick={() => onEdit(schedule)}
                      className="text-xs text-accent/70 hover:text-accent transition-colors"
                    >
                      Edit
                    </button>
                    <button
                      onClick={() => onDelete(schedule.id)}
                      className="text-xs text-error/70 hover:text-error transition-colors"
                    >
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
