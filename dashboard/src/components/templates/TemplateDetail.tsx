import { useEffect } from "react";
import { ArrowRight, Loader2, Play, X } from "lucide-react";
import { cn } from "@/lib/utils";

interface InputSchemaProperty {
  type: string;
  description?: string;
  default?: unknown;
}

interface InputSchema {
  properties: Record<string, InputSchemaProperty>;
  required?: string[];
}

interface TemplateDetailData {
  name: string;
  description: string;
  tags: string[];
  step_count: number;
  content: string;
  input_schema?: InputSchema | null;
}

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

interface TemplateDetailProps {
  template: TemplateDetailData | null;
  loading: boolean;
  detailName: string;
  onClose: () => void;
  onUseInBuilder: () => void;
  onRunNow: () => void;
}

export function TemplateDetail({
  template,
  loading,
  detailName,
  onClose,
  onUseInBuilder,
  onRunNow,
}: TemplateDetailProps) {
  // Close on Escape key
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/40" aria-hidden="true" onClick={onClose} />
      <div role="dialog" aria-modal="true" aria-label={`Template detail: ${detailName}`} className="fixed inset-y-0 right-0 z-50 flex w-full max-w-lg flex-col border-l border-border bg-surface shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <h2 className="text-lg font-semibold text-foreground">
            {detailName.replace(/_/g, " ")}
          </h2>
          <button
            onClick={onClose}
            className="rounded-lg p-1 text-muted hover:text-foreground transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted" />
            </div>
          ) : template ? (
            <>
              <div>
                <p className="text-sm text-muted leading-relaxed">{template.description}</p>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {template.tags.map((tag) => (
                    <span
                      key={tag}
                      className={cn(
                        "rounded-full px-2.5 py-0.5 text-xs font-medium",
                        tagColor(tag)
                      )}
                    >
                      {tag}
                    </span>
                  ))}
                  <span className="rounded-full bg-border/40 px-2.5 py-0.5 text-xs font-medium text-muted">
                    {template.step_count} {template.step_count === 1 ? "step" : "steps"}
                  </span>
                </div>
              </div>

              <div>
                <p className="mb-2 text-xs font-medium text-muted">YAML Definition</p>
                <pre className="max-h-96 overflow-auto rounded-lg bg-background p-4 font-mono text-xs text-foreground whitespace-pre-wrap">
                  {template.content}
                </pre>
              </div>
            </>
          ) : (
            <p className="text-sm text-muted">Template not found</p>
          )}
        </div>

        {template && (
          <div className="flex items-center gap-2 border-t border-border px-5 py-4">
            <button
              onClick={onUseInBuilder}
              className={cn(
                "flex flex-1 items-center justify-center gap-2 rounded-lg border border-border px-4 py-2",
                "text-sm font-medium text-foreground",
                "hover:bg-border/40 transition-colors"
              )}
            >
              <ArrowRight className="h-4 w-4" />
              Use in Builder
            </button>
            <button
              onClick={onRunNow}
              className={cn(
                "flex flex-1 items-center justify-center gap-2 rounded-lg bg-accent px-4 py-2",
                "text-sm font-medium text-accent-foreground",
                "hover:bg-accent-hover transition-all duration-200 shadow-sm"
              )}
            >
              <Play className="h-4 w-4" />
              Run Now
            </button>
          </div>
        )}
      </div>
    </>
  );
}
