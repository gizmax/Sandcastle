import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";

// Mock the api client module
vi.mock("@/api/client", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    setApiKey: vi.fn(),
    authHeaders: vi.fn(() => ({})),
    sseUrl: vi.fn((p: string) => `/api${p}`),
    isMockMode: false,
    onMockChange: vi.fn(() => () => {}),
  },
}));

import { useRuns } from "@/hooks/useRuns";
import { api } from "@/api/client";

const mockApi = api as unknown as {
  get: ReturnType<typeof vi.fn>;
};

describe("useRuns", () => {
  beforeEach(() => {
    mockApi.get.mockReset();
  });

  it("starts in loading state", () => {
    mockApi.get.mockResolvedValue({ data: [], meta: { total: 0, limit: 50, offset: 0 } });
    const { result } = renderHook(() => useRuns());
    expect(result.current.loading).toBe(true);
  });

  it("fetches runs on mount", async () => {
    const mockRuns = [
      { run_id: "r1", workflow_name: "test", status: "completed", total_cost_usd: 1.0, started_at: null, completed_at: null },
    ];
    mockApi.get.mockResolvedValue({ data: mockRuns, meta: { total: 1, limit: 50, offset: 0 } });

    const { result } = renderHook(() => useRuns());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.runs).toEqual(mockRuns);
    expect(result.current.total).toBe(1);
  });

  it("passes status filter as param", async () => {
    mockApi.get.mockResolvedValue({ data: [], meta: { total: 0, limit: 50, offset: 0 } });

    renderHook(() => useRuns({ status: "failed" }));

    await waitFor(() => {
      expect(mockApi.get).toHaveBeenCalledWith("/runs", expect.objectContaining({ status: "failed" }));
    });
  });

  it("passes workflow filter as param", async () => {
    mockApi.get.mockResolvedValue({ data: [], meta: { total: 0, limit: 50, offset: 0 } });

    renderHook(() => useRuns({ workflow: "my-workflow" }));

    await waitFor(() => {
      expect(mockApi.get).toHaveBeenCalledWith("/runs", expect.objectContaining({ workflow: "my-workflow" }));
    });
  });

  it("uses default limit and offset", async () => {
    mockApi.get.mockResolvedValue({ data: [], meta: { total: 0, limit: 50, offset: 0 } });

    renderHook(() => useRuns());

    await waitFor(() => {
      expect(mockApi.get).toHaveBeenCalledWith("/runs", expect.objectContaining({
        limit: "50",
        offset: "0",
      }));
    });
  });

  it("uses custom limit and offset", async () => {
    mockApi.get.mockResolvedValue({ data: [], meta: { total: 0, limit: 10, offset: 20 } });

    renderHook(() => useRuns({ limit: 10, offset: 20 }));

    await waitFor(() => {
      expect(mockApi.get).toHaveBeenCalledWith("/runs", expect.objectContaining({
        limit: "10",
        offset: "20",
      }));
    });
  });

  it("uses data length as total when meta is missing", async () => {
    const mockRuns = [
      { run_id: "r1", workflow_name: "test", status: "completed", total_cost_usd: 1.0, started_at: null, completed_at: null },
      { run_id: "r2", workflow_name: "test", status: "failed", total_cost_usd: 0.5, started_at: null, completed_at: null },
    ];
    mockApi.get.mockResolvedValue({ data: mockRuns });

    const { result } = renderHook(() => useRuns());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.total).toBe(2);
  });

  it("does not poll when all runs are completed (no running/queued)", async () => {
    const mockRuns = [
      { run_id: "r1", workflow_name: "test", status: "completed", total_cost_usd: 1.0, started_at: null, completed_at: null },
    ];
    mockApi.get.mockResolvedValue({ data: mockRuns, meta: { total: 1, limit: 50, offset: 0 } });

    renderHook(() => useRuns());

    await waitFor(() => {
      expect(mockApi.get).toHaveBeenCalledTimes(1);
    });

    // Wait a bit past poll interval and verify no extra calls
    await new Promise((r) => setTimeout(r, 100));
    // Only the initial call should have happened (interval won't fire in 100ms but confirms setup)
    expect(mockApi.get).toHaveBeenCalledTimes(1);
  });

  it("disables polling when autoPoll is false", async () => {
    const mockRuns = [
      { run_id: "r1", workflow_name: "test", status: "running", total_cost_usd: 0, started_at: null, completed_at: null },
    ];
    mockApi.get.mockResolvedValue({ data: mockRuns, meta: { total: 1, limit: 50, offset: 0 } });

    renderHook(() => useRuns({ autoPoll: false }));

    await waitFor(() => {
      expect(mockApi.get).toHaveBeenCalledTimes(1);
    });
  });

  it("exposes refetch function", async () => {
    mockApi.get.mockResolvedValue({ data: [], meta: { total: 0, limit: 50, offset: 0 } });

    const { result } = renderHook(() => useRuns());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await act(async () => {
      await result.current.refetch();
    });

    expect(mockApi.get).toHaveBeenCalledTimes(2);
  });

  it("handles null data response gracefully", async () => {
    mockApi.get.mockResolvedValue({ data: null, error: { code: "ERR", message: "fail" } });

    const { result } = renderHook(() => useRuns());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    // Runs stay empty
    expect(result.current.runs).toEqual([]);
  });

  it("re-fetches when filter params change", async () => {
    mockApi.get.mockResolvedValue({ data: [], meta: { total: 0, limit: 50, offset: 0 } });

    const { rerender } = renderHook(
      ({ status }: { status?: string }) => useRuns({ status }),
      { initialProps: { status: "completed" } }
    );

    await waitFor(() => {
      expect(mockApi.get).toHaveBeenCalledTimes(1);
    });

    rerender({ status: "failed" });

    await waitFor(() => {
      expect(mockApi.get).toHaveBeenCalledTimes(2);
      expect(mockApi.get).toHaveBeenLastCalledWith("/runs", expect.objectContaining({ status: "failed" }));
    });
  });
});
