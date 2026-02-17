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
  {
    name: "summarize",
    description: "Summarize text input with configurable detail level",
    tags: ["NLP", "Text"],
    step_count: 2,
  },
  {
    name: "translate",
    description: "Detect language and translate to target language",
    tags: ["NLP", "Translation"],
    step_count: 2,
  },
  {
    name: "research_agent",
    description: "Multi-source research with parallel analysis and fact extraction",
    tags: ["Research", "Multi-agent"],
    step_count: 4,
  },
  {
    name: "chain_of_thought",
    description: "Step-by-step reasoning through complex problems",
    tags: ["Reasoning", "Chain"],
    step_count: 3,
  },
  {
    name: "review_and_approve",
    description: "Content generation with human approval gate before publishing",
    tags: ["Human-in-loop", "Content"],
    step_count: 3,
  },
  {
    name: "blog_to_social",
    description: "Transform a blog post into platform-specific social media content",
    tags: ["Marketing", "Content", "Social"],
    step_count: 5,
  },
  {
    name: "seo_content",
    description: "Research keywords and create SEO-optimized article with meta tags",
    tags: ["Marketing", "SEO", "Content"],
    step_count: 4,
  },
  {
    name: "email_campaign",
    description: "Generate email campaign with subject line variants and A/B copy",
    tags: ["Marketing", "Email", "Campaign"],
    step_count: 5,
  },
  {
    name: "competitor_analysis",
    description: "Analyze competitor positioning, strengths, weaknesses, and opportunities",
    tags: ["Marketing", "Strategy", "Research"],
    step_count: 4,
  },
  {
    name: "ad_copy_generator",
    description: "Generate ad copy variants for Google Ads and Meta Ads campaigns",
    tags: ["Marketing", "Advertising", "Copywriting"],
    step_count: 4,
  },
  {
    name: "lead_enrichment",
    description: "Research and enrich lead data with company info, scoring, and outreach angles",
    tags: ["Sales", "Research", "Lead-Gen"],
    step_count: 5,
  },
  {
    name: "proposal_generator",
    description: "Generate a customized business proposal from meeting notes and product info",
    tags: ["Sales", "Document", "Proposal"],
    step_count: 4,
  },
  {
    name: "meeting_recap",
    description: "Transform meeting transcript into summary, action items, and follow-up email",
    tags: ["Sales", "Productivity", "Communication"],
    step_count: 3,
  },
  {
    name: "ticket_classifier",
    description: "Classify support ticket, assign priority, and draft response",
    tags: ["Support", "Classification", "Automation"],
    step_count: 4,
  },
  {
    name: "review_sentiment",
    description: "Analyze customer reviews to extract sentiment trends and actionable insights",
    tags: ["Support", "Analytics", "Sentiment"],
    step_count: 4,
  },
  {
    name: "job_description",
    description: "Generate inclusive job description with requirements, benefits, and interview plan",
    tags: ["HR", "Recruiting", "Content"],
    step_count: 4,
  },
  {
    name: "resume_screener",
    description: "Screen resume against job description with match scoring and interview recommendations",
    tags: ["HR", "Recruiting", "Screening"],
    step_count: 4,
  },
  {
    name: "contract_review",
    description: "Review contract for key terms, risks, and generate plain-language summary",
    tags: ["Legal", "Compliance", "Document"],
    step_count: 4,
  },
  {
    name: "release_notes",
    description: "Generate user-facing release notes and internal changelog from commit history",
    tags: ["Product", "Engineering", "Documentation"],
    step_count: 4,
  },
  {
    name: "data_extractor",
    description: "Extract structured data from documents with validation and error handling",
    tags: ["Product", "Data", "Automation"],
    step_count: 4,
  },
];

