import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { Keyboard, Search, X } from "lucide-react";

interface Shortcut {
  keys: string[];
  description: string;
}

interface ShortcutGroup {
  label: string;
  shortcuts: Shortcut[];
}

const SHORTCUT_GROUPS: ShortcutGroup[] = [
  {
    label: "Navigation",
    shortcuts: [
      { keys: ["\u2318", "1"], description: "Go to Overview" },
      { keys: ["\u2318", "2"], description: "Go to Runs" },
      { keys: ["\u2318", "3"], description: "Go to Workflows" },
      { keys: ["\u2318", "4"], description: "Go to Schedules" },
    ],
  },
  {
    label: "Global",
    shortcuts: [
      { keys: ["\u2318", "K"], description: "Open command palette" },
      { keys: ["?"], description: "Show keyboard shortcuts" },
      { keys: ["Esc"], description: "Close modal or panel" },
    ],
  },
  {
    label: "Run Detail",
    shortcuts: [
      { keys: ["\u2318", "E"], description: "Expand all steps" },
      { keys: ["\u2318", "\u21E7", "E"], description: "Collapse all steps" },
      { keys: ["\u2191", "\u2193"], description: "Navigate between steps" },
    ],
  },
  {
    label: "Bulk Actions",
    shortcuts: [
      { keys: ["\u2318", "A"], description: "Select all items" },
      { keys: ["\u21E7", "Click"], description: "Range select" },
      { keys: ["Del"], description: "Delete selected" },
    ],
  },
];

function Kbd({ children }: { children: string }) {
  return (
    <kbd className="inline-flex h-6 min-w-6 items-center justify-center rounded-md border border-border bg-surface px-2 py-0.5 text-xs font-mono text-foreground shadow-sm">
      {children}
    </kbd>
  );
}

export function ShortcutHelpModal() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const dialogId = useId();
  const titleId = `${dialogId}-title`;

  const handleOpen = useCallback(() => {
    setOpen(true);
    setQuery("");
  }, []);

  const handleClose = useCallback(() => {
    setOpen(false);
    setQuery("");
  }, []);

  // Listen for "?" key to open, Escape to close
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        e.target instanceof HTMLSelectElement
      ) {
        // Allow Escape to close even from the search input inside the modal
        if (e.key === "Escape" && open) {
          e.preventDefault();
          handleClose();
        }
        return;
      }

      if (e.key === "?" && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault();
        if (open) {
          handleClose();
        } else {
          handleOpen();
        }
        return;
      }

      if (e.key === "Escape" && open) {
        e.preventDefault();
        handleClose();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, handleOpen, handleClose]);

  // Focus search input when modal opens
  useEffect(() => {
    if (open) {
      // Small delay to ensure the modal has rendered
      requestAnimationFrame(() => searchRef.current?.focus());
    }
  }, [open]);

  // Filter shortcuts by search query
  const filteredGroups = useMemo(() => {
    if (!query.trim()) return SHORTCUT_GROUPS;

    const lowerQuery = query.toLowerCase();
    return SHORTCUT_GROUPS.map((group) => ({
      ...group,
      shortcuts: group.shortcuts.filter(
        (s) =>
          s.description.toLowerCase().includes(lowerQuery) ||
          s.keys.some((k) => k.toLowerCase().includes(lowerQuery)) ||
          group.label.toLowerCase().includes(lowerQuery)
      ),
    })).filter((group) => group.shortcuts.length > 0);
  }, [query]);

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm"
        onClick={handleClose}
      />

      {/* Modal */}
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          className="wizard-step-enter w-full max-w-2xl rounded-xl border border-border bg-surface shadow-2xl"
        >
          {/* Header */}
          <div className="flex items-center gap-3 border-b border-border px-6 py-4">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent/15">
              <Keyboard className="h-4 w-4 text-accent" aria-hidden="true" />
            </div>
            <h2 id={titleId} className="flex-1 text-base font-semibold text-foreground">
              Keyboard Shortcuts
            </h2>
            <button
              onClick={handleClose}
              aria-label="Close shortcuts"
              className="rounded-lg p-1.5 text-muted hover:text-foreground transition-colors"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>

          {/* Search */}
          <div className="border-b border-border px-6 py-3">
            <div className="relative">
              <Search
                className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden="true"
              />
              <input
                ref={searchRef}
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search shortcuts..."
                className="h-9 w-full rounded-lg border border-border bg-background pl-10 pr-3 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30 transition-colors"
              />
            </div>
          </div>

          {/* Shortcut groups - two column grid */}
          <div className="max-h-[60vh] overflow-y-auto px-6 py-4">
            {filteredGroups.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted">
                No shortcuts match "{query}"
              </p>
            ) : (
              <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
                {filteredGroups.map((group) => (
                  <div key={group.label}>
                    <h3 className="mb-3 text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">
                      {group.label}
                    </h3>
                    <ul className="space-y-2">
                      {group.shortcuts.map((shortcut) => (
                        <li
                          key={shortcut.description}
                          className="flex items-center justify-between gap-3 rounded-lg px-2 py-1.5 transition-colors hover:bg-border/30"
                        >
                          <span className="text-sm text-foreground">
                            {shortcut.description}
                          </span>
                          <span className="flex shrink-0 items-center gap-1">
                            {shortcut.keys.map((key, i) => (
                              <span key={i} className="flex items-center gap-1">
                                {i > 0 && (
                                  <span className="text-[10px] text-muted-foreground">+</span>
                                )}
                                <Kbd>{key}</Kbd>
                              </span>
                            ))}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="flex items-center justify-center border-t border-border px-6 py-3">
            <p className="text-xs text-muted-foreground">
              Press <Kbd>Esc</Kbd> to close
            </p>
          </div>
        </div>
      </div>
    </>
  );
}
