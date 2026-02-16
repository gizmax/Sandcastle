import { useState, useEffect, useCallback } from "react";
import { X, Layers, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/api/client";

interface Template {
  name: string;
  description: string;
  tags: string[];
  step_count: number;
}

interface TemplateDetail extends Template {
  yaml: string;
}

interface TemplateBrowserProps {
  open: boolean;
  onClose: () => void;
  onSelect: (template: TemplateDetail) => void;
}

// Stable tag color palette derived from tag name
const TAG_COLORS = [
  "bg-accent/15 text-accent",
  "bg-running/15 text-running",
  "bg-success/15 text-success",
  "bg-queued/15 text-queued",
  "bg-error/15 text-error",
  "bg-warning/15 text-warning",
];

function tagColor(tag: string): string {
  let hash = 0;
  for (let i = 0; i < tag.length; i++) {
    hash = tag.charCodeAt(i) + ((hash << 5) - hash);
  }
  return TAG_COLORS[Math.abs(hash) % TAG_COLORS.length];
}

export function TemplateBrowser({ open, onClose, onSelect }: TemplateBrowserProps) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [fetching, setFetching] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setSelectedName(null);
    api.get<Template[]>("/templates").then((res) => {
      setTemplates(res.data || []);
      setLoading(false);
    });
  }, [open]);

  const handleUseTemplate = useCallback(async () => {
    if (!selectedName) return;
    setFetching(true);
    const res = await api.get<TemplateDetail>(`/templates/${selectedName}`);
    setFetching(false);
    if (res.data) {
      onSelect(res.data);
    }
  }, [selectedName, onSelect]);

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-50 bg-black/40" onClick={onClose} />

      {/* Modal */}
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div className="w-full max-w-2xl rounded-xl border border-border bg-surface shadow-xl flex flex-col max-h-[80vh]">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-border px-6 py-4">
            <div className="flex items-center gap-2.5">
              <Layers className="h-5 w-5 text-accent" />
              <h2 className="text-lg font-semibold text-foreground">Template Browser</h2>
            </div>
            <button
              onClick={onClose}
              className="rounded-lg p-1 text-muted hover:text-foreground transition-colors"
            >
              <X className="h-5 w-5" />
            </button>
          </div>

          {/* Body */}
          <div className="flex-1 overflow-y-auto p-6">
            {loading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="h-6 w-6 animate-spin text-muted" />
              </div>
            ) : templates.length === 0 ? (
              <p className="text-center text-sm text-muted py-12">
                No templates available.
              </p>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {templates.map((t) => (
                  <button
                    key={t.name}
                    type="button"
                    onClick={() =>
                      setSelectedName(selectedName === t.name ? null : t.name)
                    }
                    className={cn(
                      "rounded-lg border p-4 text-left transition-all duration-150",
                      selectedName === t.name
                        ? "border-accent ring-2 ring-accent/20 bg-accent/5"
                        : "border-border hover:bg-border/40"
                    )}
                  >
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="text-sm font-semibold text-foreground">
                        {t.name.replace(/_/g, " ")}
                      </span>
                      <span className="text-[11px] text-muted-foreground">
                        {t.step_count} {t.step_count === 1 ? "step" : "steps"}
                      </span>
                    </div>
                    <p className="text-xs text-muted leading-relaxed mb-2.5">
                      {t.description}
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      {t.tags.map((tag) => (
                        <span
                          key={tag}
                          className={cn(
                            "rounded-full px-2 py-0.5 text-[10px] font-medium",
                            tagColor(tag)
                          )}
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="flex items-center justify-end gap-2 border-t border-border px-6 py-4">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-muted hover:text-foreground transition-colors"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleUseTemplate}
              disabled={!selectedName || fetching}
              className={cn(
                "flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground",
                "hover:bg-accent-hover transition-all duration-200 shadow-sm",
                (!selectedName || fetching) && "opacity-50 cursor-not-allowed"
              )}
            >
              {fetching && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Use Template
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
