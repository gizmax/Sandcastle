import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import { ProgressBar } from "./ProgressBar";
import { cn } from "@/lib/utils";
import type { InputSchema } from "@/types/inputSchema";

/** Template selection state shared between wizard steps (kept for backward compat). */
export interface SelectedTemplate {
  name: string;
  description?: string;
  category?: string;
  inputSchema?: InputSchema | null;
}

interface ProviderEntry {
  id: string;
  name?: string;
  configured: boolean;
  status: string;
  region: string;
}

interface AdvisorStatus {
  current_provider: string;
  available_providers: ProviderEntry[];
}

interface HealthProviders {
  [key: string]: { status: string; latency_ms: number | null; region: string };
}

interface DemoRunResult {
  run_id: string;
  status: string;
  outputs: { generate: string; summarize: string };
  total_cost_usd: number;
}

interface OnboardingWizardProps {
  onFinish: () => void;
}

const STEP_LABELS = ["Connect", "Try it"];
const STORAGE_KEY = "sandcastle-onboarding-step";
const MAX_STEP = 1;

// -----------------------------------------------------------------------
// Step 1: Connect - auto-detect providers or prompt for an API key
// -----------------------------------------------------------------------

function StepConnect({
  onNext,
  onSkip,
}: {
  onNext: () => void;
  onSkip: () => void;
}) {
  const [providers, setProviders] = useState<HealthProviders | null>(null);
  const [loading, setLoading] = useState(true);
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  // Fetch provider health on mount
  useEffect(() => {
    void (async () => {
      try {
        const res = await api.get<HealthProviders>("/health/providers");
        setProviders(res.data);
      } catch {
        // Ignore - will show fallback API key input
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const readyProviders = providers
    ? Object.entries(providers).filter(
        ([, v]) => v.status === "ok" || v.status === "running"
      )
    : [];
  const hasReady = readyProviders.length > 0;

  const handleSaveKey = async () => {
    if (!apiKey.trim()) return;
    setSaving(true);
    setSaveError(null);
    try {
      await api.post("/settings", { api_key: apiKey.trim() });
      setSaveSuccess(true);
    } catch {
      setSaveError("Could not save key. Check your connection.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="text-center">
        <h2 className="text-xl font-bold text-foreground tracking-tight">
          Connect a provider
        </h2>
        <p className="mt-1 text-sm text-muted">
          Sandcastle needs at least one AI provider to run workflows.
        </p>
      </div>

      {loading ? (
        <div className="flex justify-center py-8">
          <div className="h-6 w-6 rounded-full border-2 border-accent border-t-transparent animate-spin" />
        </div>
      ) : hasReady ? (
        <div className="space-y-3">
          <p className="text-sm font-medium text-foreground">
            Auto-detected providers:
          </p>
          {readyProviders.map(([id, info]) => (
            <div
              key={id}
              className="flex items-center gap-3 rounded-xl border border-success/30 bg-success/5 p-3"
            >
              <div className="flex items-center justify-center w-6 h-6 rounded-full bg-success text-white shrink-0">
                <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <div className="flex-1 min-w-0">
                <span className="text-sm font-medium text-foreground capitalize">
                  {id}
                </span>
                <span className="ml-2 text-xs text-muted-foreground">
                  {info.region} - {info.latency_ms ? `${Math.round(info.latency_ms)}ms` : "ready"}
                </span>
              </div>
              <span className="text-xs font-semibold text-success">Ready!</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            No providers detected. Paste your API key below to get started.
          </p>
          <label className="block text-sm font-medium text-foreground">
            Paste your Anthropic, Mistral, or OpenAI API key
          </label>
          <div className="flex gap-2">
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-..."
              className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-accent/40"
            />
            <button
              onClick={() => void handleSaveKey()}
              disabled={saving || !apiKey.trim()}
              className={cn(
                "rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground",
                "hover:bg-accent-hover transition-colors",
                "disabled:opacity-50 disabled:cursor-not-allowed"
              )}
            >
              {saving ? "Saving..." : "Save"}
            </button>
          </div>
          {saveError && (
            <p className="text-xs text-error">{saveError}</p>
          )}
          {saveSuccess && (
            <p className="text-xs text-success font-medium">
              Key saved! You can continue.
            </p>
          )}
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center justify-between pt-2">
        <button
          onClick={onSkip}
          className="text-xs text-muted hover:text-foreground transition-colors"
        >
          Skip to dashboard
        </button>
        <button
          onClick={onNext}
          disabled={!hasReady && !saveSuccess}
          className={cn(
            "inline-flex items-center gap-2 rounded-lg bg-accent px-5 py-2 text-sm font-medium text-accent-foreground",
            "hover:bg-accent-hover transition-all",
            "disabled:opacity-50 disabled:cursor-not-allowed"
          )}
        >
          Next
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
          </svg>
        </button>
      </div>
    </div>
  );
}

// -----------------------------------------------------------------------
// Step 2: Try it - run the demo workflow
// -----------------------------------------------------------------------

function StepTryIt({
  onFinish,
  onSkip,
  onBack,
}: {
  onFinish: () => void;
  onSkip: () => void;
  onBack: () => void;
}) {
  const [demoState, setDemoState] = useState<
    "idle" | "step1" | "step2" | "done" | "error"
  >("idle");
  const [output, setOutput] = useState<string | null>(null);
  const [costHint, setCostHint] = useState("Free with Ollama / ~$0.01 with Claude");

  // Detect cost hint from providers
  useEffect(() => {
    void (async () => {
      try {
        const res = await api.get<AdvisorStatus>("/advisor/status");
        if (!res.data) return;
        const ollama = res.data.available_providers.find((p) => p.id === "ollama");
        if (ollama?.status === "running") {
          setCostHint("Free with Ollama");
        }
      } catch {
        // Keep default hint
      }
    })();
  }, []);

  const runDemo = async () => {
    setDemoState("step1");
    setOutput(null);

    try {
      // Simulate step-by-step progress with a small delay before step2
      await new Promise((r) => setTimeout(r, 1000));
      setDemoState("step2");

      const res = await api.post<DemoRunResult>("/workflows/run", {
        workflow_name: "demo-hello-world",
        workflow: {
          name: "demo-hello-world",
          description: "Your first Sandcastle workflow",
          default_model: "sonnet",
          steps: [
            {
              id: "generate",
              prompt: "Write 3 interesting facts about sandcastles.",
            },
            {
              id: "summarize",
              prompt:
                "Summarize these facts in one sentence: {steps.generate.output}",
              depends_on: ["generate"],
            },
          ],
        },
      });

      if (res.data) {
        setOutput(res.data.outputs?.summarize || "Workflow completed successfully.");
        setDemoState("done");

        // Auto-check "first-run" in the Getting Started checklist
        try {
          const raw = localStorage.getItem("sandcastle_getting_started");
          const cls = raw
            ? JSON.parse(raw)
            : { dismissed: false, completed: {} };
          cls.completed["first-run"] = true;
          localStorage.setItem(
            "sandcastle_getting_started",
            JSON.stringify(cls)
          );
        } catch {
          // Not critical
        }
      } else {
        setDemoState("error");
      }
    } catch {
      setDemoState("error");
    }
  };

  return (
    <div className="space-y-6">
      <div className="text-center">
        <h2 className="text-xl font-bold text-foreground tracking-tight">
          Try it out
        </h2>
        <p className="mt-1 text-sm text-muted">
          Watch a 2-step workflow run in real-time. Takes ~10 seconds.
        </p>
      </div>

      {/* Demo card */}
      <div className="rounded-xl border border-border p-5 space-y-4">
        {demoState === "idle" && (
          <>
            <button
              onClick={() => void runDemo()}
              className={cn(
                "w-full rounded-lg bg-accent px-5 py-3 text-sm font-semibold text-accent-foreground",
                "hover:bg-accent-hover transition-all shadow-sm hover:shadow-md btn-shimmer"
              )}
            >
              Run Demo Workflow
            </button>
            <p className="text-xs text-center text-muted-foreground">
              {costHint}
            </p>
          </>
        )}

        {demoState === "step1" && (
          <div className="flex items-center gap-3 py-2">
            <div className="h-5 w-5 rounded-full border-2 border-accent border-t-transparent animate-spin shrink-0" />
            <span className="text-sm text-foreground">Running step 1 of 2 - Generating facts...</span>
          </div>
        )}

        {demoState === "step2" && (
          <div className="flex items-center gap-3 py-2">
            <div className="h-5 w-5 rounded-full border-2 border-accent border-t-transparent animate-spin shrink-0" />
            <span className="text-sm text-foreground">Running step 2 of 2 - Summarizing...</span>
          </div>
        )}

        {demoState === "done" && output && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <div className="flex items-center justify-center w-6 h-6 rounded-full bg-success text-white shrink-0">
                <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <span className="text-sm font-semibold text-success">Done!</span>
            </div>
            <div className="rounded-lg bg-accent/5 border border-accent/20 p-3">
              <p className="text-sm text-foreground leading-relaxed">{output}</p>
            </div>
          </div>
        )}

        {demoState === "error" && (
          <div className="space-y-2">
            <p className="text-sm text-error">
              Something went wrong. You can try again or skip to the dashboard.
            </p>
            <button
              onClick={() => void runDemo()}
              className="text-xs font-medium text-accent hover:text-accent-hover transition-colors"
            >
              Retry
            </button>
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center justify-between pt-2">
        <button
          onClick={onBack}
          className="text-xs text-muted hover:text-foreground transition-colors"
        >
          Back
        </button>
        <div className="flex items-center gap-3">
          <button
            onClick={onSkip}
            className="text-xs text-muted hover:text-foreground transition-colors"
          >
            Skip
          </button>
          {demoState === "done" && (
            <button
              onClick={onFinish}
              className={cn(
                "inline-flex items-center gap-2 rounded-lg bg-accent px-5 py-2 text-sm font-medium text-accent-foreground",
                "hover:bg-accent-hover transition-all"
              )}
            >
              Go to Dashboard
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
              </svg>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// -----------------------------------------------------------------------
// Main Wizard
// -----------------------------------------------------------------------

/**
 * Simplified 2-step onboarding wizard:
 *   Step 1: Connect - detect providers or paste an API key
 *   Step 2: Try it  - run a demo workflow in one click
 */
export function OnboardingWizard({ onFinish }: OnboardingWizardProps) {
  const navigate = useNavigate();

  // Restore saved step from localStorage (clamped to new range)
  const [currentStep, setCurrentStep] = useState(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    const parsed = saved ? parseInt(saved, 10) : 0;
    return isNaN(parsed) || parsed < 0 || parsed > MAX_STEP ? 0 : parsed;
  });

  const [stepKey, setStepKey] = useState(0);

  // Save progress to localStorage on step change. Wrapped so private
  // browsing / quota exceeded never breaks the wizard mid-flight.
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, String(currentStep));
    } catch {
      // Best-effort: progress isn't persisted across reloads in this mode.
    }
  }, [currentStep]);

  const goTo = useCallback((step: number) => {
    setCurrentStep(Math.max(0, Math.min(MAX_STEP, step)));
    setStepKey((k) => k + 1);
  }, []);

  const handleSkipToDashboard = useCallback(() => {
    try {
      localStorage.setItem("sandcastle-onboarding-done", "true");
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // Storage unavailable - the user can still proceed; onboarding will
      // re-appear next visit.
    }
    navigate("/");
  }, [navigate]);

  const handleFinish = useCallback(() => {
    try {
      localStorage.setItem("sandcastle-onboarding-done", "true");
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // Storage unavailable - proceed anyway.
    }
    onFinish();
  }, [onFinish]);

  return (
    <div className="flex w-full flex-col gap-8">
      {/* Progress bar */}
      <ProgressBar steps={STEP_LABELS} currentStep={currentStep} />

      {/* Step content */}
      <div className="mx-auto w-full max-w-xl rounded-xl border border-border bg-surface p-6 sm:p-8 shadow-sm">
        <div key={stepKey} className="wizard-step-enter">
          {currentStep === 0 && (
            <StepConnect
              onNext={() => goTo(1)}
              onSkip={handleSkipToDashboard}
            />
          )}

          {currentStep === 1 && (
            <StepTryIt
              onFinish={handleFinish}
              onSkip={handleSkipToDashboard}
              onBack={() => goTo(0)}
            />
          )}
        </div>
      </div>
    </div>
  );
}
