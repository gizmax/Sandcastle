import { useCallback, useState } from "react";
import type { Toast } from "@/components/shared/ToastContainer";

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
