import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import { ProgressBar } from "./ProgressBar";
import { StepWelcome } from "./StepWelcome";
import { StepChooseTemplate } from "./StepChooseTemplate";
import { StepConfigureInputs } from "./StepConfigureInputs";
import { StepApiKeysCheck } from "./StepApiKeysCheck";
import { StepLaunch } from "./StepLaunch";
import type { InputSchema } from "@/types/inputSchema";

interface ProviderEntry {
  id: string;
  configured: boolean;
  status: string;
  region: string;
}

interface AdvisorStatus {
  current_provider: string;
  available_providers: ProviderEntry[];
}

/** Template selection state shared between wizard steps. */
export interface SelectedTemplate {
  name: string;
  description?: string;
  category?: string;
  inputSchema?: InputSchema | null;
}

interface OnboardingWizardProps {
  onFinish: () => void;
}

const STEP_LABELS = [
  "Welcome",
  "Template",
  "Inputs",
  "Keys",
  "Launch",
];

const STORAGE_KEY = "sandcastle-onboarding-step";
const MAX_STEP = 4;

/**
 * Main onboarding wizard - manages 5 steps, persists progress,
 * and guides users through first workflow setup.
 */
export function OnboardingWizard({ onFinish }: OnboardingWizardProps) {
  const navigate = useNavigate();

  // Restore saved step from localStorage
  const [currentStep, setCurrentStep] = useState(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    const parsed = saved ? parseInt(saved, 10) : 0;
    return isNaN(parsed) || parsed < 0 || parsed > MAX_STEP ? 0 : parsed;
  });

  // Wizard state
  const [selectedTemplate, setSelectedTemplate] = useState<SelectedTemplate | null>(null);
  const [inputValues, setInputValues] = useState<Record<string, string>>({});
  const [launching, setLaunching] = useState(false);

  // Save progress to localStorage on step change
  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, String(currentStep));
  }, [currentStep]);

  const [stepKey, setStepKey] = useState(0);

  // Provider detection for non-Anthropic banner
  const [providerBanner, setProviderBanner] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      const res = await api.get<AdvisorStatus>("/advisor/status");
      if (!res.data) return;
      const { available_providers, current_provider } = res.data;
      const anthropic = available_providers.find((p) => p.id === "anthropic");
      const mistral = available_providers.find((p) => p.id === "mistral");
      const openai = available_providers.find((p) => p.id === "openai");
      const ollama = available_providers.find((p) => p.id === "ollama");

      if (!anthropic?.configured && mistral?.configured) {
        setProviderBanner("Mistral detected! Your AI features will use Mistral (EU data residency).");
      } else if (!anthropic?.configured && openai?.configured) {
        setProviderBanner("OpenAI detected! Your AI features will use OpenAI.");
      } else if (!anthropic?.configured && !mistral?.configured && !openai?.configured && ollama?.status === "running") {
        setProviderBanner("Local mode! All AI features powered by Ollama. No data leaves your machine.");
      } else if (current_provider !== "anthropic" && current_provider) {
        const name = current_provider.charAt(0).toUpperCase() + current_provider.slice(1);
        setProviderBanner(`${name} detected! Your AI features will use ${name}.`);
      }
    })();
  }, []);

  const goTo = useCallback((step: number) => {
    setCurrentStep(Math.max(0, Math.min(MAX_STEP, step)));
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

  const handleLaunch = useCallback(async () => {
    if (!selectedTemplate) return;
    setLaunching(true);
    try {
      await api.post("/runs", {
        workflow_name: selectedTemplate.name,
        inputs: inputValues,
      });
      // Mark onboarding as done and redirect to runs page
      localStorage.setItem("sandcastle-onboarding-done", "true");
      localStorage.removeItem(STORAGE_KEY);
      navigate("/runs");
    } catch {
      // Stay on launch step so user can retry
      setLaunching(false);
    }
  }, [selectedTemplate, inputValues, navigate]);

  return (
    <div className="flex w-full flex-col gap-8">
      {/* Non-default provider detection banner */}
      {providerBanner && (
        <div className="rounded-lg border border-accent/30 bg-accent/5 px-4 py-3 text-sm text-foreground flex items-start gap-2">
          <span className="text-accent font-semibold shrink-0">AI:</span>
          <span>{providerBanner}</span>
          <button
            onClick={() => setProviderBanner(null)}
            className="ml-auto shrink-0 text-muted hover:text-foreground transition-colors"
            aria-label="Dismiss"
          >
            x
          </button>
        </div>
      )}

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
          <StepChooseTemplate
            selected={selectedTemplate}
            onSelect={setSelectedTemplate}
            onNext={() => goTo(2)}
            onBack={() => goTo(0)}
          />
        )}

        {currentStep === 2 && (
          <StepConfigureInputs
            template={selectedTemplate}
            fieldValues={inputValues}
            onFieldValuesChange={setInputValues}
            onNext={() => goTo(3)}
            onBack={() => goTo(1)}
          />
        )}

        {currentStep === 3 && (
          <StepApiKeysCheck
            onNext={() => goTo(4)}
            onBack={() => goTo(2)}
          />
        )}

        {currentStep === 4 && (
          <StepLaunch
            template={selectedTemplate}
            inputValues={inputValues}
            onLaunch={() => void handleLaunch()}
            onBack={() => goTo(3)}
            onFinish={handleFinish}
            launching={launching}
          />
        )}
        </div>
      </div>
    </div>
  );
}
