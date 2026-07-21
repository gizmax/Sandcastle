import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CheckCircle2, Loader2, LayoutDashboard, RotateCw } from "lucide-react";
import { api } from "@/api/client";
import { ProgressBar } from "./ProgressBar";
import { StepWelcome } from "./StepWelcome";
import { StepChooseTemplate } from "./StepChooseTemplate";
import { StepConfigureInputs } from "./StepConfigureInputs";
import { cn } from "@/lib/utils";
import { useDensity } from "@/contexts/UiModeContext";
import type { InputSchema } from "@/types/inputSchema";

/** Template selection state shared between wizard steps. */
export interface SelectedTemplate {
  name: string;
  description?: string;
  category?: string;
  inputSchema?: InputSchema | null;
}

interface HealthProviders {
  [key: string]: { status: string; latency_ms: number | null; region: string };
}

interface OnboardingWizardProps {
  onFinish: () => void;
}

const STEP_LABELS = ["Welcome", "Connect", "Pick", "Configure", "Run"];
const STORAGE_KEY = "sandcastle-onboarding-step";
const MAX_STEP = 4;

function providerSettingForKey(key: string):
  | "anthropic_api_key"
  | "openai_api_key"
  | "mistral_api_key" {
  if (key.startsWith("sk-ant-")) return "anthropic_api_key";
  if (key.startsWith("sk-")) return "openai_api_key";
  return "mistral_api_key";
}

// -----------------------------------------------------------------------
// Step 1: Connect - choose local (auto-detected) or cloud (paste a key)
// -----------------------------------------------------------------------

