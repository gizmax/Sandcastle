/**
 * v27 Onboarding UX Regression Tests
 *
 * Covers 3 onboarding features:
 * 1. ProviderStatusBanner - provider badges, status dots, Ollama detection, API key input, dismiss
 * 2. One-click demo workflow - demo card in empty state, Run Demo button, cost hint, progress, success
 * 3. OnboardingWizard (simplified 2-step) - step count, provider detection, API key, demo, skip, localStorage
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
// Mocks
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

vi.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
  useLocation: () => ({ pathname: "/", state: null }),
  useSearchParams: () => [new URLSearchParams(), vi.fn()] as const,
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

import { api } from "@/api/client";

const mockApi = api as unknown as {
  get: ReturnType<typeof vi.fn>;
  post: ReturnType<typeof vi.fn>;
  patch: ReturnType<typeof vi.fn>;
  delete: ReturnType<typeof vi.fn>;
};

// ============================================================================
// Helpers
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

function okResponse<T>(data: T) {
  return { data, error: null };
}

/** Set up API mocks for the empty-state OverviewBento (0 workflows, 0 runs). */
function setupEmptyOverview() {
  mockApi.get.mockImplementation((url: string) => {
    if (url === "/stats") return Promise.resolve(okResponse(emptyStats()));
    if (url === "/runs") return Promise.resolve(okResponse([]));
    if (url === "/stats/sparklines") return Promise.resolve(okResponse({}));
    if (url === "/stats/anomalies") return Promise.resolve(okResponse([]));
    if (url === "/workflows") return Promise.resolve(okResponse([]));
    // ProviderStatusBanner calls /health/providers
    if (url === "/health/providers") return Promise.resolve(okResponse({ providers: [] }));
    return Promise.resolve({ data: null, error: null });
  });
}

/** Set up API mocks with providers returned from /health/providers. */
function setupOverviewWithProviders(providers: Array<{
  id: string;
  name: string;
  status: "running" | "configured" | "unconfigured";
  region: string;
  latency_ms: number | null;
}>) {
  mockApi.get.mockImplementation((url: string) => {
    if (url === "/stats") return Promise.resolve(okResponse(emptyStats()));
    if (url === "/runs") return Promise.resolve(okResponse([]));
    if (url === "/stats/sparklines") return Promise.resolve(okResponse({}));
    if (url === "/stats/anomalies") return Promise.resolve(okResponse([]));
    if (url === "/workflows") return Promise.resolve(okResponse([]));
    if (url === "/health/providers") return Promise.resolve(okResponse({ providers }));
    return Promise.resolve({ data: null, error: null });
  });
}

// ============================================================================
// 1. ProviderStatusBanner (8 tests)
// ============================================================================

import OverviewBento from "@/pages/OverviewBento";

