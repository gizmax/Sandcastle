import { createContext, useContext } from "react";

// -- Shared Settings types --------------------------------------------------

export interface ProviderEntry {
  id: string;
  name: string;
  region: string;
  configured: boolean;
  status: string;
}

export interface AdvisorStatus {
  current_provider: string;
  current_model: string | null;
  data_residency: string | null;
  available_providers: ProviderEntry[];
}

export interface SettingsData {
  anthropic_api_key: string;
  e2b_api_key: string;
  openai_api_key: string;
  minimax_api_key: string;
  openrouter_api_key: string;
  auth_required: boolean;
  dashboard_origin: string;
  default_max_cost_usd: number;
  webhook_secret: string;
  log_level: string;
  max_workflow_depth: number;
  storage_backend: string;
  storage_bucket: string;
  storage_endpoint: string;
  data_dir: string;
  workflows_dir: string;
  is_local_mode: boolean;
  database_url: string;
  redis_url: string;
}

export type EditableFields = Omit<
  SettingsData,
  | "storage_backend"
  | "storage_bucket"
  | "storage_endpoint"
  | "data_dir"
  | "workflows_dir"
  | "is_local_mode"
  | "database_url"
  | "redis_url"
>;

export type SectionName = "budget" | "system";

/**
 * Value shared by every Settings tab. The hub fetches `/settings` and
 * `/advisor/status` once and exposes the data plus mutation helpers so each
 * tab panel renders against a single source of truth (no duplicate fetches,
 * consistent dirty-state).
 */
export interface SettingsContextValue {
  settings: SettingsData;
  advisorStatus: AdvisorStatus | null;
  updateField: <K extends keyof SettingsData>(key: K, value: SettingsData[K]) => void;
  isSectionDirty: (section: SectionName) => boolean;
  savingSections: Set<SectionName>;
  handleSave: (section: SectionName) => void;
  refreshAdvisorStatus: () => void;
  setAdvisorStatus: React.Dispatch<React.SetStateAction<AdvisorStatus | null>>;
}

export const SettingsContext = createContext<SettingsContextValue | null>(null);

/** Access the shared Settings data. Throws if used outside the hub provider. */
export function useSettingsContext(): SettingsContextValue {
  const ctx = useContext(SettingsContext);
  if (!ctx) {
    throw new Error("useSettingsContext must be used within the Settings hub");
  }
  return ctx;
}
