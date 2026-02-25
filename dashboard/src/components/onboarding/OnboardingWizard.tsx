import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import { ProgressBar } from "./ProgressBar";
import { StepWelcome } from "./StepWelcome";
import { StepBackendSelect, type BackendChoice } from "./StepBackendSelect";
import { StepApiKeys } from "./StepApiKeys";
import { StepFirstWorkflow } from "./StepFirstWorkflow";
import { StepFirstRun } from "./StepFirstRun";
import { StepSuccess } from "./StepSuccess";

interface OnboardingWizardProps {
  onFinish: () => void;
}

const STEP_LABELS = [
  "Welcome",
  "Backend",
  "API Keys",
  "Workflow",
  "First Run",
  "Done",
];

const STORAGE_KEY = "sandcastle-onboarding-step";

/**
 * Main onboarding wizard - manages 6 steps, persists progress,
 * and auto-skips if backend is already configured.
 */
export function OnboardingWizard({ onFinish }: OnboardingWizardProps) {
  const navigate = useNavigate();

  // Restore saved step from localStorage
  const [currentStep, setCurrentStep] = useState(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    const parsed = saved ? parseInt(saved, 10) : 0;
    return isNaN(parsed) || parsed < 0 || parsed > 5 ? 0 : parsed;
  });

  // Wizard state
  const [selectedBackend, setSelectedBackend] = useState<BackendChoice | null>(null);
  const [apiKeys, setApiKeys] = useState<Record<string, string>>({});
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(null);
  const [autoSkipChecked, setAutoSkipChecked] = useState(false);

  // Save progress to localStorage on step change
  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, String(currentStep));
  }, [currentStep]);

  // Auto-skip check: if backend is already configured, jump ahead
  useEffect(() => {
    if (autoSkipChecked) return;

    async function checkRuntime() {
      try {
        const res = await api.get<{ status: string; sandbox_backend?: string }>("/runtime");
        if (res.data?.sandbox_backend) {
          // Backend is configured - if user is at step 0, jump to workflow selection
          const backend = res.data.sandbox_backend as BackendChoice;
          setSelectedBackend(backend);
          if (currentStep < 3) {
            setCurrentStep(3);
          }
        }
      } catch {
        // Backend not available - stay at current step
      }
      setAutoSkipChecked(true);
    }

    void checkRuntime();
    // Only run once on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [stepKey, setStepKey] = useState(0);

  const goTo = useCallback((step: number) => {
    setCurrentStep(Math.max(0, Math.min(5, step)));
    setStepKey((k) => k + 1);
  }, []);

  const handleSkipToDashboard = useCallback(() => {
    localStorage.setItem("sandcastle-onboarding-done", "true");
    localStorage.removeItem(STORAGE_KEY);
    navigate("/");
  }, [navigate]);

  const handleFinish = useCallback(() => {
    localStorage.setItem("sandcastle-onboarding-done", "true");
    localStorage.removeItem(STORAGE_KEY);
    onFinish();
  }, [onFinish]);

  const handleGoTo = useCallback(
    (path: string) => {
      localStorage.setItem("sandcastle-onboarding-done", "true");
      localStorage.removeItem(STORAGE_KEY);
      navigate(path);
    },
    [navigate]
  );

  return (
    <div className="flex w-full flex-col gap-8">
      {/* Progress bar */}
      <ProgressBar steps={STEP_LABELS} currentStep={currentStep} />

      {/* Step content */}
      <div className="mx-auto w-full max-w-xl rounded-xl border border-border bg-surface p-6 sm:p-8 shadow-sm">
        <div key={stepKey} className="wizard-step-enter">
        {currentStep === 0 && (
          <StepWelcome
            onNext={() => goTo(1)}
            onSkip={handleSkipToDashboard}
          />
        )}

        {currentStep === 1 && (
          <StepBackendSelect
            selected={selectedBackend}
            onSelect={setSelectedBackend}
            onNext={() => goTo(2)}
            onBack={() => goTo(0)}
          />
        )}

        {currentStep === 2 && (
          <StepApiKeys
            backend={selectedBackend}
            apiKeys={apiKeys}
            onKeysChange={setApiKeys}
            onNext={() => goTo(3)}
            onBack={() => goTo(1)}
          />
        )}

        {currentStep === 3 && (
          <StepFirstWorkflow
            selected={selectedTemplate}
            onSelect={setSelectedTemplate}
            onNext={() => goTo(4)}
            onBack={() => goTo(2)}
          />
        )}

        {currentStep === 4 && (
          <StepFirstRun
            selectedTemplate={selectedTemplate}
            onNext={() => goTo(5)}
            onBack={() => goTo(3)}
          />
        )}

        {currentStep === 5 && (
          <StepSuccess
            backend={selectedBackend}
            selectedTemplate={selectedTemplate}
            onFinish={handleFinish}
            onGoTo={handleGoTo}
          />
        )}
        </div>
      </div>
    </div>
  );
}
