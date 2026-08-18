import { useState, useEffect, useCallback } from "react";
import { ChevronDown, ChevronRight, RotateCcw, GitFork, Copy, Check, FileText } from "lucide-react";
import { RunStatusBadge } from "@/components/runs/RunStatusBadge";
import { TerminalLog } from "@/components/runs/TerminalLog";
import { API_BASE_URL } from "@/lib/constants";
import { cn, formatDuration, formatCost, parseUTC, isSafeUrl } from "@/lib/utils";

interface ImageInfo {
  index: number;
  url?: string;
  filename?: string;
  error?: string;
  mime_type?: string;
}

function extractImages(value: unknown): ImageInfo[] | null {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const obj = value as Record<string, unknown>;
    if (Array.isArray(obj._images) && obj._images.length > 0) {
      return obj._images as ImageInfo[];
    }
  }
  return null;
}

function ElapsedTimer({ since }: { since: string }) {
  const [elapsed, setElapsed] = useState(() =>
    Math.max(0, Math.floor((Date.now() - parseUTC(since).getTime()) / 1000))
  );
  useEffect(() => {
    const interval = setInterval(
      () => setElapsed(Math.max(0, Math.floor((Date.now() - parseUTC(since).getTime()) / 1000))),
      1000,
    );
    return () => clearInterval(interval);
  }, [since]);
  return <span className="font-mono text-xs text-muted">{formatDuration(elapsed)}</span>;
}

function filterInternalKeys(value: unknown): unknown {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).filter(
        ([k]) => !k.startsWith("_")
      )
    );
  }
  return value;
}

function extractText(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const obj = value as Record<string, unknown>;
    // Unwrap single-key objects like {"result": "..."}
    const keys = Object.keys(obj);
    if (keys.length === 1 && typeof obj[keys[0]] === "string") {
      return obj[keys[0]] as string;
    }
  }
  return null;
}

function looksLikeMarkdown(text: string): boolean {
  return /(?:^|\n)#\s|(\*\*|__).+?\1|- |\* |```|`[^`]+`/.test(text);
}

function looksLikeLogs(text: string): boolean {
  if (text.split("\n").length < 3) return false;
  const logPatterns = [
    /^(ERROR|WARN(ING)?|INFO|DEBUG)\b/im,
    /\[\d{2}:\d{2}:\d{2}\]/,
    /\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}/,
    /\berror:/i,
    /^(>>>|\.{3}|\$|#)\s/m,
  ];
  let hits = 0;
  for (const p of logPatterns) {
    if (p.test(text)) hits++;
  }
  return hits >= 1 || text.split("\n").length >= 10;
}

