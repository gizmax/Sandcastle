import { useState } from "react";
import { StepConnectSandstorm } from "./StepConnectSandstorm";
import { StepFirstWorkflow } from "./StepFirstWorkflow";
import { StepRunTest } from "./StepRunTest";
import { cn } from "@/lib/utils";

interface OnboardingWizardProps {
  onFinish: () => void;
}

const STEPS = ["Connect", "Workflow", "Ready"];

export function OnboardingWizard({ onFinish }: OnboardingWizardProps) {
  const [currentStep, setCurrentStep] = useState(0);

  return (
    <div className="mx-auto max-w-lg">
      {/* Progress */}
      <div className="mb-8 flex items-center justify-center gap-2">
        {STEPS.map((label, i) => (
          <div key={label} className="flex items-center gap-2">
            <div
              className={cn(
                "flex h-8 w-8 items-center justify-center rounded-full text-xs font-semibold",
                i <= currentStep
                  ? "bg-accent text-accent-foreground"
                  : "bg-border text-muted"
              )}
            >
              {i + 1}
            </div>
            <span
              className={cn(
                "text-xs font-medium",
                i <= currentStep ? "text-foreground" : "text-muted"
              )}
            >
              {label}
            </span>
            {i < STEPS.length - 1 && (
              <div
                className={cn(
                  "mx-2 h-px w-8",
                  i < currentStep ? "bg-accent" : "bg-border"
                )}
              />
            )}
          </div>
        ))}
      </div>

      {/* Content */}
      <div className="rounded-xl border border-border bg-surface p-8 shadow-sm">
        {currentStep === 0 && (
          <StepConnectSandstorm onComplete={() => setCurrentStep(1)} />
        )}
        {currentStep === 1 && (
          <StepFirstWorkflow onSelect={() => setCurrentStep(2)} />
        )}
        {currentStep === 2 && <StepRunTest onComplete={onFinish} />}
      </div>
    </div>
  );
}
