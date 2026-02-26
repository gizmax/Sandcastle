import type { ApiResponse } from "./client";

// NOTE: GET /events is a Server-Sent Events (SSE) endpoint - cannot be mocked
// via mockFetch. The endpoint streams real-time events with the following format:
//
//   event: run.started | run.completed | run.failed |
//          step.started | step.completed | step.failed | dlq.new
//   data: {"type": "<event_type>", "data": {...}, "timestamp": "ISO8601"}
//
// Event data payloads:
//   run.started    -> { run_id, workflow }
//   run.completed  -> { run_id, status, workflow, duration_seconds, total_cost_usd }
//   run.failed     -> { run_id, workflow, error }
//   step.started   -> { run_id, step_name, workflow }
//   step.completed -> { run_id, step_name, status, cost_usd, duration_seconds }
//   step.failed    -> { run_id, step_name, error }
//   dlq.new        -> { run_id, step_name, error }
//
// Connect from the frontend with: new EventSource("/events")

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
  { run_id: "a1b2c3d4-1111-4000-8000-000000000001", workflow_name: "lead-enrichment", status: "completed", total_cost_usd: 1.84, started_at: h(0.5), completed_at: h(0.45) },
  { run_id: "a1b2c3d4-2222-4000-8000-000000000002", workflow_name: "competitor-monitor", status: "running", total_cost_usd: 0.67, started_at: h(0.1), completed_at: null },
  { run_id: "a1b2c3d4-3333-4000-8000-000000000003", workflow_name: "seo-audit", status: "completed", total_cost_usd: 1.23, started_at: h(2), completed_at: h(1.9) },
  { run_id: "a1b2c3d4-4444-4000-8000-000000000004", workflow_name: "lead-enrichment", status: "failed", total_cost_usd: 0.41, started_at: h(5), completed_at: h(4.95) },
  { run_id: "a1b2c3d4-5555-4000-8000-000000000005", workflow_name: "lead-enrichment", status: "completed", total_cost_usd: 1.72, started_at: h(8), completed_at: h(7.9) },
  { run_id: "a1b2c3d4-6666-4000-8000-000000000006", workflow_name: "competitor-monitor", status: "completed", total_cost_usd: 1.35, started_at: h(12), completed_at: h(11.8) },
  { run_id: "a1b2c3d4-7777-4000-8000-000000000007", workflow_name: "seo-audit", status: "completed", total_cost_usd: 0.98, started_at: h(18), completed_at: h(17.9) },
  { run_id: "a1b2c3d4-8888-4000-8000-000000000008", workflow_name: "lead-enrichment", status: "completed", total_cost_usd: 2.16, started_at: h(24), completed_at: h(23.8) },
  { run_id: "a1b2c3d4-9999-4000-8000-000000000009", workflow_name: "competitor-monitor", status: "failed", total_cost_usd: 0.29, started_at: h(30), completed_at: h(29.9) },
  { run_id: "a1b2c3d4-aaaa-4000-8000-00000000000a", workflow_name: "seo-audit", status: "completed", total_cost_usd: 0.87, started_at: h(36), completed_at: h(35.8) },
  { run_id: "a1b2c3d4-bbbb-4000-8000-00000000000b", workflow_name: "lead-enrichment", status: "completed", total_cost_usd: 1.54, started_at: h(48), completed_at: h(47.5) },
  { run_id: "a1b2c3d4-cccc-4000-8000-00000000000c", workflow_name: "lead-enrichment", status: "completed", total_cost_usd: 1.97, started_at: h(60), completed_at: h(59.8) },
];

const MOCK_STEPS: MockStep[] = [
  { step_id: "scrape", parallel_index: null, status: "completed", output: { url: "https://example.com", title: "Example Corp", employees: 150 }, cost_usd: 0.52, duration_seconds: 12.3, attempt: 1, error: null },
  { step_id: "enrich", parallel_index: null, status: "completed", output: { company: "Example Corp", revenue: "$50M", industry: "SaaS", decision_makers: ["John CEO", "Jane CTO"] }, cost_usd: 0.89, duration_seconds: 18.7, attempt: 1, error: null },
  { step_id: "score", parallel_index: null, status: "completed", output: { lead_score: 87, tier: "A", recommendation: "High priority - schedule demo this week" }, cost_usd: 0.43, duration_seconds: 8.2, attempt: 1, error: null },
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
  const maxCost = runId === "a1b2c3d4-1111-4000-8000-000000000001" ? 2.50 : null;
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
  total_cost_today: 7.82,
  avg_duration_seconds: 42.3,
  runs_by_day: Array.from({ length: 30 }, (_, i) => {
    const completed = Math.floor(Math.random() * 12) + 2;
    const failed = Math.floor(Math.random() * 3);
    return { date: d(29 - i), completed, failed, total: completed + failed };
  }),
  cost_by_workflow: [
    { workflow: "lead-enrichment", cost: 14.58 },
    { workflow: "competitor-monitor", cost: 8.34 },
    { workflow: "seo-audit", cost: 4.72 },
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
    input_schema: {
      required: ["company_url"],
      properties: {
        company_url: { type: "string", description: "Target company website URL" },
        max_leads: { type: "number", description: "Maximum number of leads to enrich", default: 10 },
      },
    },
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
    input_schema: {
      required: ["competitors"],
      properties: {
        competitors: { type: "string", description: "Comma-separated list of competitor URLs" },
        focus_area: { type: "string", description: "Area to focus on (pricing, features, content)", default: "all" },
      },
    },
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
    input_schema: {
      required: ["url"],
      properties: {
        url: { type: "string", description: "Website URL to audit" },
        max_pages: { type: "number", description: "Maximum pages to crawl", default: 50 },
      },
    },
  },
];

const MOCK_SCHEDULES = [
  { id: "sch-001", workflow_name: "competitor-monitor", cron_expression: "0 */6 * * *", enabled: true, last_run_id: "a1b2c3d4-6666-4000-8000-000000000006", created_at: h(168) },
  { id: "sch-002", workflow_name: "lead-enrichment", cron_expression: "0 8 * * 1-5", enabled: true, last_run_id: "a1b2c3d4-1111-4000-8000-000000000001", created_at: h(240) },
  { id: "sch-003", workflow_name: "seo-audit", cron_expression: "0 0 * * 0", enabled: false, last_run_id: null, created_at: h(48) },
];

