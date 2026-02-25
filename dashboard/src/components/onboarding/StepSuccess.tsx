import { Rocket, GitBranch, LayoutDashboard } from "lucide-react";
import { cn } from "@/lib/utils";
import type { BackendChoice } from "./StepBackendSelect";

interface StepSuccessProps {
  backend: BackendChoice | null;
  selectedTemplate: string | null;
  onFinish: () => void;
  onGoTo: (path: string) => void;
}

const CONFETTI_COLORS = [
  "bg-accent", "bg-success", "bg-[#3B82F6]", "bg-[#8B5CF6]", "bg-[#EF4444]",
  "bg-accent", "bg-success", "bg-[#3B82F6]",
];

function ConfettiParticles() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
      {Array.from({ length: 20 }, (_, i) => {
        const x = `${(Math.random() - 0.5) * 300}px`;
        const y = `${Math.random() * 200 + 80}px`;
        const r = `${Math.random() * 720 - 360}deg`;
        const color = CONFETTI_COLORS[i % CONFETTI_COLORS.length];
        const size = Math.random() > 0.5 ? "h-2 w-2" : "h-1.5 w-3";
        const shape = Math.random() > 0.5 ? "rounded-full" : "rounded-sm";
        return (
          <div
            key={i}
            className={cn("absolute left-1/2 top-0", size, shape, color)}
            style={{
              "--x": x,
              "--y": y,
              "--r": r,
              animation: `confetti-fall ${1.2 + Math.random() * 0.8}s ease-out ${i * 0.04}s both`,
            } as React.CSSProperties}
          />
        );
      })}
    </div>
  );
}

function AnimatedCheckmark() {
  return (
    <svg className="h-12 w-12" viewBox="0 0 24 24" fill="none">
      <circle
        cx="12" cy="12" r="10"
        stroke="currentColor"
        strokeWidth="1.5"
        className="text-success"
        strokeDasharray="63"
        strokeDashoffset="63"
        style={{ animation: "stroke-draw 0.6s ease-out 0.2s both" }}
      />
      <path
        d="M8 12.5l2.5 2.5L16 9.5"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="text-success"
        strokeDasharray="15"
        strokeDashoffset="15"
        style={{ animation: "stroke-draw 0.4s ease-out 0.6s both" }}
      />
    </svg>
  );
}

/**
 * Final success step - celebration with confetti, animated checkmark,
 * config summary, and action cards.
 */
export function StepSuccess({ backend, selectedTemplate, onFinish, onGoTo }: StepSuccessProps) {
  return (
    <div className="relative space-y-8 text-center">
      <ConfettiParticles />

      {/* Animated checkmark */}
      <div className="relative mx-auto flex h-20 w-20 items-center justify-center">
        <div className="absolute inset-0 rounded-full bg-success/10 ambient-glow" />
        <div className="relative flex h-20 w-20 items-center justify-center rounded-full bg-success/15">
          <AnimatedCheckmark />
        </div>
      </div>

      <div className="scale-in-bounce" style={{ animationDelay: "0.3s" }}>
        <h2 className="text-2xl font-bold text-foreground tracking-tight">You're all set!</h2>
        <p className="mt-2 text-sm text-muted">
          Sandcastle is configured and ready to orchestrate your workflows.
        </p>
      </div>

      {/* Configuration summary */}
      <div
        className="mx-auto max-w-xs rounded-xl border border-border bg-background p-4 text-left animate-[fade-in-up_0.4s_ease-out_0.5s_both]"
      >
        <p className="text-xs font-semibold text-muted uppercase tracking-wider mb-3">
          Configuration Summary
        </p>
        <div className="space-y-2">
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted">Backend</span>
            <span className="font-medium text-foreground capitalize">
              {backend ?? "Default"}
            </span>
          </div>
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted">Template</span>
            <span className="font-medium text-foreground">
              {selectedTemplate
                ? selectedTemplate.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
                : "None selected"}
            </span>
          </div>
        </div>
      </div>

      {/* Action cards */}
      <div className="mx-auto max-w-sm space-y-3">
        <button
          onClick={onFinish}
          className={cn(
            "w-full inline-flex items-center justify-center gap-2 rounded-xl bg-accent px-6 py-3 text-sm font-medium text-accent-foreground btn-shimmer",
            "hover:bg-accent-hover transition-all duration-200 shadow-sm hover:shadow-md"
          )}
        >
          <Rocket className="h-4 w-4" />
          Go to Dashboard
        </button>

        <div className="grid grid-cols-2 gap-2 stagger-children">
          <button
            onClick={() => onGoTo("/workflows/builder")}
            className={cn(
              "flex flex-col items-center gap-1.5 rounded-xl border border-border p-3 text-xs text-muted card-hover-lift",
              "hover:text-accent hover:border-accent/30 transition-colors"
            )}
          >
            <GitBranch className="h-4 w-4" />
            Create Workflow
          </button>
          <button
            onClick={() => onGoTo("/templates")}
            className={cn(
              "flex flex-col items-center gap-1.5 rounded-xl border border-border p-3 text-xs text-muted card-hover-lift",
              "hover:text-accent hover:border-accent/30 transition-colors"
            )}
          >
            <LayoutDashboard className="h-4 w-4" />
            Explore Templates
          </button>
        </div>
      </div>
    </div>
  );
}
