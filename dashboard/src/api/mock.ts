import type { ApiResponse } from "./client";

interface MockStep {
  step_id: string;
  parallel_index: number | null;
  status: string;
  output: unknown;
  cost_usd: number;
  duration_seconds: number;
  attempt: number;
  error: string | null;
}

const now = new Date();
const h = (hoursAgo: number) => new Date(now.getTime() - hoursAgo * 3600000).toISOString();
const d = (daysAgo: number) => new Date(now.getTime() - daysAgo * 86400000).toISOString().slice(0, 10);

const MOCK_RUNS = [
  { run_id: "a1b2c3d4-1111-4000-8000-000000000001", workflow_name: "lead-enrichment", status: "completed", total_cost_usd: 0.12, started_at: h(0.5), completed_at: h(0.45) },
  { run_id: "a1b2c3d4-2222-4000-8000-000000000002", workflow_name: "competitor-monitor", status: "running", total_cost_usd: 0.04, started_at: h(0.1), completed_at: null },
  { run_id: "a1b2c3d4-3333-4000-8000-000000000003", workflow_name: "seo-audit", status: "completed", total_cost_usd: 0.08, started_at: h(2), completed_at: h(1.9) },
  { run_id: "a1b2c3d4-4444-4000-8000-000000000004", workflow_name: "lead-enrichment", status: "failed", total_cost_usd: 0.03, started_at: h(5), completed_at: h(4.95) },
  { run_id: "a1b2c3d4-5555-4000-8000-000000000005", workflow_name: "lead-enrichment", status: "completed", total_cost_usd: 0.11, started_at: h(8), completed_at: h(7.9) },
  { run_id: "a1b2c3d4-6666-4000-8000-000000000006", workflow_name: "competitor-monitor", status: "completed", total_cost_usd: 0.09, started_at: h(12), completed_at: h(11.8) },
  { run_id: "a1b2c3d4-7777-4000-8000-000000000007", workflow_name: "seo-audit", status: "completed", total_cost_usd: 0.07, started_at: h(18), completed_at: h(17.9) },
  { run_id: "a1b2c3d4-8888-4000-8000-000000000008", workflow_name: "lead-enrichment", status: "completed", total_cost_usd: 0.14, started_at: h(24), completed_at: h(23.8) },
  { run_id: "a1b2c3d4-9999-4000-8000-000000000009", workflow_name: "competitor-monitor", status: "failed", total_cost_usd: 0.02, started_at: h(30), completed_at: h(29.9) },
  { run_id: "a1b2c3d4-aaaa-4000-8000-00000000000a", workflow_name: "seo-audit", status: "completed", total_cost_usd: 0.06, started_at: h(36), completed_at: h(35.8) },
  { run_id: "a1b2c3d4-bbbb-4000-8000-00000000000b", workflow_name: "lead-enrichment", status: "completed", total_cost_usd: 0.10, started_at: h(48), completed_at: h(47.5) },
  { run_id: "a1b2c3d4-cccc-4000-8000-00000000000c", workflow_name: "lead-enrichment", status: "completed", total_cost_usd: 0.13, started_at: h(60), completed_at: h(59.8) },
];

const MOCK_STEPS: MockStep[] = [
  { step_id: "scrape", parallel_index: null, status: "completed", output: { url: "https://example.com", title: "Example Corp", employees: 150 }, cost_usd: 0.04, duration_seconds: 12.3, attempt: 1, error: null },
  { step_id: "enrich", parallel_index: null, status: "completed", output: { company: "Example Corp", revenue: "$50M", industry: "SaaS", decision_makers: ["John CEO", "Jane CTO"] }, cost_usd: 0.05, duration_seconds: 18.7, attempt: 1, error: null },
  { step_id: "score", parallel_index: null, status: "completed", output: { lead_score: 87, tier: "A", recommendation: "High priority - schedule demo this week" }, cost_usd: 0.03, duration_seconds: 8.2, attempt: 1, error: null },
];

const MOCK_STEPS_RUNNING: MockStep[] = [
  { step_id: "fetch-competitors", parallel_index: null, status: "completed", output: { competitors: ["CompA", "CompB", "CompC"] }, cost_usd: 0.02, duration_seconds: 6.1, attempt: 1, error: null },
  { step_id: "analyze", parallel_index: 0, status: "completed", output: { name: "CompA", changes: "New pricing page" }, cost_usd: 0.01, duration_seconds: 9.3, attempt: 1, error: null },
  { step_id: "analyze", parallel_index: 1, status: "running", output: null, cost_usd: 0.0, duration_seconds: 0, attempt: 1, error: null },
  { step_id: "analyze", parallel_index: 2, status: "pending", output: null, cost_usd: 0.0, duration_seconds: 0, attempt: 1, error: null },
];

const MOCK_STEPS_FAILED: MockStep[] = [
  { step_id: "scrape", parallel_index: null, status: "completed", output: { url: "https://broken.test" }, cost_usd: 0.02, duration_seconds: 4.1, attempt: 1, error: null },
  { step_id: "enrich", parallel_index: null, status: "failed", output: null, cost_usd: 0.01, duration_seconds: 2.3, attempt: 3, error: "Timeout after 300s - external API unreachable" },
];

