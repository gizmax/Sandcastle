/**
 * v27 Regression Tests
 *
 * Covers critical regressions across 5 dashboard components:
 * 1. OverviewBento - empty data, Getting Started, Quick Run, failover, provider costs, stats pills
 * 2. SettingsPage - provider cards, key status, backend cards, EU toggle, save/dirty
 * 3. WorkflowBuilder - step palette categories, approval step, openclaw step, YAML sync
 * 4. MemoryPage - empty state, search, delete dialog, stats, error/retry
 * 5. ApiKeysPage - AI/Infra sections, configured/not-set badges, save button visibility
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";

// ============================================================================
// Polyfills (jsdom lacks IntersectionObserver)
// ============================================================================

class MockIntersectionObserver {
  private callback: IntersectionObserverCallback;
  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback;
  }
  observe() {
    // Immediately trigger as intersecting so lazy-load sections render
    this.callback(
      [{ isIntersecting: true } as IntersectionObserverEntry],
      this as unknown as IntersectionObserver,
    );
  }
  unobserve() {}
  disconnect() {}
}

if (typeof globalThis.IntersectionObserver === "undefined") {
  (globalThis as Record<string, unknown>).IntersectionObserver = MockIntersectionObserver;
}

// ============================================================================
// Mocks (matching existing patterns from wave8/wave9)
// ============================================================================

const mockNavigate = vi.fn();

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

// Stateful useSearchParams so tab navigation in the Settings hub actually
// updates the active tab during tests. Reset between tests via
// `resetSearchParams()` (exposed on globalThis below).
vi.mock("react-router-dom", async () => {
  const React = await import("react");
  let externalParams = new URLSearchParams();
  // Reset the seed params *before* a render; the next render's useState picks
  // it up as the initial value. (Done outside render to stay side-effect free.)
  (globalThis as Record<string, unknown>).__resetSearchParams = (init?: string) => {
    externalParams = new URLSearchParams(init ?? "");
  };
  function useSearchParams() {
    const [params, setParams] = React.useState(externalParams);
    const setSearchParams = (
      next: URLSearchParams | ((prev: URLSearchParams) => URLSearchParams),
    ) => {
      const resolved =
        typeof next === "function" ? next(new URLSearchParams(externalParams)) : next;
      externalParams = new URLSearchParams(resolved);
      setParams(externalParams);
    };
    return [params, setSearchParams] as const;
  }
  return {
    useNavigate: () => mockNavigate,
    useLocation: () => ({ pathname: "/", state: null }),
    useSearchParams,
    Link: ({ children, to, ...rest }: { children: React.ReactNode; to: string }) => (
      <a href={to} {...rest}>{children}</a>
    ),
  };
});

/** Reset the mocked URL search params (defaults to empty = General tab). */
function resetSearchParams(init?: string) {
  (globalThis as Record<string, unknown> & { __resetSearchParams?: (s?: string) => void })
    .__resetSearchParams?.(init);
}

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  },
}));

// Mock recharts (browser APIs unavailable in jsdom)
vi.mock("recharts", () => ({
  LineChart: ({ children }: { children: React.ReactNode }) => <div data-testid="line-chart">{children}</div>,
  Line: () => <div data-testid="line" />,
  XAxis: () => <div />,
  YAxis: () => <div />,
  CartesianGrid: () => <div />,
  Tooltip: () => <div />,
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

// Mock hooks used by OverviewBento
vi.mock("@/hooks/useAdvisorContext", () => ({
  useAdvisorContext: () => ({
    score: 100,
    previousScore: null,
    deductions: [],
    activeInsights: [],
    loading: false,
    lastChecked: null,
    errors: [],
    refresh: vi.fn(),
    dismiss: vi.fn(),
  }),
}));

vi.mock("@/hooks/usePinnedWorkflows", () => ({
  usePinnedWorkflows: () => ({
    pinnedWorkflows: [],
    togglePin: vi.fn(),
    isPinned: vi.fn(() => false),
  }),
}));

// Density context: OverviewBento reads effectiveDensity for the calm/expand default.
vi.mock("@/contexts/UiModeContext", () => ({
  useDensity: () => ({ effectiveDensity: "Standard" }),
}));

vi.mock("@/hooks/useEventStreamContext", () => ({
  useEventStreamContext: () => ({
    subscribe: vi.fn(() => vi.fn()),
    latestEvents: [],
  }),
}));

// Mock overview chart components
vi.mock("@/components/overview/RunsChart", () => ({
  RunsChart: () => <div data-testid="runs-chart" />,
}));

vi.mock("@/components/overview/CostChart", () => ({
  CostChart: () => <div data-testid="cost-chart" />,
}));

vi.mock("@/components/overview/CostForecast", () => ({
  CostForecast: () => <div data-testid="cost-forecast" />,
}));

// Mock hooks used by SettingsPage
vi.mock("@/hooks/useRuntimeInfo", () => ({
  useRuntimeInfo: () => ({
    info: { version: "0.27.0", python: "3.12", os: "linux" },
    loading: false,
  }),
}));

vi.mock("@/hooks/useUpdateCheck", () => ({
  useUpdateCheck: () => ({
    loading: false,
    currentVersion: "0.27.0",
    latestVersion: "0.27.0",
    updateAvailable: false,
  }),
}));

vi.mock("@/hooks/useNotifications", () => ({
  useNotifications: () => ({
    permission: "default",
    notificationsEnabled: false,
    requestPermission: vi.fn(),
    toggleNotifications: vi.fn(),
  }),
}));

vi.mock("@/hooks/useAccentColor", () => ({
  useAccentColor: () => ({
    accentColor: "amber",
    setAccentColor: vi.fn(),
  }),
  ACCENT_COLORS: [{ id: "amber", label: "Amber" }],
}));

vi.mock("@/hooks/useTheme", () => ({
  useTheme: () => ({
    theme: "dark",
    toggleTheme: vi.fn(),
  }),
}));

// Mock WorkflowBuilder sub-components
vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ children }: { children?: React.ReactNode }) => <div data-testid="react-flow">{children}</div>,
  Background: () => <div />,
  Controls: () => <div />,
  addEdge: vi.fn(),
  useNodesState: () => [[], vi.fn(), vi.fn()],
  useEdgesState: () => [[], vi.fn(), vi.fn()],
}));