function StepConnect({ onNext, onSkip }: { onNext: () => void; onSkip: () => void }) {
  const [providers, setProviders] = useState<HealthProviders | null>(null);
  const [loading, setLoading] = useState(true);
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const res = await api.get<HealthProviders>("/health/providers");
        setProviders(res.data);
      } catch {
        // Ignore - will show the cloud API key input as a fallback.
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
  const hasLocal = readyProviders.some(([, v]) => v.region === "local");

  const handleSaveKey = async () => {
    if (!apiKey.trim()) return;
    setSaving(true);
    setSaveError(null);
    try {
      const key = apiKey.trim();
      const res = await api.patch("/settings", { [providerSettingForKey(key)]: key });
      if (res.error) throw new Error(res.error.message);
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
          Local or cloud?
        </h2>
        <p className="mt-1 text-sm text-muted">
          Run models privately on your own machine, or connect a cloud provider.
        </p>
      </div>

      {loading ? (
        <div className="flex justify-center py-8">
          <div className="h-6 w-6 rounded-full border-2 border-accent border-t-transparent animate-spin" />
        </div>
      ) : hasReady ? (
        <div className="space-y-3">
          <p className="text-sm font-medium text-foreground">
            {hasLocal ? "Running locally - $0/run, data stays on your box:" : "Detected providers:"}
          </p>
          {readyProviders.map(([id, info]) => (
            <div
              key={id}
              className="flex items-center gap-3 rounded-xl border border-success/30 bg-success/5 p-3"
            >
              <div className="flex items-center justify-center w-6 h-6 rounded-full bg-success text-white shrink-0">
                <CheckCircle2 className="h-4 w-4" />
              </div>
              <div className="flex-1 min-w-0">
                <span className="text-sm font-medium text-foreground capitalize">{id}</span>
                <span className="ml-2 text-xs text-muted-foreground">
                  {info.region}
                  {info.region === "local" ? " - $0/run" : ""}
                  {info.latency_ms ? ` - ${Math.round(info.latency_ms)}ms` : ""}
                </span>
              </div>
              <span className="text-xs font-semibold text-success">Ready!</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="space-y-3">
          <div className="rounded-xl border border-border bg-background p-3">
            <p className="text-sm font-medium text-foreground">Run locally (private, free)</p>
            <p className="mt-1 text-xs text-muted-foreground">
              No provider detected. Start a local model, then come back:
            </p>
            <code className="mt-2 block rounded-md bg-surface px-2 py-1.5 text-xs text-accent">
              ollama run llama3
            </code>
          </div>
          <p className="text-center text-xs text-muted-foreground">or use the cloud</p>
          <label className="block text-sm font-medium text-foreground">
            Paste an Anthropic, Mistral, or OpenAI API key
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
          {saveError && <p className="text-xs text-error">{saveError}</p>}
          {saveSuccess && (
            <p className="text-xs text-success font-medium">Key saved! You can continue.</p>
          )}
        </div>
      )}

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
        </button>
      </div>
    </div>
  );
}

// -----------------------------------------------------------------------
// Step 4: Run the chosen template (best-effort) and finish into Standard density
// -----------------------------------------------------------------------

interface RunResult {
  run_id?: string;
  status?: string;
  outputs?: Record<string, unknown>;
}

function firstOutput(outputs?: Record<string, unknown>): string | null {
  if (!outputs) return null;
  const vals = Object.values(outputs);
  for (let i = vals.length - 1; i >= 0; i--) {
    if (typeof vals[i] === "string" && (vals[i] as string).trim()) {
      return vals[i] as string;
    }
  }
  return null;
}

function StepRun({
  template,
  inputs,
  onFinish,
  onBack,
}: {
  template: SelectedTemplate | null;
  inputs: Record<string, string>;
  onFinish: () => void;
  onBack: () => void;
}) {
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [output, setOutput] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState("");
  const isWeb = template?.category === "Live web";
  const rateLimited = /rate.?limit|RATE_LIMIT|429/i.test(errorMsg);

  const run = async () => {
    if (!template) return;
    setStatus("running");
    setOutput(null);
    try {
      const cleanInputs: Record<string, string> = {};
      for (const [k, v] of Object.entries(inputs)) {
        if (v !== undefined && v !== null && String(v).trim() !== "") cleanInputs[k] = v;
      }
      // Built-in templates only resolve by name for user workflows, so fetch the
      // template's YAML and run it inline; fall back to running by name.
      let body: Record<string, unknown> = { workflow_name: template.name, inputs: cleanInputs };
      try {
        const tpl = await api.get<{ content?: string }>(
          `/templates/${encodeURIComponent(template.name)}`
        );
        if (tpl.data?.content) body = { workflow: tpl.data.content, inputs: cleanInputs };
      } catch {
        // Keep the workflow_name fallback.
      }
      const res = await api.post<RunResult>("/workflows/run", body);
      setOutput(firstOutput(res.data?.outputs) || "Workflow completed successfully.");
      setStatus("done");
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : "");
      setStatus("error");
    }
  };

  return (
    <div className="space-y-6">
      <div className="text-center">
        <h2 className="text-xl font-bold text-foreground tracking-tight">Run your first flow</h2>
        <p className="mt-1 text-sm text-muted">
          Let's run <span className="font-medium text-foreground">{template?.name ?? "your workflow"}</span> once to see it work.
        </p>
      </div>

      {isWeb && (
        <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-xs text-muted-foreground">
          🌐 Uses <strong className="text-foreground">live web search</strong> - free, no key needed. Hitting limits? Add a free Jina key (no card) in Settings &rarr; Integrations.
        </div>
      )}

      <div className="rounded-xl border border-border p-5 space-y-4">
        {status === "idle" && (
          <button
            onClick={() => void run()}
            disabled={!template}
            className={cn(
              "w-full rounded-lg bg-accent px-5 py-3 text-sm font-semibold text-accent-foreground",
              "hover:bg-accent-hover transition-all shadow-sm hover:shadow-md btn-shimmer",
              "disabled:opacity-50 disabled:cursor-not-allowed"
            )}
          >
            Run workflow
          </button>
        )}

        {status === "running" && (
          <div className="flex items-center gap-3 py-2">
            <Loader2 className="h-5 w-5 animate-spin text-accent shrink-0" />
            <span className="text-sm text-foreground">Running...</span>
          </div>
        )}

        {status === "done" && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="h-5 w-5 text-success" />
              <span className="text-sm font-semibold text-success">Done!</span>
            </div>
            {output && (
              <div className="rounded-lg bg-accent/5 border border-accent/20 p-3 max-h-40 overflow-y-auto">
                <p className="text-sm text-foreground leading-relaxed whitespace-pre-wrap">{output}</p>
              </div>
            )}
          </div>
        )}

        {status === "error" && (
          <div className="space-y-2">
            {rateLimited ? (
              <p className="text-sm text-amber-500">
                Free web-search rate limit reached. Add a free Jina key (no card) in
                Settings &rarr; Integrations &rarr; Web Search to raise the limit, then retry.
              </p>
            ) : (
              <p className="text-sm text-muted-foreground">
                Couldn't complete the run here - but your setup is ready. You can run flows from the dashboard.
              </p>
            )}
            <button
              onClick={() => void run()}
              className="inline-flex items-center gap-1.5 text-xs font-medium text-accent hover:text-accent-hover transition-colors"
            >
              <RotateCw className="h-3 w-3" /> Retry
            </button>
          </div>
        )}
      </div>

      <div className="flex items-center justify-between pt-2">
        <button
          onClick={onBack}
          className="text-xs text-muted hover:text-foreground transition-colors"
        >
          Back
        </button>
        <button
          onClick={onFinish}
          className={cn(
            "inline-flex items-center gap-2 rounded-lg px-5 py-2 text-sm font-medium transition-all",
            status === "done"
              ? "bg-accent text-accent-foreground hover:bg-accent-hover"
              : "border border-border text-muted hover:text-foreground"
          )}
        >
          <LayoutDashboard className="h-4 w-4" />
          {status === "done" ? "Go to my dashboard" : "Skip to dashboard"}
        </button>
      </div>
    </div>
  );
}

// -----------------------------------------------------------------------
// Main Wizard
// -----------------------------------------------------------------------

/**
 * Guided beginner onboarding wizard:
 *   0 Welcome   - value props
 *   1 Connect   - local (auto-detected) or cloud (paste a key)
 *   2 Pick      - choose one of 3 beginner-friendly templates
 *   3 Configure - fill the template's inputs
 *   4 Run       - run it once, then land in the balanced "Standard" dashboard
 */
export function OnboardingWizard({ onFinish }: OnboardingWizardProps) {
  const navigate = useNavigate();
  const { setDensity } = useDensity();

  const [currentStep, setCurrentStep] = useState(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    const parsed = saved ? parseInt(saved, 10) : 0;
    return isNaN(parsed) || parsed < 0 || parsed > MAX_STEP ? 0 : parsed;
  });
  const [stepKey, setStepKey] = useState(0);
  const [template, setTemplate] = useState<SelectedTemplate | null>(null);
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, String(currentStep));
    } catch {
      // Best-effort persistence only.
    }
  }, [currentStep]);

  const goTo = useCallback((step: number) => {
    setCurrentStep(Math.max(0, Math.min(MAX_STEP, step)));
    setStepKey((k) => k + 1);
  }, []);

  const markDone = useCallback(() => {
    try {
      localStorage.setItem("sandcastle-onboarding-done", "true");
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // Storage unavailable - onboarding may re-appear next visit.
    }
  }, []);

  // Skip = power user who wants the whole surface area now.
  const handleSkipToDashboard = useCallback(() => {
    markDone();
    setDensity("Everything");
    navigate("/");
  }, [markDone, navigate, setDensity]);

  // Finish the guided flow = land on the balanced default (Standard).
  const handleFinish = useCallback(() => {
    markDone();
    setDensity("Standard");
    onFinish();
  }, [markDone, onFinish, setDensity]);

  return (
    <div className="flex w-full flex-col gap-8">
      <ProgressBar steps={STEP_LABELS} currentStep={currentStep} />

      <div className="mx-auto w-full max-w-xl rounded-xl border border-border bg-surface p-6 sm:p-8 shadow-sm">
        <div key={stepKey} className="wizard-step-enter">
          {currentStep === 0 && (
            <StepWelcome onNext={() => goTo(1)} onSkip={handleSkipToDashboard} />
          )}
          {currentStep === 1 && (
            <StepConnect onNext={() => goTo(2)} onSkip={handleSkipToDashboard} />
          )}
          {currentStep === 2 && (
            <StepChooseTemplate
              selected={template}
              onSelect={setTemplate}
              onNext={() => goTo(3)}
              onBack={() => goTo(1)}
            />
          )}
          {currentStep === 3 && (
            <StepConfigureInputs
              template={template}
              fieldValues={fieldValues}
              onFieldValuesChange={setFieldValues}
              onNext={() => goTo(4)}
              onBack={() => goTo(2)}
            />
          )}
          {currentStep === 4 && (
            <StepRun
              template={template}
              inputs={fieldValues}
              onFinish={handleFinish}
              onBack={() => goTo(3)}
            />
          )}
        </div>
      </div>
    </div>
  );
}
