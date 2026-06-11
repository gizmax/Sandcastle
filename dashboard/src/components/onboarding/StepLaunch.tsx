import { ArrowLeft, Rocket, Loader2, LayoutDashboard } from "lucide-react";
import { cn } from "@/lib/utils";
import type { SelectedTemplate } from "./OnboardingWizard";

interface StepLaunchProps {
  template: SelectedTemplate | null;
  inputValues: Record<string, string>;
  onLaunch: () => void;
  onBack: () => void;
  onFinish: () => void;
  launching: boolean;
}

/** Format a template name for display: "ugc-product-shoot" -> "UGC Product Shoot" */
function formatName(name: string): string {
  return name
    .replace(/[-_]/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/\bUgc\b/, "UGC")
    .replace(/\bAi\b/g, "AI")
    .replace(/\bRag\b/, "RAG")
    .replace(/\bSeo\b/, "SEO");
}

/**
 * Step 5: Launch - review configuration and start the workflow.
 * Shows a summary of the selected template and configured inputs,
 * then fires the run on confirm. Step 6 (Monitor) is handled by
 * the redirect in OnboardingWizard.
 */
export function StepLaunch({
  template,
  inputValues,
  onLaunch,
  onBack,
  onFinish,
  launching,
}: StepLaunchProps) {
  const filledInputs = Object.entries(inputValues).filter(
    ([, v]) => v !== undefined && v !== null && v.trim() !== ""
  );

  return (
    <div className="space-y-6">
      <div className="text-center">
        <h2 className="text-lg font-semibold text-foreground">Ready to Launch</h2>
        <p className="mt-1 text-sm text-muted">
          Review your configuration and start your first workflow run.
        </p>
      </div>

      {/* Configuration summary card */}
      <div className="rounded-xl border border-border bg-background p-5 space-y-4">
        <p className="text-xs font-semibold text-muted uppercase tracking-wider">
          Run Summary
        </p>

        {/* Template */}
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted">Workflow</span>
          <span className="font-medium text-foreground">
            {template ? formatName(template.name) : "None selected"}
          </span>
        </div>

        {template?.category && (
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted">Category</span>
            <span className="rounded-full bg-accent/10 px-2 py-0.5 text-xs font-medium text-accent">
              {template.category}
            </span>
          </div>
        )}

        {/* Input values */}
        {filledInputs.length > 0 && (
          <>
            <div className="border-t border-border pt-3">
              <p className="mb-2 text-xs font-semibold text-muted uppercase tracking-wider">
                Inputs
              </p>
              <div className="space-y-2">
                {filledInputs.map(([key, val]) => (
                  <div key={key} className="flex items-start justify-between gap-4 text-sm">
                    <span className="text-muted shrink-0">{key}</span>
                    <span className="font-mono text-xs text-foreground text-right truncate max-w-[200px]">
                      {val}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}

        {filledInputs.length === 0 && (
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted">Inputs</span>
            <span className="text-xs text-muted-foreground">Default values</span>
          </div>
        )}
      </div>

      {/* Launch button */}
      <div className="flex flex-col items-center gap-3">
        <button
          onClick={onLaunch}
          disabled={!template || launching}
          className={cn(
            "w-full inline-flex items-center justify-center gap-2 rounded-xl bg-accent px-6 py-3 text-sm font-medium text-accent-foreground btn-shimmer",
            "hover:bg-accent-hover transition-settle shadow-sm hover:shadow-md",
            "disabled:opacity-60 disabled:cursor-not-allowed"
          )}
        >
          {launching ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Launching...
            </>
          ) : (
            <>
              <Rocket className="h-4 w-4" />
              Launch Workflow
            </>
          )}
        </button>

        <p className="text-xs text-muted text-center">
          After launching, you will be redirected to the run monitor page.
        </p>
      </div>

      {/* Navigation */}
      <div className="flex items-center justify-between pt-2">
        <button
          onClick={onBack}
          disabled={launching}
          className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-foreground transition-colors disabled:opacity-60"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back
        </button>
        <button
          onClick={onFinish}
          disabled={launching}
          className="inline-flex items-center gap-1.5 text-xs text-muted hover:text-foreground transition-colors disabled:opacity-60"
        >
          <LayoutDashboard className="h-3 w-3" />
          Skip to dashboard
        </button>
      </div>
    </div>
  );
}
