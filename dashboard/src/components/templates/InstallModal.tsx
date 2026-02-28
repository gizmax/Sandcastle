import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  X,
  Check,
  Loader2,
  ArrowRight,
  Play,
  Lightbulb,
  Package,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { TemplatePack } from "@/lib/templatePacks";

interface InstallModalProps {
  pack: TemplatePack;
  selectedTemplates: string[];
  onClose: () => void;
  onComplete: () => void;
}

type InstallPhase = "installing" | "done";

interface TemplateProgress {
  name: string;
  status: "pending" | "installing" | "done";
}

export function InstallModal({
  pack,
  selectedTemplates,
  onClose,
  onComplete,
}: InstallModalProps) {
  const navigate = useNavigate();
  const [phase, setPhase] = useState<InstallPhase>("installing");
  const [progress, setProgress] = useState<TemplateProgress[]>(
    selectedTemplates.map((name) => ({ name, status: "pending" }))
  );

  // Staggered install animation - each template takes ~400ms
  useEffect(() => {
    let cancelled = false;

    async function runInstall() {
      for (let i = 0; i < selectedTemplates.length; i++) {
        if (cancelled) return;

        // Mark current as installing
        setProgress((prev) =>
          prev.map((p, idx) => (idx === i ? { ...p, status: "installing" } : p))
        );

        // Simulated delay - this is a UI ceremony
        await new Promise((r) => setTimeout(r, 350 + Math.random() * 200));

        if (cancelled) return;

        // Mark current as done
        setProgress((prev) =>
          prev.map((p, idx) => (idx === i ? { ...p, status: "done" } : p))
        );
      }

      // Small pause before showing success
      await new Promise((r) => setTimeout(r, 300));
      if (!cancelled) setPhase("done");
    }

    runInstall();
    return () => {
      cancelled = true;
    };
  }, [selectedTemplates]);

  const handleOpenBuilder = useCallback(() => {
    onComplete();
    navigate("/workflows/builder");
  }, [navigate, onComplete]);

  const handleRunFirst = useCallback(() => {
    onComplete();
    // Navigate back to templates page with the first template ready
    onClose();
  }, [onComplete, onClose]);

  const Icon = pack.icon;
  const doneCount = progress.filter((p) => p.status === "done").length;
  const progressPct =
    selectedTemplates.length > 0
      ? Math.round((doneCount / selectedTemplates.length) * 100)
      : 0;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-[60] bg-black/50 backdrop-blur-sm"
        onClick={phase === "done" ? onClose : undefined}
      />

      {/* Modal */}
      <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
        <div
          role="dialog"
          aria-modal="true"
          aria-label={phase === "done" ? `${pack.name} Installed` : `Installing ${pack.name}`}
          className={cn(
            "w-full max-w-md rounded-xl border bg-surface shadow-xl",
            "transition-all duration-300",
            phase === "done" ? "border-accent/30 glow-accent" : "border-border"
          )}
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-border px-5 py-4">
            <div className="flex items-center gap-3">
              <div
                className={cn(
                  "flex h-9 w-9 items-center justify-center rounded-lg",
                  pack.color.bg
                )}
              >
                <Icon className={cn("h-4.5 w-4.5", pack.color.text)} />
              </div>
              <div>
                <h2 className="text-sm font-semibold text-foreground">
                  {phase === "done"
                    ? `${pack.name} Installed`
                    : `Installing ${pack.name}`}
                </h2>
                <p className="text-xs text-muted">
                  {phase === "done"
                    ? `${selectedTemplates.length} templates ready`
                    : `${doneCount} of ${selectedTemplates.length} templates`}
                </p>
              </div>
            </div>
            <button
              onClick={onClose}
              className="rounded-lg p-1 text-muted hover:text-foreground transition-colors"
            >
              <X className="h-5 w-5" />
            </button>
          </div>

          {/* Progress bar */}
          {phase === "installing" && (
            <div className="px-5 pt-4">
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-border/40">
                <div
                  className="h-full rounded-full bg-accent transition-all duration-300 ease-out"
                  style={{ width: `${progressPct}%` }}
                />
              </div>
            </div>
          )}

          {/* Template list */}
          <div className="max-h-64 overflow-y-auto px-5 py-3">
            <div className="space-y-1">
              {progress.map((item) => (
                <div
                  key={item.name}
                  className={cn(
                    "flex items-center gap-3 rounded-lg px-3 py-2 transition-all duration-200",
                    item.status === "installing" && "bg-accent/5",
                    item.status === "done" && "opacity-80"
                  )}
                >
                  {/* Status icon */}
                  <div className="flex h-5 w-5 shrink-0 items-center justify-center">
                    {item.status === "pending" && (
                      <div className="h-2 w-2 rounded-full bg-border" />
                    )}
                    {item.status === "installing" && (
                      <Loader2 className="h-4 w-4 animate-spin text-accent" />
                    )}
                    {item.status === "done" && (
                      <div className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500/15">
                        <Check className="h-3 w-3 text-emerald-500" />
                      </div>
                    )}
                  </div>

                  {/* Template name */}
                  <span
                    className={cn(
                      "text-sm transition-colors",
                      item.status === "installing"
                        ? "text-foreground font-medium"
                        : item.status === "done"
                          ? "text-muted"
                          : "text-muted-foreground"
                    )}
                  >
                    {item.name.replace(/_/g, " ")}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Success state */}
          {phase === "done" && (
            <div className="space-y-4 border-t border-border px-5 py-4">
              {/* Setup hint */}
              <div className="flex gap-2.5 rounded-lg bg-accent/5 border border-accent/10 px-3 py-2.5">
                <Lightbulb className="h-4 w-4 shrink-0 text-accent mt-0.5" />
                <p className="text-xs text-muted leading-relaxed">
                  {pack.setupHint}
                </p>
              </div>

              {/* Action buttons */}
              <div className="flex items-center gap-2">
                <button
                  onClick={handleOpenBuilder}
                  className={cn(
                    "flex flex-1 items-center justify-center gap-2 rounded-lg bg-accent px-4 py-2.5",
                    "text-sm font-medium text-accent-foreground",
                    "hover:bg-accent-hover transition-all duration-200 shadow-sm"
                  )}
                >
                  <ArrowRight className="h-4 w-4" />
                  Open in Builder
                </button>
                <button
                  onClick={handleRunFirst}
                  className={cn(
                    "flex flex-1 items-center justify-center gap-2 rounded-lg border border-border px-4 py-2.5",
                    "text-sm font-medium text-foreground",
                    "hover:bg-border/40 transition-colors"
                  )}
                >
                  <Play className="h-4 w-4" />
                  Browse Templates
                </button>
              </div>
            </div>
          )}

          {/* Installing state footer */}
          {phase === "installing" && (
            <div className="flex items-center justify-center border-t border-border px-5 py-3">
              <div className="flex items-center gap-2 text-xs text-muted">
                <Package className="h-3.5 w-3.5" />
                <span>Setting up your workflow templates...</span>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
