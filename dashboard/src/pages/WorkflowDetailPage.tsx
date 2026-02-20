import { useCallback, useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { VersionHistory } from "@/components/workflows/VersionHistory";
import { VersionStatusBadge } from "@/components/workflows/VersionStatusBadge";
import { VersionDiffModal } from "@/components/workflows/VersionDiffModal";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";

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

export default function WorkflowDetailPage() {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();
  const [data, setData] = useState<VersionListData | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [, setSelectedYaml] = useState<string | null>(null);
  const [diffModal, setDiffModal] = useState<{ a: number; b: number } | null>(null);

  const fetchVersions = useCallback(async () => {
    if (!name) return;
    try {
      const res = await api.get<VersionListData>(`/workflows/${name}/versions`);
      if (res.data) setData(res.data);
    } finally {
      setLoading(false);
    }
  }, [name]);

  useEffect(() => {
    void fetchVersions();
  }, [fetchVersions]);

  const handleSelectVersion = useCallback(async (version: number) => {
    setSelectedVersion(version);
    if (!name) return;
    const res = await api.get<Record<string, unknown>>(`/workflows/${name}/versions/${version}`);
    if (res.data && typeof res.data === "object") {
      // YAML might not be in the response schema directly, but steps are
      setSelectedYaml(null); // Will show step info instead
    }
  }, [name]);

  const handlePromote = useCallback(async (version: number) => {
    if (!name) return;
    const res = await api.post(`/workflows/${name}/promote`, { version });
    if (res.error) {
      toast.error(`Promote failed: ${res.error.message}`);
    } else {
      toast.success("Version promoted");
      void fetchVersions();
    }
  }, [name, fetchVersions]);

  const handleRollback = useCallback(async () => {
    if (!name) return;
    const res = await api.post(`/workflows/${name}/rollback`, {});
    if (res.error) {
      toast.error(`Rollback failed: ${res.error.message}`);
    } else {
      toast.success("Rolled back to previous version");
      void fetchVersions();
    }
  }, [name, fetchVersions]);

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (!data) {
    return (
      <div className="py-16 text-center">
        <p className="text-muted">Workflow not found</p>
      </div>
    );
  }

  const hasArchived = data.versions.some((v) => v.status === "archived");

  return (
    <div className="space-y-4 sm:space-y-6">
      {/* Back button */}
      <button
        onClick={() => navigate("/workflows")}
        className="flex items-center gap-1 text-sm text-muted hover:text-foreground transition-colors"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to Workflows
      </button>

      {/* Header */}
      <div className="rounded-xl border border-border bg-surface p-4 sm:p-5 shadow-sm">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-foreground">{data.workflow_name}</h1>
            <div className="mt-2 flex items-center gap-3 text-sm text-muted">
              {data.production_version !== null && (
                <span className="flex items-center gap-1.5">
                  Production: <VersionStatusBadge status="production" />
                  <span className="font-mono">v{data.production_version}</span>
                </span>
              )}
              {data.staging_version !== null && (
                <span className="flex items-center gap-1.5">
                  Staging: <VersionStatusBadge status="staging" />
                  <span className="font-mono">v{data.staging_version}</span>
                </span>
              )}
              {data.latest_draft_version !== null && (
                <span className="flex items-center gap-1.5">
                  Draft: <VersionStatusBadge status="draft" />
                  <span className="font-mono">v{data.latest_draft_version}</span>
                </span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {hasArchived && (
              <button
                onClick={handleRollback}
                className={cn(
                  "flex items-center gap-1.5 rounded-lg border border-warning/30 px-3 py-1.5",
                  "text-sm font-medium text-warning",
                  "hover:bg-warning/10 transition-colors"
                )}
              >
                <RotateCcw className="h-4 w-4" />
                Rollback
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Version comparison hint */}
      {selectedVersion !== null && data.production_version !== null && selectedVersion !== data.production_version && (
        <button
          onClick={() => setDiffModal({ a: data.production_version!, b: selectedVersion })}
          className="w-full rounded-lg border border-dashed border-accent/40 px-4 py-2 text-xs text-accent hover:bg-accent/5 transition-colors"
        >
          Compare v{selectedVersion} with production v{data.production_version}
        </button>
      )}

      {/* Version history */}
      <div className="rounded-xl border border-border bg-surface shadow-sm overflow-hidden">
        <div className="border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold text-foreground">
            Version History ({data.versions.length})
          </h2>
        </div>
        <VersionHistory
          versions={data.versions}
          selectedVersion={selectedVersion}
          onSelect={handleSelectVersion}
          onPromote={handlePromote}
        />
      </div>

      {/* YAML preview of selected version */}
      {selectedVersion !== null && (
        <div className="rounded-xl border border-border bg-surface shadow-sm overflow-hidden">
          <div className="border-b border-border px-4 py-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-foreground">
              Version {selectedVersion} Details
            </h2>
            <button
              onClick={() => setSelectedVersion(null)}
              className="text-xs text-muted hover:text-foreground"
            >
              Close
            </button>
          </div>
          <div className="p-4">
            {(() => {
              const v = data.versions.find((v) => v.version === selectedVersion);
              if (!v) return null;
              return (
                <div className="space-y-3">
                  <div className="grid grid-cols-3 gap-4 text-sm">
                    <div>
                      <span className="text-xs text-muted-foreground">Status</span>
                      <div className="mt-1"><VersionStatusBadge status={v.status} /></div>
                    </div>
                    <div>
                      <span className="text-xs text-muted-foreground">Steps</span>
                      <p className="mt-1 font-medium">{v.steps_count}</p>
                    </div>
                    <div>
                      <span className="text-xs text-muted-foreground">Checksum</span>
                      <p className="mt-1 font-mono text-xs truncate">{v.checksum}</p>
                    </div>
                  </div>
                  {v.description && (
                    <div>
                      <span className="text-xs text-muted-foreground">Description</span>
                      <p className="mt-1 text-sm text-foreground">{v.description}</p>
                    </div>
                  )}
                </div>
              );
            })()}
          </div>
        </div>
      )}

      {/* Diff modal */}
      {diffModal && name && (
        <VersionDiffModal
          open={true}
          onClose={() => setDiffModal(null)}
          workflowName={name}
          versionA={diffModal.a}
          versionB={diffModal.b}
        />
      )}
    </div>
  );
}
