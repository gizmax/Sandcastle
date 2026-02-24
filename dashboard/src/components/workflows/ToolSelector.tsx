import { useState, useEffect } from "react";
import { Wrench, CheckCircle2, XCircle, Loader2 } from "lucide-react";
import { api } from "@/api/client";

interface ToolConnection {
  name: string;
  tool_name: string;
  credentials_configured: string[];
  credentials_missing: string[];
}

interface Tool {
  name: string;
  description: string;
  category: string;
  icon: string;
  configured: boolean;
  missing_credentials: string[];
  connections?: ToolConnection[];
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

  // Build flat list of selectable items: base tools + their connections
  const selectableItems = tools.flatMap((t) => {
    const items = [{ ref: t.name, label: t.name, configured: t.configured, description: t.description, isConnection: false }];
    if (t.connections) {
      for (const c of t.connections) {
        items.push({
          ref: `${t.name}:${c.name}`,
          label: `${t.name}:${c.name}`,
          configured: c.credentials_missing.length === 0,
          description: `Named connection "${c.name}"`,
          isConnection: true,
        });
      }
    }
    return items;
  });

  if (compact) {
    return (
      <div className="flex flex-wrap gap-1.5">
        {selectableItems.map((item) => (
          <button
            key={item.ref}
            type="button"
            onClick={() => toggle(item.ref)}
            className={`
              inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-medium
              border transition-colors
              ${selected.includes(item.ref)
                ? "border-accent/50 bg-accent/10 text-accent"
                : "border-border bg-background text-muted-foreground hover:border-accent/30"}
            `}
          >
            <Wrench className="h-3 w-3" />
            {item.label}
            {item.configured ? (
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
              <div key={t.name}>
                <label
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
                {/* Named connections as indented sub-items */}
                {t.connections && t.connections.length > 0 && (
                  <div className="ml-6 space-y-0.5">
                    {t.connections.map((c) => {
                      const ref = `${t.name}:${c.name}`;
                      const connConfigured = c.credentials_missing.length === 0;
                      return (
                        <label
                          key={ref}
                          className="flex items-center gap-2 rounded-lg px-2 py-1 hover:bg-muted/50 transition-colors cursor-pointer"
                        >
                          <input
                            type="checkbox"
                            checked={selected.includes(ref)}
                            onChange={() => toggle(ref)}
                            className="rounded border-border text-accent focus:ring-accent"
                          />
                          <div className="flex items-center gap-1.5">
                            <span className="text-xs font-mono text-accent">{ref}</span>
                            {connConfigured ? (
                              <CheckCircle2 className="h-3 w-3 text-success" />
                            ) : (
                              <XCircle className="h-3 w-3 text-warning" />
                            )}
                          </div>
                        </label>
                      );
                    })}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
