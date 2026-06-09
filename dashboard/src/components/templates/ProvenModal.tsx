import { useCallback, useEffect, useState } from "react";
import {
  BadgeCheck,
  CheckCircle2,
  Loader2,
  Play,
  ShieldAlert,
  X,
  XCircle,
} from "lucide-react";
import { api } from "@/api/client";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";

// ---- API shapes ----

interface ChecksumEntry {
  file: string;
  sha256: string;
  valid: boolean;
  step_count?: number | null;
  recorded_cost_usd?: number | null;
}

interface VerificationData {
  proven: boolean;
  error?: string;
  manifest?: {
    name: string;
    version: string;
    description: string;
    author: string;
    license?: string;
    sandcastle_version?: string;
    created_at?: string;
  };
  workflow?: ChecksumEntry;
  cassettes?: ChecksumEntry[];
  checksums_valid?: boolean;
  installed_workflow_matches?: boolean;
}

interface CassetteReplayResult {
  file: string;
  passed: boolean;
  detail: string;
  replay_hits: number;
  replay_misses: number;
}

interface VerifyData {
  ok: boolean;
  errors: string[];
  cassettes: CassetteReplayResult[];
}

interface ProvenModalProps {
  templateName: string;
  onClose: () => void;
}

function shortSha(sha: string): string {
  return `${sha.slice(0, 12)}…`;
}