describe("ProviderStatusBanner", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockReset();
    localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders provider badges when data loaded", async () => {
    setupOverviewWithProviders([
      { id: "anthropic", name: "Anthropic", status: "configured", region: "us", latency_ms: null },
      { id: "ollama", name: "Ollama", status: "running", region: "local", latency_ms: 12 },
    ]);

    await act(async () => {
      render(<OverviewBento />);
    });

    await waitFor(() => {
      expect(screen.getByText("Anthropic")).toBeInTheDocument();
    });
    expect(screen.getByText("Ollama")).toBeInTheDocument();
  });

  it("shows green dot for running/configured providers", async () => {
    setupOverviewWithProviders([
      { id: "anthropic", name: "Anthropic", status: "configured", region: "us", latency_ms: null },
      { id: "ollama", name: "Ollama", status: "running", region: "local", latency_ms: 15 },
    ]);

    await act(async () => {
      render(<OverviewBento />);
    });

    await waitFor(() => {
      expect(screen.getByText("Anthropic")).toBeInTheDocument();
    });

    // Both active provider badges should have the success styling
    const anthropicBadge = screen.getByText("Anthropic").closest("span");
    const ollamaBadge = screen.getByText("Ollama").closest("span");
    expect(anthropicBadge?.className).toContain("text-success");
    expect(ollamaBadge?.className).toContain("text-success");
  });

  it("shows gray dot for unconfigured providers", async () => {
    setupOverviewWithProviders([
      { id: "openai", name: "OpenAI", status: "unconfigured", region: "us", latency_ms: null },
    ]);

    await act(async () => {
      render(<OverviewBento />);
    });

    await waitFor(() => {
      expect(screen.getByText("OpenAI")).toBeInTheDocument();
    });

    const badge = screen.getByText("OpenAI").closest("span");
    expect(badge?.className).toContain("text-muted-foreground");
  });

  it("shows 'Ollama detected' message when Ollama is running", async () => {
    setupOverviewWithProviders([
      { id: "ollama", name: "Ollama", status: "running", region: "local", latency_ms: 8 },
    ]);

    await act(async () => {
      render(<OverviewBento />);
    });

    await waitFor(() => {
      expect(screen.getByText(/Ollama detected/)).toBeInTheDocument();
    });
  });

  it("shows API key input when no providers are active", async () => {
    setupOverviewWithProviders([
      { id: "openai", name: "OpenAI", status: "unconfigured", region: "us", latency_ms: null },
      { id: "mistral", name: "Mistral", status: "unconfigured", region: "eu", latency_ms: null },
    ]);

    await act(async () => {
      render(<OverviewBento />);
    });

    await waitFor(() => {
      expect(screen.getByPlaceholderText("Paste any API key to start")).toBeInTheDocument();
    });

    // "Go" button should be present
    expect(screen.getByText("Go")).toBeInTheDocument();
  });

  it("dismiss button hides banner and persists to localStorage", async () => {
    setupOverviewWithProviders([
      { id: "anthropic", name: "Anthropic", status: "configured", region: "us", latency_ms: null },
    ]);

    await act(async () => {
      render(<OverviewBento />);
    });

    await waitFor(() => {
      expect(screen.getByText("Anthropic")).toBeInTheDocument();
    });

    // Click the dismiss button (has aria-label)
    const dismissBtn = screen.getByLabelText("Dismiss provider banner");
    await act(async () => {
      fireEvent.click(dismissBtn);
    });

    // Banner content should be gone
    expect(screen.queryByText("Anthropic")).not.toBeInTheDocument();

    // localStorage should record dismissal
    expect(localStorage.getItem("sandcastle_provider_banner_dismissed")).toBe("true");
  });

  it("returns null when dismissed (from localStorage)", async () => {
    // Pre-set the dismissed key before rendering
    localStorage.setItem("sandcastle_provider_banner_dismissed", "true");

    setupOverviewWithProviders([
      { id: "anthropic", name: "Anthropic", status: "configured", region: "us", latency_ms: null },
    ]);

    await act(async () => {
      render(<OverviewBento />);
    });

    // Wait for the overview to render
    await waitFor(() => {
      expect(screen.getByText("Overview")).toBeInTheDocument();
    });

    // Provider badge should not be shown because banner was previously dismissed
    expect(screen.queryByText("Anthropic")).not.toBeInTheDocument();
  });

  it("handles fetch error gracefully (no crash)", async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/stats") return Promise.resolve(okResponse(emptyStats()));
      if (url === "/runs") return Promise.resolve(okResponse([]));
      if (url === "/stats/sparklines") return Promise.resolve(okResponse({}));
      if (url === "/stats/anomalies") return Promise.resolve(okResponse([]));
      if (url === "/workflows") return Promise.resolve(okResponse([]));
      // Simulate network error for provider health
      if (url === "/health/providers") return Promise.reject(new Error("Network error"));
      return Promise.resolve({ data: null, error: null });
    });

    await act(async () => {
      render(<OverviewBento />);
    });

    // Page should still render without crashing
    await waitFor(() => {
      expect(screen.getByText("Overview")).toBeInTheDocument();
    });

    // No provider badges should appear, but no error shown either
    expect(screen.queryByLabelText("Dismiss provider banner")).not.toBeInTheDocument();
  });
});

// ============================================================================
// 2. One-click Demo (6 tests)
// ============================================================================

