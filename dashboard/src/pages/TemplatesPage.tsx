import { useState, useEffect, useCallback, useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Layers,
  Search,
  ChevronRight,
  Package,
  LayoutGrid,
  Plug,
  Download,
  Check,
  Eye,
  Plus,
  ChevronDown,
  ChevronUp,
  Globe,
  X,
  ArrowRight,
} from "lucide-react";
import { toast } from "sonner";
import jsYaml from "js-yaml";
import { api } from "@/api/client";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";
import {
  TEMPLATE_PACKS,
  resolveCategory,
  getPackById,
  getFeaturedPack,
} from "@/lib/templatePacks";
import type { TemplatePack } from "@/lib/templatePacks";
import { PackCard } from "@/components/templates/PackCard";
import { TemplateCard } from "@/components/templates/TemplateCard";
import { TemplateDetail } from "@/components/templates/TemplateDetail";
import { RunModal } from "@/components/templates/RunModal";
import { InstallModal } from "@/components/templates/InstallModal";
import { CommunityCard } from "@/components/templates/CommunityCard";
import type { CommunityTemplate } from "@/components/templates/CommunityCard";
import { TOOL_ICON_MAP } from "@/components/integrations/toolIcons";

// ---- Types ----

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

type ViewMode = "packs" | "all" | "integrations" | "community";

// ---- Community types ----

interface CommunityRegistryResponse {
  templates: CommunityTemplate[];
  stats: {
    total_templates: number;
    total_authors: number;
    categories: { id: string; count: number }[];
  };
}

// ---- Constants ----

const VIEW_TABS: { id: ViewMode; label: string; icon: typeof Package }[] = [
  { id: "packs", label: "Packs", icon: Package },
  { id: "all", label: "All Templates", icon: LayoutGrid },
  { id: "integrations", label: "By Integration", icon: Plug },
  { id: "community", label: "Community", icon: Globe },
];

// ---- Component ----

