import { useState } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

interface CreateApiKeyModalProps {
  open: boolean;
  onClose: () => void;
  onSubmit: (data: { name: string; tenant_id: string }) => void;
}

export function CreateApiKeyModal({ open, onClose, onSubmit }: CreateApiKeyModalProps) {
  const [name, setName] = useState("");
  const [tenantId, setTenantId] = useState("");

  if (!open) return null;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    onSubmit({ name, tenant_id: tenantId });
    setName("");
    setTenantId("");
  }

  const inputClass = cn(
    "h-9 w-full rounded-lg border border-border bg-background px-3 text-sm",
    "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30"
  );

  return (
    <>
      <div className="fixed inset-0 z-50 bg-black/40" onClick={onClose} />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div className="w-full max-w-md rounded-xl border border-border bg-surface p-6 shadow-xl">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-foreground">Create API Key</h2>
            <button
              onClick={onClose}
              className="rounded-lg p-1 text-muted hover:text-foreground"
            >
              <X className="h-5 w-5" />
            </button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="mb-1 block text-xs font-medium text-muted">Key Name</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Production API"
                required
                className={inputClass}
              />
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-muted">Tenant ID</label>
              <input
                type="text"
                value={tenantId}
                onChange={(e) => setTenantId(e.target.value)}
                placeholder="e.g. acme-corp"
                required
                className={inputClass}
              />
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
                className={cn(
                  "rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground",
                  "hover:bg-accent-hover transition-all duration-200",
                  "shadow-sm hover:shadow-md"
                )}
              >
                Create Key
              </button>
            </div>
          </form>
        </div>
      </div>
    </>
  );
}