const MOCK_API_KEYS = [
  { id: "key-001", key_prefix: "sc_live_abc1", tenant_id: "acme-corp", name: "Production API", created_at: h(720), last_used_at: h(0.3) },
  { id: "key-002", key_prefix: "sc_live_def2", tenant_id: "acme-corp", name: "Staging API", created_at: h(480), last_used_at: h(12) },
  { id: "key-003", key_prefix: "sc_test_ghi3", tenant_id: "beta-inc", name: "Development", created_at: h(168), last_used_at: null },
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

const MOCK_EVAL_RUNS: EvalRun[] = [
  {
    id: "eval-001",
    suite_name: "Summarize workflow regression tests",
    workflow_name: "lead-enrichment",
    status: "completed",
    total_cases: 4,
    passed_cases: 3,
    failed_cases: 1,
    pass_rate: 0.75,
    total_cost_usd: 2.34,
    total_duration_seconds: 48.2,
    started_at: h(2),
    completed_at: h(1.9),
    created_at: h(2),
    cases: [
      {
        case_name: "short text",
        passed: true,
        run_id: "a1b2c3d4-e001-4000-8000-000000000001",
        cost_usd: 0.52,
        duration_seconds: 12.3,
        assertions: [
          { type: "not_empty", passed: true, expected: "non-empty output", actual: "dict", message: "", score: null },
          { type: "llm_judge", passed: true, expected: ">= 0.7", actual: "0.85", message: "", score: 0.85 },
          { type: "max_cost", passed: true, expected: "<= $0.0500", actual: "$0.0420", message: "", score: null },
        ],
        output_summary: "Lead enrichment complete - 3 leads found",
        error: null,
      },
      {
        case_name: "long text",
        passed: true,
        run_id: "a1b2c3d4-e002-4000-8000-000000000002",
        cost_usd: 0.89,
        duration_seconds: 18.7,
        assertions: [
          { type: "contains", passed: true, expected: "lead", actual: "Lead enrichment data...", message: "", score: null },
          { type: "max_duration", passed: true, expected: "<= 30s", actual: "18.7s", message: "", score: null },
        ],
        output_summary: "Detailed enrichment with 12 data points",
        error: null,
      },
      {
        case_name: "empty input",
        passed: false,
        run_id: "a1b2c3d4-e003-4000-8000-000000000003",
        cost_usd: 0.41,
        duration_seconds: 8.2,
        assertions: [
          { type: "not_empty", passed: false, expected: "non-empty output", actual: "None", message: "Output is empty or None", score: null },
          { type: "llm_judge", passed: false, expected: ">= 0.7", actual: "0.20", message: "LLM judge score 0.20 below threshold 0.70", score: 0.2 },
        ],
        output_summary: null,
        error: "Step 'scrape' returned empty result",
      },
      {
        case_name: "special characters",
        passed: true,
        run_id: "a1b2c3d4-e004-4000-8000-000000000004",
        cost_usd: 0.52,
        duration_seconds: 9.0,
        assertions: [
          { type: "not_empty", passed: true, expected: "non-empty output", actual: "dict", message: "", score: null },
          { type: "regex_match", passed: true, expected: "\\w+", actual: "Lead data found", message: "", score: null },
        ],
        output_summary: "Handled special characters correctly",
        error: null,
      },
    ],
  },
  {
    id: "eval-002",
    suite_name: "SEO audit quality checks",
    workflow_name: "seo-audit",
    status: "completed",
    total_cases: 3,
    passed_cases: 3,
    failed_cases: 0,
    pass_rate: 1.0,
    total_cost_usd: 1.87,
    total_duration_seconds: 35.4,
    started_at: h(24),
    completed_at: h(23.8),
    created_at: h(24),
    cases: [
      {
        case_name: "basic site audit",
        passed: true,
        run_id: "a1b2c3d4-e010-4000-8000-000000000010",
        cost_usd: 0.65,
        duration_seconds: 12.1,
        assertions: [
          { type: "not_empty", passed: true, expected: "non-empty output", actual: "dict", message: "", score: null },
          { type: "contains", passed: true, expected: "recommendation", actual: "SEO recommendations...", message: "", score: null },
        ],
        output_summary: "5 recommendations generated",
        error: null,
      },
      {
        case_name: "large site audit",
        passed: true,
        run_id: "a1b2c3d4-e011-4000-8000-000000000011",
        cost_usd: 0.72,
        duration_seconds: 15.3,
        assertions: [
          { type: "not_empty", passed: true, expected: "non-empty output", actual: "dict", message: "", score: null },
          { type: "max_cost", passed: true, expected: "<= $1.0000", actual: "$0.7200", message: "", score: null },
        ],
        output_summary: "12 recommendations generated",
        error: null,
      },
      {
        case_name: "mobile-only audit",
        passed: true,
        run_id: "a1b2c3d4-e012-4000-8000-000000000012",
        cost_usd: 0.50,
        duration_seconds: 8.0,
        assertions: [
          { type: "contains", passed: true, expected: "mobile", actual: "Mobile optimization...", message: "", score: null },
        ],
        output_summary: "Mobile-specific audit complete",
        error: null,
      },
    ],
  },
  {
    id: "eval-003",
    suite_name: "Competitor monitor regression",
    workflow_name: "competitor-monitor",
    status: "completed",
    total_cases: 2,
    passed_cases: 1,
    failed_cases: 1,
    pass_rate: 0.5,
    total_cost_usd: 1.12,
    total_duration_seconds: 22.8,
    started_at: h(72),
    completed_at: h(71.5),
    created_at: h(72),
    cases: [
      {
        case_name: "single competitor",
        passed: true,
        run_id: "a1b2c3d4-e020-4000-8000-000000000020",
        cost_usd: 0.56,
        duration_seconds: 11.2,
        assertions: [
          { type: "not_empty", passed: true, expected: "non-empty output", actual: "dict", message: "", score: null },
        ],
        output_summary: "Analysis complete",
        error: null,
      },
      {
        case_name: "multiple competitors",
        passed: false,
        run_id: "a1b2c3d4-e021-4000-8000-000000000021",
        cost_usd: 0.56,
        duration_seconds: 11.6,
        assertions: [
          { type: "not_empty", passed: true, expected: "non-empty output", actual: "dict", message: "", score: null },
          { type: "max_duration", passed: false, expected: "<= 10s", actual: "11.6s", message: "Duration 11.6s exceeds limit 10.0s", score: null },
        ],
        output_summary: "Analysis complete but slow",
        error: null,
      },
    ],
  },
];

interface EvalRun {
  id: string;
  suite_name: string;
  workflow_name: string;
  status: string;
  total_cases: number;
  passed_cases: number;
  failed_cases: number;
  pass_rate: number;
  total_cost_usd: number;
  total_duration_seconds: number;
  started_at: string | null;
  completed_at: string | null;
  created_at: string | null;
  cases: {
    case_name: string;
    passed: boolean;
    run_id: string | null;
    cost_usd: number;
    duration_seconds: number;
    assertions: {
      type: string;
      passed: boolean;
      expected: unknown;
      actual: unknown;
      message: string;
      score: number | null;
    }[];
    output_summary: string | null;
    error: string | null;
  }[] | null;
}

const MOCK_EVAL_STATS = {
  total_runs: 3,
  avg_pass_rate: 0.75,
  total_cost_usd: 5.33,
  last_run_at: h(2),
  pass_rate_trend: Array.from({ length: 14 }, (_, i) => ({
    date: d(13 - i),
    avg_pass_rate: 0.6 + Math.random() * 0.35,
    runs: Math.floor(Math.random() * 3) + 1,
  })),
};

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

const MOCK_VIOLATIONS = [
  {
    id: "vio-001",
    run_id: "a1b2c3d4-1111-4000-8000-000000000001",
    step_id: "enrich",
    policy_id: "pii-redact",
    severity: "critical",
    action_taken: "redacted",
    trigger_details: "PII detected in output: email address john.doe@example.com and SSN 123-45-6789 found in enrichment response. Content was automatically redacted before passing to next step.",
    output_modified: true,
    created_at: h(1),
  },
  {
    id: "vio-002",
    run_id: "a1b2c3d4-2222-4000-8000-000000000002",
    step_id: "analyze",
    policy_id: "cost-guard",
    severity: "high",
    action_taken: "blocked",
    trigger_details: "Step cost $0.18 exceeds per-step budget limit of $0.10. Execution blocked to prevent budget overrun.",
    output_modified: false,
    created_at: h(3),
  },
  {
    id: "vio-003",
    run_id: "a1b2c3d4-3333-4000-8000-000000000003",
    step_id: "score",
    policy_id: "secret-block",
    severity: "critical",
    action_taken: "blocked",
    trigger_details: "Potential API key detected in prompt: sk-proj-abc...xyz. Step execution blocked. Remove secrets from workflow input before retrying.",
    output_modified: false,
    created_at: h(6),
  },
  {
    id: "vio-004",
    run_id: "a1b2c3d4-5555-4000-8000-000000000005",
    step_id: "enrich",
    policy_id: "pii-redact",
    severity: "medium",
    action_taken: "redacted",
    trigger_details: "Phone number +1-555-0123 detected in output field 'contact_info'. Number was replaced with [REDACTED].",
    output_modified: true,
    created_at: h(12),
  },
  {
    id: "vio-005",
    run_id: "a1b2c3d4-6666-4000-8000-000000000006",
    step_id: "summarize",
    policy_id: "length-limit",
    severity: "low",
    action_taken: "flagged",
    trigger_details: "Output length 4,200 tokens exceeds soft limit of 4,000 tokens. Flagged for review but execution continued.",
    output_modified: false,
    created_at: h(24),
  },
  {
    id: "vio-006",
    run_id: "a1b2c3d4-8888-4000-8000-000000000008",
    step_id: "analyze",
    policy_id: "cost-guard",
    severity: "high",
    action_taken: "blocked",
    trigger_details: "Cumulative run cost $0.42 exceeds max_cost_usd budget of $0.30. Remaining steps skipped.",
    output_modified: false,
    created_at: h(36),
  },
];

const MOCK_VIOLATION_STATS = {
  total_violations_30d: 23,
  violations_by_severity: { critical: 2, high: 8, medium: 10, low: 3 },
  violations_by_policy: { "pii-redact": 12, "cost-guard": 6, "secret-block": 3, "length-limit": 2 },
  violations_by_day: Array.from({ length: 30 }, (_, i) => ({
    date: d(29 - i),
    count: Math.floor(Math.random() * 4),
  })),
};

const MOCK_OPTIMIZER_DECISIONS = [
  {
    id: "opt-001",
    run_id: "a1b2c3d4-1111-4000-8000-000000000001",
    step_id: "enrich",
    selected_model: "sonnet",
    confidence: 0.92,
    reason: "High complexity step with structured output requirements. Sonnet provides best quality-cost ratio for data enrichment tasks.",
    budget_pressure: 0.3,
    alternatives: [
      { id: "sonnet-v1", model: "sonnet", avg_quality: 0.92, avg_cost: 0.08 },
      { id: "haiku-v1", model: "haiku", avg_quality: 0.61, avg_cost: 0.02 },
      { id: "opus-v1", model: "opus", avg_quality: 0.88, avg_cost: 0.15 },
    ],
    slo: { quality_min: 0.7, cost_max_usd: 0.10, latency_max_seconds: 30, optimize_for: "balanced" },
    created_at: h(0.5),
  },
  {
    id: "opt-002",
    run_id: "a1b2c3d4-2222-4000-8000-000000000002",
    step_id: "fetch-competitors",
    selected_model: "haiku",
    confidence: 0.88,
    reason: "Simple data retrieval step. Haiku sufficient for structured extraction with minimal reasoning.",
    budget_pressure: 0.1,
    alternatives: [
      { id: "haiku-v1", model: "haiku", avg_quality: 0.88, avg_cost: 0.02 },
      { id: "sonnet-v1", model: "sonnet", avg_quality: 0.72, avg_cost: 0.08 },
    ],
    slo: { quality_min: 0.5, cost_max_usd: 0.05, latency_max_seconds: 15, optimize_for: "cost" },
    created_at: h(2),
  },
  {
    id: "opt-003",
    run_id: "a1b2c3d4-3333-4000-8000-000000000003",
    step_id: "recommendations",
    selected_model: "opus",
    confidence: 0.45,
    reason: "Complex reasoning required for actionable SEO recommendations. Low confidence due to limited historical data for this step type.",
    budget_pressure: 0.92,
    alternatives: [
      { id: "opus-v1", model: "opus", avg_quality: 0.45, avg_cost: 0.15 },
      { id: "sonnet-v1", model: "sonnet", avg_quality: 0.42, avg_cost: 0.08 },
      { id: "haiku-v1", model: "haiku", avg_quality: 0.18, avg_cost: 0.02 },
    ],
    slo: { quality_min: 0.8, cost_max_usd: 0.20, latency_max_seconds: 60, optimize_for: "quality" },
    created_at: h(5),
  },
  {
    id: "opt-004",
    run_id: "a1b2c3d4-5555-4000-8000-000000000005",
    step_id: "score",
    selected_model: "haiku",
    confidence: 0.78,
    reason: "Lead scoring uses a fixed rubric. Haiku handles structured scoring well within quality SLO.",
    budget_pressure: null,
    alternatives: [
      { id: "haiku-v1", model: "haiku", avg_quality: 0.78, avg_cost: 0.02 },
      { id: "sonnet-v1", model: "sonnet", avg_quality: 0.65, avg_cost: 0.08 },
    ],
    slo: { quality_min: 0.6, cost_max_usd: 0.03, latency_max_seconds: 10, optimize_for: "cost" },
    created_at: h(8),
  },
  {
    id: "opt-005",
    run_id: "a1b2c3d4-6666-4000-8000-000000000006",
    step_id: "analyze",
    selected_model: "sonnet",
    confidence: 0.85,
    reason: "Competitor analysis requires nuanced comparison. Sonnet selected as best balance under current budget pressure.",
    budget_pressure: 0.75,
    alternatives: [
      { id: "sonnet-v1", model: "sonnet", avg_quality: 0.85, avg_cost: 0.08 },
      { id: "opus-v1", model: "opus", avg_quality: 0.82, avg_cost: 0.15 },
      { id: "haiku-v1", model: "haiku", avg_quality: 0.39, avg_cost: 0.02 },
    ],
    slo: { quality_min: 0.7, cost_max_usd: 0.08, latency_max_seconds: 45, optimize_for: "balanced" },
    created_at: h(12),
  },
];

const MOCK_OPTIMIZER_STATS = {
  total_decisions_30d: 156,
  model_distribution: { haiku: 0.45, sonnet: 0.40, opus: 0.15 },
  avg_confidence: 0.72,
  estimated_savings_30d_usd: 3.45,
};

const MOCK_TEMPLATES = [
  // --- general_ai ---
  {
    name: "chain-of-thought-solver",
    description: "Advanced problem solver using structured decomposition, parallel research and reasoning tracks, synthesis, and solution validation",
    tags: ["reasoning", "chain-of-thought", "problem-solving"],
    step_count: 5,
    category: "general_ai",
    input_schema: {"required": ["problem"], "properties": {"problem": {"type": "string", "description": "The problem to decompose and solve"}, "constraints": {"type": "string", "description": "Known constraints, requirements, or boundary conditions that the solution must satisfy", "default": ""}, "depth": {"type": "string", "description": "Analysis depth - quick (surface-level), standard (balanced), or deep (exhaustive)", "default": "standard"}}},
  },
  {
    name: "clinical-notes",
    description: "Process clinical encounter data into structured SOAP notes, extract diagnoses with ICD-10 codes, generate billing codes, and flag compliance issues",
    tags: ["Healthcare", "Clinical", "SOAP", "ICD-10", "Compliance"],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["encounter_data", "patient_context"], "properties": {"encounter_data": {"type": "string", "description": "Raw clinical encounter data - can be transcribed dictation, free-text notes, or structured intake form data describing the patient visit"}, "patient_context": {"type": "string", "description": "Patient context including age, sex, relevant medical history, current medications, allergies, and reason for visit"}, "specialty": {"type": "string", "description": "Clinical specialty context (e.g. 'general', 'cardiology', 'orthopedics', 'psychiatry', 'pediatrics', 'emergency')", "default": "general"}, "documentation_standard": {"type": "string", "description": "Documentation format standard to follow: 'SOAP', 'H&P', 'progress_note', 'discharge_summary'", "default": "SOAP"}}},
  },
  {
    name: "course-creator",
    description: "Generate complete online course content from a topic - outline, lesson scripts, quizzes, assignments, and platform-ready export",
    tags: ["Education", "Course-Design", "E-Learning", "Content"],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["course_topic", "target_audience", "difficulty_level", "course_length"], "properties": {"course_topic": {"type": "string", "description": "The subject of the course (e.g. 'Machine Learning for Product Managers', 'Advanced SQL for Data Engineers', 'Introduction to UX Research')"}, "target_audience": {"type": "string", "description": "Who the course is designed for (e.g. 'junior developers with 1-2 years experience', 'marketing professionals transitioning to data analytics', 'complete beginners with no technical background')"}, "difficulty_level": {"type": "string", "description": "Course difficulty: 'beginner', 'intermediate', or 'advanced'"}, "course_length": {"type": "string", "description": "Target course duration in hours (e.g. '4', '10', '20')"}, "platform": {"type": "string", "description": "Target learning platform: 'udemy', 'coursera', 'teachable', 'skillshare', 'general' (default: 'general')", "default": "general"}}},
  },
  {
    name: "demand-forecasting",
    description: "Multi-signal demand forecasting combining statistical models, market intelligence, and social trends for inventory planning",
    tags: ["Supply-Chain", "Forecasting", "Analytics", "Inventory"],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["product_category"], "properties": {"product_category": {"type": "string", "description": "Product category or SKU group to forecast (e.g. 'outdoor furniture', 'GPU accelerators', 'organic snacks')"}, "historical_period": {"type": "string", "description": "Historical data window to consider (default: '12 months')", "default": "12 months"}, "forecast_horizon": {"type": "string", "description": "How far ahead to forecast (default: 'next quarter')", "default": "next quarter"}}},
  },
  {
    name: "dynamic-pricing",
    description: "Optimize product pricing using competitor intelligence, demand elasticity, and margin analysis with A/B test design",
    tags: ["Pricing", "E-commerce", "Analytics", "Revenue"],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["product_catalog", "competitor_urls"], "properties": {"product_catalog": {"type": "string", "description": "Product catalog description or SKU list to optimize pricing for (e.g. 'premium headphones line - 5 SKUs')"}, "competitor_urls": {"type": "string", "description": "Comma-separated competitor store URLs or names to monitor (e.g. 'bestbuy.com, amazon.com/headphones')"}, "pricing_strategy": {"type": "string", "description": "Pricing approach: 'value-based', 'competitive', 'premium', or 'penetration' (default: 'value-based')", "default": "value-based"}, "margin_floor": {"type": "number", "description": "Minimum acceptable gross margin percentage (default: 20)", "default": 20}}},
  },
  {
    name: "earnings-call-intelligence",
    description: "Analyze earnings call transcripts for sentiment, key metrics, and investment insights",
    tags: ["Finance", "Investment", "Analytics", "Intelligence"],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["company_ticker", "sector"], "properties": {"company_ticker": {"type": "string", "description": "Stock ticker symbol of the company (e.g. AAPL, MSFT, TSLA)"}, "sector": {"type": "string", "description": "Industry sector (e.g. technology, healthcare, financials, consumer)"}, "benchmark_peers": {"type": "string", "description": "Comma-separated ticker symbols of peer companies for comparison"}}},
  },
  {
    name: "freelancer-proposal-generator",
    description: "Generate winning freelancer proposals by analyzing project requirements, matching portfolio pieces, crafting personalized pitches, and optimizing pricing strategy",
    tags: ["Freelance", "Proposals", "Upwork", "Pricing", "Business"],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["project_description", "freelancer_skills"], "properties": {"project_description": {"type": "string", "description": "Full text of the client's project posting, job description, or RFP (paste the complete listing)"}, "freelancer_skills": {"type": "string", "description": "Comma-separated list of your core skills and expertise areas (e.g. 'React, Node.js, AWS, PostgreSQL, 8 years experience')"}, "portfolio_highlights": {"type": "string", "description": "Description of relevant past projects, case studies, or portfolio pieces to reference in the proposal", "default": "no specific portfolio provided"}, "hourly_rate": {"type": "string", "description": "Your standard hourly rate or rate range (e.g. '$85/hr', '$75-100/hr', '$5000 fixed for this type')", "default": "market rate"}, "platform": {"type": "string", "description": "Freelance platform the proposal is for (affects formatting and optimization strategy)", "default": "upwork"}}},
  },
  {
    name: "grant-proposal",
    description: "Parse grant RFPs, check eligibility, draft comprehensive proposals with budgets, and generate compliance checklists",
    tags: ["Grant", "Proposal", "RFP", "Funding", "Compliance"],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["organization_name", "grant_program", "rfp_url_or_text", "project_description", "requested_amount"], "properties": {"organization_name": {"type": "string", "description": "Name of the applicant organization"}, "grant_program": {"type": "string", "description": "Name of the grant program or funding opportunity"}, "rfp_url_or_text": {"type": "string", "description": "Full text of the RFP/FOA, or URL to the funding opportunity announcement"}, "project_description": {"type": "string", "description": "Brief description of the proposed project (goals, approach, expected outcomes)"}, "requested_amount": {"type": "number", "description": "Total amount of funding requested in USD"}}},
  },
  {
    name: "invoice-processor",
    description: "Extract data from invoices using OCR patterns, validate against PO records, detect anomalies, route for approval, and generate accounting entries",
    tags: ["Finance", "Invoices", "AP", "Accounting", "Audit"],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["invoice_source", "company_name"], "properties": {"invoice_source": {"type": "string", "description": "Invoice data source or batch identifier (e.g. 'email inbox', 'AP folder', 'EDI feed', or raw invoice text/data)"}, "company_name": {"type": "string", "description": "Company name for matching against internal records"}, "approval_threshold": {"type": "string", "description": "Dollar amount above which invoices require additional approval (default: 10000)", "default": "10000"}, "accounting_system": {"type": "string", "description": "Target accounting system for journal entries (e.g. 'QuickBooks', 'NetSuite', 'Xero', 'SAP')", "default": "QuickBooks"}}},
  },
  {
    name: "language-translator",
    description: "Professional-grade translation with source analysis, cultural adaptation, precise translation, and quality review",
    tags: ["translation", "language", "i18n"],
    step_count: 4,
    category: "general_ai",
    input_schema: {"required": ["text", "target_language"], "properties": {"text": {"type": "string", "description": "The text to translate"}, "target_language": {"type": "string", "description": "The target language to translate into (e.g. 'Spanish', 'Japanese', 'Czech')"}, "tone": {"type": "string", "description": "Desired tone - professional, casual, formal, literary, technical, or marketing", "default": "professional"}, "domain": {"type": "string", "description": "Subject domain for terminology accuracy - general, legal, medical, technical, financial, academic, or marketing", "default": "general"}}},
  },
  {
    name: "pdf-summary",
    description: "Deep PDF document analysis with parallel content extraction and structure mapping, producing focused key findings and an executive brief",
    tags: ["pdf", "summarization", "parallel", "documents"],
    step_count: 5,
    category: "general_ai",
    input_schema: {"required": ["directory"], "properties": {"directory": {"type": "string", "description": "Path to directory containing PDF files", "default": "~/Desktop"}, "focus_areas": {"type": "string", "description": "Specific topics, questions, or areas of interest to prioritize in the analysis (e.g. 'financial performance, risk factors, growth strategy')", "default": ""}, "output_format": {"type": "string", "description": "Desired output format - executive-brief, detailed-report, comparison-table, or slide-notes", "default": "executive-brief"}}},
  },
  {
    name: "product-feedback-prioritizer",
    description: "Aggregate product feedback from multiple channels, deduplicate, cluster by theme, score by business impact, and produce a prioritized roadmap",
    tags: ["Product", "Feedback", "Prioritization", "Roadmap", "RICE"],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["product_name", "feedback_sources"], "properties": {"product_name": {"type": "string", "description": "Name of the product to analyze feedback for"}, "feedback_sources": {"type": "string", "description": "Comma-separated feedback channels (e.g. 'Intercom, Canny, Slack, G2, Zendesk, App Store')"}, "time_period": {"type": "string", "description": "Time period to analyze (default: last 90 days)", "default": "last 90 days"}}},
  },
  {
    name: "real-estate-listing",
    description: "Optimize real estate listings with AI-enhanced descriptions, comparative market analysis, pricing recommendations, and multi-platform distribution strategy",
    tags: ["Real Estate", "Listing", "CMA", "Marketing", "Property"],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["property_address", "property_type", "listing_price", "key_features"], "properties": {"property_address": {"type": "string", "description": "Full property address including city, state, and ZIP code"}, "property_type": {"type": "string", "description": "Type of property: residential, commercial, or land", "default": "residential"}, "listing_price": {"type": "number", "description": "Proposed listing price in USD"}, "key_features": {"type": "string", "description": "Key property features (bedrooms, bathrooms, sqft, lot size, renovations, amenities, etc.)"}, "target_buyer_profile": {"type": "string", "description": "Ideal buyer demographic and psychographic profile", "default": "General buyer seeking move-in ready property"}}},
  },
  {
    name: "research-agent",
    description: "Systematic research with scope definition, three parallel investigation tracks, rigorous synthesis, and a structured research report",
    tags: ["research", "parallel", "analysis", "extraction"],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["topic"], "properties": {"topic": {"type": "string", "description": "The research topic to investigate"}, "research_questions": {"type": "string", "description": "Specific questions the research should answer (comma-separated)", "default": ""}, "depth": {"type": "string", "description": "Research depth - survey (broad overview), standard (balanced), or deep-dive (exhaustive)", "default": "standard"}, "perspective": {"type": "string", "description": "Analytical lens - neutral, critical, comparative, or advocate", "default": "neutral"}}},
  },
  {
    name: "review-and-approve",
    description: "Comprehensive content generation with parallel quality and compliance checks, revision suggestions, and human approval gate",
    tags: ["approval", "human-in-the-loop", "content", "review"],
    step_count: 5,
    category: "general_ai",
    input_schema: {"required": ["brief", "audience"], "properties": {"brief": {"type": "string", "description": "The content brief describing what to generate"}, "audience": {"type": "string", "description": "The target audience for the content"}, "review_criteria": {"type": "string", "description": "Specific criteria the content must meet (e.g. 'must include 3 case studies, cite sources, stay under 2000 words')", "default": "Accuracy, clarity, engagement, and completeness"}, "standards": {"type": "string", "description": "Quality or compliance standards to check against (e.g. 'AP Style Guide', 'GDPR compliant', 'brand voice guidelines')", "default": "Professional writing standards with clear sourcing and factual accuracy"}}},
  },
  {
    name: "supplier-risk-intelligence",
    description: "Assess supplier portfolio risks across financial, geopolitical, and ESG dimensions with alternative sourcing recommendations",
    tags: ["Supply-Chain", "Risk", "Procurement", "ESG"],
    step_count: 7,
    category: "general_ai",
    input_schema: {"required": ["supplier_list", "industry"], "properties": {"supplier_list": {"type": "string", "description": "Comma-separated list of supplier names to evaluate (e.g. 'Foxconn, TSMC, Samsung SDI')"}, "industry": {"type": "string", "description": "Industry vertical for context (e.g. 'automotive', 'electronics', 'pharma')"}, "risk_threshold": {"type": "string", "description": "Minimum risk level to flag: 'low', 'medium', or 'high' (default: 'medium')", "default": "medium"}}},
  },
  {
    name: "text-summarizer",
    description: "Performs deep text analysis with parallel key-point extraction and structural analysis, producing a tailored executive summary",
    tags: ["text", "summarization", "formatting"],
    step_count: 4,
    category: "general_ai",
    input_schema: {"required": ["text"], "properties": {"text": {"type": "string", "description": "The text to summarize"}, "format": {"type": "string", "description": "Output format style - executive, bullet-points, narrative, or academic", "default": "executive"}, "max_length": {"type": "string", "description": "Target length for the final summary (e.g. '500 words', '1 page', '3 paragraphs')", "default": "500 words"}, "audience": {"type": "string", "description": "Target audience - determines vocabulary complexity and assumed background knowledge", "default": "general"}}},
  },
  // --- marketing ---
  {
    name: "ad-copy-generator",
    description: "Generate ad copy variants for Google Ads and Meta Ads campaigns",
    tags: ["Marketing", "Advertising", "Copywriting"],
    step_count: 5,
    category: "marketing",
    input_schema: {"required": ["product_brief"], "properties": {"product_brief": {"type": "string", "description": "Product brief describing features, target audience, and differentiators"}, "brand_voice": {"type": "string", "description": "Brand voice guidelines (e.g. professional, playful, authoritative). Optional - will be inferred from brief if not provided."}, "landing_page_url": {"type": "string", "description": "Landing page URL for display path generation and CTA alignment"}}},
  },
  {
    name: "agency-report-generator",
    description: "Generate comprehensive multi-client agency performance reports with cross-channel analytics, benchmarking, and strategic recommendations",
    tags: ["Marketing", "Analytics", "Reporting", "Agency", "Multi-Channel"],
    step_count: 6,
    category: "marketing",
    input_schema: {"required": ["agency_name", "client_list", "reporting_period", "channels"], "properties": {"agency_name": {"type": "string", "description": "Name of the agency generating the report (e.g. 'Apex Digital Media')"}, "client_list": {"type": "string", "description": "Comma-separated list of client names to include (e.g. 'Acme Corp, GlobalTech, FreshFoods Inc')"}, "reporting_period": {"type": "string", "description": "Reporting period for analysis (e.g. 'Q4 2025', 'January 2026', '2025 Full Year')"}, "channels": {"type": "string", "description": "Comma-separated marketing channels to analyze (e.g. 'paid_search, paid_social, seo, email, display')"}, "kpi_targets": {"type": "string", "description": "Key performance indicator targets and benchmarks in natural language (e.g. 'ROAS target 4.0, CPA under $35, CTR above 2.5%, email open rate 25%+')", "default": "Industry standard benchmarks"}}},
  },
  {
    name: "blog-to-social",
    description: "Transform a blog post into platform-specific social media content",
    tags: ["Marketing", "Content", "Social"],
    step_count: 5,
    category: "marketing",
    input_schema: {"required": ["blog_post"], "properties": {"blog_post": {"type": "string", "description": "The full blog post content to transform into social media posts"}, "brand_handle": {"type": "string", "description": "Your brand's social media handle (e.g. '@yourbrand') for consistent tagging"}, "industry": {"type": "string", "description": "Industry vertical for hashtag and audience context (e.g. 'SaaS', 'fintech', 'health tech')"}}},
  },
  {
    name: "competitive-intelligence-radar",
    description: "Monitor competitor activity, detect strategic changes, update battlecards, and distribute alerts",
    tags: ["Competitive-Intel", "Monitoring", "Strategy", "Battlecards"],
    step_count: 6,
    category: "marketing",
    input_schema: {"required": ["competitors", "product_name"], "properties": {"competitors": {"type": "string", "description": "Comma-separated list of competitor names to monitor (e.g. 'Acme Corp, Globex, Initech')"}, "product_name": {"type": "string", "description": "Your product name for battlecard context"}, "focus_areas": {"type": "string", "description": "Comma-separated focus areas for monitoring (default: pricing,features,positioning)", "default": "pricing,features,positioning"}}},
  },
  {
    name: "competitor-analysis",
    description: "Analyze competitor positioning, strengths, weaknesses, and opportunities",
    tags: ["Marketing", "Strategy", "Research"],
    step_count: 5,
    category: "marketing",
    input_schema: {"required": ["competitor"], "properties": {"competitor": {"type": "string", "description": "Name of the competitor to analyze"}, "our_company": {"type": "string", "description": "Your own company name and brief description for comparative positioning"}, "industry": {"type": "string", "description": "Industry or market vertical for context (e.g. 'B2B SaaS', 'e-commerce', 'fintech')"}}},
  },
  {
    name: "content-factory",
    description: "Generate a complete multi-platform content package from a single brief with SEO-optimized article and social posts",
    tags: ["Content", "SEO", "Social-Media", "Marketing"],
    step_count: 7,
    category: "marketing",
    input_schema: {"required": ["topic", "target_audience"], "properties": {"topic": {"type": "string", "description": "The content topic or theme to create content around"}, "brand_voice": {"type": "string", "description": "Brand voice and tone guidelines (e.g. professional, casual, authoritative, playful)", "default": "professional"}, "target_audience": {"type": "string", "description": "Description of the target audience (demographics, interests, pain points)"}, "platforms": {"type": "string", "description": "Comma-separated list of social platforms to create content for", "default": "linkedin,twitter,instagram"}}},
  },
  {
    name: "ecommerce-catalog",
    description: "Enrich e-commerce product catalogs with SEO-optimized descriptions, attribute extraction, cross-sell mapping, and A/B title variants",
    tags: ["E-Commerce", "SEO", "Catalog", "Product", "Marketing"],
    step_count: 6,
    category: "marketing",
    input_schema: {"required": ["catalog_source", "product_category", "brand_name"], "properties": {"catalog_source": {"type": "string", "description": "Raw product catalog data - can be CSV-formatted, JSON, or plain text with product names, SKUs, basic descriptions, and prices"}, "product_category": {"type": "string", "description": "Primary product category (e.g. 'outdoor furniture', 'organic skincare', 'running shoes', 'smart home devices')"}, "brand_name": {"type": "string", "description": "Brand name for consistent voice and messaging (e.g. 'TerraVerde', 'NovaPeak Athletics')"}, "target_marketplace": {"type": "string", "description": "Target e-commerce platform: 'shopify', 'amazon', 'woocommerce', 'bigcommerce', 'etsy', 'walmart'", "default": "shopify"}, "competitor_urls": {"type": "string", "description": "Comma-separated competitor product page URLs or brand names for competitive positioning analysis", "default": "No specific competitors provided"}}},
  },
  {
    name: "email-campaign-generator",
    description: "Generate email campaign with subject line variants and A/B copy",
    tags: ["Marketing", "Email", "Campaign"],
    step_count: 6,
    category: "marketing",
    input_schema: {"required": ["campaign_brief"], "properties": {"campaign_brief": {"type": "string", "description": "The campaign brief describing target audience, goals, and brand guidelines"}, "email_platform": {"type": "string", "description": "Email platform used (e.g. 'Mailchimp', 'HubSpot', 'Klaviyo', 'SendGrid') for platform-specific best practices"}, "list_size": {"type": "string", "description": "Approximate email list size for statistical significance calculations"}, "campaign_type": {"type": "string", "description": "Type: 'promotional', 'nurture', 'announcement', 'onboarding', 're-engagement', 'event'"}}},
  },
  {
    name: "market-entry-strategy",
    description: "Comprehensive market entry analysis covering sizing, competitive landscape, regulatory environment, customer research, and go-to-market strategy",
    tags: ["Strategy", "Market-Entry", "TAM", "Research"],
    step_count: 7,
    category: "marketing",
    input_schema: {"required": ["product_concept", "target_geography", "target_industry"], "properties": {"product_concept": {"type": "string", "description": "Description of the product or service entering the market"}, "target_geography": {"type": "string", "description": "Geographic market to enter (e.g. 'European Union', 'Japan', 'Southeast Asia')"}, "target_industry": {"type": "string", "description": "Industry vertical to target (e.g. 'Financial Services', 'Healthcare', 'Retail')"}}},
  },
  {
    name: "market-opportunity-scout",
    description: "Research market landscape, mine competitor gaps, size the TAM, and produce an actionable opportunity report",
    tags: ["Market-Research", "TAM", "Strategy", "Competitive-Intel"],
    step_count: 7,
    category: "marketing",
    input_schema: {"required": ["product_description", "target_industry"], "properties": {"product_description": {"type": "string", "description": "Description of the product or service to evaluate market opportunities for"}, "target_industry": {"type": "string", "description": "Industry vertical to analyze (e.g. 'HealthTech', 'FinTech', 'EdTech')"}, "geography": {"type": "string", "description": "Geographic scope for the analysis (default: Global)", "default": "Global"}}},
  },
  {
    name: "podcast-to-empire",
    description: "Transform a single podcast episode into a full content empire with blog, social, newsletter, and SEO landing page",
    tags: ["Content", "Podcast", "Repurpose", "Marketing"],
    step_count: 7,
    category: "marketing",
    input_schema: {"required": ["podcast_title", "episode_topic"], "properties": {"podcast_title": {"type": "string", "description": "Name of the podcast show"}, "episode_topic": {"type": "string", "description": "Topic or title of the specific episode to repurpose"}, "target_keywords": {"type": "string", "description": "Target SEO keywords for content optimization (comma-separated)"}, "brand_name": {"type": "string", "description": "Brand or company name for attribution and CTAs"}}},
  },
  {
    name: "pricing-intelligence",
    description: "Analyze competitor pricing, feature-value perception, and willingness to pay to optimize packaging and pricing strategy",
    tags: ["Pricing", "Competitive-Intel", "Strategy", "Revenue"],
    step_count: 6,
    category: "marketing",
    input_schema: {"required": ["product_name", "competitor_urls"], "properties": {"product_name": {"type": "string", "description": "Your product name for pricing comparison context"}, "competitor_urls": {"type": "string", "description": "Comma-separated list of competitor pricing page URLs or company names"}, "target_segments": {"type": "string", "description": "Target customer segments to analyze (e.g. 'SMB, Mid-Market, Enterprise')"}}},
  },
  {
    name: "seo-content-writer",
    description: "Research keywords and create SEO-optimized article with meta tags",
    tags: ["Marketing", "SEO", "Content"],
    step_count: 4,
    category: "marketing",
    input_schema: {"required": ["topic"], "properties": {"topic": {"type": "string", "description": "The topic to research keywords for and write an SEO-optimized article about"}, "target_audience": {"type": "string", "description": "Primary target audience for the content (e.g. 'SaaS founders', 'beginner developers')"}, "brand_url": {"type": "string", "description": "Your website URL for internal linking strategy and brand context"}}},
  },
  {
    name: "social-media-calendar",
    description: "Plan, create, and schedule a complete social media content calendar with platform-specific content, hashtag strategies, and performance predictions",
    tags: ["Social Media", "Content Calendar", "Marketing", "Hashtags", "Engagement"],
    step_count: 6,
    category: "marketing",
    input_schema: {"required": ["brand_name", "platforms", "content_pillars", "posting_frequency"], "properties": {"brand_name": {"type": "string", "description": "Name of the brand or business"}, "platforms": {"type": "string", "description": "Target platforms, comma-separated (e.g., Instagram, TikTok, LinkedIn, Twitter/X, Facebook, YouTube, Pinterest)"}, "content_pillars": {"type": "string", "description": "Core content themes/pillars the brand focuses on (e.g., education, behind-the-scenes, product showcases, user stories)"}, "posting_frequency": {"type": "string", "description": "Desired posting frequency per platform (e.g., 'daily on Instagram, 3x/week on LinkedIn')"}, "brand_voice": {"type": "string", "description": "Brand voice and tone description", "default": "Professional yet approachable, informative with personality"}, "time_period": {"type": "string", "description": "Calendar planning period", "default": "30 days"}}},
  },
  {
    name: "startup-growth-engine",
    description: "Comprehensive growth audit with parallel strategy tracks for content, SEO, and conversion optimization",
    tags: ["Growth", "Startup", "SEO", "Marketing", "Analytics"],
    step_count: 6,
    category: "marketing",
    input_schema: {"required": ["startup_name", "product_url"], "properties": {"startup_name": {"type": "string", "description": "Name of the startup"}, "product_url": {"type": "string", "description": "URL of the product or landing page to analyze"}, "growth_stage": {"type": "string", "description": "Current growth stage (early, growth, scale)", "default": "early"}, "monthly_budget": {"type": "number", "description": "Monthly marketing budget in USD", "default": 5000}}},
  },
  {
    name: "trend-radar",
    description: "Scan academic research, startup activity, social signals, and investment patterns to identify emerging trends and assess their strategic impact",
    tags: ["Trends", "Research", "Innovation", "Strategy"],
    step_count: 7,
    category: "marketing",
    input_schema: {"required": ["industry_keywords"], "properties": {"industry_keywords": {"type": "string", "description": "Comma-separated industry keywords to scan for trends (e.g. 'developer tools, API management, observability')"}, "technology_domains": {"type": "string", "description": "Specific technology domains to focus on (e.g. 'AI/ML, edge computing, serverless')"}, "timeframe": {"type": "string", "description": "Lookback period for trend signals (default: 6 months)", "default": "6 months"}}},
  },
  {
    name: "video-to-shorts",
    description: "Analyze long-form video content, identify viral-worthy segments, generate optimized short clips with captions and hooks for TikTok, Reels, and Shorts",
    tags: ["Video", "TikTok", "Reels", "Shorts", "Social-Media", "Marketing"],
    step_count: 6,
    category: "marketing",
    input_schema: {"required": ["video_url", "target_platforms"], "properties": {"video_url": {"type": "string", "description": "URL of the long-form video to analyze (YouTube, Vimeo, or direct link)"}, "target_platforms": {"type": "string", "description": "Comma-separated target platforms (e.g. 'TikTok, Instagram Reels, YouTube Shorts')"}, "brand_voice": {"type": "string", "description": "Brand voice and tone guidelines (e.g. 'professional but approachable', 'bold and provocative', 'educational and calm')", "default": "engaging and authentic"}, "content_goals": {"type": "string", "description": "Primary goals for the short-form content (e.g. 'drive newsletter signups', 'build thought leadership', 'increase brand awareness', 'drive product sales')", "default": "maximize engagement and reach"}}},
  },
  {
    name: "voice-of-market",
    description: "Mine forums, reviews, and social channels to extract sentiment, unmet needs, and buyer personas",
    tags: ["Market-Research", "Sentiment", "VoC", "Personas"],
    step_count: 7,
    category: "marketing",
    input_schema: {"required": ["product_category", "competitor_names"], "properties": {"product_category": {"type": "string", "description": "Product category to research (e.g. 'project management tools', 'email marketing platforms')"}, "competitor_names": {"type": "string", "description": "Comma-separated list of competitor names to mine feedback for"}, "keywords": {"type": "string", "description": "Additional search keywords to include in research (optional)"}}},
  },
  // --- sales_crm ---
  {
    name: "account-intelligence",
    description: "Profile target accounts, enrich with firmographic and technographic data in parallel, detect buying signals, and generate personalized outreach",
    tags: ["Sales", "ABM", "Intelligence", "CRM"],
    step_count: 6,
    category: "sales_crm",
    input_schema: {"required": ["target_accounts", "product_description"], "properties": {"target_accounts": {"type": "string", "description": "Comma-separated list of target account names to research (e.g. 'Acme Corp, Globex Inc, Initech')"}, "product_description": {"type": "string", "description": "Brief description of your product/service and its core value proposition"}, "icp_criteria": {"type": "string", "description": "Ideal Customer Profile criteria for scoring fit (e.g. 'B2B SaaS, 200-2000 employees, Series B+, using Kubernetes')"}}},
  },
  {
    name: "churn-prediction-pipeline",
    description: "Analyze customer usage patterns, score churn risk, identify root causes, generate retention offers, and orchestrate outreach campaigns",
    tags: ["Churn", "Customer-Success", "Retention", "Analytics"],
    step_count: 6,
    category: "sales_crm",
    input_schema: {"required": ["customer_segment"], "properties": {"customer_segment": {"type": "string", "description": "Customer segment to analyze (e.g. 'Enterprise', 'Mid-Market', 'SMB', 'Starter', or 'All')"}, "lookback_period": {"type": "string", "description": "How far back to analyze usage and behavioral data (default: '90 days')", "default": "90 days"}, "risk_threshold": {"type": "number", "description": "Minimum churn risk score (0-100) to trigger retention actions (default: 70)", "default": 70}}},
  },
  {
    name: "client-onboarding-orchestrator",
    description: "Orchestrate multi-step client onboarding with CRM updates, welcome sequences, provisioning checklists, and health score tracking",
    tags: ["Onboarding", "CRM", "Customer-Success", "Sales"],
    step_count: 6,
    category: "sales_crm",
    input_schema: {"required": ["client_name", "client_tier", "product_modules"], "properties": {"client_name": {"type": "string", "description": "Name of the client being onboarded (e.g. 'Acme Corp', 'TechStart GmbH')"}, "client_tier": {"type": "string", "description": "Client tier classification: 'enterprise', 'mid-market', or 'smb'"}, "product_modules": {"type": "string", "description": "Comma-separated list of product modules the client has purchased (e.g. 'analytics, automation, integrations, api-access')"}, "crm_source": {"type": "string", "description": "CRM system where client data originates (e.g. 'salesforce', 'hubspot', 'pipedrive', 'custom')", "default": "salesforce"}}},
  },
  {
    name: "crm-contact-enrichment",
    description: "Enrich HubSpot contacts with research data and create follow-up deals",
    tags: ["CRM", "HubSpot", "Sales", "Research"],
    step_count: 4,
    category: "sales_crm",
    input_schema: {"required": ["search_query"], "properties": {"search_query": {"type": "string", "description": "Search query for HubSpot contacts (name or email)"}, "enrichment_focus": {"type": "string", "description": "What to focus enrichment on (e.g. 'company size and revenue', 'technology stack')"}, "create_deals": {"type": "boolean", "description": "Whether to create deals for qualified contacts (default: false)"}, "deal_pipeline": {"type": "string", "description": "HubSpot pipeline for new deals (default: 'default')"}}},
  },
  {
    name: "customer-churn-predictor",
    description: "Analyze customer signals to predict churn risk, generate retention actions, and alert sales team",
    tags: ["Sales", "Salesforce", "Churn", "Analytics", "Retention"],
    step_count: 5,
    category: "sales_crm",
    input_schema: {"required": ["segment"], "properties": {"segment": {"type": "string", "description": "Customer segment to analyze - e.g. 'Enterprise', 'Mid-Market', 'SMB', or 'All'"}, "renewal_window_days": {"type": "number", "description": "Flag accounts renewing within this many days (default: 90)", "default": 90}, "slack_channel": {"type": "string", "description": "Slack channel for churn alerts (e.g. '#customer-success')", "default": "#customer-success"}, "cohort_window": {"type": "string", "description": "Cohort grouping for trend analysis - 'monthly', 'quarterly', or 'by_contract_start' (default: 'quarterly')", "default": "quarterly"}, "health_score_weights": {"type": "string", "description": "Custom weights for health score categories - e.g. 'usage:30,support:20,engagement:25,contract:25' (default: equal 25 each)"}}},
  },
  {
    name: "deal-velocity-optimizer",
    description: "Analyze pipeline health, score deal risks, recommend actions, and build competitive battle cards to accelerate deal closure",
    tags: ["Sales", "Pipeline", "CRM", "Forecasting"],
    step_count: 5,
    category: "sales_crm",
    input_schema: {"required": ["pipeline_stage"], "properties": {"pipeline_stage": {"type": "string", "description": "Pipeline stage to focus on (e.g. 'Negotiation', 'Proposal Sent', 'Discovery', or 'All')"}, "min_deal_size": {"type": "number", "description": "Minimum deal size in USD to include in analysis (default: 10000)", "default": 10000}, "crm_source": {"type": "string", "description": "CRM system to pull pipeline data from (default: 'salesforce')", "default": "salesforce"}}},
  },
  {
    name: "lead-enrichment",
    description: "Research and enrich lead data with company info, scoring, and outreach angles",
    tags: ["Sales", "Research", "Lead-Gen"],
    step_count: 5,
    category: "sales_crm",
    input_schema: {"required": ["company_name", "company_domain"], "properties": {"company_name": {"type": "string", "description": "Name of the company to research"}, "company_domain": {"type": "string", "description": "Company website domain"}, "target_persona": {"type": "string", "description": "Target persona or role to identify among contacts"}, "icp_criteria": {"type": "string", "description": "Ideal Customer Profile criteria for lead scoring"}, "value_proposition": {"type": "string", "description": "Your value proposition for outreach angle generation"}}},
  },
  {
    name: "lead-scoring",
    description: "Fetch leads from Salesforce, enrich with research data, score, and update CRM",
    tags: ["Sales", "Salesforce", "Lead-Gen", "Scoring"],
    step_count: 4,
    category: "sales_crm",
    input_schema: {"required": ["lead_source"], "properties": {"lead_source": {"type": "string", "description": "Salesforce lead source filter (e.g. 'Web', 'Event', 'Referral')"}, "min_company_size": {"type": "number", "description": "Minimum company size to qualify (number of employees, default: 10)", "default": 10}}},
  },
  {
    name: "meeting-recap",
    description: "Transform meeting transcript into summary, action items, and follow-up email",
    tags: ["Sales", "Productivity", "Communication"],
    step_count: 3,
    category: "sales_crm",
    input_schema: {"required": ["transcript", "meeting_title", "sender_name"], "properties": {"transcript": {"type": "string", "description": "The meeting transcript text"}, "meeting_title": {"type": "string", "description": "Title or subject of the meeting"}, "sender_name": {"type": "string", "description": "Name of the person sending the follow-up email"}, "meeting_type": {"type": "string", "description": "Type of meeting: standup, sprint-planning, client-call, brainstorm, 1-on-1, all-hands, retrospective"}}},
  },
  {
    name: "proposal-generator",
    description: "Generate a customized business proposal from meeting notes and product info",
    tags: ["Sales", "Document", "Proposal"],
    step_count: 5,
    category: "sales_crm",
    input_schema: {"required": ["meeting_notes", "client_name", "product_info", "pricing_tier"], "properties": {"meeting_notes": {"type": "string", "description": "Notes from the client meeting"}, "client_name": {"type": "string", "description": "Name of the client"}, "product_info": {"type": "string", "description": "Product catalog or feature descriptions"}, "pricing_tier": {"type": "string", "description": "Pricing tier to use in the proposal"}, "deal_size": {"type": "string", "description": "Estimated deal size or budget range discussed (e.g. '$50k-$80k ARR')"}, "contract_length": {"type": "string", "description": "Proposed contract length (e.g. '12 months', '24 months')"}}},
  },
  {
    name: "revenue-forecast-ensemble",
    description: "Generate revenue forecasts using statistical, ML, and LLM-based methods in parallel, then synthesize into an ensemble prediction with scenario analysis",
    tags: ["Forecasting", "Revenue", "Analytics", "Finance"],
    step_count: 6,
    category: "sales_crm",
    input_schema: {"required": ["revenue_data"], "properties": {"revenue_data": {"type": "string", "description": "Description of revenue data source and format (e.g. 'Monthly ARR by segment from Jan 2023 to present, exported from Stripe')"}, "forecast_horizon": {"type": "string", "description": "Time period to forecast (default: 'next quarter')", "default": "next quarter"}, "segments": {"type": "string", "description": "Revenue segments to analyze separately (e.g. 'Enterprise, Mid-Market, SMB, Self-Serve')"}}},
  },
  {
    name: "sales-pipeline-autopilot",
    description: "Monitor stalled deals, draft follow-ups, and alert your team on pipeline risks",
    tags: ["Sales", "Pipeline", "CRM", "Automation"],
    step_count: 4,
    category: "sales_crm",
    input_schema: {"required": ["pipeline_name"], "properties": {"pipeline_name": {"type": "string", "description": "HubSpot pipeline name to monitor (e.g. 'Sales Pipeline')"}, "stale_days": {"type": "number", "description": "Days without activity before a deal is considered stalled (default: 7)", "default": 7}, "alert_channel": {"type": "string", "description": "Slack channel for pipeline alerts (e.g. '#sales-alerts')", "default": "#sales-alerts"}}},
  },
  {
    name: "win-loss-intelligence",
    description: "Ingest CRM deal data, extract win/loss signals, identify patterns, and generate strategic recommendations with updated battlecards",
    tags: ["Sales", "Win-Loss", "CRM", "Strategy"],
    step_count: 6,
    category: "sales_crm",
    input_schema: {"required": ["crm_source"], "properties": {"crm_source": {"type": "string", "description": "CRM data source identifier (e.g. 'Salesforce', 'HubSpot') or pipeline name"}, "time_period": {"type": "string", "description": "Time period to analyze (default: last quarter)", "default": "last quarter"}, "deal_stages": {"type": "string", "description": "Comma-separated deal stages to include (default: Closed Won,Closed Lost)", "default": "Closed Won,Closed Lost"}}},
  },
  // --- support ---
  {
    name: "customer-health-check",
    description: "Aggregate Salesforce account data and Zendesk tickets to assess customer health",
    tags: ["Support", "Salesforce", "Zendesk", "Analytics"],
    step_count: 4,
    category: "support",
    input_schema: {"required": ["account_name"], "properties": {"account_name": {"type": "string", "description": "Customer account name to analyze"}}},
  },
  {
    name: "faq-generator",
    description: "Analyze resolved support tickets to auto-generate FAQ entries and publish to Notion",
    tags: ["Support", "Zendesk", "Notion", "Knowledge-Base"],
    step_count: 4,
    category: "support",
    input_schema: {"required": ["days_lookback"], "properties": {"days_lookback": {"type": "number", "description": "How many days of resolved tickets to analyze (default: 30)", "default": 30}, "min_cluster_size": {"type": "number", "description": "Minimum number of similar tickets to form a FAQ topic (default: 3)", "default": 3}, "notion_page_id": {"type": "string", "description": "Notion page ID where FAQ entries will be written"}}},
  },
  {
    name: "review-sentiment",
    description: "Analyze customer reviews to extract sentiment trends and actionable insights",
    tags: ["Support", "Analytics", "Sentiment"],
    step_count: 4,
    category: "support",
    input_schema: {"required": ["reviews", "product_name"], "properties": {"reviews": {"type": "string", "description": "Batch of customer reviews to analyze"}, "product_name": {"type": "string", "description": "Name of the product being reviewed"}}},
  },
  {
    name: "sla-watchdog",
    description: "Monitor SLA compliance, check ticket response times, and alert on breaches via Slack",
    tags: ["Support", "Zendesk", "SLA", "Monitoring"],
    step_count: 4,
    category: "support",
    input_schema: {"required": ["sla_policy"], "properties": {"sla_policy": {"type": "string", "description": "SLA policy to enforce - e.g. 'Critical: 1h first response, 4h resolution; High: 4h first response, 24h resolution'"}, "hours_lookback": {"type": "number", "description": "How many hours back to scan for tickets (default: 24)", "default": 24}, "slack_channel": {"type": "string", "description": "Slack channel for SLA breach alerts (e.g. '#sla-alerts')", "default": "#sla-alerts"}, "business_hours": {"type": "string", "description": "Business hours for SLA calculation - e.g. 'Mon-Fri 09:00-18:00 UTC' or '24/7' (default: '24/7')", "default": "24/7"}, "customer_tiers": {"type": "string", "description": "Optional tier-based SLA overrides - e.g. 'Platinum: 0.5x targets; Gold: 1x; Silver: 2x'"}, "escalation_chain": {"type": "string", "description": "Escalation contacts - e.g. 'L1: @support-lead; L2: @support-director; L3: @vp-cx'"}}},
  },
  {
    name: "support-ticket-triage",
    description: "Fetch recent Zendesk tickets, classify by urgency, draft responses, and notify Slack",
    tags: ["Support", "Zendesk", "Triage", "Automation"],
    step_count: 4,
    category: "support",
    input_schema: {"required": ["hours_lookback"], "properties": {"hours_lookback": {"type": "number", "description": "How many hours back to look for new tickets (default: 4)", "default": 4}, "slack_channel": {"type": "string", "description": "Slack channel for triage notifications (e.g. '#support-triage')", "default": "#support-triage"}}},
  },
  {
    name: "ticket-classifier",
    description: "Classify support ticket, assign priority, and draft response",
    tags: ["Support", "Classification", "Automation"],
    step_count: 4,
    category: "support",
    input_schema: {"required": ["subject", "body"], "properties": {"subject": {"type": "string", "description": "Support ticket subject line"}, "body": {"type": "string", "description": "Support ticket body text"}, "customer_tier": {"type": "string", "description": "Customer tier level (e.g. free, pro, enterprise)"}}},
  },
  {
    name: "voice-agent-pipeline",
    description: "Process call recordings with transcription, sentiment analysis, coaching insights, compliance checks, and agent performance scoring",
    tags: ["Support", "Call-Center", "QA", "Coaching", "Compliance"],
    step_count: 6,
    category: "support",
    input_schema: {"required": ["call_source", "team_name"], "properties": {"call_source": {"type": "string", "description": "Call recording source or batch identifier (e.g. 'five9-queue-support', 'genesys-inbound-jan', 'nice-batch-2024Q1', or a direct transcript paste)"}, "team_name": {"type": "string", "description": "Team or department name for reporting context (e.g. 'Tier 1 Support', 'Retention', 'Sales Development')"}, "evaluation_criteria": {"type": "string", "description": "Custom evaluation criteria or focus areas beyond defaults (e.g. 'upsell effectiveness, product knowledge depth, de-escalation handling')", "default": "standard QA evaluation"}, "compliance_requirements": {"type": "string", "description": "Regulatory and compliance frameworks to check against (e.g. 'PCI-DSS, TCPA, HIPAA, MiFID II, GDPR consent')", "default": "general best practices"}, "scoring_rubric": {"type": "string", "description": "Custom scoring weights or rubric description (e.g. '40% resolution, 30% compliance, 20% empathy, 10% efficiency')", "default": "balanced scorecard"}}},
  },
  // --- engineering ---
  {
    name: "ai-red-team",
    description: "Automated adversarial testing of AI models - probe for prompt injection, jailbreaks, bias, and safety vulnerabilities",
    tags: ["AI-Safety", "Security", "Testing", "Red-Team"],
    step_count: 6,
    category: "engineering",
    input_schema: {"required": ["target_model"], "properties": {"target_model": {"type": "string", "description": "Model to test (e.g. 'gpt-4o', 'claude-sonnet', 'llama-3.1-70b', 'gemini-2.0-flash')"}, "test_categories": {"type": "string", "description": "Comma-separated test categories to run (default: 'injection,jailbreak,bias,toxicity')", "default": "injection,jailbreak,bias,toxicity"}, "max_attempts": {"type": "number", "description": "Maximum number of adversarial attempts per test category (default: 50)", "default": 50}}},
  },
  {
    name: "api-docs-generator",
    description: "Generate comprehensive API documentation from code repositories or OpenAPI specs",
    tags: ["Engineering", "GitHub", "Documentation", "API"],
    step_count: 4,
    category: "engineering",
    input_schema: {"required": ["repo"], "properties": {"repo": {"type": "string", "description": "GitHub repository in 'owner/repo' format (e.g. 'acme/payments-api')"}, "branch": {"type": "string", "description": "Branch to generate docs from (default: 'main')", "default": "main"}, "path_filter": {"type": "string", "description": "Path filter for API source files (e.g. 'src/api/', 'routes/')"}, "doc_style": {"type": "string", "description": "Documentation style: 'reference', 'tutorial', or 'both' (default: 'reference')", "default": "reference"}}},
  },
  {
    name: "data-extractor",
    description: "Extract structured data from documents with validation and error handling",
    tags: ["Product", "Data", "Automation"],
    step_count: 4,
    category: "engineering",
    input_schema: {"required": ["document_text"], "properties": {"document_text": {"type": "string", "description": "The document text to extract structured data from"}}},
  },
  {
    name: "deployment-risk-analyzer",
    description: "Analyze deployment risk by scanning diffs, dependencies, and performance impact before go/no-go decision",
    tags: ["DevOps", "CI-CD", "Security", "Risk"],
    step_count: 5,
    category: "engineering",
    input_schema: {"required": ["repo_url", "deploy_target"], "properties": {"repo_url": {"type": "string", "description": "GitHub repository URL (e.g. 'https://github.com/org/repo')"}, "branch": {"type": "string", "description": "Branch to analyze for deployment (default: main)", "default": "main"}, "deploy_target": {"type": "string", "description": "Deployment target environment (e.g. 'production', 'staging', 'canary')"}}},
  },
  {
    name: "incident-command-center",
    description: "Automated incident response - ingest alerts, analyze logs and metrics in parallel, find root cause, and generate remediation runbooks",
    tags: ["DevOps", "SRE", "Incident-Response", "Monitoring"],
    step_count: 6,
    category: "engineering",
    input_schema: {"required": ["alert_source", "service_name"], "properties": {"alert_source": {"type": "string", "description": "Alert source integration (e.g. 'pagerduty', 'datadog', 'opsgenie')"}, "service_name": {"type": "string", "description": "Name of the affected service (e.g. 'payments-api', 'auth-service')"}, "severity": {"type": "string", "description": "Incident severity level (default: P2)", "default": "P2"}}},
  },
  {
    name: "jira-issue-triage",
    description: "Auto-triage new Jira issues with priority, labels, and assignment suggestions",
    tags: ["Project Management", "Jira", "Triage"],
    step_count: 3,
    category: "engineering",
    input_schema: {"required": ["project"], "properties": {"project": {"type": "string", "description": "Jira project key (e.g. PROJ)"}, "jql_filter": {"type": "string", "description": "Additional JQL filter (default: untriaged issues from last 24h)"}, "team_context": {"type": "string", "description": "Context about team members and their areas of expertise"}}},
  },
  {
    name: "model-evaluation-arena",
    description: "Systematically benchmark and compare multiple AI models across safety, accuracy, cost, and latency with statistical rigor",
    tags: ["AI-Safety", "Evaluation", "Testing", "Benchmarking"],
    step_count: 6,
    category: "engineering",
    input_schema: {"required": ["models_to_test"], "properties": {"models_to_test": {"type": "string", "description": "Comma-separated model names to evaluate (e.g. 'sonnet, haiku, opus, openai/gpt-4o, google/gemini-2.5-pro')"}, "test_category": {"type": "string", "description": "Focus area: 'general', 'coding', 'reasoning', 'creative', 'safety', or 'domain-specific' (default: 'general')", "default": "general"}, "num_prompts": {"type": "number", "description": "Number of test prompts to generate per category (default: 20)", "default": 20}, "evaluation_criteria": {"type": "string", "description": "Comma-separated evaluation dimensions (default: 'accuracy,safety,cost,latency')", "default": "accuracy,safety,cost,latency"}}},
  },
  {
    name: "nl-to-dashboard",
    description: "Transform natural language business questions into optimized SQL queries, visualizations, and narrative insights",
    tags: ["Analytics", "SQL", "Visualization", "Data"],
    step_count: 6,
    category: "engineering",
    input_schema: {"required": ["business_question", "database_schema"], "properties": {"business_question": {"type": "string", "description": "The business question to answer in plain English (e.g. 'Which products have the highest return rate by region in the last 6 months?')"}, "database_schema": {"type": "string", "description": "Description of available tables and columns (e.g. 'orders(id, customer_id, product_id, amount, status, region, created_at), products(id, name, category, price), returns(id, order_id, reason, created_at)')"}, "visualization_type": {"type": "string", "description": "Preferred chart type: 'auto', 'bar', 'line', 'pie', 'table', 'heatmap' (default: 'auto')", "default": "auto"}}},
  },
  {
    name: "product-design-specification",
    description: "Transform user stories and requirements into comprehensive product design specifications with wireframe descriptions, interaction patterns, and developer handoff documentation",
    tags: ["Design", "UX", "Product", "Wireframes", "Accessibility"],
    step_count: 6,
    category: "engineering",
    input_schema: {"required": ["product_name", "user_stories"], "properties": {"product_name": {"type": "string", "description": "Name of the product or feature being designed (e.g. 'Acme Dashboard', 'Checkout Flow Redesign')"}, "user_stories": {"type": "string", "description": "User stories, requirements, or feature descriptions to translate into design specs (paste all stories/requirements)"}, "design_system": {"type": "string", "description": "Design system to use or reference (e.g. 'Material Design 3', 'Apple HIG', 'Ant Design', 'custom')", "default": "custom"}, "target_platforms": {"type": "string", "description": "Comma-separated target platforms (e.g. 'web, iOS, Android', 'web-only', 'responsive web, native iOS')", "default": "responsive web"}, "accessibility_level": {"type": "string", "description": "Accessibility conformance target (e.g. 'WCAG 2.1 AA', 'WCAG 2.2 AAA', 'Section 508')", "default": "WCAG 2.1 AA"}}},
  },
  {
    name: "rag-knowledge-base",
    description: "Build and optimize a RAG knowledge base from documents - chunk, embed, evaluate retrieval quality, and generate optimization recommendations",
    tags: ["RAG", "Embeddings", "Knowledge-Base", "Retrieval", "NLP"],
    step_count: 6,
    category: "engineering",
    input_schema: {"required": ["document_sources", "domain"], "properties": {"document_sources": {"type": "string", "description": "Comma-separated document paths or URLs to ingest into the knowledge base (e.g. 'docs/api-reference.md, https://example.com/faq, docs/guides/')"}, "domain": {"type": "string", "description": "Knowledge domain for tuning chunking and retrieval heuristics (e.g. 'legal contracts', 'medical research', 'software documentation', 'financial reports')"}, "chunk_strategy": {"type": "string", "description": "Chunking strategy to use: 'semantic', 'fixed', 'recursive', 'document', or 'hybrid' (default: 'semantic')", "default": "semantic"}, "embedding_model": {"type": "string", "description": "Embedding model for vectorization (default: 'text-embedding-3-small'). Options include text-embedding-3-small, text-embedding-3-large, voyage-3, cohere-embed-v3", "default": "text-embedding-3-small"}}},
  },
  {
    name: "release-notes-generator",
    description: "Generate user-facing release notes and internal changelog from commit history",
    tags: ["Product", "Engineering", "Documentation"],
    step_count: 4,
    category: "engineering",
    input_schema: {"required": ["changelog"], "properties": {"changelog": {"type": "string", "description": "Git diff or changelog text to generate release notes from"}}},
  },
  {
    name: "self-healing-pipeline",
    description: "Monitor data pipelines for schema drift and anomalies, auto-fix common issues, and report on data quality recovery",
    tags: ["Data", "ETL", "DevOps", "Automation"],
    step_count: 6,
    category: "engineering",
    input_schema: {"required": ["pipeline_name", "data_source"], "properties": {"pipeline_name": {"type": "string", "description": "Name of the data pipeline to monitor (e.g. 'orders-etl', 'user-events-pipeline')"}, "data_source": {"type": "string", "description": "Source system description (e.g. 'PostgreSQL orders table -> Snowflake warehouse')"}, "quality_threshold": {"type": "number", "description": "Minimum data quality score (0-100) before alerting (default: 95)", "default": 95}, "auto_fix": {"type": "string", "description": "Enable automatic fix attempts for known issue patterns: 'true' or 'false' (default: 'true')", "default": "true"}}},
  },
  {
    name: "slack-standup-summary",
    description: "Collect daily standup updates from Slack and post a summary",
    tags: ["Communication", "Slack", "Team"],
    step_count: 3,
    category: "engineering",
    input_schema: {"required": ["standup_channel"], "properties": {"standup_channel": {"type": "string", "description": "Slack channel to read standups from (e.g. #daily-standup)"}, "summary_channel": {"type": "string", "description": "Slack channel to post summary to (defaults to standup_channel)"}, "lookback_hours": {"type": "number", "description": "Hours to look back for messages (default: 24)"}, "sprint_goal": {"type": "string", "description": "Current sprint goal for alignment tracking (e.g. 'Ship v2.0 checkout flow')"}, "team_members": {"type": "string", "description": "Comma-separated list of expected team members for missing-update detection"}}},
  },
  {
    name: "soc-triage-pipeline",
    description: "Security Operations Center alert triage - deduplicate, enrich with threat intel, map to MITRE ATT&CK, and recommend containment",
    tags: ["Security", "SOC", "Threat-Intel", "SIEM"],
    step_count: 7,
    category: "engineering",
    input_schema: {"required": ["siem_source", "alert_id"], "properties": {"siem_source": {"type": "string", "description": "SIEM platform source (e.g. 'splunk', 'sentinel', 'elastic', 'crowdstrike')"}, "alert_id": {"type": "string", "description": "Alert ID from the SIEM to triage (e.g. 'ALERT-2024-00847')"}, "auto_contain": {"type": "string", "description": "Whether to auto-execute containment actions (default: 'false')", "default": "false"}}},
  },
  {
    name: "sprint-standup",
    description: "Synthesize Jira sprint progress and GitHub PRs into a daily standup summary for Slack",
    tags: ["Engineering", "Jira", "GitHub", "Standup"],
    step_count: 4,
    category: "engineering",
    input_schema: {"required": ["jira_project", "github_repo"], "properties": {"jira_project": {"type": "string", "description": "Jira project key (e.g. 'PROJ')"}, "github_repo": {"type": "string", "description": "GitHub repository (e.g. 'org/repo')"}, "slack_channel": {"type": "string", "description": "Slack channel for the standup post (e.g. '#engineering')", "default": "#engineering"}}},
  },
  // --- hr_legal ---
  {
    name: "compliance-checker",
    description: "Review documents for regulatory compliance against GDPR, SOC2, HIPAA, or custom frameworks",
    tags: ["Legal", "Compliance", "GDPR", "SOC2", "Audit"],
    step_count: 4,
    category: "hr_legal",
    input_schema: {"required": ["document_text", "framework"], "properties": {"document_text": {"type": "string", "description": "The document text to review for compliance (policy, contract, or process description)"}, "framework": {"type": "string", "description": "Compliance framework to check against: 'GDPR', 'SOC2', 'HIPAA', 'PCI-DSS', or custom requirements text"}, "severity_threshold": {"type": "string", "description": "Minimum severity to report: 'low', 'medium', or 'high' (default: 'low')", "default": "low"}}},
  },
  {
    name: "contract-lifecycle",
    description: "End-to-end contract lifecycle from drafting through approval to obligation tracking",
    tags: ["Legal", "Contracts", "Negotiation", "CLM"],
    step_count: 5,
    category: "hr_legal",
    input_schema: {"required": ["contract_type", "counterparty"], "properties": {"contract_type": {"type": "string", "description": "Type of contract to draft (e.g. SaaS, NDA, MSA, SOW, License, Employment)"}, "counterparty": {"type": "string", "description": "Name of the counterparty or organization"}, "key_terms": {"type": "string", "description": "Key terms and requirements to include (comma-separated or free text)"}}},
  },
  {
    name: "contract-review",
    description: "Review contract for key terms, risks, and generate plain-language summary",
    tags: ["Legal", "Compliance", "Document"],
    step_count: 4,
    category: "hr_legal",
    input_schema: {"required": ["contract_text"], "properties": {"contract_text": {"type": "string", "description": "The contract document text to review"}}},
  },
  {
    name: "employee-onboarding",
    description: "Automate new employee onboarding - checklist, welcome email, accounts, training plan, and manager notification",
    tags: ["HR", "Onboarding", "Automation", "Slack", "Notion"],
    step_count: 5,
    category: "hr_legal",
    input_schema: {"required": ["employee_name", "role", "department", "start_date", "manager_email"], "properties": {"employee_name": {"type": "string", "description": "Full name of the new employee"}, "role": {"type": "string", "description": "Job title/role (e.g. 'Senior Frontend Engineer')"}, "department": {"type": "string", "description": "Department (e.g. 'Engineering', 'Marketing', 'Sales')"}, "start_date": {"type": "string", "description": "Start date in YYYY-MM-DD format"}, "manager_email": {"type": "string", "description": "Direct manager's email address"}, "employee_email": {"type": "string", "description": "New employee's email address (if already provisioned)"}, "location": {"type": "string", "description": "Office location or 'Remote' (default: 'Remote')", "default": "Remote"}, "seniority_level": {"type": "string", "description": "Seniority level: 'junior', 'mid', 'senior', 'lead', 'director', 'vp' (default: 'mid')", "default": "mid"}, "buddy_email": {"type": "string", "description": "Assigned onboarding buddy/mentor email (optional - will suggest if not provided)"}}},
  },
  {
    name: "job-description-generator",
    description: "Generate inclusive job description with requirements, benefits, and interview plan",
    tags: ["HR", "Recruiting", "Content"],
    step_count: 4,
    category: "hr_legal",
    input_schema: {"required": ["role_brief"], "properties": {"role_brief": {"type": "string", "description": "Brief describing the role, responsibilities, and requirements"}, "company_name": {"type": "string", "description": "Company name for the About Us section"}, "salary_range": {"type": "string", "description": "Salary range to include (e.g. '$120k-$160k'). Recommended for transparency and SEO."}, "location_type": {"type": "string", "description": "Work arrangement: remote, hybrid, on-site, or flexible"}}},
  },
  {
    name: "ma-due-diligence",
    description: "Comprehensive due diligence analysis for mergers and acquisitions with parallel risk and terms extraction",
    tags: ["Legal", "M&A", "Due-Diligence", "Finance"],
    step_count: 6,
    category: "hr_legal",
    input_schema: {"required": ["target_company"], "properties": {"target_company": {"type": "string", "description": "Name of the target company being evaluated for the transaction"}, "deal_type": {"type": "string", "description": "Type of transaction (e.g. acquisition, merger, asset purchase, joint venture)", "default": "acquisition"}, "focus_areas": {"type": "string", "description": "Comma-separated focus areas for the due diligence review", "default": "financials,contracts,ip,compliance"}}},
  },
  {
    name: "org-health-pulse",
    description: "Analyze employee survey data to surface sentiment trends, flight risks, and targeted intervention recommendations",
    tags: ["HR", "Analytics", "Culture", "Engagement"],
    step_count: 7,
    category: "hr_legal",
    input_schema: {"required": ["survey_data"], "properties": {"survey_data": {"type": "string", "description": "Description of survey data source or raw survey response data (e.g. 'Q4 2025 engagement survey - 450 responses across Engineering, Product, Sales, Marketing')"}, "department_filter": {"type": "string", "description": "Filter analysis to a specific department, or 'all' for company-wide (default: 'all')", "default": "all"}, "comparison_period": {"type": "string", "description": "Prior period to compare against for trend analysis (default: 'previous quarter')", "default": "previous quarter"}}},
  },
  {
    name: "recruiting-pipeline",
    description: "End-to-end recruiting workflow from job description to offer preparation with candidate evaluation and interview coordination",
    tags: ["HR", "Recruiting", "Hiring", "Talent"],
    step_count: 7,
    category: "hr_legal",
    input_schema: {"required": ["role_title", "department", "requirements"], "properties": {"role_title": {"type": "string", "description": "Job title for the open position (e.g. 'Senior Backend Engineer')"}, "department": {"type": "string", "description": "Hiring department (e.g. 'Engineering', 'Product', 'Marketing')"}, "requirements": {"type": "string", "description": "Key requirements, skills, and experience needed for the role"}, "salary_range": {"type": "string", "description": "Compensation range for the role (e.g. '$150K-$180K + equity')"}, "location": {"type": "string", "description": "Work location or remote policy (default: 'Remote')", "default": "Remote"}}},
  },
  {
    name: "regulatory-change-analyzer",
    description: "Monitor regulatory changes, assess compliance gaps, and generate remediation plans",
    tags: ["Compliance", "Regulatory", "Legal", "Risk"],
    step_count: 5,
    category: "hr_legal",
    input_schema: {"required": ["jurisdiction", "industry"], "properties": {"jurisdiction": {"type": "string", "description": "Primary jurisdiction to monitor (e.g. US, EU, UK, APAC)"}, "industry": {"type": "string", "description": "Industry sector (e.g. financial-services, healthcare, technology, manufacturing)"}, "regulatory_bodies": {"type": "string", "description": "Comma-separated list of regulatory bodies to monitor", "default": "SEC,FINRA,GDPR"}}},
  },
  {
    name: "resume-screener",
    description: "Screen resume against job description with match scoring and interview recommendations",
    tags: ["HR", "Recruiting", "Screening"],
    step_count: 4,
    category: "hr_legal",
    input_schema: {"required": ["resume_text", "job_description"], "properties": {"resume_text": {"type": "string", "description": "The candidate resume text to screen"}, "job_description": {"type": "string", "description": "The job description to match the resume against"}, "hiring_priority": {"type": "string", "description": "What matters most for this hire: 'technical-depth', 'leadership', 'culture-add', 'speed-to-productivity', or 'growth-potential'"}}},
  },
  // --- data (new) ---
  {
    name: "supabase-data-sync",
    description: "Sync data from Google Sheets to Supabase, validate records, and notify on Slack.",
    tags: ["Supabase", "Google Sheets", "Data Sync", "Slack"],
    step_count: 4,
    category: "data",
    input_schema: {"type": "object", "required": ["spreadsheet_id", "sheet_name", "table_name"], "properties": {"spreadsheet_id": {"type": "string", "description": "Google Sheets spreadsheet ID"}, "sheet_name": {"type": "string", "description": "Sheet/tab name to read from"}, "table_name": {"type": "string", "description": "Supabase table name to sync into"}}},
  },
  // --- devops (new) ---
  {
    name: "deployment-monitor",
    description: "Monitor Vercel deployments, check Datadog metrics, and alert via PagerDuty if anomalies detected.",
    tags: ["Vercel", "Datadog", "PagerDuty", "Monitoring", "DevOps"],
    step_count: 3,
    category: "devops",
    input_schema: {"type": "object", "required": ["project_name"], "properties": {"project_name": {"type": "string", "description": "Vercel project name to monitor"}, "metric_query": {"type": "string", "description": "Datadog metric query to check", "default": "avg:system.cpu.user{*}"}, "threshold": {"type": "number", "description": "Alert threshold for the metric", "default": 80}}},
  },
  {
    name: "discord-incident-bot",
    description: "Monitor PagerDuty for new incidents, post alerts to Discord, and auto-acknowledge with status updates.",
    tags: ["Discord", "PagerDuty", "Incident Response", "DevOps"],
    step_count: 3,
    category: "devops",
    input_schema: {"type": "object", "required": ["discord_channel_id"], "properties": {"discord_channel_id": {"type": "string", "description": "Discord channel ID for incident alerts"}, "severity": {"type": "string", "description": "Minimum severity to alert on (high or low)", "default": "high"}}},
  },
  // --- general_ai (new) ---
  {
    name: "ai-brand-sentinel",
    description: "Monitor how AI platforms represent your brand in generated answers, track sentiment shifts, identify source URLs influencing AI opinions, and generate corrective content recommendations",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["brand_name", "competitors"], "properties": {"brand_name": {"type": "string", "description": "Your brand or product name to monitor across AI platforms"}, "competitors": {"type": "string", "description": "Comma-separated list of competitor brand names to compare against"}, "ai_platforms": {"type": "string", "description": "Comma-separated list of AI platforms to probe (default: ChatGPT, Perplexity, Claude, Google AI)", "default": "ChatGPT, Perplexity, Claude, Google AI"}, "monitoring_queries": {"type": "string", "description": "Comma-separated seed queries to test across AI platforms (e.g. 'best CRM software, CRM comparison 2026')"}}},
  },
  {
    name: "ai-rag-pipeline",
    description: "Retrieve-augment-generate pipeline using Tavily search, Pinecone vector store, and OpenAI for answer synthesis.",
    tags: ["AI", "RAG", "Search", "Vectors"],
    step_count: 3,
    category: "general_ai",
    input_schema: {"type": "object", "required": ["question"], "properties": {"question": {"type": "string", "description": "The question to answer using RAG"}}},
  },
  {
    name: "board-meeting-prep",
    description: "Aggregate financial metrics, KPIs, competitive developments, and team updates into comprehensive board meeting packages with executive narratives and discussion prompts",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["company_name", "reporting_period"], "properties": {"company_name": {"type": "string", "description": "Name of the company preparing the board meeting"}, "reporting_period": {"type": "string", "description": "Reporting period for the board meeting (e.g., Q1 2026, FY 2025)"}, "board_members": {"type": "string", "description": "Comma-separated list of board member names and roles"}, "key_metrics_sources": {"type": "string", "description": "Comma-separated list of data sources for key metrics (e.g., Stripe, QuickBooks, Mixpanel)"}, "strategic_priorities": {"type": "string", "description": "Comma-separated list of current strategic priorities being tracked by the board"}}},
  },
  {
    name: "carbon-footprint-reporter",
    description: "Collect emissions data from energy bills, travel, supply chain, and fleet, calculate Scope 1/2/3 carbon footprints, generate regulatory-compliant ESG reports, and identify reduction opportunities",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["organization_name", "reporting_year", "industry_sector"], "properties": {"organization_name": {"type": "string", "description": "Name of the organization for carbon footprint reporting"}, "reporting_year": {"type": "string", "description": "Calendar or fiscal year for emissions reporting (e.g. '2025', 'FY2025')"}, "industry_sector": {"type": "string", "description": "Industry sector for benchmarking and materiality assessment (e.g. 'technology', 'manufacturing', 'retail', 'financial_services', 'healthcare')"}, "data_sources": {"type": "string", "description": "Comma-separated list of emissions data sources available (e.g. 'energy, travel, fleet, supply_chain, waste, water, refrigerants')", "default": "energy, travel, fleet, supply_chain"}, "reporting_framework": {"type": "string", "description": "Primary reporting framework: 'GHG Protocol', 'ISO 14064', 'CDP', 'CSRD', 'SEC Climate'", "default": "GHG Protocol"}}},
  },
  {
    name: "chain-of-thought",
    description: "Advanced problem solver using structured decomposition, parallel research and reasoning tracks, synthesis, and solution validation",
    tags: [],
    step_count: 5,
    category: "general_ai",
    input_schema: {"required": ["problem"], "properties": {"problem": {"type": "string", "description": "The problem to decompose and solve"}, "constraints": {"type": "string", "description": "Known constraints, requirements, or boundary conditions that the solution must satisfy", "default": ""}, "depth": {"type": "string", "description": "Analysis depth - quick (surface-level), standard (balanced), or deep (exhaustive)", "default": "standard"}}},
  },
  {
    name: "churn-predictor-v2",
    description: "Enhanced churn prediction with multi-signal analysis, Slack alerts, and automated retention campaigns via HubSpot sequences",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["hubspot_list_id"], "properties": {"hubspot_list_id": {"type": "string", "description": "HubSpot contact list ID or segment name containing customers to analyze (e.g. 'active-customers', 'enterprise-tier')"}, "lookback_days": {"type": "number", "description": "Number of days of historical activity data to analyze (default: 90)", "default": 90}, "risk_threshold": {"type": "number", "description": "Minimum churn risk score (0-100) to trigger alerts and campaigns (default: 60)", "default": 60}, "slack_channel": {"type": "string", "description": "Slack channel for churn risk alerts (e.g. '#cs-alerts')", "default": "#cs-alerts"}, "hubspot_sequence_id": {"type": "string", "description": "HubSpot sequence ID for automated retention outreach (leave blank to skip enrollment)"}}},
  },
  {
    name: "codebase-health-scanner",
    description: "Analyze codebase for technical debt signals - complexity hotspots, test coverage gaps, dependency vulnerabilities, dead code, API contract drift, and documentation staleness with prioritized remediation roadmap",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["repo_url", "language"], "properties": {"repo_url": {"type": "string", "description": "Repository URL or local path to the codebase to analyze"}, "language": {"type": "string", "description": "Primary programming language (e.g. 'python', 'typescript', 'java', 'go', 'rust')"}, "focus_areas": {"type": "string", "description": "Comma-separated areas to focus on (default: all). Options: complexity, dependencies, coverage, dead_code, api_drift, docs", "default": "all"}, "tech_debt_budget": {"type": "string", "description": "Hours available per sprint for tech debt reduction (used for roadmap planning)", "default": "20"}}},
  },
  {
    name: "competitive-teardown",
    description: "Comprehensive competitor product teardown - feature-by-feature analysis, UX evaluation, pricing deconstruction, positioning critique, and strategic vulnerability assessment",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["competitor_product", "your_product"], "properties": {"competitor_product": {"type": "string", "description": "The competitor product to tear down - URL to product page or product name (e.g. 'https://competitor.com' or 'Notion')"}, "your_product": {"type": "string", "description": "Your product name and brief description for comparison context (e.g. 'Acme Notes - collaborative documentation tool for engineering teams')"}, "analysis_depth": {"type": "string", "description": "Depth of analysis: quick (key findings only), standard (full analysis), comprehensive (exhaustive teardown with scoring)", "default": "comprehensive"}, "focus_areas": {"type": "string", "description": "Comma-separated areas to focus on: features, ux, pricing, positioning, technical, marketing, support", "default": "features, ux, pricing, positioning"}}},
  },
  {
    name: "compliance-audit-readiness",
    description: "Monitor systems against SOC 2, HIPAA, GDPR, ISO 27001 requirements, auto-generate evidence artifacts, flag control gaps, simulate auditor questions, and produce audit-ready documentation",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["frameworks", "organization_name"], "properties": {"frameworks": {"type": "string", "description": "Comma-separated list of compliance frameworks to audit against (e.g. 'SOC 2, HIPAA, GDPR, ISO 27001, PCI DSS')"}, "organization_name": {"type": "string", "description": "Name of the organization being assessed for compliance readiness"}, "system_scope": {"type": "string", "description": "Comma-separated list of systems, applications, and infrastructure in scope for the audit (e.g. 'AWS production, customer portal, HR database, email system')"}, "audit_date": {"type": "string", "description": "Target audit date or timeframe for readiness assessment", "default": "next quarter"}}},
  },
  {
    name: "construction-bid-analyzer",
    description: "Ingest construction bid documents, extract scope items and quantities, cross-reference cost databases, flag missing items, and produce structured cost estimates with risk-adjusted ranges",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["project_type", "bid_documents", "location"], "properties": {"project_type": {"type": "string", "description": "Type of construction project: 'commercial', 'residential', 'industrial', or 'infrastructure'"}, "bid_documents": {"type": "string", "description": "Description of bid documents to analyze - paste the full bid package text, scope of work narrative, specifications, and any included cost breakdowns"}, "location": {"type": "string", "description": "Project location (city, state/province, country) - used for regional cost adjustments and labor rate calibration"}, "project_size": {"type": "string", "description": "Square footage, acreage, or scope description (e.g. '45,000 SF office building', '2-mile road extension', '150-unit apartment complex')"}, "budget_target": {"type": "string", "description": "Owner's budget target or not-to-exceed figure, if known (e.g. '$12M', '$85/SF')"}}},
  },
  {
    name: "content-calendar",
    description: "Research trending topics in your niche, generate a month-long content calendar with SEO keywords, and draft outlines for each piece",
    tags: [],
    step_count: 5,
    category: "general_ai",
    input_schema: {"required": ["niche", "target_audience"], "properties": {"niche": {"type": "string", "description": "Your content niche or industry (e.g. 'B2B SaaS marketing', 'personal finance', 'sustainable fashion')"}, "target_audience": {"type": "string", "description": "Primary target audience for the content (e.g. 'startup founders', 'mid-career professionals', 'ecommerce store owners')"}, "content_types": {"type": "string", "description": "Comma-separated content formats to include (default: 'blog post, social post, newsletter, video script')", "default": "blog post, social post, newsletter, video script"}, "posts_per_week": {"type": "number", "description": "Number of content pieces to plan per week (default: 4)", "default": 4}, "brand_voice": {"type": "string", "description": "Brand voice and tone guidelines (e.g. 'professional but approachable', 'bold and opinionated')", "default": "professional, helpful, and data-driven"}}},
  },
  {
    name: "contract-reviewer-pro",
    description: "Advanced contract review with clause-by-clause risk analysis, jurisdiction-specific compliance checks, and amendment draft generation",
    tags: [],
    step_count: 5,
    category: "general_ai",
    input_schema: {"required": ["contract_text"], "properties": {"contract_text": {"type": "string", "description": "Full text of the contract to review (paste the contract content or key sections)"}, "contract_type": {"type": "string", "description": "Type of contract (e.g. 'SaaS subscription', 'employment', 'NDA', 'MSA', 'SOW', 'vendor agreement')", "default": "general commercial"}, "jurisdiction": {"type": "string", "description": "Governing law jurisdiction (e.g. 'Delaware, US', 'England & Wales', 'California, US', 'EU/GDPR')", "default": "United States (general)"}, "review_perspective": {"type": "string", "description": "Whose interests to prioritize - 'buyer', 'seller', 'employee', or 'neutral'", "default": "buyer"}, "risk_tolerance": {"type": "string", "description": "Risk tolerance level - 'conservative' (flag everything), 'moderate' (standard business), or 'aggressive' (maximum flexibility)", "default": "moderate"}}},
  },
  {
    name: "crisis-communication-commander",
    description: "Real-time PR crisis management - monitor channels, draft holding statements, generate Q&A documents, coordinate multi-channel response, and track sentiment recovery",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["crisis_description", "company_name"], "properties": {"crisis_description": {"type": "string", "description": "Detailed description of the crisis situation, including known facts, timeline, and scope"}, "company_name": {"type": "string", "description": "Company name facing the crisis"}, "affected_channels": {"type": "string", "description": "Comma-separated list of affected channels (e.g. 'twitter, linkedin, press, reddit, internal')"}, "severity_level": {"type": "string", "description": "Crisis severity: critical, high, medium, low (default: high)", "default": "high"}, "spokesperson_name": {"type": "string", "description": "Primary spokesperson name for media responses"}}},
  },
  {
    name: "crm-enrichment",
    description: "Enrich HubSpot contacts with research data and create follow-up deals",
    tags: [],
    step_count: 4,
    category: "general_ai",
    input_schema: {"required": ["search_query"], "properties": {"search_query": {"type": "string", "description": "Search query for HubSpot contacts (name or email)"}, "enrichment_focus": {"type": "string", "description": "What to focus enrichment on (e.g. 'company size and revenue', 'technology stack')"}, "create_deals": {"type": "boolean", "description": "Whether to create deals for qualified contacts (default: false)"}, "deal_pipeline": {"type": "string", "description": "HubSpot pipeline for new deals (default: 'default')"}}},
  },
  {
    name: "data-privacy-scanner",
    description: "Scan codebases and data flows to discover PII/PHI, classify sensitivity levels, map data lineage, detect compliance violations, and generate Data Protection Impact Assessments",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["scan_scope", "organization_name"], "properties": {"scan_scope": {"type": "string", "description": "Scope of the scan: codebase, database, api, or full_stack"}, "organization_name": {"type": "string", "description": "Organization name for the assessment"}, "regulations": {"type": "string", "description": "Comma-separated regulations to check against", "default": "GDPR, CCPA, HIPAA"}, "data_categories": {"type": "string", "description": "Comma-separated data categories to focus on (e.g. 'customer, employee, financial')"}, "risk_tolerance": {"type": "string", "description": "Risk tolerance level: low, medium, or high", "default": "medium"}}},
  },
  {
    name: "email-campaign",
    description: "Generate email campaign with subject line variants and A/B copy",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["campaign_brief"], "properties": {"campaign_brief": {"type": "string", "description": "The campaign brief describing target audience, goals, and brand guidelines"}, "email_platform": {"type": "string", "description": "Email platform used (e.g. 'Mailchimp', 'HubSpot', 'Klaviyo', 'SendGrid') for platform-specific best practices"}, "list_size": {"type": "string", "description": "Approximate email list size for statistical significance calculations"}, "campaign_type": {"type": "string", "description": "Type: 'promotional', 'nurture', 'announcement', 'onboarding', 're-engagement', 'event'"}}},
  },
  {
    name: "freelancer-proposal",
    description: "Generate winning freelancer proposals by analyzing project requirements, matching portfolio pieces, crafting personalized pitches, and optimizing pricing strategy",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["project_description", "freelancer_skills"], "properties": {"project_description": {"type": "string", "description": "Full text of the client's project posting, job description, or RFP (paste the complete listing)"}, "freelancer_skills": {"type": "string", "description": "Comma-separated list of your core skills and expertise areas (e.g. 'React, Node.js, AWS, PostgreSQL, 8 years experience')"}, "portfolio_highlights": {"type": "string", "description": "Description of relevant past projects, case studies, or portfolio pieces to reference in the proposal", "default": "no specific portfolio provided"}, "hourly_rate": {"type": "string", "description": "Your standard hourly rate or rate range (e.g. '$85/hr', '$75-100/hr', '$5000 fixed for this type')", "default": "market rate"}, "platform": {"type": "string", "description": "Freelance platform the proposal is for (affects formatting and optimization strategy)", "default": "upwork"}}},
  },
  {
    name: "hotel-revenue-optimizer",
    description: "Analyze booking patterns, competitor rates, events, weather, and seasonal trends to generate dynamic room pricing, occupancy forecasts, and revenue management strategies",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["hotel_name", "location"], "properties": {"hotel_name": {"type": "string", "description": "Name of the hotel property to optimize (e.g. 'Grand Marina Resort', 'Downtown Business Hotel')"}, "location": {"type": "string", "description": "Hotel location - city, state/province, country (e.g. 'San Diego, CA, USA', 'Barcelona, Spain')"}, "room_types": {"type": "string", "description": "Comma-separated list of room types and inventory counts (e.g. 'Standard King:80, Deluxe Double:45, Suite:12, Presidential:2')"}, "competitor_hotels": {"type": "string", "description": "Comma-separated list of primary competitor hotels to monitor (e.g. 'Hilton Downtown, Marriott Waterfront, Hyatt Regency')"}, "planning_horizon": {"type": "string", "description": "Forward-looking planning period for pricing and forecasting", "default": "30 days"}}},
  },
  {
    name: "incident-responder",
    description: "Monitors PagerDuty alerts, correlates logs from Datadog, runs root cause analysis, and posts incident summary to Slack with remediation steps",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["alert_id"], "properties": {"alert_id": {"type": "string", "description": "PagerDuty alert or incident ID to investigate (e.g. 'P1234ABC')"}, "pagerduty_service": {"type": "string", "description": "PagerDuty service name or ID for additional context"}, "datadog_query": {"type": "string", "description": "Custom Datadog log query to narrow log search (default: auto-detect from alert)"}, "github_repo": {"type": "string", "description": "GitHub repository to check for recent deployments (e.g. 'org/repo')"}, "slack_channel": {"type": "string", "description": "Slack channel for incident updates (e.g. '#incidents')", "default": "#incidents"}, "severity": {"type": "string", "description": "Override severity level - 'SEV1' (critical), 'SEV2' (major), 'SEV3' (minor), or 'auto' to detect from alert", "default": "auto"}}},
  },
  {
    name: "influencer-campaign-matcher",
    description: "Analyze brand requirements and campaign goals, identify optimal influencer matches by content style, audience demographics, engagement authenticity, brand safety, and predicted ROI",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["brand_name", "campaign_goal", "target_audience"], "properties": {"brand_name": {"type": "string", "description": "Brand or company name running the campaign"}, "campaign_goal": {"type": "string", "description": "Primary campaign objective: awareness, engagement, conversion, or launch", "default": "awareness"}, "target_audience": {"type": "string", "description": "Target audience description (demographics, interests, behaviors)"}, "budget_range": {"type": "string", "description": "Campaign budget range (e.g. '$5K-$20K')", "default": "$5K-$20K"}, "platform_focus": {"type": "string", "description": "Comma-separated platforms to focus on", "default": "Instagram, TikTok, YouTube"}, "content_category": {"type": "string", "description": "Content vertical (e.g. 'fitness', 'tech', 'beauty', 'food')"}}},
  },
  {
    name: "insurance-claims-adjuster",
    description: "Ingest insurance claims with documents and photos, extract damage assessments, cross-reference policy coverage, detect fraud indicators, estimate payouts, and generate adjuster-ready case summaries",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["claim_id", "claim_type", "incident_description"], "properties": {"claim_id": {"type": "string", "description": "Unique claim identifier from the claims management system (e.g. 'CLM-2024-078342')"}, "claim_type": {"type": "string", "description": "Type of insurance claim: 'auto', 'property', 'health', or 'liability'"}, "policy_details": {"type": "string", "description": "Policy information including policy number, coverage type, limits, deductibles, endorsements, and effective dates"}, "claimant_info": {"type": "string", "description": "Claimant details including name, contact information, relationship to policyholder, and any prior claims history"}, "incident_description": {"type": "string", "description": "Detailed description of the incident including date, time, location, circumstances, parties involved, and injuries or damages claimed"}}},
  },
  {
    name: "jira-triage",
    description: "Auto-triage new Jira issues with priority, labels, and assignment suggestions",
    tags: [],
    step_count: 3,
    category: "general_ai",
    input_schema: {"required": ["project"], "properties": {"project": {"type": "string", "description": "Jira project key (e.g. PROJ)"}, "jql_filter": {"type": "string", "description": "Additional JQL filter (default: untriaged issues from last 24h)"}, "team_context": {"type": "string", "description": "Context about team members and their areas of expertise"}}},
  },
  {
    name: "job-description",
    description: "Generate inclusive job description with requirements, benefits, and interview plan",
    tags: [],
    step_count: 4,
    category: "general_ai",
    input_schema: {"required": ["role_brief"], "properties": {"role_brief": {"type": "string", "description": "Brief describing the role, responsibilities, and requirements"}, "company_name": {"type": "string", "description": "Company name for the About Us section"}, "salary_range": {"type": "string", "description": "Salary range to include (e.g. '$120k-$160k'). Recommended for transparency and SEO."}, "location_type": {"type": "string", "description": "Work arrangement: remote, hybrid, on-site, or flexible"}}},
  },
  {
    name: "knowledge-base-auditor",
    description: "Audit knowledge bases for staleness, contradictions with recent support tickets, coverage gaps, and outdated information, then produce prioritized update recommendations with freshness scores",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["kb_source"], "properties": {"kb_source": {"type": "string", "description": "Knowledge base source identifier (e.g. 'zendesk', 'intercom', 'helpscout', 'confluence') or URL to the knowledge base"}, "ticket_source": {"type": "string", "description": "Support ticket source for contradiction analysis (default: zendesk)", "default": "zendesk"}, "audit_period": {"type": "string", "description": "Time period to analyze for recent tickets and changes (default: last 90 days)", "default": "last 90 days"}, "content_types": {"type": "string", "description": "Comma-separated content types to audit (default: articles, FAQs, guides)", "default": "articles, FAQs, guides"}}},
  },
  {
    name: "memory-customer-context",
    description: "Customer support agent that remembers customer history, preferences, and past issues to provide personalized, context-aware support",
    tags: [],
    step_count: 1,
    category: "general_ai",
    input_schema: {"required": ["customer_id", "message"], "properties": {"customer_id": {"type": "string", "description": "Unique customer identifier"}, "message": {"type": "string", "description": "Customer's support message"}, "channel": {"type": "string", "description": "Support channel (email, chat, phone)", "default": "chat"}}},
  },
  {
    name: "memory-learning-agent",
    description: "Research agent that accumulates domain knowledge across runs, learns from past analyses, and progressively improves its insights",
    tags: [],
    step_count: 1,
    category: "general_ai",
    input_schema: {"required": ["topic", "question"], "properties": {"topic": {"type": "string", "description": "Research domain or topic area"}, "question": {"type": "string", "description": "Specific research question to investigate"}, "depth": {"type": "string", "description": "Analysis depth (quick, standard, deep)", "default": "standard"}}},
  },
  {
    name: "memory-standup-bot",
    description: "Daily standup assistant that remembers past standups to detect patterns, recurring blockers, and track team progress over time",
    tags: [],
    step_count: 1,
    category: "general_ai",
    input_schema: {"required": ["team_member", "update"], "properties": {"team_member": {"type": "string", "description": "Name of the team member giving the standup"}, "update": {"type": "string", "description": "What did you do yesterday? What are you doing today? Any blockers?"}}},
  },
  {
    name: "multi-channel-router",
    description: "Classify incoming tickets from email, chat, and social. Route to the right team with suggested responses and auto-escalation rules",
    tags: [],
    step_count: 5,
    category: "general_ai",
    input_schema: {"required": ["ticket_content"], "properties": {"ticket_content": {"type": "string", "description": "The incoming support ticket or message content"}, "channel": {"type": "string", "description": "Source channel - 'email', 'chat', 'twitter', 'facebook', 'instagram', or 'phone'", "default": "email"}, "customer_email": {"type": "string", "description": "Customer email address for account lookup"}, "slack_channel": {"type": "string", "description": "Slack channel for routing notifications (e.g. '#support-routing')", "default": "#support-routing"}, "escalation_rules": {"type": "string", "description": "Custom escalation rules - e.g. 'VIP customers -> direct to senior agent; billing issues -> billing team within 1h'"}}},
  },
  {
    name: "onboarding-workflow",
    description: "Automate new employee onboarding - checklist, welcome email, accounts, training plan, and manager notification",
    tags: [],
    step_count: 5,
    category: "general_ai",
    input_schema: {"required": ["employee_name", "role", "department", "start_date", "manager_email"], "properties": {"employee_name": {"type": "string", "description": "Full name of the new employee"}, "role": {"type": "string", "description": "Job title/role (e.g. 'Senior Frontend Engineer')"}, "department": {"type": "string", "description": "Department (e.g. 'Engineering', 'Marketing', 'Sales')"}, "start_date": {"type": "string", "description": "Start date in YYYY-MM-DD format"}, "manager_email": {"type": "string", "description": "Direct manager's email address"}, "employee_email": {"type": "string", "description": "New employee's email address (if already provisioned)"}, "location": {"type": "string", "description": "Office location or 'Remote' (default: 'Remote')", "default": "Remote"}, "seniority_level": {"type": "string", "description": "Seniority level: 'junior', 'mid', 'senior', 'lead', 'director', 'vp' (default: 'mid')", "default": "mid"}, "buddy_email": {"type": "string", "description": "Assigned onboarding buddy/mentor email (optional - will suggest if not provided)"}}},
  },
  {
    name: "outage-postmortem-generator",
    description: "Collect incident timeline from monitoring tools, correlate log entries, identify root cause chains, draft blameless postmortem documents, extract action items, and track remediation",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["incident_id", "incident_description"], "properties": {"incident_id": {"type": "string", "description": "Unique incident identifier from your ticketing system (e.g. 'INC-2024-0847', 'PD-12345')"}, "incident_description": {"type": "string", "description": "Detailed description of what happened, including initial symptoms observed and services impacted"}, "monitoring_sources": {"type": "string", "description": "Comma-separated list of monitoring and alerting tools used (e.g. 'PagerDuty, Datadog, Slack, Grafana, CloudWatch')", "default": "PagerDuty, Datadog, Slack"}, "affected_services": {"type": "string", "description": "Comma-separated list of services, components, or systems affected by the incident (e.g. 'api-gateway, user-auth, payments-service, postgres-primary')"}, "severity": {"type": "string", "description": "Incident severity level following standard classification: SEV-1 (critical), SEV-2 (major), SEV-3 (minor), SEV-4 (low)", "default": "SEV-1"}}},
  },
  {
    name: "patent-landscape-analyzer",
    description: "Conduct prior art searches across patent databases, map competitive IP landscapes, identify innovation white spaces, and generate freedom-to-operate risk assessments",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["technology_domain", "company_name"], "properties": {"technology_domain": {"type": "string", "description": "The technology domain to analyze (e.g. 'natural language processing', 'battery electrode chemistry', 'autonomous vehicle perception')"}, "company_name": {"type": "string", "description": "Your company or organization name for the FTO assessment"}, "target_jurisdictions": {"type": "string", "description": "Comma-separated list of patent jurisdictions to cover", "default": "US, EP, CN"}, "search_keywords": {"type": "string", "description": "Comma-separated specific keywords and phrases to include in prior art searches (e.g. 'transformer attention mechanism, multi-head self-attention, sparse attention')"}, "competitor_names": {"type": "string", "description": "Comma-separated list of competitor companies whose patent portfolios should be analyzed"}}},
  },
  {
    name: "permit-application-processor",
    description: "Automate government permit applications - extract requirements from local codes, pre-fill forms, check completeness, flag non-compliance, and track approval across jurisdictions",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["project_type", "jurisdiction", "project_description"], "properties": {"project_type": {"type": "string", "description": "Type of permit required: construction, business, environmental, zoning, event"}, "jurisdiction": {"type": "string", "description": "City, county, and state/country where the permit will be filed (e.g. 'Austin, TX', 'London Borough of Camden, UK')"}, "project_description": {"type": "string", "description": "Detailed description of the project requiring permits (scope, size, location, intended use)"}, "applicant_info": {"type": "string", "description": "Applicant details: name, organization, address, contact info, professional licenses held"}, "timeline_requirement": {"type": "string", "description": "Desired project timeline and any hard deadlines (e.g. 'construction start by March 2026, occupancy by December 2026')"}}},
  },
  {
    name: "pricing-tracker",
    description: "Monitor competitor pricing pages, detect changes, compare feature matrices, generate visual diff reports, and alert via Slack",
    tags: [],
    step_count: 4,
    category: "general_ai",
    input_schema: {"required": ["competitors"], "properties": {"competitors": {"type": "string", "description": "Comma-separated list of competitor names and their pricing page URLs - e.g. 'Acme:https://acme.com/pricing, Globex:https://globex.io/plans'"}, "our_product": {"type": "string", "description": "Your product name for comparison context"}, "our_pricing": {"type": "string", "description": "Your current pricing tiers and features summary for side-by-side comparison"}, "slack_channel": {"type": "string", "description": "Slack channel for pricing change alerts (e.g. '#competitive-intel')", "default": "#competitive-intel"}, "focus_areas": {"type": "string", "description": "Specific areas to monitor - e.g. 'enterprise tier, API pricing, per-seat costs, usage limits'"}}},
  },
  {
    name: "product-design-spec",
    description: "Transform user stories and requirements into comprehensive product design specifications with wireframe descriptions, interaction patterns, and developer handoff documentation",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["product_name", "user_stories"], "properties": {"product_name": {"type": "string", "description": "Name of the product or feature being designed (e.g. 'Acme Dashboard', 'Checkout Flow Redesign')"}, "user_stories": {"type": "string", "description": "User stories, requirements, or feature descriptions to translate into design specs (paste all stories/requirements)"}, "design_system": {"type": "string", "description": "Design system to use or reference (e.g. 'Material Design 3', 'Apple HIG', 'Ant Design', 'custom')", "default": "custom"}, "target_platforms": {"type": "string", "description": "Comma-separated target platforms (e.g. 'web, iOS, Android', 'web-only', 'responsive web, native iOS')", "default": "responsive web"}, "accessibility_level": {"type": "string", "description": "Accessibility conformance target (e.g. 'WCAG 2.1 AA', 'WCAG 2.2 AAA', 'Section 508')", "default": "WCAG 2.1 AA"}}},
  },
  {
    name: "qbr-autopilot",
    description: "Generate complete Quarterly Business Review packages - pull CRM data, usage metrics, support trends, and renewal status into executive summaries, health scorecards, and strategic recommendations",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["account_name", "crm_source", "reporting_quarter"], "properties": {"account_name": {"type": "string", "description": "Name of the customer account for the QBR"}, "crm_source": {"type": "string", "description": "CRM platform containing account data (e.g., Salesforce, HubSpot, Pipedrive)"}, "reporting_quarter": {"type": "string", "description": "Quarter under review (e.g., Q1 2026, Q4 2025)"}, "account_tier": {"type": "string", "description": "Account tier classification for service level expectations", "default": "enterprise"}, "csm_name": {"type": "string", "description": "Customer Success Manager name for personalization"}}},
  },
  {
    name: "release-notes",
    description: "Generate user-facing release notes and internal changelog from commit history",
    tags: [],
    step_count: 4,
    category: "general_ai",
    input_schema: {"required": ["changelog"], "properties": {"changelog": {"type": "string", "description": "Git diff or changelog text to generate release notes from"}}},
  },
  {
    name: "release-notes-pro",
    description: "Pull merged PRs from GitHub, classify changes, generate user-facing release notes, create changelog entries, and post to Slack and docs site",
    tags: [],
    step_count: 5,
    category: "general_ai",
    input_schema: {"required": ["repo", "version"], "properties": {"repo": {"type": "string", "description": "GitHub repository in owner/repo format - e.g. 'acme/platform'"}, "version": {"type": "string", "description": "Release version tag - e.g. 'v2.4.0'"}, "since_tag": {"type": "string", "description": "Previous version tag to compare against - e.g. 'v2.3.0'. If empty, uses the last tag before the current version."}, "slack_channel": {"type": "string", "description": "Slack channel for release announcement (e.g. '#releases')", "default": "#releases"}, "audience": {"type": "string", "description": "Target audience for the notes - 'developers', 'end-users', 'internal', or 'all' (default: 'all')", "default": "all"}}},
  },
  {
    name: "rfp-response-engine",
    description: "Multi-agent RFP/RFQ response generator that ingests bid requirements, matches against company knowledge base, drafts section-by-section responses, and scores proposal strength",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["rfp_document", "company_name"], "properties": {"rfp_document": {"type": "string", "description": "Full text of the RFP/RFQ document, or URL to the bid document"}, "company_name": {"type": "string", "description": "Your company name (the bidder/proposer)"}, "knowledge_base_source": {"type": "string", "description": "Source of company knowledge base for response matching (e.g. 'confluence', 'notion', 'sharepoint', or paste key capabilities)"}, "win_themes": {"type": "string", "description": "Comma-separated strategic win themes to weave into the proposal (e.g. 'innovation leader, proven ROI, local presence')"}, "submission_deadline": {"type": "string", "description": "Submission deadline for the RFP (e.g. '2026-03-15')"}}},
  },
  {
    name: "saas-usage-optimizer",
    description: "Audit SaaS subscriptions across the organization, analyze usage vs licensed seats, identify redundant tools, calculate waste, and produce consolidation roadmap with savings projections",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["organization_name", "employee_count"], "properties": {"organization_name": {"type": "string", "description": "Name of the organization being audited"}, "saas_inventory": {"type": "string", "description": "Comma-separated list of known SaaS tools in use"}, "employee_count": {"type": "number", "description": "Total number of employees in the organization"}, "annual_saas_budget": {"type": "string", "description": "Annual SaaS budget in USD (e.g., 500000)"}, "optimization_goal": {"type": "string", "description": "Primary optimization goal", "default": "reduce_waste"}}},
  },
  {
    name: "seo-content",
    description: "Research keywords and create SEO-optimized article with meta tags",
    tags: [],
    step_count: 4,
    category: "general_ai",
    input_schema: {"required": ["topic"], "properties": {"topic": {"type": "string", "description": "The topic to research keywords for and write an SEO-optimized article about"}, "target_audience": {"type": "string", "description": "Primary target audience for the content (e.g. 'SaaS founders', 'beginner developers')"}, "brand_url": {"type": "string", "description": "Your website URL for internal linking strategy and brand context"}}},
  },
  {
    name: "slack-standup",
    description: "Collect daily standup updates from Slack and post a summary",
    tags: [],
    step_count: 3,
    category: "general_ai",
    input_schema: {"required": ["standup_channel"], "properties": {"standup_channel": {"type": "string", "description": "Slack channel to read standups from (e.g. #daily-standup)"}, "summary_channel": {"type": "string", "description": "Slack channel to post summary to (defaults to standup_channel)"}, "lookback_hours": {"type": "number", "description": "Hours to look back for messages (default: 24)"}, "sprint_goal": {"type": "string", "description": "Current sprint goal for alignment tracking (e.g. 'Ship v2.0 checkout flow')"}, "team_members": {"type": "string", "description": "Comma-separated list of expected team members for missing-update detection"}}},
  },
  {
    name: "social-repurposer",
    description: "Take any blog post URL, extract key points, generate optimized posts for Twitter/X, LinkedIn, Instagram, and TikTok with image prompts",
    tags: [],
    step_count: 5,
    category: "general_ai",
    input_schema: {"required": ["blog_url"], "properties": {"blog_url": {"type": "string", "description": "URL of the blog post to repurpose into social media content"}, "brand_voice": {"type": "string", "description": "Brand voice guidelines - e.g. 'professional but approachable, avoid jargon, use data-driven claims'"}, "target_platforms": {"type": "string", "description": "Comma-separated platforms to generate for - 'twitter, linkedin, instagram, tiktok' (default: all)", "default": "twitter, linkedin, instagram, tiktok"}, "cta_url": {"type": "string", "description": "Call-to-action URL to include in posts (e.g., landing page URL). Defaults to the blog URL."}, "hashtag_strategy": {"type": "string", "description": "Hashtag preferences - e.g. 'industry-specific, max 5 per post, include branded hashtag #AcmeTech'"}}},
  },
  {
    name: "student-learning-path-builder",
    description: "Analyze student assessment results and learning preferences to generate personalized learning paths with resource recommendations, pacing adjustments, and intervention triggers",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["student_profile", "subject_area"], "properties": {"student_profile": {"type": "string", "description": "Description of the student - include current level (grade, year, or skill level), academic history, assessment scores, known strengths and weaknesses, learning goals, and any relevant context (e.g. 'Grade 10 student, strong in algebra but struggling with geometry proofs, SAT prep goal, diagnosed ADHD')"}, "subject_area": {"type": "string", "description": "Subject or skill area for the learning path (e.g. 'High School Mathematics', 'AP Biology', 'Python Programming', 'Academic English Writing', 'Data Science Fundamentals')"}, "curriculum_standard": {"type": "string", "description": "Curriculum framework or standard to align to (e.g. 'Common Core State Standards', 'IB Diploma', 'AP Curriculum', 'NGSS', 'Cambridge IGCSE', or 'custom')", "default": "custom"}, "learning_style": {"type": "string", "description": "Preferred learning modality: 'visual', 'auditory', 'kinesthetic', 'reading-writing', or 'mixed'", "default": "mixed"}, "available_resources": {"type": "string", "description": "Comma-separated list of resource types available to the student", "default": "video, text, interactive, labs"}}},
  },
  {
    name: "summarize",
    description: "Performs deep text analysis with parallel key-point extraction and structural analysis, producing a tailored executive summary",
    tags: [],
    step_count: 4,
    category: "general_ai",
    input_schema: {"required": ["text"], "properties": {"text": {"type": "string", "description": "The text to summarize"}, "format": {"type": "string", "description": "Output format style - executive, bullet-points, narrative, or academic", "default": "executive"}, "max_length": {"type": "string", "description": "Target length for the final summary (e.g. '500 words', '1 page', '3 paragraphs')", "default": "500 words"}, "audience": {"type": "string", "description": "Target audience - determines vocabulary complexity and assumed background knowledge", "default": "general"}}},
  },
  {
    name: "tax-return-preprocessor",
    description: "Ingest W-2s, 1099s, receipts, and bank statements, categorize transactions, identify deductions, flag anomalies, and produce structured data packages ready for CPA review",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["tax_year", "filing_status"], "properties": {"tax_year": {"type": "string", "description": "Tax year being prepared (e.g. '2025', '2024')"}, "filing_status": {"type": "string", "description": "IRS filing status: 'single', 'married_joint', 'married_separate', 'head_of_household', or 'qualifying_surviving_spouse'"}, "document_sources": {"type": "string", "description": "Comma-separated list of document sources provided (e.g. 'W-2, 1099-NEC, 1099-INT, 1099-DIV, 1099-B, bank statements, receipts, K-1')"}, "business_type": {"type": "string", "description": "Type of taxpayer: 'individual' (W-2 employee), 'self_employed' (Schedule C), 'partnership' (K-1), 'scorp' (K-1 + W-2), 'rental_property' (Schedule E)", "default": "individual"}}},
  },
  {
    name: "translate",
    description: "Professional-grade translation with source analysis, cultural adaptation, precise translation, and quality review",
    tags: [],
    step_count: 4,
    category: "general_ai",
    input_schema: {"required": ["text", "target_language"], "properties": {"text": {"type": "string", "description": "The text to translate"}, "target_language": {"type": "string", "description": "The target language to translate into (e.g. 'Spanish', 'Japanese', 'Czech')"}, "tone": {"type": "string", "description": "Desired tone - professional, casual, formal, literary, technical, or marketing", "default": "professional"}, "domain": {"type": "string", "description": "Subject domain for terminology accuracy - general, legal, medical, technical, financial, academic, or marketing", "default": "general"}}},
  },
  {
    name: "vendor-renewal-negotiator",
    description: "Monitor SaaS and vendor contract renewals, analyze usage vs terms, benchmark pricing against market rates, identify leverage points, and draft counter-proposals",
    tags: [],
    step_count: 6,
    category: "general_ai",
    input_schema: {"required": ["vendor_list", "company_name"], "properties": {"vendor_list": {"type": "string", "description": "Comma-separated list of vendor/SaaS products up for renewal (e.g. 'Salesforce, Snowflake, Datadog, Slack, Zoom')"}, "company_name": {"type": "string", "description": "Your company name for the negotiation context"}, "contract_data": {"type": "string", "description": "Contract details: renewal dates, current annual spend per vendor, contract length, payment terms (e.g. 'Salesforce: $240k/yr, renews June 2026, 3yr term; Datadog: $180k/yr, renews Aug 2026, 1yr term')"}, "usage_metrics": {"type": "string", "description": "Current usage data per vendor: active users, license utilization, feature adoption, storage consumption (e.g. 'Salesforce: 150/200 licenses used, Enterprise tier; Datadog: 80 hosts monitored, 500GB/day ingestion')"}, "negotiation_priority": {"type": "string", "description": "Primary negotiation objective: cost_reduction, flexibility, feature_upgrade, term_optimization, consolidation", "default": "cost_reduction"}}},
  },
  // --- marketing (new) ---
  {
    name: "ai-content-pipeline",
    description: "Generate blog content with OpenAI, create audio version with ElevenLabs, and store assets in AWS S3.",
    tags: ["AI", "Content", "Text-to-Speech", "S3"],
    step_count: 4,
    category: "marketing",
    input_schema: {"type": "object", "required": ["topic"], "properties": {"topic": {"type": "string", "description": "Blog post topic"}, "tone": {"type": "string", "description": "Writing tone (professional, casual, technical)", "default": "professional"}, "s3_prefix": {"type": "string", "description": "S3 key prefix for storing assets", "default": "content/blog"}}},
  },
];

