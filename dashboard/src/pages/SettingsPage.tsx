import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from "react";
import {
  Settings,
  AlertCircle,
  SlidersHorizontal,
  Key,
  Cpu,
  Blocks,
  Wrench,
} from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { api } from "@/api/client";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { Tabs, TabPanel, type TabItem } from "@/components/ui/Tabs";
import { diffFields } from "@/components/settings/settingsHelpers";
import {
  SettingsContext,
  type SettingsContextValue,
  type SettingsData,
  type AdvisorStatus,
  type EditableFields,
  type SectionName,
} from "@/components/settings/settingsContext";

// Lazy-load each tab's content so a deep-link only pays for the active panel.
// (The originals — ApiKeys/Integrations — were lazy at the route level; this
// preserves that code-splitting now that they are tab panels.)
const GeneralPanel = lazy(() => import("@/components/settings/GeneralPanel"));
const ProvidersPanel = lazy(() => import("@/components/settings/ProvidersPanel"));
const AdvancedPanel = lazy(() => import("@/components/settings/AdvancedPanel"));
const ApiKeysPanel = lazy(() => import("@/components/api-keys/ApiKeysPanel"));
const IntegrationsPanel = lazy(() => import("@/components/integrations/IntegrationsPanel"));

// -- Tab definitions --------------------------------------------------------

const TABS: TabItem[] = [
  { id: "general", label: "General", icon: SlidersHorizontal },
  { id: "keys", label: "Keys", icon: Key },
  { id: "providers", label: "Providers", icon: Cpu },
  { id: "integrations", label: "Integrations", icon: Blocks },
  // Advanced is visually separated (pushed right) — "complexity at the bottom".
  { id: "advanced", label: "Advanced", icon: Wrench, separated: true },
];

const VALID_TABS = new Set(TABS.map((t) => t.id));
const DEFAULT_TAB = "general";
const TAB_ID_BASE = "settings";

// -- Main component ---------------------------------------------------------

/**
 * Unified Settings hub. One page, five tabs driven by the `?tab=` query param
 * (shareable + back/forward friendly). The hub fetches `/settings` and
 * `/advisor/status` once and shares them with every tab via context, so all
 * the original CRUD/save logic is preserved without duplicate fetches.
 *
 * "Simple at the top, complexity at the bottom": General first, Advanced last.
 */