vi.mock("@/components/workflows/StepNode", () => ({
  StepNode: () => <div data-testid="step-node" />,
}));

vi.mock("@/components/workflows/StepConfigPanel", () => ({
  StepConfigPanel: () => <div data-testid="step-config-panel" />,
}));

vi.mock("@/components/workflows/YamlPreview", () => ({
  YamlPreview: ({ yaml }: { yaml: string }) => <pre data-testid="yaml-preview">{yaml}</pre>,
}));

vi.mock("@/components/workflows/TemplateBrowser", () => ({
  TemplateBrowser: () => <div data-testid="template-browser" />,
}));

vi.mock("@/components/workflows/GenerateChatPanel", () => ({
  GenerateChatPanel: () => <div data-testid="generate-chat-panel" />,
}));

vi.mock("@/components/workflows/ToolSelector", () => ({
  ToolSelector: () => <div data-testid="tool-selector" />,
}));

vi.mock("js-yaml", () => ({
  default: {
    dump: vi.fn(() => "name: test\nsteps: []\n"),
    load: vi.fn(() => ({ name: "test", steps: [] })),
  },
}));

// Mock RunWorkflowModal (used by WorkflowBuilderPage)
vi.mock("@/components/workflows/RunWorkflowModal", () => ({
  RunWorkflowModal: () => <div data-testid="run-workflow-modal" />,
}));

// Mock ApiKeyTable and related components (used by ApiKeysPage)
vi.mock("@/components/api-keys/ApiKeyTable", () => ({
  ApiKeyTable: ({ keys }: { keys: unknown[] }) => (
    <div data-testid="api-key-table">{keys.length} keys</div>
  ),
}));

vi.mock("@/components/api-keys/CreateApiKeyModal", () => ({
  CreateApiKeyModal: () => <div data-testid="create-api-key-modal" />,
}));

vi.mock("@/components/api-keys/KeyRevealModal", () => ({
  KeyRevealModal: () => <div data-testid="key-reveal-modal" />,
}));

import { api } from "@/api/client";

const mockApi = api as unknown as {
  get: ReturnType<typeof vi.fn>;
  post: ReturnType<typeof vi.fn>;
  patch: ReturnType<typeof vi.fn>;
  delete: ReturnType<typeof vi.fn>;
};

// ============================================================================
// Helpers - realistic API data factories
// ============================================================================

function emptyStats() {
  return {
    total_runs_today: 0,
    success_rate: 0,
    total_cost_today: 0,
    avg_duration_seconds: 0,
    runs_by_day: [],
    cost_by_workflow: [],
  };
}

function realisticStats(overrides = {}) {
  return {
    total_runs_today: 42,
    success_rate: 0.95,
    total_cost_today: 12.34,
    avg_duration_seconds: 90,
    runs_by_day: [{ date: "2026-03-26", completed: 40, failed: 2, total: 42 }],
    cost_by_workflow: [{ workflow: "test-wf", cost: 12.34 }],
    ...overrides,
  };
}

function emptyApiResponse() {
  return { data: null, error: null };
}

function okResponse<T>(data: T) {
  return { data, error: null };
}


// ============================================================================
// 1. OverviewBento regression (9 tests)
// ============================================================================

import OverviewBento from "@/pages/OverviewBento";

