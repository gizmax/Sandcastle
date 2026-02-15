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

  // Add budget for the first run to demo the BudgetBar
  const maxCost = runId === "a1b2c3d4-1111-4000-8000-000000000001" ? 0.15 : null;
  // Demo parent_run_id for the third run (replay)
  const parentRunId = runId === "a1b2c3d4-3333-4000-8000-000000000003"
    ? "a1b2c3d4-1111-4000-8000-000000000001"
    : null;
  const replayFromStep = parentRunId ? "analyze-technical" : null;

  return {
    ...run,
    input_data: { target_url: "https://example.com", max_depth: 3 },
    outputs: run.status === "completed" ? { final: "Lead enrichment complete" } : null,
    error: run.status === "failed" ? "Step 'enrich' failed after 3 attempts" : null,
    steps,
    max_cost_usd: maxCost,
    parent_run_id: parentRunId,
    replay_from_step: replayFromStep,
    fork_changes: null,
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
  {
    name: "Lead Enrichment",
    description: "Scrape target websites, enrich with company data, and score leads for sales outreach priority.",
    steps_count: 3,
    file_name: "lead-enrichment.yaml",
    steps: [
      { id: "scrape", model: "sonnet", depends_on: [] },
      { id: "enrich", model: "sonnet", depends_on: ["scrape"] },
      { id: "score", model: "haiku", depends_on: ["enrich"] },
    ],
  },
  {
    name: "Competitor Monitor",
    description: "Track competitor websites for changes, analyze differences, and generate a summary report.",
    steps_count: 4,
    file_name: "competitor-monitor.yaml",
    steps: [
      { id: "fetch-competitors", model: "sonnet", depends_on: [] },
      { id: "analyze", model: "sonnet", depends_on: ["fetch-competitors"] },
      { id: "summarize", model: "sonnet", depends_on: ["analyze"] },
      { id: "format-report", model: "haiku", depends_on: ["summarize"] },
    ],
  },
  {
    name: "SEO Audit",
    description: "Crawl a website, analyze on-page SEO factors, and produce actionable recommendations.",
    steps_count: 3,
    file_name: "seo-audit.yaml",
    steps: [
      { id: "crawl", model: "sonnet", depends_on: [] },
      { id: "analyze-technical", model: "sonnet", depends_on: ["crawl"] },
      { id: "recommendations", model: "haiku", depends_on: ["analyze-technical"] },
    ],
  },
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

const MOCK_APPROVALS = [
  {
    id: "apr-001",
    run_id: "a1b2c3d4-1111-4000-8000-000000000001",
    step_id: "review-report",
    status: "pending",
    message: "Review the Q4 competitor analysis report before sending to client",
    request_data: {
      report_title: "Q4 Competitor Analysis - Acme Corp",
      sections: ["Market Overview", "Pricing Changes", "Feature Comparison", "Recommendations"],
      generated_at: "2026-02-16T10:30:00Z",
      confidence_score: 0.92,
    },
    reviewer_comment: null,
    timeout_at: new Date(now.getTime() + 24 * 3600000).toISOString(),
    on_timeout: "abort",
    allow_edit: true,
    created_at: h(0.5),
    resolved_at: null,
  },
  {
    id: "apr-002",
    run_id: "a1b2c3d4-2222-4000-8000-000000000002",
    step_id: "approve-outreach",
    status: "pending",
    message: "Approve email outreach to 15 high-priority leads",
    request_data: {
      lead_count: 15,
      avg_score: 87,
      estimated_cost: "$0.45",
      template: "enterprise-intro-v2",
    },
    reviewer_comment: null,
    timeout_at: new Date(now.getTime() + 12 * 3600000).toISOString(),
    on_timeout: "skip",
    allow_edit: false,
    created_at: h(1.2),
    resolved_at: null,
  },
  {
    id: "apr-003",
    run_id: "a1b2c3d4-5555-4000-8000-000000000005",
    step_id: "validate-data",
    status: "approved",
    message: "Validate enriched company data before storage",
    request_data: {
      companies_enriched: 42,
      data_quality_score: 0.95,
      missing_fields: ["revenue"],
    },
    reviewer_comment: "Looks good, minor missing fields are acceptable",
    timeout_at: null,
    on_timeout: "abort",
    allow_edit: true,
    created_at: h(6),
    resolved_at: h(5.5),
  },
  {
    id: "apr-004",
    run_id: "a1b2c3d4-8888-4000-8000-000000000008",
    step_id: "publish-report",
    status: "rejected",
    message: "Publish SEO audit report to client portal",
    request_data: {
      report_pages: 12,
      critical_issues: 3,
      client: "TechStart Inc",
    },
    reviewer_comment: "Report contains outdated data, needs re-run with fresh crawl",
    timeout_at: null,
    on_timeout: "abort",
    allow_edit: false,
    created_at: h(26),
    resolved_at: h(25),
  },
  {
    id: "apr-005",
    run_id: "a1b2c3d4-6666-4000-8000-000000000006",
    step_id: "deploy-changes",
    status: "skipped",
    message: "Deploy pricing page changes to staging",
    request_data: null,
    reviewer_comment: null,
    timeout_at: null,
    on_timeout: "skip",
    allow_edit: false,
    created_at: h(48),
    resolved_at: h(47),
  },
];

const MOCK_AUTOPILOT_EXPERIMENTS = [
  {
    id: "exp-001",
    workflow_name: "lead-enrichment",
    step_id: "enrich",
    status: "active",
    optimize_for: "quality",
    config: { min_samples: 20, auto_deploy: true, sample_rate: 1.0 },
    deployed_variant_id: null,
    created_at: h(72),
    completed_at: null,
    samples: [
      { id: "s-001", variant_id: "baseline", quality_score: 7.2, cost_usd: 0.05, duration_seconds: 18.7 },
      { id: "s-002", variant_id: "baseline", quality_score: 6.8, cost_usd: 0.04, duration_seconds: 16.2 },
      { id: "s-003", variant_id: "baseline", quality_score: 7.5, cost_usd: 0.05, duration_seconds: 19.1 },
      { id: "s-004", variant_id: "baseline", quality_score: 7.1, cost_usd: 0.05, duration_seconds: 17.8 },
      { id: "s-005", variant_id: "baseline", quality_score: 6.9, cost_usd: 0.04, duration_seconds: 15.9 },
      { id: "s-006", variant_id: "opus-deep", quality_score: 9.1, cost_usd: 0.12, duration_seconds: 32.4 },
      { id: "s-007", variant_id: "opus-deep", quality_score: 8.8, cost_usd: 0.11, duration_seconds: 28.6 },
      { id: "s-008", variant_id: "opus-deep", quality_score: 9.3, cost_usd: 0.13, duration_seconds: 35.1 },
      { id: "s-009", variant_id: "opus-deep", quality_score: 8.9, cost_usd: 0.11, duration_seconds: 30.2 },
      { id: "s-010", variant_id: "haiku-fast", quality_score: 5.4, cost_usd: 0.01, duration_seconds: 4.2 },
      { id: "s-011", variant_id: "haiku-fast", quality_score: 5.8, cost_usd: 0.01, duration_seconds: 3.9 },
      { id: "s-012", variant_id: "haiku-fast", quality_score: 5.1, cost_usd: 0.01, duration_seconds: 4.5 },
      { id: "s-013", variant_id: "haiku-fast", quality_score: 5.6, cost_usd: 0.01, duration_seconds: 4.1 },
    ],
  },
  {
    id: "exp-002",
    workflow_name: "competitor-monitor",
    step_id: "analyze",
    status: "completed",
    optimize_for: "pareto",
    config: { min_samples: 15, auto_deploy: true, quality_threshold: 7.0 },
    deployed_variant_id: "balanced",
    created_at: h(168),
    completed_at: h(48),
    samples: [
      { id: "s-020", variant_id: "baseline", quality_score: 7.0, cost_usd: 0.05, duration_seconds: 20.1 },
      { id: "s-021", variant_id: "baseline", quality_score: 7.2, cost_usd: 0.05, duration_seconds: 19.3 },
      { id: "s-022", variant_id: "baseline", quality_score: 6.8, cost_usd: 0.04, duration_seconds: 18.7 },
      { id: "s-023", variant_id: "baseline", quality_score: 7.1, cost_usd: 0.05, duration_seconds: 21.0 },
      { id: "s-024", variant_id: "baseline", quality_score: 7.3, cost_usd: 0.05, duration_seconds: 19.8 },
      { id: "s-025", variant_id: "balanced", quality_score: 8.1, cost_usd: 0.03, duration_seconds: 12.4 },
      { id: "s-026", variant_id: "balanced", quality_score: 8.4, cost_usd: 0.03, duration_seconds: 11.8 },
      { id: "s-027", variant_id: "balanced", quality_score: 7.9, cost_usd: 0.03, duration_seconds: 13.1 },
      { id: "s-028", variant_id: "balanced", quality_score: 8.2, cost_usd: 0.03, duration_seconds: 12.0 },
      { id: "s-029", variant_id: "balanced", quality_score: 8.0, cost_usd: 0.03, duration_seconds: 12.7 },
      { id: "s-030", variant_id: "thorough", quality_score: 9.0, cost_usd: 0.09, duration_seconds: 28.3 },
      { id: "s-031", variant_id: "thorough", quality_score: 8.7, cost_usd: 0.08, duration_seconds: 26.1 },
      { id: "s-032", variant_id: "thorough", quality_score: 9.2, cost_usd: 0.10, duration_seconds: 30.5 },
      { id: "s-033", variant_id: "thorough", quality_score: 8.9, cost_usd: 0.09, duration_seconds: 27.8 },
      { id: "s-034", variant_id: "thorough", quality_score: 9.1, cost_usd: 0.09, duration_seconds: 29.0 },
    ],
  },
  {
    id: "exp-003",
    workflow_name: "seo-audit",
    step_id: "recommendations",
    status: "active",
    optimize_for: "cost",
    config: { min_samples: 10, auto_deploy: false, sample_rate: 0.5 },
    deployed_variant_id: null,
    created_at: h(24),
    completed_at: null,
    samples: [
      { id: "s-040", variant_id: "sonnet", quality_score: 7.8, cost_usd: 0.04, duration_seconds: 14.2 },
      { id: "s-041", variant_id: "sonnet", quality_score: 8.0, cost_usd: 0.04, duration_seconds: 15.1 },
      { id: "s-042", variant_id: "haiku", quality_score: 6.5, cost_usd: 0.008, duration_seconds: 3.8 },
      { id: "s-043", variant_id: "haiku", quality_score: 6.2, cost_usd: 0.007, duration_seconds: 3.5 },
    ],
  },
];

const MOCK_AUTOPILOT_STATS = {
  total_experiments: 3,
  active_experiments: 2,
  completed_experiments: 1,
  total_samples: 32,
  avg_quality_improvement: 0.18,
  total_cost_savings_usd: 1.24,
};

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
  {
    match: /^\/approvals$/,
    handler: (params) => {
      let filtered = MOCK_APPROVALS;
      if (params.status && params.status !== "all") {
        filtered = filtered.filter((a) => a.status === params.status);
      }
      return filtered;
    },
  },
  {
    match: /^\/autopilot\/experiments$/,
    handler: () => MOCK_AUTOPILOT_EXPERIMENTS,
  },
  {
    match: /^\/autopilot\/stats$/,
    handler: () => MOCK_AUTOPILOT_STATS,
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