function OutputBlock({ value }: { value: unknown }) {
  const text = extractText(value);
  if (text) {
    // Short single-line output - render inline
    if (text.length < 100 && !text.includes("\n")) {
      return (
        <p className="text-sm text-foreground">{text}</p>
      );
    }
    const isMd = looksLikeMarkdown(text);
    return (
      <div>
        {isMd && (
          <p className="mb-1 text-[11px] font-medium text-muted/60">Markdown output</p>
        )}
        <div className="max-h-96 overflow-x-auto overflow-y-auto rounded-md bg-background p-3 text-sm text-foreground whitespace-pre-wrap break-words leading-relaxed">
          {text}
        </div>
      </div>
    );
  }
  return (
    <pre className="max-h-64 overflow-x-auto overflow-y-auto rounded-md bg-background p-3 font-mono text-xs text-foreground whitespace-pre-wrap break-words">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

interface StepCardProps {
  stepId: string;
  status: string;
  costUsd: number;
  durationSeconds: number;
  attempt: number;
  error: string | null;
  output: unknown;
  parallelIndex: number | null;
  startedAt: string | null;
  pdfArtifact?: boolean;
  responsibility?: string;
  owner?: string;
  stepType?: string;
  artifactUrl?: string;
  model?: string | null;
  tokensSaved?: number;
  compactionStrategy?: string | null;
  runId?: string;
  onReplay?: (stepId: string) => void;
  onFork?: (stepId: string) => void;
}

export function StepCard({
  stepId,
  status,
  costUsd,
  durationSeconds,
  attempt,
  error,
  output,
  parallelIndex,
  startedAt,
  pdfArtifact,
  responsibility,
  owner,
  stepType,
  artifactUrl,
  model,
  tokensSaved,
  compactionStrategy,
  runId,
  onReplay,
  onFork,
}: StepCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const isCompleted = status === "completed" || status === "failed";

  const displayOutput = filterInternalKeys(output);

  const handleCopyOutput = useCallback(async () => {
    if (displayOutput == null) return;
    const text =
      typeof displayOutput === "string"
        ? displayOutput
        : JSON.stringify(displayOutput, null, 2);
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [displayOutput]);

  return (
    <div className="rounded-lg border border-border bg-surface shadow-sm">
      <button
        onClick={() => setExpanded(!expanded)}
        className={cn(
          "flex w-full items-center gap-3 px-4 py-3 text-left",
          "transition-colors duration-150 hover:bg-border/20"
        )}
      >
        {expanded ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-muted" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-muted" />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-foreground">{stepId}</span>
            {parallelIndex !== null && (
              <span className="text-xs text-muted">[{parallelIndex}]</span>
            )}
            {owner && (
              <span className="rounded-full bg-accent/10 px-1.5 py-0.5 text-[10px] font-medium text-accent">
                {owner}
              </span>
            )}
          </div>
          {responsibility && (
            <p className="mt-0.5 text-xs italic text-muted">{responsibility}</p>
          )}
          <div className="mt-0.5 flex flex-wrap items-center gap-3 text-xs text-muted">
            {model && (
              <span className="rounded-md bg-running/10 px-1.5 py-0.5 font-mono text-[11px] text-running" title="Provider that ran this step">
                {model}
              </span>
            )}
            <span>{formatDuration(durationSeconds)}</span>
            <span>{formatCost(costUsd)}</span>
            {!!tokensSaved && tokensSaved > 0 && (
              <span
                className="rounded-md bg-success/10 px-1.5 py-0.5 font-mono text-[11px] text-success"
                title={`Context compaction removed ~${tokensSaved.toLocaleString()} tokens before this step ran${compactionStrategy ? ` (${compactionStrategy})` : ""}`}
              >
                −{tokensSaved.toLocaleString()} tok
              </span>
            )}
            {attempt > 1 && <span>attempt {attempt}</span>}
          </div>
        </div>
        <RunStatusBadge status={status} />
      </button>

      {expanded && (
        <div className="border-t border-border px-4 py-3">
          {status === "running" && !output && !error && (
            <div className="flex items-center justify-between py-2">
              <div className="flex items-center gap-2">
                <div className="h-2 w-2 animate-pulse rounded-full bg-accent" />
                <p className="text-xs text-muted">Running in sandbox...</p>
              </div>
              {startedAt && <ElapsedTimer since={startedAt} />}
            </div>
          )}
          {error && (
            <div className={cn(
              "mb-3 rounded-md px-3 py-2",
              status === "failed"
                ? "border border-error/30 bg-error/10"
                : "bg-error/10"
            )}>
              <p className="text-xs font-semibold text-error">
                {status === "failed" ? "Step Failed" : "Error"}
              </p>
              <p className="mt-1 font-mono text-xs text-error/80 whitespace-pre-wrap break-words leading-relaxed">
                {error}
              </p>
            </div>
          )}
          {/* Image gallery for approval steps */}
          {output != null && extractImages(output) && (
            <div className="mb-3">
              <p className="mb-2 text-xs font-medium text-muted">Generated Images</p>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                {extractImages(output)!.map((img) => (
                  <div key={img.index} className="rounded-lg border border-border overflow-hidden bg-background">
                    {img.error ? (
                      <div className="flex items-center justify-center h-32 text-xs text-error px-2 text-center">
                        {img.error}
                      </div>
                    ) : img.url && isSafeUrl(img.url) ? (
                      <img
                        src={img.url}
                        alt={`Generated image ${img.index + 1}`}
                        className="w-full h-auto object-contain max-h-56"
                        loading="lazy"
                      />
                    ) : null}
                    <div className="px-2 py-1 text-[10px] text-muted border-t border-border">
                      {img.filename || `Image ${img.index + 1}`}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {output != null && (() => {
            const outputText = typeof displayOutput === "string"
              ? displayOutput
              : JSON.stringify(displayOutput, null, 2);
            const isLog = typeof outputText === "string" && looksLikeLogs(outputText);
            if (isLog) {
              return (
                <TerminalLog
                  logs={outputText}
                  stepName={stepId}
                  status={status}
                  isLive={status === "running"}
                />
              );
            }
            return (
              <div>
                <div className="mb-1 flex items-center justify-between">
                  <p className="text-xs font-medium text-muted">Output</p>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      void handleCopyOutput();
                    }}
                    className={cn(
                      "flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium",
                      "border border-border text-muted",
                      "hover:bg-border/40 hover:text-foreground transition-colors"
                    )}
                  >
                    {copied ? (
                      <>
                        <Check className="h-3 w-3 text-success" />
                        Copied!
                      </>
                    ) : (
                      <>
                        <Copy className="h-3 w-3" />
                        Copy
                      </>
                    )}
                  </button>
                </div>
                <OutputBlock value={displayOutput} />
              </div>
            );
          })()}

          {/* PDF download - show for pdf_artifact flag or completed report steps with artifact_url */}
          {((pdfArtifact && runId) || (stepType === "report" && status === "completed" && artifactUrl)) && (
            <div className="mt-2">
              <a
                href={artifactUrl || `${API_BASE_URL}/runs/${runId}/steps/${stepId}/pdf`}
                target="_blank"
                rel="noopener noreferrer"
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium",
                  "border border-accent/30 text-accent",
                  "hover:bg-accent/10 transition-colors"
                )}
              >
                <FileText className="h-3 w-3" />
                Download PDF
              </a>
            </div>
          )}

          {/* Replay / Fork buttons for completed steps */}
          {isCompleted && (onReplay || onFork) && (
            <div className="mt-3 flex items-center gap-2 border-t border-border pt-3">
              {onReplay && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onReplay(stepId);
                  }}
                  className={cn(
                    "flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium",
                    "border border-border text-muted",
                    "hover:bg-border/40 hover:text-foreground transition-colors"
                  )}
                >
                  <RotateCcw className="h-3 w-3" />
                  Replay from here
                </button>
              )}
              {onFork && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onFork(stepId);
                  }}
                  className={cn(
                    "flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium",
                    "border border-accent/30 text-accent",
                    "hover:bg-accent/10 transition-colors"
                  )}
                >
                  <GitFork className="h-3 w-3" />
                  Fork from here
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
