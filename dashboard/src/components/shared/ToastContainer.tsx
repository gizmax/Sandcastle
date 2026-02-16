import { useCallback, useEffect, useState } from "react";
import { CheckCircle, XCircle, AlertTriangle, X } from "lucide-react";
import { cn } from "@/lib/utils";

export interface Toast {
  id: string;
  type: "success" | "error" | "warning";
  message: string;
}

interface ToastContainerProps {
  toasts: Toast[];
  onDismiss: (id: string) => void;
}

const TOAST_DURATION_MS = 5000;

const typeConfig = {
  success: { icon: CheckCircle, color: "text-success", border: "border-success/30" },
  error: { icon: XCircle, color: "text-error", border: "border-error/30" },
  warning: { icon: AlertTriangle, color: "text-warning", border: "border-warning/30" },
};

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: (id: string) => void }) {
  useEffect(() => {
    const timer = setTimeout(() => onDismiss(toast.id), TOAST_DURATION_MS);
    return () => clearTimeout(timer);
  }, [toast.id, onDismiss]);

  const config = typeConfig[toast.type];
  const Icon = config.icon;

  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-lg border bg-surface px-4 py-3 shadow-lg",
        "pointer-events-auto w-80 max-w-[calc(100vw-2rem)]",
        config.border
      )}
      style={{ animation: "fadeIn 0.2s ease-out" }}
    >
      <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", config.color)} />
      <p className="flex-1 text-sm text-foreground">{toast.message}</p>
      <button
        onClick={() => onDismiss(toast.id)}
        className="shrink-0 text-muted hover:text-foreground transition-colors"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

/**
 * Fixed-position toast container that renders at bottom-right of the viewport.
 */
export function ToastContainer({ toasts, onDismiss }: ToastContainerProps) {
  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 pointer-events-none">
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

// Toast state management hook
let toastIdCounter = 1;

export function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const addToast = useCallback((type: Toast["type"], message: string) => {
    const id = `toast-${toastIdCounter++}`;
    setToasts((prev) => {
      // Keep max 5 visible toasts
      const updated = [...prev, { id, type, message }];
      if (updated.length > 5) return updated.slice(-5);
      return updated;
    });
  }, []);

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return { toasts, addToast, dismissToast };
}
