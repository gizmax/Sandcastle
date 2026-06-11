/**
 * UX wave 2 — Omnibox + calm/expandable Overview.
 *
 * Covers:
 *  - Omnibox: submit -> /generate (mocked) -> renders name, step count, preview,
 *    Run it + Edit CTAs; calm hint when no provider/key configured.
 *  - Overview: calm-by-default (dense widgets hidden) with a Show details toggle
 *    that reveals the expanded dashboard and persists the choice in localStorage.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";

// ---------------------------------------------------------------------------
// Polyfills (jsdom lacks IntersectionObserver, used by lazy-load sentinels)
// ---------------------------------------------------------------------------
class MockIntersectionObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (typeof globalThis.IntersectionObserver === "undefined") {
  (globalThis as Record<string, unknown>).IntersectionObserver =
    MockIntersectionObserver as unknown as typeof IntersectionObserver;
}
// jsdom lacks scrollIntoView (used by CommandPalette + Omnibox focus handlers).
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------
const mockNavigate = vi.fn();
const mockPost = vi.fn();
const mockGet = vi.fn();

vi.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
  // minimal stand-in: render children inside a real anchor
  Link: ({ children, to }: { children?: unknown; to?: string }) => (
    <a href={to as string}>{children as never}</a>
  ),
}));

vi.mock("@/api/client", () => ({
  api: {
    post: (...args: unknown[]) => mockPost(...args),
    get: (...args: unknown[]) => mockGet(...args),
    isMockMode: false,
  },
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

// Overview dependencies — keep them simple so the test focuses on layout logic.
const mockOverviewData: Record<string, unknown> = {};
vi.mock("@/components/overview/useOverviewData", () => ({
  useOverviewData: () => mockOverviewData,
}));
vi.mock("@/hooks/useAdvisorContext", () => ({
  useAdvisorContext: () => ({ score: 90, activeInsights: [], loading: false }),
}));
let mockDensity = "Standard";
vi.mock("@/contexts/UiModeContext", () => ({
  useDensity: () => ({ effectiveDensity: mockDensity }),
}));

import { Omnibox, OMNIBOX_FOCUS_EVENT } from "@/components/overview/Omnibox";
import OverviewBento from "@/pages/OverviewBento";
import { CommandPalette } from "@/components/layout/CommandPalette";

const GENERATE_RESULT = {
  yaml_content: "name: support-summary\nsteps:\n  - id: fetch\n  - id: post",
  name: "support-summary",
  description: "Summarize tickets and post to Slack",
  steps_count: 2,
  validation_errors: [],
  input_schema: null,
};

function resetOverviewData() {
  Object.keys(mockOverviewData).forEach((k) => delete mockOverviewData[k]);
  const noopRef = { current: null };
  Object.assign(mockOverviewData, {
    stats: { success_rate: 1, total_runs_today: 5, total_cost_today: 1, avg_duration_seconds: 2, runs_by_day: [] },
    recentRuns: [{ run_id: "r1", workflow_name: "wf", status: "completed", started_at: new Date().toISOString() }],
    loading: false,
    error: null,
    sparklines: {},
    anomalies: [],
    workflowCount: 3,
    heatmapCells: [],
    heatmapLoaded: true,
    failoverData: null,
    providerCosts: null,
    providerSavings: null,
    topRecommendation: null,
    advisorRecs: [],
    advisorTotalSavings: 0,
    recDismissed: true,
    belowFoldLoaded: true,
    activityEvents: [],
    activityLoaded: true,
    showProviderCosts: false,
    forecastVisible: true,
    chartsVisible: true,
    setAdvisorRecs: vi.fn(),
    setAdvisorTotalSavings: vi.fn(),
    setRecDismissed: vi.fn(),
    heatmapRef: noopRef,
    providerRef: noopRef,
    forecastRef: noopRef,
    chartsRef: noopRef,
    failoverRef: noopRef,
    activityRef: noopRef,
    retry: vi.fn(),
  });
}

beforeEach(() => {
  mockNavigate.mockReset();
  mockPost.mockReset();
  mockGet.mockReset();
  // Default: a provider is configured (advisor/status reports one available).
  mockGet.mockResolvedValue({
    data: { available: [{ id: "anthropic", configured: true, status: "ok" }] },
    error: null,
  });
  localStorage.clear();
  mockDensity = "Standard";
  resetOverviewData();
});

// ===========================================================================
// Omnibox
// ===========================================================================
describe("Omnibox", () => {
  it("renders a labeled form with the hero question", () => {
    render(<Omnibox />);
    expect(
      screen.getByRole("heading", { name: /what should your agent do/i }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/describe the task for your agent/i)).toBeInTheDocument();
  });

  it("warns and disables Build when no AI provider is configured", async () => {
    // advisor/status reports zero configured providers
    mockGet.mockResolvedValue({
      data: { available: [{ id: "anthropic", configured: false, status: "unconfigured" }] },
      error: null,
    });
    render(<Omnibox />);
    // persistent provider notice appears
    expect(
      await screen.findByText(/no ai provider is connected/i),
    ).toBeInTheDocument();
    // Build button is disabled even with a description typed
    fireEvent.change(screen.getByLabelText(/describe the task for your agent/i), {
      target: { value: "summarize my tickets" },
    });
    const build = screen.getByRole("button", { name: /build it/i });
    expect(build).toBeDisabled();
    // never calls /generate
    expect(mockPost).not.toHaveBeenCalled();
  });

  it("maps a NO_PROVIDER generate error to the connect-a-provider message", async () => {
    mockPost.mockResolvedValue({
      data: null,
      error: { code: "NO_PROVIDER", message: "No AI provider is configured." },
    });
    render(<Omnibox />);
    fireEvent.change(screen.getByLabelText(/describe the task for your agent/i), {
      target: { value: "do something" },
    });
    fireEvent.click(screen.getByRole("button", { name: /build it/i }));
    expect(
      await screen.findByText(/connect an ai provider to generate workflows/i),
    ).toBeInTheDocument();
  });

  it("submits the description to /generate and renders the result preview", async () => {
    mockPost.mockResolvedValue({ data: GENERATE_RESULT, error: null });
    render(<Omnibox />);

    const input = screen.getByLabelText(/describe the task for your agent/i);
    fireEvent.change(input, {
      target: { value: "Summarize today's support tickets and post to Slack" },
    });
    fireEvent.click(screen.getByRole("button", { name: /build it/i }));

    await waitFor(() =>
      expect(screen.getByText("support-summary")).toBeInTheDocument(),
    );
    // Step count + preview + both CTAs.
    expect(screen.getAllByText(/2 steps/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Preview/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^run it$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /edit in builder/i })).toBeInTheDocument();

    // Called /generate with the description.
    expect(mockPost).toHaveBeenCalledWith(
      "/generate",
      expect.objectContaining({ description: expect.stringContaining("support tickets") }),
      expect.any(Number),
    );
  });

  it("runs inline (run first, best-effort save) and navigates to the run when no inputs are required", async () => {
    mockPost
      .mockResolvedValueOnce({ data: GENERATE_RESULT, error: null }) // /generate
      .mockResolvedValueOnce({ data: { run_id: "run-123" }, error: null }) // /workflows/run (first)
      .mockResolvedValueOnce({ data: { ok: true }, error: null }); // POST /workflows (best-effort save)

    render(<Omnibox />);
    fireEvent.change(screen.getByLabelText(/describe the task for your agent/i), {
      target: { value: "do a thing" },
    });
    fireEvent.click(screen.getByRole("button", { name: /build it/i }));
    await waitFor(() => screen.getByRole("button", { name: /^run it$/i }));

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^run it$/i }));
    });

    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith("/runs/run-123"),
    );
  });

  it("hands the draft to the builder on Edit", async () => {
    mockPost.mockResolvedValue({ data: GENERATE_RESULT, error: null });
    render(<Omnibox />);
    fireEvent.change(screen.getByLabelText(/describe the task for your agent/i), {
      target: { value: "do a thing" },
    });
    fireEvent.click(screen.getByRole("button", { name: /build it/i }));
    await waitFor(() => screen.getByRole("button", { name: /edit in builder/i }));

    fireEvent.click(screen.getByRole("button", { name: /edit in builder/i }));
    expect(mockNavigate).toHaveBeenCalledWith(
      "/workflows/builder",
      expect.objectContaining({
        state: expect.objectContaining({ yaml: GENERATE_RESULT.yaml_content }),
      }),
    );
  });

  it("shows a calm provider hint instead of a raw error when no key is configured", async () => {
    mockPost.mockResolvedValue({
      data: null,
      error: { code: "HTTP_400", message: "No API key configured for provider" },
    });
    render(<Omnibox />);
    fireEvent.change(screen.getByLabelText(/describe the task for your agent/i), {
      target: { value: "do a thing" },
    });
    fireEvent.click(screen.getByRole("button", { name: /build it/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/connect an ai provider/i),
    );
  });

  it("surfaces validation errors with a refine affordance", async () => {
    mockPost.mockResolvedValue({
      data: { ...GENERATE_RESULT, validation_errors: ["step 'post' has no prompt"] },
      error: null,
    });
    render(<Omnibox />);
    fireEvent.change(screen.getByLabelText(/describe the task for your agent/i), {
      target: { value: "do a thing" },
    });
    fireEvent.click(screen.getByRole("button", { name: /build it/i }));

    await waitFor(() =>
      expect(screen.getByText(/has no prompt/i)).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: /refine/i })).toBeInTheDocument();
  });
});

// ===========================================================================
// Overview calm / expand
// ===========================================================================
describe("Overview calm vs expanded", () => {
  it("renders the omnibox and recent activity but hides dense widgets by default", () => {
    render(<OverviewBento />);
    // Omnibox hero is present above the fold.
    expect(
      screen.getByRole("heading", { name: /what should your agent do/i }),
    ).toBeInTheDocument();
    // Reveal control collapsed by default at Standard density.
    const toggle = screen.getByRole("button", { name: /show details/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    // The expanded region is not in the DOM yet.
    expect(document.getElementById("overview-more-insights")).toBeNull();
  });

  it("reveals the dense dashboard when Show details is clicked and persists the choice", () => {
    render(<OverviewBento />);
    fireEvent.click(screen.getByRole("button", { name: /show details/i }));

    const toggle = screen.getByRole("button", { name: /hide details/i });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(document.getElementById("overview-more-insights")).not.toBeNull();
    expect(localStorage.getItem("sandcastle-overview-expanded")).toBe("true");
  });

  it("defaults to expanded at the Everything density tier", () => {
    mockDensity = "Everything";
    render(<OverviewBento />);
    expect(
      screen.getByRole("button", { name: /hide details/i }),
    ).toHaveAttribute("aria-expanded", "true");
    expect(document.getElementById("overview-more-insights")).not.toBeNull();
  });

  it("leads the empty state with the omnibox", () => {
    mockOverviewData.workflowCount = 0;
    mockOverviewData.recentRuns = [];
    mockOverviewData.stats = {
      success_rate: 0,
      total_runs_today: 0,
      total_cost_today: 0,
      avg_duration_seconds: 0,
      runs_by_day: [],
    };
    render(<OverviewBento />);
    expect(
      screen.getByRole("heading", { name: /describe your first agent/i }),
    ).toBeInTheDocument();
  });
});

// ===========================================================================
// CommandPalette → omnibox action
// ===========================================================================
describe("CommandPalette omnibox action", () => {
  it("lists 'Describe a workflow…' and dispatches the focus event on select", () => {
    const onClose = vi.fn();
    const focusSpy = vi.fn();
    window.addEventListener(OMNIBOX_FOCUS_EVENT, focusSpy);

    render(<CommandPalette open onClose={onClose} recentItems={[]} />);

    const action = screen.getByRole("option", { name: /describe a workflow/i });
    expect(action).toBeInTheDocument();

    fireEvent.click(action);

    // Navigates home, closes the palette, then signals the omnibox to focus.
    expect(mockNavigate).toHaveBeenCalledWith("/");
    expect(onClose).toHaveBeenCalled();

    // The focus event is dispatched on the next animation frames.
    return new Promise<void>((resolve) => {
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          expect(focusSpy).toHaveBeenCalled();
          window.removeEventListener(OMNIBOX_FOCUS_EVENT, focusSpy);
          resolve();
        }),
      );
    });
  });
});
