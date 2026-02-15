import { useState } from "react";
import { AlertTriangle, Check, Copy, X } from "lucide-react";
import { cn } from "@/lib/utils";

interface KeyRevealModalProps {
  apiKey: string;
  onClose: () => void;
}

export function KeyRevealModal({ apiKey, onClose }: KeyRevealModalProps) {
  const [copied, setCopied] = useState(false);

  function handleCopy() {
    navigator.clipboard.writeText(apiKey);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <>
      <div className="fixed inset-0 z-50 bg-black/40" onClick={onClose} />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div className="w-full max-w-lg rounded-xl border border-border bg-surface p-6 shadow-xl">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-foreground">API Key Created</h2>
            <button
              onClick={onClose}
              className="rounded-lg p-1 text-muted hover:text-foreground"
            >
              <X className="h-5 w-5" />
            </button>
          </div>

          <div className="mb-4 flex items-start gap-3 rounded-lg border border-warning/30 bg-warning/5 px-4 py-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
            <p className="text-sm text-warning">
              Save this key now. It will not be shown again.
            </p>
          </div>

          <div className="mb-4 flex items-center gap-2 rounded-lg border border-border bg-background px-4 py-3">
            <code className="flex-1 break-all font-mono text-sm text-foreground">
              {apiKey}
            </code>
            <button
              onClick={handleCopy}
              className={cn(
                "shrink-0 rounded-lg border border-border p-2 transition-colors",
                copied
                  ? "border-success/30 bg-success/10 text-success"
                  : "text-muted hover:text-foreground hover:bg-background"
              )}
            >
              {copied ? (
                <Check className="h-4 w-4" />
              ) : (
                <Copy className="h-4 w-4" />
              )}
            </button>
          </div>

          <div className="flex justify-end">
            <button
              onClick={onClose}
              className={cn(
                "rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground",
                "hover:bg-accent-hover transition-all duration-200",
                "shadow-sm hover:shadow-md"
              )}
            >
              Done
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
