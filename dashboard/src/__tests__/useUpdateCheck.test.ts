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

import { useUpdateCheck } from "@/hooks/useUpdateCheck";
import { api } from "@/api/client";

const mockApi = api as unknown as { get: ReturnType<typeof vi.fn> };

describe("useUpdateCheck", () => {
  beforeEach(() => {
    mockApi.get.mockReset();
  });

  it("starts in loading state", () => {
    mockApi.get.mockResolvedValue({ data: null });
    const { result } = renderHook(() => useUpdateCheck());
    expect(result.current.loading).toBe(true);
  });

  it("returns update info when available", async () => {
    mockApi.get.mockResolvedValue({
      data: {
        current_version: "0.16.0",
        latest_version: "0.17.0",
        update_available: true,
        release_url: "https://github.com/gizmax/Sandcastle/releases/v0.17.0",
        install_command: "pip install --upgrade sandcastle-ai",
      },
    });

    const { result } = renderHook(() => useUpdateCheck());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.updateAvailable).toBe(true);
    expect(result.current.currentVersion).toBe("0.16.0");
    expect(result.current.latestVersion).toBe("0.17.0");
    expect(result.current.installCommand).toBe("pip install --upgrade sandcastle-ai");
    expect(result.current.releaseUrl).toContain("v0.17.0");
  });

  it("returns defaults when no update available", async () => {
    mockApi.get.mockResolvedValue({
      data: {
        current_version: "0.17.0",
        latest_version: "0.17.0",
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

  it("returns safe defaults when API returns null data", async () => {
    mockApi.get.mockResolvedValue({ data: null });

    const { result } = renderHook(() => useUpdateCheck());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.updateAvailable).toBe(false);
    expect(result.current.currentVersion).toBeNull();
    expect(result.current.latestVersion).toBeNull();
    expect(result.current.installCommand).toBeNull();
    expect(result.current.releaseUrl).toBeNull();
  });
});
