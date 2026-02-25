import { useEffect, useState, useMemo } from "react";
import { ArrowLeft, ArrowRight, GitBranch, Search, Wand2, Loader2 } from "lucide-react";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";

interface StepFirstWorkflowProps {
  selected: string | null;
  onSelect: (templateId: string | null) => void;
  onNext: () => void;
  onBack: () => void;
}

interface Template {
  name: string;
  description?: string;
  category?: string;
}

// Fallback templates for demo mode
const FALLBACK_TEMPLATES: Template[] = [
  {
    name: "lead-enrichment",
    description: "Scrape and enrich lead data from websites.",
    category: "Sales",
  },
  {
    name: "competitor-monitor",
    description: "Track competitor changes and generate reports.",
    category: "Research",
  },
  {
    name: "seo-audit",
    description: "Analyze a website for SEO issues and recommendations.",
    category: "Marketing",
  },
  {
    name: "code-review",
    description: "Automated code review with security and quality checks.",
    category: "DevOps",
  },
];

// Format template name for display
function formatName(name: string): string {
  return name.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * Template selection step with API fetching, search, and category grouping.
 */
export function StepFirstWorkflow({ selected, onSelect, onNext, onBack }: StepFirstWorkflowProps) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");

  // Fetch templates from API
  useEffect(() => {
    async function fetchTemplates() {
      try {
        const res = await api.get<Template[]>("/templates");
        if (res.data && Array.isArray(res.data) && res.data.length > 0) {
          setTemplates(res.data);
        } else {
          setTemplates(FALLBACK_TEMPLATES);
        }
      } catch {
        setTemplates(FALLBACK_TEMPLATES);
      }
      setLoading(false);
    }
    void fetchTemplates();
  }, []);

  // Filter by search
  const filtered = useMemo(() => {
    if (!search.trim()) return templates;
    const q = search.toLowerCase();
    return templates.filter(
      (t) =>
        t.name.toLowerCase().includes(q) ||
        (t.description?.toLowerCase().includes(q)) ||
        (t.category?.toLowerCase().includes(q))
    );
  }, [templates, search]);

  // Group by category
  const grouped = useMemo(() => {
    const groups: Record<string, Template[]> = {};
    for (const t of filtered) {
      const cat = t.category || "Other";
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push(t);
    }
    return groups;
  }, [filtered]);

  const categories = Object.keys(grouped).sort();

  return (
    <div className="space-y-6">
      <div className="text-center">
        <h2 className="text-lg font-semibold text-foreground">Choose a Workflow</h2>
        <p className="mt-1 text-sm text-muted">
          Start with a template or build from scratch later.
        </p>
      </div>

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search templates..."
          className={cn(
            "w-full rounded-lg border border-border bg-background pl-9 pr-3 py-2 text-sm text-foreground",
            "placeholder:text-muted/50",
            "focus:outline-none focus:ring-2 focus:ring-accent/30 focus:border-accent",
            "transition-all duration-200"
          )}
        />
      </div>

      {/* Template list */}
      <div className="max-h-64 overflow-y-auto space-y-4 -mx-1 px-1">
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-accent" />
          </div>
        ) : filtered.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted">No templates found.</p>
        ) : (
          categories.map((category) => (
            <div key={category}>
              <p className="mb-2 text-xs font-semibold text-muted uppercase tracking-wider">
                {category}
              </p>
              <div className="grid gap-2">
                {grouped[category].map((t) => {
                  const isSelected = selected === t.name;
                  return (
                    <button
                      key={t.name}
                      onClick={() => onSelect(isSelected ? null : t.name)}
                      className={cn(
                        "flex items-start gap-3 rounded-xl border p-3 text-left transition-all duration-200",
                        isSelected
                          ? "border-accent bg-accent/5 glow-accent"
                          : "border-border hover:border-accent/40"
                      )}
                    >
                      <div
                        className={cn(
                          "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md",
                          isSelected ? "bg-accent/15 text-accent" : "bg-border/50 text-muted"
                        )}
                      >
                        <Wand2 className="h-3.5 w-3.5" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium text-foreground">{formatName(t.name)}</p>
                        {t.description && (
                          <p className="mt-0.5 text-xs text-muted truncate">{t.description}</p>
                        )}
                      </div>
                      {isSelected && (
                        <div className="mt-1 h-4 w-4 shrink-0 rounded-full bg-accent flex items-center justify-center">
                          <svg
                            className="h-2.5 w-2.5 text-accent-foreground"
                            fill="none"
                            viewBox="0 0 24 24"
                            stroke="currentColor"
                            strokeWidth={3}
                          >
                            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                          </svg>
                        </div>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          ))
        )}
      </div>

      {/* Skip option */}
      <div className="text-center">
        <button
          onClick={() => {
            onSelect(null);
            onNext();
          }}
          className="inline-flex items-center gap-1.5 text-xs text-muted hover:text-foreground transition-colors"
        >
          <GitBranch className="h-3 w-3" />
          Skip - I'll build from scratch
        </button>
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
