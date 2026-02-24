import { useCallback, useEffect, useRef, useState } from "react";
import { X, Eye, EyeOff, CheckCircle2, AlertTriangle, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";
import { TOOL_ICON_MAP, CATEGORY_COLORS, CATEGORY_LABELS } from "./toolIcons";

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

interface ToolConfigPanelProps {
  tool: Tool | null;
  onClose: () => void;
  onSaved: () => void;
}

export function ToolConfigPanel({ tool, onClose, onSaved }: ToolConfigPanelProps) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [visible, setVisible] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);

  // Reset form when tool changes
  useEffect(() => {
    if (tool) {
      const init: Record<string, string> = {};
      for (const v of tool.credential_env_vars) {
        init[v] = "";
      }
      setValues(init);
      setVisible({});
    }
  }, [tool]);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  const isDirty = Object.values(values).some((v) => v.length > 0);

  const handleSave = useCallback(async () => {
    if (!tool || !isDirty) return;
    setSaving(true);
    try {
      const creds: Record<string, string> = {};
      for (const [k, v] of Object.entries(values)) {
        if (v) creds[k] = v;
      }
      const res = await api.put(`/tools/${tool.name}/credentials`, { credentials: creds });
      if (res.error) {
        toast.error(res.error.message);
      } else {
        toast.success(`${tool.name} credentials saved`);
        onSaved();
        onClose();
      }
    } catch {
      toast.error("Failed to save credentials");
    } finally {
      setSaving(false);
    }
  }, [tool, values, isDirty, onSaved, onClose]);

  if (!tool) return null;

  const Icon = TOOL_ICON_MAP[tool.icon] ?? TOOL_ICON_MAP.webhook;
  const colors = CATEGORY_COLORS[tool.category] ?? CATEGORY_COLORS.general;
  const catLabel = CATEGORY_LABELS[tool.category] ?? tool.category;
  const hasVars = tool.credential_env_vars.length > 0;

  return (
    <>
      {/* Overlay */}
      <div
        className="fixed inset-0 z-50 bg-black/40 transition-opacity"
        onClick={onClose}
      />

      {/* Slide-over panel */}
      <div
        ref={panelRef}
        className={cn(
          "fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col",
          "border-l border-border bg-surface shadow-2xl",
          "animate-in slide-in-from-right duration-300"
        )}
      >
        {/* Header */}
        <div className="flex items-center gap-3 border-b border-border px-6 py-5">
          <div className={cn("flex h-10 w-10 items-center justify-center rounded-lg", colors.bg)}>
            <Icon className={cn("h-5 w-5", colors.text)} />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-lg font-semibold text-foreground capitalize">{tool.name}</h2>
            <span className={cn(
              "inline-block mt-0.5 rounded-full px-2 py-0.5 text-[11px] font-medium",
              colors.bg, colors.text
            )}>
              {catLabel}
            </span>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-muted hover:bg-border/40 hover:text-foreground transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6">
          <p className="text-sm text-muted">{tool.description}</p>

          {/* Status */}
          <div className="flex items-center gap-2">
            {tool.configured ? (
              <>
                <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                <span className="text-sm text-emerald-500 font-medium">All credentials configured</span>
              </>
            ) : (
              <>
                <AlertTriangle className="h-4 w-4 text-amber-500" />
                <span className="text-sm text-amber-500 font-medium">
                  {tool.missing_credentials.length} missing credential{tool.missing_credentials.length !== 1 ? "s" : ""}
                </span>
              </>
            )}
          </div>

          {/* Credential inputs */}
          {hasVars ? (
            <div className="space-y-4">
              <h3 className="text-sm font-medium text-foreground">Credentials</h3>
              {tool.credential_env_vars.map((envVar) => {
                const isMissing = tool.missing_credentials.includes(envVar);
                const isVisible = visible[envVar] ?? false;
                return (
                  <div key={envVar}>
                    <label className="mb-1.5 flex items-center gap-2 text-sm font-medium text-foreground">
                      {envVar}
                      {isMissing ? (
                        <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-amber-500">
                          MISSING
                        </span>
                      ) : (
                        <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-500">
                          SET
                        </span>
                      )}
                    </label>
                    <div className="relative">
                      <input
                        type={isVisible ? "text" : "password"}
                        value={values[envVar] ?? ""}
                        onChange={(e) => setValues((prev) => ({ ...prev, [envVar]: e.target.value }))}
                        placeholder={isMissing ? "Enter value..." : "Enter new value to update..."}
                        className={cn(
                          "w-full rounded-lg border bg-background px-3 py-2 pr-10 text-sm text-foreground",
                          "placeholder:text-muted-foreground/50",
                          "focus:outline-none focus:ring-2 focus:ring-accent/40 focus:border-accent",
                          "transition-colors",
                          isMissing ? "border-amber-500/40" : "border-border"
                        )}
                      />
                      <button
                        type="button"
                        onClick={() => setVisible((prev) => ({ ...prev, [envVar]: !isVisible }))}
                        className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-muted hover:text-foreground transition-colors"
                      >
                        {isVisible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="rounded-lg border border-border bg-background/50 px-4 py-3">
              <p className="text-sm text-muted">This tool does not require any credentials.</p>
            </div>
          )}

          {/* Functions list */}
          {tool.functions.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-sm font-medium text-foreground">
                Available Functions ({tool.functions.length})
              </h3>
              <div className="space-y-1">
                {tool.functions.map((fn) => (
                  <div
                    key={fn.name}
                    className="flex items-start gap-2 rounded-lg border border-border bg-background/50 px-3 py-2"
                  >
                    <code className="text-xs font-mono text-accent whitespace-nowrap">{fn.name}</code>
                    <span className="text-xs text-muted">{fn.description}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        {hasVars && (
          <div className="flex items-center justify-end gap-3 border-t border-border px-6 py-4">
            <button
              onClick={onClose}
              className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-muted hover:text-foreground hover:bg-border/40 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={!isDirty || saving}
              className={cn(
                "flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors",
                isDirty && !saving
                  ? "bg-accent text-white hover:bg-accent/90"
                  : "bg-accent/30 text-white/50 cursor-not-allowed"
              )}
            >
              {saving && <Loader2 className="h-4 w-4 animate-spin" />}
              Save Credentials
            </button>
          </div>
        )}
      </div>
    </>
  );
}
