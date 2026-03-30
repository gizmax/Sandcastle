import { useCallback, useEffect, useState } from "react";
import { GitCompare, Loader2, X } from "lucide-react";
import { api } from "@/api/client";
import { VersionHistory } from "./VersionHistory";
import { VersionStatusBadge } from "./VersionStatusBadge";
import { VersionDiffModal } from "./VersionDiffModal";
import { cn, formatRelativeTime } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface WorkflowVersion {
  id: string;
  workflow_name: string;
  version: number;
  status: string;
  description: string;
  steps_count: number;
  checksum: string;
  created_at: string | null;
  promoted_at: string | null;
}

interface VersionListData {
  workflow_name: string;
  production_version: number | null;
  staging_version: number | null;
  latest_draft_version: number | null;
  versions: WorkflowVersion[];
}

export interface VersionHistoryModalProps {
  open: boolean;
  onClose: () => void;
  workflowName: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function VersionHistoryModal({ open, onClose, workflowName }: VersionHistoryModalProps) {
  const [data, setData] = useState<VersionListData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [diffModal, setDiffModal] = useState<{ a: number; b: number } | null>(null);

  // Fetch versions when modal opens
  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(null);
    setData(null);
    setSelectedVersion(null);

    api
      .get<VersionListData>(`/workflows/${workflowName}/versions`)
      .then((res) => {
        if (res.data) setData(res.data);
        else if (res.error) setError(res.error.message);
      })
      .finally(() => setLoading(false));
  }, [open, workflowName]);

  // Keyboard: Escape to close
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !diffModal) {
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose, diffModal]);

  const handleSelectVersion = useCallback((version: number) => {
    setSelectedVersion((prev) => (prev === version ? null : version));
  }, []);

  const handlePromote = useCallback(async (version: number) => {
    const res = await api.post(`/workflows/${workflowName}/promote`, { version });
    if (!res.error) {
      // Refetch versions
      const updated = await api.get<VersionListData>(`/workflows/${workflowName}/versions`);
      if (updated.data) setData(updated.data);
    }
  }, [workflowName]);

  if (!open) return null;

  const selected = data?.versions.find((v) => v.version === selectedVersion) ?? null;

  return (
    <>
      <div
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
        onClick={onClose}
      >
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`Version history for ${workflowName}`}
          className={cn(
            "relative mx-4 flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden",
            "rounded-xl border border-border bg-surface shadow-2xl",
          )}
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-border px-5 py-3.5">
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-foreground truncate">
                {workflowName}
              </h3>
              {data && (
                <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
                  {data.production_version !== null && (
                    <span className="flex items-center gap-1">
                      <VersionStatusBadge status="production" />
                      <span className="font-mono">v{data.production_version}</span>
                    </span>
                  )}
                  {data.staging_version !== null && (
                    <span className="flex items-center gap-1">
                      <VersionStatusBadge status="staging" />
                      <span className="font-mono">v{data.staging_version}</span>
                    </span>
                  )}
                  {data.latest_draft_version !== null && (
                    <span className="flex items-center gap-1">
                      <VersionStatusBadge status="draft" />
                      <span className="font-mono">v{data.latest_draft_version}</span>
                    </span>
                  )}
                </div>
              )}
            </div>

            <div className="flex items-center gap-2 shrink-0 ml-3">
              {data && data.versions.length >= 2 && (
                <button
                  onClick={() => {
                    const sorted = [...data.versions].sort((a, b) => b.version - a.version);
                    setDiffModal({ a: sorted[1].version, b: sorted[0].version });
                  }}
                  className={cn(
                    "flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5",
                    "text-xs font-medium text-muted",
                    "hover:text-accent hover:border-accent/40 hover:bg-accent/5 transition-colors",
                  )}
                >
                  <GitCompare className="h-3.5 w-3.5" />
                  Compare
                </button>
              )}
              <button
                onClick={onClose}
                className="rounded-md p-1.5 text-muted hover:text-foreground hover:bg-border/40 transition-colors"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>

          {/* Content */}
          {loading && (
            <div className="flex flex-1 items-center justify-center py-16">
              <Loader2 className="h-6 w-6 animate-spin text-muted" />
            </div>
          )}

          {error && (
            <div className="flex flex-1 items-center justify-center py-16">
              <p className="text-sm text-error">{error}</p>
            </div>
          )}

          {data && !loading && (
            <div className="flex-1 overflow-y-auto">
              {/* Version timeline */}
              <VersionHistory
                versions={data.versions}
                selectedVersion={selectedVersion}
                onSelect={handleSelectVersion}
                onPromote={handlePromote}
              />

              {/* Selected version details */}
              {selected && (
                <div className="border-t border-border px-5 py-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <h4 className="text-xs font-semibold text-foreground uppercase tracking-wider">
                      Version {selected.version} Details
                    </h4>
                    {data.production_version !== null &&
                      selected.version !== data.production_version && (
                        <button
                          onClick={() =>
                            setDiffModal({
                              a: data.production_version!,
                              b: selected.version,
                            })
                          }
                          className="text-xs text-accent hover:text-accent/80 transition-colors"
                        >
                          Compare with production
                        </button>
                      )}
                  </div>
                  <div className="grid grid-cols-3 gap-4 text-sm">
                    <div>
                      <span className="text-xs text-muted-foreground">Status</span>
                      <div className="mt-1">
                        <VersionStatusBadge status={selected.status} />
                      </div>
                    </div>
                    <div>
                      <span className="text-xs text-muted-foreground">Steps</span>
                      <p className="mt-1 font-medium text-foreground">{selected.steps_count}</p>
                    </div>
                    <div>
                      <span className="text-xs text-muted-foreground">Checksum</span>
                      <p className="mt-1 font-mono text-xs text-foreground truncate">
                        {selected.checksum}
                      </p>
                    </div>
                  </div>
                  {selected.description && (
                    <div>
                      <span className="text-xs text-muted-foreground">Description</span>
                      <p className="mt-1 text-sm text-foreground">{selected.description}</p>
                    </div>
                  )}
                  {selected.created_at && (
                    <div>
                      <span className="text-xs text-muted-foreground">Created</span>
                      <p className="mt-1 text-xs text-muted">
                        {formatRelativeTime(selected.created_at)}
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Diff modal layered on top */}
      {diffModal && (
        <VersionDiffModal
          open={true}
          onClose={() => setDiffModal(null)}
          workflowName={workflowName}
          versionA={diffModal.a}
          versionB={diffModal.b}
          availableVersions={data?.versions.map((v) => v.version) ?? []}
        />
      )}
    </>
  );
}
