import { useState, useEffect, useCallback, useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Layers, Search, ChevronRight, Sparkles } from "lucide-react";
import { toast } from "sonner";
import jsYaml from "js-yaml";
import { api } from "@/api/client";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";
import { TEMPLATE_PACKS, resolveCategory, getPackById } from "@/lib/templatePacks";
import { PackCard } from "@/components/templates/PackCard";
import { TemplateCard } from "@/components/templates/TemplateCard";
import { TemplateDetail } from "@/components/templates/TemplateDetail";
import { RunModal } from "@/components/templates/RunModal";

interface InputSchemaProperty {
  type: string;
  description?: string;
  default?: unknown;
}

interface InputSchema {
  properties: Record<string, InputSchemaProperty>;
  required?: string[];
}

interface Template {
  name: string;
  description: string;
  tags: string[];
  step_count: number;
  category?: string | null;
  input_schema?: InputSchema | null;
}

interface TemplateDetailData extends Template {
  content: string;
}

const FEATURED_TEMPLATES = [
  "research_agent",
  "sales_pipeline_autopilot",
  "support_ticket_triage",
  "blog_to_social",
];

export default function TemplatesPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedPack = searchParams.get("pack");

  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [selectedTag, setSelectedTag] = useState<string | null>(null);
  const [detailName, setDetailName] = useState<string | null>(null);
  const [detail, setDetail] = useState<TemplateDetailData | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [runModalOpen, setRunModalOpen] = useState(false);
  const [runInput, setRunInput] = useState("{}");
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});
  const [running, setRunning] = useState(false);

  useEffect(() => {
    api.get<Template[]>("/templates").then((res) => {
      setTemplates(res.data || []);
    }).finally(() => setLoading(false));
  }, []);

  // Resolve category for templates that don't have one from the backend
  const templatesWithCategory = useMemo(() =>
    templates.map((t) => ({
      ...t,
      category: t.category || resolveCategory(null, t.tags),
    })),
    [templates]
  );

  // Pack template counts
  const packCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const pack of TEMPLATE_PACKS) {
      counts[pack.id] = templatesWithCategory.filter((t) => t.category === pack.id).length;
    }
    return counts;
  }, [templatesWithCategory]);

  // Filtered templates for drill-down view
  const filtered = useMemo(() => {
    let result = templatesWithCategory;
    if (selectedPack) {
      result = result.filter((t) => t.category === selectedPack);
    }
    if (search) {
      const q = search.toLowerCase();
      result = result.filter(
        (t) =>
          t.name.toLowerCase().includes(q) ||
          t.description.toLowerCase().includes(q) ||
          t.tags.some((tag) => tag.toLowerCase().includes(q))
      );
    }
    if (selectedTag) {
      result = result.filter((t) => t.tags.includes(selectedTag));
    }
    return result;
  }, [templatesWithCategory, selectedPack, search, selectedTag]);

  // Tags for drill-down filter pills
  const activeTags = useMemo(() => {
    const source = selectedPack
      ? templatesWithCategory.filter((t) => t.category === selectedPack)
      : templatesWithCategory;
    const tagSet = new Set<string>();
    source.forEach((t) => t.tags.forEach((tag) => tagSet.add(tag)));
    return Array.from(tagSet).sort();
  }, [templatesWithCategory, selectedPack]);

  // Featured templates for hub view
  const featured = useMemo(() =>
    FEATURED_TEMPLATES
      .map((name) => templatesWithCategory.find((t) => t.name === name))
      .filter(Boolean) as Template[],
    [templatesWithCategory]
  );

  const isHubView = !selectedPack && !search;

  const selectPack = useCallback((packId: string) => {
    setSelectedTag(null);
    setSearchParams({ pack: packId });
  }, [setSearchParams]);

  const goToHub = useCallback(() => {
    setSelectedTag(null);
    setSearch("");
    setSearchParams({});
  }, [setSearchParams]);

  const openDetail = useCallback(async (name: string) => {
    setDetailName(name);
    setDetailLoading(true);
    const res = await api.get<TemplateDetailData>(`/templates/${name}`);
    if (res.data) setDetail(res.data);
    setDetailLoading(false);
  }, []);

  const closeDetail = useCallback(() => {
    setDetailName(null);
    setDetail(null);
  }, []);

  const handleUseInBuilder = useCallback(() => {
    if (!detail) return;
    try {
      const parsed = jsYaml.load(detail.content) as Record<string, unknown>;
      const rawSteps = (parsed.steps ?? []) as Array<Record<string, unknown>>;
      const steps = rawSteps.map((s) => ({
        id: String(s.id ?? ""),
        model: s.model ? String(s.model) : undefined,
        depends_on: Array.isArray(s.depends_on) ? s.depends_on.map(String) : undefined,
        prompt: s.prompt ? String(s.prompt) : undefined,
      }));
      navigate("/workflows/builder", {
        state: {
          workflow: {
            name: String(parsed.name ?? detail.name),
            description: String(parsed.description ?? detail.description ?? ""),
            steps_count: steps.length,
            file_name: `${detail.name}.yaml`,
            steps,
            yaml_content: detail.content,
          },
        },
      });
    } catch {
      navigate("/workflows/builder", { state: { yaml: detail.content } });
    }
  }, [detail, navigate]);

  const handleRunNow = useCallback(() => {
    if (detail?.input_schema?.properties) {
      const init: Record<string, string> = {};
      for (const [key, prop] of Object.entries(detail.input_schema.properties)) {
        init[key] = prop.default != null ? String(prop.default) : "";
      }
      setFieldValues(init);
    } else {
      setFieldValues({});
    }
    setRunInput("{}");
    setRunModalOpen(true);
  }, [detail]);

  const handleRunSubmit = useCallback(async () => {
    if (!detail) return;
    setRunning(true);
    let parsed: Record<string, unknown> = {};
    if (detail.input_schema?.properties) {
      for (const [key, val] of Object.entries(fieldValues)) {
        if (val) parsed[key] = val;
      }
    } else {
      try {
        parsed = JSON.parse(runInput);
      } catch {
        // empty
      }
    }
    const res = await api.post<{ run_id: string }>("/workflows/run", {
      workflow: detail.content,
      input: parsed,
    });
    setRunning(false);
    setRunModalOpen(false);
    if (res.error) {
      toast.error(`Run failed: ${res.error.message}`);
    } else if (res.data) {
      toast.success("Workflow started");
      const runId = (res.data as Record<string, unknown>).run_id as string;
      if (runId) navigate(`/runs/${runId}`);
    }
  }, [detail, fieldValues, runInput, navigate]);

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  const currentPack = selectedPack ? getPackById(selectedPack) : null;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="space-y-1">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent/15">
              <Layers className="h-5 w-5 text-accent" />
            </div>
            <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">
              Template Hub
            </h1>
          </div>
          <span className="text-sm text-muted">{templates.length} templates</span>
        </div>
        {isHubView && (
          <p className="text-sm text-muted ml-[46px]">
            Ready-made AI workflows for every team
          </p>
        )}
      </div>

      {/* Breadcrumb for drill-down */}
      {!isHubView && (
        <div className="flex items-center gap-1.5 text-sm">
          <button
            onClick={goToHub}
            className="text-accent hover:text-accent-hover transition-colors font-medium"
          >
            Template Hub
          </button>
          {currentPack && (
            <>
              <ChevronRight className="h-3.5 w-3.5 text-muted" />
              <span className="text-foreground font-medium">{currentPack.name}</span>
            </>
          )}
          {search && !selectedPack && (
            <>
              <ChevronRight className="h-3.5 w-3.5 text-muted" />
              <span className="text-muted">Search results</span>
            </>
          )}
        </div>
      )}

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search templates..."
          className={cn(
            "h-9 w-full rounded-lg border border-border bg-background pl-9 pr-3 text-sm",
            "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30"
          )}
        />
      </div>

      {isHubView ? (
        <>
          {/* Featured row */}
          {featured.length > 0 && (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <Sparkles className="h-4 w-4 text-amber-500" />
                <h2 className="text-sm font-semibold text-foreground">Featured</h2>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                {featured.map((t) => (
                  <TemplateCard
                    key={t.name}
                    template={t}
                    isSelected={detailName === t.name}
                    onClick={() => openDetail(t.name)}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Browse by Category */}
          <div className="space-y-3">
            <h2 className="text-sm font-semibold text-foreground">Browse by Category</h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {TEMPLATE_PACKS.map((pack) => (
                <PackCard
                  key={pack.id}
                  pack={pack}
                  count={packCounts[pack.id] || 0}
                  onClick={() => selectPack(pack.id)}
                />
              ))}
            </div>
          </div>
        </>
      ) : (
        <>
          {/* Tag filter pills (drill-down view) */}
          <div className="flex flex-wrap gap-1.5">
            <button
              onClick={() => setSelectedTag(null)}
              className={cn(
                "rounded-full px-2.5 py-1 text-xs font-medium transition-colors",
                !selectedTag
                  ? "bg-accent text-accent-foreground"
                  : "bg-border/40 text-muted hover:text-foreground"
              )}
            >
              All
            </button>
            {activeTags.map((tag) => (
              <button
                key={tag}
                onClick={() => setSelectedTag(selectedTag === tag ? null : tag)}
                className={cn(
                  "rounded-full px-2.5 py-1 text-xs font-medium transition-colors",
                  selectedTag === tag
                    ? "bg-accent text-accent-foreground"
                    : "bg-border/40 text-muted hover:text-foreground"
                )}
              >
                {tag}
              </button>
            ))}
          </div>

          {/* Template grid */}
          {filtered.length === 0 ? (
            <EmptyState
              icon={Layers}
              title="No templates found"
              description="Try adjusting your search or tag filter."
              action={
                search || selectedTag
                  ? {
                      label: "Reset filters",
                      onClick: () => {
                        setSearch("");
                        setSelectedTag(null);
                      },
                    }
                  : undefined
              }
            />
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {filtered.map((t) => (
                <TemplateCard
                  key={t.name}
                  template={t}
                  isSelected={detailName === t.name}
                  onClick={() => openDetail(t.name)}
                />
              ))}
            </div>
          )}
        </>
      )}

      {/* Detail slide-over */}
      {detailName && (
        <TemplateDetail
          template={detail}
          loading={detailLoading}
          detailName={detailName}
          onClose={closeDetail}
          onUseInBuilder={handleUseInBuilder}
          onRunNow={handleRunNow}
        />
      )}

      {/* Run modal */}
      {runModalOpen && detail && (
        <RunModal
          template={detail}
          open={runModalOpen}
          onClose={() => setRunModalOpen(false)}
          onSubmit={handleRunSubmit}
          running={running}
          fieldValues={fieldValues}
          onFieldChange={(key, value) => setFieldValues((prev) => ({ ...prev, [key]: value }))}
          runInput={runInput}
          onRunInputChange={setRunInput}
        />
      )}
    </div>
  );
}