function cassetteLabel(file: string): string {
  return file.replace(/^cassettes\//, "");
}

/** Proof inspector for a bundle-verified template: manifest details, payload
 *  checksums, and a one-click strict replay of the bundled cassettes. */
export function ProvenModal({ templateName, onClose }: ProvenModalProps) {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [data, setData] = useState<VerificationData | null>(null);

  const [replaying, setReplaying] = useState(false);
  const [replayError, setReplayError] = useState<string | null>(null);
  const [replay, setReplay] = useState<VerifyData | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(false);
    api
      .get<VerificationData>(`/templates/${encodeURIComponent(templateName)}/verification`)
      .then((res) => {
        if (cancelled) return;
        if (res.data) setData(res.data);
        else setLoadError(true);
      })
      .catch(() => {
        if (!cancelled) setLoadError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [templateName]);

  const handleReplay = useCallback(async () => {
    setReplaying(true);
    setReplayError(null);
    setReplay(null);
    try {
      const res = await api.post<VerifyData>(
        `/templates/${encodeURIComponent(templateName)}/verify`
      );
      if (res.data) setReplay(res.data);
      else setReplayError(res.error?.message ?? "Verification request failed");
    } catch {
      setReplayError("Could not reach the API server");
    } finally {
      setReplaying(false);
    }
  }, [templateName]);

  const manifest = data?.manifest;

  return (
    <>
      <div
        className="fixed inset-0 z-[60] bg-black/40"
        onClick={onClose}
        aria-hidden="true"
      />
      <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`Proof of execution for ${templateName}`}
          className="w-full max-w-lg max-h-[85vh] overflow-y-auto rounded-xl border border-border bg-surface p-6 shadow-xl"
        >
          {/* Header */}
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-foreground flex items-center gap-2">
              <BadgeCheck className="h-5 w-5 text-success" />
              Proven Template
            </h2>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close proof dialog"
              className="rounded-lg p-1 text-muted hover:text-foreground"
            >
              <X aria-hidden="true" className="h-5 w-5" />
            </button>
          </div>

          {loading ? (
            <div className="flex h-32 items-center justify-center">
              <LoadingSpinner size="md" />
            </div>
          ) : loadError || !data ? (
            <div className="flex items-center gap-2 rounded-lg bg-error/10 border border-error/30 p-3">
              <ShieldAlert className="h-5 w-5 text-error shrink-0" />
              <p className="text-sm text-error">
                Could not load the verification status. Check that the API
                server is running and try again.
              </p>
            </div>
          ) : !data.proven ? (
            <p className="text-sm text-muted">
              {data.error ??
                "This template was not installed from a verified .sctpl bundle."}
            </p>
          ) : (
            <div className="space-y-4">
              <p className="text-sm text-muted">
                Installed from a verified .sctpl bundle. The recorded cassettes
                are the proof - replay them any time, offline and at $0.
              </p>

              {/* Manifest */}
              {manifest && (
                <div className="rounded-lg bg-background p-3 space-y-1.5">
                  {[
                    ["Name", manifest.name],
                    ["Version", manifest.version],
                    ["Author", manifest.author || "-"],
                    ["License", manifest.license ?? "-"],
                    ["Packed with", manifest.sandcastle_version ?? "-"],
                    [
                      "Created",
                      manifest.created_at
                        ? new Date(manifest.created_at).toLocaleDateString()
                        : "-",
                    ],
                  ].map(([label, value]) => (
                    <div
                      key={label}
                      className="flex items-center gap-2 text-xs text-muted-foreground"
                    >
                      <span className="w-24 shrink-0 font-medium">{label}:</span>
                      <span className="font-mono text-foreground truncate">{value}</span>
                    </div>
                  ))}
                </div>
              )}

              {/* Checksums */}
              <div className="space-y-1.5">
                <p className="text-xs font-semibold text-muted uppercase tracking-wider">
                  Payload checksums
                </p>
                <div className="rounded-lg border border-border divide-y divide-border/60 overflow-hidden">
                  {data.workflow && (
                    <ChecksumRow
                      label={data.workflow.file}
                      sha={data.workflow.sha256}
                      valid={data.workflow.valid}
                    />
                  )}
                  {(data.cassettes ?? []).map((c) => (
                    <ChecksumRow
                      key={c.file}
                      label={cassetteLabel(c.file)}
                      sha={c.sha256}
                      valid={c.valid}
                    />
                  ))}
                </div>
                {data.installed_workflow_matches === false && (
                  <p className="flex items-center gap-1.5 text-xs text-warning">
                    <ShieldAlert className="h-3.5 w-3.5 shrink-0" />
                    The installed workflow was edited after install - it no
                    longer matches the bundled proof.
                  </p>
                )}
              </div>

              {/* Replay proof */}
              <div className="space-y-2">
                <button
                  type="button"
                  onClick={handleReplay}
                  disabled={replaying}
                  className={cn(
                    "flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-4 py-2",
                    "text-sm font-medium text-accent-foreground",
                    "hover:bg-accent-hover transition-all shadow-sm",
                    replaying && "opacity-70 cursor-not-allowed"
                  )}
                >
                  {replaying ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Replaying cassettes...
                    </>
                  ) : (
                    <>
                      <Play className="h-4 w-4" />
                      Replay proof locally
                    </>
                  )}
                </button>

                {replayError && (
                  <div className="flex items-center gap-2 rounded-lg bg-error/10 border border-error/30 p-3">
                    <XCircle className="h-4 w-4 text-error shrink-0" />
                    <p className="text-xs text-error">{replayError}</p>
                  </div>
                )}

                {replay && (
                  <div className="space-y-2" data-testid="replay-result">
                    <div
                      className={cn(
                        "flex items-center gap-2 rounded-lg border p-3",
                        replay.ok
                          ? "bg-success/10 border-success/30"
                          : "bg-error/10 border-error/30"
                      )}
                    >
                      {replay.ok ? (
                        <CheckCircle2 className="h-4 w-4 text-success shrink-0" />
                      ) : (
                        <XCircle className="h-4 w-4 text-error shrink-0" />
                      )}
                      <p
                        className={cn(
                          "text-xs font-medium",
                          replay.ok ? "text-success" : "text-error"
                        )}
                      >
                        {replay.ok
                          ? "Proof verified - every cassette replayed at $0."
                          : "Verification failed."}
                      </p>
                    </div>

                    {replay.errors.length > 0 && (
                      <ul className="space-y-1 rounded-lg bg-background p-3">
                        {replay.errors.map((err) => (
                          <li key={err} className="text-xs text-error font-mono">
                            {err}
                          </li>
                        ))}
                      </ul>
                    )}

                    {replay.cassettes.length > 0 && (
                      <div className="rounded-lg border border-border divide-y divide-border/60 overflow-hidden">
                        {replay.cassettes.map((c) => (
                          <div
                            key={c.file}
                            className="flex items-center justify-between gap-3 px-3 py-2"
                          >
                            <div className="min-w-0">
                              <p className="text-xs font-mono text-foreground truncate">
                                {cassetteLabel(c.file)}
                              </p>
                              <p className="text-[11px] text-muted-foreground truncate">
                                {c.detail}
                              </p>
                            </div>
                            <span
                              className={cn(
                                "shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold",
                                c.passed
                                  ? "bg-success/15 text-success"
                                  : "bg-error/15 text-error"
                              )}
                            >
                              {c.passed ? "PASS" : "FAIL"}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function ChecksumRow({
  label,
  sha,
  valid,
}: {
  label: string;
  sha: string;
  valid: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-3 px-3 py-2">
      <div className="min-w-0">
        <p className="text-xs font-mono text-foreground truncate">{label}</p>
        <p className="text-[11px] font-mono text-muted-foreground" title={sha}>
          sha256: {shortSha(sha)}
        </p>
      </div>
      {valid ? (
        <CheckCircle2 className="h-4 w-4 shrink-0 text-success" />
      ) : (
        <XCircle className="h-4 w-4 shrink-0 text-error" />
      )}
    </div>
  );
}
