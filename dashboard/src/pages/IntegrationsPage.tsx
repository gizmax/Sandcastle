import { useCallback, useEffect, useState } from "react";
import { Search, CheckCircle2, AlertTriangle } from "lucide-react";
import { api } from "@/api/client";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { ToolConfigPanel } from "@/components/integrations/ToolConfigPanel";
import { TOOL_ICON_MAP, CATEGORY_COLORS, CATEGORY_LABELS } from "@/components/integrations/toolIcons";
import { cn } from "@/lib/utils";

interface Tool {
  name: string;
  description: string;
  category: string;
  icon: string;
  configured: boolean;
  missing_credentials: string[];
  credential_env_vars: string[];
  functions: { name: string; description: string }[];
}

const CATEGORIES = ["all", "communication", "project_management", "crm", "data", "general"] as const;
type Category = (typeof CATEGORIES)[number];

const CATEGORY_TAB_LABELS: Record<Category, string> = {
  all: "All",
  communication: "Communication",
  project_management: "Project Mgmt",
  crm: "CRM",
  data: "Data",
  general: "General",
};

export default function IntegrationsPage() {
  const [tools, setTools] = useState<Tool[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<Category>("all");
  const [selectedTool, setSelectedTool] = useState<Tool | null>(null);

  const fetchTools = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get<{ tools: Tool[]; total: number }>("/tools");
      if (res.error) {
        setError(res.error.message);
      } else if (res.data) {
        setTools(res.data.tools);
      }
    } catch {
      setError("Failed to load tools");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTools();
  }, [fetchTools]);

  const filtered = tools.filter((t) => {
    if (category !== "all" && t.category !== category) return false;
    if (search) {
      const q = search.toLowerCase();
      return (
        t.name.toLowerCase().includes(q) ||
        t.description.toLowerCase().includes(q)
      );
    }
    return true;
  });

  const configuredCount = tools.filter((t) => t.configured).length;

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-3">
        <AlertTriangle className="h-8 w-8 text-amber-500" />
        <p className="text-muted">{error}</p>
        <button
          onClick={fetchTools}
          className="text-sm font-medium text-accent hover:text-accent/80 transition-colors"
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-foreground">Integrations</h1>
        <p className="mt-1 text-sm text-muted">
          {configuredCount} of {tools.length} tools configured
        </p>
      </div>

      {/* Search + Category tabs */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative max-w-xs w-full">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search tools..."
            className={cn(
              "w-full rounded-lg border border-border bg-background pl-9 pr-3 py-2 text-sm text-foreground",
              "placeholder:text-muted-foreground/50",
              "focus:outline-none focus:ring-2 focus:ring-accent/40 focus:border-accent",
              "transition-colors"
            )}
          />
        </div>
        <div className="flex gap-1 overflow-x-auto">
          {CATEGORIES.map((cat) => (
            <button
              key={cat}
              onClick={() => setCategory(cat)}
              className={cn(
                "whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
                category === cat
                  ? "bg-accent/10 text-accent"
                  : "text-muted hover:bg-border/40 hover:text-foreground"
              )}
            >
              {CATEGORY_TAB_LABELS[cat]}
            </button>
          ))}
        </div>
      </div>

      {/* Cards grid */}
      {filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 gap-2">
          <p className="text-muted">No tools match your search.</p>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((tool) => {
            const Icon = TOOL_ICON_MAP[tool.icon] ?? TOOL_ICON_MAP.webhook;
            const colors = CATEGORY_COLORS[tool.category] ?? CATEGORY_COLORS.general;
            return (
              <button
                key={tool.name}
                onClick={() => setSelectedTool(tool)}
                className={cn(
                  "flex items-start gap-4 rounded-xl border border-border bg-surface p-4",
                  "text-left transition-all duration-200",
                  "hover:border-accent/40 hover:shadow-md hover:shadow-accent/5",
                  "focus:outline-none focus:ring-2 focus:ring-accent/40"
                )}
              >
                <div className={cn("flex h-10 w-10 shrink-0 items-center justify-center rounded-lg", colors.bg)}>
                  <Icon className={cn("h-5 w-5", colors.text)} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-foreground capitalize">{tool.name}</span>
                    {tool.configured ? (
                      <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
                    ) : (
                      <AlertTriangle className="h-3.5 w-3.5 text-amber-500 shrink-0" />
                    )}
                  </div>
                  <p className="mt-0.5 text-xs text-muted line-clamp-2">{tool.description}</p>
                  <div className="mt-2 flex items-center gap-2">
                    <span className={cn(
                      "rounded-full px-2 py-0.5 text-[10px] font-medium",
                      colors.bg, colors.text
                    )}>
                      {CATEGORY_LABELS[tool.category] ?? tool.category}
                    </span>
                    <span className="text-[10px] text-muted">
                      {tool.functions?.length ?? 0} function{(tool.functions?.length ?? 0) !== 1 ? "s" : ""}
                    </span>
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      )}

      {/* Slide-over panel */}
      <ToolConfigPanel
        tool={selectedTool}
        onClose={() => setSelectedTool(null)}
        onSaved={fetchTools}
      />
    </div>
  );
}
