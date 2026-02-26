import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Search,
  CheckCircle2,
  AlertTriangle,
  Link2,
  Blocks,
  Plug,
  KeyRound,
} from "lucide-react";
import { api } from "@/api/client";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { EmptyState } from "@/components/shared/EmptyState";
import { ContextBanner } from "@/components/shared/ContextBanner";
import { ToolConfigPanel } from "@/components/integrations/ToolConfigPanel";
import {
  TOOL_ICON_MAP,
  CATEGORY_COLORS,
  CATEGORY_LABELS,
} from "@/components/integrations/toolIcons";
import { cn } from "@/lib/utils";

interface ToolConnection {
  name: string;
  tool_name: string;
  credentials_configured: string[];
  credentials_missing: string[];
  created_at: string | null;
}

interface Tool {
  name: string;
  description: string;
  category: string;
  icon: string;
  configured: boolean;
  missing_credentials: string[];
  credential_env_vars: string[];
  functions: { name: string; description: string }[];
  connections: ToolConnection[];
}

const CATEGORIES = [
  "all",
  "communication",
  "data",
  "ai",
  "devops",
  "project_management",
  "crm",
  "erp",
  "payments",
  "general",
] as const;
type Category = (typeof CATEGORIES)[number];

const CATEGORY_TAB_LABELS: Record<Category, string> = {
  all: "All",
  communication: "Communication",
  data: "Data",
  ai: "AI",
  devops: "DevOps",
  project_management: "Project Mgmt",
  crm: "CRM",
  erp: "ERP",
  payments: "Payments",
  general: "General",
};