function getRunDetail(runId: string) {
  const run = MOCK_RUNS.find((r) => r.run_id === runId);
  if (!run) return null;

  let steps = MOCK_STEPS;
  if (run.status === "running") steps = MOCK_STEPS_RUNNING;
  if (run.status === "failed") steps = MOCK_STEPS_FAILED;

  return {
    ...run,
    input_data: { target_url: "https://example.com", max_depth: 3 },
    outputs: run.status === "completed" ? { final: "Lead enrichment complete" } : null,
    error: run.status === "failed" ? "Step 'enrich' failed after 3 attempts" : null,
    steps,
  };
}

const MOCK_STATS = {
  total_runs_today: 8,
  success_rate: 0.875,
  total_cost_today: 0.56,
  avg_duration_seconds: 42.3,
  runs_by_day: Array.from({ length: 30 }, (_, i) => {
    const completed = Math.floor(Math.random() * 12) + 2;
    const failed = Math.floor(Math.random() * 3);
    return { date: d(29 - i), completed, failed, total: completed + failed };
  }),
  cost_by_workflow: [
    { workflow: "lead-enrichment", cost: 2.34 },
    { workflow: "competitor-monitor", cost: 1.12 },
    { workflow: "seo-audit", cost: 0.67 },
  ],
};

const MOCK_WORKFLOWS = [
  { name: "Lead Enrichment", description: "Scrape target websites, enrich with company data, and score leads for sales outreach priority.", steps_count: 3, file_name: "lead-enrichment.yaml" },
  { name: "Competitor Monitor", description: "Track competitor websites for changes, analyze differences, and generate a summary report.", steps_count: 4, file_name: "competitor-monitor.yaml" },
  { name: "SEO Audit", description: "Crawl a website, analyze on-page SEO factors, and produce actionable recommendations.", steps_count: 3, file_name: "seo-audit.yaml" },
];

const MOCK_SCHEDULES = [
  { id: "sch-001", workflow_name: "competitor-monitor", cron_expression: "0 */6 * * *", enabled: true, last_run_id: "a1b2c3d4-6666-4000-8000-000000000006", created_at: h(168) },
  { id: "sch-002", workflow_name: "lead-enrichment", cron_expression: "0 8 * * 1-5", enabled: true, last_run_id: "a1b2c3d4-1111-4000-8000-000000000001", created_at: h(240) },
  { id: "sch-003", workflow_name: "seo-audit", cron_expression: "0 0 * * 0", enabled: false, last_run_id: null, created_at: h(48) },
];

const MOCK_DLQ = [
  { id: "dlq-001", run_id: "a1b2c3d4-4444-4000-8000-000000000004", step_id: "enrich", error: "Timeout after 300s - external API unreachable", attempts: 3, created_at: h(5), resolved_at: null, resolved_by: null },
  { id: "dlq-002", run_id: "a1b2c3d4-9999-4000-8000-000000000009", step_id: "analyze", error: "Rate limit exceeded (429) - retry after 60s", attempts: 3, created_at: h(30), resolved_at: null, resolved_by: null },
];

// Route matcher
type MockRoute = {
  match: RegExp;
  method?: string;
  handler: (params: Record<string, string>, body?: unknown) => unknown;
};

const routes: MockRoute[] = [
  {
    match: /^\/health$/,
    handler: () => ({ status: "ok", sandstorm: true, redis: true, database: true }),
  },
  {
    match: /^\/stats$/,
    handler: () => MOCK_STATS,
  },
  {
    match: /^\/workflows$/,
    method: "GET",
    handler: () => MOCK_WORKFLOWS,
  },
  {
    match: /^\/runs$/,
    handler: (_params) => {
      const status = _params.status;
      let filtered = MOCK_RUNS;
      if (status && status !== "all") {
        filtered = filtered.filter((r) => r.status === status);
      }
      const offset = Number(_params.offset || 0);
      const limit = Number(_params.limit || 20);
      return {
        _data: filtered.slice(offset, offset + limit),
        _meta: { total: filtered.length, limit, offset },
      };
    },
  },
  {
    match: /^\/runs\/([^/]+)$/,
    handler: (params) => getRunDetail(params._1),
  },
  {
    match: /^\/schedules$/,
    method: "GET",
    handler: () => ({
      _data: MOCK_SCHEDULES,
      _meta: { total: MOCK_SCHEDULES.length, limit: 50, offset: 0 },
    }),
  },
  {
    match: /^\/dead-letter$/,
    handler: () => ({
      _data: MOCK_DLQ,
      _meta: { total: MOCK_DLQ.length, limit: 50, offset: 0 },
    }),
  },
];

export function mockFetch(path: string, params?: Record<string, string>): ApiResponse {
  const mergedParams = params || {};

  for (const route of routes) {
    const m = path.match(route.match);
    if (m) {
      const extractedParams: Record<string, string> = { ...mergedParams };
      m.slice(1).forEach((val, i) => {
        extractedParams[`_${i + 1}`] = val;
      });

      const result = route.handler(extractedParams);

      // Handle routes that return _data/_meta separately
      if (result && typeof result === "object" && "_data" in (result as Record<string, unknown>)) {
        const r = result as { _data: unknown; _meta: unknown };
        return { data: r._data, error: null, meta: r._meta as ApiResponse["meta"] };
      }

      return { data: result, error: null };
    }
  }

  return { data: null, error: { code: "NOT_FOUND", message: `Mock: ${path} not found` } };
}
