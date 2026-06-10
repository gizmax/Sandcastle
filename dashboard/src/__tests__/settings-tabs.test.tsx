/**
 * Settings hub (tabbed) tests
 *
 * Verifies the wave-2 Settings hub:
 *  - renders the correct tab content from the `?tab=` query param
 *  - defaults to General when the param is missing/invalid
 *  - switching tabs updates the URL search params (shareable deep links)
 *  - the tab bar exposes proper tablist/tab/tabpanel aria wiring + keyboard nav
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";

// -- Router mock: stateful useSearchParams driving the active tab -----------

let currentParams = new URLSearchParams();

vi.mock("react-router-dom", async () => {
  const React = await import("react");
  function useSearchParams() {
    const [params, setParams] = React.useState(currentParams);
    const setSearchParams = (
      next: URLSearchParams | ((p: URLSearchParams) => URLSearchParams),
    ) => {
      const resolved =
        typeof next === "function" ? next(new URLSearchParams(currentParams)) : next;
      currentParams = new URLSearchParams(resolved);
      setParams(currentParams);
    };
    return [params, setSearchParams] as const;
  }
  return {
    useSearchParams,
    useNavigate: () => vi.fn(),
    useLocation: () => ({ pathname: "/settings", state: null }),
    Link: ({ children, to, ...rest }: { children: React.ReactNode; to: string }) => (
      <a href={to} {...rest}>{children}</a>
    ),
  };
});

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

// Minimal settings payloads — the hub fetches these once on mount.
const mockSettings = {
  anthropic_api_key: "sk-ant-***",
  e2b_api_key: "",
  openai_api_key: "",
  minimax_api_key: "",
  openrouter_api_key: "",
  mistral_api_key: "",
  auth_required: false,
  dashboard_origin: "http://localhost:5173",
  default_max_cost_usd: 10,
  webhook_secret: "",
  log_level: "info",
  max_workflow_depth: 5,
  storage_backend: "local",
  storage_bucket: "",
  storage_endpoint: "",
  data_dir: "./data",
  workflows_dir: "./workflows",
  is_local_mode: true,
  database_url: "",
  redis_url: "",
};

const mockAdvisorStatus = {
  current_provider: "anthropic",
  current_model: null,
  data_residency: null,
  available_providers: [
    { id: "anthropic", name: "Anthropic", region: "us", configured: true, status: "ok" },
    { id: "mistral", name: "Mistral", region: "eu", configured: false, status: "idle" },
  ],
};

const mockApiGet = vi.fn();
const mockApiPatch = vi.fn();

vi.mock("@/api/client", () => ({
  api: {
    get: (url: string) => mockApiGet(url),
    post: vi.fn(() => Promise.resolve({ data: null, error: null })),
    patch: (url: string, body: unknown) => mockApiPatch(url, body),
    delete: vi.fn(() => Promise.resolve({ data: null, error: null })),
    hasStoredKey: () => false,
    storedKeyPrefix: () => null,
    clearStoredKey: vi.fn(),
  },
}));

// The Integrations panel hits /tools; keep it from erroring.
beforeEach(() => {
  currentParams = new URLSearchParams();
  vi.clearAllMocks();
  mockApiGet.mockImplementation((url: string) => {
    if (url === "/settings") return Promise.resolve({ data: mockSettings, error: null });
    if (url === "/advisor/status") return Promise.resolve({ data: mockAdvisorStatus, error: null });
    if (url === "/api-keys") return Promise.resolve({ data: [], error: null });
    if (url === "/tools") return Promise.resolve({ data: { tools: [], total: 0 }, error: null });
    return Promise.resolve({ data: null, error: null });
  });
  mockApiPatch.mockResolvedValue({ data: mockSettings, error: null });
});

import SettingsPage from "@/pages/SettingsPage";

function setTab(tab: string) {
  currentParams = new URLSearchParams(tab ? `tab=${tab}` : "");
}

async function renderHub() {
  await act(async () => {
    render(<SettingsPage />);
  });
  await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());
}

describe("Settings hub - tab rendering from ?tab=", () => {
  it("defaults to the General tab when no tab param is present", async () => {
    await renderHub();
    await waitFor(() => {
      expect(screen.getByText(/Default Max Cost per Run/i)).toBeInTheDocument();
    });
    const generalTab = screen.getByRole("tab", { name: /General/i });
    expect(generalTab).toHaveAttribute("aria-selected", "true");
  });

  it("renders the Keys tab when ?tab=keys", async () => {
    setTab("keys");
    await renderHub();
    await waitFor(() => {
      expect(screen.getByText("AI Provider Keys")).toBeInTheDocument();
    });
    expect(screen.getByRole("tab", { name: /Keys/i })).toHaveAttribute("aria-selected", "true");
  });

  it("renders the Providers tab when ?tab=providers", async () => {
    setTab("providers");
    await renderHub();
    await waitFor(() => {
      expect(screen.getByText("Anthropic")).toBeInTheDocument();
    });
    expect(screen.getByLabelText(/EU Data Residency/i)).toBeInTheDocument();
  });

  it("renders the Advanced tab when ?tab=advanced", async () => {
    setTab("advanced");
    await renderHub();
    await waitFor(() => {
      expect(screen.getByText("Infrastructure")).toBeInTheDocument();
    });
    expect(screen.getByText("Webhooks")).toBeInTheDocument();
  });

  it("falls back to General for an unknown tab value", async () => {
    setTab("bogus");
    await renderHub();
    const generalTab = screen.getByRole("tab", { name: /General/i });
    expect(generalTab).toHaveAttribute("aria-selected", "true");
  });
});

describe("Settings hub - switching tabs updates URL + content", () => {
  it("clicking the Advanced tab updates ?tab=advanced and shows its panel", async () => {
    await renderHub();
    await waitFor(() => screen.getByText(/Default Max Cost per Run/i));

    await act(async () => {
      fireEvent.click(screen.getByRole("tab", { name: /Advanced/i }));
    });

    await waitFor(() => {
      expect(screen.getByText("Infrastructure")).toBeInTheDocument();
    });
    expect(currentParams.get("tab")).toBe("advanced");
  });

  it("clicking General clears the tab param (canonical default URL)", async () => {
    setTab("advanced");
    await renderHub();
    await waitFor(() => screen.getByText("Infrastructure"));

    await act(async () => {
      fireEvent.click(screen.getByRole("tab", { name: /General/i }));
    });

    await waitFor(() => {
      expect(screen.getByText(/Default Max Cost per Run/i)).toBeInTheDocument();
    });
    expect(currentParams.get("tab")).toBeNull();
  });
});

describe("Settings hub - accessibility", () => {
  it("exposes a labelled tablist with one tab per section", async () => {
    await renderHub();
    const tablist = screen.getByRole("tablist");
    expect(tablist).toHaveAttribute("aria-label");
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(5);
  });

  it("only the active tab is in the tab order (roving tabindex)", async () => {
    await renderHub();
    const tabs = screen.getAllByRole("tab");
    const focusable = tabs.filter((t) => t.getAttribute("tabindex") === "0");
    expect(focusable).toHaveLength(1);
    expect(focusable[0]).toHaveAttribute("aria-selected", "true");
  });

  it("ArrowRight moves selection to the next tab", async () => {
    await renderHub();
    await waitFor(() => screen.getByText(/Default Max Cost per Run/i));
    const generalTab = screen.getByRole("tab", { name: /General/i });

    await act(async () => {
      fireEvent.keyDown(generalTab, { key: "ArrowRight" });
    });

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /Keys/i })).toHaveAttribute("aria-selected", "true");
    });
    expect(currentParams.get("tab")).toBe("keys");
  });
});