const TEMPLATE_YAMLS: Record<string, string> = {
  "text-summarizer": `name: "summarize"
description: "Summarize text input with configurable detail level"
default_model: sonnet
default_max_turns: 10
default_timeout: 300

steps:
  - id: "extract"
    prompt: |
      Extract the key points from the following text.
      Focus on the main arguments and supporting evidence.
      Input: {input.text}
    model: haiku

  - id: "summarize"
    prompt: |
      Write a concise summary based on these key points.
      Detail level: {input.detail_level}
      Key points: {steps.extract.output}
    model: sonnet
    depends_on:
      - "extract"
`,
  "language-translator": `name: "translate"
description: "Detect language and translate to target language"
default_model: sonnet
default_max_turns: 10
default_timeout: 300

steps:
  - id: "detect_language"
    prompt: |
      Detect the language of the following text and return the language code.
      Text: {input.text}
    model: haiku

  - id: "translate"
    prompt: |
      Translate the following text to {input.target_language}.
      Source language: {steps.detect_language.output}
      Text: {input.text}
    model: sonnet
    depends_on:
      - "detect_language"
`,
  "research-agent": `name: "research_agent"
description: "Multi-source research with parallel analysis and fact extraction"
default_model: sonnet
default_max_turns: 10
default_timeout: 300

steps:
  - id: "plan"
    prompt: |
      Create a research plan for the topic: {input.topic}
      Identify 3-5 angles to investigate and list them as JSON array.
    model: sonnet

  - id: "research"
    prompt: |
      Research the following angle in depth.
      Topic: {input.topic}
      Angle: {item}
      Return key findings with sources.
    model: sonnet
    depends_on:
      - "plan"
    parallel_over: "steps.plan.output"

  - id: "extract_facts"
    prompt: |
      Extract verified facts from all research findings.
      Findings: {steps.research.output}
      Return a structured list of facts with confidence scores.
    model: sonnet
    depends_on:
      - "research"

  - id: "synthesize"
    prompt: |
      Synthesize the research into a comprehensive report.
      Facts: {steps.extract_facts.output}
      Include an executive summary, detailed findings, and recommendations.
    model: opus
    depends_on:
      - "extract_facts"
`,
  "chain-of-thought-solver": `name: "chain_of_thought"
description: "Step-by-step reasoning through complex problems"
default_model: sonnet
default_max_turns: 10
default_timeout: 300

steps:
  - id: "decompose"
    prompt: |
      Break down this problem into logical sub-problems.
      Problem: {input.problem}
      Return a numbered list of sub-problems to solve in order.
    model: sonnet

  - id: "reason"
    prompt: |
      Solve each sub-problem step by step.
      Sub-problems: {steps.decompose.output}
      Show your reasoning for each step.
    model: opus

  - id: "conclude"
    prompt: |
      Based on the step-by-step reasoning, provide the final answer.
      Reasoning: {steps.reason.output}
      Original problem: {input.problem}
      Verify the answer is consistent with all reasoning steps.
    model: sonnet
    depends_on:
      - "reason"
`,
  "review-and-approve": `name: "review_and_approve"
description: "Content generation with human approval gate before publishing"
default_model: sonnet
default_max_turns: 10
default_timeout: 300

steps:
  - id: "generate"
    prompt: |
      Generate content based on the following brief.
      Brief: {input.brief}
      Tone: {input.tone}
      Target audience: {input.audience}
    model: sonnet

  - id: "review"
    type: approval
    approval_config:
      message: "Review the generated content before publishing"
      timeout_hours: 24
      on_timeout: abort
      allow_edit: true
    depends_on:
      - "generate"

  - id: "publish"
    prompt: |
      Format the approved content for publishing.
      Content: {steps.generate.output}
      Format: {input.output_format}
    model: haiku
    depends_on:
      - "review"
`,
  "blog-to-social": `# name: Blog to Social Media
# description: Transform a blog post into platform-specific social media content
# tags: [Marketing, Content, Social]

name: blog-to-social
description: Transform a blog post into platform-specific social media content

default_model: claude-sonnet-4-20250514
default_max_turns: 5
default_timeout: 180

steps:
  - id: analyze-post
    prompt: >
      Analyze the following blog post and extract the key points, overall tone,
      target audience, and core message. Identify the most shareable insights
      and any statistics or quotes worth highlighting.
      Blog post: {input.blog_post}
    model: claude-sonnet-4-20250514
    max_turns: 5

  - id: linkedin-post
    depends_on: [analyze-post]
    prompt: >
      Using the blog analysis below, write a professional LinkedIn post that
      drives engagement. Use a thought-leadership tone, include a compelling
      hook in the first line, add relevant line breaks for readability, and
      end with a call-to-action. Keep it under 1300 characters.
      Analysis: {steps.analyze-post.output}
    model: claude-sonnet-4-20250514
    max_turns: 3

  - id: twitter-thread
    depends_on: [analyze-post]
    prompt: >
      Using the blog analysis below, write an X/Twitter thread of exactly
      5 tweets. The first tweet should hook the reader with a bold claim or
      question. Each tweet must be under 280 characters. Number them 1/5
      through 5/5 and make the last tweet link back to the original post.
      Analysis: {steps.analyze-post.output}
    model: claude-sonnet-4-20250514
    max_turns: 3

  - id: instagram-caption
    depends_on: [analyze-post]
    prompt: >
      Using the blog analysis below, write an engaging Instagram caption.
      Start with an attention-grabbing first line, use short paragraphs,
      include relevant emojis sparingly, and end with 15-20 relevant hashtags
      grouped at the bottom. Keep the caption under 2200 characters.
      Analysis: {steps.analyze-post.output}
    model: claude-haiku-4-5-20251001
    max_turns: 3

  - id: compile
    depends_on: [linkedin-post, twitter-thread, instagram-caption]
    prompt: >
      Compile all the social media content variants into a single structured
      output. Include sections for LinkedIn, X/Twitter, and Instagram. Add a
      brief recommendation on optimal posting times and any platform-specific
      tips for maximizing engagement.
      LinkedIn: {steps.linkedin-post.output}
      Twitter: {steps.twitter-thread.output}
      Instagram: {steps.instagram-caption.output}
    model: claude-haiku-4-5-20251001
    max_turns: 3
`,
  "seo-content-writer": `# name: SEO Content Writer
# description: Research keywords and create SEO-optimized article with meta tags
# tags: [Marketing, SEO, Content]

name: seo-content-writer
description: Research keywords and create SEO-optimized article with meta tags

default_model: claude-sonnet-4-20250514
default_max_turns: 10
default_timeout: 300

steps:
  - id: keyword-research
    prompt: >
      Analyze the following topic and perform keyword research. Identify a
      primary keyword, 5-8 secondary keywords, and long-tail variations.
      For each keyword, describe the likely search intent (informational,
      transactional, navigational) and estimated competition level.
      Topic: {input.topic}
    model: claude-sonnet-4-20250514
    max_turns: 5

  - id: outline
    depends_on: [keyword-research]
    prompt: >
      Create a detailed article outline optimized for the target keywords.
      Structure it with a compelling H1 title, 4-6 H2 sections, and H3
      subsections where appropriate. Include notes on where to naturally
      place primary and secondary keywords. Plan for approximately 1500-2000
      words total.
      Keywords: {steps.keyword-research.output}
    model: claude-sonnet-4-20250514
    max_turns: 5

  - id: write-article
    depends_on: [outline]
    prompt: >
      Write the full SEO-optimized article following the outline provided.
      Naturally incorporate the target keywords without stuffing. Use short
      paragraphs, include transition sentences between sections, and write
      in an authoritative yet accessible tone. Add a strong introduction
      and a conclusion with a clear call-to-action.
      Outline: {steps.outline.output}
    model: claude-sonnet-4-20250514
    max_turns: 10

  - id: meta-tags
    depends_on: [write-article]
    prompt: >
      Generate SEO meta tags for the article. Include a title tag (under 60
      characters), meta description (under 155 characters), Open Graph title
      and description, Twitter card tags, and a suggested URL slug. Ensure
      the primary keyword appears in the title tag and meta description.
      Article: {steps.write-article.output}
    model: claude-haiku-4-5-20251001
    max_turns: 3
`,
  "email-campaign-generator": `# name: Email Campaign Generator
# description: Generate email campaign with subject line variants and A/B copy
# tags: [Marketing, Email, Campaign]

name: email-campaign-generator
description: Generate email campaign with subject line variants and A/B copy

default_model: claude-sonnet-4-20250514
default_max_turns: 5
default_timeout: 180

steps:
  - id: audience-brief
    prompt: >
      Analyze the target audience and campaign goal described below. Identify
      the audience demographics, pain points, motivations, and the primary
      action you want them to take. Define the tone of voice and any brand
      guidelines to follow.
      Campaign brief: {input.campaign_brief}
    model: claude-sonnet-4-20250514
    max_turns: 5

  - id: subject-lines
    depends_on: [audience-brief]
    prompt: >
      Generate 5 email subject line variants for this campaign. For each
      variant, use a different persuasion technique (curiosity, urgency,
      personalization, benefit-driven, social proof). Explain the reasoning
      behind each and predict which audience segment it would resonate with most.
      Audience brief: {steps.audience-brief.output}
    model: claude-sonnet-4-20250514
    max_turns: 3

  - id: body-variant-a
    depends_on: [audience-brief]
    prompt: >
      Write email body Variant A using a benefit-focused approach. Lead with
      the key value proposition, use bullet points to highlight benefits,
      include one testimonial placeholder, and end with a clear CTA button
      text. Keep the email concise - under 200 words for the body copy.
      Audience brief: {steps.audience-brief.output}
    model: claude-sonnet-4-20250514
    max_turns: 3

  - id: body-variant-b
    depends_on: [audience-brief]
    prompt: >
      Write email body Variant B using a story-focused approach. Open with
      a relatable scenario or customer story, build emotional connection,
      then transition to the product as the solution. End with a soft CTA
      that feels like a natural next step. Keep it under 250 words.
      Audience brief: {steps.audience-brief.output}
    model: claude-sonnet-4-20250514
    max_turns: 3

  - id: review
    depends_on: [subject-lines, body-variant-a, body-variant-b]
    type: approval
    prompt: >
      Review the complete email campaign package before sending. Verify
      subject lines, both body variants, and overall brand alignment.
    approval_config:
      message: "Review the email campaign variants and approve for sending"
      show_data: steps.body-variant-a.output
      timeout_hours: 24
      on_timeout: abort
      allow_edit: true
`,
  "competitor-analysis": `# name: Competitor Analysis
# description: Analyze competitor positioning, strengths, weaknesses, and opportunities
# tags: [Marketing, Strategy, Research]

name: competitor-analysis
description: Analyze competitor positioning, strengths, weaknesses, and opportunities

default_model: claude-sonnet-4-20250514
default_max_turns: 10
default_timeout: 300

steps:
  - id: gather-info
    prompt: >
      Research the following competitor thoroughly. Gather information about
      their product offerings, pricing model, key messaging and positioning,
      target market segments, and overall market share. Identify their
      marketing channels and recent strategic moves.
      Competitor: {input.competitor}
    model: claude-sonnet-4-20250514
    max_turns: 10

  - id: analyze-strengths
    depends_on: [gather-info]
    prompt: >
      Based on the competitor research below, identify and analyze their key
      strengths. Focus on what they do well in product quality, brand
      perception, customer experience, market positioning, and technical
      capabilities. Rank each strength by impact on their market position.
      Research: {steps.gather-info.output}
    model: claude-sonnet-4-20250514
    max_turns: 5

  - id: analyze-weaknesses
    depends_on: [gather-info]
    prompt: >
      Based on the competitor research below, identify gaps, weaknesses,
      and areas of customer dissatisfaction. Look for common complaints,
      missing features, pricing concerns, poor support experiences, and
      strategic blind spots. Highlight areas that represent opportunities
      for differentiation.
      Research: {steps.gather-info.output}
    model: claude-sonnet-4-20250514
    max_turns: 5

  - id: swot-report
    depends_on: [analyze-strengths, analyze-weaknesses]
    prompt: >
      Compile a comprehensive SWOT analysis report combining the strengths
      and weaknesses analysis. Add an Opportunities section identifying how
      to capitalize on competitor weaknesses, and a Threats section covering
      risks from their strengths. End with 3-5 actionable recommendations
      for competitive positioning.
      Strengths: {steps.analyze-strengths.output}
      Weaknesses: {steps.analyze-weaknesses.output}
    model: claude-sonnet-4-20250514
    max_turns: 10
`,
  "ad-copy-generator": `# name: Ad Copy Generator
# description: Generate ad copy variants for Google Ads and Meta Ads campaigns
# tags: [Marketing, Advertising, Copywriting]

name: ad-copy-generator
description: Generate ad copy variants for Google Ads and Meta Ads campaigns

default_model: claude-sonnet-4-20250514
default_max_turns: 5
default_timeout: 180

steps:
  - id: analyze-product
    prompt: >
      Analyze the following product brief and extract the unique selling
      propositions, target audience segments, key benefits, and competitive
      differentiators. Identify the primary emotional triggers and rational
      arguments that would drive conversions.
      Product brief: {input.product_brief}
    model: claude-sonnet-4-20250514
    max_turns: 5

  - id: google-ads
    depends_on: [analyze-product]
    prompt: >
      Generate 5 Google Ads variants based on the product analysis. Each
      variant must include 3 headlines (max 30 characters each), 2
      descriptions (max 90 characters each), and display URL paths. Use
      different angles for each variant - feature-focused, benefit-focused,
      urgency, social proof, and competitive comparison.
      Product analysis: {steps.analyze-product.output}
    model: claude-sonnet-4-20250514
    max_turns: 5

  - id: meta-ads
    depends_on: [analyze-product]
    prompt: >
      Generate 5 Meta/Facebook ad variants based on the product analysis.
      Each variant must include primary text (up to 125 characters for
      optimal display), a headline (max 40 characters), a link description,
      and a suggested CTA button type. Vary the creative approach across
      variants - storytelling, testimonial-style, direct response, question
      hook, and listicle format.
      Product analysis: {steps.analyze-product.output}
    model: claude-sonnet-4-20250514
    max_turns: 5

  - id: compile-report
    depends_on: [google-ads, meta-ads]
    prompt: >
      Compile all ad variants into a structured report. For each variant,
      add a recommendation score (1-10) based on predicted click-through
      rate potential. Suggest which variants to A/B test first and provide
      a recommended budget split across the top-performing variants.
      Google Ads: {steps.google-ads.output}
      Meta Ads: {steps.meta-ads.output}
    model: claude-haiku-4-5-20251001
    max_turns: 3
`,
  "lead-enrichment": `# name: Lead Enrichment
# description: Research and enrich lead data with company info, scoring, and outreach angles
# tags: [Sales, Research, Lead-Gen]

name: lead-enrichment
description: Research and enrich lead data with company info, scoring, and outreach angles

default_model: sonnet
default_max_turns: 10
default_timeout: 300

steps:
  - id: research-company
    prompt: >
      Research the following company thoroughly. Find their company size,
      industry vertical, recent news and press releases, technology stack,
      and any known funding rounds or financial milestones.
      Company: {input.company_name}
      Domain: {input.company_domain}
    model: claude-sonnet-4-20250514
    max_turns: 10

  - id: research-contacts
    depends_on: [research-company]
    prompt: >
      Based on the company research, identify key decision makers and
      stakeholders who would be relevant for a B2B sales conversation.
      Include their titles, responsibilities, and any public LinkedIn or
      professional profile insights.
      Company info: {steps.research-company.output}
      Target persona: {input.target_persona}
    model: claude-sonnet-4-20250514
    max_turns: 8

  - id: score-lead
    depends_on: [research-company]
    prompt: >
      Score this lead on a scale of 1-100 based on how well it matches
      our Ideal Customer Profile (ICP). Consider company size, industry fit,
      technology compatibility, and growth signals. Provide a breakdown of
      scoring factors with individual scores and reasoning.
      Company info: {steps.research-company.output}
      ICP criteria: {input.icp_criteria}
    model: claude-haiku-4-5-20251001
    max_turns: 5

  - id: outreach-angles
    depends_on: [research-company, research-contacts]
    prompt: >
      Suggest 3 personalized outreach angles for engaging this lead.
      Each angle should reference specific company details, recent events,
      or contact-specific insights. Include a suggested subject line and
      opening sentence for each approach.
      Company info: {steps.research-company.output}
      Key contacts: {steps.research-contacts.output}
      Our value prop: {input.value_proposition}
    model: claude-sonnet-4-20250514
    max_turns: 8

  - id: compile-profile
    depends_on: [research-contacts, score-lead, outreach-angles]
    prompt: >
      Compile a complete lead profile document combining all research findings.
      Structure it with sections for Company Overview, Key Contacts, Lead Score
      with rationale, and Recommended Outreach Strategy. Format it cleanly
      for the sales team to review and act on.
      Contacts: {steps.research-contacts.output}
      Lead score: {steps.score-lead.output}
      Outreach angles: {steps.outreach-angles.output}
    model: claude-sonnet-4-20250514
    max_turns: 10
`,
  "proposal-generator": `# name: Proposal Generator
# description: Generate a customized business proposal from meeting notes and product info
# tags: [Sales, Document, Proposal]

name: proposal-generator
description: Generate a customized business proposal from meeting notes and product info

default_model: sonnet
default_max_turns: 10
default_timeout: 300

steps:
  - id: extract-requirements
    prompt: >
      Analyze the following meeting notes and extract all client requirements,
      pain points, budget signals, timeline expectations, and any technical
      constraints mentioned. Organize findings by priority and flag any
      ambiguous or missing information that should be clarified.
      Meeting notes: {input.meeting_notes}
      Client name: {input.client_name}
    model: claude-sonnet-4-20250514
    max_turns: 8

  - id: match-solutions
    depends_on: [extract-requirements]
    prompt: >
      Map each identified client need to our product features and solutions.
      For each pain point, explain how our offering addresses it, include
      relevant case studies or metrics where applicable, and note any gaps
      where custom work or integrations may be needed.
      Client requirements: {steps.extract-requirements.output}
      Product catalog: {input.product_info}
    model: claude-sonnet-4-20250514
    max_turns: 10

  - id: write-proposal
    depends_on: [match-solutions]
    prompt: >
      Write a complete business proposal document with the following sections:
      Executive Summary, Understanding of Needs, Proposed Solution, Implementation
      Timeline, Pricing and Investment, and Next Steps. Use a professional tone,
      reference specific client pain points, and highlight ROI where possible.
      Solution mapping: {steps.match-solutions.output}
      Client name: {input.client_name}
      Pricing tier: {input.pricing_tier}
    model: claude-sonnet-4-20250514
    max_turns: 10

  - id: review-gate
    depends_on: [write-proposal]
    type: approval
    prompt: Review the generated proposal before sending to client
    approval_config:
      message: "Review the generated proposal and approve for delivery to the client"
      show_data: steps.write-proposal.output
      timeout_hours: 48
      on_timeout: abort
      allow_edit: true
`,
  "meeting-recap": `# name: Meeting Recap
# description: Transform meeting transcript into summary, action items, and follow-up email
# tags: [Sales, Productivity, Communication]

name: meeting-recap
description: Transform meeting transcript into summary, action items, and follow-up email

default_model: sonnet
default_max_turns: 10
default_timeout: 300

steps:
  - id: summarize
    prompt: >
      Create a structured meeting summary from the following transcript.
      Include the meeting date, attendees, key discussion points, decisions
      made, and any open questions. Organize by topic and highlight the
      most important outcomes clearly.
      Transcript: {input.transcript}
      Meeting title: {input.meeting_title}
    model: claude-sonnet-4-20250514
    max_turns: 8

  - id: action-items
    depends_on: [summarize]
    prompt: >
      Extract all action items from the meeting summary. For each item,
      specify the owner (who is responsible), a clear description of the
      task, the agreed deadline or timeframe, and the priority level.
      Format as a structured checklist that can be imported into a task
      tracker.
      Meeting summary: {steps.summarize.output}
    model: claude-haiku-4-5-20251001
    max_turns: 5

  - id: follow-up-email
    depends_on: [summarize, action-items]
    prompt: >
      Draft a professional follow-up email to send to all meeting attendees.
      Include a brief recap of key decisions, the full list of action items
      with owners and deadlines, and proposed next steps or next meeting date.
      Keep the tone friendly but professional, and make it easy to scan quickly.
      Meeting summary: {steps.summarize.output}
      Action items: {steps.action-items.output}
      Sender name: {input.sender_name}
    model: claude-sonnet-4-20250514
    max_turns: 8
`,
  "ticket-classifier": `# name: Ticket Classifier
# description: Classify support ticket, assign priority, and draft response
# tags: [Support, Classification, Automation]

name: ticket-classifier
description: Classify support ticket, assign priority, and draft response

default_model: sonnet
default_max_turns: 10
default_timeout: 300

steps:
  - id: classify
    prompt: >
      Analyze the following support ticket and classify it into one of these
      categories: bug, feature_request, billing, how_to, or account. Also
      detect the customer sentiment (positive, neutral, frustrated, angry)
      and identify the core topic or product area involved.
      Ticket subject: {input.subject}
      Ticket body: {input.body}
      Customer tier: {input.customer_tier}
    model: claude-haiku-4-5-20251001
    max_turns: 5
    output_schema:
      type: object
      properties:
        category:
          type: string
          enum: [bug, feature_request, billing, how_to, account]
        sentiment:
          type: string
          enum: [positive, neutral, frustrated, angry]
        topic:
          type: string
      required: [category, sentiment, topic]

  - id: prioritize
    depends_on: [classify]
    prompt: >
      Assign a priority level (P1-P4) to this support ticket based on
      the classification, customer sentiment, potential business impact,
      and customer tier. P1 is critical and needs immediate attention,
      P4 is low priority. Provide a brief justification for the priority.
      Classification: {steps.classify.output}
      Customer tier: {input.customer_tier}
    model: claude-haiku-4-5-20251001
    max_turns: 5

  - id: draft-response
    depends_on: [classify, prioritize]
    prompt: >
      Draft a helpful and empathetic support response addressing the
      customer's issue. Match the tone to the detected sentiment - be
      extra empathetic for frustrated or angry customers. Include specific
      troubleshooting steps for bugs, clear explanations for how-to questions,
      and appropriate escalation language for billing or account issues.
      Original ticket: {input.body}
      Classification: {steps.classify.output}
      Priority: {steps.prioritize.output}
    model: claude-sonnet-4-20250514
    max_turns: 8

  - id: suggest-routing
    depends_on: [classify, prioritize]
    prompt: >
      Based on the ticket classification and priority, suggest the best
      internal team to route this ticket to. Choose from: engineering
      (for bugs and technical issues), billing (for payment and subscription),
      success (for account management and feature requests), or support
      (for how-to and general inquiries). Include a brief handoff note
      for the receiving team.
      Classification: {steps.classify.output}
      Priority: {steps.prioritize.output}
    model: claude-haiku-4-5-20251001
    max_turns: 5
`,
  "review-sentiment": `# name: Review Sentiment
# description: Analyze customer reviews to extract sentiment trends and actionable insights
# tags: [Support, Analytics, Sentiment]

name: review-sentiment
description: Analyze customer reviews to extract sentiment trends and actionable insights

default_model: sonnet
default_max_turns: 10
default_timeout: 300

steps:
  - id: parse-reviews
    prompt: >
      Parse and normalize the following batch of customer reviews. Extract
      each individual review text, the rating if available, the date, and
      any product or feature mentioned. Clean up formatting issues and
      standardize the data for downstream analysis.
      Reviews data: {input.reviews}
      Product name: {input.product_name}
    model: claude-haiku-4-5-20251001
    max_turns: 5

  - id: sentiment-analysis
    depends_on: [parse-reviews]
    prompt: >
      Perform sentiment analysis on each parsed review. Score sentiment on
      a scale from -1.0 (very negative) to 1.0 (very positive). Identify
      recurring positive themes (e.g. ease of use, good support) and negative
      themes (e.g. bugs, missing features, slow performance). Group reviews
      by sentiment tier and highlight representative quotes.
      Parsed reviews: {steps.parse-reviews.output}
    model: claude-sonnet-4-20250514
    max_turns: 10

  - id: trend-detection
    depends_on: [sentiment-analysis]
    prompt: >
      Analyze the sentiment results to detect trending topics and patterns.
      Identify recurring complaints that may indicate systemic issues,
      features receiving consistent praise, and any shifts in sentiment
      over time. Flag urgent issues that appear in multiple negative reviews
      and highlight opportunities from positive feedback patterns.
      Sentiment results: {steps.sentiment-analysis.output}
    model: claude-sonnet-4-20250514
    max_turns: 8

  - id: insights-report
    depends_on: [trend-detection]
    prompt: >
      Generate an executive insights report summarizing the review analysis.
      Include an overall sentiment score, descriptions of key charts (sentiment
      distribution, topic frequency, trend over time), the top 5 issues to
      address, top 5 strengths to promote, and specific actionable
      recommendations for the product and support teams.
      Trend analysis: {steps.trend-detection.output}
      Product name: {input.product_name}
    model: claude-sonnet-4-20250514
    max_turns: 10
`,
  "job-description-generator": `# name: Job Description Generator
# description: Generate inclusive job description with requirements, benefits, and interview plan
# tags: [HR, Recruiting, Content]

name: job-description-generator
description: Generate inclusive job description with requirements, benefits, and interview plan

default_model: sonnet
default_max_turns: 10
default_timeout: 300

steps:
  - id: analyze-role
    prompt: >
      Analyze the following role brief and extract the key details: core responsibilities,
      required and preferred skills, seniority level, team context, and reporting structure.
      Identify any implicit requirements and suggest a clear job title if not provided.
      Role brief: {input.role_brief}
    model: claude-sonnet-4-20250514
    max_turns: 5

  - id: write-jd
    depends_on: [analyze-role]
    prompt: >
      Write a complete, polished job description using inclusive language based on the role
      analysis. Include sections for About the Role, Responsibilities, Requirements (must-have
      vs nice-to-have), Benefits, and Growth Opportunities. Avoid gendered pronouns and
      unnecessary jargon. Role analysis: {steps.analyze-role.output}
    model: claude-sonnet-4-20250514
    max_turns: 10

  - id: bias-check
    depends_on: [write-jd]
    prompt: >
      Review the job description for potential bias issues. Check for gendered language,
      age-coded terms, unnecessary degree requirements, culturally exclusive phrases, and
      inflated experience requirements. Provide a corrected version with all issues fixed
      and a summary of changes made. Job description: {steps.write-jd.output}
    model: claude-sonnet-4-20250514
    max_turns: 5

  - id: interview-plan
    depends_on: [analyze-role]
    prompt: >
      Create a structured interview plan aligned with the role requirements. Include
      screening questions, technical assessment criteria, behavioral interview questions
      mapped to key competencies, and a scoring rubric. Ensure questions are legal and
      non-discriminatory. Role analysis: {steps.analyze-role.output}
    model: claude-sonnet-4-20250514
    max_turns: 5
`,
  "resume-screener": `# name: Resume Screener
# description: Screen resume against job description with match scoring and interview recommendations
# tags: [HR, Recruiting, Screening]

name: resume-screener
description: Screen resume against job description with match scoring and interview recommendations

default_model: sonnet
default_max_turns: 10
default_timeout: 300

steps:
  - id: parse-resume
    prompt: >
      Extract structured data from the following resume. Identify and organize: work
      experience (company, role, duration, achievements), technical and soft skills,
      education and certifications, notable projects, and any quantified accomplishments.
      Resume: {input.resume_text}
    model: claude-sonnet-4-20250514
    max_turns: 5
    output_schema:
      type: object
      properties:
        candidate_name:
          type: string
        experience:
          type: array
          items:
            type: object
            properties:
              company:
                type: string
              role:
                type: string
              duration:
                type: string
              achievements:
                type: array
                items:
                  type: string
        skills:
          type: array
          items:
            type: string
        education:
          type: array
          items:
            type: object
            properties:
              institution:
                type: string
              degree:
                type: string
              year:
                type: string
        total_years_experience:
          type: number

  - id: match-analysis
    depends_on: [parse-resume]
    prompt: >
      Compare the parsed resume data against the job description requirements. Score the
      overall match from 0-100, break down scoring by category (skills, experience, education),
      identify specific gaps, and highlight standout qualifications that exceed requirements.
      Parsed resume: {steps.parse-resume.output}
      Job description: {input.job_description}
    model: claude-sonnet-4-20250514
    max_turns: 5

  - id: interview-questions
    depends_on: [parse-resume, match-analysis]
    prompt: >
      Generate targeted interview questions based on the candidate's profile and identified
      gaps. Include questions to verify claimed experience, probe skill gaps, explore
      career motivations, and assess cultural fit. Prioritize questions by importance.
      Parsed resume: {steps.parse-resume.output}
      Match analysis: {steps.match-analysis.output}
    model: claude-sonnet-4-20250514
    max_turns: 5

  - id: summary-card
    depends_on: [match-analysis, interview-questions]
    prompt: >
      Create a concise candidate summary card with a clear recommendation (proceed, hold,
      or reject). Include the match score, top 3 strengths, top 3 concerns, recommended
      interview focus areas, and a brief justification for the recommendation.
      Match analysis: {steps.match-analysis.output}
      Interview questions: {steps.interview-questions.output}
    model: claude-haiku-4-5-20251001
    max_turns: 3
`,
  "contract-review": `# name: Contract Review
# description: Review contract for key terms, risks, and generate plain-language summary
# tags: [Legal, Compliance, Document]

name: contract-review
description: Review contract for key terms, risks, and generate plain-language summary

default_model: sonnet
default_max_turns: 10
default_timeout: 300

steps:
  - id: extract-terms
    prompt: >
      Extract all key terms from the following contract document. Identify: parties involved,
      effective and termination dates, core obligations for each party, payment terms and
      schedule, liability caps, indemnification clauses, intellectual property provisions,
      termination conditions, and governing law. Contract: {input.contract_text}
    model: claude-sonnet-4-20250514
    max_turns: 10
    output_schema:
      type: object
      properties:
        parties:
          type: array
          items:
            type: object
            properties:
              name:
                type: string
              role:
                type: string
        effective_date:
          type: string
        termination_date:
          type: string
        obligations:
          type: array
          items:
            type: object
            properties:
              party:
                type: string
              description:
                type: string
        payment_terms:
          type: object
          properties:
            amount:
              type: string
            schedule:
              type: string
            conditions:
              type: string
        termination_clauses:
          type: array
          items:
            type: string
        governing_law:
          type: string

  - id: risk-analysis
    depends_on: [extract-terms]
    prompt: >
      Analyze the extracted contract terms for potential risks. Identify unusual or
      one-sided clauses, missing standard protections (limitation of liability, force
      majeure, dispute resolution), auto-renewal traps, broad non-compete provisions,
      and any terms that deviate from market standards. Rate each risk as low, medium,
      or high severity. Extracted terms: {steps.extract-terms.output}
    model: claude-sonnet-4-20250514
    max_turns: 10

  - id: compliance-check
    depends_on: [extract-terms]
    prompt: >
      Check the contract terms against standard compliance requirements. Verify data
      protection and privacy provisions, regulatory compliance references, required
      insurance and bonding clauses, accessibility and non-discrimination language,
      and record-keeping obligations. Flag any missing or insufficient provisions.
      Extracted terms: {steps.extract-terms.output}
    model: claude-sonnet-4-20250514
    max_turns: 5

  - id: plain-summary
    depends_on: [extract-terms, risk-analysis, compliance-check]
    prompt: >
      Generate a plain-language summary of the contract suitable for non-legal stakeholders.
      Include what each party is agreeing to, key dates and deadlines, financial obligations,
      highlighted risks with severity levels, compliance concerns, and a prioritized list
      of action items for negotiation or clarification.
      Extracted terms: {steps.extract-terms.output}
      Risk analysis: {steps.risk-analysis.output}
      Compliance check: {steps.compliance-check.output}
    model: claude-sonnet-4-20250514
    max_turns: 10
`,
  "release-notes-generator": `# name: Release Notes Generator
# description: Generate user-facing release notes and internal changelog from commit history
# tags: [Product, Engineering, Documentation]

name: release-notes-generator
description: Generate user-facing release notes and internal changelog from commit history

default_model: sonnet
default_max_turns: 10
default_timeout: 300

steps:
  - id: parse-changes
    prompt: >
      Parse the following git diff or changelog and categorize each change into one of:
      features (new capabilities), fixes (bug corrections), improvements (enhancements to
      existing features), breaking changes (backwards-incompatible modifications), or
      internal (refactoring, dependencies, CI). For each change, extract a short summary,
      affected components, and severity. Changes: {input.changelog}
    model: claude-sonnet-4-20250514
    max_turns: 5

  - id: user-release-notes
    depends_on: [parse-changes]
    prompt: >
      Write user-facing release notes from the categorized changes. Use friendly, non-technical
      language. Lead with the most impactful features, include before/after examples where
      helpful, clearly call out breaking changes with migration steps, and close with a
      thank-you note. Format with markdown headings and bullet points.
      Categorized changes: {steps.parse-changes.output}
    model: claude-sonnet-4-20250514
    max_turns: 10

  - id: internal-changelog
    depends_on: [parse-changes]
    prompt: >
      Write a technical internal changelog for the engineering team. Include detailed
      descriptions of each change with PR/commit references, migration notes for breaking
      changes with code examples, infrastructure and dependency updates, performance
      impact notes, and known issues or follow-up tasks.
      Categorized changes: {steps.parse-changes.output}
    model: claude-sonnet-4-20250514
    max_turns: 10

  - id: social-announcement
    depends_on: [user-release-notes]
    prompt: >
      Draft a short, engaging social media announcement for this release. Highlight the
      top 2-3 user-facing improvements, keep it under 280 characters for the main post,
      include relevant hashtags, and suggest an optional longer thread format for platforms
      that support it. Release notes: {steps.user-release-notes.output}
    model: claude-haiku-4-5-20251001
    max_turns: 3
`,
  "data-extractor": `# name: Data Extractor
# description: Extract structured data from documents with validation and error handling
# tags: [Product, Data, Automation]

name: data-extractor
description: Extract structured data from documents with validation and error handling

default_model: sonnet
default_max_turns: 10
default_timeout: 300

steps:
  - id: analyze-document
    prompt: >
      Analyze the following document to detect its type (invoice, receipt, report, form,
      letter, etc.) and structure. Identify all extractable fields, their expected data
      types, and any repeating sections or tables. Note the document quality and any
      areas that may be difficult to extract accurately. Document: {input.document_text}
    model: claude-sonnet-4-20250514
    max_turns: 5

  - id: extract-data
    depends_on: [analyze-document]
    prompt: >
      Extract all identified fields from the document into a structured JSON format.
      For each field, include the extracted value, the source location in the document,
      and a confidence score (0.0-1.0). Handle missing or ambiguous fields gracefully
      by marking them as null with an explanation.
      Document analysis: {steps.analyze-document.output}
      Original document: {input.document_text}
    model: claude-sonnet-4-20250514
    max_turns: 10
    output_schema:
      type: object
      properties:
        document_type:
          type: string
        fields:
          type: array
          items:
            type: object
            properties:
              field_name:
                type: string
              value:
                type: string
              confidence:
                type: number
              source_location:
                type: string
        tables:
          type: array
          items:
            type: object
            properties:
              table_name:
                type: string
              headers:
                type: array
                items:
                  type: string
              rows:
                type: array
                items:
                  type: array
                  items:
                    type: string

  - id: validate
    depends_on: [extract-data]
    prompt: >
      Validate the extracted data for completeness and correctness. Check that all required
      fields are present, verify data format consistency (dates, numbers, currencies),
      cross-reference related fields for logical consistency (e.g., line items sum to total),
      and flag any values that appear anomalous or potentially incorrect.
      Extracted data: {steps.extract-data.output}
    model: claude-sonnet-4-20250514
    max_turns: 5

  - id: format-output
    depends_on: [validate]
    prompt: >
      Format the validated data into the final output structure. Include per-field confidence
      scores, validation status for each field, a summary of any issues found during
      validation, and an overall extraction quality score. Present the data in a clean,
      machine-readable JSON format ready for downstream processing.
      Validated data: {steps.validate.output}
      Original extraction: {steps.extract-data.output}
    model: claude-haiku-4-5-20251001
    max_turns: 3
`,
  "sales-pipeline-autopilot": `# name: Sales Pipeline Autopilot
# description: Monitor stalled deals, draft follow-ups, and alert your team on pipeline risks
# tags: [Sales, Pipeline, CRM, Automation]
# category: sales_crm

name: sales-pipeline-autopilot
description: Monitor stalled deals, draft follow-ups, and alert your team on pipeline risks

default_model: sonnet
default_max_turns: 10
default_timeout: 300
default_tools: [hubspot, slack]

input_schema:
  required: ["pipeline_name"]
  properties:
    pipeline_name:
      type: string
      description: "HubSpot pipeline name to monitor"
    stale_days:
      type: number
      description: "Days without activity before stalled (default: 7)"
      default: 7
    alert_channel:
      type: string
      description: "Slack channel for pipeline alerts"
      default: "#sales-alerts"

steps:
  - id: fetch-deals
    prompt: >
      Fetch all open deals from the HubSpot pipeline: {input.pipeline_name}.
      Return deal name, stage, amount, last activity date, and owner.
    model: haiku
    max_turns: 5

  - id: analyze-stalled
    depends_on: [fetch-deals]
    prompt: >
      Analyze deals for stalled progress. Stale threshold: {input.stale_days} days.
      For each deal determine risk level, recommended action, and follow-up angle.
      Deals: {steps.fetch-deals.output}
    model: sonnet
    max_turns: 8

  - id: draft-followups
    depends_on: [analyze-stalled]
    prompt: >
      Draft personalized follow-up emails for High/Medium risk stalled deals.
      Analysis: {steps.analyze-stalled.output}
    model: sonnet
    max_turns: 8

  - id: send-alerts
    depends_on: [analyze-stalled]
    prompt: >
      Send pipeline health summary to Slack channel {input.alert_channel}.
      Analysis: {steps.analyze-stalled.output}
    model: haiku
    max_turns: 5
    tools: [slack]
`,
  "lead-scoring": `# name: Lead Scoring
# description: Fetch leads from Salesforce, enrich with research data, score, and update CRM
# tags: [Sales, Salesforce, Lead-Gen, Scoring]
# category: sales_crm

name: lead-scoring
description: Fetch leads from Salesforce, enrich with research data, score, and update CRM

default_model: sonnet
default_max_turns: 10
default_timeout: 300
default_tools: [salesforce]

input_schema:
  required: ["lead_source"]
  properties:
    lead_source:
      type: string
      description: "Salesforce lead source filter"
    min_company_size:
      type: number
      description: "Minimum company size (employees)"
      default: 10

steps:
  - id: fetch-leads
    prompt: >
      Query Salesforce for recent leads matching source: {input.lead_source}.
      Return name, company, title, email, and creation date.
    model: haiku
    max_turns: 5

  - id: enrich-data
    depends_on: [fetch-leads]
    prompt: >
      Research and enrich leads with company data. Filter below {input.min_company_size} employees.
      Leads: {steps.fetch-leads.output}
    model: sonnet
    max_turns: 10

  - id: score-leads
    depends_on: [enrich-data]
    prompt: >
      Score each lead 1-100. Classify as Hot (80+), Warm (50-79), Cold (0-49).
      Enriched leads: {steps.enrich-data.output}
    model: sonnet
    max_turns: 8

  - id: update-crm
    depends_on: [score-leads]
    prompt: >
      Update Salesforce lead records with scores. Move Hot leads to Qualified status.
      Scored leads: {steps.score-leads.output}
    model: haiku
    max_turns: 15
`,
  "support-ticket-triage": `# name: Support Ticket Triage
# description: Fetch recent Zendesk tickets, classify by urgency, draft responses, and notify Slack
# tags: [Support, Zendesk, Triage, Automation]
# category: support

name: support-ticket-triage
description: Fetch recent Zendesk tickets, classify by urgency, draft responses, and notify Slack

default_model: sonnet
default_max_turns: 10
default_timeout: 300
default_tools: [zendesk, slack]

input_schema:
  required: ["hours_lookback"]
  properties:
    hours_lookback:
      type: number
      description: "Hours to look back for new tickets"
      default: 4
    slack_channel:
      type: string
      description: "Slack channel for triage notifications"
      default: "#support-triage"

steps:
  - id: fetch-tickets
    prompt: >
      Fetch new/open Zendesk tickets from the last {input.hours_lookback} hours.
      Return ID, subject, description, requester, and priority.
    model: haiku
    max_turns: 5

  - id: classify
    depends_on: [fetch-tickets]
    prompt: >
      Classify each ticket by urgency (Critical/High/Normal/Low), category, and sentiment.
      Tickets: {steps.fetch-tickets.output}
    model: sonnet
    max_turns: 8

  - id: draft-responses
    depends_on: [classify]
    prompt: >
      Draft initial responses for each classified ticket with appropriate tone.
      Classified tickets: {steps.classify.output}
    model: sonnet
    max_turns: 8

  - id: notify-slack
    depends_on: [classify]
    prompt: >
      Post triage summary to Slack channel {input.slack_channel}.
      Classification: {steps.classify.output}
    model: haiku
    max_turns: 5
    tools: [slack]
`,
  "customer-health-check": `# name: Customer Health Check
# description: Aggregate Salesforce account data and Zendesk tickets to assess customer health
# tags: [Support, Salesforce, Zendesk, Analytics]
# category: support

name: customer-health-check
description: Aggregate Salesforce account data and Zendesk tickets to assess customer health

default_model: sonnet
default_max_turns: 10
default_timeout: 300
default_tools: [salesforce, zendesk]

input_schema:
  required: ["account_name"]
  properties:
    account_name:
      type: string
      description: "Customer account name to analyze"

steps:
  - id: fetch-customer
    prompt: >
      Look up customer account in Salesforce: {input.account_name}.
      Return plan, ARR, renewal date, contacts, and last interaction.
    model: haiku
    max_turns: 5

  - id: fetch-tickets
    prompt: >
      Fetch Zendesk tickets for {input.account_name} from the last 90 days.
      Return ticket count, resolution times, and recurring themes.
    model: haiku
    max_turns: 5

  - id: analyze-health
    depends_on: [fetch-customer, fetch-tickets]
    prompt: >
      Calculate Health Score (1-100) based on support volume, resolution satisfaction,
      engagement, and contract signals. Classify as Healthy/At Risk/Critical.
      Account: {steps.fetch-customer.output}
      Tickets: {steps.fetch-tickets.output}
    model: sonnet
    max_turns: 8

  - id: report
    depends_on: [analyze-health]
    prompt: >
      Generate a customer health report with executive summary, metrics,
      risk factors, positive signals, and recommended actions.
      Analysis: {steps.analyze-health.output}
    model: sonnet
    max_turns: 8
`,
  "sprint-standup": `# name: Sprint Standup
# description: Synthesize Jira sprint progress and GitHub PRs into a daily standup summary
# tags: [Engineering, Jira, GitHub, Standup]
# category: engineering

name: sprint-standup
description: Synthesize Jira sprint progress and GitHub PRs into a daily standup summary for Slack

default_model: sonnet
default_max_turns: 10
default_timeout: 300
default_tools: [jira, github, slack]

input_schema:
  required: ["jira_project", "github_repo"]
  properties:
    jira_project:
      type: string
      description: "Jira project key (e.g. 'PROJ')"
    github_repo:
      type: string
      description: "GitHub repository (e.g. 'org/repo')"
    slack_channel:
      type: string
      description: "Slack channel for the standup post"
      default: "#engineering"

steps:
  - id: fetch-sprint
    prompt: >
      Fetch active sprint data for Jira project {input.jira_project}.
      Return sprint name, days remaining, completed/in-progress/blocked issues.
    model: haiku
    max_turns: 5

  - id: fetch-prs
    prompt: >
      Fetch PR activity for {input.github_repo} from the last 24 hours.
      Return merged PRs, PRs in review, and stale PRs.
    model: haiku
    max_turns: 5

  - id: synthesize
    depends_on: [fetch-sprint, fetch-prs]
    prompt: >
      Create daily standup summary from sprint and PR data.
      Sprint: {steps.fetch-sprint.output}
      PRs: {steps.fetch-prs.output}
    model: sonnet
    max_turns: 8

  - id: post-to-slack
    depends_on: [synthesize]
    prompt: >
      Post standup summary to {input.slack_channel} with Slack formatting.
      Summary: {steps.synthesize.output}
    model: haiku
    max_turns: 5
    tools: [slack]
`,
};