const POPULAR_TOOLS = ["slack", "openai", "github", "postgresql"];

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

  const configuredTools = useMemo(
    () =>
      tools
        .filter((t) => t.configured)
        .sort((a, b) => {
          const aNeedsAttention = a.missing_credentials.length > 0 ? 0 : 1;
          const bNeedsAttention = b.missing_credentials.length > 0 ? 0 : 1;
          if (aNeedsAttention !== bNeedsAttention)
            return aNeedsAttention - bNeedsAttention;
          return a.name.localeCompare(b.name);
        }),
    [tools],
  );

  const unconfiguredTools = useMemo(
    () => tools.filter((t) => !t.configured),
    [tools],
  );

  const filteredAvailable = useMemo(
    () =>
      unconfiguredTools.filter((t) => {
        if (category !== "all" && t.category !== category) return false;
        if (search) {
          const q = search.toLowerCase();
          return (
            t.name.toLowerCase().includes(q) ||
            t.description.toLowerCase().includes(q)
          );
        }
        return true;
      }),
    [unconfiguredTools, category, search],
  );

  const needsAttentionTools = useMemo(
    () => tools.filter((t) => t.missing_credentials.length > 0 && t.connections?.length > 0),
    [tools],
  );

  const totalConnections = useMemo(
    () => tools.reduce((sum, t) => sum + (t.connections?.length ?? 0), 0),
    [tools],
  );

  const stats = useMemo(
    () => ({
      configured: configuredTools.length,
      needsAttention: needsAttentionTools.length,
      connections: totalConnections,
      available: tools.length,
    }),
    [configuredTools.length, needsAttentionTools.length, totalConnections, tools.length],
  );

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-64 items-center justify-center">
        <div className="rounded-xl border border-red-500/30 bg-red-500/5 p-6 text-center">
          <AlertTriangle className="mx-auto h-8 w-8 text-red-500" />
          <p className="mt-2 text-sm text-muted">{error}</p>
          <button
            onClick={fetchTools}
            className="mt-3 text-sm font-medium text-accent hover:text-accent/80 transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-foreground">Integrations</h1>
        <p className="mt-1 text-sm text-muted">
          Connect your workflows to external services and APIs.
        </p>
      </div>

      {/* Context banner */}
      {configuredTools.filter((t) => t.missing_credentials.length > 0).length > 0 && (
        <ContextBanner variant="warning" icon={KeyRound}>
          {configuredTools.filter((t) => t.missing_credentials.length > 0).length} configured tool{configuredTools.filter((t) => t.missing_credentials.length > 0).length > 1 ? "s" : ""} missing credentials - click any to configure.
        </ContextBanner>
      )}

      {/* Summary Stats */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
            <p className="text-xs font-medium text-muted-foreground">Configured</p>
          </div>
          <p className="mt-1 text-2xl font-semibold text-foreground">{stats.configured}</p>
        </div>
        <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-amber-500" />
            <p className="text-xs font-medium text-muted-foreground">Needs Attention</p>
          </div>
          <p className="mt-1 text-2xl font-semibold text-foreground">{stats.needsAttention}</p>
        </div>
        <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
          <div className="flex items-center gap-2">
            <Link2 className="h-4 w-4 text-accent" />
            <p className="text-xs font-medium text-muted-foreground">Connections</p>
          </div>
          <p className="mt-1 text-2xl font-semibold text-foreground">{stats.connections}</p>
        </div>
        <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
          <div className="flex items-center gap-2">
            <Blocks className="h-4 w-4 text-muted-foreground" />
            <p className="text-xs font-medium text-muted-foreground">Available</p>
          </div>
          <p className="mt-1 text-2xl font-semibold text-foreground">{stats.available}</p>
        </div>
      </div>

      {/* Needs Attention Banner */}
      {needsAttentionTools.length > 0 && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4">
          <div className="flex items-center gap-2 mb-3">
            <AlertTriangle className="h-4 w-4 text-amber-500" />
            <p className="text-sm font-medium text-foreground">
              {needsAttentionTools.length} integration{needsAttentionTools.length !== 1 ? "s" : ""} with missing credentials
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {needsAttentionTools.map((tool) => (
              <button
                key={tool.name}
                onClick={() => setSelectedTool(tool)}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-1.5",
                  "text-xs font-medium text-amber-600 dark:text-amber-400",
                  "hover:bg-amber-500/20 transition-colors cursor-pointer",
                )}
              >
                <span className="capitalize">{tool.name}</span>
                <span className="text-amber-500/60">-</span>
                <span className="text-amber-500/80 font-normal">
                  {tool.missing_credentials[0]}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Section A: Your Integrations */}
      {configuredTools.length > 0 ? (
        <section>
          <h2 className="text-lg font-semibold text-foreground mb-4">
            Your Integrations ({configuredTools.length})
          </h2>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {configuredTools.map((tool) => {
              const Icon = TOOL_ICON_MAP[tool.icon] ?? TOOL_ICON_MAP.webhook;
              const colors = CATEGORY_COLORS[tool.category] ?? CATEGORY_COLORS.general;
              const hasIssue = tool.missing_credentials.length > 0;
              return (
                <button
                  key={tool.name}
                  onClick={() => setSelectedTool(tool)}
                  className={cn(
                    "relative flex items-start gap-4 rounded-xl border border-border bg-surface p-4",
                    "text-left transition-all duration-200",
                    "hover:border-accent/40 hover:shadow-md hover:shadow-accent/5",
                    "focus:outline-none focus:ring-2 focus:ring-accent/40",
                  )}
                >
                  {/* Left accent border */}
                  <div
                    className={cn(
                      "absolute left-0 top-3 bottom-3 w-[3px] rounded-full",
                      hasIssue ? "bg-amber-500" : "bg-emerald-500",
                    )}
                  />
                  <div
                    className={cn(
                      "ml-2 flex h-10 w-10 shrink-0 items-center justify-center rounded-lg",
                      colors.bg,
                    )}
                  >
                    <Icon className={cn("h-5 w-5", colors.text)} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold text-foreground capitalize">
                        {tool.name}
                      </span>
                      {hasIssue ? (
                        <AlertTriangle className="h-3.5 w-3.5 text-amber-500 shrink-0" />
                      ) : (
                        <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
                      )}
                    </div>
                    <p className="mt-0.5 text-xs text-muted">
                      {hasIssue
                        ? `Missing: ${tool.missing_credentials.join(", ")}`
                        : "All credentials configured"}
                    </p>
                    <div className="mt-2 flex items-center gap-2">
                      {(tool.connections?.length ?? 0) > 0 && (
                        <span className="rounded-full bg-accent/10 px-2 py-0.5 text-[10px] font-medium text-accent">
                          {tool.connections.length} connection
                          {tool.connections.length !== 1 ? "s" : ""}
                        </span>
                      )}
                      <span className="text-[10px] text-muted">
                        {tool.functions?.length ?? 0} function
                        {(tool.functions?.length ?? 0) !== 1 ? "s" : ""}
                      </span>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        </section>
      ) : (
        <section>
          <EmptyState
            icon={Plug}
            title="No integrations configured yet"
            description="Connect your workflows to external services."
          />
          {/* Popular quick-start cards */}
          <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 max-w-3xl mx-auto">
            {tools
              .filter((t) => POPULAR_TOOLS.includes(t.name))
              .map((tool) => {
                const Icon = TOOL_ICON_MAP[tool.icon] ?? TOOL_ICON_MAP.webhook;
                const colors =
                  CATEGORY_COLORS[tool.category] ?? CATEGORY_COLORS.general;
                return (
                  <button
                    key={tool.name}
                    onClick={() => setSelectedTool(tool)}
                    className={cn(
                      "flex flex-col items-center gap-2 rounded-xl border border-border bg-surface p-4",
                      "transition-all duration-200",
                      "hover:border-accent/40 hover:shadow-md hover:shadow-accent/5",
                      "focus:outline-none focus:ring-2 focus:ring-accent/40",
                    )}
                  >
                    <div
                      className={cn(
                        "flex h-10 w-10 items-center justify-center rounded-lg",
                        colors.bg,
                      )}
                    >
                      <Icon className={cn("h-5 w-5", colors.text)} />
                    </div>
                    <span className="text-sm font-medium text-foreground capitalize">
                      {tool.name}
                    </span>
                    <span className="text-[10px] text-accent font-medium">
                      Quick setup
                    </span>
                  </button>
                );
              })}
          </div>
        </section>
      )}

      {/* Section B: Available Integrations */}
      <section>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between mb-4">
          <h2 className="text-lg font-semibold text-foreground">
            Available ({filteredAvailable.length})
          </h2>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <div className="relative max-w-xs w-full sm:w-auto">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search tools..."
                className={cn(
                  "w-full sm:w-56 rounded-lg border border-border bg-background pl-9 pr-3 py-2 text-sm text-foreground",
                  "placeholder:text-muted-foreground/50",
                  "focus:outline-none focus:ring-2 focus:ring-accent/40 focus:border-accent",
                  "transition-colors",
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
                      : "text-muted hover:bg-border/40 hover:text-foreground",
                  )}
                >
                  {CATEGORY_TAB_LABELS[cat]}
                </button>
              ))}
            </div>
          </div>
        </div>

        {filteredAvailable.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 gap-2">
            <p className="text-sm text-muted">No tools match your filters.</p>
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {filteredAvailable.map((tool) => {
              const Icon = TOOL_ICON_MAP[tool.icon] ?? TOOL_ICON_MAP.webhook;
              const colors =
                CATEGORY_COLORS[tool.category] ?? CATEGORY_COLORS.general;
              return (
                <button
                  key={tool.name}
                  onClick={() => setSelectedTool(tool)}
                  className={cn(
                    "flex items-start gap-3 rounded-xl border border-border bg-surface p-3",
                    "text-left transition-all duration-200",
                    "hover:border-accent/40 hover:shadow-md hover:shadow-accent/5",
                    "focus:outline-none focus:ring-2 focus:ring-accent/40",
                  )}
                >
                  <div
                    className={cn(
                      "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg",
                      colors.bg,
                    )}
                  >
                    <Icon className={cn("h-4 w-4", colors.text)} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-foreground capitalize">
                        {tool.name}
                      </span>
                      <span
                        className={cn(
                          "rounded-full px-1.5 py-0.5 text-[9px] font-medium",
                          colors.bg,
                          colors.text,
                        )}
                      >
                        {CATEGORY_LABELS[tool.category] ?? tool.category}
                      </span>
                    </div>
                    <p className="mt-0.5 text-xs text-muted line-clamp-1">
                      {tool.description}
                    </p>
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </section>

      {/* Slide-over panel */}
      <ToolConfigPanel
        tool={selectedTool}
        onClose={() => setSelectedTool(null)}
        onSaved={fetchTools}
      />
    </div>
  );
}