describe("One-click demo workflow card", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockReset();
    localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("demo card is visible in empty state", async () => {
    setupEmptyOverview();

    await act(async () => {
      render(<OverviewBento />);
    });

    await waitFor(() => {
      expect(screen.getByText("Run Your First Workflow")).toBeInTheDocument();
    });
  });

  it("'Run Demo' button is present in idle state", async () => {
    setupEmptyOverview();

    await act(async () => {
      render(<OverviewBento />);
    });

    await waitFor(() => {
      expect(screen.getByText("Run Demo")).toBeInTheDocument();
    });
  });

  it("shows cost hint text next to Run Demo button", async () => {
    setupEmptyOverview();

    await act(async () => {
      render(<OverviewBento />);
    });

    await waitFor(() => {
      expect(screen.getByText(/Free with Ollama/)).toBeInTheDocument();
    });
    expect(screen.getByText(/\$0\.01 with Claude/)).toBeInTheDocument();
  });

  it("demo running state shows progress text", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });

    setupEmptyOverview();
    // Mock the workflow run to never resolve so we can observe the loading state
    mockApi.post.mockReturnValue(new Promise(() => {}));

    await act(async () => {
      render(<OverviewBento />);
    });

    // Wait for idle state
    await waitFor(() => {
      expect(screen.getByText("Run Demo")).toBeInTheDocument();
    });

    // Click Run Demo
    await act(async () => {
      fireEvent.click(screen.getByText("Run Demo"));
    });

    // Should show step 1 progress immediately
    expect(screen.getByText(/Running step 1 of 2/)).toBeInTheDocument();

    // Advance past the 1-second delay to trigger step 2
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1100);
    });

    await waitFor(() => {
      expect(screen.getByText(/Running step 2 of 2/)).toBeInTheDocument();
    });
  });

  it("demo success shows output and green border", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });

    setupEmptyOverview();
    mockApi.post.mockResolvedValue(okResponse({
      run_id: "demo-123",
      status: "completed",
      outputs: {
        generate: "Three facts about sandcastles.",
        summarize: "Sandcastles are ancient, artistic, and fragile.",
      },
      total_cost_usd: 0.008,
    }));

    await act(async () => {
      render(<OverviewBento />);
    });

    await waitFor(() => {
      expect(screen.getByText("Run Demo")).toBeInTheDocument();
    });

    // Click Run Demo
    await act(async () => {
      fireEvent.click(screen.getByText("Run Demo"));
    });

    // Advance past the internal 1-second setTimeout
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1100);
    });

    // Wait for "Done!" text
    await waitFor(() => {
      expect(screen.getByText("Done!")).toBeInTheDocument();
    });

    // The summarize output should be displayed
    expect(screen.getByText("Sandcastles are ancient, artistic, and fragile.")).toBeInTheDocument();

    // The card wrapper should have a green border class
    const cardContainer = screen.getByText("Run Your First Workflow").closest("[class*='border']");
    expect(cardContainer?.className).toContain("border-success");
  });

  it("auto-checks first-run in Getting Started after demo", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });

    setupEmptyOverview();
    mockApi.post.mockResolvedValue(okResponse({
      run_id: "demo-123",
      status: "completed",
      outputs: {
        generate: "Three facts.",
        summarize: "Summary text.",
      },
      total_cost_usd: 0.005,
    }));

    await act(async () => {
      render(<OverviewBento />);
    });

    await waitFor(() => {
      expect(screen.getByText("Run Demo")).toBeInTheDocument();
    });

    // Click Run Demo
    await act(async () => {
      fireEvent.click(screen.getByText("Run Demo"));
    });

    // Advance timers
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1100);
    });

    await waitFor(() => {
      expect(screen.getByText("Done!")).toBeInTheDocument();
    });

    // Check that localStorage has first-run marked as completed
    const raw = localStorage.getItem("sandcastle_getting_started");
    expect(raw).toBeTruthy();
    const parsed = JSON.parse(raw!);
    expect(parsed.completed["first-run"]).toBe(true);
  });
});

// ============================================================================
// 3. OnboardingWizard simplified (8 tests)
// ============================================================================

import { OnboardingWizard } from "@/components/onboarding/OnboardingWizard";
import { UiModeProvider } from "@/contexts/UiModeContext";

/** The guided wizard reads/writes UI mode, so renders are wrapped in its provider. */
function renderWizard(onFinish: () => void) {
  return render(
    <UiModeProvider>
      <OnboardingWizard onFinish={onFinish} />
    </UiModeProvider>
  );
}

