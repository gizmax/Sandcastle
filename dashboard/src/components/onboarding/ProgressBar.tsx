import { cn } from "@/lib/utils";

interface ProgressBarProps {
  steps: string[];
  currentStep: number;
}

/**
 * Horizontal stepped progress bar with amber fill.
 * Completed steps are solid amber with animated checkmark draw,
 * current step glows and pulses, future steps are muted gray.
 * Connectors animate their fill left-to-right.
 */
export function ProgressBar({ steps, currentStep }: ProgressBarProps) {
  return (
    <nav aria-label="Onboarding progress" className="w-full px-4">
      <ol className="flex items-center justify-between">
        {steps.map((label, i) => {
          const isCompleted = i < currentStep;
          const isCurrent = i === currentStep;
          const isFuture = i > currentStep;

          return (
            <li key={label} className="flex flex-1 items-center last:flex-none">
              {/* Step indicator + label */}
              <div className="flex flex-col items-center gap-1.5">
                <div
                  className={cn(
                    "flex h-8 w-8 items-center justify-center rounded-full text-xs font-semibold transition-settle",
                    isCompleted && "bg-accent text-accent-foreground scale-in-bounce",
                    isCurrent && "bg-accent text-accent-foreground glow-accent ring-2 ring-accent/30 step-indicator-pulse",
                    isFuture && "bg-border text-muted"
                  )}
                >
                  {isCompleted ? (
                    <svg
                      className="h-4 w-4"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                      strokeWidth={2.5}
                    >
                      <path
                        className="checkmark-draw"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M5 13l4 4L19 7"
                      />
                    </svg>
                  ) : (
                    i + 1
                  )}
                </div>
                <span
                  className={cn(
                    "text-[11px] font-medium whitespace-nowrap transition-colors duration-300",
                    isCompleted && "text-accent",
                    isCurrent && "text-foreground",
                    isFuture && "text-muted"
                  )}
                >
                  {label}
                </span>
              </div>

              {/* Connector line between steps - two layers for animated fill */}
              {i < steps.length - 1 && (
                <div className="relative mx-2 mt-[-18px] h-0.5 flex-1">
                  {/* Background track */}
                  <div className="absolute inset-0 rounded-full bg-border" />
                  {/* Amber overlay with animated scaleX */}
                  {i < currentStep && (
                    <div
                      className="absolute inset-0 rounded-full bg-accent origin-left"
                      style={{
                        animation: `connector-fill 0.5s ease-out ${i * 0.15}s both`,
                      }}
                    />
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