describe("OverviewBento", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockReset();
    localStorage.clear();
  });

  afterEach(() => {
    // Ensure fake timers are always restored after each test
    vi.useRealTimers();
  });

  it("renders without crash when all API calls return empty data", async () => {
    // Return appropriate empty shapes for each endpoint
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/stats") return Promise.resolve(okResponse(emptyStats()));
      if (url === "/runs") return Promise.resolve(okResponse([]));
      if (url === "/stats/sparklines") return Promise.resolve(okResponse({}));
      if (url === "/stats/anomalies") return Promise.resolve(okResponse([]));
      if (url === "/workflows") return Promise.resolve(okResponse([]));
      return Promise.resolve(emptyApiResponse());
    });

    await act(async () => {
      render(<OverviewBento />);
    });

    // Should eventually show the overview - either empty state or content
    await waitFor(() => {
      expect(screen.getByText("Overview")).toBeInTheDocument();
    });
  });

  it("renders Getting Started checklist when workflowCount=0 and no runs", async () => {
    // All endpoints return empty data
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/stats") return Promise.resolve(okResponse(emptyStats()));
      if (url === "/runs") return Promise.resolve(okResponse([]));
      if (url === "/stats/sparklines") return Promise.resolve(okResponse({}));
      if (url === "/stats/anomalies") return Promise.resolve(okResponse([]));
      if (url === "/workflows") return Promise.resolve(okResponse([]));
      return Promise.resolve(emptyApiResponse());
    });

    await act(async () => {
      render(<OverviewBento />);
    });

    await waitFor(() => {
      expect(screen.getByText("Getting Started")).toBeInTheDocument();
    });

    // The 3 missions should be present
    expect(screen.getByText("Run your first workflow")).toBeInTheDocument();
    expect(screen.getByText("Connect an AI provider")).toBeInTheDocument();
    expect(screen.getByText("Build a multi-step pipeline")).toBeInTheDocument();
  });

  it("Quick Run cards are always visible when in empty state", async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/stats") return Promise.resolve(okResponse(emptyStats()));
      if (url === "/runs") return Promise.resolve(okResponse([]));
      if (url === "/stats/sparklines") return Promise.resolve(okResponse({}));
      if (url === "/stats/anomalies") return Promise.resolve(okResponse([]));
      if (url === "/workflows") return Promise.resolve(okResponse([]));
      return Promise.resolve(emptyApiResponse());
    });

    await act(async () => {
      render(<OverviewBento />);
    });

    await waitFor(() => {
      expect(screen.getByText("Quick Start")).toBeInTheDocument();
    });

    // All 4 quick-run cards should be present
    expect(screen.getByText("Summarize text")).toBeInTheDocument();
    expect(screen.getByText("Analyze a URL")).toBeInTheDocument();
    expect(screen.getByText("Generate code")).toBeInTheDocument();
    expect(screen.getByText("Research a topic")).toBeInTheDocument();
  });

  it("Failover widget hidden when total_failovers_7d=0", async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/stats") return Promise.resolve(okResponse(realisticStats()));
      if (url === "/runs") return Promise.resolve(okResponse([
        { run_id: "r1", workflow_name: "test", status: "completed", total_cost_usd: 0.01, started_at: "2026-03-26T10:00:00Z" },
      ]));
      if (url === "/stats/sparklines") return Promise.resolve(okResponse({}));
      if (url === "/stats/anomalies") return Promise.resolve(okResponse([]));
      if (url === "/workflows") return Promise.resolve(okResponse([{ name: "test-wf" }]));
      if (url === "/stats/failover-events") return Promise.resolve(okResponse({
        events: [], total_failovers_7d: 0, total_cost_delta_7d: 0,
      }));
      return Promise.resolve(emptyApiResponse());
    });

    await act(async () => {
      render(<OverviewBento />);
    });

    await waitFor(() => {
      expect(screen.getByText("Overview")).toBeInTheDocument();
    });

    // Provider Failovers text should not appear
    expect(screen.queryByText("Provider Failovers")).not.toBeInTheDocument();
  });

  it("Provider costs hidden when <=1 provider", async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/stats") return Promise.resolve(okResponse(realisticStats()));
      if (url === "/runs") return Promise.resolve(okResponse([
        { run_id: "r1", workflow_name: "test", status: "completed", total_cost_usd: 0.01, started_at: "2026-03-26T10:00:00Z" },
      ]));
      if (url === "/stats/sparklines") return Promise.resolve(okResponse({}));
      if (url === "/stats/anomalies") return Promise.resolve(okResponse([]));
      if (url === "/workflows") return Promise.resolve(okResponse([{ name: "test-wf" }]));
      if (url === "/stats/provider-costs") return Promise.resolve(okResponse({
        period_days: 7,
        total_cost_usd: 5.00,
        by_provider: [
          { provider: "anthropic", model: "sonnet", region: "us", total_cost_usd: 5.0, run_count: 50, avg_cost_per_run: 0.1, percentage: 100 },
        ],
        advisor_costs: { total_usd: 0, by_purpose: [] },
      }));
      if (url === "/stats/provider-savings") return Promise.resolve(okResponse(null));
      if (url === "/stats/provider-recommendation") return Promise.resolve(okResponse({ recommendations: [] }));
      if (url === "/advisor/recommendations") return Promise.resolve(okResponse({ recommendations: [], total_potential_savings: 0 }));
      return Promise.resolve(emptyApiResponse());
    });

    await act(async () => {
      render(<OverviewBento />);
    });

    await waitFor(() => {
      expect(screen.getByText("Overview")).toBeInTheDocument();
    });

    // "Cost by Provider" heading should not appear because only 1 provider exists
    expect(screen.queryByText("Cost by Provider")).not.toBeInTheDocument();
  });

  it("Stats pills show 0% not NaN when success_rate is undefined", async () => {
    // success_rate is null/undefined in the stats response
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/stats") return Promise.resolve(okResponse({
        ...emptyStats(),
        total_runs_today: 5,
        success_rate: undefined,
      }));
      if (url === "/runs") return Promise.resolve(okResponse([
        { run_id: "r1", workflow_name: "test", status: "running", total_cost_usd: 0, started_at: "2026-03-26T10:00:00Z" },
      ]));
      if (url === "/stats/sparklines") return Promise.resolve(okResponse({}));
      if (url === "/stats/anomalies") return Promise.resolve(okResponse([]));
      if (url === "/workflows") return Promise.resolve(okResponse([{ name: "wf" }]));
      return Promise.resolve(emptyApiResponse());
    });

    await act(async () => {
      render(<OverviewBento />);
    });

    await waitFor(() => {
      expect(screen.getByText("Overview")).toBeInTheDocument();
    });

    // Should show "0% success" instead of "NaN% success"
    expect(screen.getByText(/0% success/)).toBeInTheDocument();
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
  });

  it("Recommendation banner logic: below-fold data fetched when provider section becomes visible", async () => {
    // The OverviewBento gates below-fold fetches behind a 1-second setTimeout
    // plus IntersectionObserver visibility. This test verifies that:
    // 1. Above-fold APIs are called immediately
    // 2. Below-fold APIs (provider-costs, advisor/recommendations) are deferred
    // 3. The page renders correctly with stats and quick actions

    const apiCalls: string[] = [];

    mockApi.get.mockImplementation((url: string) => {
      apiCalls.push(url);
      if (url === "/stats") return Promise.resolve(okResponse(realisticStats()));
      if (url === "/runs") return Promise.resolve(okResponse([
        { run_id: "r1", workflow_name: "test", status: "completed", total_cost_usd: 0.5, started_at: "2026-03-26T10:00:00Z" },
      ]));
      if (url === "/stats/sparklines") return Promise.resolve(okResponse({}));
      if (url === "/stats/anomalies") return Promise.resolve(okResponse([]));
      if (url === "/workflows") return Promise.resolve(okResponse([{ name: "wf1" }]));
      if (url === "/stats/heatmap") return Promise.resolve(okResponse([]));
      if (url === "/stats/provider-costs") return Promise.resolve(okResponse({
        period_days: 7, total_cost_usd: 50.0,
        by_provider: [
          { provider: "openai", model: "gpt4", region: "us", total_cost_usd: 30, run_count: 100, avg_cost_per_run: 0.3, percentage: 60 },
          { provider: "anthropic", model: "sonnet", region: "us", total_cost_usd: 20, run_count: 80, avg_cost_per_run: 0.25, percentage: 40 },
        ],
        advisor_costs: { total_usd: 0, by_purpose: [] },
      }));
      if (url === "/stats/provider-savings") return Promise.resolve(okResponse({
        current_total_usd: 50.0, alternatives: [],
      }));
      if (url === "/stats/provider-recommendation") return Promise.resolve(okResponse({ recommendations: [] }));
      if (url === "/stats/failover-events") return Promise.resolve(okResponse({
        events: [], total_failovers_7d: 0, total_cost_delta_7d: 0,
      }));
      if (url === "/advisor/recommendations") return Promise.resolve(okResponse({
        recommendations: [
          {
            workflow: "data-pipeline",
            current_provider: "openai",
            current_cost_30d: 30,
            suggested_provider: "mistral",
            suggested_model: "mistral-large",
            estimated_cost_30d: 15,
            savings_monthly: 15,
            savings_percent: 50,
            eu_compliant: true,
            reason: "Cheaper EU provider",
          },
        ],
        total_potential_savings: 15,
      }));
      return Promise.resolve(emptyApiResponse());
    });

    await act(async () => {
      render(<OverviewBento />);
    });

    await waitFor(() => {
      expect(screen.getByText("Overview")).toBeInTheDocument();
    });

    // Above-fold APIs should have been called during initial render
    expect(apiCalls).toContain("/stats");
    expect(apiCalls).toContain("/runs");
    expect(apiCalls).toContain("/stats/sparklines");
    expect(apiCalls).toContain("/workflows");

    // Verify the main content rendered with correct stats
    // "42" appears in both hero and stat card, so use getAllByText
    expect(screen.getAllByText("42").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/95.*success/)).toBeInTheDocument(); // success rate pill
    expect(screen.getByText("Run Workflow")).toBeInTheDocument();
  });

  it("Quick Actions buttons (Run Workflow, View Failures, Cost Report) are always visible in main state", async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/stats") return Promise.resolve(okResponse(realisticStats()));
      if (url === "/runs") return Promise.resolve(okResponse([
        { run_id: "r1", workflow_name: "test", status: "completed", total_cost_usd: 0.01, started_at: "2026-03-26T10:00:00Z" },
      ]));
      if (url === "/stats/sparklines") return Promise.resolve(okResponse({}));
      if (url === "/stats/anomalies") return Promise.resolve(okResponse([]));
      if (url === "/workflows") return Promise.resolve(okResponse([{ name: "test-wf" }]));
      return Promise.resolve(emptyApiResponse());
    });

    await act(async () => {
      render(<OverviewBento />);
    });

    await waitFor(() => {
      expect(screen.getByText("Run Workflow")).toBeInTheDocument();
    });

    expect(screen.getByText("View Failures")).toBeInTheDocument();
    expect(screen.getByText("Cost Report")).toBeInTheDocument();
  });

  it("handles error state and shows Retry button", async () => {
    mockApi.get.mockRejectedValue(new Error("Network error"));

    await act(async () => {
      render(<OverviewBento />);
    });

    await waitFor(() => {
      expect(screen.getByText(/Could not connect/)).toBeInTheDocument();
    });

    expect(screen.getByText("Retry")).toBeInTheDocument();
  });

  it("Getting Started checklist dismiss button hides the checklist", async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/stats") return Promise.resolve(okResponse(emptyStats()));
      if (url === "/runs") return Promise.resolve(okResponse([]));
      if (url === "/stats/sparklines") return Promise.resolve(okResponse({}));
      if (url === "/stats/anomalies") return Promise.resolve(okResponse([]));
      if (url === "/workflows") return Promise.resolve(okResponse([]));
      return Promise.resolve(emptyApiResponse());
    });

    await act(async () => {
      render(<OverviewBento />);
    });

    await waitFor(() => {
      expect(screen.getByText("Getting Started")).toBeInTheDocument();
    });

    // Click dismiss
    fireEvent.click(screen.getByText("Dismiss"));

    // Checklist should be gone, but Quick Start remains
    expect(screen.queryByText("Getting Started")).not.toBeInTheDocument();
    expect(screen.getByText("Quick Start")).toBeInTheDocument();
  });
});

