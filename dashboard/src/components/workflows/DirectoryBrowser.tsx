import { useEffect, useState } from "react";
import { Folder, File, ChevronUp, Loader2, X } from "lucide-react";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";

interface BrowseEntry {
  name: string;
  path: string;
  is_dir: boolean;
}

interface BrowseResult {
  current: string;
  parent: string | null;
  entries: BrowseEntry[];
}

interface DirectoryBrowserProps {
  open: boolean;
  initialPath?: string;
  onSelect: (path: string) => void;
  onClose: () => void;
}

export function DirectoryBrowser({ open, initialPath, onSelect, onClose }: DirectoryBrowserProps) {
  const [currentPath, setCurrentPath] = useState(initialPath || "~");
  const [entries, setEntries] = useState<BrowseEntry[]>([]);
  const [parentPath, setParentPath] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(null);
    api
      .get<BrowseResult>("/browse", { path: currentPath })
      .then((res) => {
        if (res.data) {
          setCurrentPath(res.data.current);
          setEntries(res.data.entries);
          setParentPath(res.data.parent);
        } else if (res.error) {
          setError(res.error.message);
        }
      })
      .catch(() => setError("Failed to browse directory"))
      .finally(() => setLoading(false));
  }, [open, currentPath]);

  if (!open) return null;

  const directories = entries.filter((e) => e.is_dir);
  const files = entries.filter((e) => !e.is_dir);

  return (
    <>
      <div className="fixed inset-0 z-[60] bg-black/40" onClick={onClose} />
      <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
        <div className="w-full max-w-lg rounded-xl border border-border bg-surface shadow-xl flex flex-col max-h-[70vh]">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <h3 className="text-sm font-semibold text-foreground">Browse Directory</h3>
            <button
              onClick={onClose}
              className="rounded-lg p-1 text-muted hover:text-foreground transition-colors"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* Current path */}
          <div className="flex items-center gap-2 border-b border-border px-4 py-2 bg-background/50">
            {parentPath && (
              <button
                onClick={() => setCurrentPath(parentPath)}
                className="shrink-0 rounded-md p-1 text-muted hover:text-foreground hover:bg-surface transition-colors"
                title="Go up"
              >
                <ChevronUp className="h-4 w-4" />
              </button>
            )}
            <code className="flex-1 truncate text-xs text-muted">{currentPath}</code>
          </div>

          {/* Entries */}
          <div className="flex-1 overflow-y-auto p-2 min-h-[200px]">
            {loading ? (
              <div className="flex items-center justify-center py-12 text-muted">
                <Loader2 className="h-5 w-5 animate-spin" />
              </div>
            ) : error ? (
              <div className="flex items-center justify-center py-12">
                <p className="text-xs text-error">{error}</p>
              </div>
            ) : entries.length === 0 ? (
              <div className="flex items-center justify-center py-12">
                <p className="text-xs text-muted">Empty directory</p>
              </div>
            ) : (
              <div className="space-y-0.5">
                {directories.map((entry) => (
                  <button
                    key={entry.path}
                    onClick={() => setCurrentPath(entry.path)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-lg px-3 py-1.5 text-left",
                      "text-xs text-foreground hover:bg-accent/10 transition-colors"
                    )}
                  >
                    <Folder className="h-3.5 w-3.5 shrink-0 text-accent" />
                    <span className="truncate">{entry.name}</span>
                  </button>
                ))}
                {files.map((entry) => (
                  <div
                    key={entry.path}
                    className="flex items-center gap-2 rounded-lg px-3 py-1.5 text-xs text-muted"
                  >
                    <File className="h-3.5 w-3.5 shrink-0" />
                    <span className="truncate">{entry.name}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
            <button
              onClick={onClose}
              className="rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-muted hover:text-foreground transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={() => onSelect(currentPath)}
              className={cn(
                "rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-accent-foreground",
                "hover:bg-accent-hover transition-all duration-200 shadow-sm"
              )}
            >
              Select This Directory
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
