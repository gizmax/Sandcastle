import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

vi.mock("@/api/client", () => ({
  api: {
    get: vi.fn(),
    setApiKey: vi.fn(),
    isMockMode: false,
    onMockChange: vi.fn(() => () => {}),
  },
}));

import { useRuntimeInfo } from "@/hooks/useRuntimeInfo";
import { api } from "@/api/client";

const mockApi = api as unknown as { get: ReturnType<typeof vi.fn> };

describe("useRuntimeInfo", () => {
  beforeEach(() => {
    mockApi.get.mockReset();
  });

  it("starts in loading state", () => {
    mockApi.get.mockResolvedValue({ data: null });
    const { result } = renderHook(() => useRuntimeInfo());
    expect(result.current.loading).toBe(true);
    expect(result.current.info).toBeNull();
  });

  it("returns runtime info on success", async () => {
    const runtimeInfo = {
      mode: "local" as const,
      database: "sqlite",
      queue: "in-memory",
      storage: "local",
      data_dir: "/data",
      version: "0.17.0",
      sandbox_backend: "e2b" as const,
      license: {
        status: "valid" as const,
        tier: "pro" as const,
        licensee: "Test User",
        max_seats: 10,
        expires: "2027-01-01",
      },
    };
    mockApi.get.mockResolvedValue({ data: runtimeInfo });

    const { result } = renderHook(() => useRuntimeInfo());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.info).toEqual(runtimeInfo);
    expect(result.current.info?.sandbox_backend).toBe("e2b");
    expect(result.current.info?.license?.tier).toBe("pro");
  });

  it("stays null when API returns null data", async () => {
    mockApi.get.mockResolvedValue({ data: null });

    const { result } = renderHook(() => useRuntimeInfo());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.info).toBeNull();
  });

  it("handles missing license field", async () => {
    mockApi.get.mockResolvedValue({
      data: {
        mode: "local",
        database: "sqlite",
        queue: "in-memory",
        storage: "local",
        data_dir: null,
        version: "0.17.0",
        sandbox_backend: "local",
        license: null,
      },
    });

    const { result } = renderHook(() => useRuntimeInfo());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.info?.license).toBeNull();
  });
});
