/**
 * Night Shift page tests
 *
 * Covers: loading, error + retry, idle/armed empty states, headline stat math,
 * hero side stats, adapter table, detail drawer (model string + copy, lineage,
 * hyperparameters), and the parent-adapter jump.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";

// ============================================================================
// Mocks
// ============================================================================

vi.mock("@/api/client", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
    isMockMode: false,
    onMockChange: vi.fn(() => () => {}),
  },
}));

// Mock recharts - it uses browser APIs unavailable in jsdom
vi.mock("recharts", () => ({
  AreaChart: ({ children }: { children: React.ReactNode }) => <div data-testid="area-chart">{children}</div>,
  Area: () => <div data-testid="area" />,
  XAxis: () => <div />,
  YAxis: () => <div />,
  CartesianGrid: () => <div />,
  Tooltip: () => <div />,
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

// Mock the xyflow-based lineage graph - ReactFlow needs real layout/ResizeObserver
vi.mock("@/components/evolution/AdapterLineageGraph", () => ({
  AdapterLineageGraph: ({
    adapters,
    onSelect,
  }: {
    adapters: { adapter_id: string }[];
    onSelect: (a: { adapter_id: string }) => void;
  }) => (
    <div data-testid="lineage-graph">
      {adapters.map((a) => (
        <button key={a.adapter_id} onClick={() => onSelect(a)}>
          graph:{a.adapter_id}
        </button>
      ))}
    </div>
  ),
}));

import { api } from "@/api/client";
import NightShiftPage from "@/pages/NightShiftPage";

const mockApi = api as unknown as { get: ReturnType<typeof vi.fn> };

// ============================================================================
// Fixtures
// ============================================================================

const NOW_SEC = Math.floor(Date.now() / 1000);

const ADAPTERS = [
  {
    adapter_id: "mock-sonnet-gen1",
    base_model: "sonnet",
    metrics: { loss: 0.31, eval_score: 0.71 },
    samples: 18,
    lora_config: { r: 8, alpha: 16, lr: 0.0001, epochs: 3 },
    dataset_hash: "9c4f1d2e7ab8350a6c1e9f2b4d8a7c3e5f0b1a2d4c6e8f0a1b3c5d7e9f1a3b5c",
    parent_adapter_id: null,
    created_at: NOW_SEC - 2 * 86400,
    served: false,
  },
  {
    adapter_id: "mock-sonnet-gen2",
    base_model: "sonnet",
    metrics: { loss: 0.19, eval_score: 0.84 },
    samples: 34,
    lora_config: { r: 8, alpha: 16, lr: 0.0001, epochs: 3 },
    dataset_hash: "3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d",
    parent_adapter_id: "mock-sonnet-gen1",
    created_at: NOW_SEC - 86400,
    served: true,
  },
];

const NIGHTS = {
  nights: [
    {
      night: "2026-06-07",
      mutations_tried: 3,
      mutations_kept: 1,
      adapters_produced: 1,
      best_eval_score: 0.71,
      best_delta: null,
      adapter_ids: ["mock-sonnet-gen1"],
    },
    {
      night: "2026-06-08",
      mutations_tried: 2,
      mutations_kept: 1,
      adapters_produced: 1,
      best_eval_score: 0.84,
      // prev best 0.71 -> +0.13 absolute -> +18.3% relative
      best_delta: 0.13,
      adapter_ids: ["mock-sonnet-gen2"],
    },
  ],
  enabled: true,
  total_adapters: 2,
};

function mockData({
  adapters = ADAPTERS,
  nights = NIGHTS,
}: {
  adapters?: typeof ADAPTERS | [];
  nights?: typeof NIGHTS;
} = {}) {
  mockApi.get.mockImplementation((path: string) => {
    if (path === "/adapters") return Promise.resolve({ data: adapters, error: null });
    if (path === "/self-tune/nights") return Promise.resolve({ data: nights, error: null });
    return Promise.resolve({ data: null, error: null });
  });
}

beforeEach(() => {
  vi.clearAllMocks();
});

// ============================================================================
// Tests
// ============================================================================

describe("NightShiftPage", () => {
  it("shows loading spinner initially", () => {
    mockApi.get.mockReturnValue(new Promise(() => {}));
    render(<NightShiftPage />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("shows error state with retry on API failure", async () => {
    mockApi.get.mockRejectedValue(new Error("Network error"));
    render(<NightShiftPage />);
    await waitFor(() => {
      expect(screen.getByText("Could not connect to the API server")).toBeInTheDocument();
    });

    mockData();
    fireEvent.click(screen.getByText("Retry"));
    await waitFor(() => {
      expect(screen.getByText("better overnight")).toBeInTheDocument();
    });
  });

  it("shows the idle empty state with a docs link when self-tune is off", async () => {
    mockData({ adapters: [], nights: { ...NIGHTS, nights: [], enabled: false, total_adapters: 0 } });
    render(<NightShiftPage />);
    await waitFor(() => {
      expect(screen.getByText("Night Shift is idle")).toBeInTheDocument();
    });
    expect(screen.getByText("evolution_auto_finetune")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /read the docs/i })).toHaveAttribute(
      "href",
      expect.stringContaining("github.com")
    );
    // How-it-works strip renders with zero adapters
    expect(screen.getByText("Evolve")).toBeInTheDocument();
    expect(screen.getByText("Train")).toBeInTheDocument();
    expect(screen.getByText("Serve")).toBeInTheDocument();
  });

  it("shows the armed empty state when self-tune is on but nothing trained yet", async () => {
    mockData({ adapters: [], nights: { ...NIGHTS, nights: [], enabled: true, total_adapters: 0 } });
    render(<NightShiftPage />);
    await waitFor(() => {
      expect(screen.getByText("Night Shift is armed")).toBeInTheDocument();
    });
  });

  it("renders the headline improvement stat from the most recent night", async () => {
    mockData();
    render(<NightShiftPage />);
    await waitFor(() => {
      // 0.13 / 0.71 = +18.3%
      expect(screen.getByText("+18.3%")).toBeInTheDocument();
    });
    expect(screen.getByText("Your agent got")).toBeInTheDocument();
    expect(screen.getByText("better overnight")).toBeInTheDocument();
    expect(screen.getByText(/0\.71 → 0\.84/)).toBeInTheDocument();
  });

  it("falls back to a first-night headline when there is no delta yet", async () => {
    mockData({
      adapters: [ADAPTERS[0]],
      nights: { ...NIGHTS, nights: [NIGHTS.nights[0]], total_adapters: 1 },
    });
    render(<NightShiftPage />);
    await waitFor(() => {
      // Headline stat + table eval cell both show the score
      expect(screen.getAllByText("0.71").length).toBeGreaterThanOrEqual(1);
    });
    expect(screen.getByText(/first night complete/i)).toBeInTheDocument();
  });

  it("shows the serving adapter and counts in the hero side stats", async () => {
    mockData();
    render(<NightShiftPage />);
    await waitFor(() => {
      expect(screen.getByText("Serving now")).toBeInTheDocument();
    });
    expect(screen.getByText("Adapters trained")).toBeInTheDocument();
    expect(screen.getByText("Training nights")).toBeInTheDocument();
    // Served adapter id appears in hero stat + lineage graph + table
    expect(screen.getAllByText(/mock-sonnet-gen2/).length).toBeGreaterThanOrEqual(2);
  });

  it("lists all adapters in the table with a SERVING badge", async () => {
    mockData();
    render(<NightShiftPage />);
    await waitFor(() => {
      expect(screen.getByText("All adapters (2)")).toBeInTheDocument();
    });
    const table = screen.getByRole("table", { name: "Trained adapters" });
    expect(within(table).getByText("mock-sonnet-gen1")).toBeInTheDocument();
    expect(within(table).getByText("mock-sonnet-gen2")).toBeInTheDocument();
    expect(within(table).getByText("SERVING")).toBeInTheDocument();
    expect(within(table).getByText("archived")).toBeInTheDocument();
  });

  it("opens the detail drawer with model string, metadata, and hyperparameters", async () => {
    mockData();
    render(<NightShiftPage />);
    await waitFor(() => {
      expect(screen.getByRole("table", { name: "Trained adapters" })).toBeInTheDocument();
    });

    const table = screen.getByRole("table", { name: "Trained adapters" });
    fireEvent.click(within(table).getByText("mock-sonnet-gen2"));

    const drawer = await screen.findByRole("dialog", { name: "Adapter details" });
    expect(within(drawer).getByText("adapter/mock-sonnet-gen2")).toBeInTheDocument();
    expect(within(drawer).getByText("sonnet")).toBeInTheDocument();
    expect(within(drawer).getByText("34")).toBeInTheDocument();
    expect(within(drawer).getByText(/3c4d5e6f7a8b/)).toBeInTheDocument(); // truncated dataset hash
    expect(within(drawer).getByText("LoRA rank (r)")).toBeInTheDocument();
    expect(within(drawer).getByText("Learning rate")).toBeInTheDocument();
    expect(
      within(drawer).getByRole("button", { name: /copy model string/i })
    ).toBeInTheDocument();
  });

  it("jumps to the parent adapter from the drawer lineage section", async () => {
    mockData();
    render(<NightShiftPage />);
    await waitFor(() => {
      expect(screen.getByRole("table", { name: "Trained adapters" })).toBeInTheDocument();
    });

    fireEvent.click(
      within(screen.getByRole("table", { name: "Trained adapters" })).getByText("mock-sonnet-gen2")
    );
    const drawer = await screen.findByRole("dialog", { name: "Adapter details" });
    expect(within(drawer).getByText("Parent adapter")).toBeInTheDocument();

    fireEvent.click(within(drawer).getByText("mock-sonnet-gen1"));
    await waitFor(() => {
      expect(screen.getByText("adapter/mock-sonnet-gen1")).toBeInTheDocument();
    });
    // Root adapter -> first generation message instead of a parent link
    expect(screen.getByText(/first generation/i)).toBeInTheDocument();
  });

  it("closes the drawer on Escape", async () => {
    mockData();
    render(<NightShiftPage />);
    await waitFor(() => {
      expect(screen.getByRole("table", { name: "Trained adapters" })).toBeInTheDocument();
    });

    fireEvent.click(
      within(screen.getByRole("table", { name: "Trained adapters" })).getByText("mock-sonnet-gen1")
    );
    await screen.findByRole("dialog", { name: "Adapter details" });

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Adapter details" })).not.toBeInTheDocument();
    });
  });
});
