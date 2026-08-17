import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("@/api/client", () => ({
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    post: (...args: unknown[]) => apiPost(...args),
  },
}));

vi.mock("react-router-dom", () => ({
  useLocation: () => ({ state: null }),
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock("recharts", () => ({
  CartesianGrid: () => <div />,
  Line: () => <div />,
  LineChart: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  ReferenceLine: () => <div />,
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Tooltip: () => <div />,
  XAxis: () => <div />,
  YAxis: () => <div />,
}));

import EvolutionPage from "@/pages/EvolutionPage";

const workflowName = "evolution/a#b?c";

const latestEvolution = {
  id: "latest",
  workflow_name: workflowName,
  status: "running",
  optimize_for: "quality",
  baseline_score: 70,
  best_score: 80,
  baseline_quality: null,
  best_quality: null,
  baseline_cost: null,
  best_cost: null,
  max_iterations: 4,
  current_iteration: 2,
  total_keeps: 1,
  total_discards: 1,
  budget_limit_usd: null,
  created_at: "2026-07-20T12:00:00Z",
  completed_at: null,
  error: null,
};

const supersededEvolution = {
  ...latestEvolution,
  id: "superseded",
  status: "completed",
  current_iteration: 4,
  completed_at: "2026-07-20T11:00:00Z",
};

beforeEach(() => {
  vi.clearAllMocks();
  apiGet.mockImplementation((path: string) => {
    if (path === "/evolution") {
      return Promise.resolve({ data: [latestEvolution, supersededEvolution], error: null });
    }
    if (path === "/evolution/stats") {
      return Promise.resolve({
        data: {
          total_evolutions: 2,
          active_evolutions: 1,
          completed_evolutions: 1,
          total_improvements: 1,
          avg_improvement: 10,
          top_workflows: [],
        },
        error: null,
      });
    }
    if (path === "/workflows") return Promise.resolve({ data: [], error: null });
    if (path.endsWith("/status")) {
      return Promise.resolve({ data: { ...latestEvolution, evolution_id: latestEvolution.id, iterations: [] }, error: null });
    }
    return Promise.resolve({ data: null, error: null });
  });
  apiPost.mockResolvedValue({ data: {}, error: null });
});

afterEach(cleanup);

describe("EvolutionPage", () => {
  it("disables superseded rows and encodes paths for the latest evolution", async () => {
    render(<EvolutionPage />);

    expect(await screen.findByText("Superseded")).toBeInTheDocument();
    const acceptButton = screen.getByRole("button", { name: "Accept" });
    expect(acceptButton).toBeDisabled();

    apiGet.mockClear();
    fireEvent.click(screen.getAllByText(workflowName)[1]);
    expect(apiGet).not.toHaveBeenCalled();

    fireEvent.click(screen.getAllByText(workflowName)[0]);
    await waitFor(() => {
      expect(apiGet).toHaveBeenCalledWith(`/evolution/${encodeURIComponent(workflowName)}/status`);
    });

    fireEvent.click(screen.getByRole("button", { name: "Cancel evolution" }));
    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(`/evolution/${encodeURIComponent(workflowName)}/cancel`);
    });
  });

  it("keeps failed evolutions visible with their diagnostic", async () => {
    apiGet.mockImplementation((path: string) => {
      if (path === "/evolution") {
        return Promise.resolve({
          data: [{
            ...latestEvolution,
            id: "failed",
            status: "failed",
            completed_at: "2026-07-20T12:05:00Z",
            error: "Provider unavailable",
          }],
          error: null,
        });
      }
      if (path === "/evolution/stats") {
        return Promise.resolve({
          data: {
            total_evolutions: 1,
            active_evolutions: 0,
            completed_evolutions: 0,
            total_improvements: 0,
            avg_improvement: null,
            top_workflows: [],
          },
          error: null,
        });
      }
      if (path === "/workflows") return Promise.resolve({ data: [], error: null });
      return Promise.resolve({ data: null, error: null });
    });

    render(<EvolutionPage />);

    expect(await screen.findByText("Stopped (1)")).toBeInTheDocument();
    expect(screen.getByText("Provider unavailable")).toBeInTheDocument();
  });
});
