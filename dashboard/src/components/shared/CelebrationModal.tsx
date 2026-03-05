import { useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { cn } from "@/lib/utils";
import { formatCost, formatDuration } from "@/lib/utils";

interface CelebrationModalProps {
  open: boolean;
  onClose: () => void;
  workflowName: string;
  runId: string;
  costUsd: number;
  durationSeconds: number | null;
  /** If true, show milestone text instead of first-run text */
  milestone?: number | null;
}

const AUTO_DISMISS_MS = 8000;

export function CelebrationModal({
  open,
  onClose,
  workflowName,
  runId,
  costUsd,
  durationSeconds,
  milestone,
}: CelebrationModalProps) {
  const navigate = useNavigate();
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const modalRef = useRef<HTMLDivElement>(null);

  // Auto-dismiss after 8 seconds
  useEffect(() => {
    if (!open) return;
    timerRef.current = setTimeout(onClose, AUTO_DISMISS_MS);
    return () => clearTimeout(timerRef.current);
  }, [open, onClose]);

  // Focus trap & escape
  useEffect(() => {
    if (!open) return;
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handleKeyDown);
    modalRef.current?.focus();
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  const handleNavigate = useCallback(
    (path: string) => {
      onClose();
      navigate(path);
    },
    [navigate, onClose]
  );

  if (!open) return null;

  const heading = milestone
    ? `${milestone} successful runs!`
    : "Your first workflow ran successfully!";

  const subtext = milestone
    ? "You're on a roll. Keep building."
    : "Welcome to Sandcastle. Here's what you can do next.";

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-[60] bg-black/30 backdrop-blur-[2px]"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
        <div
          ref={modalRef}
          role="dialog"
          aria-modal="true"
          aria-label="Celebration"
          tabIndex={-1}
          className={cn(
            "w-full max-w-md rounded-2xl border border-border bg-surface p-6 shadow-2xl",
            "outline-none",
            "celebration-modal-enter"
          )}
        >
          {/* Animated checkmark */}
          <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-full bg-accent/15">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              className="h-8 w-8"
              aria-hidden="true"
            >
              <circle
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="1.5"
                className="text-accent/30"
              />
              <path
                d="M8 12.5l2.5 2.5 5.5-5.5"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="text-accent celebration-checkmark-draw"
              />
            </svg>
          </div>

          {/* Heading */}
          <h2 className="text-center text-lg font-semibold text-foreground">
            {heading}
          </h2>
          <p className="mt-1 text-center text-sm text-muted">{subtext}</p>

          {/* Run stats */}
          <div className="mt-4 flex items-center justify-center gap-4 text-xs text-muted">
            <span className="font-mono" title="Workflow">
              {workflowName}
            </span>
            <span className="text-border">|</span>
            <span className="font-mono">{formatCost(costUsd)}</span>
            {durationSeconds !== null && (
              <>
                <span className="text-border">|</span>
                <span className="font-mono">
                  {formatDuration(durationSeconds)}
                </span>
              </>
            )}
          </div>

          {/* Quick links (only for first run) */}
          {!milestone && (
            <div className="mt-5 space-y-2">
              <p className="text-xs font-medium text-muted-foreground">
                What's next?
              </p>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                <button
                  onClick={() => handleNavigate(`/runs/${runId}`)}
                  className={cn(
                    "rounded-lg border border-border bg-background px-3 py-2.5",
                    "text-left text-xs font-medium text-foreground",
                    "card-hover-lift",
                    "hover:border-accent/40 transition-colors"
                  )}
                >
                  <span className="block text-accent text-[10px] font-semibold uppercase tracking-wider mb-0.5">
                    Results
                  </span>
                  View run output
                </button>
                <button
                  onClick={() => handleNavigate("/templates")}
                  className={cn(
                    "rounded-lg border border-border bg-background px-3 py-2.5",
                    "text-left text-xs font-medium text-foreground",
                    "card-hover-lift",
                    "hover:border-accent/40 transition-colors"
                  )}
                >
                  <span className="block text-accent text-[10px] font-semibold uppercase tracking-wider mb-0.5">
                    Templates
                  </span>
                  Try another workflow
                </button>
                <button
                  onClick={() => handleNavigate("/schedules")}
                  className={cn(
                    "rounded-lg border border-border bg-background px-3 py-2.5",
                    "text-left text-xs font-medium text-foreground",
                    "card-hover-lift",
                    "hover:border-accent/40 transition-colors"
                  )}
                >
                  <span className="block text-accent text-[10px] font-semibold uppercase tracking-wider mb-0.5">
                    Schedule
                  </span>
                  Automate runs
                </button>
              </div>
            </div>
          )}

          {/* Dismiss button */}
          <div className="mt-5 flex justify-center">
            <button
              onClick={onClose}
              className={cn(
                "rounded-lg border border-border px-4 py-1.5",
                "text-sm font-medium text-muted",
                "hover:text-foreground hover:border-foreground/20 transition-colors"
              )}
            >
              Got it
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