function getTemplateDetail(name: string) {
  const template = MOCK_TEMPLATES.find((t) => t.name === name);
  if (!template) return null;
  return {
    ...template,
    file_name: `${name}.yaml`,
    content: TEMPLATE_YAMLS[name] || `name: "${name}"\nsteps: []`,
    input_schema: (template as Record<string, unknown>).input_schema || null,
  };
}

const MOCK_WORKFLOW_VERSIONS: Record<string, unknown[]> = {
  "lead-enrichment": [
    { id: "wv-001", workflow_name: "lead-enrichment", version: 3, status: "production", description: "Improved scoring model", steps_count: 3, checksum: "abc123", created_at: h(24), promoted_at: h(12) },
    { id: "wv-002", workflow_name: "lead-enrichment", version: 2, status: "archived", description: "Added parallel enrichment", steps_count: 3, checksum: "def456", created_at: h(72), promoted_at: h(48) },
    { id: "wv-003", workflow_name: "lead-enrichment", version: 1, status: "archived", description: "Initial version", steps_count: 3, checksum: "ghi789", created_at: h(168), promoted_at: h(120) },
  ],
  "competitor-monitor": [
    { id: "wv-004", workflow_name: "competitor-monitor", version: 2, status: "production", description: "Added format-report step", steps_count: 4, checksum: "jkl012", created_at: h(48), promoted_at: h(24) },
    { id: "wv-005", workflow_name: "competitor-monitor", version: 1, status: "archived", description: "Initial version", steps_count: 3, checksum: "mno345", created_at: h(120), promoted_at: h(96) },
  ],
  "seo-audit": [
    { id: "wv-006", workflow_name: "seo-audit", version: 2, status: "staging", description: "Enhanced recommendations", steps_count: 3, checksum: "pqr678", created_at: h(12), promoted_at: h(6) },
    { id: "wv-007", workflow_name: "seo-audit", version: 1, status: "production", description: "Initial version", steps_count: 3, checksum: "stu901", created_at: h(96), promoted_at: h(72) },
  ],
};

