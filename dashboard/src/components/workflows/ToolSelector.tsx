import { useState, useEffect } from "react";
import { Wrench, CheckCircle2, XCircle, Loader2 } from "lucide-react";
import { api } from "@/api/client";

interface Tool {
  name: string;
  description: string;
  category: string;
  icon: string;
  configured: boolean;
  missing_credentials: string[];
}

interface ToolSelectorProps {
  selected: string[];
  onChange: (tools: string[]) => void;
  compact?: boolean;
}

const CATEGORIES: Record<string, string> = {
  communication: "Communication",
  project_management: "Project Management",
  crm: "CRM & Sales",
  data: "Data & Storage",
  general: "General",
};

export function ToolSelector({ selected, onChange, compact = false }: ToolSelectorProps) {
  const [tools, setTools] = useState<Tool[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const res = await api.get<{ tools: Tool[] }>("/tools");
        setTools(res.data?.tools || []);
      } catch {
        setTools([]);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const toggle = (name: string) => {
    onChange(
      selected.includes(name)
        ? selected.filter((t) => t !== name)
        : [...selected, name]
    );
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        Loading tools...
      </div>
    );
  }

  // Group by category
  const grouped = tools.reduce<Record<string, Tool[]>>((acc, t) => {
    (acc[t.category] ??= []).push(t);
    return acc;
  }, {});

  if (compact) {
    return (
      <div className="flex flex-wrap gap-1.5">
        {tools.map((t) => (
          <button
            key={t.name}
            type="button"
            onClick={() => toggle(t.name)}
            className={`
              inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-medium
              border transition-colors
              ${selected.includes(t.name)
                ? "border-accent/50 bg-accent/10 text-accent"
                : "border-border bg-background text-muted-foreground hover:border-accent/30"}
            `}
          >
            <Wrench className="h-3 w-3" />
            {t.name}
            {t.configured ? (
              <CheckCircle2 className="h-3 w-3 text-success" />
            ) : (
              <XCircle className="h-3 w-3 text-warning" />
            )}
          </button>
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {Object.entries(grouped).map(([category, categoryTools]) => (
        <div key={category}>
          <h4 className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider mb-1.5">
            {CATEGORIES[category] || category}
          </h4>
          <div className="space-y-1">
            {categoryTools.map((t) => (
              <label
                key={t.name}
                className="flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-muted/50 transition-colors cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={selected.includes(t.name)}
                  onChange={() => toggle(t.name)}
                  className="rounded border-border text-accent focus:ring-accent"
                />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs font-medium text-foreground">{t.name}</span>
                    {t.configured ? (
                      <CheckCircle2 className="h-3 w-3 text-success" />
                    ) : (
                      <XCircle className="h-3 w-3 text-warning" aria-label={`Missing: ${t.missing_credentials.join(", ")}`} />
                    )}
                  </div>
                  <p className="text-[10px] text-muted-foreground truncate">{t.description}</p>
                </div>
              </label>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
