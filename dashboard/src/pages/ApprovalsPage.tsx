import { useCallback, useEffect, useState } from "react";
import { ShieldCheck, Clock, CheckCircle2, XCircle, SkipForward, RefreshCw, MessageSquare, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { formatRelativeTime, cn, isSafeUrl } from "@/lib/utils";
import { useNavigate } from "react-router-dom";

interface ApprovalItem {
  id: string;
  run_id: string;
  step_id: string;
  status: string;
  message: string;
  request_data: Record<string, unknown> | null;
  response_data: Record<string, unknown> | null;
  reviewer_id: string | null;
  reviewer_comment: string | null;
  timeout_at: string | null;
  on_timeout: string;
  allow_edit: boolean;
  created_at: string | null;
  resolved_at: string | null;
}

interface ImageEntry {
  index: number;
  url?: string;
  filename?: string;
  error?: string;
  mime_type?: string;
}

const STATUS_STYLES: Record<string, { bg: string; text: string; dot: string; label: string }> = {
  pending: { bg: "bg-warning/15 border-warning/30", text: "text-warning", dot: "bg-warning animate-pulse", label: "Pending" },
  approved: { bg: "bg-success/15 border-success/30", text: "text-success", dot: "bg-success", label: "Approved" },
  rejected: { bg: "bg-error/15 border-error/30", text: "text-error", dot: "bg-error", label: "Rejected" },
  skipped: { bg: "bg-muted/15 border-muted/30", text: "text-muted", dot: "bg-muted", label: "Skipped" },
  timed_out: { bg: "bg-muted/15 border-muted/30", text: "text-muted", dot: "bg-muted", label: "Timed Out" },
};

export default function ApprovalsPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<ApprovalItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("all");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<Set<string>>(new Set());

  // Selective regeneration state - keyed by approval id
  const [rejectedShots, setRejectedShots] = useState<Record<string, Set<number>>>({});
  const [regenFeedback, setRegenFeedback] = useState<Record<string, string>>({});

  const fetchItems = useCallback(async (cancelled?: { current: boolean }) => {
    try {
      setError(null);
      const params: Record<string, string> = {};
      if (filter !== "all") params.status = filter;
      const res = await api.get<ApprovalItem[]>("/approvals", params);
      if (cancelled?.current) return;
      if (res.data) setItems(res.data);
    } catch {
      if (cancelled?.current) return;
      setError("Could not connect to the API server");
    } finally {
      if (!cancelled?.current) setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    const cancelled = { current: false };
    void fetchItems(cancelled);
    return () => { cancelled.current = true; };
  }, [fetchItems]);

  const handleAction = useCallback(
    async (id: string, action: "approve" | "reject" | "skip") => {
      if (actionLoading.has(id)) return;
      setActionLoading((prev) => new Set(prev).add(id));
      try {
        const res = await api.post(`/approvals/${id}/${action}`);
        if (res.error) {
          toast.error(`Failed to ${action}: ${res.error.message}`);
          return;
        }
        const ACTION_PAST: Record<string, string> = { approve: "approved", reject: "rejected", skip: "skipped" };
        toast.success(`Approval ${ACTION_PAST[action] ?? action}`);
        void fetchItems();
      } finally {
        setActionLoading((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
      }
    },
    [fetchItems, actionLoading]
  );

  const toggleRejectedShot = useCallback((approvalId: string, shotIndex: number) => {
    setRejectedShots((prev) => {
      const current = new Set(prev[approvalId] ?? []);
      if (current.has(shotIndex)) {
        current.delete(shotIndex);
      } else {
        current.add(shotIndex);
      }
      return { ...prev, [approvalId]: current };
    });
  }, []);

  const handleRegenerate = useCallback(
    async (id: string) => {
      const selected = rejectedShots[id];
      if (!selected || selected.size === 0) {
        toast.error("Select at least one image to regenerate");
        return;
      }
      if (actionLoading.has(id)) return;
      setActionLoading((prev) => new Set(prev).add(id));
      try {
        const res = await api.post(`/approvals/${id}/regenerate`, {
          comment: regenFeedback[id] || null,
          edited_data: {
            rejected_shots: Array.from(selected).sort((a, b) => a - b),
            feedback: regenFeedback[id] || "",
          },
        });
        if (res.error) {
          toast.error(`Failed to regenerate: ${res.error.message}`);
          return;
        }
        toast.success(`Regenerating ${selected.size} image${selected.size > 1 ? "s" : ""}...`);
        // Clear selection state for this approval
        setRejectedShots((prev) => {
          const next = { ...prev };
          delete next[id];
          return next;
        });
        setRegenFeedback((prev) => {
          const next = { ...prev };
          delete next[id];
          return next;
        });
        void fetchItems();
      } finally {
        setActionLoading((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
      }
    },
    [fetchItems, actionLoading, rejectedShots, regenFeedback]
  );

  const filters = [
    { key: "all", label: "All" },
    { key: "pending", label: "Pending" },
    { key: "approved", label: "Approved" },
    { key: "rejected", label: "Rejected" },
  ];

  const pendingCount = items.filter((i) => i.status === "pending").length;

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <h1 className="mb-4 sm:mb-6 text-xl sm:text-2xl font-semibold font-display tracking-tight text-foreground">Approval Gates</h1>
        <div className="rounded-xl border border-error/30 bg-error/5 p-4">
          <p className="text-sm text-error">{error}</p>
          <button
            onClick={() => { setLoading(true); void fetchItems(); }}
            className="mt-2 text-xs font-medium text-accent hover:text-accent/80 transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4 sm:space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-xl sm:text-2xl font-semibold font-display tracking-tight text-foreground">Approval Gates</h1>
          {pendingCount > 0 && (
            <span className="flex h-6 min-w-6 items-center justify-center rounded-full bg-warning px-2 text-xs font-semibold text-white">
              {pendingCount}
            </span>
          )}
        </div>
      </div>

      {/* Filters */}
      <div className="flex gap-1 rounded-lg bg-background p-1">
        {filters.map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            aria-pressed={filter === f.key}
            className={cn(
              "rounded-md px-3 py-1.5 text-sm font-medium transition-all duration-200",
              filter === f.key
                ? "bg-surface text-foreground shadow-sm"
                : "text-muted hover:text-foreground"
            )}
          >
            {f.label}
          </button>
        ))}
      </div>

      {items.length === 0 ? (
        <EmptyState
          icon={ShieldCheck}
          title="No approvals"
          description="Workflow steps requiring human review will appear here."
        />
      ) : (
        <div className="space-y-3">
          {items.map((item) => {
            const style = STATUS_STYLES[item.status] || STATUS_STYLES.pending;
            const isExpanded = expandedId === item.id;
            const images = Array.isArray((item.request_data as Record<string, unknown>)?._images)
              ? ((item.request_data as Record<string, unknown>)._images as ImageEntry[])
              : null;
            const selectedForRegen = rejectedShots[item.id] ?? new Set<number>();
            const hasImages = images !== null && images.length > 0;
            const isPending = item.status === "pending";

            return (
              <div
                key={item.id}
                className="rounded-xl border border-border bg-surface shadow-sm overflow-hidden"
              >
                <div
                  role="button"
                  tabIndex={0}
                  aria-expanded={isExpanded}
                  aria-label={`${item.message} - ${isExpanded ? "collapse" : "expand"} details`}
                  className="flex items-center gap-3 sm:gap-4 px-3 sm:px-5 py-3 sm:py-4 cursor-pointer hover:bg-border/10 transition-colors focus-visible:outline-none focus-visible:bg-border/10"
                  onClick={() => setExpandedId(isExpanded ? null : item.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setExpandedId(isExpanded ? null : item.id);
                    }
                  }}
                >
                  {/* Status indicator */}
                  <div className={cn("flex h-10 w-10 shrink-0 items-center justify-center rounded-lg", style.bg, "border")}>
                    {item.status === "pending" ? (
                      <Clock className={cn("h-5 w-5", style.text)} />
                    ) : item.status === "approved" ? (
                      <CheckCircle2 className={cn("h-5 w-5", style.text)} />
                    ) : item.status === "rejected" ? (
                      <XCircle className={cn("h-5 w-5", style.text)} />
                    ) : (
                      <SkipForward className={cn("h-5 w-5", style.text)} />
                    )}
                  </div>

                  {/* Info */}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-foreground truncate">{item.message}</p>
                    <div className="flex items-center gap-3 mt-1">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          navigate(`/runs/${item.run_id}`);
                        }}
                        className="text-xs font-medium text-accent hover:text-accent-hover transition-colors"
                      >
                        {item.step_id}
                      </button>
                      <span className="text-xs text-muted">
                        {item.created_at ? formatRelativeTime(item.created_at) : ""}
                      </span>
                      {item.timeout_at && item.status === "pending" && (
                        <span className="text-xs text-warning">
                          Timeout: {item.on_timeout}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Status badge */}
                  <span
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium",
                      style.bg,
                      style.text
                    )}
                  >
                    <span className={cn("h-1.5 w-1.5 rounded-full", style.dot)} />
                    {style.label}
                  </span>

                  {/* Action buttons (only for pending) */}
                  {isPending && (
                    <div className="flex items-center gap-1.5 shrink-0">
                      <button
                        disabled={actionLoading.has(item.id)}
                        onClick={(e) => {
                          e.stopPropagation();
                          handleAction(item.id, "approve");
                        }}
                        className={cn(
                          "flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium",
                          "bg-success/10 text-success hover:bg-success/20 transition-colors",
                          "disabled:opacity-50 disabled:cursor-not-allowed"
                        )}
                      >
                        {actionLoading.has(item.id) ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                        Approve
                      </button>
                      <button
                        disabled={actionLoading.has(item.id)}
                        onClick={(e) => {
                          e.stopPropagation();
                          handleAction(item.id, "reject");
                        }}
                        className={cn(
                          "flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium",
                          "bg-error/10 text-error hover:bg-error/20 transition-colors",
                          "disabled:opacity-50 disabled:cursor-not-allowed"
                        )}
                      >
                        {actionLoading.has(item.id) ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <XCircle className="h-3.5 w-3.5" />}
                        Reject
                      </button>
                      <button
                        disabled={actionLoading.has(item.id)}
                        onClick={(e) => {
                          e.stopPropagation();
                          handleAction(item.id, "skip");
                        }}
                        className={cn(
                          "flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium",
                          "text-muted hover:bg-border/40 transition-colors",
                          "disabled:opacity-50 disabled:cursor-not-allowed"
                        )}
                      >
                        {actionLoading.has(item.id) ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <SkipForward className="h-3.5 w-3.5" />}
                        Skip
                      </button>
                    </div>
                  )}
                </div>

                {/* Expanded detail */}
                {isExpanded && item.request_data && (
                  <div className="border-t border-border px-5 py-4 bg-background/50">
                    {/* Image gallery with selection checkboxes */}
                    {hasImages && (
                      <div className="mb-4">
                        <div className="flex items-center justify-between mb-2">
                          <p className="text-xs font-medium text-muted-foreground">Generated Images</p>
                          {isPending && selectedForRegen.size > 0 && (
                            <span className="text-xs text-accent font-medium">
                              {selectedForRegen.size} selected for regeneration
                            </span>
                          )}
                        </div>
                        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                          {images!.map((img) => {
                            const isSelected = selectedForRegen.has(img.index);
                            return (
                              <div
                                key={img.index}
                                className={cn(
                                  "rounded-lg border overflow-hidden bg-surface relative group",
                                  isSelected
                                    ? "border-accent ring-2 ring-accent/30"
                                    : "border-border",
                                  isPending && "cursor-pointer"
                                )}
                                onClick={isPending ? () => toggleRejectedShot(item.id, img.index) : undefined}
                              >
                                {/* Selection checkbox overlay for pending approvals */}
                                {isPending && (
                                  <div className="absolute top-2 left-2 z-10">
                                    <div
                                      className={cn(
                                        "flex h-5 w-5 items-center justify-center rounded border-2 transition-all",
                                        isSelected
                                          ? "bg-accent border-accent text-white"
                                          : "bg-black/30 border-white/70 text-transparent group-hover:border-white"
                                      )}
                                    >
                                      {isSelected && (
                                        <XCircle className="h-3 w-3" />
                                      )}
                                    </div>
                                  </div>
                                )}

                                {/* Rejection overlay */}
                                {isSelected && (
                                  <div className="absolute inset-0 bg-error/10 z-[5] pointer-events-none" />
                                )}

                                {img.error ? (
                                  <div className="flex items-center justify-center h-40 text-xs text-error px-2 text-center">
                                    {img.error}
                                  </div>
                                ) : img.url && isSafeUrl(img.url) ? (
                                  <img
                                    src={img.url}
                                    alt={`Generated image ${img.index + 1}`}
                                    className={cn(
                                      "w-full h-auto object-contain max-h-64 transition-opacity",
                                      isSelected && "opacity-60"
                                    )}
                                    loading="lazy"
                                  />
                                ) : null}
                                <div className={cn(
                                  "px-2 py-1 text-[10px] border-t flex items-center justify-between",
                                  isSelected
                                    ? "text-accent border-accent/30 bg-accent/5"
                                    : "text-muted border-border"
                                )}>
                                  <span>{img.filename || `Image ${img.index + 1}`}</span>
                                  {isSelected && (
                                    <span className="font-medium">Regenerate</span>
                                  )}
                                </div>
                              </div>
                            );
                          })}
                        </div>

                        {/* Regeneration controls - visible when images are selected */}
                        {isPending && selectedForRegen.size > 0 && (
                          <div className="mt-3 space-y-2 rounded-lg border border-accent/30 bg-accent/5 p-3">
                            <div className="flex items-center gap-2 text-sm font-medium text-accent">
                              <RefreshCw className="h-4 w-4" />
                              Regenerate {selectedForRegen.size} image{selectedForRegen.size > 1 ? "s" : ""}
                            </div>
                            <div className="flex items-start gap-2">
                              <MessageSquare className="h-4 w-4 text-muted mt-2 shrink-0" />
                              <textarea
                                value={regenFeedback[item.id] || ""}
                                onChange={(e) =>
                                  setRegenFeedback((prev) => ({ ...prev, [item.id]: e.target.value }))
                                }
                                placeholder="Feedback for regeneration (e.g. 'too dark, wrong angle, product label not visible')"
                                className="flex-1 rounded-md border border-border bg-surface px-3 py-2 text-sm text-foreground placeholder:text-muted resize-none focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
                                rows={2}
                              />
                            </div>
                            <div className="flex items-center justify-between">
                              <button
                                onClick={() => {
                                  setRejectedShots((prev) => {
                                    const next = { ...prev };
                                    delete next[item.id];
                                    return next;
                                  });
                                  setRegenFeedback((prev) => {
                                    const next = { ...prev };
                                    delete next[item.id];
                                    return next;
                                  });
                                }}
                                className="text-xs text-muted hover:text-foreground transition-colors"
                              >
                                Clear selection
                              </button>
                              <button
                                disabled={actionLoading.has(item.id)}
                                onClick={() => handleRegenerate(item.id)}
                                className={cn(
                                  "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium",
                                  "bg-accent text-white hover:bg-accent/90 transition-colors",
                                  "disabled:opacity-50 disabled:cursor-not-allowed"
                                )}
                              >
                                <RefreshCw className={cn("h-3.5 w-3.5", actionLoading.has(item.id) && "animate-spin")} />
                                Regenerate selected ({selectedForRegen.size})
                              </button>
                            </div>
                          </div>
                        )}

                        {/* Hint text when no images are selected */}
                        {isPending && hasImages && selectedForRegen.size === 0 && (
                          <p className="mt-2 text-xs text-muted">
                            Click on images you want to regenerate, or use the buttons above to approve/reject all.
                          </p>
                        )}
                      </div>
                    )}
                    <p className="text-xs font-medium text-muted-foreground mb-2">Request Data</p>
                    <pre className="max-h-48 overflow-auto rounded-lg bg-surface border border-border p-3 font-mono text-xs text-muted">
                      {JSON.stringify(item.request_data, null, 2)}
                    </pre>
                    {item.allow_edit && item.status === "pending" && (
                      <p className="mt-2 text-xs text-accent">Editing allowed - reviewer can modify data before approving</p>
                    )}
                    {item.reviewer_comment && (
                      <div className="mt-3">
                        <p className="text-xs font-medium text-muted-foreground mb-1">Reviewer Comment</p>
                        <p className="text-sm text-foreground">{item.reviewer_comment}</p>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