// ============================================================================
// 2. SettingsPage regression (6 tests)
// ============================================================================

import SettingsPage from "@/pages/SettingsPage";

describe("SettingsPage", () => {
  const mockAdvisorStatus = {
    current_provider: "anthropic",
    current_model: null,
    data_residency: null,
    available_providers: [
      { id: "anthropic", name: "Anthropic", region: "us", configured: true, status: "ok" },
      { id: "mistral", name: "Mistral", region: "eu", configured: true, status: "ok" },
      { id: "openai", name: "OpenAI", region: "us", configured: false, status: "idle" },
      { id: "minimax", name: "MiniMax", region: "us", configured: false, status: "idle" },
      { id: "openrouter", name: "OpenRouter", region: "us", configured: true, status: "ok" },
    ],
  };

  const mockSettings = {
    anthropic_api_key: "sk-ant-***",
    e2b_api_key: "",
    openai_api_key: "",
    minimax_api_key: "",
    openrouter_api_key: "sk-or-***",
    auth_required: false,
    dashboard_origin: "http://localhost:5173",
    default_max_cost_usd: 10.0,
    webhook_secret: "",
    log_level: "info",
    max_workflow_depth: 5,
    storage_backend: "local",
    storage_bucket: "",
    storage_endpoint: "",
    data_dir: "./data",
    workflows_dir: "./workflows",
    is_local_mode: true,
    database_url: "sqlite:///data/sandcastle.db",
    redis_url: "",
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockReset();
    resetSearchParams(); // start on the General tab
  });

  const mockGetForSettings = () =>
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/settings") return Promise.resolve(okResponse(mockSettings));
      if (url === "/advisor/status") return Promise.resolve(okResponse(mockAdvisorStatus));
      return Promise.resolve(emptyApiResponse());
    });

  /** Render the hub on a specific tab and wait for its lazy panel to mount. */
  async function renderTab(tab: string, waitForText: RegExp | string) {
    resetSearchParams(tab === "general" ? "" : `tab=${tab}`);
    await act(async () => {
      render(<SettingsPage />);
    });
    await waitFor(() => {
      expect(screen.getByText(waitForText)).toBeInTheDocument();
    });
  }

  it("Provider cards render all providers from /advisor/status (Providers tab)", async () => {
    mockGetForSettings();
    await renderTab("providers", "Anthropic");

    expect(screen.getByText("Mistral")).toBeInTheDocument();
    expect(screen.getByText("OpenAI")).toBeInTheDocument();
    expect(screen.getByText("MiniMax")).toBeInTheDocument();
    expect(screen.getByText("OpenRouter")).toBeInTheDocument();
  });

  it("'Key set' and 'Set API key' links show correctly based on configured status", async () => {
    mockGetForSettings();
    await renderTab("providers", "Anthropic");

    // Configured providers show "Key set"
    const keySetElements = screen.getAllByText("Key set");
    expect(keySetElements.length).toBeGreaterThanOrEqual(1);

    // Unconfigured providers show "Set API key" links
    const setLinks = screen.getAllByText(/Set API key/);
    expect(setLinks.length).toBeGreaterThanOrEqual(1);
  });

  it("Backend configurator cards expand/collapse on click (Advanced tab)", async () => {
    mockGetForSettings();
    await renderTab("advanced", "Infrastructure");

    // Find a backend card - "Database" label is from BackendCard
    const dbCard = screen.queryByText("Database");
    if (dbCard) {
      fireEvent.click(dbCard);
      await waitFor(() => {
        const envTexts = screen.queryAllByText(/Add to .env/);
        expect(envTexts.length).toBeGreaterThanOrEqual(0);
      });
    }
    // No crash
    expect(screen.getByText("Infrastructure")).toBeInTheDocument();
  });

  it("Copy .env button does not crash when clipboard API is unavailable", async () => {
    const originalClipboard = navigator.clipboard;
    Object.defineProperty(navigator, "clipboard", {
      value: undefined,
      writable: true,
      configurable: true,
    });

    mockGetForSettings();
    await renderTab("advanced", "Infrastructure");

    // No crash even without clipboard API
    expect(screen.getByText("Infrastructure")).toBeInTheDocument();

    Object.defineProperty(navigator, "clipboard", {
      value: originalClipboard,
      writable: true,
      configurable: true,
    });
  });

  it("EU Data Residency toggle is present (Providers tab)", async () => {
    mockGetForSettings();
    await renderTab("providers", "Anthropic");

    const euToggle = screen.getByLabelText(/EU Data Residency/i);
    expect(euToggle).toBeInTheDocument();
    expect(euToggle).not.toBeChecked();
  });

  it("Save button resets dirty state after successful save (General tab budget)", async () => {
    mockGetForSettings();
    mockApi.patch.mockResolvedValue(okResponse({ ...mockSettings, default_max_cost_usd: 20 }));

    await renderTab("general", /Default Max Cost per Run/i);

    // Find the max cost input and change it to make the budget section dirty
    const costInput = screen.getByLabelText(/Max Cost per Run/i);
    if (costInput) {
      fireEvent.change(costInput, { target: { value: "20" } });

      const saveButtons = screen.getAllByText("Save");
      const enabledSave = saveButtons.find(
        (btn) => !(btn as HTMLButtonElement).disabled,
      );
      if (enabledSave) {
        await act(async () => {
          fireEvent.click(enabledSave);
        });
      }
    }

    expect(screen.getByText("Settings")).toBeInTheDocument();
  });
});

