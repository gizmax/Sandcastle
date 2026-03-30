/**
 * v28 Regression Tests
 *
 * Covers 6 feature areas introduced in v0.28:
 * 1. BatchRunModal       - JSON textarea, max parallel slider, start batch, progress, results, close
 * 2. ScheduleMonitorPage - schedule cards, status badges, success rate, next run, pause/resume, empty
 * 3. VersionHistoryModal - version timeline, details on select, compare diff, Escape close
 * 4. Run Compare (Runs)  - checkboxes, compare button visibility, navigation, hidden when !=2
 * 5. ActivityFeed        - timeline rendering, icons + relative time, view all link
 * 6. Update Banner       - shows when available, dismiss, update now navigation
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
  useLocation: () => ({ pathname: "/runs", state: null }),
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

// Mock sub-components that VersionHistoryModal imports
vi.mock("@/components/workflows/VersionHistory", () => ({
  VersionHistory: ({
    versions,
    selectedVersion,
    onSelect,
  }: {
    versions: Array<{ id: string; version: number; status: string; description: string }>;
    selectedVersion: number | null;
    onSelect: (v: number) => void;
    onPromote: (v: number) => void;
  }) => (
    <div data-testid="version-timeline">
      {versions.map((v) => (
        <div
          key={v.id}
          data-testid={`version-item-${v.version}`}
          data-selected={selectedVersion === v.version ? "true" : "false"}
          onClick={() => onSelect(v.version)}
        >
          v{v.version} - {v.status} - {v.description}
        </div>
      ))}
    </div>
  ),
}));

vi.mock("@/components/workflows/VersionStatusBadge", () => ({
  VersionStatusBadge: ({ status }: { status: string }) => (
    <span data-testid={`status-badge-${status}`}>{status}</span>
  ),
}));

vi.mock("@/components/workflows/VersionDiffModal", () => ({
  VersionDiffModal: ({
    open,
    onClose,
    versionA,
    versionB,
  }: {
    open: boolean;
    onClose: () => void;
    workflowName: string;
    versionA: number;
    versionB: number;
    availableVersions?: number[];
  }) =>
    open ? (
      <div data-testid="diff-modal">
        Diff: v{versionA} vs v{versionB}
        <button onClick={onClose}>Close diff</button>
      </div>
    ) : null,
}));

// Mock cron utilities used by ScheduleMonitorPage
vi.mock("@/lib/cron", () => ({
  cronToHuman: (expr: string) => `cron: ${expr}`,
  getNextRuns: () => [new Date(Date.now() + 3600_000)],
  parseCron: () => ({ frequency: "daily", hour: 9, minute: 0, interval: 6, weekdays: [], monthDays: [] }),
  buildCron: () => "0 9 * * *",
}));

// Mock the shared components used by ScheduleMonitorPage
vi.mock("@/components/shared/LoadingSpinner", () => ({
  LoadingSpinner: ({ size }: { size?: string }) => (
    <div data-testid="loading-spinner" data-size={size}>Loading...</div>
  ),
}));

vi.mock("@/components/shared/EmptyState", () => ({
  EmptyState: ({ title, description }: { title: string; description: string; icon?: unknown }) => (
    <div data-testid="empty-state">
      <h3>{title}</h3>
      <p>{description}</p>
    </div>
  ),
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

function okResponse<T>(data: T) {
  return { data, error: null };
}

// errorResponse available for future use
// function errorResponse(msg: string) { return { data: null, error: { message: msg } }; }

// ============================================================================
// 1. BatchRunModal (7 tests)
// ============================================================================

import { BatchRunModal } from "@/components/workflows/BatchRunModal";

describe("BatchRunModal", () => {
  const defaultProps = {
    open: true,
    workflowName: "data-pipeline",
    fileName: "data-pipeline.yaml",
    onClose: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    defaultProps.onClose.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders with JSON textarea and Start Batch button when open", async () => {
    await act(async () => {
      render(<BatchRunModal {...defaultProps} />);
    });

    // Modal title should contain the workflow name
    expect(screen.getByText(/Batch Run - data-pipeline/)).toBeInTheDocument();

    // JSON textarea should be present with default example content
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(textarea).toBeInTheDocument();
    expect(textarea.value).toContain("First item to process");

    // Start Batch button should be visible
    expect(screen.getByText("Start Batch")).toBeInTheDocument();
  });

  it("has max parallel slider with default value of 5", async () => {
    await act(async () => {
      render(<BatchRunModal {...defaultProps} />);
    });

    // Slider should be present
    const slider = screen.getByRole("slider") as HTMLInputElement;
    expect(slider).toBeInTheDocument();
    expect(slider.value).toBe("5");

    // Label should show "Max parallel"
    expect(screen.getByText("Max parallel")).toBeInTheDocument();
    // Current value should be displayed
    expect(screen.getByText("5")).toBeInTheDocument();
  });

  it("Start Batch triggers POST to /workflows/{name}/batch", async () => {
    mockApi.post.mockResolvedValue(okResponse({ batch_id: "batch-001" }));
    // Mock polling response
    mockApi.get.mockResolvedValue(okResponse({
      batch_id: "batch-001",
      workflow: "data-pipeline",
      status: "running",
      total: 3,
      completed: 0,
      failed: 0,
      running: 1,
      pending: 2,
      total_cost_usd: 0,
      items: [],
    }));

    await act(async () => {
      render(<BatchRunModal {...defaultProps} />);
    });

    // Click Start Batch
    await act(async () => {
      fireEvent.click(screen.getByText("Start Batch"));
    });

    // Should call POST /workflows/data-pipeline/batch
    expect(mockApi.post).toHaveBeenCalledWith(
      "/workflows/data-pipeline/batch",
      expect.objectContaining({
        items: expect.any(Array),
        max_parallel: 5,
      }),
    );
  });

  it("progress bar appears after batch starts", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });

    mockApi.post.mockResolvedValue(okResponse({ batch_id: "batch-002" }));
    mockApi.get.mockResolvedValue(okResponse({
      batch_id: "batch-002",
      workflow: "data-pipeline",
      status: "running",
      total: 3,
      completed: 1,
      failed: 0,
      running: 1,
      pending: 1,
      total_cost_usd: 0.05,
      items: [
        { index: 0, status: "completed", run_id: "r1", cost_usd: 0.05, error: null },
        { index: 1, status: "running", run_id: "r2", cost_usd: 0, error: null },
        { index: 2, status: "pending", run_id: null, cost_usd: 0, error: null },
      ],
    }));

    await act(async () => {
      render(<BatchRunModal {...defaultProps} />);
    });

    await act(async () => {
      fireEvent.click(screen.getByText("Start Batch"));
    });

    // Wait for the batch status to be fetched and progress shown
    await waitFor(() => {
      expect(screen.getByText("1 / 3")).toBeInTheDocument();
    });

    // Percentage should display
    expect(screen.getByText("33%")).toBeInTheDocument();
  });

  it("results show passed/failed counts after completion", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });

    mockApi.post.mockResolvedValue(okResponse({ batch_id: "batch-003" }));
    mockApi.get.mockResolvedValue(okResponse({
      batch_id: "batch-003",
      workflow: "data-pipeline",
      status: "completed",
      total: 3,
      completed: 2,
      failed: 1,
      running: 0,
      pending: 0,
      total_cost_usd: 0.12,
      items: [
        { index: 0, status: "completed", run_id: "r1", cost_usd: 0.04, error: null },
        { index: 1, status: "completed", run_id: "r2", cost_usd: 0.05, error: null },
        { index: 2, status: "failed", run_id: "r3", cost_usd: 0.03, error: "timeout" },
      ],
    }));

    await act(async () => {
      render(<BatchRunModal {...defaultProps} />);
    });

    await act(async () => {
      fireEvent.click(screen.getByText("Start Batch"));
    });

    // Wait for results to display
    await waitFor(() => {
      expect(screen.getByText("Passed")).toBeInTheDocument();
    });

    // Passed count
    expect(screen.getByText("2")).toBeInTheDocument();
    // Failed count
    expect(screen.getByText("1")).toBeInTheDocument();
    // Cost display
    expect(screen.getByText("$0.12")).toBeInTheDocument();
  });

  it("modal is closeable via Cancel button", async () => {
    await act(async () => {
      render(<BatchRunModal {...defaultProps} />);
    });

    fireEvent.click(screen.getByText("Cancel"));
    expect(defaultProps.onClose).toHaveBeenCalledTimes(1);
  });

  it("returns null when open is false", () => {
    const { container } = render(
      <BatchRunModal {...defaultProps} open={false} />,
    );
    // Should render nothing
    expect(container.innerHTML).toBe("");
  });
});

// ============================================================================
// 2. ScheduleMonitorPage (6 tests)
// ============================================================================

import ScheduleMonitorPage from "@/pages/ScheduleMonitorPage";

function makeSchedule(overrides: Partial<{
  id: string;
  workflow_name: string;
  cron_expression: string;
  enabled: boolean;
  status: string;
  success_rate: number;
  next_run_at: string | null;
  last_run_at: string | null;
  last_run_status: string | null;
}> = {}) {
  return {
    id: overrides.id ?? "sched-1",
    workflow_name: overrides.workflow_name ?? "nightly-report",
    cron_expression: overrides.cron_expression ?? "0 2 * * *",
    enabled: overrides.enabled ?? true,
    last_run_id: null,
    last_run_at: overrides.last_run_at ?? null,
    last_run_status: overrides.last_run_status ?? null,
    next_run_at: overrides.next_run_at ?? new Date(Date.now() + 3600_000).toISOString(),
    success_rate: overrides.success_rate ?? 0.95,
    status: overrides.status ?? "active",
    created_at: null,
  };
}

describe("ScheduleMonitorPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders schedule cards with workflow names", async () => {
    mockApi.get.mockResolvedValue(okResponse([
      makeSchedule({ id: "s1", workflow_name: "nightly-report" }),
      makeSchedule({ id: "s2", workflow_name: "etl-pipeline" }),
    ]));

    await act(async () => {
      render(<ScheduleMonitorPage />);
    });

    await waitFor(() => {
      expect(screen.getByText("nightly-report")).toBeInTheDocument();
    });
    expect(screen.getByText("etl-pipeline")).toBeInTheDocument();
  });

  it("shows status badges (Active/Paused/Failing)", async () => {
    mockApi.get.mockResolvedValue(okResponse([
      makeSchedule({ id: "s1", workflow_name: "active-wf", status: "active" }),
      makeSchedule({ id: "s2", workflow_name: "paused-wf", status: "paused", enabled: false }),
      makeSchedule({ id: "s3", workflow_name: "failing-wf", status: "failing" }),
    ]));

    await act(async () => {
      render(<ScheduleMonitorPage />);
    });

    await waitFor(() => {
      expect(screen.getByText("Active")).toBeInTheDocument();
    });
    expect(screen.getByText("Paused")).toBeInTheDocument();
    expect(screen.getByText("Failing")).toBeInTheDocument();
  });

  it("shows success rate percentage", async () => {
    mockApi.get.mockResolvedValue(okResponse([
      makeSchedule({ id: "s1", success_rate: 0.87 }),
    ]));

    await act(async () => {
      render(<ScheduleMonitorPage />);
    });

    await waitFor(() => {
      expect(screen.getByText("87%")).toBeInTheDocument();
    });

    // The "Success Rate" label should be present
    expect(screen.getByText("Success Rate")).toBeInTheDocument();
  });

  it("shows next run countdown", async () => {
    const futureDate = new Date(Date.now() + 3 * 3600_000).toISOString();
    mockApi.get.mockResolvedValue(okResponse([
      makeSchedule({ id: "s1", next_run_at: futureDate }),
    ]));

    await act(async () => {
      render(<ScheduleMonitorPage />);
    });

    await waitFor(() => {
      // The countdown should show hours/minutes format
      expect(screen.getByText("Next Run")).toBeInTheDocument();
    });

    // Should not show "-" since the schedule is enabled and has a next run time
    const nextRunValues = screen.getAllByText(/\d+h|\d+m|imminent/);
    expect(nextRunValues.length).toBeGreaterThanOrEqual(1);
  });

  it("Pause/Resume buttons are present for each schedule", async () => {
    mockApi.get.mockResolvedValue(okResponse([
      makeSchedule({ id: "s1", workflow_name: "active-wf", enabled: true, status: "active" }),
      makeSchedule({ id: "s2", workflow_name: "paused-wf", enabled: false, status: "paused" }),
    ]));

    await act(async () => {
      render(<ScheduleMonitorPage />);
    });

    await waitFor(() => {
      expect(screen.getByText("active-wf")).toBeInTheDocument();
    });

    // Active schedule should have a Pause button
    expect(screen.getByText("Pause")).toBeInTheDocument();
    // Paused schedule should have a Resume button
    expect(screen.getByText("Resume")).toBeInTheDocument();
  });

  it("shows empty state when no schedules exist", async () => {
    mockApi.get.mockResolvedValue(okResponse([]));

    await act(async () => {
      render(<ScheduleMonitorPage />);
    });

    await waitFor(() => {
      expect(screen.getByText("No schedules configured")).toBeInTheDocument();
    });
  });
});

// ============================================================================
// 3. VersionHistoryModal (4 tests)
// ============================================================================

import { VersionHistoryModal } from "@/components/workflows/VersionHistoryModal";

const versionListData = {
  workflow_name: "code-review",
  production_version: 2,
  staging_version: 3,
  latest_draft_version: 4,
  versions: [
    {
      id: "v1-id",
      workflow_name: "code-review",
      version: 1,
      status: "archived",
      description: "Initial version",
      steps_count: 3,
      checksum: "abc123",
      created_at: "2026-03-20T10:00:00Z",
      promoted_at: null,
    },
    {
      id: "v2-id",
      workflow_name: "code-review",
      version: 2,
      status: "production",
      description: "Added validation step",
      steps_count: 4,
      checksum: "def456",
      created_at: "2026-03-22T14:00:00Z",
      promoted_at: "2026-03-22T15:00:00Z",
    },
    {
      id: "v3-id",
      workflow_name: "code-review",
      version: 3,
      status: "staging",
      description: "New summarizer",
      steps_count: 5,
      checksum: "ghi789",
      created_at: "2026-03-24T09:00:00Z",
      promoted_at: null,
    },
    {
      id: "v4-id",
      workflow_name: "code-review",
      version: 4,
      status: "draft",
      description: "Experimental branch",
      steps_count: 6,
      checksum: "jkl012",
      created_at: "2026-03-25T16:00:00Z",
      promoted_at: null,
    },
  ],
};

describe("VersionHistoryModal", () => {
  const defaultProps = {
    open: true,
    onClose: vi.fn(),
    workflowName: "code-review",
  };

  beforeEach(() => {
    vi.clearAllMocks();
    defaultProps.onClose.mockReset();
  });

  it("renders version timeline with all versions", async () => {
    mockApi.get.mockResolvedValue(okResponse(versionListData));

    await act(async () => {
      render(<VersionHistoryModal {...defaultProps} />);
    });

    // Wait for data to load
    await waitFor(() => {
      expect(screen.getByTestId("version-timeline")).toBeInTheDocument();
    });

    // All 4 versions should be rendered
    expect(screen.getByTestId("version-item-1")).toBeInTheDocument();
    expect(screen.getByTestId("version-item-2")).toBeInTheDocument();
    expect(screen.getByTestId("version-item-3")).toBeInTheDocument();
    expect(screen.getByTestId("version-item-4")).toBeInTheDocument();
  });

  it("shows version details when a version is selected", async () => {
    mockApi.get.mockResolvedValue(okResponse(versionListData));

    await act(async () => {
      render(<VersionHistoryModal {...defaultProps} />);
    });

    await waitFor(() => {
      expect(screen.getByTestId("version-timeline")).toBeInTheDocument();
    });

    // Click on version 3 to select it
    await act(async () => {
      fireEvent.click(screen.getByTestId("version-item-3"));
    });

    // Detail section should show version 3 info
    await waitFor(() => {
      expect(screen.getByText("Version 3 Details")).toBeInTheDocument();
    });

    // Should display the status badge for staging (one in header, one in detail)
    const stagingBadges = screen.getAllByTestId("status-badge-staging");
    expect(stagingBadges.length).toBeGreaterThanOrEqual(2);

    // Steps count should appear
    expect(screen.getByText("5")).toBeInTheDocument();
  });

  it("Compare button opens diff view", async () => {
    mockApi.get.mockResolvedValue(okResponse(versionListData));

    await act(async () => {
      render(<VersionHistoryModal {...defaultProps} />);
    });

    await waitFor(() => {
      expect(screen.getByTestId("version-timeline")).toBeInTheDocument();
    });

    // The Compare button should be present (since there are >= 2 versions)
    const compareBtn = screen.getByText("Compare");
    expect(compareBtn).toBeInTheDocument();

    // Click Compare
    await act(async () => {
      fireEvent.click(compareBtn);
    });

    // The diff modal should appear
    await waitFor(() => {
      expect(screen.getByTestId("diff-modal")).toBeInTheDocument();
    });

    // It should compare the two most recent versions (v3 and v4)
    expect(screen.getByText("Diff: v3 vs v4")).toBeInTheDocument();
  });

  it("is closeable via Escape key", async () => {
    mockApi.get.mockResolvedValue(okResponse(versionListData));

    await act(async () => {
      render(<VersionHistoryModal {...defaultProps} />);
    });

    await waitFor(() => {
      expect(screen.getByTestId("version-timeline")).toBeInTheDocument();
    });

    // Press Escape
    await act(async () => {
      fireEvent.keyDown(document, { key: "Escape" });
    });

    expect(defaultProps.onClose).toHaveBeenCalledTimes(1);
  });
});

// ============================================================================
// 4. Run Compare on Runs page (4 tests)
// ============================================================================

// The Runs page has deep dependencies (useRuns hook, RunsTable, saved filters,
// etc). We test the compare logic by directly rendering the page with mocked
// hooks and verifying behavior through the DOM.

vi.mock("@/hooks/useRuns", () => ({
  useRuns: vi.fn(() => ({
    runs: [
      { run_id: "run-aaa", workflow_name: "wf1", status: "completed", total_cost_usd: 0.1, started_at: "2026-03-25T10:00:00Z", completed_at: "2026-03-25T10:01:00Z" },
      { run_id: "run-bbb", workflow_name: "wf1", status: "completed", total_cost_usd: 0.2, started_at: "2026-03-25T11:00:00Z", completed_at: "2026-03-25T11:02:00Z" },
      { run_id: "run-ccc", workflow_name: "wf2", status: "failed", total_cost_usd: 0.05, started_at: "2026-03-25T12:00:00Z", completed_at: "2026-03-25T12:00:30Z" },
    ],
    total: 3,
    loading: false,
    error: null,
    refetch: vi.fn(),
  })),
}));

vi.mock("@/hooks/useSavedFilters", () => ({
  useSavedFilters: () => ({
    savedFilters: [],
    saveCurrentFilter: vi.fn(),
    deleteFilter: vi.fn(),
    getFilter: vi.fn(),
  }),
}));

vi.mock("@/components/runs/RunsTable", () => ({
  RunsTable: ({
    runs,
    selectedIds,
    onSelectionChange,
  }: {
    runs: Array<{ run_id: string; workflow_name: string; status: string }>;
    allRuns?: unknown[];
    total: number;
    limit: number;
    offset: number;
    onPageChange: (n: number) => void;
    selectedIds?: Set<string>;
    onSelectionChange?: (ids: Set<string>) => void;
  }) => (
    <div data-testid="runs-table">
      {runs.map((r) => (
        <div key={r.run_id} data-testid={`run-row-${r.run_id}`}>
          <input
            type="checkbox"
            data-testid={`checkbox-${r.run_id}`}
            checked={selectedIds?.has(r.run_id) ?? false}
            onChange={() => {
              if (!onSelectionChange) return;
              const next = new Set(selectedIds);
              if (next.has(r.run_id)) next.delete(r.run_id);
              else next.add(r.run_id);
              onSelectionChange(next);
            }}
          />
          <span>{r.workflow_name}</span>
        </div>
      ))}
    </div>
  ),
}));

vi.mock("@/components/shared/ConfirmDialog", () => ({
  ConfirmDialog: () => null,
}));

vi.mock("@/components/shared/ContextBanner", () => ({
  ContextBanner: () => null,
}));

vi.mock("@/components/ui/Skeleton", () => ({
  Skeleton: () => <div data-testid="skeleton" />,
}));

vi.mock("@/lib/exportCsv", () => ({
  exportToCsv: vi.fn(),
}));

import Runs from "@/pages/Runs";

describe("Run Compare on Runs page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockReset();
    // Mock stats and workflows API calls used by Runs page on mount
    mockApi.get.mockImplementation((url: string) => {
      if (url === "/workflows") return Promise.resolve(okResponse([]));
      if (url === "/stats") return Promise.resolve(okResponse({ success_rate: 1.0 }));
      return Promise.resolve(okResponse(null));
    });
  });

  it("checkboxes appear on run rows", async () => {
    await act(async () => {
      render(<Runs />);
    });

    await waitFor(() => {
      expect(screen.getByTestId("runs-table")).toBeInTheDocument();
    });

    // Each run row should have a checkbox
    expect(screen.getByTestId("checkbox-run-aaa")).toBeInTheDocument();
    expect(screen.getByTestId("checkbox-run-bbb")).toBeInTheDocument();
    expect(screen.getByTestId("checkbox-run-ccc")).toBeInTheDocument();
  });

  it("Compare button appears when exactly 2 runs are selected", async () => {
    await act(async () => {
      render(<Runs />);
    });

    await waitFor(() => {
      expect(screen.getByTestId("runs-table")).toBeInTheDocument();
    });

    // Select first run
    await act(async () => {
      fireEvent.click(screen.getByTestId("checkbox-run-aaa"));
    });

    // With 1 selected, Compare should NOT appear
    expect(screen.queryByText("Compare")).not.toBeInTheDocument();

    // Select second run
    await act(async () => {
      fireEvent.click(screen.getByTestId("checkbox-run-bbb"));
    });

    // With 2 selected, Compare button should appear
    await waitFor(() => {
      expect(screen.getByText("Compare")).toBeInTheDocument();
    });
  });

  it("Compare button navigates to /runs/compare with selected IDs", async () => {
    await act(async () => {
      render(<Runs />);
    });

    await waitFor(() => {
      expect(screen.getByTestId("runs-table")).toBeInTheDocument();
    });

    // Select two runs
    await act(async () => {
      fireEvent.click(screen.getByTestId("checkbox-run-aaa"));
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("checkbox-run-bbb"));
    });

    await waitFor(() => {
      expect(screen.getByText("Compare")).toBeInTheDocument();
    });

    // Click Compare
    await act(async () => {
      fireEvent.click(screen.getByText("Compare"));
    });

    // Should navigate to compare page with query params
    expect(mockNavigate).toHaveBeenCalledWith(
      expect.stringContaining("/runs/compare"),
    );
    expect(mockNavigate).toHaveBeenCalledWith(
      expect.stringContaining("run_a="),
    );
    expect(mockNavigate).toHaveBeenCalledWith(
      expect.stringContaining("run_b="),
    );
  });

  it("Compare button is hidden when not exactly 2 runs are selected", async () => {
    await act(async () => {
      render(<Runs />);
    });

    await waitFor(() => {
      expect(screen.getByTestId("runs-table")).toBeInTheDocument();
    });

    // 0 selected -> no Compare
    expect(screen.queryByText("Compare")).not.toBeInTheDocument();

    // Select 1 run
    await act(async () => {
      fireEvent.click(screen.getByTestId("checkbox-run-aaa"));
    });
    expect(screen.queryByText("Compare")).not.toBeInTheDocument();

    // Select 2 runs -> Compare appears
    await act(async () => {
      fireEvent.click(screen.getByTestId("checkbox-run-bbb"));
    });
    await waitFor(() => {
      expect(screen.getByText("Compare")).toBeInTheDocument();
    });

    // Select 3rd run -> Compare disappears
    await act(async () => {
      fireEvent.click(screen.getByTestId("checkbox-run-ccc"));
    });
    await waitFor(() => {
      expect(screen.queryByText("Compare")).not.toBeInTheDocument();
    });
  });
});

// ============================================================================
// 5. ActivityFeed (3 tests)
// ============================================================================

// Mock the useActivityFeed hook with controlled data
const mockMarkAsRead = vi.fn();
const mockMarkAllRead = vi.fn();

vi.mock("@/hooks/useActivityFeed", () => ({
  useActivityFeed: () => ({
    events: [
      {
        id: "evt-1",
        type: "run_completed",
        title: "Run completed",
        description: "'data-pipeline' completed in 45s",
        timestamp: new Date(Date.now() - 300_000), // 5 min ago
        read: false,
        link: "/runs/evt-1",
      },
      {
        id: "evt-2",
        type: "run_failed",
        title: "Run failed",
        description: "'image-gen' failed: timeout after 30s",
        timestamp: new Date(Date.now() - 600_000), // 10 min ago
        read: true,
        link: "/runs/evt-2",
      },
      {
        id: "evt-3",
        type: "schedule_fired",
        title: "Schedule triggered",
        description: "'Nightly cleanup' triggered",
        timestamp: new Date(Date.now() - 1800_000), // 30 min ago
        read: false,
        link: "/schedules",
      },
    ],
    unreadCount: 2,
    markAsRead: mockMarkAsRead,
    markAllRead: mockMarkAllRead,
    filterEvents: (filter: string) => {
      const events = [
        {
          id: "evt-1",
          type: "run_completed" as const,
          title: "Run completed",
          description: "'data-pipeline' completed in 45s",
          timestamp: new Date(Date.now() - 300_000),
          read: false,
          link: "/runs/evt-1",
        },
        {
          id: "evt-2",
          type: "run_failed" as const,
          title: "Run failed",
          description: "'image-gen' failed: timeout after 30s",
          timestamp: new Date(Date.now() - 600_000),
          read: true,
          link: "/runs/evt-2",
        },
        {
          id: "evt-3",
          type: "schedule_fired" as const,
          title: "Schedule triggered",
          description: "'Nightly cleanup' triggered",
          timestamp: new Date(Date.now() - 1800_000),
          read: false,
          link: "/schedules",
        },
      ];
      if (filter === "all") return events;
      if (filter === "runs") return events.filter((e) => e.type.startsWith("run_"));
      return events.filter((e) => !e.type.startsWith("run_"));
    },
    newEventFlag: 0,
    isDemo: true,
  }),
}));

import { ActivityFeed } from "@/components/layout/ActivityFeed";

describe("ActivityFeed", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockReset();
  });

  it("renders audit events in timeline when opened", async () => {
    await act(async () => {
      render(<ActivityFeed />);
    });

    // Click the feed button to open the dropdown
    const feedButton = screen.getByLabelText(/Activity feed/);
    await act(async () => {
      fireEvent.click(feedButton);
    });

    // Events should be visible
    await waitFor(() => {
      expect(screen.getByText("Run completed")).toBeInTheDocument();
    });
    expect(screen.getByText("Run failed")).toBeInTheDocument();
    expect(screen.getByText("Schedule triggered")).toBeInTheDocument();

    // Descriptions should be present
    expect(screen.getByText("'data-pipeline' completed in 45s")).toBeInTheDocument();
  });

  it("shows event icons and relative time", async () => {
    await act(async () => {
      render(<ActivityFeed />);
    });

    // Open the feed
    const feedButton = screen.getByLabelText(/Activity feed/);
    await act(async () => {
      fireEvent.click(feedButton);
    });

    await waitFor(() => {
      expect(screen.getByText("Run completed")).toBeInTheDocument();
    });

    // The events list should have the list role
    const eventList = screen.getByRole("list", { name: /Activity events/ });
    expect(eventList).toBeInTheDocument();

    // Relative timestamps should appear (e.g., "5m ago", "10m ago", "30m ago")
    // The formatRelativeTime utility will produce something like "Xm ago"
    const timeElements = eventList.querySelectorAll("span");
    const timeTexts = Array.from(timeElements).map((el) => el.textContent);
    // At least one should contain a time indicator
    expect(timeTexts.some((t) => t && /\d+/.test(t))).toBe(true);
  });

  it("'View all activity' link is present", async () => {
    await act(async () => {
      render(<ActivityFeed />);
    });

    // Open the feed
    const feedButton = screen.getByLabelText(/Activity feed/);
    await act(async () => {
      fireEvent.click(feedButton);
    });

    await waitFor(() => {
      expect(screen.getByText("View all activity")).toBeInTheDocument();
    });

    // Click the link
    await act(async () => {
      fireEvent.click(screen.getByText("View all activity"));
    });

    // Should navigate to /runs
    expect(mockNavigate).toHaveBeenCalledWith("/runs");
  });
});

// ============================================================================
// 6. Update Banner in Header (3 tests)
// ============================================================================

// Mock useUpdateCheck for Header tests
const mockUpdateCheckReturn = {
  updateAvailable: false,
  currentVersion: "0.27.0",
  latestVersion: "0.28.0",
  installCommand: "pip install -U sandcastle-ai",
  releaseUrl: "https://github.com/gizmax/Sandcastle/releases/tag/v0.28.0",
  changelogUrl: "https://github.com/gizmax/Sandcastle/blob/main/CHANGELOG.md",
  highlights: ["Batch runs", "Schedule monitor"],
  loading: false,
  lastChecked: new Date().toISOString(),
  updateStatus: "idle" as const,
  updateError: null,
  triggerUpdate: vi.fn(),
  triggerRollback: vi.fn(),
};

// We need to reset the module-level _cachedProvider, so mock the advisor call
// Also need to mock sub-components used by Header
vi.mock("@/components/shared/ThemeToggle", () => ({
  ThemeToggle: () => <div data-testid="theme-toggle" />,
}));

vi.mock("@/components/shared/LiveIndicator", () => ({
  LiveIndicator: () => <div data-testid="live-indicator" />,
}));

vi.mock("@/components/layout/NotificationCenter", () => ({
  NotificationCenter: () => <div data-testid="notification-center" />,
}));

vi.mock("@/hooks/useUpdateCheck", () => ({
  useUpdateCheck: () => mockUpdateCheckReturn,
}));

import { Header } from "@/components/layout/Header";

describe("Update Banner in Header", () => {
  const headerProps = {
    onMenuToggle: vi.fn(),
    onOpenPalette: vi.fn(),
    notifications: [] as Array<{ id: string; type: "success" | "error" | "warning" | "info"; message: string; timestamp: Date; read: boolean; link?: string }>,
    onMarkAllRead: vi.fn(),
    onClickNotification: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockReset();
    localStorage.clear();
    // Mock advisor status call that Header makes
    mockApi.get.mockResolvedValue(okResponse({ current_provider: "anthropic" }));
  });

  it("banner shows when update is available", async () => {
    mockUpdateCheckReturn.updateAvailable = true;

    await act(async () => {
      render(<Header {...headerProps} />);
    });

    // Banner should be visible with version info
    expect(screen.getByText(/v0\.28\.0 is available/)).toBeInTheDocument();

    // "Update now" button should be present
    expect(screen.getByText("Update now")).toBeInTheDocument();

    // Highlights should be shown
    expect(screen.getByText(/Batch runs/)).toBeInTheDocument();
  });

  it("dismiss button hides the banner", async () => {
    mockUpdateCheckReturn.updateAvailable = true;

    await act(async () => {
      render(<Header {...headerProps} />);
    });

    // Banner should be visible
    expect(screen.getByText(/v0\.28\.0 is available/)).toBeInTheDocument();

    // Click dismiss
    const dismissBtn = screen.getByLabelText("Dismiss update banner");
    await act(async () => {
      fireEvent.click(dismissBtn);
    });

    // Banner should be gone
    expect(screen.queryByText(/v0\.28\.0 is available/)).not.toBeInTheDocument();

    // localStorage should have the dismissal timestamp
    const stored = localStorage.getItem("sandcastle_update_banner_dismissed");
    expect(stored).toBeTruthy();
    expect(Number(stored)).toBeGreaterThan(0);
  });

  it("'Update now' navigates to settings page", async () => {
    mockUpdateCheckReturn.updateAvailable = true;

    await act(async () => {
      render(<Header {...headerProps} />);
    });

    // Click "Update now"
    const updateBtn = screen.getByText("Update now");
    await act(async () => {
      fireEvent.click(updateBtn);
    });

    // Should navigate to /settings
    expect(mockNavigate).toHaveBeenCalledWith("/settings");
  });
});
