/**
 * useAdvisor Error Propagation Tests
 *
 * Tests that the useAdvisor hook correctly collects and surfaces errors
 * from the 13 API endpoints it fetches in parallel.
 *
 * Scenarios:
 * 1. All 13 endpoints fail -> errors array has 13 entries
 * 2. 3 endpoints fail, 10 succeed -> errors has exactly 3 entries
 * 3. Error format: "endpoint_name: message"
 * 4. All succeed -> errors is empty
 * 5. After refresh where errors clear -> errors becomes empty
 */

import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";

// ============================================================================
// Mocks - must be hoisted before imports of the modules under test
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

const mockSubscribe = vi.fn(() => () => {});

vi.mock("@/hooks/useEventStreamContext", () => ({
  useEventStreamContext: () => ({
    events: [],
    connectionStatus: "disconnected",
    subscribe: mockSubscribe,
    clearEvents: vi.fn(),
  }),
}));

// Mock insights pure functions - we only care about error collection here,
// not scoring/insight generation
vi.mock("@/lib/insights", () => ({
  computeScore: vi.fn(() => ({ score: 100, deductions: [] })),
  generateInsights: vi.fn(() => []),
  filterDismissed: vi.fn((insights: unknown[]) => insights),
  dismissInsight: vi.fn(),
}));

// ============================================================================
// Import after mocks are set up
// ============================================================================

import { api } from "@/api/client";
import { useAdvisor } from "@/hooks/useAdvisor";

// ============================================================================
// Helpers
// ============================================================================

const SUCCESS_RESPONSE = { data: {}, error: null };

const ERROR_RESPONSE = {
  data: null,
  error: { code: "HTTP_500", message: "Internal Server Error" },
};

/** All 13 endpoint names in the order useAdvisor labels them. */
const ENDPOINT_NAMES = [
  "health",
  "stats",
  "runs",
  "tools",
  "dead-letter",
  "violations",
  "optimizer",
  "autopilot",
  "approvals",
  "workflows",
  "schedules",
  "eval",
  "api-keys",
] as const;

/**
 * Build an api.get mock that maps each call's path to the provided response.
 * The order of api.get calls in useAdvisor is:
 *   /health, /stats, /runs, /tools, /dead-letter, /violations/stats,
 *   /optimizer/stats, /autopilot/stats, /approvals, /workflows, /schedules,
 *   /eval/stats, /api-keys
 *
 * We match by path prefix.
 */
function makeGetMock(failPaths: Set<string>) {
  return vi.fn(async (path: string) => {
    const normalised = path.split("?")[0]; // strip query params just in case
    const shouldFail =
      failPaths.has(normalised) ||
      // also match /violations/stats -> "violations"
      [...failPaths].some((p) => normalised.startsWith(`/${p}`));
    if (shouldFail) return { ...ERROR_RESPONSE };
    return { ...SUCCESS_RESPONSE };
  });
}

// ============================================================================
// Tests
// ============================================================================

