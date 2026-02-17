import { VersionStatusBadge } from "./VersionStatusBadge";
import { formatRelativeTime } from "@/lib/utils";

interface WorkflowVersion {
  id: string;
  version: number;
  status: string;
  description: string;
  steps_count: number;
  checksum: string;
  created_at: string | null;
  promoted_at: string | null;
}

interface VersionHistoryProps {
  versions: WorkflowVersion[];
  selectedVersion: number | null;
  onSelect: (version: number) => void;
  onPromote: (version: number) => void;
}

export function VersionHistory({ versions, selectedVersion, onSelect, onPromote }: VersionHistoryProps) {
  return (
    <div className="divide-y divide-border">
      {versions.map((v) => (
        <div
          key={v.id}
          onClick={() => onSelect(v.version)}
          className={`flex items-center justify-between px-4 py-3 cursor-pointer transition-colors ${
            selectedVersion === v.version ? "bg-accent/10" : "hover:bg-border/20"
          }`}
        >
          <div className="flex items-center gap-3 min-w-0">
            <span className="font-mono text-sm font-semibold text-foreground shrink-0">
              v{v.version}
            </span>
            <VersionStatusBadge status={v.status} />
            <span className="text-xs text-muted truncate">
              {v.description || `${v.steps_count} steps`}
            </span>
          </div>
          <div className="flex items-center gap-3 shrink-0">
            {v.created_at && (
              <span className="text-xs text-muted">{formatRelativeTime(v.created_at)}</span>
            )}
            {(v.status === "draft" || v.status === "staging") && (
              <button
                onClick={(e) => { e.stopPropagation(); onPromote(v.version); }}
                className="rounded-md bg-accent/10 px-2 py-1 text-xs font-medium text-accent hover:bg-accent/20 transition-colors"
              >
                {v.status === "draft" ? "To Staging" : "To Production"}
              </button>
            )}
          </div>
        </div>
      ))}
      {versions.length === 0 && (
        <div className="px-4 py-8 text-center text-sm text-muted">No versions found</div>
      )}
    </div>
  );
}
