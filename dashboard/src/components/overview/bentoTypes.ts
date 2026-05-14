// Shared types for the Bento overview sub-components.

export interface Stats {
  total_runs_today: number;
  success_rate: number;
  total_cost_today: number;
  avg_duration_seconds: number;
  runs_by_day: Array<{ date: string; completed: number; failed: number; total: number }>;
  cost_by_workflow: Array<{ workflow: string; cost: number }>;
}

export interface RunItem {
  run_id: string;
  workflow_name: string;
  status: string;
  total_cost_usd: number;
  started_at: string | null;
}

export interface SparklineData {
  values: number[];
  /** Backend returns trend_percent; we normalise to trendPercent in the fetch. */
  trendPercent: number;
}

export interface ApiSparklineData {
  values: number[];
  trend_percent: number;
}

export interface HeatmapApiCell {
  date: string;
  count: number;
  day_of_week: number;
}

export interface HeatmapCell {
  date: string;
  count: number;
  /** 0=Monday ... 6=Sunday (matches backend day_of_week) */
  dayOfWeek: number;
}

export interface AnomalyItem {
  type: string;
  severity: "warning" | "critical";
  workflow: string;
  message: string;
  /** May be empty string or null when anomaly is not linked to a specific run. */
  run_id: string | null;
  value: number;
  threshold: number;
}

export interface ProviderCostEntry {
  provider: string;
  model: string;
  region: string;
  total_cost_usd: number;
  run_count: number;
  avg_cost_per_run: number;
  percentage: number;
}

export interface ProviderSavingsAlternative {
  provider: string;
  model: string;
  region: string;
  projected_cost_usd: number;
  savings_usd: number;
  savings_percent: number;
  note: string;
}

export interface ProviderCostsData {
  period_days: number;
  total_cost_usd: number;
  by_provider: ProviderCostEntry[];
  advisor_costs: {
    total_usd: number;
    by_purpose: Array<{ purpose: string; cost_usd: number; calls: number }>;
  };
}

export interface ProviderSavingsData {
  current_total_usd: number;
  alternatives: ProviderSavingsAlternative[];
}

export interface ProviderRecommendation {
  type: string;
  severity: "high" | "medium" | "info";
  title: string;
  description: string;
  action: string;
  provider: string;
  estimated_savings_usd: number;
  confidence: number;
}

export interface AdvisorRecommendation {
  workflow: string;
  current_provider: string;
  current_cost_30d: number;
  suggested_provider: string;
  suggested_model: string;
  estimated_cost_30d: number;
  savings_monthly: number;
  savings_percent: number;
  eu_compliant: boolean;
  reason: string;
}

export interface AdvisorRecommendationsData {
  recommendations: AdvisorRecommendation[];
  total_potential_savings: number;
}

export interface FailoverEvent {
  timestamp: string;
  original_provider: string;
  failover_provider: string;
  reason: string;
  cost_delta: number;
}

export interface FailoverEventsData {
  events: FailoverEvent[];
  total_failovers_7d: number;
  total_cost_delta_7d: number;
}

export interface AuditEvent {
  id: string;
  event_type: string;
  workflow_name: string | null;
  run_id: string | null;
  timestamp: string;
  metadata: Record<string, unknown>;
}

export interface ProviderInfo {
  id: string;
  name: string;
  status: "running" | "configured" | "unconfigured";
  region: string;
  latency_ms: number | null;
}

export function normaliseSparkline(raw: ApiSparklineData): SparklineData {
  return { values: raw.values, trendPercent: raw.trend_percent };
}

export function normaliseHeatmapCell(raw: HeatmapApiCell): HeatmapCell {
  return { date: raw.date, count: raw.count, dayOfWeek: raw.day_of_week };
}
