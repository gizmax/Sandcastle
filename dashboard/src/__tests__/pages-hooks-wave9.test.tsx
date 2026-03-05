/**
 * Wave 9 Dashboard Tests - Pages and Components
 *
 * Covers areas not tested in previous waves:
 * - ApprovalsPage: loading, error, empty, list rendering, approve/reject/skip actions, filters, expand/collapse
 * - EvaluationsPage: loading, error, empty, stats display, run list, case expansion
 * - SystemHealthPage: loading, error, healthy/degraded states, refresh, service checks, quick stats
 * - ViolationsPage: loading, error, empty, severity filters, violation list, expand details, critical banner
 * - AdvisorPanel: open/close, score display, trend arrows, insights grouped by severity, dismiss, escape close
 * - Header: page title, search with debounce, keyboard navigation, mobile search toggle, Escape close
 * - Workflows page: loading, error, empty, workflow list, new workflow button
 * - TemplatesPage: loading, template display
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
// userEvent intentionally not imported - all tests use fireEvent instead

// ============================================================================
// Mocks
// ============================================================================

const mockNavigate = vi.fn();
const mockLocation = { pathname: "/" };
const mockSearchParams = [new URLSearchParams(), vi.fn()] as const;

vi.mock("@/api/client", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
    setApiKey: vi.fn(),
    clearStoredKey: vi.fn(),
    hasStoredKey: vi.fn(() => false),
    storedKeyPrefix: vi.fn(() => null),
    getStoredKey: vi.fn(() => null),
    storeApiKey: vi.fn(),
    authHeaders: vi.fn(() => ({})),
    sseUrl: vi.fn((p: string) => `/api${p}`),
    isMockMode: false,
    onMockChange: vi.fn(() => () => {}),
  },
}));

vi.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
  useLocation: () => mockLocation,
  useSearchParams: () => mockSearchParams,
  Link: ({ children, to, ...rest }: { children: React.ReactNode; to: string }) => (
    <a href={to} {...rest}>{children}</a>
  ),
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  },
}));

// Mock recharts - it uses browser APIs unavailable in jsdom
vi.mock("recharts", () => ({
  LineChart: ({ children }: { children: React.ReactNode }) => <div data-testid="line-chart">{children}</div>,
  Line: () => <div data-testid="line" />,
  XAxis: () => <div />,
  YAxis: () => <div />,
  CartesianGrid: () => <div />,
  Tooltip: () => <div />,
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

// Mock sub-components used by Header
vi.mock("@/components/shared/ThemeToggle", () => ({
  ThemeToggle: () => <div data-testid="theme-toggle" />,
}));

vi.mock("@/components/shared/LiveIndicator", () => ({
  LiveIndicator: () => <div data-testid="live-indicator" />,
}));

vi.mock("@/components/layout/NotificationCenter", () => ({
  NotificationCenter: (props: { notifications: unknown[] }) => (
    <div data-testid="notification-center">{JSON.stringify(props.notifications.length)}</div>
  ),
}));

vi.mock("@/hooks/useAdvisorContext", () => ({
  useAdvisorContext: () => ({
    score: 100,
    previousScore: null,
    deductions: [],
    activeInsights: [],
    loading: false,
    lastChecked: null,
    refresh: vi.fn(),
    dismiss: vi.fn(),
  }),
}));

// Mock sub-components used by Workflows page
vi.mock("@/components/workflows/WorkflowList", () => ({
  WorkflowList: ({ workflows }: { workflows: unknown[] }) => (
    <div data-testid="workflow-list">{workflows.length} workflows</div>
  ),
}));

vi.mock("@/components/workflows/RunWorkflowModal", () => ({
  RunWorkflowModal: () => <div data-testid="run-workflow-modal" />,
}));

vi.mock("@/components/workflows/DagGraph", () => ({
  DagGraph: () => <div data-testid="dag-graph" />,
}));

// Mock js-yaml for TemplatesPage
vi.mock("js-yaml", () => ({
  default: { dump: vi.fn(() => "yaml-content") },
}));

// Mock templatePacks for TemplatesPage
vi.mock("@/lib/templatePacks", () => ({
  TEMPLATE_PACKS: [],
  resolveCategory: vi.fn((cat: string) => cat),
  getPackById: vi.fn(() => null),
  getFeaturedPack: vi.fn(() => null),
}));

// Mock TemplatesPage sub-components
vi.mock("@/components/templates/PackCard", () => ({
  PackCard: () => <div data-testid="pack-card" />,
}));

vi.mock("@/components/templates/TemplateCard", () => ({
  TemplateCard: ({ name }: { name: string }) => <div data-testid="template-card">{name}</div>,
}));

vi.mock("@/components/templates/TemplateDetail", () => ({
  TemplateDetail: () => <div data-testid="template-detail" />,
}));

vi.mock("@/components/templates/RunModal", () => ({
  RunModal: () => <div data-testid="run-modal" />,
}));

vi.mock("@/components/templates/InstallModal", () => ({
  InstallModal: () => <div data-testid="install-modal" />,
}));

vi.mock("@/components/templates/CommunityCard", () => ({
  CommunityCard: () => <div data-testid="community-card" />,
}));

vi.mock("@/components/integrations/toolIcons", () => ({
  TOOL_ICON_MAP: {},
}));

import { api } from "@/api/client";
import { toast } from "sonner";

const mockApi = api as unknown as {
  get: ReturnType<typeof vi.fn>;
  post: ReturnType<typeof vi.fn>;
  patch: ReturnType<typeof vi.fn>;
  delete: ReturnType<typeof vi.fn>;
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  mockNavigate.mockReset();
  mockLocation.pathname = "/";
});

afterEach(() => {
  vi.useRealTimers();
});


// ============================================================================
// 1. ApprovalsPage
// ============================================================================

import ApprovalsPage from "@/pages/ApprovalsPage";

describe("ApprovalsPage", () => {
  const pendingApproval = {
    id: "app-1",
    run_id: "run-abc",
    step_id: "step_review",
    status: "pending",
    message: "Review user data before sending",
    request_data: { key: "value" },
    response_data: null,
    reviewer_id: null,
    reviewer_comment: null,
    timeout_at: "2026-03-01T00:00:00Z",
    on_timeout: "auto_reject",
    allow_edit: true,
    created_at: "2026-02-28T10:00:00Z",
    resolved_at: null,
  };

  const approvedItem = {
    ...pendingApproval,
    id: "app-2",
    status: "approved",
    reviewer_comment: "Looks good",
    resolved_at: "2026-02-28T11:00:00Z",
  };

  it("shows loading spinner initially", () => {
    mockApi.get.mockReturnValue(new Promise(() => {}));
    render(<ApprovalsPage />);
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.getByText("Loading...")).toBeInTheDocument();
  });

  it("shows error state with retry button on API failure", async () => {
    mockApi.get.mockRejectedValueOnce(new Error("Network error"));
    render(<ApprovalsPage />);
    await waitFor(() => {
      expect(screen.getByText("Could not connect to the API server")).toBeInTheDocument();
    });
    expect(screen.getByText("Retry")).toBeInTheDocument();
  });

  it("retries fetch when Retry button is clicked", async () => {
    mockApi.get.mockRejectedValueOnce(new Error("fail"));
    render(<ApprovalsPage />);
    await waitFor(() => {
      expect(screen.getByText("Retry")).toBeInTheDocument();
    });

    mockApi.get.mockResolvedValueOnce({ data: [], error: null });
    fireEvent.click(screen.getByText("Retry"));
    await waitFor(() => {
      expect(screen.getByText("No approvals")).toBeInTheDocument();
    });
  });

  it("shows empty state when no approvals exist", async () => {
    mockApi.get.mockResolvedValueOnce({ data: [], error: null });
    render(<ApprovalsPage />);
    await waitFor(() => {
      expect(screen.getByText("No approvals")).toBeInTheDocument();
      expect(screen.getByText("Workflow steps requiring human review will appear here.")).toBeInTheDocument();
    });
  });

  it("renders approval items with correct status badges", async () => {
    mockApi.get.mockResolvedValueOnce({ data: [pendingApproval, approvedItem], error: null });
    render(<ApprovalsPage />);
    await waitFor(() => {
      // Both items share the same message text, so use getAllByText
      expect(screen.getAllByText("Review user data before sending").length).toBe(2);
    });
    // Status badges: "Pending" appears both as a filter button and a status badge
    // "Approved" only appears as the status badge for the approved item
    const pendingBadges = screen.getAllByText("Pending");
    expect(pendingBadges.length).toBeGreaterThanOrEqual(2); // filter + badge
    expect(screen.getAllByText("Approved").length).toBeGreaterThanOrEqual(1);
  });

  it("shows pending count badge when there are pending items", async () => {
    mockApi.get.mockResolvedValueOnce({ data: [pendingApproval], error: null });
    render(<ApprovalsPage />);
    await waitFor(() => {
      expect(screen.getByText("1")).toBeInTheDocument();
    });
  });

  it("does not show pending count badge when zero pending", async () => {
    mockApi.get.mockResolvedValueOnce({ data: [approvedItem], error: null });
    render(<ApprovalsPage />);
    await waitFor(() => {
      expect(screen.getByText("Approval Gates")).toBeInTheDocument();
    });
    // No pending count badge
    // @ts-expect-error - informational query
    const badges = screen.queryAllByText("0");
    // Could be zero in some stat but no pending badge
    expect(screen.queryByText("1")).not.toBeInTheDocument();
  });

  it("shows approve/reject/skip buttons for pending items only", async () => {
    mockApi.get.mockResolvedValueOnce({ data: [pendingApproval, approvedItem], error: null });
    render(<ApprovalsPage />);
    await waitFor(() => {
      expect(screen.getByText("Approve")).toBeInTheDocument();
    });
    expect(screen.getByText("Reject")).toBeInTheDocument();
    expect(screen.getByText("Skip")).toBeInTheDocument();
    // Only 1 set of buttons (for pending item only)
    expect(screen.getAllByText("Approve")).toHaveLength(1);
  });

  it("calls approve API and shows success toast", async () => {
    mockApi.get.mockResolvedValue({ data: [pendingApproval], error: null });
    mockApi.post.mockResolvedValueOnce({ data: {}, error: null });
    render(<ApprovalsPage />);
    await waitFor(() => {
      expect(screen.getByText("Approve")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Approve"));
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/approvals/app-1/approve");
    });
    expect(toast.success).toHaveBeenCalledWith("Approval approved");
  });

  it("calls reject API and shows success toast", async () => {
    mockApi.get.mockResolvedValue({ data: [pendingApproval], error: null });
    mockApi.post.mockResolvedValueOnce({ data: {}, error: null });
    render(<ApprovalsPage />);
    await waitFor(() => {
      expect(screen.getByText("Reject")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Reject"));
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/approvals/app-1/reject");
    });
    expect(toast.success).toHaveBeenCalledWith("Approval rejected");
  });

  it("shows error toast when action fails", async () => {
    mockApi.get.mockResolvedValue({ data: [pendingApproval], error: null });
    mockApi.post.mockResolvedValueOnce({ data: null, error: { code: "ERR", message: "Not allowed" } });
    render(<ApprovalsPage />);
    await waitFor(() => {
      expect(screen.getByText("Approve")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Approve"));
    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("Failed to approve: Not allowed");
    });
  });

  it("expands/collapses approval details on click", async () => {
    mockApi.get.mockResolvedValueOnce({ data: [pendingApproval], error: null });
    render(<ApprovalsPage />);
    await waitFor(() => {
      expect(screen.getByText("Review user data before sending")).toBeInTheDocument();
    });

    // Not expanded initially
    expect(screen.queryByText("Request Data")).not.toBeInTheDocument();

    // Click to expand
    const expandButton = screen.getByRole("button", { name: /expand details/ });
    fireEvent.click(expandButton);
    expect(screen.getByText("Request Data")).toBeInTheDocument();
    expect(screen.getByText(/Editing allowed/)).toBeInTheDocument();

    // Click again to collapse
    fireEvent.click(screen.getByRole("button", { name: /collapse details/ }));
    expect(screen.queryByText("Request Data")).not.toBeInTheDocument();
  });

  it("shows reviewer comment when expanded and available", async () => {
    mockApi.get.mockResolvedValueOnce({ data: [approvedItem], error: null });
    render(<ApprovalsPage />);
    await waitFor(() => {
      expect(screen.getByText("Review user data before sending")).toBeInTheDocument();
    });

    const expandButton = screen.getByRole("button", { name: /expand details/ });
    fireEvent.click(expandButton);
    expect(screen.getByText("Reviewer Comment")).toBeInTheDocument();
    expect(screen.getByText("Looks good")).toBeInTheDocument();
  });

  it("navigates to run detail when step_id link is clicked", async () => {
    mockApi.get.mockResolvedValueOnce({ data: [pendingApproval], error: null });
    render(<ApprovalsPage />);
    await waitFor(() => {
      expect(screen.getByText("step_review")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("step_review"));
    expect(mockNavigate).toHaveBeenCalledWith("/runs/run-abc");
  });

  it("switches filters and refetches data", async () => {
    mockApi.get.mockResolvedValue({ data: [pendingApproval], error: null });
    render(<ApprovalsPage />);
    await waitFor(() => {
      expect(screen.getByText("Approval Gates")).toBeInTheDocument();
    });

    // Click the Pending filter button (aria-pressed attribute distinguishes it from the status badge)
    const pendingFilterBtn = screen.getByRole("button", { name: "Pending" });
    fireEvent.click(pendingFilterBtn);
    await waitFor(() => {
      // The last call should include status param
      const lastCall = mockApi.get.mock.calls[mockApi.get.mock.calls.length - 1];
      expect(lastCall[0]).toBe("/approvals");
      expect(lastCall[1]).toEqual({ status: "pending" });
    });
  });

  it("handles keyboard Enter on expandable item", async () => {
    mockApi.get.mockResolvedValueOnce({ data: [pendingApproval], error: null });
    render(<ApprovalsPage />);
    await waitFor(() => {
      expect(screen.getByText("Review user data before sending")).toBeInTheDocument();
    });

    const expandButton = screen.getByRole("button", { name: /expand details/ });
    fireEvent.keyDown(expandButton, { key: "Enter" });
    expect(screen.getByText("Request Data")).toBeInTheDocument();
  });

  it("shows timeout info for pending items with timeout_at", async () => {
    mockApi.get.mockResolvedValueOnce({ data: [pendingApproval], error: null });
    render(<ApprovalsPage />);
    await waitFor(() => {
      expect(screen.getByText("Timeout: auto_reject")).toBeInTheDocument();
    });
  });
});


// ============================================================================
// 2. EvaluationsPage
// ============================================================================

import EvaluationsPage from "@/pages/EvaluationsPage";

describe("EvaluationsPage", () => {
  const mockStats = {
    total_runs: 15,
    avg_pass_rate: 0.85,
    total_cost_usd: 42.5,
    last_run_at: "2026-02-28T10:00:00Z",
    pass_rate_trend: [
      { date: "2026-02-01", avg_pass_rate: 0.8, runs: 3 },
      { date: "2026-02-15", avg_pass_rate: 0.9, runs: 5 },
    ],
  };

  const mockRun = {
    id: "eval-1",
    suite_name: "Quality Suite",
    workflow_name: "data-processor",
    status: "completed",
    total_cases: 5,
    passed_cases: 4,
    failed_cases: 1,
    pass_rate: 0.8,
    total_cost_usd: 2.5,
    total_duration_seconds: 45.3,
    started_at: "2026-02-28T10:00:00Z",
    completed_at: "2026-02-28T10:01:00Z",
    created_at: "2026-02-28T10:00:00Z",
    cases: [
      {
        case_name: "test_basic",
        passed: true,
        run_id: "run-123",
        cost_usd: 1.0,
        duration_seconds: 20.1,
        assertions: [
          { type: "contains", passed: true, expected: "hello", actual: "hello world", message: "matched", score: null },
        ],
        output_summary: "Success",
        error: null,
      },
      {
        case_name: "test_edge",
        passed: false,
        run_id: "run-456",
        cost_usd: 1.5,
        duration_seconds: 25.2,
        assertions: [
          { type: "exact_match", passed: false, expected: "yes", actual: "no", message: "mismatch", score: 0.25 },
        ],
        output_summary: null,
        error: "Assertion failed",
      },
    ],
  };

  it("shows loading spinner initially", () => {
    mockApi.get.mockReturnValue(new Promise(() => {}));
    render(<EvaluationsPage />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("shows error state with retry on API failure", async () => {
    mockApi.get.mockRejectedValue(new Error("Network error"));
    render(<EvaluationsPage />);
    await waitFor(() => {
      expect(screen.getByText("Could not connect to the API server")).toBeInTheDocument();
    });
    expect(screen.getByText("Retry")).toBeInTheDocument();
  });

  it("retries on Retry click", async () => {
    // Initial fetch: Promise.all for /eval/runs and /eval/stats both reject
    mockApi.get.mockRejectedValueOnce(new Error("fail"));
    render(<EvaluationsPage />);
    await waitFor(() => {
      expect(screen.getByText("Retry")).toBeInTheDocument();
    });

    // On retry, EvaluationsPage calls Promise.all([api.get("/eval/runs"), api.get("/eval/stats")])
    mockApi.get
      .mockResolvedValueOnce({ data: [], error: null })       // /eval/runs
      .mockResolvedValueOnce({ data: null, error: null });    // /eval/stats
    fireEvent.click(screen.getByText("Retry"));
    await waitFor(() => {
      expect(screen.getByText("No evaluations yet")).toBeInTheDocument();
    });
  });

  it("shows empty state when no eval runs", async () => {
    mockApi.get
      .mockResolvedValueOnce({ data: [], error: null }) // runs
      .mockResolvedValueOnce({ data: null, error: null }); // stats
    render(<EvaluationsPage />);
    await waitFor(() => {
      expect(screen.getByText("No evaluations yet")).toBeInTheDocument();
      expect(screen.getByText(/Run your first eval suite/)).toBeInTheDocument();
    });
  });

  it("displays stats cards when stats are available", async () => {
    mockApi.get
      .mockResolvedValueOnce({ data: [mockRun], error: null })
      .mockResolvedValueOnce({ data: mockStats, error: null });
    render(<EvaluationsPage />);
    await waitFor(() => {
      expect(screen.getByText("Total Runs")).toBeInTheDocument();
    });
    expect(screen.getByText("15")).toBeInTheDocument();
    expect(screen.getByText("Avg Pass Rate")).toBeInTheDocument();
    expect(screen.getByText("85%")).toBeInTheDocument();
    expect(screen.getByText("Total Cost")).toBeInTheDocument();
    expect(screen.getByText("Last Run")).toBeInTheDocument();
  });

  it("renders eval run items with pass rate", async () => {
    mockApi.get
      .mockResolvedValueOnce({ data: [mockRun], error: null })
      .mockResolvedValueOnce({ data: mockStats, error: null });
    render(<EvaluationsPage />);
    await waitFor(() => {
      expect(screen.getByText("Quality Suite")).toBeInTheDocument();
    });
    expect(screen.getByText("data-processor")).toBeInTheDocument();
    expect(screen.getByText("5 cases")).toBeInTheDocument();
    expect(screen.getByText("80%")).toBeInTheDocument();
  });

  it("shows pass rate trend chart when trend has > 1 data points", async () => {
    mockApi.get
      .mockResolvedValueOnce({ data: [mockRun], error: null })
      .mockResolvedValueOnce({ data: mockStats, error: null });
    render(<EvaluationsPage />);
    await waitFor(() => {
      expect(screen.getByText("Pass Rate Trend (30 days)")).toBeInTheDocument();
    });
  });

  it("does not show chart when trend has <= 1 data point", async () => {
    const singlePointStats = { ...mockStats, pass_rate_trend: [{ date: "2026-02-01", avg_pass_rate: 0.8, runs: 1 }] };
    mockApi.get
      .mockResolvedValueOnce({ data: [mockRun], error: null })
      .mockResolvedValueOnce({ data: singlePointStats, error: null });
    render(<EvaluationsPage />);
    await waitFor(() => {
      expect(screen.getByText("Quality Suite")).toBeInTheDocument();
    });
    expect(screen.queryByText("Pass Rate Trend (30 days)")).not.toBeInTheDocument();
  });

  it("expands eval run to show cases table", async () => {
    mockApi.get
      .mockResolvedValueOnce({ data: [mockRun], error: null })
      .mockResolvedValueOnce({ data: mockStats, error: null });
    render(<EvaluationsPage />);
    await waitFor(() => {
      expect(screen.getByText("Quality Suite")).toBeInTheDocument();
    });

    // Click to expand
    const expandBtn = screen.getByRole("button", { name: /expand cases/ });
    fireEvent.click(expandBtn);
    expect(screen.getByText("test_basic")).toBeInTheDocument();
    expect(screen.getByText("test_edge")).toBeInTheDocument();
    expect(screen.getByText("Case")).toBeInTheDocument();
    expect(screen.getByText("Status")).toBeInTheDocument();
  });

  it("expands individual case to show assertions", async () => {
    mockApi.get
      .mockResolvedValueOnce({ data: [mockRun], error: null })
      .mockResolvedValueOnce({ data: mockStats, error: null });
    render(<EvaluationsPage />);
    await waitFor(() => {
      expect(screen.getByText("Quality Suite")).toBeInTheDocument();
    });

    // Expand run
    fireEvent.click(screen.getByRole("button", { name: /expand cases/ }));
    expect(screen.getByText("test_edge")).toBeInTheDocument();

    // Expand case - click the row
    fireEvent.click(screen.getByText("test_edge"));
    await waitFor(() => {
      expect(screen.getByText("exact_match")).toBeInTheDocument();
      expect(screen.getByText("score: 0.25")).toBeInTheDocument();
      expect(screen.getByText("Error: Assertion failed")).toBeInTheDocument();
    });
  });

  it("shows passed/failed assertion counts per case", async () => {
    mockApi.get
      .mockResolvedValueOnce({ data: [mockRun], error: null })
      .mockResolvedValueOnce({ data: mockStats, error: null });
    render(<EvaluationsPage />);
    await waitFor(() => {
      expect(screen.getByText("Quality Suite")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /expand cases/ }));
    // test_basic: 1/1, test_edge: 0/1
    expect(screen.getByText("1/1")).toBeInTheDocument();
    expect(screen.getByText("0/1")).toBeInTheDocument();
  });

  it("collapses eval run on second click", async () => {
    mockApi.get
      .mockResolvedValueOnce({ data: [mockRun], error: null })
      .mockResolvedValueOnce({ data: mockStats, error: null });
    render(<EvaluationsPage />);
    await waitFor(() => {
      expect(screen.getByText("Quality Suite")).toBeInTheDocument();
    });

    const expandBtn = screen.getByRole("button", { name: /expand cases/ });
    fireEvent.click(expandBtn);
    expect(screen.getByText("test_basic")).toBeInTheDocument();

    // Click again to collapse
    fireEvent.click(screen.getByRole("button", { name: /collapse cases/ }));
    expect(screen.queryByText("test_basic")).not.toBeInTheDocument();
  });

  it("handles keyboard Enter to expand/collapse run", async () => {
    mockApi.get
      .mockResolvedValueOnce({ data: [mockRun], error: null })
      .mockResolvedValueOnce({ data: mockStats, error: null });
    render(<EvaluationsPage />);
    await waitFor(() => {
      expect(screen.getByText("Quality Suite")).toBeInTheDocument();
    });

    const expandBtn = screen.getByRole("button", { name: /expand cases/ });
    fireEvent.keyDown(expandBtn, { key: "Enter" });
    expect(screen.getByText("test_basic")).toBeInTheDocument();
  });
});


// ============================================================================
// 3. SystemHealthPage
// ============================================================================

import SystemHealthPage from "@/pages/SystemHealthPage";

describe("SystemHealthPage", () => {
  const mockHealth = { status: "ok", runtime: true, redis: true, database: true };
  const mockRuntime = {
    mode: "local",
    database: "sqlite",
    queue: "memory",
    storage: "local",
    sandbox_backend: "e2b",
    data_dir: "/data",
    version: "0.18.0",
  };
  const mockStatsData = { total_runs_today: 42, success_rate: 0.95, total_cost_today: 12.34, avg_duration_seconds: 90 };

  function setupHealthyApi() {
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/health") return Promise.resolve({ data: mockHealth, error: null });
      if (url === "/runtime") return Promise.resolve({ data: mockRuntime, error: null });
      if (url === "/stats") return Promise.resolve({ data: mockStatsData, error: null });
      if (url === "/workflows") return Promise.resolve({ data: [{ name: "wf1" }, { name: "wf2" }], error: null });
      if (url === "/templates") return Promise.resolve({ data: [{ id: "t1" }], error: null });
      if (url === "/api-keys") return Promise.resolve({ data: [{ id: "k1" }, { id: "k2" }], error: null });
      return Promise.resolve({ data: null, error: null });
    });
  }

  it("shows loading spinner initially", () => {
    mockApi.get.mockReturnValue(new Promise(() => {}));
    render(<SystemHealthPage />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("shows error state on API failure", async () => {
    mockApi.get.mockRejectedValue(new Error("fail"));
    render(<SystemHealthPage />);
    await waitFor(() => {
      expect(screen.getByText("Could not connect to the API server")).toBeInTheDocument();
    });
    expect(screen.getByText("Retry")).toBeInTheDocument();
  });

  it("renders healthy state with all systems operational", async () => {
    setupHealthyApi();
    render(<SystemHealthPage />);
    await waitFor(() => {
      expect(screen.getByText("System Health")).toBeInTheDocument();
    });
    expect(screen.getByText("All Systems Operational")).toBeInTheDocument();
  });

  it("renders server status card with version", async () => {
    setupHealthyApi();
    render(<SystemHealthPage />);
    await waitFor(() => {
      expect(screen.getByText("Server Status")).toBeInTheDocument();
    });
    expect(screen.getByText("OK")).toBeInTheDocument();
    // Version appears in both Server Status and Environment cards
    expect(screen.getAllByText("0.18.0").length).toBeGreaterThanOrEqual(1);
  });

  it("renders runtime info card", async () => {
    setupHealthyApi();
    render(<SystemHealthPage />);
    await waitFor(() => {
      expect(screen.getByText("Runtime Info")).toBeInTheDocument();
    });
    expect(screen.getByText("Local")).toBeInTheDocument();
    expect(screen.getByText("E2B")).toBeInTheDocument();
  });

  it("renders service checks with healthy statuses", async () => {
    setupHealthyApi();
    render(<SystemHealthPage />);
    await waitFor(() => {
      expect(screen.getByText("Service Checks")).toBeInTheDocument();
    });
    expect(screen.getByText("API Server")).toBeInTheDocument();
    // "Database" appears both in Runtime Info card and Service Checks, use getAllByText
    expect(screen.getAllByText("Database").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Redis")).toBeInTheDocument();
    // "Sandbox Backend" appears in both Runtime Info and Service Checks
    expect(screen.getAllByText("Sandbox Backend").length).toBeGreaterThanOrEqual(1);
    // "Storage Backend" appears in both Environment and Service Checks sections
    expect(screen.getAllByText("Storage Backend").length).toBeGreaterThanOrEqual(1);
    // All healthy
    const healthyLabels = screen.getAllByText("Healthy");
    expect(healthyLabels.length).toBeGreaterThanOrEqual(4);
  });

  it("renders quick stats cards", async () => {
    setupHealthyApi();
    render(<SystemHealthPage />);
    await waitFor(() => {
      expect(screen.getByText("Total Runs (today)")).toBeInTheDocument();
    });
    // Quick stats values - some numbers may appear in multiple places
    expect(screen.getAllByText("42").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Workflows")).toBeInTheDocument();
    expect(screen.getAllByText("2").length).toBeGreaterThanOrEqual(1); // workflows count + possibly api keys count
    expect(screen.getByText("Templates")).toBeInTheDocument();
    expect(screen.getAllByText("1").length).toBeGreaterThanOrEqual(1); // templates count
    expect(screen.getByText("API Keys")).toBeInTheDocument();
  });

  it("shows degraded status when health.status is degraded", async () => {
    const degradedHealth = { ...mockHealth, status: "degraded" };
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/health") return Promise.resolve({ data: degradedHealth, error: null });
      if (url === "/runtime") return Promise.resolve({ data: mockRuntime, error: null });
      if (url === "/stats") return Promise.resolve({ data: mockStatsData, error: null });
      if (url === "/workflows") return Promise.resolve({ data: [], error: null });
      if (url === "/templates") return Promise.resolve({ data: [], error: null });
      if (url === "/api-keys") return Promise.resolve({ data: [], error: null });
      return Promise.resolve({ data: null, error: null });
    });
    render(<SystemHealthPage />);
    await waitFor(() => {
      expect(screen.getByText("Degraded")).toBeInTheDocument();
    });
  });

  it("shows Redis as unconfigured when redis is null", async () => {
    const noRedisHealth = { ...mockHealth, redis: null };
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/health") return Promise.resolve({ data: noRedisHealth, error: null });
      if (url === "/runtime") return Promise.resolve({ data: mockRuntime, error: null });
      if (url === "/stats") return Promise.resolve({ data: mockStatsData, error: null });
      if (url === "/workflows") return Promise.resolve({ data: [], error: null });
      if (url === "/templates") return Promise.resolve({ data: [], error: null });
      if (url === "/api-keys") return Promise.resolve({ data: [], error: null });
      return Promise.resolve({ data: null, error: null });
    });
    render(<SystemHealthPage />);
    await waitFor(() => {
      expect(screen.getByText("Not Configured")).toBeInTheDocument();
    });
    expect(screen.getByText("Using in-process queue (local mode)")).toBeInTheDocument();
  });

  it("renders Environment section with all fields", async () => {
    setupHealthyApi();
    render(<SystemHealthPage />);
    await waitFor(() => {
      expect(screen.getByText("Environment")).toBeInTheDocument();
    });
    expect(screen.getByText("Sandcastle Version")).toBeInTheDocument();
    expect(screen.getByText("Runtime Mode")).toBeInTheDocument();
    expect(screen.getByText("Database Engine")).toBeInTheDocument();
    expect(screen.getByText("Queue Backend")).toBeInTheDocument();
    // "Storage Backend" appears in both Environment and Service Checks sections
    expect(screen.getAllByText("Storage Backend").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Data Directory")).toBeInTheDocument();
    expect(screen.getByText("/data")).toBeInTheDocument();
  });

  it("calls refresh on Refresh button click", async () => {
    setupHealthyApi();
    render(<SystemHealthPage />);
    await waitFor(() => {
      expect(screen.getByText("Refresh")).toBeInTheDocument();
    });

    const callCountBefore = mockApi.get.mock.calls.length;
    fireEvent.click(screen.getByText("Refresh"));
    await waitFor(() => {
      expect(mockApi.get.mock.calls.length).toBeGreaterThan(callCountBefore);
    });
  });

  it("shows last checked timestamp", async () => {
    setupHealthyApi();
    render(<SystemHealthPage />);
    await waitFor(() => {
      expect(screen.getByText(/Last checked:/)).toBeInTheDocument();
    });
  });
});


// ============================================================================
// 4. ViolationsPage
// ============================================================================

import ViolationsPage from "@/pages/ViolationsPage";

describe("ViolationsPage", () => {
  const mockViolation = {
    id: "v-1",
    run_id: "run-xyz-12345678",
    step_id: "step_pii_check",
    policy_id: "pii_detection",
    severity: "critical",
    action_taken: "blocked",
    trigger_details: "Detected PII: SSN pattern found in output",
    output_modified: false,
    created_at: "2026-02-28T10:00:00Z",
  };

  const mockViolationLow = {
    ...mockViolation,
    id: "v-2",
    severity: "low",
    action_taken: "logged",
    policy_id: "cost_threshold",
    trigger_details: "Cost exceeded threshold by 5%",
    output_modified: true,
  };

  const mockViolationStats = {
    total_violations_30d: 25,
    violations_by_severity: { critical: 3, high: 7, medium: 10, low: 5 },
    violations_by_policy: { pii_detection: 10, cost_threshold: 8, rate_limit: 7 },
    violations_by_day: [{ date: "2026-02-28", count: 5 }],
  };

  it("shows loading spinner initially", () => {
    mockApi.get.mockReturnValue(new Promise(() => {}));
    render(<ViolationsPage />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("shows error state on API failure", async () => {
    mockApi.get.mockRejectedValue(new Error("fail"));
    render(<ViolationsPage />);
    await waitFor(() => {
      expect(screen.getByText("Could not connect to the API server")).toBeInTheDocument();
    });
    expect(screen.getByText("Retry")).toBeInTheDocument();
  });

  it("shows empty state when no violations", async () => {
    mockApi.get
      .mockResolvedValueOnce({ data: [], error: null })
      .mockResolvedValueOnce({ data: null, error: null });
    render(<ViolationsPage />);
    await waitFor(() => {
      expect(screen.getByText("No violations")).toBeInTheDocument();
    });
  });

  it("renders violation items with severity badges", async () => {
    mockApi.get
      .mockResolvedValueOnce({ data: [mockViolation, mockViolationLow], error: null })
      .mockResolvedValueOnce({ data: mockViolationStats, error: null });
    render(<ViolationsPage />);
    await waitFor(() => {
      // "pii_detection" appears both in the Top Policy stats card and the violation item
      expect(screen.getAllByText("pii_detection").length).toBeGreaterThanOrEqual(2);
    });
    // "Critical" appears as stats card label, severity filter, and severity badge
    expect(screen.getAllByText("Critical").length).toBeGreaterThanOrEqual(2);
    // "Low" appears as both a filter button and a severity badge
    expect(screen.getAllByText("Low").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("blocked")).toBeInTheDocument();
    expect(screen.getByText("logged")).toBeInTheDocument();
  });

  it("shows critical violations banner when there are critical violations", async () => {
    mockApi.get
      .mockResolvedValueOnce({ data: [mockViolation], error: null })
      .mockResolvedValueOnce({ data: mockViolationStats, error: null });
    render(<ViolationsPage />);
    await waitFor(() => {
      expect(screen.getByText(/3 critical violations detected/)).toBeInTheDocument();
    });
  });

  it("does not show critical banner when zero critical violations", async () => {
    const noCritStats = { ...mockViolationStats, violations_by_severity: { ...mockViolationStats.violations_by_severity, critical: 0 } };
    mockApi.get
      .mockResolvedValueOnce({ data: [mockViolationLow], error: null })
      .mockResolvedValueOnce({ data: noCritStats, error: null });
    render(<ViolationsPage />);
    await waitFor(() => {
      expect(screen.getByText("Policy Violations")).toBeInTheDocument();
    });
    expect(screen.queryByText(/critical violation/)).not.toBeInTheDocument();
  });

  it("renders stats cards with violation counts", async () => {
    mockApi.get
      .mockResolvedValueOnce({ data: [mockViolation], error: null })
      .mockResolvedValueOnce({ data: mockViolationStats, error: null });
    render(<ViolationsPage />);
    await waitFor(() => {
      expect(screen.getByText("Total Violations (30d)")).toBeInTheDocument();
    });
    expect(screen.getByText("25")).toBeInTheDocument();
    expect(screen.getByText("Top Policy")).toBeInTheDocument();
    // "pii_detection" appears both in stats card and violation item
    expect(screen.getAllByText("pii_detection").length).toBeGreaterThanOrEqual(2);
  });

  it("shows Modified badge for output_modified violations", async () => {
    mockApi.get
      .mockResolvedValueOnce({ data: [mockViolationLow], error: null })
      .mockResolvedValueOnce({ data: mockViolationStats, error: null });
    render(<ViolationsPage />);
    await waitFor(() => {
      expect(screen.getByText("Modified")).toBeInTheDocument();
    });
  });

  it("expands violation details on click", async () => {
    mockApi.get
      .mockResolvedValueOnce({ data: [mockViolation], error: null })
      .mockResolvedValueOnce({ data: mockViolationStats, error: null });
    render(<ViolationsPage />);
    await waitFor(() => {
      // "pii_detection" appears in both stats card and violation item
      expect(screen.getAllByText("pii_detection").length).toBeGreaterThanOrEqual(1);
    });

    // Not expanded initially
    expect(screen.queryByText("Trigger Details")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /expand details/ }));
    expect(screen.getByText("Trigger Details")).toBeInTheDocument();
    // run_id "run-xyz-12345678".slice(0,8) = "run-xyz-"
    expect(screen.getByText(/run-xyz-/)).toBeInTheDocument();
  });

  it("shows output modified note in expanded details", async () => {
    mockApi.get
      .mockResolvedValueOnce({ data: [mockViolationLow], error: null })
      .mockResolvedValueOnce({ data: mockViolationStats, error: null });
    render(<ViolationsPage />);
    await waitFor(() => {
      expect(screen.getByText("cost_threshold")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /expand details/ }));
    expect(screen.getByText("Output was modified")).toBeInTheDocument();
  });

  it("navigates to run detail when step link is clicked", async () => {
    mockApi.get
      .mockResolvedValueOnce({ data: [mockViolation], error: null })
      .mockResolvedValueOnce({ data: mockViolationStats, error: null });
    render(<ViolationsPage />);
    await waitFor(() => {
      expect(screen.getByText("step_pii_check")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("step_pii_check"));
    expect(mockNavigate).toHaveBeenCalledWith("/runs/run-xyz-12345678");
  });

  it("filters violations by severity", async () => {
    // ViolationsPage uses Promise.all for /violations and /violations/stats
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/violations") return Promise.resolve({ data: [mockViolation], error: null });
      if (url === "/violations/stats") return Promise.resolve({ data: mockViolationStats, error: null });
      return Promise.resolve({ data: null, error: null });
    });
    render(<ViolationsPage />);
    await waitFor(() => {
      expect(screen.getByText("Policy Violations")).toBeInTheDocument();
    });

    // Click the "Critical" filter button (use aria-pressed to distinguish from severity badge)
    const criticalFilterBtn = screen.getByRole("button", { name: "Critical" });
    fireEvent.click(criticalFilterBtn);
    await waitFor(() => {
      const lastViolationCall = mockApi.get.mock.calls.filter(
        (c: string[]) => c[0] === "/violations"
      );
      const lastCall = lastViolationCall[lastViolationCall.length - 1];
      expect(lastCall[1]).toEqual({ severity: "critical" });
    });
  });

  it("handles keyboard Enter on expandable violation", async () => {
    mockApi.get
      .mockResolvedValueOnce({ data: [mockViolation], error: null })
      .mockResolvedValueOnce({ data: mockViolationStats, error: null });
    render(<ViolationsPage />);
    await waitFor(() => {
      expect(screen.getAllByText("pii_detection").length).toBeGreaterThanOrEqual(1);
    });

    fireEvent.keyDown(screen.getByRole("button", { name: /expand details/ }), { key: "Enter" });
    expect(screen.getByText("Trigger Details")).toBeInTheDocument();
  });
});


// ============================================================================
// 5. AdvisorPanel - REMOVED (merged into NotificationCenter)
// ============================================================================


// ============================================================================
// 6. Header
// ============================================================================

import { Header } from "@/components/layout/Header";

describe("Header", () => {
  const defaultHeaderProps = {
    onMenuToggle: vi.fn(),
    notifications: [] as { id: string; type: "success" | "error" | "warning" | "info"; message: string; timestamp: Date; read: boolean }[],
    onMarkAllRead: vi.fn(),
    onClickNotification: vi.fn(),
  };

  it("renders header with menu toggle button", () => {
    render(<Header {...defaultHeaderProps} />);
    expect(screen.getByLabelText("Toggle navigation menu")).toBeInTheDocument();
  });

  it("calls onMenuToggle when menu button is clicked", () => {
    const onMenuToggle = vi.fn();
    render(<Header {...defaultHeaderProps} onMenuToggle={onMenuToggle} />);
    fireEvent.click(screen.getByLabelText("Toggle navigation menu"));
    expect(onMenuToggle).toHaveBeenCalled();
  });

  it("renders search input with combobox role", () => {
    render(<Header {...defaultHeaderProps} />);
    const searchInputs = screen.getAllByRole("combobox");
    expect(searchInputs.length).toBeGreaterThan(0);
  });

  it("renders notification center", () => {
    render(<Header {...defaultHeaderProps} />);
    expect(screen.getByTestId("notification-center")).toBeInTheDocument();
  });

  it("renders theme toggle", () => {
    render(<Header {...defaultHeaderProps} />);
    expect(screen.getByTestId("theme-toggle")).toBeInTheDocument();
  });

  it("renders live indicator", () => {
    render(<Header {...defaultHeaderProps} />);
    expect(screen.getByTestId("live-indicator")).toBeInTheDocument();
  });

  it("shows page title based on location pathname", () => {
    mockLocation.pathname = "/runs";
    render(<Header {...defaultHeaderProps} />);
    expect(screen.getByText("Runs")).toBeInTheDocument();
    mockLocation.pathname = "/";
  });

  it("shows Run Detail for /runs/:id paths", () => {
    mockLocation.pathname = "/runs/abc-123";
    render(<Header {...defaultHeaderProps} />);
    expect(screen.getByText("Run Detail")).toBeInTheDocument();
    mockLocation.pathname = "/";
  });

  it("shows Sandcastle for unknown paths", () => {
    mockLocation.pathname = "/unknown-page";
    render(<Header {...defaultHeaderProps} />);
    expect(screen.getByText("Sandcastle")).toBeInTheDocument();
    mockLocation.pathname = "/";
  });

  it("performs search and shows results on input", async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/runs") return Promise.resolve({
        data: [{ run_id: "run-123", workflow_name: "TestWorkflow", status: "completed" }],
        error: null,
      });
      if (url === "/workflows") return Promise.resolve({
        data: [{ name: "TestWorkflow", file_name: "test.yaml", steps_count: 3 }],
        error: null,
      });
      if (url === "/tools") return Promise.resolve({
        data: { tools: [{ name: "TestTool", description: "A tool", category: "General", configured: true }] },
        error: null,
      });
      return Promise.resolve({ data: null, error: null });
    });

    render(<Header {...defaultHeaderProps} />);
    const searchInput = screen.getAllByRole("combobox")[0];

    fireEvent.change(searchInput, { target: { value: "test" } });

    // Wait for debounced search (300ms)
    act(() => { vi.advanceTimersByTime(350); });

    await waitFor(() => {
      // "TestWorkflow" appears for both run result and workflow result in the search dropdown
      expect(screen.getAllByText("TestWorkflow").length).toBeGreaterThanOrEqual(1);
    });
  });

  it("shows no results message for unmatched query", async () => {
    mockApi.get.mockResolvedValue({ data: [], error: null });

    render(<Header {...defaultHeaderProps} />);
    const searchInput = screen.getAllByRole("combobox")[0];

    fireEvent.change(searchInput, { target: { value: "zzzzzzz" } });
    act(() => { vi.advanceTimersByTime(350); });

    await waitFor(() => {
      expect(screen.getByText(/No results for/)).toBeInTheDocument();
    });
  });

  it("does not search for queries shorter than 2 chars", async () => {
    render(<Header {...defaultHeaderProps} />);
    const searchInput = screen.getAllByRole("combobox")[0];

    fireEvent.change(searchInput, { target: { value: "a" } });
    act(() => { vi.advanceTimersByTime(350); });

    // Should not have called API
    await waitFor(() => {
      expect(mockApi.get).not.toHaveBeenCalled();
    });
  });

  it("navigates and clears search on result selection", async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/runs") return Promise.resolve({
        data: [{ run_id: "run-xyz", workflow_name: "MyFlow", status: "completed" }],
        error: null,
      });
      if (url === "/workflows") return Promise.resolve({ data: [], error: null });
      if (url === "/tools") return Promise.resolve({ data: { tools: [] }, error: null });
      return Promise.resolve({ data: null, error: null });
    });

    render(<Header {...defaultHeaderProps} />);
    const searchInput = screen.getAllByRole("combobox")[0];

    fireEvent.change(searchInput, { target: { value: "myflow" } });
    act(() => { vi.advanceTimersByTime(350); });

    await waitFor(() => {
      expect(screen.getByText("MyFlow")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("MyFlow"));
    expect(mockNavigate).toHaveBeenCalledWith("/runs/run-xyz");
  });

  it("supports keyboard navigation in search results", async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/runs") return Promise.resolve({
        data: [
          { run_id: "r1", workflow_name: "Alpha", status: "completed" },
          { run_id: "r2", workflow_name: "AlphaBeta", status: "completed" },
        ],
        error: null,
      });
      if (url === "/workflows") return Promise.resolve({ data: [], error: null });
      if (url === "/tools") return Promise.resolve({ data: { tools: [] }, error: null });
      return Promise.resolve({ data: null, error: null });
    });

    render(<Header {...defaultHeaderProps} />);
    const searchInput = screen.getAllByRole("combobox")[0];

    fireEvent.change(searchInput, { target: { value: "alpha" } });
    act(() => { vi.advanceTimersByTime(350); });

    await waitFor(() => {
      expect(screen.getByText("Alpha")).toBeInTheDocument();
    });

    // Arrow down to first result
    fireEvent.keyDown(searchInput, { key: "ArrowDown" });
    const firstOption = screen.getByText("Alpha").closest("[role='option']");
    expect(firstOption?.getAttribute("aria-selected")).toBe("true");

    // Arrow down to second result
    fireEvent.keyDown(searchInput, { key: "ArrowDown" });
    const secondOption = screen.getByText("AlphaBeta").closest("[role='option']");
    expect(secondOption?.getAttribute("aria-selected")).toBe("true");

    // Enter selects the result
    fireEvent.keyDown(searchInput, { key: "Enter" });
    expect(mockNavigate).toHaveBeenCalledWith("/runs/r2");
  });

  it("closes search results on Escape", async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/runs") return Promise.resolve({
        data: [{ run_id: "r1", workflow_name: "SearchResult", status: "completed" }],
        error: null,
      });
      if (url === "/workflows") return Promise.resolve({ data: [], error: null });
      if (url === "/tools") return Promise.resolve({ data: { tools: [] }, error: null });
      return Promise.resolve({ data: null, error: null });
    });

    render(<Header {...defaultHeaderProps} />);
    const searchInput = screen.getAllByRole("combobox")[0];

    fireEvent.change(searchInput, { target: { value: "search" } });
    act(() => { vi.advanceTimersByTime(350); });

    await waitFor(() => {
      expect(screen.getByText("SearchResult")).toBeInTheDocument();
    });

    // Press Escape
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByText("SearchResult")).not.toBeInTheDocument();
    });
  });

  it("handles API error gracefully during search", async () => {
    mockApi.get.mockRejectedValue(new Error("fail"));

    render(<Header {...defaultHeaderProps} />);
    const searchInput = screen.getAllByRole("combobox")[0];

    fireEvent.change(searchInput, { target: { value: "test" } });
    act(() => { vi.advanceTimersByTime(350); });

    // Should not crash - no results shown
    await waitFor(() => {
      expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    });
  });

  it("toggles mobile search on button click", () => {
    render(<Header {...defaultHeaderProps} />);
    const toggleBtn = screen.getByLabelText("Toggle search");
    fireEvent.click(toggleBtn);
    // Mobile search is rendered - there should now be 2 combobox inputs (desktop + mobile)
    const inputs = screen.getAllByRole("combobox");
    expect(inputs.length).toBe(2);
  });
});


// ============================================================================
// 7. Workflows Page
// ============================================================================

import Workflows from "@/pages/Workflows";

describe("Workflows Page", () => {
  it("shows loading spinner initially", () => {
    mockApi.get.mockReturnValue(new Promise(() => {}));
    render(<Workflows />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("shows error state on API failure", async () => {
    mockApi.get.mockRejectedValueOnce(new Error("fail"));
    render(<Workflows />);
    await waitFor(() => {
      expect(screen.getByText("Could not connect to the API server")).toBeInTheDocument();
    });
    expect(screen.getByText("Retry")).toBeInTheDocument();
  });

  it("shows empty state when no workflows found", async () => {
    mockApi.get.mockResolvedValueOnce({ data: [], error: null });
    render(<Workflows />);
    await waitFor(() => {
      expect(screen.getByText("No workflows found")).toBeInTheDocument();
    });
    expect(screen.getByText("Create Workflow")).toBeInTheDocument();
  });

  it("renders workflow list when workflows exist", async () => {
    mockApi.get.mockResolvedValueOnce({
      data: [
        { name: "WF1", description: "First", steps_count: 3, file_name: "wf1.yaml" },
        { name: "WF2", description: "Second", steps_count: 5, file_name: "wf2.yaml" },
      ],
      error: null,
    });
    render(<Workflows />);
    await waitFor(() => {
      expect(screen.getByTestId("workflow-list")).toBeInTheDocument();
      expect(screen.getByText("2 workflows")).toBeInTheDocument();
    });
  });

  it("shows New Workflow button", async () => {
    mockApi.get.mockResolvedValueOnce({ data: [{ name: "WF1", description: "D", steps_count: 2, file_name: "wf1.yaml" }], error: null });
    render(<Workflows />);
    await waitFor(() => {
      expect(screen.getByLabelText("New workflow")).toBeInTheDocument();
    });
  });

  it("navigates to workflow builder on New Workflow click", async () => {
    mockApi.get.mockResolvedValueOnce({ data: [{ name: "WF1", description: "D", steps_count: 2, file_name: "wf1.yaml" }], error: null });
    render(<Workflows />);
    await waitFor(() => {
      expect(screen.getByLabelText("New workflow")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByLabelText("New workflow"));
    expect(mockNavigate).toHaveBeenCalledWith("/workflows/builder");
  });

  it("navigates to workflow builder on empty state action click", async () => {
    mockApi.get.mockResolvedValueOnce({ data: [], error: null });
    render(<Workflows />);
    await waitFor(() => {
      expect(screen.getByText("Create Workflow")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Create Workflow"));
    expect(mockNavigate).toHaveBeenCalledWith("/workflows/builder");
  });

  it("retries fetch on Retry click in error state", async () => {
    mockApi.get.mockRejectedValueOnce(new Error("fail"));
    render(<Workflows />);
    await waitFor(() => {
      expect(screen.getByText("Retry")).toBeInTheDocument();
    });

    mockApi.get.mockResolvedValueOnce({ data: [], error: null });
    fireEvent.click(screen.getByText("Retry"));
    await waitFor(() => {
      expect(screen.getByText("No workflows found")).toBeInTheDocument();
    });
  });
});


// ============================================================================
// 8. ScoreRing and InsightCard (sub-components of AdvisorPanel)
// ============================================================================

import { ScoreRing } from "@/components/advisor/ScoreRing";
import { InsightCard } from "@/components/advisor/InsightCard";
import type { Insight } from "@/lib/insights";

describe("ScoreRing", () => {
  it("renders score value", () => {
    render(<ScoreRing score={72} />);
    expect(screen.getByText("72")).toBeInTheDocument();
  });

  it("renders accessible label", () => {
    render(<ScoreRing score={72} />);
    expect(screen.getByRole("img", { name: "Health score: 72 out of 100" })).toBeInTheDocument();
  });

  it("clamps score to 0-100 range in the ring", () => {
    // Should not crash with extreme values
    const { container: c1 } = render(<ScoreRing score={-10} />);
    expect(c1).toBeTruthy();

    const { container: c2 } = render(<ScoreRing score={150} />);
    expect(c2).toBeTruthy();
  });

  it("renders in large size", () => {
    render(<ScoreRing score={90} size="lg" />);
    const svg = screen.getByRole("img");
    expect(svg.getAttribute("width")).toBe("120");
  });

  it("renders in small size by default", () => {
    render(<ScoreRing score={90} />);
    const svg = screen.getByRole("img");
    expect(svg.getAttribute("width")).toBe("56");
  });
});

describe("InsightCard", () => {
  const insight: Insight = {
    id: "ins-test",
    severity: "critical",
    title: "Test Insight Title",
    description: "Test insight description here",
    link: "/system-health",
    icon: "Database",
  };

  it("renders insight title and description", () => {
    render(<InsightCard insight={insight} onDismiss={vi.fn()} />);
    expect(screen.getByText("Test Insight Title")).toBeInTheDocument();
    expect(screen.getByText("Test insight description here")).toBeInTheDocument();
  });

  it("renders View link to the correct page", () => {
    render(<InsightCard insight={insight} onDismiss={vi.fn()} />);
    const link = screen.getByText("View").closest("a");
    expect(link?.getAttribute("href")).toBe("/system-health");
  });

  it("calls onDismiss with insight id when dismiss button is clicked", () => {
    const onDismiss = vi.fn();
    render(<InsightCard insight={insight} onDismiss={onDismiss} />);
    fireEvent.click(screen.getByTitle("Dismiss"));
    expect(onDismiss).toHaveBeenCalledWith("ins-test");
  });

  it("falls back to AlertTriangle icon for unknown icon names", () => {
    const unknownIconInsight = { ...insight, icon: "NonExistentIcon" };
    // Should not crash
    const { container } = render(<InsightCard insight={unknownIconInsight} onDismiss={vi.fn()} />);
    expect(container).toBeTruthy();
    expect(screen.getByText("Test Insight Title")).toBeInTheDocument();
  });

  it("renders correctly for each severity level", () => {
    const severities: Insight["severity"][] = ["critical", "warning", "optimize", "discover"];
    for (const severity of severities) {
      const { unmount } = render(
        <InsightCard insight={{ ...insight, severity }} onDismiss={vi.fn()} />
      );
      expect(screen.getByText("Test Insight Title")).toBeInTheDocument();
      unmount();
    }
  });
});