const MOCK_RUN_COMPARE = {
  run_a: MOCK_RUNS[0],
  run_b: MOCK_RUNS[2],
  total_cost_a: 1.84,
  total_cost_b: 1.23,
  total_cost_delta: -0.61,
  total_duration_a: 180,
  total_duration_b: 360,
  total_duration_delta: 180,
  same_workflow: false,
  steps: [
    { step_id: "scrape", parallel_index: null, presence: "both", config_a: { model: "sonnet", prompt: "Scrape..." }, config_b: { model: "sonnet", prompt: "Scrape..." }, config_changed: false, output_a: { url: "https://example.com" }, output_b: { url: "https://example.com" }, output_changed: false, cost_a: 0.52, cost_b: 0.41, cost_delta: -0.11, duration_a: 12.3, duration_b: 11.8, duration_delta: -0.5, status_a: "completed", status_b: "completed", error_a: null, error_b: null },
    { step_id: "enrich", parallel_index: null, presence: "both", config_a: { model: "sonnet", prompt: "Enrich..." }, config_b: { model: "opus", prompt: "Enrich v2..." }, config_changed: true, output_a: { company: "Example Corp" }, output_b: { company: "Example Corp", extra: "data" }, output_changed: true, cost_a: 0.89, cost_b: 0.62, cost_delta: -0.27, duration_a: 18.7, duration_b: 14.2, duration_delta: -4.5, status_a: "completed", status_b: "completed", error_a: null, error_b: null },
    { step_id: "score", parallel_index: null, presence: "both", config_a: { model: "haiku" }, config_b: { model: "haiku" }, config_changed: false, output_a: { lead_score: 87 }, output_b: { lead_score: 92 }, output_changed: true, cost_a: 0.43, cost_b: 0.20, cost_delta: -0.23, duration_a: 8.2, duration_b: 6.1, duration_delta: -2.1, status_a: "completed", status_b: "completed", error_a: null, error_b: null },
  ],
};

