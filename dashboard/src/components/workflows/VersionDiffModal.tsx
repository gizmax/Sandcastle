import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { api } from "@/api/client";

interface DiffData {
  version_a: number;
  version_b: number;
  yaml_a: string;
  yaml_b: string;
  steps_added: string[];
  steps_removed: string[];
  steps_changed: string[];
}

interface VersionDiffModalProps {
  open: boolean;
  onClose: () => void;
  workflowName: string;
  versionA: number;
  versionB: number;
}

export function VersionDiffModal({ open, onClose, workflowName, versionA, versionB }: VersionDiffModalProps) {
  const [diff, setDiff] = useState<DiffData | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    api
      .get<DiffData>(`/workflows/${workflowName}/versions/diff`, {
        version_a: String(versionA),
        version_b: String(versionB),
      })
      .then((res) => {
        if (res.data) setDiff(res.data);
      })
      .finally(() => setLoading(false));
  }, [open, workflowName, versionA, versionB]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="relative mx-4 max-h-[85vh] w-full max-w-5xl overflow-hidden rounded-xl border border-border bg-surface shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <h3 className="text-sm font-semibold text-foreground">
            Diff: v{versionA} vs v{versionB}
          </h3>
          <button onClick={onClose} className="text-muted hover:text-foreground"><X className="h-4 w-4" /></button>
        </div>

        {loading && <div className="p-8 text-center text-sm text-muted">Loading diff...</div>}

        {diff && (
          <div className="overflow-auto max-h-[calc(85vh-48px)]">
            {/* Summary */}
            {(diff.steps_added.length > 0 || diff.steps_removed.length > 0 || diff.steps_changed.length > 0) && (
              <div className="flex gap-4 px-5 py-3 border-b border-border text-xs">
                {diff.steps_added.length > 0 && (
                  <span className="text-success">+{diff.steps_added.length} added: {diff.steps_added.join(", ")}</span>
                )}
                {diff.steps_removed.length > 0 && (
                  <span className="text-error">-{diff.steps_removed.length} removed: {diff.steps_removed.join(", ")}</span>
                )}
                {diff.steps_changed.length > 0 && (
                  <span className="text-warning">~{diff.steps_changed.length} changed: {diff.steps_changed.join(", ")}</span>
                )}
              </div>
            )}

            {/* Side-by-side YAML */}
            <div className="grid grid-cols-2 divide-x divide-border">
              <div className="p-4">
                <div className="text-xs font-medium text-muted-foreground mb-2">v{versionA}</div>
                <pre className="text-xs font-mono text-foreground whitespace-pre-wrap">{diff.yaml_a}</pre>
              </div>
              <div className="p-4">
                <div className="text-xs font-medium text-muted-foreground mb-2">v{versionB}</div>
                <pre className="text-xs font-mono text-foreground whitespace-pre-wrap">{diff.yaml_b}</pre>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
