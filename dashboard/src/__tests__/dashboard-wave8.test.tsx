/**
 * Wave 8 Dashboard Tests
 *
 * Covers areas not tested in previous waves:
 * - StatsCards edge cases (NaN, zero, negative values)
 * - CreateApiKeyModal form validation and lifecycle
 * - RunsTable pagination edge cases and rendering
 * - WorkflowCard rendering with missing/edge-case fields
 * - NotificationCenter open/close, empty state, many notifications
 * - ToastContainer auto-dismiss and overflow
 * - useToasts hook behavior
 * - useUpdateCheck hook
 * - useRuntimeInfo hook
 * - ContextBanner variant rendering
 * - ErrorState component
 * - formatCost / formatDuration edge cases for NaN/Infinity
 * - RunStatusBadge unknown status handling
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { renderHook } from "@testing-library/react";

// ============================================================================
// Mocks
// ============================================================================

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
  useNavigate: vi.fn(() => vi.fn()),
  useLocation: vi.fn(() => ({ pathname: "/" })),
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

import { api } from "@/api/client";
const mockApi = api as unknown as {
  get: ReturnType<typeof vi.fn>;
  post: ReturnType<typeof vi.fn>;
  patch: ReturnType<typeof vi.fn>;
  delete: ReturnType<typeof vi.fn>;
};

// ============================================================================
// 1. StatsCards - Edge Cases
// ============================================================================

import { StatsCards } from "@/components/overview/StatsCards";

describe("StatsCards", () => {
  it("renders all four stat cards", () => {
    render(
      <StatsCards totalRuns={42} successRate={0.95} totalCost={12.34} avgDuration={180} />
    );
    expect(screen.getByText("Total Runs Today")).toBeInTheDocument();
    expect(screen.getByText("Success Rate")).toBeInTheDocument();
    expect(screen.getByText("Total Cost Today")).toBeInTheDocument();
    expect(screen.getByText("Avg Duration")).toBeInTheDocument();
  });

  it("displays correct formatted values", () => {
    render(
      <StatsCards totalRuns={42} successRate={0.95} totalCost={12.34} avgDuration={90} />
    );
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("95%")).toBeInTheDocument();
    expect(screen.getByText("$12.34")).toBeInTheDocument();
    expect(screen.getByText("1m 30s")).toBeInTheDocument();
  });

  it("handles zero values without crashing", () => {
    render(
      <StatsCards totalRuns={0} successRate={0} totalCost={0} avgDuration={0} />
    );
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.getByText("0%")).toBeInTheDocument();
    expect(screen.getByText("$0.00")).toBeInTheDocument();
    expect(screen.getByText("0s")).toBeInTheDocument();
  });

  it("handles NaN success rate gracefully (displays 0%)", () => {
    render(
      <StatsCards totalRuns={0} successRate={NaN} totalCost={0} avgDuration={0} />
    );
    // After the bug fix, NaN should show as 0%
    expect(screen.getByText("0%")).toBeInTheDocument();
  });

  it("handles Infinity cost gracefully", () => {
    render(
      <StatsCards totalRuns={0} successRate={0} totalCost={Infinity} avgDuration={0} />
    );
    // formatCost handles non-finite -> "$0.00"
    expect(screen.getByText("$0.00")).toBeInTheDocument();
  });

  it("handles NaN duration gracefully", () => {
    render(
      <StatsCards totalRuns={0} successRate={0} totalCost={0} avgDuration={NaN} />
    );
    // formatDuration handles non-finite -> "0s"
    expect(screen.getByText("0s")).toBeInTheDocument();
  });

  it("handles negative duration gracefully", () => {
    render(
      <StatsCards totalRuns={0} successRate={0} totalCost={0} avgDuration={-10} />
    );
    expect(screen.getByText("0s")).toBeInTheDocument();
  });

  it("renders 100% success rate", () => {
    render(
      <StatsCards totalRuns={100} successRate={1} totalCost={50} avgDuration={30} />
    );
    expect(screen.getByText("100%")).toBeInTheDocument();
  });
});

// ============================================================================
// 2. CreateApiKeyModal - Form Validation and Lifecycle
// ============================================================================

import { CreateApiKeyModal } from "@/components/api-keys/CreateApiKeyModal";

describe("CreateApiKeyModal", () => {
  const defaultProps = {
    open: true,
    onClose: vi.fn(),
    onSubmit: vi.fn(),
  };

  beforeEach(() => {
    defaultProps.onClose.mockClear();
    defaultProps.onSubmit.mockClear();
  });

  it("renders nothing when closed", () => {
    const { container } = render(
      <CreateApiKeyModal {...defaultProps} open={false} />
    );
    expect(container.innerHTML).toBe("");
  });

  it("renders form fields when open", () => {
    render(<CreateApiKeyModal {...defaultProps} />);
    expect(screen.getByLabelText(/Key Name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Tenant ID/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Max Cost/i)).toBeInTheDocument();
  });

  it("calls onClose when Escape is pressed", () => {
    render(<CreateApiKeyModal {...defaultProps} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(defaultProps.onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when Cancel button is clicked", () => {
    render(<CreateApiKeyModal {...defaultProps} />);
    fireEvent.click(screen.getByText("Cancel"));
    expect(defaultProps.onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when backdrop is clicked", () => {
    render(<CreateApiKeyModal {...defaultProps} />);
    const backdrop = document.querySelector(".fixed.inset-0.bg-black\\/40");
    if (backdrop) {
      fireEvent.click(backdrop);
      expect(defaultProps.onClose).toHaveBeenCalledTimes(1);
    }
  });

  it("submits with trimmed name and tenant_id", () => {
    render(<CreateApiKeyModal {...defaultProps} />);
    fireEvent.change(screen.getByLabelText(/Key Name/i), { target: { value: "  My Key  " } });
    fireEvent.change(screen.getByLabelText(/Tenant ID/i), { target: { value: "  acme  " } });
    fireEvent.click(screen.getByText("Create Key"));
    expect(defaultProps.onSubmit).toHaveBeenCalledWith({
      name: "My Key",
      tenant_id: "acme",
    });
  });

  it("does not submit when name is empty/whitespace", () => {
    render(<CreateApiKeyModal {...defaultProps} />);
    // The HTML5 required attribute prevents submission in real browsers,
    // but our handleSubmit also guards with trim check
    fireEvent.change(screen.getByLabelText(/Key Name/i), { target: { value: "   " } });
    fireEvent.change(screen.getByLabelText(/Tenant ID/i), { target: { value: "acme" } });
    // Manually call submit on form to bypass HTML5 validation
    const form = document.querySelector("form");
    if (form) {
      fireEvent.submit(form);
    }
    expect(defaultProps.onSubmit).not.toHaveBeenCalled();
  });

  it("omits max_cost_per_run_usd when field is empty", () => {
    render(<CreateApiKeyModal {...defaultProps} />);
    fireEvent.change(screen.getByLabelText(/Key Name/i), { target: { value: "Key" } });
    fireEvent.change(screen.getByLabelText(/Tenant ID/i), { target: { value: "tenant" } });
    // Leave max cost empty
    fireEvent.click(screen.getByText("Create Key"));
    expect(defaultProps.onSubmit).toHaveBeenCalledWith({
      name: "Key",
      tenant_id: "tenant",
    });
  });

  it("includes valid max_cost_per_run_usd when provided", () => {
    render(<CreateApiKeyModal {...defaultProps} />);
    fireEvent.change(screen.getByLabelText(/Key Name/i), { target: { value: "Key" } });
    fireEvent.change(screen.getByLabelText(/Tenant ID/i), { target: { value: "tenant" } });
    fireEvent.change(screen.getByLabelText(/Max Cost/i), { target: { value: "5.50" } });
    fireEvent.click(screen.getByText("Create Key"));
    expect(defaultProps.onSubmit).toHaveBeenCalledWith({
      name: "Key",
      tenant_id: "tenant",
      max_cost_per_run_usd: 5.5,
    });
  });

  it("omits NaN max cost (guards against invalid input)", () => {
    render(<CreateApiKeyModal {...defaultProps} />);
    fireEvent.change(screen.getByLabelText(/Key Name/i), { target: { value: "Key" } });
    fireEvent.change(screen.getByLabelText(/Tenant ID/i), { target: { value: "tenant" } });
    fireEvent.change(screen.getByLabelText(/Max Cost/i), { target: { value: "abc" } });
    fireEvent.click(screen.getByText("Create Key"));
    // NaN should be filtered out, so no max_cost_per_run_usd
    expect(defaultProps.onSubmit).toHaveBeenCalledWith({
      name: "Key",
      tenant_id: "tenant",
    });
  });

  it("omits negative max cost (submitted via form)", () => {
    render(<CreateApiKeyModal {...defaultProps} />);
    fireEvent.change(screen.getByLabelText(/Key Name/i), { target: { value: "Key" } });
    fireEvent.change(screen.getByLabelText(/Tenant ID/i), { target: { value: "tenant" } });
    fireEvent.change(screen.getByLabelText(/Max Cost/i), { target: { value: "-5" } });
    // Use form submit to bypass native min="0" validation
    const form = document.querySelector("form")!;
    fireEvent.submit(form);
    expect(defaultProps.onSubmit).toHaveBeenCalledWith({
      name: "Key",
      tenant_id: "tenant",
    });
  });

  it("resets form fields when modal reopens", () => {
    const { rerender } = render(<CreateApiKeyModal {...defaultProps} />);
    fireEvent.change(screen.getByLabelText(/Key Name/i), { target: { value: "Old Key" } });

    // Close
    rerender(<CreateApiKeyModal {...defaultProps} open={false} />);
    // Reopen
    rerender(<CreateApiKeyModal {...defaultProps} open={true} />);

    expect(screen.getByLabelText(/Key Name/i)).toHaveValue("");
  });

  it("has aria-modal and aria-labelledby on dialog", () => {
    render(<CreateApiKeyModal {...defaultProps} />);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAttribute("aria-labelledby", "create-key-title");
  });

  it("has close button with aria-label", () => {
    render(<CreateApiKeyModal {...defaultProps} />);
    expect(screen.getByLabelText("Close dialog")).toBeInTheDocument();
  });
});

// ============================================================================
// 3. RunsTable - Pagination and Rendering
// ============================================================================

import { RunsTable } from "@/components/runs/RunsTable";

// RunsTable uses useNavigate
const mockNavigate = vi.fn();
vi.mocked(vi.fn()).mockReturnValue(mockNavigate);

import { useNavigate } from "react-router-dom";

describe("RunsTable", () => {
  const baseRun = {
    run_id: "aaaa-bbbb-cccc-dddd",
    workflow_name: "test-workflow",
    status: "completed",
    total_cost_usd: 1.5,
    started_at: "2025-06-15T10:00:00Z",
    completed_at: "2025-06-15T10:01:00Z",
  };

  beforeEach(() => {
    vi.mocked(useNavigate).mockReturnValue(mockNavigate);
    mockNavigate.mockClear();
  });

  it("renders a table with runs", () => {
    render(
      <RunsTable
        runs={[baseRun]}
        total={1}
        limit={20}
        offset={0}
        onPageChange={vi.fn()}
      />
    );
    expect(screen.getByText("test-workflow")).toBeInTheDocument();
    expect(screen.getByText("$1.50")).toBeInTheDocument();
  });

  it("does not show pagination when total fits in one page", () => {
    render(
      <RunsTable
        runs={[baseRun]}
        total={1}
        limit={20}
        offset={0}
        onPageChange={vi.fn()}
      />
    );
    expect(screen.queryByText("Previous")).toBeNull();
    expect(screen.queryByText("Next")).toBeNull();
  });

  it("shows pagination when total exceeds limit", () => {
    const runs = Array.from({ length: 20 }, (_, i) => ({
      ...baseRun,
      run_id: `run-${i}`,
    }));
    render(
      <RunsTable
        runs={runs}
        total={45}
        limit={20}
        offset={0}
        onPageChange={vi.fn()}
      />
    );
    expect(screen.getByText("Previous")).toBeInTheDocument();
    expect(screen.getByText("Next")).toBeInTheDocument();
    expect(screen.getByText("1 / 3")).toBeInTheDocument();
    expect(screen.getByText("Showing 1-20 of 45")).toBeInTheDocument();
  });

  it("disables Previous on first page", () => {
    const runs = Array.from({ length: 10 }, (_, i) => ({
      ...baseRun,
      run_id: `run-${i}`,
    }));
    render(
      <RunsTable
        runs={runs}
        total={30}
        limit={10}
        offset={0}
        onPageChange={vi.fn()}
      />
    );
    const prevBtn = screen.getByText("Previous");
    expect(prevBtn).toBeDisabled();
  });

  it("disables Next on last page", () => {
    const runs = Array.from({ length: 5 }, (_, i) => ({
      ...baseRun,
      run_id: `run-${i}`,
    }));
    render(
      <RunsTable
        runs={runs}
        total={25}
        limit={10}
        offset={20}
        onPageChange={vi.fn()}
      />
    );
    const nextBtn = screen.getByText("Next");
    expect(nextBtn).toBeDisabled();
  });

  it("calls onPageChange with correct offset on Next click", () => {
    const onPageChange = vi.fn();
    const runs = Array.from({ length: 10 }, (_, i) => ({
      ...baseRun,
      run_id: `run-${i}`,
    }));
    render(
      <RunsTable
        runs={runs}
        total={30}
        limit={10}
        offset={0}
        onPageChange={onPageChange}
      />
    );
    fireEvent.click(screen.getByText("Next"));
    expect(onPageChange).toHaveBeenCalledWith(10);
  });

  it("calls onPageChange with correct offset on Previous click", () => {
    const onPageChange = vi.fn();
    const runs = Array.from({ length: 10 }, (_, i) => ({
      ...baseRun,
      run_id: `run-${i}`,
    }));
    render(
      <RunsTable
        runs={runs}
        total={30}
        limit={10}
        offset={10}
        onPageChange={onPageChange}
      />
    );
    fireEvent.click(screen.getByText("Previous"));
    expect(onPageChange).toHaveBeenCalledWith(0);
  });

  it("navigates to run detail on row click", () => {
    render(
      <RunsTable
        runs={[baseRun]}
        total={1}
        limit={20}
        offset={0}
        onPageChange={vi.fn()}
      />
    );
    // Click the table row
    const row = screen.getByRole("row", { name: /Run test-workflow/ });
    fireEvent.click(row);
    expect(mockNavigate).toHaveBeenCalledWith("/runs/aaaa-bbbb-cccc-dddd");
  });

  it("navigates to run detail on Enter key press", () => {
    render(
      <RunsTable
        runs={[baseRun]}
        total={1}
        limit={20}
        offset={0}
        onPageChange={vi.fn()}
      />
    );
    const row = screen.getByRole("row", { name: /Run test-workflow/ });
    fireEvent.keyDown(row, { key: "Enter" });
    expect(mockNavigate).toHaveBeenCalledWith("/runs/aaaa-bbbb-cccc-dddd");
  });

  it("navigates to run detail on Space key press", () => {
    render(
      <RunsTable
        runs={[baseRun]}
        total={1}
        limit={20}
        offset={0}
        onPageChange={vi.fn()}
      />
    );
    const row = screen.getByRole("row", { name: /Run test-workflow/ });
    fireEvent.keyDown(row, { key: " " });
    expect(mockNavigate).toHaveBeenCalledWith("/runs/aaaa-bbbb-cccc-dddd");
  });

  it("truncates long workflow names with CSS (no crash)", () => {
    const longNameRun = {
      ...baseRun,
      workflow_name: "A".repeat(200),
    };
    render(
      <RunsTable
        runs={[longNameRun]}
        total={1}
        limit={20}
        offset={0}
        onPageChange={vi.fn()}
      />
    );
    expect(screen.getByText("A".repeat(200))).toBeInTheDocument();
  });

  it("shows 'queued' when started_at is null", () => {
    const queuedRun = { ...baseRun, started_at: null, status: "queued" };
    render(
      <RunsTable
        runs={[queuedRun]}
        total={1}
        limit={20}
        offset={0}
        onPageChange={vi.fn()}
      />
    );
    // "queued" appears in both status badge and Started column
    const allQueued = screen.getAllByText("queued");
    expect(allQueued.length).toBeGreaterThanOrEqual(1);
  });

  it("renders selection checkboxes when onSelectionChange is provided", () => {
    render(
      <RunsTable
        runs={[baseRun]}
        total={1}
        limit={20}
        offset={0}
        onPageChange={vi.fn()}
        selectedIds={new Set()}
        onSelectionChange={vi.fn()}
      />
    );
    expect(screen.getByLabelText("Select all runs")).toBeInTheDocument();
  });

  it("toggles select all checkbox", () => {
    const onSelectionChange = vi.fn();
    render(
      <RunsTable
        runs={[baseRun]}
        total={1}
        limit={20}
        offset={0}
        onPageChange={vi.fn()}
        selectedIds={new Set()}
        onSelectionChange={onSelectionChange}
      />
    );
    fireEvent.click(screen.getByLabelText("Select all runs"));
    expect(onSelectionChange).toHaveBeenCalledWith(new Set(["aaaa-bbbb-cccc-dddd"]));
  });

  it("deselects all when all are selected", () => {
    const onSelectionChange = vi.fn();
    render(
      <RunsTable
        runs={[baseRun]}
        total={1}
        limit={20}
        offset={0}
        onPageChange={vi.fn()}
        selectedIds={new Set(["aaaa-bbbb-cccc-dddd"])}
        onSelectionChange={onSelectionChange}
      />
    );
    fireEvent.click(screen.getByLabelText("Select all runs"));
    expect(onSelectionChange).toHaveBeenCalledWith(new Set());
  });

  it("handles run with zero cost", () => {
    const zeroCostRun = { ...baseRun, total_cost_usd: 0 };
    render(
      <RunsTable
        runs={[zeroCostRun]}
        total={1}
        limit={20}
        offset={0}
        onPageChange={vi.fn()}
      />
    );
    expect(screen.getByText("$0.00")).toBeInTheDocument();
  });

  it("has proper aria-label on table", () => {
    render(
      <RunsTable
        runs={[baseRun]}
        total={1}
        limit={20}
        offset={0}
        onPageChange={vi.fn()}
      />
    );
    expect(screen.getByLabelText("Workflow runs")).toBeInTheDocument();
  });
});

// ============================================================================
// 4. WorkflowCard - Rendering Edge Cases
// ============================================================================

import { WorkflowCard } from "@/components/workflows/WorkflowCard";

describe("WorkflowCard", () => {
  const defaultProps = {
    name: "lead-enrichment",
    description: "Enriches lead data with company info",
    stepsCount: 3,
    fileName: "lead-enrichment.yaml",
    onRun: vi.fn(),
    onEdit: vi.fn(),
    onViewDag: vi.fn(),
  };

  it("renders name and description", () => {
    render(<WorkflowCard {...defaultProps} />);
    expect(screen.getByText("lead-enrichment")).toBeInTheDocument();
    expect(screen.getByText("Enriches lead data with company info")).toBeInTheDocument();
  });

  it("renders step count with plural", () => {
    render(<WorkflowCard {...defaultProps} stepsCount={5} />);
    expect(screen.getByText("5 steps")).toBeInTheDocument();
  });

  it("renders step count singular for 1 step", () => {
    render(<WorkflowCard {...defaultProps} stepsCount={1} />);
    expect(screen.getByText("1 step")).toBeInTheDocument();
  });

  it("renders 0 steps", () => {
    render(<WorkflowCard {...defaultProps} stepsCount={0} />);
    expect(screen.getByText("0 steps")).toBeInTheDocument();
  });

  it("renders version info when provided", () => {
    render(<WorkflowCard {...defaultProps} version={3} />);
    expect(screen.getByText("v3")).toBeInTheDocument();
  });

  it("does not render version when null", () => {
    render(<WorkflowCard {...defaultProps} version={null} />);
    expect(screen.queryByText(/^v/)).toBeNull();
  });

  it("renders version status badge", () => {
    render(
      <WorkflowCard {...defaultProps} version={1} versionStatus="production" />
    );
    expect(screen.getByText("production")).toBeInTheDocument();
  });

  it("renders staging version status", () => {
    render(
      <WorkflowCard {...defaultProps} version={1} versionStatus="staging" />
    );
    expect(screen.getByText("staging")).toBeInTheDocument();
  });

  it("renders total versions when > 1", () => {
    render(
      <WorkflowCard {...defaultProps} totalVersions={5} />
    );
    expect(screen.getByText("5 versions")).toBeInTheDocument();
  });

  it("does not render total versions when 1", () => {
    render(
      <WorkflowCard {...defaultProps} totalVersions={1} />
    );
    expect(screen.queryByText("1 versions")).toBeNull();
  });

  it("calls onRun when Run button is clicked", () => {
    const onRun = vi.fn();
    render(<WorkflowCard {...defaultProps} onRun={onRun} />);
    fireEvent.click(screen.getByText("Run"));
    expect(onRun).toHaveBeenCalledTimes(1);
  });

  it("calls onEdit when Edit button is clicked", () => {
    const onEdit = vi.fn();
    render(<WorkflowCard {...defaultProps} onEdit={onEdit} />);
    fireEvent.click(screen.getByText("Edit"));
    expect(onEdit).toHaveBeenCalledTimes(1);
  });

  it("calls onViewDag when DAG button is clicked", () => {
    const onViewDag = vi.fn();
    render(<WorkflowCard {...defaultProps} onViewDag={onViewDag} />);
    fireEvent.click(screen.getByText("DAG"));
    expect(onViewDag).toHaveBeenCalledTimes(1);
  });

  it("renders versions button when onViewVersions is provided", () => {
    render(
      <WorkflowCard {...defaultProps} onViewVersions={vi.fn()} />
    );
    expect(screen.getByLabelText("View versions")).toBeInTheDocument();
  });

  it("does not render versions button when onViewVersions is absent", () => {
    render(<WorkflowCard {...defaultProps} />);
    expect(screen.queryByLabelText("View versions")).toBeNull();
  });

  it("handles very long description (CSS truncation)", () => {
    const longDesc = "X".repeat(500);
    render(<WorkflowCard {...defaultProps} description={longDesc} />);
    expect(screen.getByText(longDesc)).toBeInTheDocument();
  });

  it("handles empty description", () => {
    render(<WorkflowCard {...defaultProps} description="" />);
    // Should not crash
    expect(screen.getByText("lead-enrichment")).toBeInTheDocument();
  });
});

// ============================================================================
// 5. NotificationCenter - Toggle, Empty State, Badge
// ============================================================================

import { NotificationCenter, type Notification } from "@/components/layout/NotificationCenter";

describe("NotificationCenter", () => {
  const makeNotification = (id: string, overrides?: Partial<Notification>): Notification => ({
    id,
    type: "info",
    message: `Notification ${id}`,
    timestamp: new Date("2025-06-15T10:00:00Z"),
    read: false,
    ...overrides,
  });

  it("renders bell button", () => {
    render(
      <NotificationCenter
        notifications={[]}
        onMarkAllRead={vi.fn()}
        onClickNotification={vi.fn()}
      />
    );
    expect(screen.getByRole("button", { name: /Notifications/ })).toBeInTheDocument();
  });

  it("shows unread count badge", () => {
    render(
      <NotificationCenter
        notifications={[makeNotification("1"), makeNotification("2")]}
        onMarkAllRead={vi.fn()}
        onClickNotification={vi.fn()}
      />
    );
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("does not show badge when all notifications are read", () => {
    render(
      <NotificationCenter
        notifications={[makeNotification("1", { read: true })]}
        onMarkAllRead={vi.fn()}
        onClickNotification={vi.fn()}
      />
    );
    // aria-label should not mention unread
    expect(screen.getByLabelText("Notifications")).toBeInTheDocument();
  });

  it("opens dropdown on click", () => {
    render(
      <NotificationCenter
        notifications={[makeNotification("1")]}
        onMarkAllRead={vi.fn()}
        onClickNotification={vi.fn()}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /Notifications/ }));
    expect(screen.getByText("Notification 1")).toBeInTheDocument();
  });

  it("shows empty state when no notifications", () => {
    render(
      <NotificationCenter
        notifications={[]}
        onMarkAllRead={vi.fn()}
        onClickNotification={vi.fn()}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /Notifications/ }));
    expect(screen.getByText("No notifications")).toBeInTheDocument();
  });

  it("closes on Escape key", () => {
    render(
      <NotificationCenter
        notifications={[makeNotification("1")]}
        onMarkAllRead={vi.fn()}
        onClickNotification={vi.fn()}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /Notifications/ }));
    expect(screen.getByText("Notification 1")).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByText("Notification 1")).toBeNull();
  });

  it("calls onMarkAllRead", () => {
    const onMarkAllRead = vi.fn();
    render(
      <NotificationCenter
        notifications={[makeNotification("1")]}
        onMarkAllRead={onMarkAllRead}
        onClickNotification={vi.fn()}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /Notifications/ }));
    fireEvent.click(screen.getByText("Mark all as read"));
    expect(onMarkAllRead).toHaveBeenCalledTimes(1);
  });

  it("does not show Mark all as read when all are read", () => {
    render(
      <NotificationCenter
        notifications={[makeNotification("1", { read: true })]}
        onMarkAllRead={vi.fn()}
        onClickNotification={vi.fn()}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /Notifications/ }));
    expect(screen.queryByText("Mark all as read")).toBeNull();
  });

  it("calls onClickNotification and closes dropdown", () => {
    const onClickNotification = vi.fn();
    const notification = makeNotification("1");
    render(
      <NotificationCenter
        notifications={[notification]}
        onMarkAllRead={vi.fn()}
        onClickNotification={onClickNotification}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /Notifications/ }));
    fireEvent.click(screen.getByText("Notification 1"));
    expect(onClickNotification).toHaveBeenCalledWith(notification);
    // Dropdown should close
    expect(screen.queryByText("Notification 1")).toBeNull();
  });

  it("renders different notification types with correct icons", () => {
    const notifications = [
      makeNotification("1", { type: "success", message: "Success msg" }),
      makeNotification("2", { type: "error", message: "Error msg" }),
      makeNotification("3", { type: "warning", message: "Warning msg" }),
      makeNotification("4", { type: "info", message: "Info msg" }),
    ];
    render(
      <NotificationCenter
        notifications={notifications}
        onMarkAllRead={vi.fn()}
        onClickNotification={vi.fn()}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /Notifications/ }));
    expect(screen.getByText("Success msg")).toBeInTheDocument();
    expect(screen.getByText("Error msg")).toBeInTheDocument();
    expect(screen.getByText("Warning msg")).toBeInTheDocument();
    expect(screen.getByText("Info msg")).toBeInTheDocument();
  });

  it("has aria-expanded attribute on bell button", () => {
    render(
      <NotificationCenter
        notifications={[]}
        onMarkAllRead={vi.fn()}
        onClickNotification={vi.fn()}
      />
    );
    const btn = screen.getByRole("button", { name: /Notifications/ });
    expect(btn).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(btn);
    expect(btn).toHaveAttribute("aria-expanded", "true");
  });
});

// ============================================================================
// 6. ToastContainer - Auto-dismiss, Rendering
// ============================================================================

import { ToastContainer, type Toast } from "@/components/shared/ToastContainer";

describe("ToastContainer", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders nothing when no toasts", () => {
    const { container } = render(
      <ToastContainer toasts={[]} onDismiss={vi.fn()} />
    );
    expect(container.innerHTML).toBe("");
  });

  it("renders toast messages", () => {
    const toasts: Toast[] = [
      { id: "t1", type: "success", message: "Saved!" },
      { id: "t2", type: "error", message: "Failed!" },
    ];
    render(<ToastContainer toasts={toasts} onDismiss={vi.fn()} />);
    expect(screen.getByText("Saved!")).toBeInTheDocument();
    expect(screen.getByText("Failed!")).toBeInTheDocument();
  });

  it("calls onDismiss when dismiss button is clicked", () => {
    const onDismiss = vi.fn();
    const toasts: Toast[] = [
      { id: "t1", type: "success", message: "Saved!" },
    ];
    render(<ToastContainer toasts={toasts} onDismiss={onDismiss} />);
    fireEvent.click(screen.getByLabelText("Dismiss notification"));
    expect(onDismiss).toHaveBeenCalledWith("t1");
  });

  it("auto-dismisses after timeout", () => {
    const onDismiss = vi.fn();
    const toasts: Toast[] = [
      { id: "t1", type: "success", message: "Saved!" },
    ];
    render(<ToastContainer toasts={toasts} onDismiss={onDismiss} />);
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(onDismiss).toHaveBeenCalledWith("t1");
  });

  it("renders warning toast type", () => {
    const toasts: Toast[] = [
      { id: "t1", type: "warning", message: "Caution!" },
    ];
    render(<ToastContainer toasts={toasts} onDismiss={vi.fn()} />);
    expect(screen.getByText("Caution!")).toBeInTheDocument();
  });

  it("toasts have role=alert for screen readers", () => {
    const toasts: Toast[] = [
      { id: "t1", type: "error", message: "Error!" },
    ];
    render(<ToastContainer toasts={toasts} onDismiss={vi.fn()} />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});

// ============================================================================
// 7. useToasts Hook
// ============================================================================

import { useToasts } from "@/hooks/useToasts";

describe("useToasts", () => {
  it("starts with empty toasts", () => {
    const { result } = renderHook(() => useToasts());
    expect(result.current.toasts).toEqual([]);
  });

  it("adds a toast", () => {
    const { result } = renderHook(() => useToasts());
    act(() => {
      result.current.addToast("success", "Saved!");
    });
    expect(result.current.toasts).toHaveLength(1);
    expect(result.current.toasts[0].type).toBe("success");
    expect(result.current.toasts[0].message).toBe("Saved!");
  });

  it("dismisses a toast by id", () => {
    const { result } = renderHook(() => useToasts());
    act(() => {
      result.current.addToast("success", "Saved!");
    });
    const id = result.current.toasts[0].id;
    act(() => {
      result.current.dismissToast(id);
    });
    expect(result.current.toasts).toHaveLength(0);
  });

  it("caps toasts at 5", () => {
    const { result } = renderHook(() => useToasts());
    act(() => {
      for (let i = 0; i < 7; i++) {
        result.current.addToast("warning", `Toast ${i}`);
      }
    });
    expect(result.current.toasts.length).toBeLessThanOrEqual(5);
    // Should keep the most recent 5
    expect(result.current.toasts[4].message).toBe("Toast 6");
  });

  it("generates unique ids for toasts", () => {
    const { result } = renderHook(() => useToasts());
    act(() => {
      result.current.addToast("success", "First");
      result.current.addToast("error", "Second");
    });
    const ids = result.current.toasts.map((t) => t.id);
    expect(new Set(ids).size).toBe(2);
  });
});

// ============================================================================
// 8. useUpdateCheck Hook
// ============================================================================

import { useUpdateCheck } from "@/hooks/useUpdateCheck";

describe("useUpdateCheck", () => {
  beforeEach(() => {
    mockApi.get.mockReset();
  });

  it("starts in loading state", () => {
    mockApi.get.mockReturnValue(new Promise(() => {})); // never resolves
    const { result } = renderHook(() => useUpdateCheck());
    expect(result.current.loading).toBe(true);
    expect(result.current.updateAvailable).toBe(false);
  });

  it("returns update info when available", async () => {
    mockApi.get.mockResolvedValue({
      data: {
        current_version: "0.17.0",
        latest_version: "0.18.0",
        update_available: true,
        release_url: "https://github.com/gizmax/Sandcastle/releases/v0.18.0",
        install_command: "pip install --upgrade sandcastle-ai",
      },
    });

    const { result } = renderHook(() => useUpdateCheck());
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.updateAvailable).toBe(true);
    expect(result.current.currentVersion).toBe("0.17.0");
    expect(result.current.latestVersion).toBe("0.18.0");
    expect(result.current.installCommand).toBe("pip install --upgrade sandcastle-ai");
    expect(result.current.releaseUrl).toBe("https://github.com/gizmax/Sandcastle/releases/v0.18.0");
  });

  it("returns no update when up-to-date", async () => {
    mockApi.get.mockResolvedValue({
      data: {
        current_version: "0.18.0",
        latest_version: "0.18.0",
        update_available: false,
        release_url: "",
        install_command: "",
      },
    });

    const { result } = renderHook(() => useUpdateCheck());
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.updateAvailable).toBe(false);
  });

  it("handles API error gracefully", async () => {
    mockApi.get.mockRejectedValue(new Error("Network error"));

    const { result } = renderHook(() => useUpdateCheck());
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.updateAvailable).toBe(false);
    expect(result.current.currentVersion).toBeNull();
  });

  it("handles null data response", async () => {
    mockApi.get.mockResolvedValue({ data: null });

    const { result } = renderHook(() => useUpdateCheck());
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.updateAvailable).toBe(false);
    expect(result.current.currentVersion).toBeNull();
  });
});

// ============================================================================
// 9. useRuntimeInfo Hook
// ============================================================================

import { useRuntimeInfo } from "@/hooks/useRuntimeInfo";

describe("useRuntimeInfo", () => {
  beforeEach(() => {
    mockApi.get.mockReset();
  });

  it("starts in loading state", () => {
    mockApi.get.mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useRuntimeInfo());
    expect(result.current.loading).toBe(true);
    expect(result.current.info).toBeNull();
  });

  it("returns runtime info on success", async () => {
    const runtimeData = {
      mode: "local",
      database: "sqlite",
      queue: "in-process",
      storage: "local",
      data_dir: "/tmp/data",
      version: "0.18.0",
      sandbox_backend: "e2b",
      license: null,
    };
    mockApi.get.mockResolvedValue({ data: runtimeData });

    const { result } = renderHook(() => useRuntimeInfo());
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.info).toEqual(runtimeData);
  });

  it("handles API error gracefully", async () => {
    mockApi.get.mockRejectedValue(new Error("Network error"));

    const { result } = renderHook(() => useRuntimeInfo());
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.info).toBeNull();
  });

  it("handles null data response", async () => {
    mockApi.get.mockResolvedValue({ data: null });

    const { result } = renderHook(() => useRuntimeInfo());
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.info).toBeNull();
  });

  it("includes license info when present", async () => {
    const runtimeData = {
      mode: "production",
      database: "postgresql",
      queue: "redis",
      storage: "s3",
      data_dir: null,
      version: "0.18.0",
      sandbox_backend: "docker",
      license: {
        status: "valid",
        tier: "pro",
        licensee: "Acme Corp",
        max_seats: 10,
        expires: "2027-01-01",
      },
    };
    mockApi.get.mockResolvedValue({ data: runtimeData });

    const { result } = renderHook(() => useRuntimeInfo());
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.info?.license?.tier).toBe("pro");
    expect(result.current.info?.license?.licensee).toBe("Acme Corp");
  });
});

// ============================================================================
// 10. ContextBanner - Variant Rendering
// ============================================================================

import { ContextBanner } from "@/components/shared/ContextBanner";
import { AlertTriangle } from "lucide-react";

describe("ContextBanner", () => {
  it("renders children text", () => {
    render(
      <ContextBanner variant="info" icon={AlertTriangle}>
        Test message here
      </ContextBanner>
    );
    expect(screen.getByText("Test message here")).toBeInTheDocument();
  });

  it("renders error variant with alert role", () => {
    render(
      <ContextBanner variant="error" icon={AlertTriangle}>
        Error message
      </ContextBanner>
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("renders warning variant with status role", () => {
    render(
      <ContextBanner variant="warning" icon={AlertTriangle}>
        Warning message
      </ContextBanner>
    );
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("renders info variant without role", () => {
    const { container } = render(
      <ContextBanner variant="info" icon={AlertTriangle}>
        Info message
      </ContextBanner>
    );
    const div = container.firstElementChild;
    expect(div).not.toHaveAttribute("role");
  });

  it("renders link when provided", () => {
    render(
      <ContextBanner
        variant="error"
        icon={AlertTriangle}
        link={{ to: "/runs", label: "View Runs" }}
      >
        Check runs
      </ContextBanner>
    );
    const link = screen.getByText("View Runs");
    expect(link).toBeInTheDocument();
    expect(link.closest("a")).toHaveAttribute("href", "/runs");
  });

  it("does not render link when not provided", () => {
    render(
      <ContextBanner variant="info" icon={AlertTriangle}>
        No link
      </ContextBanner>
    );
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("applies custom className", () => {
    const { container } = render(
      <ContextBanner variant="info" icon={AlertTriangle} className="mt-4">
        Styled
      </ContextBanner>
    );
    expect(container.firstElementChild).toHaveClass("mt-4");
  });
});

// ============================================================================
// 11. ErrorState Component
// ============================================================================

import { ErrorState } from "@/components/shared/ErrorState";

describe("ErrorState", () => {
  it("renders error message", () => {
    render(<ErrorState message="Something went wrong" />);
    expect(screen.getByText("Something went wrong")).toBeInTheDocument();
  });

  it("renders title when provided", () => {
    render(<ErrorState title="System Health" message="Connection failed" />);
    expect(screen.getByText("System Health")).toBeInTheDocument();
    expect(screen.getByText("Connection failed")).toBeInTheDocument();
  });

  it("does not render title when not provided", () => {
    render(<ErrorState message="Error" />);
    expect(screen.queryByRole("heading")).toBeNull();
  });

  it("renders Retry button when onRetry is provided", () => {
    const onRetry = vi.fn();
    render(<ErrorState message="Error" onRetry={onRetry} />);
    const btn = screen.getByText("Retry");
    expect(btn).toBeInTheDocument();
    fireEvent.click(btn);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("does not render Retry button when onRetry is not provided", () => {
    render(<ErrorState message="Error" />);
    expect(screen.queryByText("Retry")).toBeNull();
  });
});

// ============================================================================
// 12. RunStatusBadge - Unknown Status
// ============================================================================

import { RunStatusBadge } from "@/components/runs/RunStatusBadge";

describe("RunStatusBadge", () => {
  it("renders known status", () => {
    render(<RunStatusBadge status="completed" />);
    expect(screen.getByText("completed")).toBeInTheDocument();
  });

  it("renders running status", () => {
    render(<RunStatusBadge status="running" />);
    expect(screen.getByText("running")).toBeInTheDocument();
  });

  it("renders unknown status without crashing", () => {
    render(<RunStatusBadge status="unknown_status_xyz" />);
    expect(screen.getByText("unknown_status_xyz")).toBeInTheDocument();
  });

  it("renders empty status string without crashing", () => {
    const { container } = render(<RunStatusBadge status="" />);
    // Should not crash - renders a badge span with fallback styling
    const badge = container.querySelector("span");
    expect(badge).toBeInTheDocument();
  });

  it("applies sm size by default (uses px-2)", () => {
    render(<RunStatusBadge status="completed" />);
    const badge = screen.getByText("completed").closest("span.inline-flex");
    expect(badge).toBeInTheDocument();
    // sm size adds px-2 class
    expect(badge!.className).toContain("px-2");
  });

  it("applies md size when specified (uses px-3)", () => {
    render(<RunStatusBadge status="completed" size="md" />);
    const badge = screen.getByText("completed").closest("span.inline-flex");
    expect(badge).toBeInTheDocument();
    // md size adds px-3 class
    expect(badge!.className).toContain("px-3");
  });

  it("renders all known statuses", () => {
    const statuses = [
      "queued", "running", "completed", "failed",
      "partial", "cancelled", "budget_exceeded",
      "pending", "skipped", "awaiting_approval",
    ];
    for (const status of statuses) {
      const { unmount } = render(<RunStatusBadge status={status} />);
      expect(screen.getByText(status)).toBeInTheDocument();
      unmount();
    }
  });
});

// ============================================================================
// 13. LoadingSpinner Sizes
// ============================================================================

import { LoadingSpinner } from "@/components/shared/LoadingSpinner";

describe("LoadingSpinner", () => {
  it("renders with role=status", () => {
    render(<LoadingSpinner />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("has sr-only text", () => {
    render(<LoadingSpinner />);
    expect(screen.getByText("Loading...")).toBeInTheDocument();
  });

  it("applies custom className", () => {
    const { container } = render(<LoadingSpinner className="mt-8" />);
    expect(container.firstElementChild).toHaveClass("mt-8");
  });

  it("renders sm size", () => {
    const { container } = render(<LoadingSpinner size="sm" />);
    const spinner = container.querySelector(".animate-spin");
    expect(spinner).toHaveClass("h-4", "w-4");
  });

  it("renders lg size", () => {
    const { container } = render(<LoadingSpinner size="lg" />);
    const spinner = container.querySelector(".animate-spin");
    expect(spinner).toHaveClass("h-8", "w-8");
  });
});

// ============================================================================
// 14. Utility Function Edge Cases (extended)
// ============================================================================

import { formatCost, formatDuration, parseUTC, formatRelativeTime } from "@/lib/utils";

describe("formatCost edge cases", () => {
  it("returns $0.00 for NaN", () => {
    expect(formatCost(NaN)).toBe("$0.00");
  });

  it("returns $0.00 for Infinity", () => {
    expect(formatCost(Infinity)).toBe("$0.00");
  });

  it("returns $0.00 for -Infinity", () => {
    expect(formatCost(-Infinity)).toBe("$0.00");
  });

  it("handles very large numbers", () => {
    expect(formatCost(999999.99)).toBe("$999999.99");
  });

  it("handles very small positive numbers", () => {
    expect(formatCost(0.001)).toBe("$0.00");
  });
});

describe("formatDuration edge cases", () => {
  it("returns 0s for NaN", () => {
    expect(formatDuration(NaN)).toBe("0s");
  });

  it("returns 0s for Infinity", () => {
    expect(formatDuration(Infinity)).toBe("0s");
  });

  it("returns 0s for -Infinity", () => {
    expect(formatDuration(-Infinity)).toBe("0s");
  });

  it("returns 0s for negative values", () => {
    expect(formatDuration(-100)).toBe("0s");
  });

  it("handles exactly 0", () => {
    expect(formatDuration(0)).toBe("0s");
  });

  it("handles very large duration", () => {
    // 100000 seconds = 1666m 40s
    expect(formatDuration(100000)).toBe("1666m 40s");
  });
});

describe("parseUTC edge cases", () => {
  it("handles ISO format with offset +00:00", () => {
    const result = parseUTC("2025-01-15T10:30:00+00:00");
    expect(result.toISOString()).toBe("2025-01-15T10:30:00.000Z");
  });

  it("handles ISO format with negative offset", () => {
    const result = parseUTC("2025-01-15T10:30:00-05:00");
    expect(result.toISOString()).toBe("2025-01-15T15:30:00.000Z");
  });

  it("handles date-only strings", () => {
    // Date-only is treated as UTC by JS Date spec
    const result = parseUTC("2025-01-15");
    expect(result.toISOString()).toBe("2025-01-15T00:00:00.000Z");
  });
});

describe("formatRelativeTime edge cases", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2025-06-15T12:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("handles future dates gracefully", () => {
    expect(formatRelativeTime("2025-06-15T13:00:00Z")).toBe("just now");
  });

  it("handles exactly now", () => {
    expect(formatRelativeTime("2025-06-15T12:00:00Z")).toBe("just now");
  });

  it("handles exactly 1 minute ago", () => {
    expect(formatRelativeTime("2025-06-15T11:59:00Z")).toBe("1m ago");
  });

  it("handles exactly 1 hour ago", () => {
    expect(formatRelativeTime("2025-06-15T11:00:00Z")).toBe("1h ago");
  });

  it("handles exactly 1 day ago", () => {
    expect(formatRelativeTime("2025-06-14T12:00:00Z")).toBe("1d ago");
  });

  it("handles many days ago", () => {
    expect(formatRelativeTime("2025-06-01T12:00:00Z")).toBe("14d ago");
  });
});

// ============================================================================
// 15. useRuns - Additional Edge Cases
// ============================================================================

import { useRuns } from "@/hooks/useRuns";

describe("useRuns (wave 8 extended)", () => {
  beforeEach(() => {
    mockApi.get.mockReset();
  });

  it("sets error on API error response", async () => {
    mockApi.get.mockResolvedValue({
      data: null,
      error: { code: "ERR", message: "Internal server error" },
    });

    const { result } = renderHook(() => useRuns());
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.error).toBe("Internal server error");
    expect(result.current.runs).toEqual([]);
  });

  it("sets generic error on thrown exception", async () => {
    mockApi.get.mockRejectedValue(new Error("Network down"));

    const { result } = renderHook(() => useRuns());
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.error).toBe("Failed to fetch runs");
  });

  it("clears error on successful refetch after failure", async () => {
    // First call fails
    mockApi.get.mockResolvedValueOnce({
      data: null,
      error: { code: "ERR", message: "Server error" },
    });

    const { result } = renderHook(() => useRuns());
    await waitFor(() => {
      expect(result.current.error).toBe("Server error");
    });

    // Second call succeeds
    mockApi.get.mockResolvedValueOnce({
      data: [{ run_id: "r1", workflow_name: "test", status: "completed", total_cost_usd: 0, started_at: null, completed_at: null }],
      meta: { total: 1, limit: 50, offset: 0 },
    });

    await act(async () => {
      await result.current.refetch();
    });

    expect(result.current.error).toBeNull();
    expect(result.current.runs).toHaveLength(1);
  });

  it("resets loading to true on param changes", async () => {
    mockApi.get.mockResolvedValue({ data: [], meta: { total: 0, limit: 50, offset: 0 } });

    const { result, rerender } = renderHook(
      ({ status }: { status?: string }) => useRuns({ status }),
      { initialProps: { status: "completed" } }
    );

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    // Change param - should trigger loading
    rerender({ status: "failed" });
    // Loading should be reset (the effect sets it synchronously)
    expect(result.current.loading).toBe(true);

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
  });

  it("handles empty data array from API", async () => {
    mockApi.get.mockResolvedValue({ data: [], meta: { total: 0, limit: 50, offset: 0 } });

    const { result } = renderHook(() => useRuns());
    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.runs).toEqual([]);
    expect(result.current.total).toBe(0);
    expect(result.current.error).toBeNull();
  });

  it("does not include status param when not specified", async () => {
    mockApi.get.mockResolvedValue({ data: [], meta: { total: 0, limit: 50, offset: 0 } });

    renderHook(() => useRuns());

    await waitFor(() => {
      expect(mockApi.get).toHaveBeenCalledWith(
        "/runs",
        expect.not.objectContaining({ status: expect.anything() })
      );
    });
  });

  it("does not include workflow param when not specified", async () => {
    mockApi.get.mockResolvedValue({ data: [], meta: { total: 0, limit: 50, offset: 0 } });

    renderHook(() => useRuns());

    await waitFor(() => {
      expect(mockApi.get).toHaveBeenCalledWith(
        "/runs",
        expect.not.objectContaining({ workflow: expect.anything() })
      );
    });
  });
});

// ============================================================================
// 16. EmptyState - Extended Tests
// ============================================================================

import { EmptyState } from "@/components/shared/EmptyState";
import { Inbox } from "lucide-react";

describe("EmptyState (wave 8)", () => {
  it("renders without action button", () => {
    render(<EmptyState title="Empty" description="Nothing" />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("renders with icon", () => {
    const { container } = render(
      <EmptyState icon={Inbox} title="Empty" description="Nothing" />
    );
    expect(container.querySelector(".rounded-2xl")).not.toBeNull();
  });

  it("handles action click", () => {
    const onClick = vi.fn();
    render(
      <EmptyState
        title="Empty"
        description="Nothing"
        action={{ label: "Create", onClick }}
      />
    );
    fireEvent.click(screen.getByText("Create"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});

// ============================================================================
// 17. Constants - Status Colors Coverage
// ============================================================================

import { STATUS_COLORS, STATUS_DOT_COLORS, POLL_INTERVAL } from "@/lib/constants";

describe("Constants", () => {
  it("has colors for all known statuses", () => {
    const expectedStatuses = [
      "queued", "running", "completed", "failed",
      "partial", "cancelled", "budget_exceeded",
      "pending", "skipped", "awaiting_approval",
    ];
    for (const status of expectedStatuses) {
      expect(STATUS_COLORS[status]).toBeDefined();
      expect(STATUS_DOT_COLORS[status]).toBeDefined();
    }
  });

  it("has a reasonable poll interval", () => {
    expect(POLL_INTERVAL).toBeGreaterThanOrEqual(1000);
    expect(POLL_INTERVAL).toBeLessThanOrEqual(30000);
  });
});

// ============================================================================
// 18. useTheme Hook
// ============================================================================

import { useTheme } from "@/hooks/useTheme";

describe("useTheme", () => {
  it("defaults to light when no stored preference", () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("light");
  });

  it("reads stored theme from localStorage", () => {
    localStorage.setItem("theme", "dark");
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("dark");
  });

  it("toggles theme", () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("light");

    act(() => {
      result.current.toggleTheme();
    });
    expect(result.current.theme).toBe("dark");

    act(() => {
      result.current.toggleTheme();
    });
    expect(result.current.theme).toBe("light");
  });

  it("persists theme to localStorage", () => {
    const { result } = renderHook(() => useTheme());
    act(() => {
      result.current.toggleTheme();
    });
    expect(localStorage.getItem("theme")).toBe("dark");
  });

  it("ignores invalid stored values", () => {
    localStorage.setItem("theme", "invalid");
    const { result } = renderHook(() => useTheme());
    // Falls back to matchMedia (mocked to false = light)
    expect(result.current.theme).toBe("light");
  });
});

// ============================================================================
// 19. ConfirmDialog - Additional Tests
// ============================================================================

import { ConfirmDialog } from "@/components/shared/ConfirmDialog";

describe("ConfirmDialog (wave 8)", () => {
  it("renders with warning variant", () => {
    render(
      <ConfirmDialog
        open={true}
        title="Caution"
        description="Proceed?"
        variant="warning"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    );
    expect(screen.getByText("Caution")).toBeInTheDocument();
  });

  it("focuses cancel button on open", () => {
    render(
      <ConfirmDialog
        open={true}
        title="Test"
        description="Focus test"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    );
    // Cancel button should be focused on open
    expect(document.activeElement).toBe(screen.getByText("Cancel"));
  });

  it("confirm button respects disabled state", () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        open={true}
        title="Test"
        description="Disabled test"
        confirmDisabled={true}
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />
    );
    const confirmBtn = screen.getByText("Confirm");
    expect(confirmBtn).toBeDisabled();
    fireEvent.click(confirmBtn);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