const TEMPLATE_YAMLS: Record<string, string> = {
  summarize: `name: "summarize"
description: "Summarize text input with configurable detail level"
sandstorm_url: "\${SANDSTORM_URL}"
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
  translate: `name: "translate"
description: "Detect language and translate to target language"
sandstorm_url: "\${SANDSTORM_URL}"
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
  research_agent: `name: "research_agent"
description: "Multi-source research with parallel analysis and fact extraction"
sandstorm_url: "\${SANDSTORM_URL}"
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
  chain_of_thought: `name: "chain_of_thought"
description: "Step-by-step reasoning through complex problems"
sandstorm_url: "\${SANDSTORM_URL}"
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
  review_and_approve: `name: "review_and_approve"
description: "Content generation with human approval gate before publishing"
sandstorm_url: "\${SANDSTORM_URL}"
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
  blog_to_social: `# name: Blog to Social Media
# description: Transform a blog post into platform-specific social media content
# tags: [Marketing, Content, Social]

name: blog-to-social
description: Transform a blog post into platform-specific social media content

sandstorm_url: \${SANDSTORM_URL}
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
  seo_content: `# name: SEO Content Writer
# description: Research keywords and create SEO-optimized article with meta tags
# tags: [Marketing, SEO, Content]

name: seo-content-writer
description: Research keywords and create SEO-optimized article with meta tags

sandstorm_url: \${SANDSTORM_URL}
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
  email_campaign: `# name: Email Campaign Generator
# description: Generate email campaign with subject line variants and A/B copy
# tags: [Marketing, Email, Campaign]

name: email-campaign-generator
description: Generate email campaign with subject line variants and A/B copy

sandstorm_url: \${SANDSTORM_URL}
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
  competitor_analysis: `# name: Competitor Analysis
# description: Analyze competitor positioning, strengths, weaknesses, and opportunities
# tags: [Marketing, Strategy, Research]

name: competitor-analysis
description: Analyze competitor positioning, strengths, weaknesses, and opportunities

sandstorm_url: \${SANDSTORM_URL}
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
  ad_copy_generator: `# name: Ad Copy Generator
# description: Generate ad copy variants for Google Ads and Meta Ads campaigns
# tags: [Marketing, Advertising, Copywriting]

name: ad-copy-generator
description: Generate ad copy variants for Google Ads and Meta Ads campaigns

sandstorm_url: \${SANDSTORM_URL}
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
  lead_enrichment: `# name: Lead Enrichment
# description: Research and enrich lead data with company info, scoring, and outreach angles
# tags: [Sales, Research, Lead-Gen]

name: lead-enrichment
description: Research and enrich lead data with company info, scoring, and outreach angles

sandstorm_url: \${SANDSTORM_URL}
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
  proposal_generator: `# name: Proposal Generator
# description: Generate a customized business proposal from meeting notes and product info
# tags: [Sales, Document, Proposal]

name: proposal-generator
description: Generate a customized business proposal from meeting notes and product info

sandstorm_url: \${SANDSTORM_URL}
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
  meeting_recap: `# name: Meeting Recap
# description: Transform meeting transcript into summary, action items, and follow-up email
# tags: [Sales, Productivity, Communication]

name: meeting-recap
description: Transform meeting transcript into summary, action items, and follow-up email

sandstorm_url: \${SANDSTORM_URL}
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
  ticket_classifier: `# name: Ticket Classifier
# description: Classify support ticket, assign priority, and draft response
# tags: [Support, Classification, Automation]

name: ticket-classifier
description: Classify support ticket, assign priority, and draft response

sandstorm_url: \${SANDSTORM_URL}
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
  review_sentiment: `# name: Review Sentiment
# description: Analyze customer reviews to extract sentiment trends and actionable insights
# tags: [Support, Analytics, Sentiment]

name: review-sentiment
description: Analyze customer reviews to extract sentiment trends and actionable insights

sandstorm_url: \${SANDSTORM_URL}
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
  job_description: `# name: Job Description Generator
# description: Generate inclusive job description with requirements, benefits, and interview plan
# tags: [HR, Recruiting, Content]

name: job-description-generator
description: Generate inclusive job description with requirements, benefits, and interview plan

sandstorm_url: \${SANDSTORM_URL}
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
  resume_screener: `# name: Resume Screener
# description: Screen resume against job description with match scoring and interview recommendations
# tags: [HR, Recruiting, Screening]

name: resume-screener
description: Screen resume against job description with match scoring and interview recommendations

sandstorm_url: \${SANDSTORM_URL}
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
  contract_review: `# name: Contract Review
# description: Review contract for key terms, risks, and generate plain-language summary
# tags: [Legal, Compliance, Document]

name: contract-review
description: Review contract for key terms, risks, and generate plain-language summary

sandstorm_url: \${SANDSTORM_URL}
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
  release_notes: `# name: Release Notes Generator
# description: Generate user-facing release notes and internal changelog from commit history
# tags: [Product, Engineering, Documentation]

name: release-notes-generator
description: Generate user-facing release notes and internal changelog from commit history

sandstorm_url: \${SANDSTORM_URL}
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
  data_extractor: `# name: Data Extractor
# description: Extract structured data from documents with validation and error handling
# tags: [Product, Data, Automation]

name: data-extractor
description: Extract structured data from documents with validation and error handling

sandstorm_url: \${SANDSTORM_URL}
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
};

function getTemplateDetail(name: string) {
  const template = MOCK_TEMPLATES.find((t) => t.name === name);
  if (!template) return null;
  return {
    ...template,
    file_name: `${name}.yaml`,
    content: TEMPLATE_YAMLS[name] || `name: "${name}"\nsteps: []`,
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
  sandstorm_url: "http://localhost:8080",
  anthropic_api_key: "****Qf8x",
  e2b_api_key: "****mN2k",
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

// Route matcher
type MockRoute = {
  match: RegExp;
  method?: string;
  handler: (params: Record<string, string>, body?: unknown) => unknown;
};

const routes: MockRoute[] = [
  {
    match: /^\/health$/,
    handler: () => ({ status: "ok", sandstorm: true, redis: null, database: true }),
  },
  {
    match: /^\/runtime$/,
    handler: () => ({ mode: "local", database: "sqlite", queue: "in-process", storage: "local", data_dir: "./data" }),
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
    match: /^\/runs\/compare$/,
    method: "GET",
    handler: () => MOCK_RUN_COMPARE,
  },
  {
    match: /^\/workflows\/([^/]+)\/versions$/,
    method: "GET",
    handler: (params) => {
      const name = params._1;
      const versions = MOCK_WORKFLOW_VERSIONS[name] || [];
      const prodVer = versions.find((v: Record<string, unknown>) => v.status === "production") as Record<string, unknown> | undefined;
      const stagingVer = versions.find((v: Record<string, unknown>) => v.status === "staging") as Record<string, unknown> | undefined;
      const draftVer = versions.find((v: Record<string, unknown>) => v.status === "draft") as Record<string, unknown> | undefined;
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
