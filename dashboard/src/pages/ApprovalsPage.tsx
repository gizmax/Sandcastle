import { useCallback, useEffect, useState } from "react";
import { ShieldCheck, Clock, CheckCircle2, XCircle, SkipForward } from "lucide-react";
import { api } from "@/api/client";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { formatRelativeTime, cn } from "@/lib/utils";
import { useNavigate } from "react-router-dom";

interface ApprovalItem {
  id: string;
  run_id: string;
  step_id: string;
  status: string;
  message: string;
  request_data: Record<string, unknown> | null;
  reviewer_comment: string | null;
  timeout_at: string | null;
  on_timeout: string;
  allow_edit: boolean;
  created_at: string | null;
  resolved_at: string | null;
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
  const [filter, setFilter] = useState<string>("all");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const fetchItems = useCallback(async () => {
    const params: Record<string, string> = {};
    if (filter !== "all") params.status = filter;
    const res = await api.get<ApprovalItem[]>("/approvals", params);
    if (res.data) setItems(res.data);
    setLoading(false);
  }, [filter]);

  useEffect(() => {
    void fetchItems();
  }, [fetchItems]);

  const handleAction = useCallback(
    async (id: string, action: "approve" | "reject" | "skip") => {
      await api.post(`/approvals/${id}/${action}`);
      void fetchItems();
    },
    [fetchItems]
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

  return (
    <div className="space-y-4 sm:space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">Approval Gates</h1>
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

            return (
              <div
                key={item.id}
                className="rounded-xl border border-border bg-surface shadow-sm overflow-hidden"
              >
                <div
                  className="flex items-center gap-3 sm:gap-4 px-3 sm:px-5 py-3 sm:py-4 cursor-pointer hover:bg-border/10 transition-colors"
                  onClick={() => setExpandedId(isExpanded ? null : item.id)}
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
                  {item.status === "pending" && (
                    <div className="flex items-center gap-1.5 shrink-0">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleAction(item.id, "approve");
                        }}
                        className={cn(
                          "flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium",
                          "bg-success/10 text-success hover:bg-success/20 transition-colors"
                        )}
                      >
                        <CheckCircle2 className="h-3.5 w-3.5" />
                        Approve
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleAction(item.id, "reject");
                        }}
                        className={cn(
                          "flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium",
                          "bg-error/10 text-error hover:bg-error/20 transition-colors"
                        )}
                      >
                        <XCircle className="h-3.5 w-3.5" />
                        Reject
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleAction(item.id, "skip");
                        }}
                        className={cn(
                          "flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium",
                          "text-muted hover:bg-border/40 transition-colors"
                        )}
                      >
                        <SkipForward className="h-3.5 w-3.5" />
                        Skip
                      </button>
                    </div>
                  )}
                </div>

                {/* Expanded detail */}
                {isExpanded && item.request_data && (
                  <div className="border-t border-border px-5 py-4 bg-background/50">
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