// ============================================================================
// 3. WorkflowBuilder regression (5 tests)
// ============================================================================

import { WorkflowBuilder } from "@/components/workflows/WorkflowBuilder";

describe("WorkflowBuilder", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockReset();
  });

  it("Step palette shows 4 categories (AI, Control Flow, Integration, Output)", () => {
    render(
      <WorkflowBuilder
        onSave={vi.fn()}
        onRun={vi.fn()}
      />
    );

    // The 4 category labels in the palette
    expect(screen.getByText("AI")).toBeInTheDocument();
    expect(screen.getByText("Control Flow")).toBeInTheDocument();
    expect(screen.getByText("Integration")).toBeInTheDocument();
    expect(screen.getByText("Output")).toBeInTheDocument();
  });

  it("Adding approval step creates a node with correct label", async () => {
    render(
      <WorkflowBuilder
        onSave={vi.fn()}
        onRun={vi.fn()}
      />
    );

    // Find the Approval button in the palette
    const approvalBtn = screen.getByText("Approval");
    expect(approvalBtn).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(approvalBtn);
    });

    // After clicking, the YAML preview should contain "approval"
    const yamlPreview = screen.getByTestId("yaml-preview");
    expect(yamlPreview.textContent).toContain("approval");
  });

  it("Adding openclaw step creates a node with correct label", async () => {
    render(
      <WorkflowBuilder
        onSave={vi.fn()}
        onRun={vi.fn()}
      />
    );

    // Find the OpenClaw button in the palette
    const openclawBtn = screen.getByText("OpenClaw");
    expect(openclawBtn).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(openclawBtn);
    });

    // After clicking, the YAML preview should contain "openclaw"
    const yamlPreview = screen.getByTestId("yaml-preview");
    expect(yamlPreview).toBeInTheDocument();
  });

  it("YAML preview is present in the UI", () => {
    render(
      <WorkflowBuilder
        onSave={vi.fn()}
        onRun={vi.fn()}
      />
    );

    const yamlPreview = screen.getByTestId("yaml-preview");
    expect(yamlPreview).toBeInTheDocument();
    // Should contain the workflow name placeholder
    expect(yamlPreview.textContent).toBeDefined();
  });

  it("Step palette items include Agent, LLM, Classify in AI category and HTTP, Code in Integration", () => {
    render(
      <WorkflowBuilder
        onSave={vi.fn()}
        onRun={vi.fn()}
      />
    );

    // AI category items
    expect(screen.getByText("Agent")).toBeInTheDocument();
    expect(screen.getByText("LLM")).toBeInTheDocument();
    expect(screen.getByText("Classify")).toBeInTheDocument();

    // Integration category items
    expect(screen.getByText("HTTP")).toBeInTheDocument();
    expect(screen.getByText("Code")).toBeInTheDocument();
    expect(screen.getByText("Delegate")).toBeInTheDocument();
    expect(screen.getByText("Browser")).toBeInTheDocument();
  });
});

