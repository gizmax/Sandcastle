import { Castle, ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";

interface StepWelcomeProps {
  onNext: () => void;
  onSkip: () => void;
}

const VALUE_PROPS = [
  {
    emoji: "🔀",
    title: "Orchestrate AI workflows",
    description: "Chain LLM steps, code, HTTP calls, and approvals into reliable pipelines.",
  },
  {
    emoji: "🧊",
    title: "Pluggable sandbox backends",
    description: "Run code in E2B cloud, Docker, local subprocess, or Cloudflare edge.",
  },
  {
    emoji: "🛡️",
    title: "Built-in safety and monitoring",
    description: "Budget limits, policy guardrails, circuit breakers, and real-time events.",
  },
];

/**
 * Welcome step - brand intro with value propositions.
 */
export function StepWelcome({ onNext, onSkip }: StepWelcomeProps) {
  return (
    <div className="space-y-8 text-center">
      {/* Brand mark */}
      <div className="mx-auto flex h-20 w-20 items-center justify-center rounded-2xl bg-accent/10 glow-accent">
        <Castle className="h-10 w-10 text-accent" />
      </div>

      <div>
        <h1 className="text-2xl font-bold text-foreground tracking-tight">
          Welcome to Sandcastle
        </h1>
        <p className="mt-2 text-sm text-muted">
          Set up your workflow orchestrator in a few quick steps.
        </p>
      </div>

      {/* Value props */}
      <div className="mx-auto max-w-md space-y-4 text-left">
        {VALUE_PROPS.map((prop) => (
          <div key={prop.title} className="flex items-start gap-3">
            <span className="mt-0.5 text-lg leading-none">{prop.emoji}</span>
            <div>
              <p className="text-sm font-semibold text-foreground">{prop.title}</p>
              <p className="mt-0.5 text-xs text-muted leading-relaxed">{prop.description}</p>
            </div>
          </div>
        ))}
      </div>

      {/* Actions */}
      <div className="space-y-3 pt-2">
        <button
          onClick={onNext}
          className={cn(
            "inline-flex items-center gap-2 rounded-lg bg-accent px-6 py-2.5 text-sm font-medium text-accent-foreground",
            "hover:bg-accent-hover transition-all duration-200 shadow-sm hover:shadow-md"
          )}
        >
          Get Started
          <ArrowRight className="h-4 w-4" />
        </button>
        <div>
          <button
            onClick={onSkip}
            className="text-xs text-muted hover:text-foreground transition-colors"
          >
            Skip to dashboard
          </button>
        </div>
      </div>
    </div>
  );
}
