import { useEffect, useState } from "react";
import { X, Loader2 } from "lucide-react";
import { cn, inputClass as sharedInputClass } from "@/lib/utils";

interface CreateApiKeyModalProps {
  open: boolean;
  loading?: boolean;
  onClose: () => void;
  onSubmit: (data: { name: string; tenant_id: string; max_cost_per_run_usd?: number }) => void;
}

export function CreateApiKeyModal({ open, loading, onClose, onSubmit }: CreateApiKeyModalProps) {
  const [name, setName] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [maxCost, setMaxCost] = useState("");

  // Reset form fields when modal opens
  useEffect(() => {
    if (open) {
      setName("");
      setTenantId("");
      setMaxCost("");
    }
  }, [open]);

  // Close on Escape key
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName) return;
    const parsed = maxCost !== "" ? parseFloat(maxCost) : undefined;
    // Guard against NaN from invalid number input (e.g. pasted text)
    const safeCost = parsed !== undefined && Number.isFinite(parsed) && parsed >= 0
      ? parsed
      : undefined;
    onSubmit({
      name: trimmedName,
      tenant_id: tenantId.trim(),
      ...(safeCost !== undefined ? { max_cost_per_run_usd: safeCost } : {}),
    });
  }

  const inputClass = sharedInputClass;

  return (
    <>
      <div className="fixed inset-0 z-50 bg-black/40" onClick={onClose} />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div role="dialog" aria-modal="true" aria-labelledby="create-key-title" className="w-full max-w-md rounded-xl border border-border bg-surface p-6 shadow-xl">
          <div className="mb-4 flex items-center justify-between">
            <h2 id="create-key-title" className="text-lg font-semibold text-foreground">Create API Key</h2>
            <button
              onClick={onClose}
              aria-label="Close dialog"
              className="rounded-lg p-1 text-muted hover:text-foreground"
            >
              <X aria-hidden="true" className="h-5 w-5" />
            </button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label htmlFor="key-name" className="mb-1 block text-xs font-medium text-muted">Key Name</label>
              <input
                id="key-name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Production API"
                required
                className={inputClass}
              />
              <p className="text-[11px] text-muted-foreground mt-0.5">Label for identifying this key in the dashboard.</p>
            </div>

            <div>
              <label htmlFor="key-tenant" className="mb-1 block text-xs font-medium text-muted">Tenant ID</label>
              <input
                id="key-tenant"
                type="text"
                value={tenantId}
                onChange={(e) => setTenantId(e.target.value)}
                placeholder="e.g. acme-corp"
                required
                className={inputClass}
              />
              <p className="text-[11px] text-muted-foreground mt-0.5">All runs created with this key are scoped to this tenant.</p>
            </div>

            <div>
              <label htmlFor="key-max-cost" className="mb-1 block text-xs font-medium text-muted">Max Cost per Run (USD)</label>
              <input
                id="key-max-cost"
                type="number"
                value={maxCost}
                onChange={(e) => setMaxCost(e.target.value)}
                placeholder="e.g. 5.00"
                min="0"
                step="0.01"
                className={inputClass}
              />
              <p className="text-[11px] text-muted-foreground mt-0.5">Budget limit per run for this API key. Leave empty for unlimited.</p>
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={onClose}
                className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-muted hover:text-foreground transition-colors"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={loading}
                className={cn(
                  "flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground",
                  "hover:bg-accent-hover transition-settle",
                  "shadow-sm hover:shadow-md",
                  "disabled:opacity-50 disabled:cursor-not-allowed"
                )}
              >
                {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                {loading ? "Creating..." : "Create Key"}
              </button>
            </div>
          </form>
        </div>
      </div>
    </>
  );
}
