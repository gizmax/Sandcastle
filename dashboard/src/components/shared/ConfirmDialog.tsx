import { useEffect, useId, useRef } from "react";
import { AlertTriangle, X } from "lucide-react";
import { cn } from "@/lib/utils";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel?: string;
  variant?: "danger" | "warning";
  confirmDisabled?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "Confirm",
  variant = "danger",
  confirmDisabled = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const cancelBtnRef = useRef<HTMLButtonElement>(null);
  const dialogId = useId();
  const titleId = `${dialogId}-title`;
  const descId = `${dialogId}-desc`;

  useEffect(() => {
    if (!open) return;
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    window.addEventListener("keydown", handleKeyDown);
    cancelBtnRef.current?.focus();
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <>
      <div className="fixed inset-0 z-50 bg-black/40" onClick={onCancel} />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descId} className="w-full max-w-sm rounded-xl border border-border bg-surface p-6 shadow-xl">
          <div className="mb-4 flex items-start gap-3">
            <div
              className={cn(
                "flex h-9 w-9 shrink-0 items-center justify-center rounded-full",
                variant === "danger" ? "bg-error/10" : "bg-warning/10"
              )}
            >
              <AlertTriangle
                aria-hidden="true"
                className={cn(
                  "h-5 w-5",
                  variant === "danger" ? "text-error" : "text-warning"
                )}
              />
            </div>
            <div className="flex-1">
              <h3 id={titleId} className="text-sm font-semibold text-foreground">{title}</h3>
              <p id={descId} className="mt-1 text-sm text-muted">{description}</p>
            </div>
            <button
              onClick={onCancel}
              aria-label="Close dialog"
              className="rounded-lg p-1 text-muted hover:text-foreground transition-colors"
            >
              <X aria-hidden="true" className="h-4 w-4" />
            </button>
          </div>
          <div className="flex justify-end gap-2">
            <button
              ref={cancelBtnRef}
              onClick={onCancel}
              className="rounded-lg border border-border px-3 py-1.5 text-sm font-medium text-muted hover:text-foreground transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={onConfirm}
              disabled={confirmDisabled}
              className={cn(
                "rounded-lg px-3 py-1.5 text-sm font-medium text-white transition-colors",
                variant === "danger"
                  ? "bg-error hover:bg-error/90"
                  : "bg-warning hover:bg-warning/90",
                "disabled:opacity-50 disabled:cursor-not-allowed"
              )}
            >
              {confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