export default function TemplatesPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // URL state
  const viewParam = (searchParams.get("view") as ViewMode) || "packs";
  const selectedPackId = searchParams.get("pack");

  // Data state
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");

  // Detail slide-over state
  const [detailName, setDetailName] = useState<string | null>(null);
  const [detail, setDetail] = useState<TemplateDetailData | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Run modal state
  const [runModalOpen, setRunModalOpen] = useState(false);
  const [runInput, setRunInput] = useState("{}");
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});
  const [running, setRunning] = useState(false);

  // Install modal state
  const [installPack, setInstallPack] = useState<TemplatePack | null>(null);
  const [installTemplates, setInstallTemplates] = useState<string[]>([]);

  // Pack detail - selected templates for selective install
  const [packSelected, setPackSelected] = useState<Record<string, boolean>>({});

  // Tag filter for "All" view
  const [selectedTag, setSelectedTag] = useState<string | null>(null);

  // Integrations view - expanded tools
  const [expandedTools, setExpandedTools] = useState<Set<string>>(new Set());

  // Community view state
  const [communityTemplates, setCommunityTemplates] = useState<CommunityTemplate[]>([]);
  const [communityStats, setCommunityStats] = useState<{ total_templates: number; total_authors: number } | null>(null);
  const [communityLoading, setCommunityLoading] = useState(false);
  const [communityError, setCommunityError] = useState(false);
  const [communitySearch, setCommunitySearch] = useState("");
  const [communityInstallTarget, setCommunityInstallTarget] = useState<CommunityTemplate | null>(null);
  const [communityPreviewTarget, setCommunityPreviewTarget] = useState<CommunityTemplate | null>(null);
  const [communityInstallYaml, setCommunityInstallYaml] = useState<string | null>(null);
  const [communityInstallLoading, setCommunityInstallLoading] = useState(false);

  // ---- Fetch templates ----

  useEffect(() => {
    api
      .get<Template[]>("/templates")
      .then((res) => {
        setTemplates(res.data || []);
      })
      .finally(() => setLoading(false));
  }, []);

  // ---- Fetch community templates when tab is active ----

  useEffect(() => {
    if (viewParam !== "community") return;
    if (communityTemplates.length > 0 || communityLoading) return;
    setCommunityLoading(true);
    setCommunityError(false);
    api
      .get<CommunityRegistryResponse>("/hub/registry")
      .then((res) => {
        if (res.data) {
          setCommunityTemplates(res.data.templates || []);
          setCommunityStats(res.data.stats || null);
        } else {
          setCommunityError(true);
        }
      })
      .catch(() => setCommunityError(true))
      .finally(() => setCommunityLoading(false));
  }, [viewParam, communityTemplates.length, communityLoading]);

  // ---- Derived data ----

  const templatesWithCategory = useMemo(
    () =>
      templates.map((t) => ({
        ...t,
        category: t.category || resolveCategory(null, t.tags),
      })),
    [templates]
  );

  const packCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const pack of TEMPLATE_PACKS) {
      counts[pack.id] = templatesWithCategory.filter(
        (t) => t.category === pack.id
      ).length;
    }
    return counts;
  }, [templatesWithCategory]);

  const featuredPack = useMemo(() => getFeaturedPack(), []);
  const nonFeaturedPacks = useMemo(
    () => TEMPLATE_PACKS.filter((p) => !p.featured),
    []
  );

  // Pack detail: templates belonging to the selected pack
  const packDetailTemplates = useMemo(() => {
    if (!selectedPackId) return [];
    const pack = getPackById(selectedPackId);
    if (!pack) return [];
    // Try to match by explicit templates list first, fall back to category
    return pack.templates
      .map((name) => templatesWithCategory.find((t) => t.name === name))
      .filter(Boolean) as Template[];
  }, [selectedPackId, templatesWithCategory]);

  // All view: filtered templates
  const allFiltered = useMemo(() => {
    let result = templatesWithCategory;
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
      result = result.filter((t) => {
        // Filter by pack category
        const pack = TEMPLATE_PACKS.find((p) => p.id === selectedTag);
        if (pack) return t.category === pack.id;
        return false;
      });
    }
    return result;
  }, [templatesWithCategory, search, selectedTag]);

  // Community view: filtered templates
  const communityFiltered = useMemo(() => {
    if (!communitySearch) return communityTemplates;
    const q = communitySearch.toLowerCase();
    return communityTemplates.filter(
      (t) =>
        t.name.toLowerCase().includes(q) ||
        t.description.toLowerCase().includes(q) ||
        t.author.toLowerCase().includes(q) ||
        t.tags.some((tag) => tag.toLowerCase().includes(q))
    );
  }, [communityTemplates, communitySearch]);

  // Integrations view: group templates by tool
  const integrationGroups = useMemo(() => {
    const groups: Record<string, { tool: string; templates: Template[] }> = {};
    for (const pack of TEMPLATE_PACKS) {
      for (const tool of pack.tools) {
        if (!groups[tool]) {
          groups[tool] = { tool, templates: [] };
        }
        // Add templates from this pack that belong to this tool
        for (const tplName of pack.templates) {
          const tpl = templatesWithCategory.find((t) => t.name === tplName);
          if (tpl && !groups[tool].templates.some((t) => t.name === tpl.name)) {
            groups[tool].templates.push(tpl);
          }
        }
      }
    }
    // Sort by template count descending
    return Object.values(groups).sort(
      (a, b) => b.templates.length - a.templates.length
    );
  }, [templatesWithCategory]);

  // ---- Navigation helpers ----

  const setView = useCallback(
    (view: ViewMode) => {
      setSearch("");
      setSelectedTag(null);
      setSearchParams({ view });
    },
    [setSearchParams]
  );

  const openPackDetail = useCallback(
    (packId: string) => {
      const pack = getPackById(packId);
      if (!pack) return;
      // Initialize all templates as selected
      const sel: Record<string, boolean> = {};
      pack.templates.forEach((name) => {
        sel[name] = true;
      });
      setPackSelected(sel);
      setSearchParams({ view: "packs", pack: packId });
    },
    [setSearchParams]
  );

  const closePackDetail = useCallback(() => {
    setSearchParams({ view: "packs" });
  }, [setSearchParams]);

  // ---- Detail slide-over ----

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
        depends_on: Array.isArray(s.depends_on)
          ? s.depends_on.map(String)
          : undefined,
        prompt: s.prompt ? String(s.prompt) : undefined,
      }));
      navigate("/workflows/builder", {
        state: {
          workflow: {
            name: String(parsed.name ?? detail.name),
            description: String(
              parsed.description ?? detail.description ?? ""
            ),
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

  // ---- Install flow ----

  const handleInstallPack = useCallback(
    (pack: TemplatePack, selectedNames?: string[]) => {
      const names = selectedNames || pack.templates;
      setInstallPack(pack);
      setInstallTemplates(names);
    },
    []
  );

  const handleInstallComplete = useCallback(() => {
    setInstallPack(null);
    setInstallTemplates([]);
  }, []);

  const handleInstallClose = useCallback(() => {
    setInstallPack(null);
    setInstallTemplates([]);
  }, []);

  // Pack detail: toggle template selection
  const togglePackTemplate = useCallback((name: string) => {
    setPackSelected((prev) => ({ ...prev, [name]: !prev[name] }));
  }, []);

  const selectedPackTemplateCount = useMemo(
    () => Object.values(packSelected).filter(Boolean).length,
    [packSelected]
  );

  // Integration view: toggle tool expansion
  const toggleToolExpand = useCallback((tool: string) => {
    setExpandedTools((prev) => {
      const next = new Set(prev);
      if (next.has(tool)) {
        next.delete(tool);
      } else {
        next.add(tool);
      }
      return next;
    });
  }, []);

  // ---- Community install flow ----

  const handleCommunityInstall = useCallback((tpl: CommunityTemplate) => {
    setCommunityInstallTarget(tpl);
    setCommunityInstallYaml(null);
  }, []);

  const handleCommunityPreview = useCallback((tpl: CommunityTemplate) => {
    setCommunityPreviewTarget(tpl);
    setCommunityInstallYaml(null);
    // Fetch the YAML for preview
    setCommunityInstallLoading(true);
    fetch(tpl.download_url)
      .then((res) => (res.ok ? res.text() : Promise.reject(new Error("fetch failed"))))
      .then((yaml) => setCommunityInstallYaml(yaml))
      .catch(() => {
        // Fallback: show a placeholder
        setCommunityInstallYaml(`# Could not fetch YAML from ${tpl.download_url}\n# Use the CLI instead:\n# sandcastle hub install ${tpl.slug}`);
      })
      .finally(() => setCommunityInstallLoading(false));
  }, []);

  const handleCommunityFetchAndInstall = useCallback(() => {
    if (!communityInstallTarget) return;
    setCommunityInstallLoading(true);
    fetch(communityInstallTarget.download_url)
      .then((res) => (res.ok ? res.text() : Promise.reject(new Error("fetch failed"))))
      .then((yaml) => {
        setCommunityInstallYaml(yaml);
      })
      .catch(() => {
        setCommunityInstallYaml(`# Could not fetch YAML from ${communityInstallTarget.download_url}\n# Use the CLI instead:\n# sandcastle hub install ${communityInstallTarget.slug}`);
      })
      .finally(() => setCommunityInstallLoading(false));
  }, [communityInstallTarget]);

  const handleCommunityOpenInBuilder = useCallback(() => {
    const tpl = communityPreviewTarget || communityInstallTarget;
    if (!communityInstallYaml || !tpl) return;
    try {
      const parsed = jsYaml.load(communityInstallYaml) as Record<string, unknown>;
      const rawSteps = (parsed.steps ?? []) as Array<Record<string, unknown>>;
      const steps = rawSteps.map((s) => ({
        id: String(s.id ?? ""),
        model: s.model ? String(s.model) : undefined,
        depends_on: Array.isArray(s.depends_on)
          ? s.depends_on.map(String)
          : undefined,
        prompt: s.prompt ? String(s.prompt) : undefined,
      }));
      navigate("/workflows/builder", {
        state: {
          workflow: {
            name: String(parsed.name ?? tpl.name),
            description: String(parsed.description ?? tpl.description ?? ""),
            steps_count: steps.length,
            file_name: `${tpl.slug}.yaml`,
            steps,
            yaml_content: communityInstallYaml,
          },
        },
      });
    } catch {
      navigate("/workflows/builder", { state: { yaml: communityInstallYaml } });
    }
    setCommunityInstallTarget(null);
    setCommunityPreviewTarget(null);
    setCommunityInstallYaml(null);
  }, [communityInstallYaml, communityInstallTarget, communityPreviewTarget, navigate]);

  const handleCommunityDownloadYaml = useCallback(() => {
    const tpl = communityPreviewTarget || communityInstallTarget;
    if (!communityInstallYaml || !tpl) return;
    const blob = new Blob([communityInstallYaml], { type: "text/yaml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${tpl.slug}.yaml`;
    a.click();
    URL.revokeObjectURL(url);
  }, [communityInstallYaml, communityInstallTarget, communityPreviewTarget]);

  const closeCommunityModals = useCallback(() => {
    setCommunityInstallTarget(null);
    setCommunityPreviewTarget(null);
    setCommunityInstallYaml(null);
    setCommunityInstallLoading(false);
  }, []);

  const handleCommunityRetry = useCallback(() => {
    setCommunityError(false);
    setCommunityLoading(true);
    setCommunityTemplates([]);
    api
      .get<CommunityRegistryResponse>("/hub/registry")
      .then((res) => {
        if (res.data) {
          setCommunityTemplates(res.data.templates || []);
          setCommunityStats(res.data.stats || null);
        } else {
          setCommunityError(true);
        }
      })
      .catch(() => setCommunityError(true))
      .finally(() => setCommunityLoading(false));
  }, []);

  // ---- Render ----

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  const currentPack = selectedPackId ? getPackById(selectedPackId) : null;
  const isPackDetail = viewParam === "packs" && !!currentPack;

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
          <span className="text-sm text-muted font-data">
            {templates.length} templates
          </span>
        </div>
      </div>

      {/* Breadcrumb for pack detail */}
      {isPackDetail && (
        <div className="flex items-center gap-1.5 text-sm">
          <button
            onClick={closePackDetail}
            className="text-accent hover:text-accent-hover transition-colors font-medium"
          >
            Template Hub
          </button>
          <ChevronRight className="h-3.5 w-3.5 text-muted" />
          <span className="text-foreground font-medium">
            {currentPack!.name}
          </span>
        </div>
      )}

      {/* Three-view segmented control */}
      {!isPackDetail && (
        <div className="flex items-center gap-1 rounded-lg bg-background border border-border p-1">
          {VIEW_TABS.map((tab) => {
            const TabIcon = tab.icon;
            const isActive = viewParam === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setView(tab.id)}
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-all duration-150",
                  isActive
                    ? "bg-surface text-foreground shadow-sm"
                    : "text-muted hover:text-foreground"
                )}
              >
                <TabIcon className="h-3.5 w-3.5" />
                {tab.label}
              </button>
            );
          })}
        </div>
      )}

      {/* Search bar - shown in all views except pack detail */}
      {!isPackDetail && (
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
      )}

      {/* ---- PACK DETAIL VIEW ---- */}
      {isPackDetail && currentPack && (
        <PackDetailView
          pack={currentPack}
          templates={packDetailTemplates}
          packSelected={packSelected}
          selectedCount={selectedPackTemplateCount}
          onToggle={togglePackTemplate}
          onPreview={openDetail}
          onAdd={openDetail}
          onInstallSelected={() => {
            const selected = Object.entries(packSelected)
              .filter(([, v]) => v)
              .map(([k]) => k);
            handleInstallPack(currentPack, selected);
          }}
        />
      )}

      {/* ---- PACKS VIEW ---- */}
      {viewParam === "packs" && !isPackDetail && (
        <PacksView
          featuredPack={featuredPack}
          nonFeaturedPacks={nonFeaturedPacks}
          packCounts={packCounts}
          templatesWithCategory={templatesWithCategory}
          search={search}
          onSelectPack={openPackDetail}
          onInstallPack={handleInstallPack}
          onOpenDetail={openDetail}
        />
      )}

      {/* ---- ALL TEMPLATES VIEW ---- */}
      {viewParam === "all" && (
        <AllTemplatesView
          templates={allFiltered}
          search={search}
          selectedTag={selectedTag}
          onTagSelect={setSelectedTag}
          onReset={() => {
            setSearch("");
            setSelectedTag(null);
          }}
          onOpenDetail={openDetail}
          detailName={detailName}
        />
      )}

      {/* ---- BY INTEGRATION VIEW ---- */}
      {viewParam === "integrations" && (
        <IntegrationView
          groups={integrationGroups}
          expandedTools={expandedTools}
          onToggleTool={toggleToolExpand}
          onOpenDetail={openDetail}
        />
      )}

      {/* ---- COMMUNITY VIEW ---- */}
      {viewParam === "community" && (
        <div className="space-y-6">
          {/* Community header */}
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-base font-semibold text-foreground flex items-center gap-2">
                <Globe className="h-4 w-4 text-accent" />
                Community Workflows
              </h2>
              {communityStats && (
                <p className="text-sm text-muted mt-0.5">
                  <span className="font-data">{communityStats.total_templates}</span> workflows from{" "}
                  <span className="font-data">{communityStats.total_authors}</span> author{communityStats.total_authors !== 1 ? "s" : ""}
                </p>
              )}
            </div>
            <a
              href="https://github.com/gizmax/Sandcastle/tree/main/hub"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-muted hover:text-foreground transition-colors"
            >
              <Plus className="h-3.5 w-3.5" />
              Submit Workflow
            </a>
          </div>

          {/* Community search */}
          <div className="relative">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" />
            <input
              type="text"
              value={communitySearch}
              onChange={(e) => setCommunitySearch(e.target.value)}
              placeholder="Search community workflows..."
              className={cn(
                "h-9 w-full rounded-lg border border-border bg-background pl-9 pr-3 text-sm",
                "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30"
              )}
            />
          </div>

          {/* Loading / Error / Content */}
          {communityLoading ? (
            <div className="flex h-48 items-center justify-center">
              <LoadingSpinner size="lg" />
            </div>
          ) : communityError ? (
            <EmptyState
              icon={Globe}
              title="Could not load community workflows"
              description="Check your connection and try again."
              action={{ label: "Retry", onClick: handleCommunityRetry }}
            />
          ) : communityFiltered.length === 0 ? (
            <EmptyState
              icon={Globe}
              title={communitySearch ? "No matching workflows" : "No community workflows yet"}
              description={communitySearch ? "Try adjusting your search." : "Be the first to contribute!"}
              action={
                communitySearch
                  ? { label: "Clear search", onClick: () => setCommunitySearch("") }
                  : { label: "Submit a workflow", onClick: () => window.open("https://github.com/gizmax/Sandcastle/tree/main/hub", "_blank") }
              }
            />
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {communityFiltered.map((tpl) => (
                <CommunityCard
                  key={tpl.slug}
                  template={tpl}
                  onInstall={() => handleCommunityInstall(tpl)}
                  onPreview={() => handleCommunityPreview(tpl)}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Community install modal */}
      {communityInstallTarget && (
        <>
          <div className="fixed inset-0 z-[60] bg-black/40" onClick={closeCommunityModals} />
          <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
            <div className="w-full max-w-md rounded-xl border border-border bg-surface p-6 shadow-xl">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-foreground">
                  Install {communityInstallTarget.name}
                </h2>
                <button onClick={closeCommunityModals} className="rounded-lg p-1 text-muted hover:text-foreground">
                  <X className="h-5 w-5" />
                </button>
              </div>

              {communityInstallYaml ? (
                <div className="space-y-4">
                  <pre className="max-h-48 overflow-auto rounded-lg bg-background p-3 font-mono text-xs text-foreground whitespace-pre-wrap">
                    {communityInstallYaml.slice(0, 2000)}
                    {communityInstallYaml.length > 2000 && "\n..."}
                  </pre>
                  <div className="flex gap-2">
                    <button
                      onClick={handleCommunityOpenInBuilder}
                      className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground hover:bg-accent-hover transition-all shadow-sm"
                    >
                      <ArrowRight className="h-4 w-4" />
                      Open in Builder
                    </button>
                    <button
                      onClick={handleCommunityDownloadYaml}
                      className="flex flex-1 items-center justify-center gap-2 rounded-lg border border-border px-4 py-2 text-sm font-medium text-foreground hover:bg-border/40 transition-colors"
                    >
                      <Download className="h-4 w-4" />
                      Download
                    </button>
                  </div>
                </div>
              ) : communityInstallLoading ? (
                <div className="flex h-24 items-center justify-center">
                  <LoadingSpinner size="md" />
                </div>
              ) : (
                <div className="space-y-4">
                  <p className="text-sm text-muted">
                    Install this workflow from the community hub?
                  </p>
                  <div className="rounded-lg bg-background p-3">
                    <p className="text-xs text-muted mb-1">CLI command:</p>
                    <code className="text-sm font-mono text-accent">
                      sandcastle hub install {communityInstallTarget.slug}
                    </code>
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={handleCommunityFetchAndInstall}
                      className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground hover:bg-accent-hover transition-all shadow-sm"
                    >
                      <Download className="h-4 w-4" />
                      Fetch and Preview
                    </button>
                    <button
                      onClick={closeCommunityModals}
                      className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-muted hover:text-foreground transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </>
      )}

      {/* Community preview modal */}
      {communityPreviewTarget && !communityInstallTarget && (
        <>
          <div className="fixed inset-0 z-[60] bg-black/40" onClick={closeCommunityModals} />
          <div className="fixed inset-y-0 right-0 z-[60] flex w-full max-w-lg flex-col border-l border-border bg-surface shadow-xl">
            <div className="flex items-center justify-between border-b border-border px-5 py-4">
              <h2 className="text-lg font-semibold text-foreground">{communityPreviewTarget.name}</h2>
              <button onClick={closeCommunityModals} className="rounded-lg p-1 text-muted hover:text-foreground">
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-5 space-y-4">
              <p className="text-sm text-muted">{communityPreviewTarget.description}</p>
              <div className="flex flex-wrap gap-1.5">
                {communityPreviewTarget.tags.map((tag) => (
                  <span key={tag} className="rounded-full bg-border/40 px-2 py-0.5 text-xs text-muted">{tag}</span>
                ))}
                <span className="rounded-full bg-border/40 px-2.5 py-0.5 text-xs font-medium text-muted font-data">
                  {communityPreviewTarget.step_count} steps
                </span>
              </div>
              <div className="text-xs text-muted-foreground">
                by <a href={communityPreviewTarget.author_url} target="_blank" rel="noopener noreferrer" className="text-accent hover:text-accent-hover">@{communityPreviewTarget.author}</a>
              </div>
              {communityInstallLoading ? (
                <div className="flex h-24 items-center justify-center"><LoadingSpinner size="md" /></div>
              ) : communityInstallYaml ? (
                <div>
                  <p className="mb-2 text-xs font-medium text-muted">YAML Definition</p>
                  <pre className="max-h-96 overflow-auto rounded-lg bg-background p-4 font-mono text-xs text-foreground whitespace-pre-wrap">{communityInstallYaml}</pre>
                </div>
              ) : null}
            </div>
            <div className="flex items-center gap-2 border-t border-border px-5 py-4">
              <button
                onClick={() => {
                  setCommunityInstallTarget(communityPreviewTarget);
                  setCommunityPreviewTarget(null);
                }}
                className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground hover:bg-accent-hover transition-all shadow-sm"
              >
                <Download className="h-4 w-4" />
                Install
              </button>
              {communityInstallYaml && (
                <button
                  onClick={handleCommunityOpenInBuilder}
                  className="flex flex-1 items-center justify-center gap-2 rounded-lg border border-border px-4 py-2 text-sm font-medium text-foreground hover:bg-border/40 transition-colors"
                >
                  <ArrowRight className="h-4 w-4" />
                  Open in Builder
                </button>
              )}
            </div>
          </div>
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
          onFieldChange={(key, value) =>
            setFieldValues((prev) => ({ ...prev, [key]: value }))
          }
          runInput={runInput}
          onRunInputChange={setRunInput}
        />
      )}

      {/* Install modal */}
      {installPack && installTemplates.length > 0 && (
        <InstallModal
          pack={installPack}
          selectedTemplates={installTemplates}
          onClose={handleInstallClose}
          onComplete={handleInstallComplete}
        />
      )}
    </div>
  );
}

// ---- Sub-components ----

// Hero Pack card for the featured pack
function HeroPack({
  pack,
  templates,
  onBrowse,
  onInstall,
}: {
  pack: TemplatePack;
  templates: Template[];
  onBrowse: () => void;
  onInstall: () => void;
}) {
  const Icon = pack.icon;

  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-xl border p-6",
        "bg-surface glow-accent",
        pack.color.border
      )}
    >
      {/* Dot pattern on right */}
      <div className="absolute right-0 top-0 h-full w-1/3 bg-grid opacity-[0.08] pointer-events-none" />

      <div className="relative flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        {/* Left side */}
        <div className="space-y-3 max-w-xl">
          <div className="flex items-center gap-3">
            <div
              className={cn(
                "flex h-12 w-12 items-center justify-center rounded-xl",
                pack.color.bg
              )}
            >
              <Icon className={cn("h-6 w-6", pack.color.text)} />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-lg font-semibold text-foreground">
                  {pack.name}
                </h2>
                <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[10px] font-semibold text-accent uppercase tracking-wide">
                  Featured
                </span>
              </div>
              <p className="text-sm text-muted">{pack.tagline}</p>
            </div>
          </div>

          {/* Template chips */}
          <div className="flex flex-wrap gap-1.5">
            {pack.templates.slice(0, 5).map((name) => {
              const tpl = templates.find((t) => t.name === name);
              return (
                <span
                  key={name}
                  className="inline-flex items-center gap-1 rounded-md bg-border/40 px-2 py-1 text-[11px] text-muted-foreground"
                >
                  {name.replace(/_/g, " ")}
                  {tpl && (
                    <span className="text-[10px] text-muted font-data">
                      {tpl.step_count}s
                    </span>
                  )}
                </span>
              );
            })}
            {pack.templates.length > 5 && (
              <span className="inline-flex items-center rounded-md bg-border/40 px-2 py-1 text-[11px] text-muted">
                +{pack.templates.length - 5} more
              </span>
            )}
          </div>

          {/* Tool icons */}
          <div className="flex items-center gap-1.5">
            {pack.tools.map((tool) => {
              const ToolIcon = TOOL_ICON_MAP[tool];
              if (!ToolIcon) return null;
              return (
                <div
                  key={tool}
                  className="flex h-7 w-7 items-center justify-center rounded-md bg-border/40"
                  title={tool}
                >
                  <ToolIcon className="h-3.5 w-3.5 text-muted-foreground" />
                </div>
              );
            })}
          </div>
        </div>

        {/* Right side - buttons */}
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={(e) => {
              e.stopPropagation();
              onInstall();
            }}
            className={cn(
              "flex items-center gap-2 rounded-lg bg-accent px-4 py-2.5",
              "text-sm font-medium text-accent-foreground",
              "hover:bg-accent-hover transition-all duration-200 shadow-sm"
            )}
          >
            <Download className="h-4 w-4" />
            Install Entire Pack
          </button>
          <button
            onClick={onBrowse}
            className={cn(
              "flex items-center gap-2 rounded-lg border border-border px-4 py-2.5",
              "text-sm font-medium text-foreground",
              "hover:bg-border/40 transition-colors"
            )}
          >
            Browse Templates
          </button>
        </div>
      </div>
    </div>
  );
}

// Packs view - hero + grid
function PacksView({
  featuredPack,
  nonFeaturedPacks,
  packCounts,
  templatesWithCategory,
  search,
  onSelectPack,
  onInstallPack,
  onOpenDetail,
}: {
  featuredPack: TemplatePack;
  nonFeaturedPacks: TemplatePack[];
  packCounts: Record<string, number>;
  templatesWithCategory: Template[];
  search: string;
  onSelectPack: (packId: string) => void;
  onInstallPack: (pack: TemplatePack) => void;
  onOpenDetail: (name: string) => void;
}) {
  // If searching, show filtered results instead of packs
  const searchResults = useMemo(() => {
    if (!search) return null;
    const q = search.toLowerCase();
    return templatesWithCategory.filter(
      (t) =>
        t.name.toLowerCase().includes(q) ||
        t.description.toLowerCase().includes(q) ||
        t.tags.some((tag) => tag.toLowerCase().includes(q))
    );
  }, [search, templatesWithCategory]);

  if (searchResults) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-muted">
          {searchResults.length} result{searchResults.length !== 1 ? "s" : ""}{" "}
          for "{search}"
        </p>
        {searchResults.length === 0 ? (
          <EmptyState
            icon={Layers}
            title="No templates found"
            description="Try adjusting your search query."
          />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {searchResults.map((t) => (
              <TemplateCard
                key={t.name}
                template={t}
                isSelected={false}
                onClick={() => onOpenDetail(t.name)}
              />
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <>
      {/* Hero pack */}
      <HeroPack
        pack={featuredPack}
        templates={templatesWithCategory}
        onBrowse={() => onSelectPack(featuredPack.id)}
        onInstall={() => onInstallPack(featuredPack)}
      />

      {/* Pack grid */}
      <div className="space-y-3">
        <h2 className="text-sm font-semibold text-foreground">
          Browse by Category
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {nonFeaturedPacks.map((pack) => (
            <PackCard
              key={pack.id}
              pack={pack}
              count={packCounts[pack.id] || 0}
              onClick={() => onSelectPack(pack.id)}
              onInstall={() => onInstallPack(pack)}
            />
          ))}
        </div>
      </div>
    </>
  );
}

// Pack detail view - full template list with checkboxes
function PackDetailView({
  pack,
  templates,
  packSelected,
  selectedCount,
  onToggle,
  onPreview,
  onAdd,
  onInstallSelected,
}: {
  pack: TemplatePack;
  templates: Template[];
  packSelected: Record<string, boolean>;
  selectedCount: number;
  onToggle: (name: string) => void;
  onPreview: (name: string) => void;
  onAdd: (name: string) => void;
  onInstallSelected: () => void;
}) {
  const Icon = pack.icon;

  return (
    <div className="space-y-6">
      {/* Pack header banner */}
      <div
        className={cn(
          "relative overflow-hidden rounded-xl border p-5",
          "bg-surface",
          pack.color.border
        )}
      >
        <div className="absolute right-0 top-0 h-full w-1/4 bg-grid opacity-[0.06] pointer-events-none" />
        <div className="relative flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div
              className={cn(
                "flex h-10 w-10 items-center justify-center rounded-lg",
                pack.color.bg
              )}
            >
              <Icon className={cn("h-5 w-5", pack.color.text)} />
            </div>
            <div>
              <h2 className="text-base font-semibold text-foreground">
                {pack.name}
              </h2>
              <p className="text-sm text-muted">{pack.tagline}</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1.5">
              {pack.tools.map((tool) => {
                const ToolIcon = TOOL_ICON_MAP[tool];
                if (!ToolIcon) return null;
                return (
                  <div
                    key={tool}
                    className="flex h-6 w-6 items-center justify-center rounded bg-border/40"
                    title={tool}
                  >
                    <ToolIcon className="h-3.5 w-3.5 text-muted-foreground" />
                  </div>
                );
              })}
            </div>
            <button
              onClick={onInstallSelected}
              disabled={selectedCount === 0}
              className={cn(
                "flex items-center gap-2 rounded-lg bg-accent px-4 py-2",
                "text-sm font-medium text-accent-foreground",
                "hover:bg-accent-hover transition-all duration-200 shadow-sm",
                selectedCount === 0 && "opacity-50 cursor-not-allowed"
              )}
            >
              <Download className="h-4 w-4" />
              Install All
            </button>
          </div>
        </div>
      </div>

      {/* What's included */}
      <div className="space-y-3">
        <h3 className="text-xs font-semibold text-muted uppercase tracking-wider">
          What's Included
        </h3>
        <div className="rounded-xl border border-border bg-surface overflow-hidden">
          {templates.length === 0 ? (
            <div className="px-5 py-8 text-center text-sm text-muted">
              No templates loaded for this pack yet.
            </div>
          ) : (
            <div className="divide-y divide-border">
              {templates.map((tpl) => {
                const checked = packSelected[tpl.name] ?? true;
                return (
                  <div
                    key={tpl.name}
                    className={cn(
                      "flex items-center gap-4 px-5 py-3 transition-colors",
                      "hover:bg-border/20"
                    )}
                  >
                    {/* Checkbox */}
                    <button
                      type="button"
                      onClick={() => onToggle(tpl.name)}
                      className={cn(
                        "flex h-5 w-5 shrink-0 items-center justify-center rounded border transition-all",
                        checked
                          ? "border-accent bg-accent"
                          : "border-border bg-background hover:border-muted-foreground"
                      )}
                    >
                      {checked && (
                        <Check className="h-3 w-3 text-accent-foreground" />
                      )}
                    </button>

                    {/* Template info */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-foreground">
                          {tpl.name.replace(/_/g, " ")}
                        </span>
                        <span className="text-[11px] text-muted font-data">
                          {tpl.step_count}{" "}
                          {tpl.step_count === 1 ? "step" : "steps"}
                        </span>
                      </div>
                      <p className="text-xs text-muted line-clamp-1 mt-0.5">
                        {tpl.description}
                      </p>
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-1.5 shrink-0">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onPreview(tpl.name);
                        }}
                        className={cn(
                          "flex items-center gap-1 rounded-md border border-border px-2 py-1",
                          "text-[11px] font-medium text-muted-foreground",
                          "hover:text-foreground hover:bg-border/40 transition-colors"
                        )}
                      >
                        <Eye className="h-3 w-3" />
                        Preview
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onAdd(tpl.name);
                        }}
                        className={cn(
                          "flex items-center gap-1 rounded-md border border-border px-2 py-1",
                          "text-[11px] font-medium text-muted-foreground",
                          "hover:text-foreground hover:border-accent/40 hover:bg-accent/5 transition-colors"
                        )}
                      >
                        <Plus className="h-3 w-3" />
                        Add
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Sticky bottom bar */}
      {templates.length > 0 && (
        <div className="sticky bottom-0 -mx-6 mt-4 border-t border-border bg-surface/95 backdrop-blur-sm px-6 py-3">
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted">
              Selected:{" "}
              <span className="font-medium text-foreground font-data">
                {selectedCount}
              </span>{" "}
              of{" "}
              <span className="font-data">{templates.length}</span>
            </span>
            <button
              onClick={onInstallSelected}
              disabled={selectedCount === 0}
              className={cn(
                "flex items-center gap-2 rounded-lg bg-accent px-4 py-2",
                "text-sm font-medium text-accent-foreground",
                "hover:bg-accent-hover transition-all duration-200 shadow-sm",
                selectedCount === 0 && "opacity-50 cursor-not-allowed"
              )}
            >
              <Download className="h-4 w-4" />
              Install Selected ({selectedCount})
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// All Templates flat view with category pills
function AllTemplatesView({
  templates,
  search,
  selectedTag,
  onTagSelect,
  onReset,
  onOpenDetail,
  detailName,
}: {
  templates: Template[];
  search: string;
  selectedTag: string | null;
  onTagSelect: (tag: string | null) => void;
  onReset: () => void;
  onOpenDetail: (name: string) => void;
  detailName: string | null;
}) {
  return (
    <>
      {/* Category filter pills */}
      <div className="flex flex-wrap gap-1.5">
        <button
          onClick={() => onTagSelect(null)}
          className={cn(
            "rounded-full px-2.5 py-1 text-xs font-medium transition-colors",
            !selectedTag
              ? "bg-accent text-accent-foreground"
              : "bg-border/40 text-muted hover:text-foreground"
          )}
        >
          All
        </button>
        {TEMPLATE_PACKS.map((pack) => {
          const PackIcon = pack.icon;
          return (
            <button
              key={pack.id}
              onClick={() =>
                onTagSelect(selectedTag === pack.id ? null : pack.id)
              }
              className={cn(
                "flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium transition-colors",
                selectedTag === pack.id
                  ? cn(pack.color.bg, pack.color.text)
                  : "bg-border/40 text-muted hover:text-foreground"
              )}
            >
              <PackIcon className="h-3 w-3" />
              {pack.name}
            </button>
          );
        })}
      </div>

      {/* Template grid */}
      {templates.length === 0 ? (
        <EmptyState
          icon={Layers}
          title="No templates found"
          description="Try adjusting your search or category filter."
          action={
            search || selectedTag
              ? { label: "Reset filters", onClick: onReset }
              : undefined
          }
        />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {templates.map((t) => (
            <TemplateCard
              key={t.name}
              template={t}
              isSelected={detailName === t.name}
              onClick={() => onOpenDetail(t.name)}
            />
          ))}
        </div>
      )}
    </>
  );
}

// By Integration view - grouped by tools
function IntegrationView({
  groups,
  expandedTools,
  onToggleTool,
  onOpenDetail,
}: {
  groups: { tool: string; templates: Template[] }[];
  expandedTools: Set<string>;
  onToggleTool: (tool: string) => void;
  onOpenDetail: (name: string) => void;
}) {
  if (groups.length === 0) {
    return (
      <EmptyState
        icon={Plug}
        title="No integrations found"
        description="Template packs with tool integrations will appear here."
      />
    );
  }

  return (
    <div className="space-y-2">
      {groups.map(({ tool, templates }) => {
        const ToolIcon = TOOL_ICON_MAP[tool];
        const isExpanded = expandedTools.has(tool);

        return (
          <div
            key={tool}
            className="rounded-xl border border-border bg-surface overflow-hidden"
          >
            {/* Tool header */}
            <button
              type="button"
              onClick={() => onToggleTool(tool)}
              className={cn(
                "flex w-full items-center justify-between px-5 py-3.5",
                "hover:bg-border/20 transition-colors"
              )}
            >
              <div className="flex items-center gap-3">
                {ToolIcon && (
                  <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-border/40">
                    <ToolIcon className="h-4 w-4 text-muted-foreground" />
                  </div>
                )}
                <div className="text-left">
                  <span className="text-sm font-medium text-foreground capitalize">
                    {tool}
                  </span>
                  <span className="ml-2 text-xs text-muted font-data">
                    {templates.length}{" "}
                    {templates.length === 1 ? "template" : "templates"}
                  </span>
                </div>
              </div>
              {isExpanded ? (
                <ChevronUp className="h-4 w-4 text-muted" />
              ) : (
                <ChevronDown className="h-4 w-4 text-muted" />
              )}
            </button>

            {/* Expanded template list */}
            {isExpanded && (
              <div className="border-t border-border divide-y divide-border/60">
                {templates.map((tpl) => (
                  <button
                    key={tpl.name}
                    type="button"
                    onClick={() => onOpenDetail(tpl.name)}
                    className={cn(
                      "flex w-full items-center justify-between px-5 py-2.5 text-left",
                      "hover:bg-border/20 transition-colors"
                    )}
                  >
                    <div className="min-w-0">
                      <span className="text-sm text-foreground">
                        {tpl.name.replace(/_/g, " ")}
                      </span>
                      <p className="text-xs text-muted line-clamp-1">
                        {tpl.description}
                      </p>
                    </div>
                    <span className="text-[11px] text-muted shrink-0 ml-3 font-data">
                      {tpl.step_count}{" "}
                      {tpl.step_count === 1 ? "step" : "steps"}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
