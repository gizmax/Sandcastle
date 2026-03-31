import { useState, useEffect, useCallback, useRef } from "react";
import { X, Layers, Loader2, CheckCircle, XCircle, AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";

interface BatchRunModalProps {
  open: boolean;
  workflowName: string;
  fileName: string;
  onClose: () => void;
}

interface BatchItemStatus {
  index: number;
  status: string;
  run_id: string | null;
  cost_usd: number;
  error: string | null;
}

interface BatchStatus {
  batch_id: string;
  workflow: string;
  status: string;
  total: number;
  completed: number;
  failed: number;
  running: number;
  pending: number;
  total_cost_usd: number;
  items: BatchItemStatus[];
}

const EXAMPLE_ITEMS = JSON.stringify(
  [
    { text: "First item to process" },
    { text: "Second item to process" },
    { text: "Third item to process" },
  ],
  null,
  2
);

export function BatchRunModal({ open, workflowName, fileName, onClose }: BatchRunModalProps) {
  const [itemsJson, setItemsJson] = useState(EXAMPLE_ITEMS);
  const [maxParallel, setMaxParallel] = useState(5);
  const [maxCostPerItem, setMaxCostPerItem] = useState("");
  const [batchId, setBatchId] = useState<string | null>(null);
  const [batchStatus, setBatchStatus] = useState<BatchStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Close on Escape key
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  // Reset state when modal opens
  useEffect(() => {
    if (open) {
      setItemsJson(EXAMPLE_ITEMS);
      setMaxParallel(5);
      setMaxCostPerItem("");
      setBatchId(null);
      setBatchStatus(null);
      setStarting(false);
      setCancelling(false);
    }
  }, [open]);

  // Poll batch status when batchId is set
  const pollStatus = useCallback(async () => {
    if (!batchId) return;
    try {
      const res = await api.get<BatchStatus>(`/batch/${batchId}/status`);
      if (res.data) {
        setBatchStatus(res.data);
        // Stop polling when batch is done
        if (res.data.status !== "running") {
          if (pollRef.current) {
            clearInterval(pollRef.current);
            pollRef.current = null;
          }
        }
      }
    } catch {
      // Network error during poll - will retry on next interval
    }
  }, [batchId]);

  useEffect(() => {
    if (!batchId) return;
    // Initial fetch
    void pollStatus();
    // Poll every 2 seconds
    pollRef.current = setInterval(() => void pollStatus(), 2000);
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [batchId, pollStatus]);

  // Cleanup on unmount / close
  useEffect(() => {
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, []);

  async function handleStart() {
    // Parse and validate items JSON
    let items: unknown[];
    try {
      const parsed = JSON.parse(itemsJson);
      if (!Array.isArray(parsed) || parsed.length === 0) {
        toast.error("Items must be a non-empty JSON array");
        return;
      }
      items = parsed;
    } catch {
      toast.error("Invalid JSON - please provide a valid array");
      return;
    }

    setStarting(true);
    try {
      const wfName = fileName.replace(".yaml", "");
      const body: Record<string, unknown> = {
        items,
        max_parallel: maxParallel,
      };
      if (maxCostPerItem && parseFloat(maxCostPerItem) > 0) {
        body.max_cost_per_item_usd = parseFloat(maxCostPerItem);
      }

      const res = await api.post<{ batch_id: string }>(`/workflows/${wfName}/batch`, body);

      if (res.error) {
        toast.error(`Batch failed: ${res.error.message}`);
        return;
      }

      if (res.data?.batch_id) {
        setBatchId(res.data.batch_id);
        toast.success("Batch started");
      }
    } catch {
      toast.error("Network error - could not start batch");
    } finally {
      setStarting(false);
    }
  }

  async function handleCancel() {
    if (!batchId || cancelling) return;
    setCancelling(true);
    try {
      const res = await api.post<{ status: string; completed: number; cancelled: number }>(
        `/batch/${batchId}/cancel`
      );
      if (res.error) {
        toast.error(`Cancel failed: ${res.error.message}`);
      } else if (res.data) {
        toast.success(
          `Batch cancelled: ${res.data.completed} completed, ${res.data.cancelled} cancelled`
        );
        // Refresh status immediately after cancel
        void pollStatus();
      }
    } catch {
      toast.error("Network error - could not cancel batch");
    } finally {
      setCancelling(false);
    }
  }

  if (!open) return null;

  const isRunning = batchStatus?.status === "running";
  const isDone = batchStatus && !isRunning;
  // Guard against division by zero when total is 0
  const progressPct = batchStatus && batchStatus.total > 0
    ? Math.round(((batchStatus.completed + batchStatus.failed) / batchStatus.total) * 100)
    : 0;

  return (
    <>
      <div className="fixed inset-0 z-50 bg-black/40" onClick={onClose} />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`Batch run ${workflowName}`}
          className="w-full max-w-lg max-h-[85vh] flex flex-col rounded-xl border border-border bg-surface shadow-xl"
        >
          {/* Header */}
          <div className="shrink-0 px-6 pt-6 pb-4 border-b border-border">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Layers className="h-5 w-5 text-accent" />
                <h2 className="text-lg font-semibold text-foreground">
                  Batch Run - {workflowName}
                </h2>
              </div>
              <button onClick={onClose} className="rounded-lg p-1 text-muted hover:text-foreground">
                <X className="h-5 w-5" />
              </button>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
            {!batchId ? (
              <>
                {/* Items input */}
                <div>
                  <label className="mb-1 block text-xs font-medium text-muted">
                    Items (JSON array)
                  </label>
                  <p className="mb-1.5 text-xs text-muted-foreground">
                    Each object in the array will be passed as input to a separate workflow run.
                  </p>
                  <textarea
                    value={itemsJson}
                    onChange={(e) => setItemsJson(e.target.value)}
                    rows={8}
                    spellCheck={false}
                    className={cn(
                      "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm font-mono resize-y",
                      "focus-visible:border-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
                    )}
                  />
                </div>

                {/* Max parallel slider */}
                <div>
                  <label className="mb-1 flex items-center justify-between text-xs font-medium text-muted">
                    <span>Max parallel</span>
                    <span className="font-mono text-foreground">{maxParallel}</span>
                  </label>
                  <input
                    type="range"
                    min={1}
                    max={20}
                    value={maxParallel}
                    onChange={(e) => setMaxParallel(Number(e.target.value))}
                    className="w-full accent-accent"
                  />
                  <div className="flex justify-between text-[10px] text-muted-foreground">
                    <span>1</span>
                    <span>20</span>
                  </div>
                </div>

                {/* Max cost per item */}
                <div>
                  <label className="mb-1 block text-xs font-medium text-muted">
                    Max cost per item (USD, optional)
                  </label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    value={maxCostPerItem}
                    onChange={(e) => setMaxCostPerItem(e.target.value)}
                    placeholder="0.50"
                    className={cn(
                      "h-9 w-full rounded-lg border border-border bg-background px-3 text-sm",
                      "focus-visible:border-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
                    )}
                  />
                </div>
              </>
            ) : (
              <>
                {/* Progress view */}
                <div className="space-y-3">
                  {/* Status badge */}
                  <div className="flex items-center gap-2">
                    {batchStatus?.status === "running" && (
                      <Loader2 className="h-4 w-4 animate-spin text-accent" />
                    )}
                    {batchStatus?.status === "completed" && (
                      <CheckCircle className="h-4 w-4 text-success" />
                    )}
                    {batchStatus?.status === "partial_failure" && (
                      <AlertTriangle className="h-4 w-4 text-warning" />
                    )}
                    {batchStatus?.status === "failed" && (
                      <XCircle className="h-4 w-4 text-error" />
                    )}
                    {batchStatus?.status === "cancelled" && (
                      <XCircle className="h-4 w-4 text-warning" />
                    )}
                    <span className="text-sm font-medium text-foreground capitalize">
                      {batchStatus?.status || "Starting..."}
                    </span>
                  </div>

                  {/* Progress bar */}
                  {batchStatus && (
                    <div>
                      <div className="flex items-center justify-between text-xs text-muted mb-1">
                        <span>
                          {batchStatus.completed + batchStatus.failed} / {batchStatus.total}
                        </span>
                        <span>{progressPct}%</span>
                      </div>
                      <div className="h-2.5 w-full rounded-full bg-border overflow-hidden">
                        <div className="h-full flex">
                          {batchStatus.completed > 0 && batchStatus.total > 0 && (
                            <div
                              className="bg-success transition-all duration-500"
                              style={{
                                width: `${(batchStatus.completed / batchStatus.total) * 100}%`,
                              }}
                            />
                          )}
                          {batchStatus.failed > 0 && batchStatus.total > 0 && (
                            <div
                              className="bg-error transition-all duration-500"
                              style={{
                                width: `${(batchStatus.failed / batchStatus.total) * 100}%`,
                              }}
                            />
                          )}
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Stats row */}
                  {batchStatus && (
                    <div className="grid grid-cols-4 gap-2 text-center">
                      <div className="rounded-lg border border-border bg-background p-2">
                        <div className="text-lg font-semibold text-foreground font-data">
                          {batchStatus.completed}
                        </div>
                        <div className="text-[10px] text-success font-medium">Passed</div>
                      </div>
                      <div className="rounded-lg border border-border bg-background p-2">
                        <div className="text-lg font-semibold text-foreground font-data">
                          {batchStatus.failed}
                        </div>
                        <div className="text-[10px] text-error font-medium">Failed</div>
                      </div>
                      <div className="rounded-lg border border-border bg-background p-2">
                        <div className="text-lg font-semibold text-foreground font-data">
                          {batchStatus.running}
                        </div>
                        <div className="text-[10px] text-accent font-medium">Running</div>
                      </div>
                      <div className="rounded-lg border border-border bg-background p-2">
                        <div className="text-lg font-semibold text-foreground font-data">
                          ${(batchStatus.total_cost_usd ?? 0).toFixed(2)}
                        </div>
                        <div className="text-[10px] text-muted font-medium">Cost</div>
                      </div>
                    </div>
                  )}

                  {/* Item list (scrollable) */}
                  {batchStatus && batchStatus.items.length > 0 && (
                    <div>
                      <h4 className="text-xs font-medium text-muted mb-2">Items</h4>
                      <div className="space-y-1 max-h-48 overflow-y-auto">
                        {batchStatus.items.map((item) => (
                          <div
                            key={item.index}
                            className="flex items-center justify-between rounded-md border border-border bg-background px-3 py-1.5 text-xs"
                          >
                            <div className="flex items-center gap-2">
                              {item.status === "completed" && (
                                <CheckCircle className="h-3 w-3 text-success" />
                              )}
                              {item.status === "failed" && (
                                <XCircle className="h-3 w-3 text-error" />
                              )}
                              {item.status === "running" && (
                                <Loader2 className="h-3 w-3 animate-spin text-accent" />
                              )}
                              {item.status === "pending" && (
                                <span className="h-3 w-3 rounded-full border border-muted" />
                              )}
                              {item.status === "cancelled" && (
                                <XCircle className="h-3 w-3 text-muted" />
                              )}
                              <span className="font-mono text-muted-foreground">#{item.index}</span>
                              {item.error && (
                                <span className="text-error truncate max-w-[200px]" title={item.error}>
                                  {item.error}
                                </span>
                              )}
                            </div>
                            <span className="font-mono text-muted">
                              ${(item.cost_usd ?? 0).toFixed(3)}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Completion summary */}
                  {isDone && (
                    <div
                      className={cn(
                        "rounded-lg border p-3 text-sm",
                        batchStatus?.status === "completed"
                          ? "border-success/30 bg-success/5 text-success"
                          : "border-warning/30 bg-warning/5 text-warning"
                      )}
                    >
                      {batchStatus?.status === "completed"
                        ? `All ${batchStatus.total} items completed successfully. Total cost: $${(batchStatus.total_cost_usd ?? 0).toFixed(2)}`
                        : batchStatus?.status === "cancelled"
                          ? `Batch cancelled: ${batchStatus?.completed ?? 0} completed, ${batchStatus.total - (batchStatus?.completed ?? 0) - (batchStatus?.failed ?? 0)} cancelled. Total cost: $${(batchStatus?.total_cost_usd ?? 0).toFixed(2)}`
                          : `Batch finished: ${batchStatus?.completed ?? 0} passed, ${batchStatus?.failed ?? 0} failed. Total cost: $${(batchStatus?.total_cost_usd ?? 0).toFixed(2)}`}
                    </div>
                  )}
                </div>
              </>
            )}
          </div>

          {/* Footer */}
          <div className="shrink-0 flex justify-end gap-2 px-6 py-4 border-t border-border">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-muted hover:text-foreground transition-colors"
            >
              {isDone ? "Close" : "Dismiss"}
            </button>
            {batchId && isRunning && (
              <button
                type="button"
                onClick={handleCancel}
                disabled={cancelling}
                className={cn(
                  "flex items-center gap-2 rounded-lg border border-error/30 px-4 py-2 text-sm font-medium text-error",
                  "hover:bg-error/10 transition-all duration-200",
                  "disabled:opacity-50 disabled:cursor-not-allowed"
                )}
              >
                {cancelling ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <XCircle className="h-3.5 w-3.5" />
                )}
                {cancelling ? "Cancelling..." : "Cancel Batch"}
              </button>
            )}
            {!batchId && (
              <button
                type="button"
                onClick={handleStart}
                disabled={starting}
                className={cn(
                  "flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground",
                  "hover:bg-accent-hover transition-all duration-200 shadow-sm hover:shadow-md",
                  "disabled:opacity-50 disabled:cursor-not-allowed"
                )}
              >
                {starting ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Layers className="h-3.5 w-3.5" />
                )}
                Start Batch
              </button>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