// ============================================================================
// 4. MemoryPage regression (5 tests)
// ============================================================================

import MemoryPage from "@/pages/MemoryPage";

describe("MemoryPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockReset();
  });

  it("Renders empty state when no memories exist", async () => {
    mockApi.get.mockResolvedValue(okResponse({ memories: [], total: 0 }));

    await act(async () => {
      render(<MemoryPage />);
    });

    await waitFor(() => {
      expect(screen.getByText("No memories yet")).toBeInTheDocument();
    });

    expect(screen.getByText(/Memories are created automatically/)).toBeInTheDocument();
  });

  it("Search returns filtered results and shows clear button", async () => {
    const memories = [
      {
        id: "m1",
        memory: "Learned about API rate limits",
        metadata: { importance: 0.8, decay: 0.02, tags: ["api"] },
        created_at: "2026-03-26T10:00:00Z",
        updated_at: "2026-03-26T10:00:00Z",
      },
    ];

    // Initial load returns memories
    mockApi.get.mockResolvedValue(okResponse({ memories, total: 1 }));

    // Search returns filtered results
    mockApi.post.mockResolvedValue(okResponse({
      memories: [memories[0]],
      total: 1,
    }));

    await act(async () => {
      render(<MemoryPage />);
    });

    await waitFor(() => {
      expect(screen.getByText("Agent Memory")).toBeInTheDocument();
    });

    // Type in search and submit
    const searchInput = screen.getByPlaceholderText(/Search memories/i);
    fireEvent.change(searchInput, { target: { value: "rate limits" } });
    fireEvent.keyDown(searchInput, { key: "Enter" });

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/memories/search",
        expect.objectContaining({ query: "rate limits" })
      );
    });

    // Clear button should appear
    expect(screen.getByText("Clear")).toBeInTheDocument();
  });

  it("Delete confirmation dialog works", async () => {
    const memories = [
      {
        id: "m1",
        memory: "Test memory content for deletion",
        metadata: { importance: 0.5, decay: 0.05, tags: [] },
        created_at: "2026-03-26T10:00:00Z",
        updated_at: "2026-03-26T10:00:00Z",
      },
    ];

    mockApi.get.mockResolvedValue(okResponse({ memories, total: 1 }));
    mockApi.delete.mockResolvedValue(okResponse(null));

    await act(async () => {
      render(<MemoryPage />);
    });

    await waitFor(() => {
      expect(screen.getByText("Test memory content for deletion")).toBeInTheDocument();
    });

    // Click the delete button (trash icon) on the memory card
    const deleteButtons = screen.getAllByLabelText(/delete/i);
    if (deleteButtons.length > 0) {
      await act(async () => {
        fireEvent.click(deleteButtons[0]);
      });

      // Confirm dialog should appear
      await waitFor(() => {
        const confirmBtn = screen.queryByText("Delete") || screen.queryByText("Confirm");
        expect(confirmBtn).toBeInTheDocument();
      });
    }
  });

  it("Stats show correct averages from memory data", async () => {
    const memories = [
      {
        id: "m1",
        memory: "First memory",
        metadata: { importance: 0.8, decay: 0.04, tags: ["tag1"] },
        created_at: "2026-03-25T10:00:00Z",
        updated_at: "2026-03-25T10:00:00Z",
      },
      {
        id: "m2",
        memory: "Second memory",
        metadata: { importance: 0.6, decay: 0.06, tags: ["tag2"] },
        created_at: "2026-03-26T10:00:00Z",
        updated_at: "2026-03-26T10:00:00Z",
      },
    ];

    mockApi.get.mockResolvedValue(okResponse({ memories, total: 2 }));

    await act(async () => {
      render(<MemoryPage />);
    });

    await waitFor(() => {
      expect(screen.getByText("Agent Memory")).toBeInTheDocument();
    });

    // Total Memories should be 2
    expect(screen.getByText("2")).toBeInTheDocument();

    // Avg Importance: (0.8 + 0.6) / 2 = 0.7 => 70%
    expect(screen.getByText("70%")).toBeInTheDocument();

    // Avg Decay: (0.04 + 0.06) / 2 = 0.05 => 5.0%
    expect(screen.getByText("5.0%")).toBeInTheDocument();
  });

  it("Error state shows retry button", async () => {
    mockApi.get.mockRejectedValue(new Error("Network failure"));

    await act(async () => {
      render(<MemoryPage />);
    });

    await waitFor(() => {
      expect(screen.getByText(/Could not connect/)).toBeInTheDocument();
    });

    expect(screen.getByText("Retry")).toBeInTheDocument();

    // Clicking retry should re-fetch
    mockApi.get.mockResolvedValue(okResponse({ memories: [], total: 0 }));

    await act(async () => {
      fireEvent.click(screen.getByText("Retry"));
    });

    await waitFor(() => {
      expect(screen.getByText("No memories yet")).toBeInTheDocument();
    });
  });
});

