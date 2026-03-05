import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

interface KeyboardShortcutsOptions {
  onCommandPalette?: () => void;
}

export function useKeyboardShortcuts(options?: KeyboardShortcutsOptions) {
  const navigate = useNavigate();

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      // Allow Cmd+K even when focused on an input (it opens the palette)
      if (
        (e.metaKey || e.ctrlKey) &&
        (e.key === "k" || e.key === "K")
      ) {
        e.preventDefault();
        options?.onCommandPalette?.();
        return;
      }

      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        e.target instanceof HTMLSelectElement
      ) {
        return;
      }

      if (e.metaKey || e.ctrlKey) {
        switch (e.key) {
          case "1":
            e.preventDefault();
            navigate("/");
            break;
          case "2":
            e.preventDefault();
            navigate("/runs");
            break;
          case "3":
            e.preventDefault();
            navigate("/workflows");
            break;
          case "4":
            e.preventDefault();
            navigate("/schedules");
            break;
        }
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [navigate, options]);
}
