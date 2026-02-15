import { cn } from "@/lib/utils";
import { formatRelativeTime } from "@/lib/utils";

export interface ApiKeyItem {
  id: string;
  key_prefix: string;
  tenant_id: string;
  name: string;
  created_at: string;
  last_used_at: string | null;
}

interface ApiKeyTableProps {
  keys: ApiKeyItem[];
  onDeactivate: (id: string) => void;
}

export function ApiKeyTable({ keys, onDeactivate }: ApiKeyTableProps) {
  return (
    <div className="rounded-xl border border-border bg-surface shadow-sm overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-background/50">
              <th className="px-5 py-3 text-left font-medium text-muted">Key</th>
              <th className="px-5 py-3 text-left font-medium text-muted">Name</th>
              <th className="px-5 py-3 text-left font-medium text-muted">Tenant</th>
              <th className="px-5 py-3 text-left font-medium text-muted">Created</th>
              <th className="px-5 py-3 text-left font-medium text-muted">Last Used</th>
              <th className="px-5 py-3 text-right font-medium text-muted">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {keys.map((key) => (
              <tr key={key.id}>
                <td className="px-5 py-3">
                  <code className="rounded-md bg-background px-2 py-0.5 font-mono text-xs text-foreground">
                    {key.key_prefix}...
                  </code>
                </td>
                <td className="px-5 py-3 font-medium text-foreground">
                  {key.name}
                </td>
                <td className="px-5 py-3 text-muted">
                  {key.tenant_id}
                </td>
                <td className="px-5 py-3 text-muted">
                  {formatRelativeTime(key.created_at)}
                </td>
                <td className="px-5 py-3 text-muted">
                  {key.last_used_at ? formatRelativeTime(key.last_used_at) : "Never"}
                </td>
                <td className="px-5 py-3 text-right">
                  <button
                    onClick={() => onDeactivate(key.id)}
                    className={cn(
                      "rounded-lg border border-error/30 px-3 py-1 text-xs font-medium text-error/70",
                      "hover:bg-error/10 hover:text-error transition-colors"
                    )}
                  >
                    Deactivate
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
