import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";

// Mock the api client
vi.mock("@/api/client", () => ({
  api: {
    setApiKey: vi.fn(),
    isMockMode: false,
    onMockChange: vi.fn(() => () => {}),
  },
  // Re-export API_BASE_URL from constants mock below
}));

vi.mock("@/lib/constants", () => ({
  API_BASE_URL: "/api",
  POLL_INTERVAL: 5000,
  STATUS_COLORS: {},
  STATUS_DOT_COLORS: {},
}));

import { useAuth } from "@/hooks/useAuth";
import { api } from "@/api/client";

const mockSetApiKey = api.setApiKey as ReturnType<typeof vi.fn>;

describe("useAuth", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    mockSetApiKey.mockReset();
    localStorage.clear();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("starts in loading state", () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response("", { status: 200 }));
    const { result } = renderHook(() => useAuth());
    expect(result.current.state).toBe("loading");
  });

  it("becomes authenticated when no auth required", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response("", { status: 200 }));
    const { result } = renderHook(() => useAuth());

    await waitFor(() => {
      expect(result.current.state).toBe("authenticated");
    });
  });

  it("becomes unauthenticated when 401 returned and no saved key", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response("", { status: 401 }));
    const { result } = renderHook(() => useAuth());

    await waitFor(() => {
      expect(result.current.state).toBe("unauthenticated");
    });
  });

  it("uses saved key from localStorage", async () => {
    localStorage.setItem("sandcastle_api_key", "saved-key-123");
    let callCount = 0;
    globalThis.fetch = vi.fn().mockImplementation(async (_url: string, opts: RequestInit) => {
      callCount++;
      const headers = opts?.headers as Record<string, string>;
      // First call: no key (probe), returns 401
      if (!headers?.["X-API-Key"]) {
        return new Response("", { status: 401 });
      }
      // Second call: with key, returns 200
      if (headers?.["X-API-Key"] === "saved-key-123") {
        return new Response("", { status: 200 });
      }
      return new Response("", { status: 401 });
    });

    const { result } = renderHook(() => useAuth());

    await waitFor(() => {
      expect(result.current.state).toBe("authenticated");
    });
    expect(mockSetApiKey).toHaveBeenCalledWith("saved-key-123");
  });

  it("removes invalid saved key and becomes unauthenticated", async () => {
    localStorage.setItem("sandcastle_api_key", "bad-key");
    globalThis.fetch = vi.fn().mockResolvedValue(new Response("", { status: 401 }));

    const { result } = renderHook(() => useAuth());

    await waitFor(() => {
      expect(result.current.state).toBe("unauthenticated");
    });
    expect(localStorage.getItem("sandcastle_api_key")).toBeNull();
  });

  it("login() saves key and sets authenticated on success", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response("", { status: 200 }));

    const { result } = renderHook(() => useAuth());

    await waitFor(() => {
      expect(result.current.state).toBe("authenticated");
    });

    let loginResult: boolean;
    await act(async () => {
      loginResult = await result.current.login("new-key");
    });
    expect(loginResult!).toBe(true);
    expect(localStorage.getItem("sandcastle_api_key")).toBe("new-key");
    expect(mockSetApiKey).toHaveBeenCalledWith("new-key");
  });

  it("login() returns false on 401", async () => {
    // Initial probe: 200 (auth not required)
    let callCount = 0;
    globalThis.fetch = vi.fn().mockImplementation(async () => {
      callCount++;
      if (callCount === 1) {
        return new Response("", { status: 200 });
      }
      return new Response("", { status: 401 });
    });

    const { result } = renderHook(() => useAuth());

    await waitFor(() => {
      expect(result.current.state).toBe("authenticated");
    });

    let loginResult: boolean;
    await act(async () => {
      loginResult = await result.current.login("bad-key");
    });
    expect(loginResult!).toBe(false);
  });

  it("logout() removes key and sets unauthenticated", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response("", { status: 200 }));

    const { result } = renderHook(() => useAuth());

    await waitFor(() => {
      expect(result.current.state).toBe("authenticated");
    });

    act(() => {
      result.current.logout();
    });

    expect(result.current.state).toBe("unauthenticated");
    expect(localStorage.getItem("sandcastle_api_key")).toBeNull();
    expect(mockSetApiKey).toHaveBeenCalledWith(null);
  });

  it("treats network error as authenticated (mock mode fallback)", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));

    const { result } = renderHook(() => useAuth());

    await waitFor(() => {
      expect(result.current.state).toBe("authenticated");
    });
  });
});
