import { useState, useEffect, useCallback, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Layers, Search, Play, ArrowRight, X, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";

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
  input_schema?: InputSchema | null;
}

interface TemplateDetail extends Template {
  content: string;
}

const TAG_COLORS = [
  "bg-accent/15 text-accent",
  "bg-running/15 text-running",
  "bg-success/15 text-success",
  "bg-queued/15 text-queued",
  "bg-error/15 text-error",
  "bg-warning/15 text-warning",
];

function tagColor(tag: string): string {
  let hash = 0;
  for (let i = 0; i < tag.length; i++) {
    hash = tag.charCodeAt(i) + ((hash << 5) - hash);
  }
  return TAG_COLORS[Math.abs(hash) % TAG_COLORS.length];
}

export default function TemplatesPage() {
  const navigate = useNavigate();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [selectedTag, setSelectedTag] = useState<string | null>(null);
  const [detailName, setDetailName] = useState<string | null>(null);
  const [detail, setDetail] = useState<TemplateDetail | null>(null);
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

  const allTags = useMemo(() => {
    const tagSet = new Set<string>();
    templates.forEach((t) => t.tags.forEach((tag) => tagSet.add(tag)));
    return Array.from(tagSet).sort();
  }, [templates]);

  const filtered = useMemo(() => {
    let result = templates;
    if (search) {
      const q = search.toLowerCase();
      result = result.filter(
        (t) =>
          t.name.toLowerCase().includes(q) ||
          t.description.toLowerCase().includes(q)
      );
    }
    if (selectedTag) {
      result = result.filter((t) => t.tags.includes(selectedTag));
    }
    return result;
  }, [templates, search, selectedTag]);

  const openDetail = useCallback(async (name: string) => {
    setDetailName(name);
    setDetailLoading(true);
    const res = await api.get<TemplateDetail>(`/templates/${name}`);
    if (res.data) setDetail(res.data);
    setDetailLoading(false);
  }, []);

  const closeDetail = useCallback(() => {
    setDetailName(null);
    setDetail(null);
  }, []);

  const handleUseInBuilder = useCallback(() => {
    if (!detail) return;
    navigate("/workflows/builder", { state: { yaml: detail.content } });
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

  return (
    <div className="space-y-4 sm:space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">
          Templates
        </h1>
        <span className="text-sm text-muted">{templates.length} templates</span>
      </div>

      {/* Search + tag filters */}
      <div className="space-y-3">
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
          {allTags.map((tag) => (
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
      </div>

      {/* Grid */}
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
            <button
              key={t.name}
              type="button"
              onClick={() => openDetail(t.name)}
              className={cn(
                "group rounded-xl border p-4 text-left transition-all duration-200",
                detailName === t.name
                  ? "border-accent ring-2 ring-accent/20 bg-accent/5"
                  : "border-border bg-surface hover:border-border hover:shadow-md hover:bg-surface"
              )}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-semibold text-foreground">
                  {t.name.replace(/_/g, " ")}
                </span>
                <ArrowRight className="h-4 w-4 text-muted opacity-0 group-hover:opacity-100 transition-opacity" />
              </div>
              <p className="text-xs text-muted leading-relaxed mb-3 line-clamp-2">
                {t.description}
              </p>
              <div className="flex items-center justify-between">
                <div className="flex flex-wrap gap-1.5">
                  {t.tags.map((tag) => (
                    <span
                      key={tag}
                      className={cn(
                        "rounded-full px-2 py-0.5 text-[10px] font-medium",
                        tagColor(tag)
                      )}
                    >
                      {tag}
                    </span>
                  ))}
                </div>
                <span className="text-[11px] text-muted-foreground shrink-0 ml-2">
                  {t.step_count} {t.step_count === 1 ? "step" : "steps"}
                </span>
              </div>
            </button>
          ))}
        </div>
      )}

      {/* Detail slide-over */}
      {detailName && (
        <>
          <div className="fixed inset-0 z-40 bg-black/40" onClick={closeDetail} />
          <div className="fixed inset-y-0 right-0 z-50 flex w-full max-w-lg flex-col border-l border-border bg-surface shadow-xl">
            <div className="flex items-center justify-between border-b border-border px-5 py-4">
              <h2 className="text-lg font-semibold text-foreground">
                {detailName.replace(/_/g, " ")}
              </h2>
              <button
                onClick={closeDetail}
                className="rounded-lg p-1 text-muted hover:text-foreground transition-colors"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-5 space-y-5">
              {detailLoading ? (
                <div className="flex items-center justify-center py-12">
                  <Loader2 className="h-6 w-6 animate-spin text-muted" />
                </div>
              ) : detail ? (
                <>
                  <div>
                    <p className="text-sm text-muted leading-relaxed">{detail.description}</p>
                    <div className="mt-3 flex flex-wrap gap-1.5">
                      {detail.tags.map((tag) => (
                        <span
                          key={tag}
                          className={cn(
                            "rounded-full px-2.5 py-0.5 text-xs font-medium",
                            tagColor(tag)
                          )}
                        >
                          {tag}
                        </span>
                      ))}
                      <span className="rounded-full bg-border/40 px-2.5 py-0.5 text-xs font-medium text-muted">
                        {detail.step_count} {detail.step_count === 1 ? "step" : "steps"}
                      </span>
                    </div>
                  </div>

                  <div>
                    <p className="mb-2 text-xs font-medium text-muted">YAML Definition</p>
                    <pre className="max-h-96 overflow-auto rounded-lg bg-background p-4 font-mono text-xs text-foreground whitespace-pre-wrap">
                      {detail.content}
                    </pre>
                  </div>
                </>
              ) : (
                <p className="text-sm text-muted">Template not found</p>
              )}
            </div>

            {detail && (
              <div className="flex items-center gap-2 border-t border-border px-5 py-4">
                <button
                  onClick={handleUseInBuilder}
                  className={cn(
                    "flex flex-1 items-center justify-center gap-2 rounded-lg border border-border px-4 py-2",
                    "text-sm font-medium text-foreground",
                    "hover:bg-border/40 transition-colors"
                  )}
                >
                  <ArrowRight className="h-4 w-4" />
                  Use in Builder
                </button>
                <button
                  onClick={handleRunNow}
                  className={cn(
                    "flex flex-1 items-center justify-center gap-2 rounded-lg bg-accent px-4 py-2",
                    "text-sm font-medium text-accent-foreground",
                    "hover:bg-accent-hover transition-all duration-200 shadow-sm"
                  )}
                >
                  <Play className="h-4 w-4" />
                  Run Now
                </button>
              </div>
            )}
          </div>
        </>
      )}

      {/* Run modal */}
      {runModalOpen && detail && (
        <>
          <div className="fixed inset-0 z-[60] bg-black/40" onClick={() => setRunModalOpen(false)} />
          <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
            <div className="w-full max-w-md rounded-xl border border-border bg-surface p-6 shadow-xl">
              <div className="mb-4 flex items-center justify-between">
                <h2 className="text-lg font-semibold text-foreground">
                  Run {detail.name.replace(/_/g, " ")}
                </h2>
                <button
                  onClick={() => setRunModalOpen(false)}
                  className="rounded-lg p-1 text-muted hover:text-foreground"
                >
                  <X className="h-5 w-5" />
                </button>
              </div>
              <div className="space-y-4">
                {detail.input_schema?.properties ? (
                  Object.entries(detail.input_schema.properties).map(([key, prop]) => (
                    <div key={key}>
                      <label className="mb-1 block text-xs font-medium text-muted">
                        {key}
                        {detail.input_schema?.required?.includes(key) && (
                          <span className="text-error ml-0.5">*</span>
                        )}
                      </label>
                      {prop.description && (
                        <p className="mb-1.5 text-xs text-muted-foreground">{prop.description}</p>
                      )}
                      <input
                        type="text"
                        value={fieldValues[key] || ""}
                        onChange={(e) => setFieldValues((prev) => ({ ...prev, [key]: e.target.value }))}
                        placeholder={prop.default != null ? String(prop.default) : key}
                        required={detail.input_schema?.required?.includes(key)}
                        className={cn(
                          "h-9 w-full rounded-lg border border-border bg-background px-3 text-sm",
                          "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30"
                        )}
                      />
                    </div>
                  ))
                ) : (
                  <div>
                    <label className="mb-1 block text-xs font-medium text-muted">
                      Input Data (JSON)
                    </label>
                    <textarea
                      value={runInput}
                      onChange={(e) => setRunInput(e.target.value)}
                      rows={6}
                      className={cn(
                        "w-full rounded-lg border border-border bg-background px-3 py-2 font-mono text-sm",
                        "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30"
                      )}
                    />
                  </div>
                )}
                <div className="flex justify-end gap-2">
                  <button
                    onClick={() => setRunModalOpen(false)}
                    className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-muted hover:text-foreground transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleRunSubmit}
                    disabled={running}
                    className={cn(
                      "flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground",
                      "hover:bg-accent-hover transition-all duration-200 shadow-sm",
                      running && "opacity-50 cursor-not-allowed"
                    )}
                  >
                    {running && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                    <Play className="h-3.5 w-3.5" />
                    Run
                  </button>
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