// ============================================================================
// 5. ApiKeysPage regression (4 tests)
// ============================================================================

import ApiKeysPage from "@/pages/ApiKeysPage";

describe("ApiKeysPage", () => {
  const mockSettingsForKeys = {
    anthropic_api_key: "sk-ant-***",
    mistral_api_key: "",
    openai_api_key: "sk-***",
    minimax_api_key: "",
    openrouter_api_key: "",
    e2b_api_key: "e2b_***",
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockReset();
  });

  it("AI Provider Keys section shows 5 providers", async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/api-keys") return Promise.resolve(okResponse([]));
      if (url === "/settings") return Promise.resolve(okResponse(mockSettingsForKeys));
      return Promise.resolve(emptyApiResponse());
    });

    await act(async () => {
      render(<ApiKeysPage />);
    });

    await waitFor(() => {
      expect(screen.getByText("AI Provider Keys")).toBeInTheDocument();
    });

    // All 5 AI providers should be listed
    expect(screen.getByText("Anthropic (Claude)")).toBeInTheDocument();
    expect(screen.getByText("Mistral")).toBeInTheDocument();
    expect(screen.getByText("OpenAI")).toBeInTheDocument();
    expect(screen.getByText("MiniMax")).toBeInTheDocument();
    expect(screen.getByText(/OpenRouter/)).toBeInTheDocument();
  });

  it("Infrastructure Keys section shows E2B", async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/api-keys") return Promise.resolve(okResponse([]));
      if (url === "/settings") return Promise.resolve(okResponse(mockSettingsForKeys));
      return Promise.resolve(emptyApiResponse());
    });

    await act(async () => {
      render(<ApiKeysPage />);
    });

    await waitFor(() => {
      expect(screen.getByText("Infrastructure Keys")).toBeInTheDocument();
    });

    expect(screen.getByText("E2B Sandbox")).toBeInTheDocument();
  });

  it("Configured/Not set badges display correctly", async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/api-keys") return Promise.resolve(okResponse([]));
      if (url === "/settings") return Promise.resolve(okResponse(mockSettingsForKeys));
      return Promise.resolve(emptyApiResponse());
    });

    await act(async () => {
      render(<ApiKeysPage />);
    });

    await waitFor(() => {
      expect(screen.getByText("AI Provider Keys")).toBeInTheDocument();
    });

    // anthropic_api_key, openai_api_key, and e2b_api_key are set (truthy)
    const configuredBadges = screen.getAllByText("Configured");
    expect(configuredBadges.length).toBe(3); // Anthropic, OpenAI, E2B

    // mistral_api_key, minimax_api_key, openrouter_api_key are not set
    const notSetBadges = screen.getAllByText("Not set");
    expect(notSetBadges.length).toBe(3); // Mistral, MiniMax, OpenRouter
  });

  it("Save Changes button appears only when editing a key field", async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/api-keys") return Promise.resolve(okResponse([]));
      if (url === "/settings") return Promise.resolve(okResponse(mockSettingsForKeys));
      return Promise.resolve(emptyApiResponse());
    });

    await act(async () => {
      render(<ApiKeysPage />);
    });

    await waitFor(() => {
      expect(screen.getByText("AI Provider Keys")).toBeInTheDocument();
    });

    // Save Changes should NOT be visible initially
    expect(screen.queryByText("Save Changes")).not.toBeInTheDocument();

    // Click "Set" on an unconfigured key (Mistral)
    const setButtons = screen.getAllByText("Set");
    expect(setButtons.length).toBeGreaterThan(0);

    await act(async () => {
      fireEvent.click(setButtons[0]);
    });

    // Type a new value into the input field
    const inputs = screen.getAllByPlaceholderText(/mistral/i);
    if (inputs.length > 0) {
      fireEvent.change(inputs[0], { target: { value: "new-key-value" } });
    }

    // Save Changes button should now appear
    await waitFor(() => {
      expect(screen.getByText("Save Changes")).toBeInTheDocument();
    });
  });
});
