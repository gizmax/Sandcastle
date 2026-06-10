/**
 * TimeMachinePage tests - config form, dry-run job flow with polling,
 * live-mode budget input, error surfacing, and the empty-selection state.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("@/api/client", () => ({
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    post: (...args: unknown[]) => apiPost(...args),
    isMockMode: false,
    onMockChange: vi.fn(() => () => {}),
  },
}));

vi.mock("react-router-dom", () => ({
  useNavigate: () => vi.fn(),
  useLocation: () => ({ pathname: "/time-machine" }),
  Link: ({ children, to, ...rest }: { children: React.ReactNode; to: string }) => (
    <a href={to} {...rest}>{children}</a>
  ),
}));

import TimeMachinePage from "@/pages/TimeMachinePage";

const REPORT = {
  mode: "dry_run",
  target_model: "nim/llama-3.1-70b",
  judge_model: null,
  selection: {
    runs: 12,
    steps: 40,
    workflows: ["lead-enrichment"],
    original_cost_usd: 20.0,
    window_days: 28,
  },
  cost: { original_usd: 20.0, new_usd: 0.0, delta_usd: -20.0, delta_pct: -100 },
  quality: null,
  latency: null,
  live: null,
  extrapolation: {
    window_days: 28,
    monthly_original_usd: 21.4,
    monthly_projected_usd: 0,
    monthly_savings_usd: 21.4,
  },
  per_workflow: [
    {
      workflow: "lead-enrichment",
      runs: 12,
      steps: 40,
      original_cost_usd: 20.0,
      new_cost_usd: 0.0,
      cost_delta_usd: -20.0,
      cost_delta_pct: -100,
      quality_old: null,
      quality_new: null,
      quality_delta_pct: null,
      latency_old_seconds: null,
      latency_new_seconds: null,
      latency_delta_pct: null,
    },
  ],
  verdict: "Switching to nim/llama-3.1-70b saves $21.40/mo (projected from recorded token volume). Run a live replay to measure quality.",
};

function mockDefaults(jobReport: unknown = REPORT) {
  apiGet.mockImplementation((path: string) => {
    if (path === "/timemachine") return Promise.resolve({ data: [], error: null });
    if (path === "/workflows") {
      return Promise.resolve({ data: [{ name: "lead-enrichment" }], error: null });
    }
    if (path.startsWith("/timemachine/")) {
      return Promise.resolve({
        data: { job_id: "job-1", status: "completed", report: jobReport, error: null },
        error: null,
      });
    }
    return Promise.resolve({ data: null, error: null });
  });
  apiPost.mockResolvedValue({ data: { job_id: "job-1", status: "running" }, error: null });
}

describe("TimeMachinePage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockDefaults();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders the config form and the confident empty state", async () => {
    render(<TimeMachinePage />);
    expect(screen.getByText("Model Time Machine")).toBeInTheDocument();
    expect(screen.getByLabelText("Target model")).toBeInTheDocument();
    expect(
      await screen.findByText("Test any model against your real workload")
    ).toBeInTheDocument();
    // dry run is the default - the run button says so
    expect(screen.getByRole("button", { name: /run dry-run estimate/i })).toBeInTheDocument();
  });

  it("runs a dry-run job and renders the report after polling", async () => {
    render(<TimeMachinePage />);
    fireEvent.click(screen.getByRole("button", { name: /run dry-run estimate/i }));

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        "/timemachine",
        expect.objectContaining({
          target_model: "nim/llama-3.1-70b",
          since: "30d",
          live: false,
        })
      );
    });

    // poll interval is 1.5s - wait for the completed report to land
    expect(await screen.findByTestId("tm-verdict", {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getAllByText(/saves \$21\.40\/mo/).length).toBeGreaterThan(0);
    expect(screen.getAllByText("lead-enrichment").length).toBeGreaterThan(1);
    expect(screen.getByText(/Per-workflow deltas/)).toBeInTheDocument();
  }, 10000);

  it("reveals the budget input only in live mode and sends budget_usd", async () => {
    render(<TimeMachinePage />);
    expect(screen.queryByLabelText(/budget cap/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByLabelText(/budget cap/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /run live replay/i }));
    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        "/timemachine",
        expect.objectContaining({ live: true, budget_usd: 5.0 })
      );
    });
  });

  it("surfaces a budget refusal error from the API", async () => {
    apiPost.mockResolvedValue({
      data: null,
      error: { code: "BUDGET_EXCEEDED", message: "Estimated cost $4.20 exceeds budget $1.00" },
    });
    render(<TimeMachinePage />);
    fireEvent.click(screen.getByRole("button", { name: /run dry-run estimate/i }));
    expect(
      await screen.findByText(/exceeds budget \$1\.00/)
    ).toBeInTheDocument();
  });

  it("shows the empty-selection state when no recorded workload matched", async () => {
    mockDefaults({ ...REPORT, selection: { ...REPORT.selection, runs: 0, steps: 0 } });
    render(<TimeMachinePage />);
    fireEvent.click(screen.getByRole("button", { name: /run dry-run estimate/i }));
    expect(
      await screen.findByText("No recorded workload in this window", {}, { timeout: 4000 })
    ).toBeInTheDocument();
  }, 10000);
});