describe("useAdvisor - error propagation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSubscribe.mockReturnValue(() => {});
  });

  // --------------------------------------------------------------------------
  // 1. All 13 endpoints fail
  // --------------------------------------------------------------------------
  it("collects 13 errors when all endpoints return errors", async () => {
    (api.get as Mock).mockResolvedValue({ ...ERROR_RESPONSE });

    const { result } = renderHook(() => useAdvisor());

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.errors).toHaveLength(13);
  });

  // --------------------------------------------------------------------------
  // 2. Exactly 3 endpoints fail
  // --------------------------------------------------------------------------
  it("collects exactly 3 errors when 3 endpoints fail and 10 succeed", async () => {
    // Fail: health, dead-letter, api-keys
    const failingEndpoints = ["/health", "/dead-letter", "/api-keys"];

    (api.get as Mock).mockImplementation(async (path: string) => {
      if (failingEndpoints.some((p) => path === p || path.startsWith(p))) {
        return { ...ERROR_RESPONSE };
      }
      return { ...SUCCESS_RESPONSE };
    });

    const { result } = renderHook(() => useAdvisor());

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.errors).toHaveLength(3);
  });

  // --------------------------------------------------------------------------
  // 3. Error format: "endpoint_name: message"
  // --------------------------------------------------------------------------
  it("formats each error as 'endpoint_name: message'", async () => {
    (api.get as Mock).mockResolvedValue({ ...ERROR_RESPONSE });

    const { result } = renderHook(() => useAdvisor());

    await waitFor(() => expect(result.current.loading).toBe(false));

    // Every error entry must match the format "name: message"
    for (const entry of result.current.errors) {
      // Should contain ": " separator
      expect(entry).toMatch(/^[^:]+: .+$/);
    }

    // Check a concrete endpoint name appears in the errors list
    const expectedNames = new Set(ENDPOINT_NAMES);
    for (const entry of result.current.errors) {
      const name = entry.split(": ")[0];
      expect(expectedNames.has(name as (typeof ENDPOINT_NAMES)[number])).toBe(true);
    }
  });

  // --------------------------------------------------------------------------
  // 3b. Error message is taken from error.message, not error.code
  // --------------------------------------------------------------------------
  it("uses error.message in the error string (not error.code)", async () => {
    (api.get as Mock).mockResolvedValue({
      data: null,
      error: { code: "HTTP_500", message: "Internal Server Error" },
    });

    const { result } = renderHook(() => useAdvisor());

    await waitFor(() => expect(result.current.loading).toBe(false));

    // All entries should reference the message, not the code
    for (const entry of result.current.errors) {
      expect(entry).toContain("Internal Server Error");
      expect(entry).not.toContain("HTTP_500");
    }
  });

  // --------------------------------------------------------------------------
  // 4. All succeed -> errors empty
  // --------------------------------------------------------------------------
  it("produces an empty errors array when all endpoints succeed", async () => {
    (api.get as Mock).mockResolvedValue({ ...SUCCESS_RESPONSE });

    const { result } = renderHook(() => useAdvisor());

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.errors).toHaveLength(0);
    expect(result.current.errors).toEqual([]);
  });

  // --------------------------------------------------------------------------
  // 5. After refresh where previous errors clear -> errors becomes empty
  // --------------------------------------------------------------------------
  it("clears errors on refresh when endpoints subsequently succeed", async () => {
    // First call: all fail
    (api.get as Mock).mockResolvedValueOnce({ ...ERROR_RESPONSE }); // health
    // Return error for all 13 on first fetchAll pass
    (api.get as Mock).mockResolvedValue({ ...ERROR_RESPONSE });

    const { result } = renderHook(() => useAdvisor());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.errors.length).toBeGreaterThan(0);

    // Second call: all succeed
    (api.get as Mock).mockResolvedValue({ ...SUCCESS_RESPONSE });

    await act(async () => {
      await result.current.refresh();
    });

    expect(result.current.errors).toHaveLength(0);
  });

  // --------------------------------------------------------------------------
  // Bonus: loading starts as true and turns false after fetch
  // --------------------------------------------------------------------------
  it("sets loading to false after fetchAll completes", async () => {
    (api.get as Mock).mockResolvedValue({ ...SUCCESS_RESPONSE });

    const { result } = renderHook(() => useAdvisor());

    expect(result.current.loading).toBe(true);

    await waitFor(() => expect(result.current.loading).toBe(false));
  });

  // --------------------------------------------------------------------------
  // Bonus: partial error with code fallback when message is missing
  // --------------------------------------------------------------------------
  it("falls back to error.code when error.message is absent/undefined", async () => {
    (api.get as Mock).mockResolvedValue({
      data: null,
      error: { code: "NETWORK_ERROR", message: undefined as unknown as string },
    });

    const { result } = renderHook(() => useAdvisor());

    await waitFor(() => expect(result.current.loading).toBe(false));

    // When message is absent, the hook uses `res.error.message ?? res.error.code`
    // so entries should fall back to the code
    for (const entry of result.current.errors) {
      expect(entry).toContain("NETWORK_ERROR");
    }
  });
});