describe("OnboardingWizard (guided beginner flow)", () => {
  const mockOnFinish = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockReset();
    mockOnFinish.mockReset();
    localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("has 5 guided steps", async () => {
    mockApi.get.mockResolvedValue(okResponse({}));

    await act(async () => {
      renderWizard(mockOnFinish);
    });

    const progressNav = screen.getByRole("navigation", { name: /progress/i });
    const stepItems = progressNav.querySelectorAll("li");
    expect(stepItems.length).toBe(5);
  });

  it("starts on the Welcome step", async () => {
    mockApi.get.mockResolvedValue(okResponse({}));

    await act(async () => {
      renderWizard(mockOnFinish);
    });

    expect(screen.getByText("Welcome to Sandcastle")).toBeInTheDocument();
    expect(screen.getByText("Get Started")).toBeInTheDocument();
  });

  it("Welcome -> Connect asks local or cloud", async () => {
    mockApi.get.mockResolvedValue(okResponse({}));

    await act(async () => {
      renderWizard(mockOnFinish);
    });

    await act(async () => {
      fireEvent.click(screen.getByText("Get Started"));
    });

    await waitFor(() => {
      expect(screen.getByText("Local or cloud?")).toBeInTheDocument();
    });
  });

  it("Connect shows the local hint when no providers are detected", async () => {
    mockApi.get.mockResolvedValue(okResponse({}));

    await act(async () => {
      renderWizard(mockOnFinish);
    });

    await act(async () => {
      fireEvent.click(screen.getByText("Get Started"));
    });

    await waitFor(() => {
      expect(screen.getByText("ollama run llama3")).toBeInTheDocument();
    });
    expect(screen.getByPlaceholderText("sk-...")).toBeInTheDocument();
  });

  it("Connect shows auto-detected local provider", async () => {
    mockApi.get.mockResolvedValue(okResponse({
      ollama: { status: "running", latency_ms: 8, region: "local" },
    }));

    await act(async () => {
      renderWizard(mockOnFinish);
    });

    await act(async () => {
      fireEvent.click(screen.getByText("Get Started"));
    });

    await waitFor(() => {
      expect(screen.getByText("ollama")).toBeInTheDocument();
    });
    expect(screen.getByText("Ready!")).toBeInTheDocument();
  });

  it("Skip keeps Full mode and marks onboarding done", async () => {
    mockApi.get.mockResolvedValue(okResponse({}));

    await act(async () => {
      renderWizard(mockOnFinish);
    });

    await waitFor(() => {
      expect(screen.getByText("Skip to dashboard")).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.click(screen.getByText("Skip to dashboard"));
    });

    expect(mockNavigate).toHaveBeenCalledWith("/");
    expect(localStorage.getItem("sandcastle-onboarding-done")).toBe("true");
    expect(localStorage.getItem("sandcastle-ui-mode")).toBe("full");
  });

  it("completing the guided flow opts into Lite mode", async () => {
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/health/providers") {
        return Promise.resolve(okResponse({
          ollama: { status: "running", latency_ms: 5, region: "local" },
        }));
      }
      if (url === "/templates") {
        return Promise.resolve(okResponse([
          {
            name: "text-summarizer",
            description: "Summarize text.",
            input_schema: {
              required: ["text"],
              properties: { text: { type: "string", description: "Text to summarize" } },
            },
          },
        ]));
      }
      return Promise.resolve({ data: null, error: null });
    });
    mockApi.post.mockResolvedValue(okResponse({
      run_id: "run-1",
      status: "completed",
      outputs: { summary: "A concise summary." },
    }));

    await act(async () => {
      renderWizard(mockOnFinish);
    });

    // Welcome -> Connect
    await act(async () => {
      fireEvent.click(screen.getByText("Get Started"));
    });
    await waitFor(() => expect(screen.getByText("Ready!")).toBeInTheDocument());

    // Connect -> Pick
    await act(async () => {
      fireEvent.click(screen.getByText("Next"));
    });
    await waitFor(() => expect(screen.getByText("Pick your first flow")).toBeInTheDocument());

    // Pick the Text Summarizer card -> Configure
    await act(async () => {
      fireEvent.click(screen.getByText("Text Summarizer"));
    });
    await act(async () => {
      fireEvent.click(screen.getByText("Continue"));
    });
    await waitFor(() => expect(screen.getByText("Configure Inputs")).toBeInTheDocument());

    // Configure -> Run
    await act(async () => {
      fireEvent.click(screen.getByText("Continue"));
    });
    await waitFor(() => expect(screen.getByText("Run your first flow")).toBeInTheDocument());

    // Run it
    await act(async () => {
      fireEvent.click(screen.getByText("Run workflow"));
    });
    await waitFor(() => expect(screen.getByText("Done!")).toBeInTheDocument());

    // Finish into the dashboard
    await act(async () => {
      fireEvent.click(screen.getByText("Go to my dashboard"));
    });

    expect(mockOnFinish).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem("sandcastle-onboarding-done")).toBe("true");
    expect(localStorage.getItem("sandcastle-ui-mode")).toBe("lite");
  });
});