const MOCK_SETTINGS = {
  anthropic_api_key: "****Qf8x",
  e2b_api_key: "****mN2k",
  openai_api_key: "****xK9m",
  minimax_api_key: "****pL3n",
  openrouter_api_key: "****qR7z",
  auth_required: true,
  dashboard_origin: "http://localhost:5173",
  default_max_cost_usd: 5.0,
  webhook_secret: "****tR9w",
  log_level: "info",
  max_workflow_depth: 10,
  storage_backend: "local",
  storage_bucket: "",
  storage_endpoint: "",
  data_dir: "./data",
  workflows_dir: "./workflows",
  is_local_mode: true,
  database_url: "sqlite+aiosqlite:///./data/sandcastle.db",
  redis_url: "",
};

interface MockToolConnection {
  name: string;
  tool_name: string;
  credentials_configured: string[];
  credentials_missing: string[];
  created_at: string;
}

interface MockTool {
  name: string;
  description: string;
  category: string;
  icon: string;
  configured: boolean;
  missing_credentials: string[];
  credential_env_vars: string[];
  functions: { name: string; description: string; parameters: Record<string, never> }[];
  connections: MockToolConnection[];
}

const MOCK_TOOLS: MockTool[] = [
  {
    name: "slack",
    description: "Send messages, create channels, and manage Slack workspace interactions",
    category: "communication",
    icon: "slack",
    configured: true,
    missing_credentials: [],
    credential_env_vars: ["TOOL_SLACK_BOT_TOKEN"],
    functions: [
      { name: "send_message", description: "Send a message to a Slack channel", parameters: {} },
      { name: "create_channel", description: "Create a new Slack channel", parameters: {} },
      { name: "list_channels", description: "List all Slack channels", parameters: {} },
    ],
    connections: [
      { name: "engineering", tool_name: "slack", credentials_configured: ["TOOL_SLACK_BOT_TOKEN"], credentials_missing: [], created_at: h(48) },
      { name: "support", tool_name: "slack", credentials_configured: ["TOOL_SLACK_BOT_TOKEN"], credentials_missing: [], created_at: h(24) },
    ],
  },
  {
    name: "gmail",
    description: "Send emails, manage drafts, and handle SMTP-based email delivery",
    category: "communication",
    icon: "mail",
    configured: false,
    missing_credentials: ["TOOL_SMTP_HOST", "TOOL_SMTP_PORT", "TOOL_SMTP_USER", "TOOL_SMTP_PASSWORD"],
    credential_env_vars: ["TOOL_SMTP_HOST", "TOOL_SMTP_PORT", "TOOL_SMTP_USER", "TOOL_SMTP_PASSWORD"],
    functions: [
      { name: "send_email", description: "Send an email via SMTP", parameters: {} },
      { name: "send_html_email", description: "Send an HTML-formatted email", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "teams",
    description: "Post messages to Microsoft Teams channels via incoming webhooks",
    category: "communication",
    icon: "teams",
    configured: false,
    missing_credentials: ["TOOL_TEAMS_WEBHOOK_URL"],
    credential_env_vars: ["TOOL_TEAMS_WEBHOOK_URL"],
    functions: [
      { name: "send_message", description: "Post a message to Teams channel", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "jira",
    description: "Create and manage Jira issues, search tickets, and update statuses",
    category: "project_management",
    icon: "jira",
    configured: true,
    missing_credentials: [],
    credential_env_vars: ["TOOL_JIRA_API_TOKEN", "TOOL_JIRA_BASE_URL", "TOOL_JIRA_EMAIL"],
    functions: [
      { name: "create_issue", description: "Create a new Jira issue", parameters: {} },
      { name: "search_issues", description: "Search Jira issues with JQL", parameters: {} },
      { name: "update_issue", description: "Update an existing Jira issue", parameters: {} },
      { name: "get_issue", description: "Get details of a Jira issue", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "github",
    description: "Manage GitHub repositories, issues, pull requests, and code reviews",
    category: "project_management",
    icon: "github",
    configured: true,
    missing_credentials: [],
    credential_env_vars: ["TOOL_GITHUB_TOKEN"],
    functions: [
      { name: "create_issue", description: "Create a new GitHub issue", parameters: {} },
      { name: "create_pull_request", description: "Create a pull request", parameters: {} },
      { name: "list_repos", description: "List repositories", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "notion",
    description: "Create pages, query databases, and manage content in Notion workspaces",
    category: "project_management",
    icon: "notion",
    configured: false,
    missing_credentials: ["TOOL_NOTION_API_KEY"],
    credential_env_vars: ["TOOL_NOTION_API_KEY"],
    functions: [
      { name: "create_page", description: "Create a new Notion page", parameters: {} },
      { name: "query_database", description: "Query a Notion database", parameters: {} },
      { name: "update_page", description: "Update an existing Notion page", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "hubspot",
    description: "Manage contacts, deals, and CRM pipelines in HubSpot",
    category: "crm",
    icon: "hubspot",
    configured: false,
    missing_credentials: ["TOOL_HUBSPOT_API_KEY"],
    credential_env_vars: ["TOOL_HUBSPOT_API_KEY"],
    functions: [
      { name: "create_contact", description: "Create a new HubSpot contact", parameters: {} },
      { name: "create_deal", description: "Create a new deal", parameters: {} },
      { name: "search_contacts", description: "Search contacts", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "salesforce",
    description: "Interact with Salesforce CRM - manage leads, opportunities, and accounts",
    category: "crm",
    icon: "salesforce",
    configured: false,
    missing_credentials: ["TOOL_SALESFORCE_CLIENT_ID", "TOOL_SALESFORCE_CLIENT_SECRET", "TOOL_SALESFORCE_REFRESH_TOKEN", "TOOL_SALESFORCE_INSTANCE_URL"],
    credential_env_vars: ["TOOL_SALESFORCE_CLIENT_ID", "TOOL_SALESFORCE_CLIENT_SECRET", "TOOL_SALESFORCE_REFRESH_TOKEN", "TOOL_SALESFORCE_INSTANCE_URL"],
    functions: [
      { name: "query", description: "Execute a SOQL query", parameters: {} },
      { name: "create_record", description: "Create a Salesforce record", parameters: {} },
      { name: "update_record", description: "Update a Salesforce record", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "zendesk",
    description: "Create and manage support tickets, search knowledge base in Zendesk",
    category: "crm",
    icon: "zendesk",
    configured: false,
    missing_credentials: ["TOOL_ZENDESK_SUBDOMAIN", "TOOL_ZENDESK_EMAIL", "TOOL_ZENDESK_API_TOKEN"],
    credential_env_vars: ["TOOL_ZENDESK_SUBDOMAIN", "TOOL_ZENDESK_EMAIL", "TOOL_ZENDESK_API_TOKEN"],
    functions: [
      { name: "create_ticket", description: "Create a new support ticket", parameters: {} },
      { name: "search_tickets", description: "Search support tickets", parameters: {} },
      { name: "update_ticket", description: "Update a support ticket", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "gdrive",
    description: "Upload, download, and manage files in Google Drive",
    category: "data",
    icon: "gdrive",
    configured: false,
    missing_credentials: ["TOOL_GOOGLE_SERVICE_ACCOUNT"],
    credential_env_vars: ["TOOL_GOOGLE_SERVICE_ACCOUNT"],
    functions: [
      { name: "upload_file", description: "Upload a file to Google Drive", parameters: {} },
      { name: "download_file", description: "Download a file from Google Drive", parameters: {} },
      { name: "list_files", description: "List files in a folder", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "postgresql",
    description: "Execute queries and manage data in PostgreSQL databases",
    category: "data",
    icon: "database",
    configured: true,
    missing_credentials: [],
    credential_env_vars: ["TOOL_POSTGRESQL_URL"],
    functions: [
      { name: "query", description: "Execute a SQL query", parameters: {} },
      { name: "execute", description: "Execute a SQL statement", parameters: {} },
    ],
    connections: [
      { name: "analytics", tool_name: "postgresql", credentials_configured: ["TOOL_POSTGRESQL_URL"], credentials_missing: [], created_at: h(72) },
      { name: "staging", tool_name: "postgresql", credentials_configured: [], credentials_missing: ["TOOL_POSTGRESQL_URL"], created_at: h(48) },
    ],
  },
  {
    name: "webhook",
    description: "Send HTTP requests to external webhooks and APIs",
    category: "general",
    icon: "webhook",
    configured: true,
    missing_credentials: [],
    credential_env_vars: [],
    functions: [
      { name: "send", description: "Send an HTTP request to a webhook URL", parameters: {} },
      { name: "send_batch", description: "Send multiple webhook requests", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "sap",
    description: "Search business partners, manage sales orders, and query materials in SAP S/4HANA",
    category: "erp",
    icon: "sap",
    configured: false,
    missing_credentials: ["TOOL_SAP_BASE_URL", "TOOL_SAP_API_KEY"],
    credential_env_vars: ["TOOL_SAP_BASE_URL", "TOOL_SAP_API_KEY"],
    functions: [
      { name: "get_business_partners", description: "Search business partners", parameters: {} },
      { name: "get_sales_orders", description: "List sales orders", parameters: {} },
      { name: "create_sales_order", description: "Create a new sales order", parameters: {} },
      { name: "get_material", description: "Get material/product details", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "servicenow",
    description: "Create, search, and manage incidents and change requests in ServiceNow",
    category: "project_management",
    icon: "servicenow",
    configured: false,
    missing_credentials: ["TOOL_SERVICENOW_INSTANCE", "TOOL_SERVICENOW_USERNAME", "TOOL_SERVICENOW_PASSWORD"],
    credential_env_vars: ["TOOL_SERVICENOW_INSTANCE", "TOOL_SERVICENOW_USERNAME", "TOOL_SERVICENOW_PASSWORD"],
    functions: [
      { name: "get_incidents", description: "Search incidents", parameters: {} },
      { name: "create_incident", description: "Create a new incident", parameters: {} },
      { name: "update_incident", description: "Update an existing incident", parameters: {} },
      { name: "get_change_requests", description: "List change requests", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "snowflake",
    description: "Execute SQL queries and explore schemas in Snowflake data warehouse",
    category: "data",
    icon: "snowflake",
    configured: false,
    missing_credentials: ["TOOL_SNOWFLAKE_ACCOUNT", "TOOL_SNOWFLAKE_USERNAME", "TOOL_SNOWFLAKE_PASSWORD", "TOOL_SNOWFLAKE_WAREHOUSE"],
    credential_env_vars: ["TOOL_SNOWFLAKE_ACCOUNT", "TOOL_SNOWFLAKE_USERNAME", "TOOL_SNOWFLAKE_PASSWORD", "TOOL_SNOWFLAKE_WAREHOUSE"],
    functions: [
      { name: "execute_query", description: "Run a SQL query", parameters: {} },
      { name: "list_databases", description: "List available databases", parameters: {} },
      { name: "list_tables", description: "List tables in a schema", parameters: {} },
      { name: "describe_table", description: "Get table column definitions", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "mongodb",
    description: "Query, insert, update, and aggregate documents in MongoDB via Atlas Data API",
    category: "data",
    icon: "mongodb",
    configured: false,
    missing_credentials: ["TOOL_MONGODB_URI", "TOOL_MONGODB_API_KEY"],
    credential_env_vars: ["TOOL_MONGODB_URI", "TOOL_MONGODB_API_KEY"],
    functions: [
      { name: "find_documents", description: "Query documents from a collection", parameters: {} },
      { name: "insert_document", description: "Insert a document", parameters: {} },
      { name: "update_document", description: "Update documents matching a filter", parameters: {} },
      { name: "aggregate", description: "Run an aggregation pipeline", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "stripe",
    description: "Manage customers, invoices, and subscriptions in Stripe",
    category: "payments",
    icon: "stripe",
    configured: false,
    missing_credentials: ["TOOL_STRIPE_SECRET_KEY"],
    credential_env_vars: ["TOOL_STRIPE_SECRET_KEY"],
    functions: [
      { name: "list_customers", description: "Search or list customers", parameters: {} },
      { name: "get_customer", description: "Get customer details", parameters: {} },
      { name: "list_invoices", description: "List invoices", parameters: {} },
      { name: "create_invoice", description: "Create a draft invoice", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "twilio",
    description: "Send SMS, make calls, and manage messaging via Twilio",
    category: "communication",
    icon: "twilio",
    configured: false,
    missing_credentials: ["TOOL_TWILIO_ACCOUNT_SID", "TOOL_TWILIO_AUTH_TOKEN", "TOOL_TWILIO_FROM_NUMBER"],
    credential_env_vars: ["TOOL_TWILIO_ACCOUNT_SID", "TOOL_TWILIO_AUTH_TOKEN", "TOOL_TWILIO_FROM_NUMBER"],
    functions: [
      { name: "send_sms", description: "Send an SMS message", parameters: {} },
      { name: "list_messages", description: "List message history", parameters: {} },
      { name: "get_message", description: "Get message details", parameters: {} },
      { name: "make_call", description: "Initiate a voice call", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "sendgrid",
    description: "Send transactional emails and manage marketing contacts via SendGrid",
    category: "communication",
    icon: "sendgrid",
    configured: false,
    missing_credentials: ["TOOL_SENDGRID_API_KEY", "TOOL_SENDGRID_FROM_EMAIL"],
    credential_env_vars: ["TOOL_SENDGRID_API_KEY", "TOOL_SENDGRID_FROM_EMAIL"],
    functions: [
      { name: "send_email", description: "Send a transactional email", parameters: {} },
      { name: "list_contacts", description: "Search marketing contacts", parameters: {} },
      { name: "add_contacts", description: "Add or update contacts", parameters: {} },
      { name: "get_stats", description: "Get email delivery statistics", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "intercom",
    description: "Search contacts, manage conversations, and send replies via Intercom",
    category: "communication",
    icon: "intercom",
    configured: false,
    missing_credentials: ["TOOL_INTERCOM_ACCESS_TOKEN"],
    credential_env_vars: ["TOOL_INTERCOM_ACCESS_TOKEN"],
    functions: [
      { name: "search_contacts", description: "Search users and leads", parameters: {} },
      { name: "create_conversation", description: "Start a new conversation", parameters: {} },
      { name: "reply_conversation", description: "Reply to a conversation", parameters: {} },
      { name: "list_conversations", description: "List conversations", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "google-sheets",
    description: "Read, write, and manage Google Sheets spreadsheets",
    category: "data",
    icon: "google-sheets",
    configured: false,
    missing_credentials: ["TOOL_GOOGLE_SERVICE_ACCOUNT"],
    credential_env_vars: ["TOOL_GOOGLE_SERVICE_ACCOUNT"],
    functions: [
      { name: "read_sheet", description: "Read data from a spreadsheet", parameters: {} },
      { name: "write_sheet", description: "Write data to a spreadsheet", parameters: {} },
      { name: "create_sheet", description: "Create a new spreadsheet", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "airtable",
    description: "Create records, list views, and manage Airtable bases",
    category: "data",
    icon: "airtable",
    configured: false,
    missing_credentials: ["TOOL_AIRTABLE_API_KEY"],
    credential_env_vars: ["TOOL_AIRTABLE_API_KEY"],
    functions: [
      { name: "list_records", description: "List records from a table", parameters: {} },
      { name: "create_record", description: "Create a new record", parameters: {} },
      { name: "update_record", description: "Update an existing record", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "linear",
    description: "Create issues, manage projects, and track bugs in Linear",
    category: "project_management",
    icon: "linear",
    configured: false,
    missing_credentials: ["TOOL_LINEAR_API_KEY"],
    credential_env_vars: ["TOOL_LINEAR_API_KEY"],
    functions: [
      { name: "create_issue", description: "Create a new issue", parameters: {} },
      { name: "list_issues", description: "List issues with filters", parameters: {} },
      { name: "update_issue", description: "Update an existing issue", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "discord",
    description: "Send messages, manage channels, and interact with Discord servers",
    category: "communication",
    icon: "discord",
    configured: false,
    missing_credentials: ["TOOL_DISCORD_BOT_TOKEN"],
    credential_env_vars: ["TOOL_DISCORD_BOT_TOKEN"],
    functions: [
      { name: "send_message", description: "Send a message to a channel", parameters: {} },
      { name: "create_channel", description: "Create a new channel", parameters: {} },
      { name: "list_channels", description: "List server channels", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "openai",
    description: "Generate text, embeddings, and moderate content via OpenAI API",
    category: "ai",
    icon: "openai",
    configured: false,
    missing_credentials: ["TOOL_OPENAI_API_KEY"],
    credential_env_vars: ["TOOL_OPENAI_API_KEY"],
    functions: [
      { name: "chat_completion", description: "Generate a chat completion", parameters: {} },
      { name: "create_embedding", description: "Create text embeddings", parameters: {} },
      { name: "moderate", description: "Moderate content for policy violations", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "anthropic",
    description: "Generate text responses via Anthropic Claude API",
    category: "ai",
    icon: "anthropic",
    configured: false,
    missing_credentials: ["TOOL_ANTHROPIC_API_KEY"],
    credential_env_vars: ["TOOL_ANTHROPIC_API_KEY"],
    functions: [
      { name: "create_message", description: "Create a message completion", parameters: {} },
      { name: "count_tokens", description: "Count tokens in a message", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "aws-s3",
    description: "Upload, download, and manage files in Amazon S3 buckets",
    category: "data",
    icon: "aws-s3",
    configured: false,
    missing_credentials: ["TOOL_AWS_ACCESS_KEY_ID", "TOOL_AWS_SECRET_ACCESS_KEY", "TOOL_AWS_S3_BUCKET"],
    credential_env_vars: ["TOOL_AWS_ACCESS_KEY_ID", "TOOL_AWS_SECRET_ACCESS_KEY", "TOOL_AWS_S3_BUCKET"],
    functions: [
      { name: "put_object", description: "Upload an object to a bucket", parameters: {} },
      { name: "get_object", description: "Download an object from a bucket", parameters: {} },
      { name: "list_objects", description: "List objects in a bucket", parameters: {} },
      { name: "delete_object", description: "Delete an object from a bucket", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "redis",
    description: "Get, set, and manage key-value data in Redis",
    category: "data",
    icon: "redis",
    configured: false,
    missing_credentials: ["TOOL_REDIS_URL"],
    credential_env_vars: ["TOOL_REDIS_URL"],
    functions: [
      { name: "get", description: "Get a value by key", parameters: {} },
      { name: "set", description: "Set a key-value pair", parameters: {} },
      { name: "del", description: "Delete a key", parameters: {} },
      { name: "keys", description: "List keys matching a pattern", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "supabase",
    description: "Query tables, insert rows, and manage data in Supabase",
    category: "data",
    icon: "supabase",
    configured: false,
    missing_credentials: ["TOOL_SUPABASE_URL", "TOOL_SUPABASE_ANON_KEY"],
    credential_env_vars: ["TOOL_SUPABASE_URL", "TOOL_SUPABASE_ANON_KEY"],
    functions: [
      { name: "select", description: "Query rows from a table", parameters: {} },
      { name: "insert", description: "Insert rows into a table", parameters: {} },
      { name: "update", description: "Update rows in a table", parameters: {} },
      { name: "rpc", description: "Call a Postgres function", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "pinecone",
    description: "Upsert and query vector embeddings in Pinecone",
    category: "ai",
    icon: "pinecone",
    configured: false,
    missing_credentials: ["TOOL_PINECONE_API_KEY", "TOOL_PINECONE_INDEX"],
    credential_env_vars: ["TOOL_PINECONE_API_KEY", "TOOL_PINECONE_INDEX"],
    functions: [
      { name: "upsert", description: "Upsert vectors into an index", parameters: {} },
      { name: "query", description: "Query vectors by similarity", parameters: {} },
      { name: "delete_vectors", description: "Delete vectors from an index", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "resend",
    description: "Send transactional emails via Resend API",
    category: "communication",
    icon: "resend",
    configured: false,
    missing_credentials: ["TOOL_RESEND_API_KEY"],
    credential_env_vars: ["TOOL_RESEND_API_KEY"],
    functions: [
      { name: "send_email", description: "Send a transactional email", parameters: {} },
      { name: "list_emails", description: "List sent emails", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "vercel",
    description: "Manage deployments and projects on Vercel",
    category: "devops",
    icon: "vercel",
    configured: false,
    missing_credentials: ["TOOL_VERCEL_TOKEN"],
    credential_env_vars: ["TOOL_VERCEL_TOKEN"],
    functions: [
      { name: "list_deployments", description: "List recent deployments", parameters: {} },
      { name: "create_deployment", description: "Create a new deployment", parameters: {} },
      { name: "list_projects", description: "List all projects", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "cloudflare-workers",
    description: "Deploy and manage Cloudflare Workers scripts",
    category: "devops",
    icon: "cloudflare-workers",
    configured: false,
    missing_credentials: ["TOOL_CF_API_TOKEN", "TOOL_CF_ACCOUNT_ID"],
    credential_env_vars: ["TOOL_CF_API_TOKEN", "TOOL_CF_ACCOUNT_ID"],
    functions: [
      { name: "list_workers", description: "List deployed workers", parameters: {} },
      { name: "deploy_worker", description: "Deploy a worker script", parameters: {} },
      { name: "delete_worker", description: "Delete a worker", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "firecrawl",
    description: "Scrape web pages and crawl websites for structured data",
    category: "ai",
    icon: "firecrawl",
    configured: false,
    missing_credentials: ["TOOL_FIRECRAWL_API_KEY"],
    credential_env_vars: ["TOOL_FIRECRAWL_API_KEY"],
    functions: [
      { name: "scrape", description: "Scrape a single web page", parameters: {} },
      { name: "crawl", description: "Crawl a website recursively", parameters: {} },
      { name: "check_crawl", description: "Check crawl job status", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "tavily",
    description: "Search the web for AI-optimized results",
    category: "ai",
    icon: "tavily",
    configured: false,
    missing_credentials: ["TOOL_TAVILY_API_KEY"],
    credential_env_vars: ["TOOL_TAVILY_API_KEY"],
    functions: [
      { name: "search", description: "Search the web", parameters: {} },
      { name: "extract", description: "Extract content from URLs", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "elevenlabs",
    description: "Generate speech audio from text via ElevenLabs",
    category: "ai",
    icon: "elevenlabs",
    configured: false,
    missing_credentials: ["TOOL_ELEVENLABS_API_KEY"],
    credential_env_vars: ["TOOL_ELEVENLABS_API_KEY"],
    functions: [
      { name: "text_to_speech", description: "Convert text to speech audio", parameters: {} },
      { name: "list_voices", description: "List available voices", parameters: {} },
      { name: "get_voice", description: "Get voice details", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "zapier",
    description: "Trigger Zapier webhooks and manage automation workflows",
    category: "general",
    icon: "zapier",
    configured: false,
    missing_credentials: ["TOOL_ZAPIER_NLA_API_KEY"],
    credential_env_vars: ["TOOL_ZAPIER_NLA_API_KEY"],
    functions: [
      { name: "trigger_webhook", description: "Trigger a Zapier webhook", parameters: {} },
      { name: "list_actions", description: "List available actions", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "shopify",
    description: "Manage products, orders, and customers in Shopify stores",
    category: "erp",
    icon: "shopify",
    configured: false,
    missing_credentials: ["TOOL_SHOPIFY_STORE", "TOOL_SHOPIFY_ACCESS_TOKEN"],
    credential_env_vars: ["TOOL_SHOPIFY_STORE", "TOOL_SHOPIFY_ACCESS_TOKEN"],
    functions: [
      { name: "list_products", description: "List store products", parameters: {} },
      { name: "create_product", description: "Create a new product", parameters: {} },
      { name: "list_orders", description: "List store orders", parameters: {} },
      { name: "get_order", description: "Get order details", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "quickbooks",
    description: "Create invoices, manage payments, and query QuickBooks Online",
    category: "erp",
    icon: "quickbooks",
    configured: false,
    missing_credentials: ["TOOL_QUICKBOOKS_CLIENT_ID", "TOOL_QUICKBOOKS_CLIENT_SECRET", "TOOL_QUICKBOOKS_REFRESH_TOKEN", "TOOL_QUICKBOOKS_REALM_ID"],
    credential_env_vars: ["TOOL_QUICKBOOKS_CLIENT_ID", "TOOL_QUICKBOOKS_CLIENT_SECRET", "TOOL_QUICKBOOKS_REFRESH_TOKEN", "TOOL_QUICKBOOKS_REALM_ID"],
    functions: [
      { name: "query", description: "Query entities with SQL-like syntax", parameters: {} },
      { name: "create_invoice", description: "Create a new invoice", parameters: {} },
      { name: "get_invoice", description: "Get invoice details", parameters: {} },
      { name: "create_payment", description: "Record a payment", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "helios",
    description: "Helios iNuvio (Asseco) - invoices, orders, contacts, and stock queries via REST API",
    category: "erp",
    icon: "helios",
    configured: true,
    missing_credentials: [],
    credential_env_vars: ["TOOL_HELIOS_BASE_URL", "TOOL_HELIOS_API_KEY"],
    functions: [
      { name: "get_contacts", description: "Search contacts (business partners)", parameters: {} },
      { name: "get_invoices", description: "List issued invoices", parameters: {} },
      { name: "create_invoice", description: "Create a new issued invoice", parameters: {} },
      { name: "get_orders", description: "List sales orders", parameters: {} },
      { name: "get_stock", description: "Query warehouse stock levels", parameters: {} },
    ],
    connections: [
      { name: "production", tool_name: "helios", credentials_configured: ["TOOL_HELIOS_BASE_URL", "TOOL_HELIOS_API_KEY"], credentials_missing: [], created_at: h(72) },
    ],
  },
  {
    name: "abra",
    description: "ABRA Gen (ABRA Software) - business partners, invoices, orders, and warehouse management",
    category: "erp",
    icon: "abra",
    configured: false,
    missing_credentials: ["TOOL_ABRA_BASE_URL", "TOOL_ABRA_USERNAME", "TOOL_ABRA_PASSWORD"],
    credential_env_vars: ["TOOL_ABRA_BASE_URL", "TOOL_ABRA_USERNAME", "TOOL_ABRA_PASSWORD"],
    functions: [
      { name: "get_firms", description: "Search business partners (firms)", parameters: {} },
      { name: "get_issued_invoices", description: "List issued invoices", parameters: {} },
      { name: "create_issued_invoice", description: "Create a new issued invoice", parameters: {} },
      { name: "get_orders", description: "List sales orders", parameters: {} },
      { name: "get_store_cards", description: "Query warehouse store cards", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "calendly",
    description: "List events, create scheduling links in Calendly",
    category: "general",
    icon: "calendly",
    configured: false,
    missing_credentials: ["TOOL_CALENDLY_API_KEY"],
    credential_env_vars: ["TOOL_CALENDLY_API_KEY"],
    functions: [
      { name: "list_events", description: "List scheduled events", parameters: {} },
      { name: "get_event", description: "Get event details", parameters: {} },
      { name: "list_event_types", description: "List available event types", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "whatsapp",
    description: "Send messages and manage WhatsApp Business conversations",
    category: "communication",
    icon: "whatsapp",
    configured: false,
    missing_credentials: ["TOOL_WHATSAPP_TOKEN", "TOOL_WHATSAPP_PHONE_ID"],
    credential_env_vars: ["TOOL_WHATSAPP_TOKEN", "TOOL_WHATSAPP_PHONE_ID"],
    functions: [
      { name: "send_message", description: "Send a text message", parameters: {} },
      { name: "send_template", description: "Send a template message", parameters: {} },
      { name: "get_messages", description: "Get conversation messages", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "figma",
    description: "Read files, export images, and inspect components in Figma",
    category: "general",
    icon: "figma",
    configured: false,
    missing_credentials: ["TOOL_FIGMA_ACCESS_TOKEN"],
    credential_env_vars: ["TOOL_FIGMA_ACCESS_TOKEN"],
    functions: [
      { name: "get_file", description: "Get a Figma file", parameters: {} },
      { name: "get_images", description: "Export images from a file", parameters: {} },
      { name: "get_comments", description: "Get file comments", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "datadog",
    description: "Query metrics, list monitors, and manage alerts in Datadog",
    category: "devops",
    icon: "datadog",
    configured: false,
    missing_credentials: ["TOOL_DATADOG_API_KEY", "TOOL_DATADOG_APP_KEY"],
    credential_env_vars: ["TOOL_DATADOG_API_KEY", "TOOL_DATADOG_APP_KEY"],
    functions: [
      { name: "query_metrics", description: "Query time-series metrics", parameters: {} },
      { name: "list_monitors", description: "List configured monitors", parameters: {} },
      { name: "create_monitor", description: "Create a new monitor", parameters: {} },
      { name: "trigger_event", description: "Send a custom event", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "plaid",
    description: "Access bank accounts, transactions, and balances via Plaid",
    category: "payments",
    icon: "plaid",
    configured: false,
    missing_credentials: ["TOOL_PLAID_CLIENT_ID", "TOOL_PLAID_SECRET"],
    credential_env_vars: ["TOOL_PLAID_CLIENT_ID", "TOOL_PLAID_SECRET"],
    functions: [
      { name: "get_accounts", description: "Get linked bank accounts", parameters: {} },
      { name: "get_transactions", description: "Get account transactions", parameters: {} },
      { name: "get_balance", description: "Get account balances", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "docusign",
    description: "Send envelopes for e-signatures via DocuSign",
    category: "erp",
    icon: "docusign",
    configured: false,
    missing_credentials: ["TOOL_DOCUSIGN_ACCESS_TOKEN", "TOOL_DOCUSIGN_ACCOUNT_ID"],
    credential_env_vars: ["TOOL_DOCUSIGN_ACCESS_TOKEN", "TOOL_DOCUSIGN_ACCOUNT_ID"],
    functions: [
      { name: "create_envelope", description: "Create and send an envelope", parameters: {} },
      { name: "get_envelope", description: "Get envelope status", parameters: {} },
      { name: "list_envelopes", description: "List envelopes", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "pagerduty",
    description: "Create and manage incidents and on-call schedules in PagerDuty",
    category: "devops",
    icon: "pagerduty",
    configured: false,
    missing_credentials: ["TOOL_PAGERDUTY_API_KEY"],
    credential_env_vars: ["TOOL_PAGERDUTY_API_KEY"],
    functions: [
      { name: "create_incident", description: "Create a new incident", parameters: {} },
      { name: "list_incidents", description: "List incidents", parameters: {} },
      { name: "acknowledge_incident", description: "Acknowledge an incident", parameters: {} },
      { name: "resolve_incident", description: "Resolve an incident", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "mcp-bridge",
    description: "Bridge to external MCP servers and proxy tool calls",
    category: "general",
    icon: "mcp-bridge",
    configured: false,
    missing_credentials: ["TOOL_MCP_SERVER_URL"],
    credential_env_vars: ["TOOL_MCP_SERVER_URL"],
    functions: [
      { name: "list_tools", description: "List tools from the MCP server", parameters: {} },
      { name: "call_tool", description: "Call a tool on the MCP server", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "human-input",
    description: "Request human input, approval, or file uploads during workflow execution",
    category: "general",
    icon: "human-input",
    configured: true,
    missing_credentials: [],
    credential_env_vars: [],
    functions: [
      { name: "ask_question", description: "Ask the user a question", parameters: {} },
      { name: "request_approval", description: "Request user approval", parameters: {} },
      { name: "request_file", description: "Request a file upload", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "filesystem",
    description: "Read, write, and manage files within the sandbox",
    category: "general",
    icon: "filesystem",
    configured: true,
    missing_credentials: [],
    credential_env_vars: [],
    functions: [
      { name: "read_file", description: "Read a file", parameters: {} },
      { name: "write_file", description: "Write content to a file", parameters: {} },
      { name: "list_directory", description: "List directory contents", parameters: {} },
      { name: "delete_file", description: "Delete a file", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "shell",
    description: "Execute shell commands in the sandbox environment",
    category: "general",
    icon: "shell",
    configured: true,
    missing_credentials: [],
    credential_env_vars: [],
    functions: [
      { name: "execute", description: "Execute a shell command", parameters: {} },
      { name: "execute_background", description: "Execute a command in the background", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "python-runtime",
    description: "Execute Python scripts and install packages in the sandbox",
    category: "general",
    icon: "python-runtime",
    configured: true,
    missing_credentials: [],
    credential_env_vars: [],
    functions: [
      { name: "execute", description: "Execute Python code", parameters: {} },
      { name: "install_package", description: "Install a Python package", parameters: {} },
      { name: "execute_file", description: "Execute a Python file", parameters: {} },
    ],
    connections: [],
  },
  {
    name: "code-interpreter",
    description: "Run code, analyze data, and generate visualizations",
    category: "general",
    icon: "code-interpreter",
    configured: true,
    missing_credentials: [],
    credential_env_vars: [],
    functions: [
      { name: "execute", description: "Execute code", parameters: {} },
      { name: "install_packages", description: "Install packages", parameters: {} },
      { name: "upload_file", description: "Upload a file", parameters: {} },
      { name: "download_file", description: "Download a file", parameters: {} },
    ],
    connections: [],
  },
];

// Community hub mock data
const MOCK_COMMUNITY_TEMPLATES = [
  {
    slug: "lena-content/ad-copy-generator",
    name: "Ad Copy Generator",
    description: "Generate ad copy variants for Google Ads and Meta Ads campaigns",
    author: "lena-content",
    tags: ["Marketing", "Advertising", "Copywriting"],
    step_count: 4,
    category: "marketing",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/ad_copy_generator.yaml",
    stars: 254,
    forks: 1,
    downloads: 407,
    featured: true,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "tomr-eng/api-docs-generator",
    name: "API Docs Generator",
    description: "Generate comprehensive API documentation from code repositories or OpenAPI specs",
    author: "tomr-eng",
    tags: ["Engineering", "GitHub", "Documentation", "API"],
    step_count: 4,
    category: "engineering",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/api_docs_generator.yaml",
    stars: 95,
    forks: 4,
    downloads: 92,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "elena-growth/blog-to-social",
    name: "Blog to Social Media",
    description: "Transform a blog post into platform-specific social media content",
    author: "elena-growth",
    tags: ["Marketing", "Content", "Social"],
    step_count: 5,
    category: "marketing",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/blog_to_social.yaml",
    stars: 153,
    forks: 3,
    downloads: 205,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "workflow-lab/chain-of-thought",
    name: "Chain of Thought Solver",
    description: "Decomposes a problem into sub-problems, solves each one, and synthesizes a final answer",
    author: "workflow-lab",
    tags: ["reasoning", "chain-of-thought", "problem-solving"],
    step_count: 3,
    category: "general_ai",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/chain_of_thought.yaml",
    stars: 100,
    forks: 1,
    downloads: 151,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "content-queen/competitor-analysis",
    name: "Competitor Analysis",
    description: "Analyze competitor positioning, strengths, weaknesses, and opportunities",
    author: "content-queen",
    tags: ["Marketing", "Strategy", "Research"],
    step_count: 4,
    category: "marketing",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/competitor_analysis.yaml",
    stars: 335,
    forks: 8,
    downloads: 426,
    featured: true,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "marcus-hr/compliance-checker",
    name: "Compliance Checker",
    description: "Review documents for regulatory compliance against GDPR, SOC2, HIPAA, or custom frameworks",
    author: "marcus-hr",
    tags: ["Legal", "Compliance", "GDPR", "SOC2", "Audit"],
    step_count: 4,
    category: "hr_legal",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/compliance_checker.yaml",
    stars: 134,
    forks: 6,
    downloads: 124,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "gizmax/contract-review",
    name: "Contract Review",
    description: "Review contract for key terms, risks, and generate plain-language summary",
    author: "gizmax",
    tags: ["Legal", "Compliance", "Document"],
    step_count: 4,
    category: "hr_legal",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/contract_review.yaml",
    stars: 57,
    forks: 0,
    downloads: 96,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "jmartinez/crm-enrichment",
    name: "CRM Contact Enrichment",
    description: "Enrich HubSpot contacts with research data and create follow-up deals",
    author: "jmartinez",
    tags: ["CRM", "HubSpot", "Sales", "Research"],
    step_count: 4,
    category: "sales_crm",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/crm_enrichment.yaml",
    stars: 106,
    forks: 3,
    downloads: 127,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "gizmax/customer-churn-predictor",
    name: "Customer Churn Predictor",
    description: "Analyze customer signals to predict churn risk, generate retention actions, and alert sales team",
    author: "gizmax",
    tags: ["Sales", "Salesforce", "Churn", "Analytics", "Retention"],
    step_count: 5,
    category: "sales_crm",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/customer_churn_predictor.yaml",
    stars: 199,
    forks: 8,
    downloads: 199,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "supportflow/customer-health-check",
    name: "Customer Health Check",
    description: "Aggregate Salesforce account data and Zendesk tickets to assess customer health",
    author: "supportflow",
    tags: ["Support", "Salesforce", "Zendesk", "Analytics"],
    step_count: 4,
    category: "support",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/customer_health_check.yaml",
    stars: 232,
    forks: 0,
    downloads: 388,
    featured: true,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "tomr-eng/data-extractor",
    name: "Data Extractor",
    description: "Extract structured data from documents with validation and error handling",
    author: "tomr-eng",
    tags: ["Product", "Data", "Automation"],
    step_count: 4,
    category: "engineering",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/data_extractor.yaml",
    stars: 250,
    forks: 3,
    downloads: 367,
    featured: true,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "content-queen/email-campaign",
    name: "Email Campaign Generator",
    description: "Generate email campaign with subject line variants and A/B copy",
    author: "content-queen",
    tags: ["Marketing", "Email", "Campaign"],
    step_count: 5,
    category: "marketing",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/email_campaign.yaml",
    stars: 347,
    forks: 8,
    downloads: 446,
    featured: true,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "nadia-cx/faq-generator",
    name: "FAQ Generator",
    description: "Analyze resolved support tickets to auto-generate FAQ entries and publish to Notion",
    author: "nadia-cx",
    tags: ["Support", "Zendesk", "Notion", "Knowledge-Base"],
    step_count: 4,
    category: "support",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/faq_generator.yaml",
    stars: 206,
    forks: 3,
    downloads: 294,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "tomr-eng/jira-triage",
    name: "Jira Issue Triage",
    description: "Auto-triage new Jira issues with priority, labels, and assignment suggestions",
    author: "tomr-eng",
    tags: ["Project Management", "Jira", "Triage"],
    step_count: 3,
    category: "engineering",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/jira_triage.yaml",
    stars: 225,
    forks: 4,
    downloads: 309,
    featured: true,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "marcus-hr/job-description",
    name: "Job Description Generator",
    description: "Generate inclusive job description with requirements, benefits, and interview plan",
    author: "marcus-hr",
    tags: ["HR", "Recruiting", "Content"],
    step_count: 4,
    category: "hr_legal",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/job_description.yaml",
    stars: 296,
    forks: 0,
    downloads: 494,
    featured: true,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "gizmax/lead-enrichment",
    name: "Lead Enrichment",
    description: "Research and enrich lead data with company info, scoring, and outreach angles",
    author: "gizmax",
    tags: ["Sales", "Research", "Lead-Gen"],
    step_count: 5,
    category: "sales_crm",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/lead_enrichment.yaml",
    stars: 300,
    forks: 2,
    downloads: 468,
    featured: true,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "jmartinez/lead-scoring",
    name: "Lead Scoring",
    description: "Fetch leads from Salesforce, enrich with research data, score, and update CRM",
    author: "jmartinez",
    tags: ["Sales", "Salesforce", "Lead-Gen", "Scoring"],
    step_count: 4,
    category: "sales_crm",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/lead_scoring.yaml",
    stars: 322,
    forks: 6,
    downloads: 437,
    featured: true,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "revops-sam/meeting-recap",
    name: "Meeting Recap",
    description: "Transform meeting transcript into summary, action items, and follow-up email",
    author: "revops-sam",
    tags: ["Sales", "Productivity", "Communication"],
    step_count: 3,
    category: "sales_crm",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/meeting_recap.yaml",
    stars: 192,
    forks: 4,
    downloads: 254,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "lawtech-ai/onboarding-workflow",
    name: "Employee Onboarding",
    description: "Automate new employee onboarding - checklist, welcome email, accounts, training plan, and manager notification",
    author: "lawtech-ai",
    tags: ["HR", "Onboarding", "Automation", "Slack", "Notion"],
    step_count: 5,
    category: "hr_legal",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/onboarding_workflow.yaml",
    stars: 125,
    forks: 3,
    downloads: 159,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "datacraft-io/pdf-summary",
    name: "PDF Summary",
    description: "Scan a directory for PDF files, summarize each one in parallel, and create a final report",
    author: "datacraft-io",
    tags: ["pdf", "summarization", "parallel", "documents"],
    step_count: 3,
    category: "general_ai",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/pdf_summary.yaml",
    stars: 332,
    forks: 5,
    downloads: 470,
    featured: true,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "revops-sam/proposal-generator",
    name: "Proposal Generator",
    description: "Generate a customized business proposal from meeting notes and product info",
    author: "revops-sam",
    tags: ["Sales", "Document", "Proposal"],
    step_count: 4,
    category: "sales_crm",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/proposal_generator.yaml",
    stars: 89,
    forks: 1,
    downloads: 132,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "ops-ninja/release-notes",
    name: "Release Notes Generator",
    description: "Generate user-facing release notes and internal changelog from commit history",
    author: "ops-ninja",
    tags: ["Product", "Engineering", "Documentation"],
    step_count: 4,
    category: "engineering",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/release_notes.yaml",
    stars: 174,
    forks: 1,
    downloads: 274,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "priya-fintech/research-agent",
    name: "Research Agent",
    description: "Searches for information, then analyzes sources and extracts facts in parallel, and combines all results",
    author: "priya-fintech",
    tags: ["research", "parallel", "analysis", "extraction"],
    step_count: 4,
    category: "general_ai",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/research_agent.yaml",
    stars: 207,
    forks: 5,
    downloads: 263,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "lawtech-ai/resume-screener",
    name: "Resume Screener",
    description: "Screen resume against job description with match scoring and interview recommendations",
    author: "lawtech-ai",
    tags: ["HR", "Recruiting", "Screening"],
    step_count: 4,
    category: "hr_legal",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/resume_screener.yaml",
    stars: 273,
    forks: 4,
    downloads: 389,
    featured: true,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "amira-dev/review-and-approve",
    name: "Review and Approve",
    description: "Generates content, pauses for human review and approval, then publishes the approved content",
    author: "amira-dev",
    tags: ["approval", "human-in-the-loop", "content", "review"],
    step_count: 3,
    category: "general_ai",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/review_and_approve.yaml",
    stars: 295,
    forks: 0,
    downloads: 493,
    featured: true,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "gizmax/review-sentiment",
    name: "Review Sentiment",
    description: "Analyze customer reviews to extract sentiment trends and actionable insights",
    author: "gizmax",
    tags: ["Support", "Analytics", "Sentiment"],
    step_count: 4,
    category: "support",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/review_sentiment.yaml",
    stars: 341,
    forks: 7,
    downloads: 453,
    featured: true,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "jmartinez/sales-pipeline-autopilot",
    name: "Sales Pipeline Autopilot",
    description: "Monitor stalled deals, draft follow-ups, and alert your team on pipeline risks",
    author: "jmartinez",
    tags: ["Sales", "Pipeline", "CRM", "Automation"],
    step_count: 4,
    category: "sales_crm",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/sales_pipeline_autopilot.yaml",
    stars: 222,
    forks: 1,
    downloads: 354,
    featured: true,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "lena-content/seo-content",
    name: "SEO Content Writer",
    description: "Research keywords and create SEO-optimized article with meta tags",
    author: "lena-content",
    tags: ["Marketing", "SEO", "Content"],
    step_count: 4,
    category: "marketing",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/seo_content.yaml",
    stars: 173,
    forks: 1,
    downloads: 273,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "supportflow/sla-watchdog",
    name: "SLA Watchdog",
    description: "Monitor SLA compliance, check ticket response times, and alert on breaches via Slack",
    author: "supportflow",
    tags: ["Support", "Zendesk", "SLA", "Monitoring"],
    step_count: 4,
    category: "support",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/sla_watchdog.yaml",
    stars: 257,
    forks: 4,
    downloads: 362,
    featured: true,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "k8s-ops/slack-standup",
    name: "Slack Standup Summary",
    description: "Collect daily standup updates from Slack and post a summary",
    author: "k8s-ops",
    tags: ["Communication", "Slack", "Team"],
    step_count: 3,
    category: "engineering",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/slack_standup.yaml",
    stars: 352,
    forks: 5,
    downloads: 504,
    featured: true,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "k8s-ops/sprint-standup",
    name: "Sprint Standup",
    description: "Synthesize Jira sprint progress and GitHub PRs into a daily standup summary for Slack",
    author: "k8s-ops",
    tags: ["Engineering", "Jira", "GitHub", "Standup"],
    step_count: 4,
    category: "engineering",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/sprint_standup.yaml",
    stars: 255,
    forks: 3,
    downloads: 375,
    featured: true,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "priya-fintech/summarize",
    name: "Text Summarizer",
    description: "Takes text input, summarizes it, and formats the summary for output",
    author: "priya-fintech",
    tags: ["text", "summarization", "formatting"],
    step_count: 2,
    category: "general_ai",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/summarize.yaml",
    stars: 274,
    forks: 1,
    downloads: 440,
    featured: true,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "gizmax/support-ticket-triage",
    name: "Support Ticket Triage",
    description: "Fetch recent Zendesk tickets, classify by urgency, draft responses, and notify Slack",
    author: "gizmax",
    tags: ["Support", "Zendesk", "Triage", "Automation"],
    step_count: 4,
    category: "support",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/support_ticket_triage.yaml",
    stars: 91,
    forks: 3,
    downloads: 103,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "nadia-cx/ticket-classifier",
    name: "Ticket Classifier",
    description: "Classify support ticket, assign priority, and draft response",
    author: "nadia-cx",
    tags: ["Support", "Classification", "Automation"],
    step_count: 4,
    category: "support",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/ticket_classifier.yaml",
    stars: 325,
    forks: 4,
    downloads: 475,
    featured: true,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "priya-fintech/translate",
    name: "Language Translator",
    description: "Detects the source language and translates text to the target language",
    author: "priya-fintech",
    tags: ["translation", "language", "i18n"],
    step_count: 2,
    category: "general_ai",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/translate.yaml",
    stars: 102,
    forks: 3,
    downloads: 120,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "sarah-ml/churn-predictor-v2",
    name: "Churn Predictor V2",
    description: "Enhanced churn prediction with multi-signal analysis, Slack alerts, and automated retention campaigns via HubSpot sequences",
    author: "sarah-ml",
    tags: ["ML", "Churn", "Retention", "CRM", "HubSpot"],
    step_count: 6,
    category: "sales_crm",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/hub/community/sarah-ml/churn-predictor-v2.yaml",
    stars: 170,
    forks: 3,
    downloads: 234,
    featured: false,
    created_at: "2026-02-20",
    updated_at: "2026-02-24",
  },
  {
    slug: "k8s-ops/incident-responder",
    name: "DevOps Incident Responder",
    description: "Monitors PagerDuty alerts, correlates logs from Datadog, runs root cause analysis, and posts incident summary to Slack with remediation steps",
    author: "k8s-ops",
    tags: ["DevOps", "Incident", "Monitoring", "Automation", "PagerDuty"],
    step_count: 6,
    category: "engineering",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/hub/community/k8s-ops/incident-responder.yaml",
    stars: 323,
    forks: 5,
    downloads: 456,
    featured: true,
    created_at: "2026-02-18",
    updated_at: "2026-02-22",
  },
  {
    slug: "content-queen/content-calendar",
    name: "Content Calendar Generator",
    description: "Research trending topics in your niche, generate a month-long content calendar with SEO keywords, and draft outlines for each piece",
    author: "content-queen",
    tags: ["Content", "SEO", "Planning", "Marketing", "Calendar"],
    step_count: 5,
    category: "marketing",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/hub/community/content-queen/content-calendar.yaml",
    stars: 207,
    forks: 2,
    downloads: 312,
    featured: true,
    created_at: "2026-02-15",
    updated_at: "2026-02-20",
  },
  {
    slug: "lawtech-ai/contract-reviewer-pro",
    name: "Contract Reviewer Pro",
    description: "Advanced contract review with clause-by-clause risk analysis, jurisdiction-specific compliance checks, and amendment draft generation",
    author: "lawtech-ai",
    tags: ["Legal", "PDF", "Compliance", "Contract", "Risk"],
    step_count: 5,
    category: "hr_legal",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/hub/community/lawtech-ai/contract-reviewer-pro.yaml",
    stars: 123,
    forks: 1,
    downloads: 189,
    featured: false,
    created_at: "2026-02-14",
    updated_at: "2026-02-23",
  },
  {
    slug: "devrel-bot/release-notes-pro",
    name: "Release Notes Pro",
    description: "Pull merged PRs from GitHub, classify changes, generate user-facing release notes, create changelog entries, and post to Slack and docs site",
    author: "devrel-bot",
    tags: ["DevRel", "GitHub", "Documentation", "Release", "Changelog"],
    step_count: 5,
    category: "engineering",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/hub/community/devrel-bot/release-notes-pro.yaml",
    stars: 212,
    forks: 4,
    downloads: 287,
    featured: false,
    created_at: "2026-02-12",
    updated_at: "2026-02-21",
  },
  {
    slug: "social-sage/social-repurposer",
    name: "Social Media Repurposer",
    description: "Take any blog post URL, extract key points, generate optimized posts for Twitter/X, LinkedIn, Instagram, and TikTok with image prompts",
    author: "social-sage",
    tags: ["Social", "Content", "Repurpose", "Multi-platform"],
    step_count: 5,
    category: "marketing",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/hub/community/social-sage/social-repurposer.yaml",
    stars: 138,
    forks: 2,
    downloads: 198,
    featured: false,
    created_at: "2026-02-10",
    updated_at: "2026-02-19",
  },
  {
    slug: "pricewatch/pricing-tracker",
    name: "Competitor Pricing Tracker",
    description: "Monitor competitor pricing pages, detect changes, compare feature matrices, generate visual diff reports, and alert via Slack",
    author: "pricewatch",
    tags: ["Pricing", "Competitors", "Alerts", "Monitoring"],
    step_count: 4,
    category: "sales_crm",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/hub/community/pricewatch/pricing-tracker.yaml",
    stars: 110,
    forks: 1,
    downloads: 167,
    featured: false,
    created_at: "2026-02-08",
    updated_at: "2026-02-18",
  },
  {
    slug: "supportflow/multi-channel-router",
    name: "Multi-Channel Support Router",
    description: "Classify incoming tickets from email, chat, and social. Route to the right team with suggested responses and auto-escalation rules",
    author: "supportflow",
    tags: ["Support", "Routing", "Classification", "Multi-channel"],
    step_count: 5,
    category: "support",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/hub/community/supportflow/multi-channel-router.yaml",
    stars: 163,
    forks: 3,
    downloads: 223,
    featured: false,
    created_at: "2026-02-06",
    updated_at: "2026-02-17",
  },
  {
    slug: "gizmax/market-opportunity-scout",
    name: "Market Opportunity Scout",
    description: "Research market landscape, mine competitor gaps, size the TAM, and produce an actionable opportunity report",
    author: "gizmax",
    tags: ["Market-Research", "TAM", "Strategy", "Competitive-Intel"],
    step_count: 7,
    category: "marketing",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/market_opportunity_scout.yaml",
    stars: 111,
    forks: 3,
    downloads: 135,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "content-queen/competitive-intelligence-radar",
    name: "Competitive Intelligence Radar",
    description: "Monitor competitor activity, detect strategic changes, update battlecards, and distribute alerts",
    author: "content-queen",
    tags: ["Competitive-Intel", "Monitoring", "Strategy", "Battlecards"],
    step_count: 6,
    category: "marketing",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/competitive_intelligence_radar.yaml",
    stars: 105,
    forks: 3,
    downloads: 125,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "social-sage/voice-of-market",
    name: "Voice of Market",
    description: "Mine forums, reviews, and social channels to extract sentiment, unmet needs, and buyer personas",
    author: "social-sage",
    tags: ["Market-Research", "Sentiment", "VoC", "Personas"],
    step_count: 7,
    category: "marketing",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/voice_of_market.yaml",
    stars: 141,
    forks: 5,
    downloads: 153,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "pricewatch/pricing-intelligence",
    name: "Pricing Intelligence",
    description: "Analyze competitor pricing, feature-value perception, and willingness to pay to optimize packaging and pricing strategy",
    author: "pricewatch",
    tags: ["Pricing", "Competitive-Intel", "Strategy", "Revenue"],
    step_count: 6,
    category: "marketing",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/pricing_intelligence.yaml",
    stars: 67,
    forks: 1,
    downloads: 96,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "lena-content/trend-radar",
    name: "Trend Radar",
    description: "Scan academic research, startup activity, social signals, and investment patterns to identify emerging trends and assess their strategic impact",
    author: "lena-content",
    tags: ["Trends", "Research", "Innovation", "Strategy"],
    step_count: 7,
    category: "marketing",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/trend_radar.yaml",
    stars: 38,
    forks: 2,
    downloads: 30,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "gizmax/win-loss-intelligence",
    name: "Win-Loss Intelligence",
    description: "Ingest CRM deal data, extract win/loss signals, identify patterns, and generate strategic recommendations with updated battlecards",
    author: "gizmax",
    tags: ["Sales", "Win-Loss", "CRM", "Strategy"],
    step_count: 6,
    category: "sales_crm",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/win_loss_intelligence.yaml",
    stars: 64,
    forks: 1,
    downloads: 91,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "lena-content/market-entry-strategy",
    name: "Market Entry Strategy",
    description: "Comprehensive market entry analysis covering sizing, competitive landscape, regulatory environment, customer research, and go-to-market strategy",
    author: "lena-content",
    tags: ["Strategy", "Market-Entry", "TAM", "Research"],
    step_count: 7,
    category: "marketing",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/market_entry_strategy.yaml",
    stars: 44,
    forks: 2,
    downloads: 40,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "gizmax/account-intelligence",
    name: "Account Intelligence Engine",
    description: "Profile target accounts, enrich with firmographic and technographic data in parallel, detect buying signals, and generate personalized outreach",
    author: "gizmax",
    tags: ["Sales", "ABM", "Intelligence", "CRM"],
    step_count: 6,
    category: "sales_crm",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/account_intelligence.yaml",
    stars: 55,
    forks: 0,
    downloads: 93,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "nadia-cx/deal-velocity-optimizer",
    name: "Deal Velocity Optimizer",
    description: "Analyze pipeline health, score deal risks, recommend actions, and build competitive battle cards to accelerate deal closure",
    author: "nadia-cx",
    tags: ["Sales", "Pipeline", "CRM", "Forecasting"],
    step_count: 5,
    category: "sales_crm",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/deal_velocity_optimizer.yaml",
    stars: 117,
    forks: 2,
    downloads: 162,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "priya-fintech/revenue-forecast-ensemble",
    name: "Revenue Forecast Ensemble",
    description: "Generate revenue forecasts using statistical, ML, and LLM-based methods in parallel, then synthesize into an ensemble prediction with scenario analysis",
    author: "priya-fintech",
    tags: ["Forecasting", "Revenue", "Analytics", "Finance"],
    step_count: 6,
    category: "sales_crm",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/revenue_forecast_ensemble.yaml",
    stars: 67,
    forks: 3,
    downloads: 62,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "revops-sam/churn-prediction-pipeline",
    name: "Churn Prediction Pipeline",
    description: "Analyze customer usage patterns, score churn risk, identify root causes, generate retention offers, and orchestrate outreach campaigns",
    author: "revops-sam",
    tags: ["Churn", "Customer-Success", "Retention", "Analytics"],
    step_count: 6,
    category: "sales_crm",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/churn_prediction_pipeline.yaml",
    stars: 23,
    forks: 0,
    downloads: 39,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "kai-security/incident-command-center",
    name: "Incident Command Center",
    description: "Automated incident response - ingest alerts, analyze logs and metrics in parallel, find root cause, and generate remediation runbooks",
    author: "kai-security",
    tags: ["DevOps", "SRE", "Incident-Response", "Monitoring"],
    step_count: 6,
    category: "engineering",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/incident_command_center.yaml",
    stars: 61,
    forks: 4,
    downloads: 36,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "kai-security/deployment-risk-analyzer",
    name: "Deployment Risk Analyzer",
    description: "Analyze deployment risk by scanning diffs, dependencies, and performance impact before go/no-go decision",
    author: "kai-security",
    tags: ["DevOps", "CI-CD", "Security", "Risk"],
    step_count: 5,
    category: "engineering",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/deployment_risk_analyzer.yaml",
    stars: 99,
    forks: 3,
    downloads: 115,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "kai-security/soc-triage-pipeline",
    name: "SOC Triage Pipeline",
    description: "Security Operations Center alert triage - deduplicate, enrich with threat intel, map to MITRE ATT&CK, and recommend containment",
    author: "kai-security",
    tags: ["Security", "SOC", "Threat-Intel", "SIEM"],
    step_count: 7,
    category: "engineering",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/soc_triage_pipeline.yaml",
    stars: 89,
    forks: 1,
    downloads: 132,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "kai-security/ai-red-team",
    name: "AI Red Team",
    description: "Automated adversarial testing of AI models - probe for prompt injection, jailbreaks, bias, and safety vulnerabilities",
    author: "kai-security",
    tags: ["AI-Safety", "Security", "Testing", "Red-Team"],
    step_count: 6,
    category: "engineering",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/ai_red_team.yaml",
    stars: 107,
    forks: 5,
    downloads: 95,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "priya-fintech/ma-due-diligence",
    name: "M&A Due Diligence",
    description: "Comprehensive due diligence analysis for mergers and acquisitions with parallel risk and terms extraction",
    author: "priya-fintech",
    tags: ["Legal", "M&A", "Due-Diligence", "Finance"],
    step_count: 6,
    category: "hr_legal",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/ma_due_diligence.yaml",
    stars: 73,
    forks: 5,
    downloads: 39,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "marcus-hr/regulatory-change-analyzer",
    name: "Regulatory Change Analyzer",
    description: "Monitor regulatory changes, assess compliance gaps, and generate remediation plans",
    author: "marcus-hr",
    tags: ["Compliance", "Regulatory", "Legal", "Risk"],
    step_count: 5,
    category: "hr_legal",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/regulatory_change_analyzer.yaml",
    stars: 87,
    forks: 2,
    downloads: 113,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "lawtech-ai/contract-lifecycle",
    name: "Contract Lifecycle Manager",
    description: "End-to-end contract lifecycle from drafting through approval to obligation tracking",
    author: "lawtech-ai",
    tags: ["Legal", "Contracts", "Negotiation", "CLM"],
    step_count: 5,
    category: "hr_legal",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/contract_lifecycle.yaml",
    stars: 61,
    forks: 0,
    downloads: 103,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "priya-fintech/earnings-call-intelligence",
    name: "Earnings Call Intelligence",
    description: "Analyze earnings call transcripts for sentiment, key metrics, and investment insights",
    author: "priya-fintech",
    tags: ["Finance", "Investment", "Analytics", "Intelligence"],
    step_count: 6,
    category: "general_ai",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/earnings_call_intelligence.yaml",
    stars: 41,
    forks: 1,
    downloads: 53,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "lena-content/content-factory",
    name: "Content Factory",
    description: "Generate a complete multi-platform content package from a single brief with SEO-optimized article and social posts",
    author: "lena-content",
    tags: ["Content", "SEO", "Social-Media", "Marketing"],
    step_count: 7,
    category: "marketing",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/content_factory.yaml",
    stars: 67,
    forks: 1,
    downloads: 96,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "content-queen/podcast-to-empire",
    name: "Podcast to Empire",
    description: "Transform a single podcast episode into a full content empire with blog, social, newsletter, and SEO landing page",
    author: "content-queen",
    tags: ["Content", "Podcast", "Repurpose", "Marketing"],
    step_count: 7,
    category: "marketing",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/podcast_to_empire.yaml",
    stars: 87,
    forks: 0,
    downloads: 146,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "lena-content/startup-growth-engine",
    name: "Startup Growth Engine",
    description: "Comprehensive growth audit with parallel strategy tracks for content, SEO, and conversion optimization",
    author: "lena-content",
    tags: ["Growth", "Startup", "SEO", "Marketing", "Analytics"],
    step_count: 6,
    category: "marketing",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/startup_growth_engine.yaml",
    stars: 78,
    forks: 3,
    downloads: 81,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "workflow-lab/supplier-risk-intelligence",
    name: "Supplier Risk Intelligence",
    description: "Assess supplier portfolio risks across financial, geopolitical, and ESG dimensions with alternative sourcing recommendations",
    author: "workflow-lab",
    tags: ["Supply-Chain", "Risk", "Procurement", "ESG"],
    step_count: 7,
    category: "general_ai",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/supplier_risk_intelligence.yaml",
    stars: 48,
    forks: 2,
    downloads: 47,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "workflow-lab/demand-forecasting",
    name: "Demand Forecasting",
    description: "Multi-signal demand forecasting combining statistical models, market intelligence, and social trends for inventory planning",
    author: "workflow-lab",
    tags: ["Supply-Chain", "Forecasting", "Analytics", "Inventory"],
    step_count: 6,
    category: "general_ai",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/demand_forecasting.yaml",
    stars: 94,
    forks: 3,
    downloads: 107,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "pricewatch/dynamic-pricing",
    name: "Dynamic Pricing",
    description: "Optimize product pricing using competitor intelligence, demand elasticity, and margin analysis with A/B test design",
    author: "pricewatch",
    tags: ["Pricing", "E-commerce", "Analytics", "Revenue"],
    step_count: 6,
    category: "general_ai",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/dynamic_pricing.yaml",
    stars: 80,
    forks: 2,
    downloads: 100,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "workflow-lab/recruiting-pipeline",
    name: "Recruiting Pipeline",
    description: "End-to-end recruiting workflow from job description to offer preparation with candidate evaluation and interview coordination",
    author: "workflow-lab",
    tags: ["HR", "Recruiting", "Hiring", "Talent"],
    step_count: 7,
    category: "hr_legal",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/recruiting_pipeline.yaml",
    stars: 80,
    forks: 4,
    downloads: 68,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "marcus-hr/org-health-pulse",
    name: "Org Health Pulse",
    description: "Analyze employee survey data to surface sentiment trends, flight risks, and targeted intervention recommendations",
    author: "marcus-hr",
    tags: ["HR", "Analytics", "Culture", "Engagement"],
    step_count: 7,
    category: "hr_legal",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/org_health_pulse.yaml",
    stars: 77,
    forks: 2,
    downloads: 95,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "k8s-ops/self-healing-pipeline",
    name: "Self-Healing Pipeline",
    description: "Monitor data pipelines for schema drift and anomalies, auto-fix common issues, and report on data quality recovery",
    author: "k8s-ops",
    tags: ["Data", "ETL", "DevOps", "Automation"],
    step_count: 6,
    category: "engineering",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/self_healing_pipeline.yaml",
    stars: 123,
    forks: 2,
    downloads: 172,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "k8s-ops/nl-to-dashboard",
    name: "NL to Dashboard",
    description: "Transform natural language business questions into optimized SQL queries, visualizations, and narrative insights",
    author: "k8s-ops",
    tags: ["Analytics", "SQL", "Visualization", "Data"],
    step_count: 6,
    category: "engineering",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/nl_to_dashboard.yaml",
    stars: 73,
    forks: 1,
    downloads: 105,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "datacraft-io/model-evaluation-arena",
    name: "Model Evaluation Arena",
    description: "Systematically benchmark and compare multiple AI models across safety, accuracy, cost, and latency with statistical rigor",
    author: "datacraft-io",
    tags: ["AI-Safety", "Evaluation", "Testing", "Benchmarking"],
    step_count: 6,
    category: "engineering",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/model_evaluation_arena.yaml",
    stars: 122,
    forks: 4,
    downloads: 138,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "datacraft-io/product-feedback-prioritizer",
    name: "Product Feedback Prioritizer",
    description: "Aggregate product feedback from multiple channels, deduplicate, cluster by theme, score by business impact, and produce a prioritized roadmap",
    author: "datacraft-io",
    tags: ["Product", "Feedback", "Prioritization", "Roadmap", "RICE"],
    step_count: 6,
    category: "general_ai",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/product_feedback_prioritizer.yaml",
    stars: 18,
    forks: 0,
    downloads: 30,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "priya-fintech/invoice-processor",
    name: "Invoice Processor",
    description: "Extract data from invoices using OCR patterns, validate against PO records, detect anomalies, route for approval, and generate accounting entries",
    author: "priya-fintech",
    tags: ["Finance", "Invoices", "AP", "Accounting", "Audit"],
    step_count: 6,
    category: "general_ai",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/invoice_processor.yaml",
    stars: 31,
    forks: 1,
    downloads: 36,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "lena-content/video-to-shorts",
    name: "Video to Shorts",
    description: "Analyze long-form video content, identify viral-worthy segments, generate optimized short clips with captions and hooks for TikTok, Reels, and Shorts",
    author: "lena-content",
    tags: ["Video", "TikTok", "Reels", "Shorts", "Social-Media", "Marketing"],
    step_count: 6,
    category: "marketing",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/video_to_shorts.yaml",
    stars: 51,
    forks: 2,
    downloads: 53,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "datacraft-io/rag-knowledge-base",
    name: "RAG Knowledge Base Builder",
    description: "Build and optimize a RAG knowledge base from documents - chunk, embed, evaluate retrieval quality, and generate optimization recommendations",
    author: "datacraft-io",
    tags: ["RAG", "Embeddings", "Knowledge-Base", "Retrieval", "NLP"],
    step_count: 6,
    category: "engineering",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/rag_knowledge_base.yaml",
    stars: 108,
    forks: 0,
    downloads: 180,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "gizmax/client-onboarding-orchestrator",
    name: "Client Onboarding Orchestrator",
    description: "Orchestrate multi-step client onboarding with CRM updates, welcome sequences, provisioning checklists, and health score tracking",
    author: "gizmax",
    tags: ["Onboarding", "CRM", "Customer-Success", "Sales"],
    step_count: 6,
    category: "sales_crm",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/client_onboarding_orchestrator.yaml",
    stars: 70,
    forks: 5,
    downloads: 34,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "lena-content/course-creator",
    name: "Course Creator",
    description: "Generate complete online course content from a topic - outline, lesson scripts, quizzes, assignments, and platform-ready export",
    author: "lena-content",
    tags: ["Education", "Course-Design", "E-Learning", "Content"],
    step_count: 6,
    category: "general_ai",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/course_creator.yaml",
    stars: 71,
    forks: 1,
    downloads: 103,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "jmartinez/real-estate-listing",
    name: "Real Estate Listing Optimizer",
    description: "Optimize real estate listings with AI-enhanced descriptions, comparative market analysis, pricing recommendations, and multi-platform distribution strategy",
    author: "jmartinez",
    tags: ["Real Estate", "Listing", "CMA", "Marketing", "Property"],
    step_count: 6,
    category: "general_ai",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/real_estate_listing.yaml",
    stars: 123,
    forks: 5,
    downloads: 122,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "content-queen/social-media-calendar",
    name: "Social Media Content Calendar",
    description: "Plan, create, and schedule a complete social media content calendar with platform-specific content, hashtag strategies, and performance predictions",
    author: "content-queen",
    tags: ["Social Media", "Content Calendar", "Marketing", "Hashtags", "Engagement"],
    step_count: 6,
    category: "marketing",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/social_media_calendar.yaml",
    stars: 122,
    forks: 5,
    downloads: 120,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "jmartinez/grant-proposal",
    name: "Grant Proposal Builder",
    description: "Parse grant RFPs, check eligibility, draft comprehensive proposals with budgets, and generate compliance checklists",
    author: "jmartinez",
    tags: ["Grant", "Proposal", "RFP", "Funding", "Compliance"],
    step_count: 6,
    category: "general_ai",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/grant_proposal.yaml",
    stars: 115,
    forks: 4,
    downloads: 125,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "elena-growth/agency-report-generator",
    name: "Agency Report Generator",
    description: "Generate comprehensive multi-client agency performance reports with cross-channel analytics, benchmarking, and strategic recommendations",
    author: "elena-growth",
    tags: ["Marketing", "Analytics", "Reporting", "Agency", "Multi-Channel"],
    step_count: 6,
    category: "marketing",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/agency_report_generator.yaml",
    stars: 123,
    forks: 5,
    downloads: 123,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "workflow-lab/clinical-notes",
    name: "Clinical Notes Processor",
    description: "Process clinical encounter data into structured SOAP notes, extract diagnoses with ICD-10 codes, generate billing codes, and flag compliance issues",
    author: "workflow-lab",
    tags: ["Healthcare", "Clinical", "SOAP", "ICD-10", "Compliance"],
    step_count: 6,
    category: "general_ai",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/clinical_notes.yaml",
    stars: 127,
    forks: 4,
    downloads: 145,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "elena-growth/ecommerce-catalog",
    name: "E-Commerce Catalog Enrichment",
    description: "Enrich e-commerce product catalogs with SEO-optimized descriptions, attribute extraction, cross-sell mapping, and A/B title variants",
    author: "elena-growth",
    tags: ["E-Commerce", "SEO", "Catalog", "Product", "Marketing"],
    step_count: 6,
    category: "marketing",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/ecommerce_catalog.yaml",
    stars: 64,
    forks: 0,
    downloads: 108,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "nadia-cx/voice-agent-pipeline",
    name: "Voice Agent Pipeline",
    description: "Process call recordings with transcription, sentiment analysis, coaching insights, compliance checks, and agent performance scoring",
    author: "nadia-cx",
    tags: ["Support", "Call-Center", "QA", "Coaching", "Compliance"],
    step_count: 6,
    category: "support",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/voice_agent_pipeline.yaml",
    stars: 126,
    forks: 4,
    downloads: 144,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "amira-dev/freelancer-proposal",
    name: "Freelancer Proposal Generator",
    description: "Generate winning freelancer proposals by analyzing project requirements, matching portfolio pieces, crafting personalized pitches, and optimizing pricing strategy",
    author: "amira-dev",
    tags: ["Freelance", "Proposals", "Upwork", "Pricing", "Business"],
    step_count: 6,
    category: "general_ai",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/freelancer_proposal.yaml",
    stars: 42,
    forks: 0,
    downloads: 70,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
  {
    slug: "tomr-eng/product-design-spec",
    name: "Product Design Specification",
    description: "Transform user stories and requirements into comprehensive product design specifications with wireframe descriptions, interaction patterns, and developer handoff documentation",
    author: "tomr-eng",
    tags: ["Design", "UX", "Product", "Wireframes", "Accessibility"],
    step_count: 6,
    category: "engineering",
    download_url: "https://raw.githubusercontent.com/gizmax/Sandcastle/main/src/sandcastle/templates/product_design_spec.yaml",
    stars: 79,
    forks: 1,
    downloads: 116,
    featured: false,
    created_at: "2026-02-25",
    updated_at: "2026-02-25",
  },
];

const MOCK_COMMUNITY_STATS = {
  total_templates: MOCK_COMMUNITY_TEMPLATES.length,
  total_authors: new Set(MOCK_COMMUNITY_TEMPLATES.map((t) => t.author)).size,
  categories: Object.entries(
    MOCK_COMMUNITY_TEMPLATES.reduce<Record<string, number>>((acc, t) => {
      acc[t.category] = (acc[t.category] || 0) + 1;
      return acc;
    }, {})
  ).map(([id, count]) => ({ id, count })),
};

const MOCK_COMMUNITY_COLLECTIONS = [
  {
    id: "sales-automation-stack",
    name: "Complete Sales Stack",
    description: "End-to-end sales pipeline - from lead generation to deal management",
    icon: "target",
    template_slugs: [
      "gizmax/customer-churn-predictor",
      "pricewatch/pricing-tracker",
      "gizmax/lead-enrichment",
      "jmartinez/lead-scoring",
      "jmartinez/sales-pipeline-autopilot",
      "gizmax/win-loss-intelligence",
    ],
    downloads: 892,
  },
  {
    id: "content-machine",
    name: "Content Machine",
    description: "Full content creation pipeline - research, write, optimize, distribute",
    icon: "pen-tool",
    template_slugs: [
      "content-queen/content-calendar",
      "social-sage/social-repurposer",
      "elena-growth/blog-to-social",
      "lena-content/seo-content",
      "lena-content/ad-copy-generator",
      "lena-content/trend-radar",
    ],
    downloads: 756,
  },
  {
    id: "devops-essentials",
    name: "DevOps Essentials",
    description: "Developer productivity - sprint tracking, standups, releases",
    icon: "terminal",
    template_slugs: [
      "k8s-ops/incident-responder",
      "devrel-bot/release-notes-pro",
      "k8s-ops/sprint-standup",
      "k8s-ops/slack-standup",
      "tomr-eng/api-docs-generator",
    ],
    downloads: 634,
  },
  {
    id: "support-suite",
    name: "Support Suite",
    description: "Customer support automation - triage, SLA, FAQ generation",
    icon: "headphones",
    template_slugs: [
      "supportflow/multi-channel-router",
      "gizmax/support-ticket-triage",
      "nadia-cx/ticket-classifier",
      "supportflow/customer-health-check",
      "nadia-cx/faq-generator",
      "supportflow/sla-watchdog",
    ],
    downloads: 543,
  },
  {
    id: "hr-toolkit",
    name: "HR Toolkit",
    description: "HR operations - hiring, onboarding, compliance, contracts",
    icon: "users",
    template_slugs: [
      "lawtech-ai/onboarding-workflow",
      "lawtech-ai/contract-reviewer-pro",
      "lawtech-ai/resume-screener",
      "marcus-hr/job-description",
      "marcus-hr/compliance-checker",
    ],
    downloads: 421,
  },
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
    handler: () => ({ status: "ok", runtime: true, redis: null, database: true }),
  },
  {
    match: /^\/runtime$/,
    handler: () => ({ mode: "local", database: "sqlite", queue: "in-process", storage: "local", data_dir: "./data", version: "0.15.0", sandbox_backend: "e2b" }),
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
      const workflow = _params.workflow;
      let filtered = MOCK_RUNS;
      if (status && status !== "all") {
        filtered = filtered.filter((r) => r.status === status);
      }
      if (workflow) {
        filtered = filtered.filter((r) => r.workflow_name === workflow);
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
    match: /^\/runs\/compare$/,
    method: "GET",
    handler: () => MOCK_RUN_COMPARE,
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
    match: /^\/eval\/runs$/,
    method: "GET",
    handler: () => MOCK_EVAL_RUNS,
  },
  {
    match: /^\/eval\/runs\/([^/]+)$/,
    method: "GET",
    handler: (params) => {
      const run = MOCK_EVAL_RUNS.find((r) => r.id === params._1);
      return run || null;
    },
  },
  {
    match: /^\/eval\/stats$/,
    method: "GET",
    handler: () => MOCK_EVAL_STATS,
  },
  {
    match: /^\/eval\/run$/,
    method: "POST",
    handler: () => MOCK_EVAL_RUNS[0],
  },
  {
    match: /^\/autopilot\/experiments$/,
    handler: () => MOCK_AUTOPILOT_EXPERIMENTS,
  },
  {
    match: /^\/autopilot\/stats$/,
    handler: () => MOCK_AUTOPILOT_STATS,
  },
  {
    match: /^\/violations$/,
    handler: (params) => {
      let filtered = MOCK_VIOLATIONS;
      if (params.severity && params.severity !== "all") {
        filtered = filtered.filter((v) => v.severity === params.severity);
      }
      return filtered;
    },
  },
  {
    match: /^\/violations\/stats$/,
    handler: () => MOCK_VIOLATION_STATS,
  },
  {
    match: /^\/optimizer\/decisions$/,
    handler: () => MOCK_OPTIMIZER_DECISIONS,
  },
  {
    match: /^\/optimizer\/stats$/,
    handler: () => MOCK_OPTIMIZER_STATS,
  },
  {
    match: /^\/templates$/,
    method: "GET",
    handler: () => MOCK_TEMPLATES,
  },
  {
    match: /^\/templates\/([^/]+)$/,
    method: "GET",
    handler: (params) => getTemplateDetail(params._1),
  },
  {
    match: /^\/hub\/registry$/,
    method: "GET",
    handler: () => ({
      templates: MOCK_COMMUNITY_TEMPLATES,
      stats: MOCK_COMMUNITY_STATS,
      collections: MOCK_COMMUNITY_COLLECTIONS,
    }),
  },
  {
    match: /^\/hub\/installed$/,
    method: "GET",
    handler: () => [],
  },
  {
    match: /^\/hub\/install\/(.+)$/,
    method: "POST",
    handler: () => ({ ok: true }),
  },
  {
    match: /^\/hub\/install\/(.+)$/,
    method: "DELETE",
    handler: () => ({ ok: true }),
  },
  {
    match: /^\/workflows\/([^/]+)\/export$/,
    method: "GET",
    handler: (params) => ({
      name: params._1,
      yaml: `name: ${params._1}\ndescription: Exported workflow\nsteps:\n  - id: step_1\n    model: sonnet\n    prompt: "Process the input data"\n  - id: step_2\n    model: haiku\n    depends_on: [step_1]\n    prompt: "Summarize results from {steps.step_1.output}"`,
    }),
  },
  {
    match: /^\/workflows\/([^/]+)\/versions$/,
    method: "GET",
    handler: (params) => {
      const name = params._1;
      const versions = MOCK_WORKFLOW_VERSIONS[name] || [];
      const prodVer = versions.find((v) => (v as Record<string, unknown>).status === "production") as Record<string, unknown> | undefined;
      const stagingVer = versions.find((v) => (v as Record<string, unknown>).status === "staging") as Record<string, unknown> | undefined;
      const draftVer = versions.find((v) => (v as Record<string, unknown>).status === "draft") as Record<string, unknown> | undefined;
      return {
        _data: {
          workflow_name: name,
          production_version: prodVer ? prodVer.version : null,
          staging_version: stagingVer ? stagingVer.version : null,
          latest_draft_version: draftVer ? draftVer.version : null,
          versions,
        },
        _meta: { total: versions.length, limit: 50, offset: 0 },
      };
    },
  },
  {
    match: /^\/workflows\/([^/]+)\/versions\/diff$/,
    method: "GET",
    handler: (params) => ({
      version_a: Number(params.version_a || 1),
      version_b: Number(params.version_b || 2),
      yaml_a: "name: example\nsteps:\n  - id: step1\n    model: sonnet",
      yaml_b: "name: example\nsteps:\n  - id: step1\n    model: opus\n  - id: step2\n    model: haiku",
      steps_added: ["step2"],
      steps_removed: [],
      steps_changed: ["step1"],
    }),
  },
  {
    match: /^\/settings$/,
    method: "GET",
    handler: () => ({ ...MOCK_SETTINGS }),
  },
  {
    match: /^\/settings$/,
    method: "PATCH",
    handler: (_params, body) => {
      const updates = body as Record<string, unknown> | undefined;
      if (updates) {
        Object.assign(MOCK_SETTINGS, updates);
      }
      return { ...MOCK_SETTINGS };
    },
  },
  {
    match: /^\/workflows$/,
    method: "POST",
    handler: (_params, body) => {
      const b = body as { name?: string; content?: string } | undefined;
      return {
        name: b?.name || "untitled",
        file_name: `${b?.name || "untitled"}.yaml`,
        version: 1,
        status: "draft",
      };
    },
  },
  {
    match: /^\/workflows\/run$/,
    method: "POST",
    handler: () => {
      return {
        run_id: `demo-${Date.now().toString(36)}`,
        status: "queued",
      };
    },
  },
  {
    match: /^\/generate$/,
    method: "POST",
    handler: (_params, body) => {
      const b = body as { description?: string } | undefined;
      const desc = b?.description || "Generated workflow";
      return {
        yaml_content: `name: generated-workflow\ndescription: "${desc}"\n\ndefault_model: sonnet\ndefault_max_turns: 10\ndefault_timeout: 300\n\ninput_schema:\n  required: ["topic"]\n  properties:\n    topic:\n      type: string\n      description: "The main topic or subject"\n\nsteps:\n  - id: research\n    prompt: >\n      Research the following topic thoroughly.\n      Topic: {input.topic}\n    model: sonnet\n    max_turns: 10\n\n  - id: draft\n    depends_on: [research]\n    prompt: >\n      Based on the research, create a comprehensive draft.\n      Research: {steps.research.output}\n    model: sonnet\n    max_turns: 10\n\n  - id: polish\n    depends_on: [draft]\n    prompt: >\n      Polish and finalize the draft for publication.\n      Draft: {steps.draft.output}\n    model: haiku\n    max_turns: 5\n`,
        name: "generated-workflow",
        description: desc,
        steps_count: 3,
        validation_errors: [],
        input_schema: {
          required: ["topic"],
          properties: { topic: { type: "string", description: "The main topic or subject" } },
        },
      };
    },
  },
  {
    match: /^\/generate\/chat$/,
    method: "POST",
    handler: (_params, body) => {
      const b = body as { messages?: Array<{ role: string; content: string }>; existing_yaml?: string } | undefined;
      const msgs = b?.messages || [];
      const hasExisting = !!b?.existing_yaml;
      // Heuristic: ask questions on first message if no existing YAML, otherwise generate YAML
      const hasYamlInHistory = msgs.some((m) => m.role === "assistant" && m.content.includes("steps"));
      const userText = msgs.filter((m) => m.role === "user").map((m) => m.content).join(" ").toLowerCase();
      const skipQuestions = userText.includes("just generate") || userText.includes("go ahead") || userText.includes("skip");

      if (msgs.length <= 1 && !hasExisting && !skipQuestions && !hasYamlInHistory) {
        return {
          mode: "questions",
          message: "Great idea! Let me ask a few questions to design the best workflow for you:\n\n1. What data sources or inputs will this workflow need?\n2. How many processing steps do you envision (simple 2-3 steps, or a more complex pipeline)?\n3. Do you need any approval gates or human review before certain steps?\n4. What should the final output look like (report, notification, data file)?",
        };
      }

      return {
        mode: "yaml",
        message: "Here's your workflow based on our discussion. You can refine it further or use it as-is.",
        yaml_content: `name: generated-workflow\ndescription: "AI-generated workflow"\n\ndefault_model: sonnet\ndefault_max_turns: 10\ndefault_timeout: 300\n\ninput_schema:\n  required: ["topic"]\n  properties:\n    topic:\n      type: string\n      description: "The main topic or subject"\n\nsteps:\n  - id: research\n    prompt: >\n      Research the following topic thoroughly.\n      Topic: {input.topic}\n    model: sonnet\n    max_turns: 10\n\n  - id: draft\n    depends_on: [research]\n    prompt: >\n      Based on the research, create a comprehensive draft.\n      Research: {steps.research.output}\n    model: sonnet\n    max_turns: 10\n\n  - id: polish\n    depends_on: [draft]\n    prompt: >\n      Polish and finalize the draft for publication.\n      Draft: {steps.draft.output}\n    model: haiku\n    max_turns: 5\n`,
        name: "generated-workflow",
        steps_count: 3,
        validation_errors: [],
        input_schema: {
          required: ["topic"],
          properties: { topic: { type: "string", description: "The main topic or subject" } },
        },
      };
    },
  },
  {
    match: /^\/api-keys$/,
    method: "GET",
    handler: () => MOCK_API_KEYS,
  },
  {
    match: /^\/api-keys$/,
    method: "POST",
    handler: (_params, body) => {
      const b = body as { name?: string; tenant_id?: string } | undefined;
      return {
        id: `key-${Date.now()}`,
        key: `sc_live_${Math.random().toString(36).slice(2, 14)}${Math.random().toString(36).slice(2, 14)}`,
        key_prefix: `sc_live_${Math.random().toString(36).slice(2, 6)}`,
        tenant_id: b?.tenant_id || "default",
        name: b?.name || "Untitled",
        created_at: new Date().toISOString(),
        last_used_at: null,
      };
    },
  },
  {
    match: /^\/tools$/,
    method: "GET",
    handler: (params) => {
      let filtered = MOCK_TOOLS;
      if (params.category && params.category !== "all") {
        filtered = filtered.filter((t) => t.category === params.category);
      }
      return { tools: filtered, total: filtered.length };
    },
  },
  {
    match: /^\/tools\/([^/]+)\/credentials$/,
    method: "PUT",
    handler: (params, body) => {
      const toolName = params._1;
      const tool = MOCK_TOOLS.find((t) => t.name === toolName);
      if (!tool) return null;
      const creds = (body as { credentials?: Record<string, string> })?.credentials || {};
      // In mock mode, update the tool's configured status
      const allVars = tool.credential_env_vars;
      const nowConfigured = allVars.length === 0 || allVars.every((v) => creds[v]);
      tool.configured = nowConfigured;
      tool.missing_credentials = allVars.filter((v) => !creds[v]);
      return { name: tool.name, configured: tool.configured, missing_credentials: tool.missing_credentials };
    },
  },
  {
    match: /^\/tools\/([^/]+)\/connections$/,
    method: "GET",
    handler: (params) => {
      const tool = MOCK_TOOLS.find((t) => t.name === params._1);
      return tool?.connections || [];
    },
  },
  {
    match: /^\/tools\/([^/]+)\/connections$/,
    method: "POST",
    handler: (params, body) => {
      const tool = MOCK_TOOLS.find((t) => t.name === params._1);
      if (!tool) return null;
      const b = body as { name?: string; credentials?: Record<string, string> };
      const name = b?.name || "default";
      const creds = b?.credentials || {};
      const allVars = tool.credential_env_vars;
      const conn = {
        name,
        tool_name: tool.name,
        credentials_configured: allVars.filter((v) => creds[v]),
        credentials_missing: allVars.filter((v) => !creds[v]),
        created_at: new Date().toISOString(),
      };
      tool.connections = tool.connections || [];
      tool.connections.push(conn);
      return conn;
    },
  },
  {
    match: /^\/tools\/([^/]+)\/connections\/([^/]+)$/,
    method: "PUT",
    handler: (params, body) => {
      const tool = MOCK_TOOLS.find((t) => t.name === params._1);
      if (!tool) return null;
      const conn = tool.connections?.find((c) => c.name === params._2);
      if (!conn) return null;
      const creds = (body as { credentials?: Record<string, string> })?.credentials || {};
      const allVars = tool.credential_env_vars;
      conn.credentials_configured = allVars.filter((v) => creds[v]);
      conn.credentials_missing = allVars.filter((v) => !creds[v]);
      return conn;
    },
  },
  {
    match: /^\/tools\/([^/]+)\/connections\/([^/]+)$/,
    method: "DELETE",
    handler: (params) => {
      const tool = MOCK_TOOLS.find((t) => t.name === params._1);
      if (!tool) return null;
      tool.connections = (tool.connections || []).filter((c) => c.name !== params._2);
      return { deleted: true };
    },
  },
];

export function mockFetch(
  path: string,
  params?: Record<string, string>,
  method?: string,
  body?: unknown
): ApiResponse {
  const mergedParams = params || {};
  const reqMethod = (method || "GET").toUpperCase();

  for (const route of routes) {
    const m = path.match(route.match);
    if (!m) continue;
    // Check method if specified on route
    if (route.method && route.method.toUpperCase() !== reqMethod) continue;

    const extractedParams: Record<string, string> = { ...mergedParams };
    m.slice(1).forEach((val, i) => {
      extractedParams[`_${i + 1}`] = val;
    });

    const result = route.handler(extractedParams, body);

    // Handle routes that return _data/_meta separately
    if (result && typeof result === "object" && "_data" in (result as Record<string, unknown>)) {
      const r = result as { _data: unknown; _meta: unknown };
      return { data: r._data, error: null, meta: r._meta as ApiResponse["meta"] };
    }

    return { data: result, error: null };
  }

  return { data: null, error: { code: "NOT_FOUND", message: `Mock: ${path} not found` } };
}
