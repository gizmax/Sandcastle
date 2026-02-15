import { useNavigate } from "react-router-dom";
import { RotateCcw, CheckCircle } from "lucide-react";
import { formatRelativeTime, cn } from "@/lib/utils";

export interface DLQItem {
  id: string;
  run_id: string;
  step_id: string;
  error: string | null;
  attempts: number;
  created_at: string | null;
  resolved_at: string | null;
  resolved_by: string | null;
}

interface DeadLetterTableProps {
  items: DLQItem[];
  onRetry: (id: string) => void;
  onResolve: (id: string) => void;
}

export function DeadLetterTable({ items, onRetry, onResolve }: DeadLetterTableProps) {
  const navigate = useNavigate();

  return (
    <div className="rounded-xl border border-border bg-surface shadow-sm overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-background/50">
              <th className="px-5 py-3 text-left font-medium text-muted">Step</th>
              <th className="px-5 py-3 text-left font-medium text-muted">Error</th>
              <th className="px-5 py-3 text-left font-medium text-muted">Attempts</th>
              <th className="px-5 py-3 text-left font-medium text-muted">Created</th>
              <th className="px-5 py-3 text-right font-medium text-muted">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {items.map((item) => (
              <tr key={item.id}>
                <td className="px-5 py-3">
                  <button
                    onClick={() => navigate(`/runs/${item.run_id}`)}
                    className="font-medium text-accent hover:text-accent-hover transition-colors"
                  >
                    {item.step_id}
                  </button>
                </td>
                <td className="max-w-xs px-5 py-3">
                  <p className="truncate font-mono text-xs text-error">
                    {item.error || "-"}
                  </p>
                </td>
                <td className="px-5 py-3 text-muted">{item.attempts}</td>
                <td className="px-5 py-3 text-muted">
                  {item.created_at ? formatRelativeTime(item.created_at) : "-"}
                </td>
                <td className="px-5 py-3 text-right">
                  <div className="flex items-center justify-end gap-2">
                    <button
                      onClick={() => onRetry(item.id)}
                      className={cn(
                        "flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium",
                        "text-running hover:bg-running/10 transition-colors"
                      )}
                    >
                      <RotateCcw className="h-3 w-3" />
                      Retry
                    </button>
                    <button
                      onClick={() => onResolve(item.id)}
                      className={cn(
                        "flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium",
                        "text-success hover:bg-success/10 transition-colors"
                      )}
                    >
                      <CheckCircle className="h-3 w-3" />
                      Resolve
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
