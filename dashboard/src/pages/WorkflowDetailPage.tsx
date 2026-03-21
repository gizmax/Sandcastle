import { useCallback, useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { GitCompare, RotateCcw, Sparkles, Upload, X, Download, Loader2, Copy, ArrowLeft } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { VersionHistory } from "@/components/workflows/VersionHistory";
import { VersionStatusBadge } from "@/components/workflows/VersionStatusBadge";
import { VersionDiffModal } from "@/components/workflows/VersionDiffModal";
import { Breadcrumb } from "@/components/shared/Breadcrumb";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { CopyButton } from "@/components/shared/CopyButton";
import { cn, HUB_CONTRIB_URL } from "@/lib/utils";

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
  const [diffModal, setDiffModal] = useState<{ a: number; b: number } | null>(null);
  const [shareOpen, setShareOpen] = useState(false);
  const [shareYaml, setShareYaml] = useState<string | null>(null);
  const [shareLoading, setShareLoading] = useState(false);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape" && shareOpen) setShareOpen(false);
      if (e.key === "Escape" && diffModal) setDiffModal(null);
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [shareOpen, diffModal]);

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
    await api.get<Record<string, unknown>>(`/workflows/${name}/versions/${version}`);
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

  const handleShare = useCallback(async () => {
    if (!name) return;
    setShareOpen(true);
    setShareLoading(true);
    setShareYaml(null);
    const res = await api.get<{ yaml_content: string }>(`/workflows/${name}/export`);
    if (res.data) {
      setShareYaml((res.data as Record<string, unknown>).yaml_content as string);
    } else {
      setShareYaml("# Export not available - workflow may not have a production version");
    }
    setShareLoading(false);
  }, [name]);

  const handleShareDownload = useCallback(() => {
    if (!shareYaml || !name) return;
    const blob = new Blob([shareYaml], { type: "text/yaml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${name}.yaml`;
    a.click();
    URL.revokeObjectURL(url);
  }, [shareYaml, name]);

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (!data) {
    return (
      <div className="py-16 text-center space-y-3">
        <p className="text-muted">Workflow &ldquo;{name}&rdquo; was not found. It may have been deleted or renamed.</p>
        <button
          onClick={() => navigate("/workflows")}
          className="inline-flex items-center gap-1 text-sm text-accent hover:text-accent/80 transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Workflows
        </button>
      </div>
    );
  }

  const hasArchived = data.versions.some((v) => v.status === "archived");

  return (
    <div className="space-y-4 sm:space-y-6">
      <Breadcrumb items={[
        { label: "Overview", href: "/" },
        { label: "Workflows", href: "/workflows" },
        { label: data.workflow_name },
      ]} />

      {/* Header */}
      <div className="rounded-xl border border-border bg-surface p-4 sm:p-5 shadow-sm">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="flex items-center gap-1 text-xl font-semibold tracking-tight text-foreground">
              {data.workflow_name}
              <CopyButton value={data.workflow_name} label="workflow name" />
            </h1>
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
            <button
              onClick={() => navigate("/evolution", { state: { workflow: data.workflow_name } })}
              className={cn(
                "flex items-center gap-1.5 rounded-lg border border-accent/30 px-3 py-1.5",
                "text-sm font-medium text-accent",
                "hover:bg-accent/10 transition-colors"
              )}
            >
              <Sparkles className="h-4 w-4" />
              Evolve
            </button>
            <button
              onClick={handleShare}
              className={cn(
                "flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5",
                "text-sm font-medium text-muted",
                "hover:text-foreground hover:bg-border/40 transition-colors"
              )}
            >
              <Upload className="h-4 w-4" />
              Share
            </button>
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
        <div className="border-b border-border px-4 py-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-foreground">
            Version History ({data.versions.length})
          </h2>
          {data.versions.length >= 2 && (
            <button
              onClick={() => {
                const sorted = [...data.versions].sort((a, b) => b.version - a.version);
                setDiffModal({ a: sorted[1].version, b: sorted[0].version });
              }}
              className={cn(
                "flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5",
                "text-xs font-medium text-muted",
                "hover:text-accent hover:border-accent/40 hover:bg-accent/5 transition-colors"
              )}
            >
              <GitCompare className="h-3.5 w-3.5" />
              Compare Versions
            </button>
          )}
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
          availableVersions={data.versions.map((v) => v.version)}
        />
      )}

      {/* Share modal */}
      {shareOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" onClick={() => setShareOpen(false)}>
          <div className="w-full max-w-2xl rounded-xl border border-border bg-surface shadow-2xl mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-border px-5 py-4">
              <h2 className="text-base font-semibold text-foreground">Share Workflow</h2>
              <button
                onClick={() => setShareOpen(false)}
                className="rounded-md p-1 text-muted hover:text-foreground hover:bg-border/40 transition-colors"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="p-5 space-y-4">
              {shareLoading ? (
                <div className="flex items-center justify-center py-12">
                  <Loader2 className="h-6 w-6 animate-spin text-muted" />
                </div>
              ) : (
                <>
                  <p className="text-xs text-muted">
                    Exported YAML has credentials and environment variables removed.
                  </p>
                  <div className="max-h-72 overflow-auto rounded-lg border border-border bg-background p-4">
                    <pre className="font-data text-xs text-foreground whitespace-pre-wrap">{shareYaml}</pre>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => {
                          if (shareYaml) {
                            void navigator.clipboard.writeText(shareYaml);
                            toast.success("YAML copied to clipboard");
                          }
                        }}
                        className={cn(
                          "flex items-center gap-1.5 rounded-lg border border-border px-4 py-2",
                          "text-sm font-medium text-foreground",
                          "hover:bg-border/40 transition-colors"
                        )}
                      >
                        <Copy className="h-4 w-4" />
                        Copy YAML
                      </button>
                      <button
                        onClick={handleShareDownload}
                        className={cn(
                          "flex items-center gap-1.5 rounded-lg border border-border px-4 py-2",
                          "text-sm font-medium text-foreground",
                          "hover:bg-border/40 transition-colors"
                        )}
                      >
                        <Download className="h-4 w-4" />
                        Download YAML
                      </button>
                    </div>
                    <a
                      href={HUB_CONTRIB_URL}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={cn(
                        "flex items-center gap-1.5 rounded-lg bg-accent px-4 py-2",
                        "text-sm font-medium text-background",
                        "hover:bg-accent/90 transition-colors"
                      )}
                    >
                      <Upload className="h-4 w-4" />
                      Publish to Hub
                    </a>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