export default function SettingsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const rawTab = searchParams.get("tab") ?? DEFAULT_TAB;
  const activeTab = VALID_TABS.has(rawTab) ? rawTab : DEFAULT_TAB;

  const changeTab = useCallback(
    (id: string) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (id === DEFAULT_TAB) next.delete("tab");
          else next.set("tab", id);
          return next;
        },
        { replace: false },
      );
    },
    [setSearchParams],
  );

  // -- Shared data ----------------------------------------------------------

  const [settings, setSettings] = useState<SettingsData | null>(null);
  // Snapshot of the last-saved values, kept in state (not a ref) so dirty
  // checks can run during render without tripping react-hooks/refs.
  const [original, setOriginal] = useState<SettingsData | null>(null);
  const [advisorStatus, setAdvisorStatus] = useState<AdvisorStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [savingSections, setSavingSections] = useState<Set<SectionName>>(new Set());

  const fetchSettings = useCallback(async () => {
    setFetchError(null);
    const res = await api.get<SettingsData>("/settings");
    if (res.data) {
      setSettings(res.data);
      setOriginal({ ...res.data });
    } else if (res.error) {
      setFetchError(res.error.message || "Failed to load settings");
    }
    setLoading(false);
  }, []);

  const fetchAdvisorStatus = useCallback(async () => {
    const res = await api.get<AdvisorStatus>("/advisor/status");
    if (res.data) setAdvisorStatus(res.data);
  }, []);

  useEffect(() => {
    void fetchSettings();
    void fetchAdvisorStatus();
  }, [fetchSettings, fetchAdvisorStatus]);

  const updateField = useCallback(
    <K extends keyof SettingsData>(key: K, value: SettingsData[K]) => {
      setSettings((prev) => (prev ? { ...prev, [key]: value } : prev));
    },
    [],
  );

  const isSectionDirty = useCallback(
    (section: SectionName): boolean => {
      if (!settings || !original) return false;
      const o = original;
      switch (section) {
        case "budget":
          return settings.default_max_cost_usd !== o.default_max_cost_usd;
        case "system":
          return settings.log_level !== o.log_level || settings.max_workflow_depth !== o.max_workflow_depth;
      }
    },
    [settings, original],
  );

  const handleSave = useCallback(
    async (section: SectionName) => {
      if (!settings || !original) return;
      const o = original;

      let changed: Partial<EditableFields> = {};
      switch (section) {
        case "budget":
          changed = diffFields(
            { default_max_cost_usd: settings.default_max_cost_usd },
            { default_max_cost_usd: o.default_max_cost_usd },
          );
          break;
        case "system":
          changed = diffFields(
            { log_level: settings.log_level, max_workflow_depth: settings.max_workflow_depth },
            { log_level: o.log_level, max_workflow_depth: o.max_workflow_depth },
          );
          break;
        default:
          return;
      }
      if (Object.keys(changed).length === 0) return;

      setSavingSections((prev) => new Set(prev).add(section));
      const res = await api.patch<SettingsData>("/settings", changed);
      setSavingSections((prev) => {
        const next = new Set(prev);
        next.delete(section);
        return next;
      });

      if (res.error) {
        toast.error(`Failed to save: ${res.error.message}`);
      } else {
        // Re-snapshot so dirty state resets to match the new server values.
        setOriginal({ ...settings });
        toast.success("Settings saved");
      }
    },
    [settings, original],
  );

  const contextValue = useMemo<SettingsContextValue | null>(() => {
    if (!settings) return null;
    return {
      settings,
      advisorStatus,
      updateField,
      isSectionDirty,
      savingSections,
      handleSave: (section) => void handleSave(section),
      refreshAdvisorStatus: () => void fetchAdvisorStatus(),
      setAdvisorStatus,
    };
  }, [settings, advisorStatus, updateField, isSectionDirty, savingSections, handleSave, fetchAdvisorStatus]);

  // -- Render ---------------------------------------------------------------

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (fetchError || !settings || !contextValue) {
    return (
      <div className="flex h-64 flex-col items-center justify-center gap-3">
        <AlertCircle className="h-8 w-8 text-error" />
        <p className="text-sm text-muted">{fetchError || "Could not load settings"}</p>
        <button
          onClick={() => { setLoading(true); void fetchSettings(); }}
          className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-foreground hover:bg-border/40 transition-colors"
        >
          Retry
        </button>
      </div>
    );
  }

  const panelFallback = (
    <div className="flex h-48 items-center justify-center">
      <LoadingSpinner size="md" />
    </div>
  );

  return (
    <SettingsContext.Provider value={contextValue}>
      <div className="space-y-5 sm:space-y-6">
        <div className="flex items-center gap-3">
          <Settings className="h-6 w-6 text-muted" />
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">
            Settings
          </h1>
        </div>

        <Tabs
          tabs={TABS}
          active={activeTab}
          onChange={changeTab}
          idBase={TAB_ID_BASE}
          aria-label="Settings sections"
        />

        <Suspense fallback={panelFallback}>
          <TabPanel id="general" idBase={TAB_ID_BASE} active={activeTab === "general"}>
            <GeneralPanel />
          </TabPanel>
          <TabPanel id="keys" idBase={TAB_ID_BASE} active={activeTab === "keys"}>
            <ApiKeysPanel embedded />
          </TabPanel>
          <TabPanel id="providers" idBase={TAB_ID_BASE} active={activeTab === "providers"}>
            <ProvidersPanel />
          </TabPanel>
          <TabPanel id="integrations" idBase={TAB_ID_BASE} active={activeTab === "integrations"}>
            <IntegrationsPanel embedded />
          </TabPanel>
          <TabPanel id="advanced" idBase={TAB_ID_BASE} active={activeTab === "advanced"}>
            <AdvancedPanel />
          </TabPanel>
        </Suspense>
      </div>
    </SettingsContext.Provider>
  );
}
