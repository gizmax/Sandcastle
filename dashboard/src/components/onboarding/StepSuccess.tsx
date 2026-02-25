import { CheckCircle, Rocket, GitBranch, LayoutDashboard } from "lucide-react";
import { cn } from "@/lib/utils";
import type { BackendChoice } from "./StepBackendSelect";

interface StepSuccessProps {
  backend: BackendChoice | null;
  selectedTemplate: string | null;
  onFinish: () => void;
  onGoTo: (path: string) => void;
}

/**
 * Final success step - celebration with summary and quick actions.
 */
export function StepSuccess({ backend, selectedTemplate, onFinish, onGoTo }: StepSuccessProps) {
  return (
    <div className="space-y-8 text-center">
      {/* Celebration icon */}
      <div className="relative mx-auto flex h-20 w-20 items-center justify-center">
        {/* Outer ring animation */}
        <div className="absolute inset-0 rounded-full bg-success/10 animate-ping" />
        <div className="relative flex h-20 w-20 items-center justify-center rounded-full bg-success/15">
          <CheckCircle className="h-10 w-10 text-success" />
        </div>
      </div>

      <div>
        <h2 className="text-2xl font-bold text-foreground tracking-tight">You're all set!</h2>
        <p className="mt-2 text-sm text-muted">
          Sandcastle is configured and ready to orchestrate your workflows.
        </p>
      </div>

      {/* Configuration summary */}
      <div className="mx-auto max-w-xs rounded-xl border border-border bg-background p-4 text-left">
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

      {/* Quick actions */}
      <div className="mx-auto grid max-w-sm gap-2">
        <button
          onClick={onFinish}
          className={cn(
            "inline-flex items-center justify-center gap-2 rounded-lg bg-accent px-6 py-2.5 text-sm font-medium text-accent-foreground",
            "hover:bg-accent-hover transition-all duration-200 shadow-sm hover:shadow-md"
          )}
        >
          <Rocket className="h-4 w-4" />
          Go to Dashboard
        </button>

        <div className="flex items-center justify-center gap-4 pt-1">
          <button
            onClick={() => onGoTo("/workflows/builder")}
            className="inline-flex items-center gap-1.5 text-xs text-muted hover:text-accent transition-colors"
          >
            <GitBranch className="h-3.5 w-3.5" />
            Create Workflow
          </button>
          <button
            onClick={() => onGoTo("/templates")}
            className="inline-flex items-center gap-1.5 text-xs text-muted hover:text-accent transition-colors"
          >
            <LayoutDashboard className="h-3.5 w-3.5" />
            Explore Templates
          </button>
        </div>
      </div>
    </div>
  );
}
