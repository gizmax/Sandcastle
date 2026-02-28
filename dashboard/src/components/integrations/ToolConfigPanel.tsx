import { useCallback, useEffect, useRef, useState } from "react";
import { X, Eye, EyeOff, CheckCircle2, AlertTriangle, Loader2, Plus, Trash2, Link2, ExternalLink } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";
import { TOOL_ICON_MAP, CATEGORY_COLORS, CATEGORY_LABELS, TOOL_DISPLAY_NAMES, CREDENTIAL_LABELS, TOOL_SIGNUP_URLS } from "./toolIcons";

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
  connections?: ToolConnection[];
}

interface ToolConfigPanelProps {
  tool: Tool | null;
  onClose: () => void;
  onSaved: () => void;
}

function autoLabel(envVar: string, toolName: string): string {
  let s = envVar.replace(/^TOOL_/, "");
  const prefix = toolName.toUpperCase().replace(/-/g, "_") + "_";
  if (s.startsWith(prefix)) s = s.slice(prefix.length);
  return s.split("_").map((w) => w.charAt(0) + w.slice(1).toLowerCase()).join(" ");
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
    if (!tool) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose, tool]);

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
        toast.success(`${TOOL_DISPLAY_NAMES[tool.name] ?? tool.name} credentials saved`);
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
            <h2 className="text-lg font-semibold text-foreground">{TOOL_DISPLAY_NAMES[tool.name] ?? tool.name}</h2>
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

          {/* Signup link */}
          {TOOL_SIGNUP_URLS[tool.name] && (
            <a
              href={TOOL_SIGNUP_URLS[tool.name].url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-sm text-accent hover:underline"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              Get your credentials at {TOOL_SIGNUP_URLS[tool.name].label}
            </a>
          )}

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
                const credInfo = CREDENTIAL_LABELS[envVar];
                const displayLabel = credInfo?.label ?? autoLabel(envVar, tool.name);
                return (
                  <div key={envVar}>
                    <label className="mb-0.5 flex items-center gap-2 text-sm font-medium text-foreground">
                      {displayLabel}
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
                    <p className="text-[11px] text-muted-foreground mt-0.5 mb-1.5 font-mono">{envVar}</p>
                    <div className="relative">
                      <input
                        type={isVisible ? "text" : "password"}
                        value={values[envVar] ?? ""}
                        onChange={(e) => setValues((prev) => ({ ...prev, [envVar]: e.target.value }))}
                        placeholder={credInfo?.hint ?? (isMissing ? "Enter value..." : "Enter new value to update...")}
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

          {/* Named Connections */}
          {hasVars && (
            <ConnectionsSection tool={tool} onSaved={onSaved} />
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


// --- Named Connections Sub-component ---

function ConnectionsSection({ tool, onSaved }: { tool: Tool; onSaved: () => void }) {
  const [connections, setConnections] = useState<ToolConnection[]>(tool.connections || []);
  const [showForm, setShowForm] = useState(false);
  const [newName, setNewName] = useState("");
  const [newCreds, setNewCreds] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);

  useEffect(() => {
    setConnections(tool.connections || []);
  }, [tool]);

  const handleCreate = useCallback(async () => {
    if (!newName.trim()) return;
    setSaving(true);
    const res = await api.post<ToolConnection>(`/tools/${tool.name}/connections`, {
      name: newName.trim(),
      credentials: newCreds,
    });
    setSaving(false);
    if (res.error) {
      toast.error(res.error.message);
      return;
    }
    if (res.data) {
      setConnections((prev) => [...prev, res.data!]);
      setNewName("");
      setNewCreds({});
      setShowForm(false);
      onSaved();
      toast.success(`Connection "${newName.trim()}" created`);
    }
  }, [tool.name, newName, newCreds, onSaved]);

  const handleDelete = useCallback(async (connName: string) => {
    setDeleting(connName);
    const res = await api.delete(`/tools/${tool.name}/connections/${connName}`);
    setDeleting(null);
    if (res.error) {
      toast.error(res.error.message);
      return;
    }
    setConnections((prev) => prev.filter((c) => c.name !== connName));
    onSaved();
    toast.success(`Connection "${connName}" deleted`);
  }, [tool.name, onSaved]);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-foreground flex items-center gap-1.5">
          <Link2 className="h-3.5 w-3.5" />
          Named Connections
        </h3>
        <button
          onClick={() => setShowForm(!showForm)}
          className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs font-medium text-accent hover:bg-accent/10 transition-colors"
        >
          <Plus className="h-3 w-3" />
          Add
        </button>
      </div>

      {connections.length === 0 && !showForm && (
        <p className="text-xs text-muted">
          No named connections. Use <code className="text-accent">{tool.name}:name</code> syntax in workflows.
        </p>
      )}

      {/* Existing connections */}
      {connections.map((conn) => (
        <div
          key={conn.name}
          className="flex items-center gap-3 rounded-lg border border-border bg-background/50 px-3 py-2"
        >
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <code className="text-xs font-mono text-accent">{tool.name}:{conn.name}</code>
              {conn.credentials_missing.length === 0 ? (
                <CheckCircle2 className="h-3 w-3 text-emerald-500 shrink-0" />
              ) : (
                <AlertTriangle className="h-3 w-3 text-amber-500 shrink-0" />
              )}
            </div>
            {conn.credentials_missing.length > 0 && (
              <p className="mt-0.5 text-[10px] text-amber-500">
                Missing: {conn.credentials_missing.join(", ")}
              </p>
            )}
          </div>
          <button
            onClick={() => handleDelete(conn.name)}
            disabled={deleting === conn.name}
            className="rounded p-1 text-muted hover:text-red-400 hover:bg-red-500/10 transition-colors"
          >
            {deleting === conn.name ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Trash2 className="h-3.5 w-3.5" />
            )}
          </button>
        </div>
      ))}

      {/* New connection form */}
      {showForm && (
        <div className="space-y-3 rounded-lg border border-accent/30 bg-accent/5 p-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-foreground">Connection Name</label>
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="e.g. analytics, staging..."
              className={cn(
                "w-full rounded-lg border border-border bg-background px-3 py-1.5 text-sm text-foreground",
                "placeholder:text-muted-foreground/50",
                "focus:outline-none focus:ring-2 focus:ring-accent/40 focus:border-accent"
              )}
            />
          </div>
          {tool.credential_env_vars.map((envVar) => {
            const credInfo = CREDENTIAL_LABELS[envVar];
            const displayLabel = credInfo?.label ?? autoLabel(envVar, tool.name);
            return (
            <div key={envVar}>
              <label className="mb-0.5 block text-xs font-medium text-foreground">{displayLabel}</label>
              <p className="text-[10px] text-muted-foreground mb-1 font-mono">{envVar}</p>
              <input
                type="password"
                value={newCreds[envVar] ?? ""}
                onChange={(e) => setNewCreds((prev) => ({ ...prev, [envVar]: e.target.value }))}
                placeholder={credInfo?.hint ?? "Enter value..."}
                className={cn(
                  "w-full rounded-lg border border-border bg-background px-3 py-1.5 text-sm text-foreground",
                  "placeholder:text-muted-foreground/50",
                  "focus:outline-none focus:ring-2 focus:ring-accent/40 focus:border-accent"
                )}
              />
            </div>
            );
          })}
          <div className="flex gap-2 justify-end">
            <button
              onClick={() => { setShowForm(false); setNewName(""); setNewCreds({}); }}
              className="rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-muted hover:text-foreground transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleCreate}
              disabled={!newName.trim() || saving}
              className={cn(
                "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors",
                newName.trim() && !saving
                  ? "bg-accent text-white hover:bg-accent/90"
                  : "bg-accent/30 text-white/50 cursor-not-allowed"
              )}
            >
              {saving && <Loader2 className="h-3 w-3 animate-spin" />}
              Create Connection
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
