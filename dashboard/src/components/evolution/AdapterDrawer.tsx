import { useEffect } from "react";
import { ArrowUpRight, Database, Layers, Radio, X } from "lucide-react";
import { CopyButton } from "@/components/shared/CopyButton";
import { cn, formatRelativeTime } from "@/lib/utils";
import type { AdapterInfo } from "@/pages/NightShiftPage";

interface AdapterDrawerProps {
  adapter: AdapterInfo;
  parent: AdapterInfo | null;
  onSelectParent: (adapter: AdapterInfo) => void;
  onClose: () => void;
}

const LORA_LABELS: Record<string, string> = {
  r: "LoRA rank (r)",
  alpha: "Alpha",
  lr: "Learning rate",
  epochs: "Epochs",
};

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2 border-b border-border/60 last:border-b-0">
      <span className="text-xs text-muted shrink-0">{label}</span>
      <span className="text-xs text-foreground text-right min-w-0">{children}</span>
    </div>
  );
}

/** Right-side detail drawer for a single trained adapter. */
export function AdapterDrawer({ adapter, parent, onSelectParent, onClose }: AdapterDrawerProps) {
  // Close on Escape
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const modelString = `adapter/${adapter.adapter_id}`;
  const score = adapter.metrics?.eval_score;
  const loss = adapter.metrics?.loss;
  const createdAt = adapter.created_at > 0 ? new Date(adapter.created_at * 1000) : null;

  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-label="Adapter details">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Panel */}
      <aside className="absolute inset-y-0 right-0 w-full max-w-md bg-surface border-l border-border shadow-2xl overflow-y-auto">
        <div className="flex items-center justify-between border-b border-border px-5 py-4 sticky top-0 bg-surface z-10">
          <div className="flex items-center gap-2 min-w-0">
            <div className={cn(
              "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg",
              adapter.served ? "bg-accent/15 border border-accent/30" : "bg-border/30"
            )}>
              <Layers className={cn("h-4 w-4", adapter.served ? "text-accent" : "text-muted")} />
            </div>
            <h2 className="font-mono text-sm font-semibold text-foreground truncate">
              {adapter.adapter_id}
            </h2>
            {adapter.served && (
              <span className="inline-flex items-center gap-1 rounded-full bg-accent/15 border border-accent/30 px-2 py-0.5 text-[10px] font-semibold text-accent shrink-0">
                <Radio className="h-2.5 w-2.5 animate-pulse" />
                SERVING
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            aria-label="Close adapter details"
            className="rounded-md p-1 text-muted hover:text-foreground hover:bg-border/40 transition-colors shrink-0"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-5 space-y-5">
          {/* Model string - the thing you paste into a workflow */}
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1.5">Model string</p>
            <div className="flex items-center justify-between gap-2 rounded-lg border border-accent/30 bg-accent/5 px-3 py-2.5">
              <code className="font-mono text-xs text-accent truncate">{modelString}</code>
              <CopyButton value={modelString} label="model string" />
            </div>
            <p className="mt-1 text-[10px] text-muted">
              Use this as a step model to route a workflow to this adapter.
            </p>
          </div>

          {/* Eval metrics */}
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-lg border border-success/30 bg-success/5 p-3">
              <p className="text-[10px] font-medium text-muted-foreground">Eval score</p>
              <p className="mt-0.5 text-2xl font-bold text-success font-data">
                {score != null ? score.toFixed(2) : "-"}
              </p>
            </div>
            <div className="rounded-lg border border-border bg-background/40 p-3">
              <p className="text-[10px] font-medium text-muted-foreground">Training loss</p>
              <p className="mt-0.5 text-2xl font-bold text-foreground font-data">
                {loss != null ? loss.toFixed(3) : "-"}
              </p>
            </div>
          </div>

          {/* Provenance */}
          <div className="rounded-lg border border-border bg-background/40 px-4 py-1">
            <DetailRow label="Base model">
              <span className="font-mono">{adapter.base_model || "-"}</span>
            </DetailRow>
            <DetailRow label="Training samples">
              <span className="font-data">{adapter.samples}</span>
            </DetailRow>
            <DetailRow label="Dataset hash">
              {adapter.dataset_hash ? (
                <span className="inline-flex items-center gap-1">
                  <span className="font-mono" title={adapter.dataset_hash}>
                    {adapter.dataset_hash.slice(0, 12)}…
                  </span>
                  <CopyButton value={adapter.dataset_hash} label="dataset hash" />
                </span>
              ) : (
                <span className="text-muted">-</span>
              )}
            </DetailRow>
            <DetailRow label="Trained">
              {createdAt ? (
                <span title={createdAt.toLocaleString()}>
                  {formatRelativeTime(createdAt)}
                </span>
              ) : (
                <span className="text-muted">-</span>
              )}
            </DetailRow>
          </div>

          {/* Hyperparameters */}
          {Object.keys(adapter.lora_config ?? {}).length > 0 && (
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1.5">Hyperparameters</p>
              <div className="grid grid-cols-2 gap-2">
                {Object.entries(adapter.lora_config).map(([key, value]) => (
                  <div key={key} className="rounded-lg border border-border bg-background/40 px-3 py-2">
                    <p className="text-[10px] text-muted">{LORA_LABELS[key] ?? key}</p>
                    <p className="text-sm font-semibold text-foreground font-data">{String(value)}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Lineage */}
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1.5">Lineage</p>
            {parent ? (
              <button
                onClick={() => onSelectParent(parent)}
                className={cn(
                  "flex w-full items-center justify-between gap-2 rounded-lg border border-border px-3 py-2.5",
                  "text-left hover:border-accent/40 hover:bg-border/10 transition-colors"
                )}
              >
                <span className="min-w-0">
                  <span className="block text-[10px] text-muted">Parent adapter</span>
                  <span className="block font-mono text-xs text-foreground truncate">
                    {parent.adapter_id}
                  </span>
                </span>
                <ArrowUpRight className="h-3.5 w-3.5 text-muted shrink-0" />
              </button>
            ) : (
              <div className="flex items-center gap-2 rounded-lg border border-dashed border-border px-3 py-2.5">
                <Database className="h-3.5 w-3.5 text-muted shrink-0" />
                <span className="text-xs text-muted">
                  First generation - trained directly from <span className="font-mono">{adapter.base_model || "the base model"}</span>
                </span>
              </div>
            )}
          </div>
        </div>
      </aside>
    </div>
  );
}
