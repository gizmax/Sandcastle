import { GitBranch, Wand2 } from "lucide-react";
import { cn } from "@/lib/utils";

const templates = [
  {
    id: "lead-enrichment",
    name: "Lead Enrichment",
    description: "Scrape and enrich lead data from websites",
  },
  {
    id: "competitor-monitor",
    name: "Competitor Monitor",
    description: "Track competitor changes and generate reports",
  },
  {
    id: "seo-audit",
    name: "SEO Audit",
    description: "Analyze a website for SEO issues and recommendations",
  },
];

interface StepFirstWorkflowProps {
  onSelect: (templateId: string | null) => void;
}

export function StepFirstWorkflow({ onSelect }: StepFirstWorkflowProps) {
  return (
    <div className="space-y-6 text-center">
      <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-accent/10">
        <GitBranch className="h-8 w-8 text-accent" />
      </div>
      <div>
        <h2 className="text-lg font-semibold text-foreground">Choose a Workflow</h2>
        <p className="mt-1 text-sm text-muted">
          Start with a template or build from scratch.
        </p>
      </div>

      <div className="mx-auto grid max-w-md gap-3">
        {templates.map((t) => (
          <button
            key={t.id}
            onClick={() => onSelect(t.id)}
            className={cn(
              "flex items-start gap-3 rounded-xl border border-border p-4 text-left",
              "hover:border-accent/40 hover:shadow-sm transition-all duration-200"
            )}
          >
            <Wand2 className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
            <div>
              <p className="text-sm font-medium text-foreground">{t.name}</p>
              <p className="mt-0.5 text-xs text-muted">{t.description}</p>
            </div>
          </button>
        ))}
      </div>

      <button
        onClick={() => onSelect(null)}
        className="text-sm text-accent hover:text-accent-hover transition-colors"
      >
        Start from scratch
      </button>
    </div>
  );
}
