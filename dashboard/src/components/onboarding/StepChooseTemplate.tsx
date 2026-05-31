import { useEffect, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Camera,
  FileText,
  Search,
  Database,
  Loader2,
} from "lucide-react";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";
import type { SelectedTemplate } from "./OnboardingWizard";
import type { LucideIcon } from "lucide-react";
import type { InputSchema } from "@/types/inputSchema";

interface StepChooseTemplateProps {
  selected: SelectedTemplate | null;
  onSelect: (template: SelectedTemplate | null) => void;
  onNext: () => void;
  onBack: () => void;
}

interface FeaturedTemplate {
  name: string;
  displayName: string;
  description: string;
  category: string;
  icon: LucideIcon;
  color: string;
}

// The 4 popular built-in templates to feature
const FEATURED_TEMPLATES: FeaturedTemplate[] = [
  {
    name: "ugc-studio",
    displayName: "UGC Studio",
    description: "Drop a product photo, pick a mode - get faithful, social-ready UGC shots, scored and auto-fixed by a vision judge.",
    category: "Creative",
    icon: Camera,
    color: "text-[#F472B6] bg-[#F472B6]/10",
  },
  {
    name: "ai-content-pipeline",
    displayName: "AI Content Pipeline",
    description: "Research, draft, and publish content with automated SEO optimization.",
    category: "Marketing",
    icon: FileText,
    color: "text-[#60A5FA] bg-[#60A5FA]/10",
  },
  {
    name: "market-opportunity-scout",
    displayName: "Market Opportunity Scout",
    description: "Analyze markets, competitors, and trends to identify growth opportunities.",
    category: "Research",
    icon: Search,
    color: "text-[#34D399] bg-[#34D399]/10",
  },
  {
    name: "ai-rag-pipeline",
    displayName: "AI RAG Pipeline",
    description: "Build retrieval-augmented generation pipelines over your own documents.",
    category: "Data",
    icon: Database,
    color: "text-[#A78BFA] bg-[#A78BFA]/10",
  },
];

interface ApiTemplate {
  name: string;
  description?: string;
  category?: string;
  input_schema?: InputSchema | null;
}

/**
 * Step 2: Choose a template from 4 featured options displayed as cards.
 * Fetches real input schemas from the API when available.
 */
export function StepChooseTemplate({
  selected,
  onSelect,
  onNext,
  onBack,
}: StepChooseTemplateProps) {
  const [schemaMap, setSchemaMap] = useState<Record<string, InputSchema | null>>({});
  const [loading, setLoading] = useState(true);

  // Fetch template details from API to get input schemas
  useEffect(() => {
    async function fetchSchemas() {
      try {
        const res = await api.get<ApiTemplate[]>("/templates");
        if (res.data && Array.isArray(res.data)) {
          const map: Record<string, InputSchema | null> = {};
          for (const t of res.data) {
            map[t.name] = t.input_schema ?? null;
          }
          setSchemaMap(map);
        }
      } catch {
        // Schemas unavailable - will proceed without pre-populated fields
      }
      setLoading(false);
    }
    void fetchSchemas();
  }, []);

  function handleSelect(featured: FeaturedTemplate) {
    if (selected?.name === featured.name) {
      onSelect(null);
      return;
    }
    onSelect({
      name: featured.name,
      description: featured.description,
      category: featured.category,
      inputSchema: schemaMap[featured.name] ?? null,
    });
  }

  return (
    <div className="space-y-6">
      <div className="text-center">
        <h2 className="text-lg font-semibold text-foreground">Choose a Template</h2>
        <p className="mt-1 text-sm text-muted">
          Pick a popular workflow template to get started quickly.
        </p>
      </div>

      {/* Template cards */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-5 w-5 animate-spin text-accent" />
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 stagger-children">
          {FEATURED_TEMPLATES.map((template) => {
            const isSelected = selected?.name === template.name;
            const Icon = template.icon;
            const [iconColor, iconBg] = template.color.split(" ");

            return (
              <button
                key={template.name}
                onClick={() => handleSelect(template)}
                className={cn(
                  "relative flex flex-col items-start gap-3 rounded-xl border p-4 text-left transition-all duration-200",
                  isSelected
                    ? "border-accent bg-accent/5 glow-accent card-selected"
                    : "border-border hover:border-accent/40 hover:bg-accent/[0.02] card-hover-lift"
                )}
              >
                {/* Category badge */}
                <span className="absolute top-3 right-3 rounded-full bg-muted/10 px-2 py-0.5 text-[10px] font-medium text-muted">
                  {template.category}
                </span>

                {/* Icon */}
                <div
                  className={cn(
                    "flex h-10 w-10 items-center justify-center rounded-lg transition-colors",
                    isSelected ? "bg-accent/15 text-accent" : iconBg + " " + iconColor
                  )}
                >
                  <Icon className="h-5 w-5" />
                </div>

                {/* Text */}
                <div className="pr-6">
                  <p className="text-sm font-semibold text-foreground">
                    {template.displayName}
                  </p>
                  <p className="mt-1 text-xs text-muted leading-relaxed">
                    {template.description}
                  </p>
                </div>

                {/* Selection indicator */}
                <div
                  className={cn(
                    "absolute bottom-3 right-3 h-4 w-4 rounded-full border-2 transition-all",
                    isSelected ? "border-accent bg-accent" : "border-border"
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
                      <path
                        className="checkmark-draw"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M5 13l4 4L19 7"
                      />
                    </svg>
                  )}
                </div>
              </button>
            );
          })}
        </div>
      )}

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
            "disabled:opacity-60 disabled:cursor-not-allowed"
          )}
        >
          Continue
          <ArrowRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
