import { Cloud, Container, Terminal, Globe, ArrowRight, ArrowLeft } from "lucide-react";
import { cn } from "@/lib/utils";
import type { LucideIcon } from "lucide-react";

export type BackendChoice = "e2b" | "docker" | "local" | "cloudflare";

interface StepBackendSelectProps {
  selected: BackendChoice | null;
  onSelect: (backend: BackendChoice) => void;
  onNext: () => void;
  onBack: () => void;
}

interface BackendOption {
  id: BackendChoice;
  icon: LucideIcon;
  title: string;
  subtitle: string;
  description: string;
  badge?: string;
}

const BACKENDS: BackendOption[] = [
  {
    id: "e2b",
    icon: Cloud,
    title: "E2B",
    subtitle: "Cloud Sandboxes",
    description: "Isolated cloud VMs with pre-installed runtimes. Best for production workloads.",
    badge: "Recommended",
  },
  {
    id: "docker",
    icon: Container,
    title: "Docker",
    subtitle: "Local Containers",
    description: "Run sandboxes in Docker containers on your machine. Good for self-hosted setups.",
  },
  {
    id: "local",
    icon: Terminal,
    title: "Local",
    subtitle: "Subprocess",
    description: "Execute code as local subprocesses. No isolation - for development only.",
    badge: "Dev only",
  },
  {
    id: "cloudflare",
    icon: Globe,
    title: "Cloudflare",
    subtitle: "Edge Workers",
    description: "Run on Cloudflare Workers at the edge. Ultra-low latency, limited runtimes.",
  },
];

/**
 * Backend selection step - pick the sandbox backend.
 */
export function StepBackendSelect({
  selected,
  onSelect,
  onNext,
  onBack,
}: StepBackendSelectProps) {
  return (
    <div className="space-y-6">
      <div className="text-center">
        <h2 className="text-lg font-semibold text-foreground">Choose a Sandbox Backend</h2>
        <p className="mt-1 text-sm text-muted">
          Where should Sandcastle execute your workflow code?
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {BACKENDS.map((backend) => {
          const isSelected = selected === backend.id;
          const Icon = backend.icon;

          return (
            <button
              key={backend.id}
              onClick={() => onSelect(backend.id)}
              className={cn(
                "relative flex flex-col items-start gap-2 rounded-xl border p-4 text-left transition-all duration-200",
                isSelected
                  ? "border-accent bg-accent/5 glow-accent card-selected"
                  : "border-border hover:border-accent/40 hover:bg-accent/[0.02] card-hover-lift"
              )}
            >
              {/* Badge */}
              {backend.badge && (
                <span
                  className={cn(
                    "absolute top-3 right-3 rounded-full px-2 py-0.5 text-[10px] font-semibold animate-[fade-in-left_0.3s_ease-out_0.2s_both]",
                    backend.badge === "Recommended"
                      ? "bg-accent/15 text-accent"
                      : "bg-muted/15 text-muted"
                  )}
                >
                  {backend.badge}
                </span>
              )}

              <div
                className={cn(
                  "flex h-10 w-10 items-center justify-center rounded-lg transition-colors",
                  isSelected ? "bg-accent/15 text-accent" : "bg-border/50 text-muted"
                )}
              >
                <Icon className="h-5 w-5" />
              </div>

              <div>
                <p className="text-sm font-semibold text-foreground">
                  {backend.title}
                  <span className="ml-1.5 text-xs font-normal text-muted">
                    {backend.subtitle}
                  </span>
                </p>
                <p className="mt-1 text-xs text-muted leading-relaxed">
                  {backend.description}
                </p>
              </div>

              {/* Selection indicator */}
              <div
                className={cn(
                  "absolute bottom-3 right-3 h-4 w-4 rounded-full border-2 transition-all",
                  isSelected
                    ? "border-accent bg-accent"
                    : "border-border"
                )}
              >
                {isSelected && (
                  <svg
                    className="h-full w-full text-accent-foreground"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={3}
                  >
                    <path className="checkmark-draw" strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                )}
              </div>
            </button>
          );
        })}
      </div>

      {/* Navigation */}
      <div className="flex items-center justify-between pt-2">
        <button
          onClick={onBack}
          className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-foreground transition-colors"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back
        </button>
        <button
          onClick={onNext}
          disabled={!selected}
          className={cn(
            "inline-flex items-center gap-2 rounded-lg bg-accent px-5 py-2 text-sm font-medium text-accent-foreground",
            "hover:bg-accent-hover transition-all duration-200 shadow-sm",
            "disabled:opacity-40 disabled:cursor-not-allowed"
          )}
        >
          Continue
          <ArrowRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
