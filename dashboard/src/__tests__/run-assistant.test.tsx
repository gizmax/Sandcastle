/**
 * Run Assistant sidebar (0.42.1): advisor-backed answers with local fallback.
 * - sends the question to POST /runs/{id}/assistant and renders the answer
 * - falls back to the built-in heuristics when the endpoint fails (NO_PROVIDER)
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { AiChatSidebar } from "@/components/runs/AiChatSidebar";
import { api } from "@/api/client";

vi.mock("@/api/client", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

const mockApi = api as unknown as { post: ReturnType<typeof vi.fn> };

// jsdom nema scrollIntoView
window.HTMLElement.prototype.scrollIntoView = vi.fn();

const RUN = {
  run_id: "11111111-2222-3333-4444-555555555555",
  workflow_name: "lucerna-articles",
  status: "failed",
  input_data: null,
  outputs: null,
  total_cost_usd: 0.02,
  max_cost_usd: null,
  started_at: new Date().toISOString(),
  completed_at: new Date().toISOString(),
  error: null,
  steps: [
    {
      step_id: "search-web",
      status: "failed",
      attempt: 1,
      error: "Sandbox backend 'e2b' is not available",
      cost_usd: 0.001,
      duration_seconds: 2,
    },
  ],
};

function renderSidebar() {
  return render(
    <AiChatSidebar open={true} onClose={() => {}} run={RUN as never} />
  );
}

async function ask(question: string) {
  const input = screen.getByPlaceholderText(/ask about this run/i);
  fireEvent.change(input, { target: { value: question } });
  await act(async () => {
    fireEvent.submit(input.closest("form") as HTMLFormElement);
  });
}

describe("AiChatSidebar (advisor-backed)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("sends the question to the assistant endpoint and renders the answer", async () => {
    mockApi.post.mockResolvedValue({
      data: { answer: "Krok search-web selhal: sandbox e2b neni dostupny." },
      error: null,
    });

    renderSidebar();
    await ask("proc to spadlo?");

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        `/runs/${RUN.run_id}/assistant`,
        expect.objectContaining({ question: "proc to spadlo?" })
      );
      expect(
        screen.getByText(/sandbox e2b neni dostupny/i)
      ).toBeInTheDocument();
    });
  });

  it("falls back to local heuristics when the endpoint fails", async () => {
    mockApi.post.mockResolvedValue({
      data: null,
      error: { code: "NO_PROVIDER", message: "No AI provider is configured." },
    });

    renderSidebar();
    await ask("Why did this fail?");

    await waitFor(() => {
      // Heuristic fallback quotes the failing step and its captured error
      expect(screen.getByText(/search-web/)).toBeInTheDocument();
      expect(
        screen.getByText(/Sandbox backend 'e2b' is not available/)
      ).toBeInTheDocument();
    });
  });
});
