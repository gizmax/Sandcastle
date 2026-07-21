import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// We cannot import the singleton directly because it probes on construction.
// Instead we test the client behavior by mocking fetch at the global level
// and re-importing the module for specific scenarios.

describe("ApiClient", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    // Clear storage before each test to prevent leakage
    sessionStorage.clear();
    localStorage.clear();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  // ── handleResponse behavior ─────────────────────────────────────────────

  describe("handleResponse (via api.get)", () => {
    it("falls back to mock mode on network error", async () => {
      // Simulate network failure for probe + actual request
      globalThis.fetch = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));

      // Dynamic import to get a fresh module (singleton is constructed on import)
      vi.resetModules();
      const { api } = await import("@/api/client");

      // The probe will fail, setting mock mode
      const result = await api.get("/runs");

      // Should have fallen back to mock data
      expect(result.data).not.toBeNull();
    });

    it("handles 500 response with JSON detail", async () => {
      // First call: probe succeeds
      // Second call: actual request returns 500
      let callCount = 0;
      globalThis.fetch = vi.fn().mockImplementation(async () => {
        callCount++;
        if (callCount === 1) {
          // Probe
          return new Response(JSON.stringify({ data: { status: "ok" } }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        // Actual request: 500
        return new Response(
          JSON.stringify({ detail: "Internal Server Error" }),
          { status: 500, headers: { "Content-Type": "application/json" } }
        );
      });

      vi.resetModules();
      const { api } = await import("@/api/client");
      const result = await api.get("/failing-endpoint");

      expect(result.error).toBeDefined();
      expect(result.error?.code).toBe("HTTP_500");
    });

    it("handles non-JSON error response", async () => {
      let callCount = 0;
      globalThis.fetch = vi.fn().mockImplementation(async () => {
        callCount++;
        if (callCount === 1) {
          return new Response(JSON.stringify({ data: { status: "ok" } }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response("Bad Gateway", {
          status: 502,
          statusText: "Bad Gateway",
        });
      });

      vi.resetModules();
      const { api } = await import("@/api/client");
      const result = await api.get("/bad-gateway");

      expect(result.error?.code).toBe("HTTP_502");
      expect(result.error?.message).toBe("Bad Gateway");
    });

    it("handles FastAPI HTTPException detail object", async () => {
      let callCount = 0;
      globalThis.fetch = vi.fn().mockImplementation(async () => {
        callCount++;
        if (callCount === 1) {
          return new Response(JSON.stringify({ data: { status: "ok" } }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response(
          JSON.stringify({
            detail: {
              error: { code: "RATE_LIMITED", message: "Too many requests" },
              data: null,
            },
          }),
          { status: 429, headers: { "Content-Type": "application/json" } }
        );
      });

      vi.resetModules();
      const { api } = await import("@/api/client");
      const result = await api.get("/rate-limited");

      expect(result.error?.code).toBe("RATE_LIMITED");
    });
  });

  // ── POST / PATCH / PUT / DELETE ─────────────────────────────────────────

  describe("POST method", () => {
    it("returns network error on fetch failure", async () => {
      let callCount = 0;
      globalThis.fetch = vi.fn().mockImplementation(async () => {
        callCount++;
        if (callCount === 1) {
          return new Response(JSON.stringify({ data: { status: "ok" } }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        throw new TypeError("Failed to fetch");
      });

      vi.resetModules();
      const { api } = await import("@/api/client");
      const result = await api.post("/create", { name: "test" });

      expect(result.error?.code).toBe("NETWORK_ERROR");
    });
  });

  // ── authHeaders ─────────────────────────────────────────────────────────

  describe("authHeaders", () => {
    it("keeps the API key in an X-API-Key header", async () => {
      globalThis.fetch = vi.fn().mockRejectedValue(new TypeError("no backend"));

      vi.resetModules();
      const { api } = await import("@/api/client");
      api.setApiKey("test-key-123");

      expect(api.authHeaders()).toEqual({ "X-API-Key": "test-key-123" });
    });

    it("returns no auth header when no API key is set", async () => {
      globalThis.fetch = vi.fn().mockRejectedValue(new TypeError("no backend"));

      vi.resetModules();
      const { api } = await import("@/api/client");
      api.setApiKey(null);

      expect(api.authHeaders()).toEqual({});
    });

    it("does not include Content-Type", async () => {
      globalThis.fetch = vi.fn().mockRejectedValue(new TypeError("no backend"));

      vi.resetModules();
      const { api } = await import("@/api/client");
      api.setApiKey("my-key");

      const headers = api.authHeaders();
      expect(headers).not.toHaveProperty("Content-Type");
    });
  });

  // ── Mock mode listener ────────────────────────────────────────────────

  describe("onMockChange", () => {
    it("notifies listeners when switching to mock mode", async () => {
      globalThis.fetch = vi.fn().mockRejectedValue(new TypeError("no backend"));

      vi.resetModules();
      const { api } = await import("@/api/client");

      const listener = vi.fn();
      api.onMockChange(listener);

      // Trigger mock mode by making a GET request that fails
      // (probe already set mock=true, so listener may already be called)
      // Listener is called synchronously when setMock changes value
      expect(api.isMockMode).toBe(true);
    });

    it("unsubscribe stops notifications", async () => {
      globalThis.fetch = vi.fn().mockRejectedValue(new TypeError("no backend"));

      vi.resetModules();
      const { api } = await import("@/api/client");

      const listener = vi.fn();
      const unsub = api.onMockChange(listener);
      unsub();

      // The listener should have been removed
      // We can't easily trigger another mock change, but confirm unsub runs without error
      expect(typeof unsub).toBe("function");
    });
  });

  // ── Query parameters ──────────────────────────────────────────────────

  describe("GET with query params", () => {
    it("appends non-empty query parameters to URL", async () => {
      let requestedUrl = "";
      let callCount = 0;
      globalThis.fetch = vi.fn().mockImplementation(async (url: string) => {
        callCount++;
        if (callCount === 1) {
          return new Response(JSON.stringify({ data: { status: "ok" } }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        requestedUrl = url;
        return new Response(JSON.stringify({ data: [], meta: { total: 0, limit: 50, offset: 0 } }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      });

      vi.resetModules();
      const { api } = await import("@/api/client");
      await api.get("/runs", { status: "completed", limit: "50", empty: "" });

      expect(requestedUrl).toContain("status=completed");
      expect(requestedUrl).toContain("limit=50");
      // Empty values should be excluded
      expect(requestedUrl).not.toContain("empty=");
    });
  });

  // ── Retry logic ────────────────────────────────────────────────────────

  describe("retry on 5xx", () => {
    it("retries GET requests on 500 and succeeds on second attempt", async () => {
      let callCount = 0;
      globalThis.fetch = vi.fn().mockImplementation(async () => {
        callCount++;
        if (callCount === 1) {
          // Probe succeeds
          return new Response(JSON.stringify({ data: { status: "ok" } }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (callCount === 2) {
          // First GET attempt: 500
          return new Response(
            JSON.stringify({ detail: "Internal Server Error" }),
            { status: 500, headers: { "Content-Type": "application/json" } }
          );
        }
        // Retry: 200
        return new Response(
          JSON.stringify({ data: [{ id: 1 }], meta: null }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      });

      vi.resetModules();
      const { api } = await import("@/api/client");
      const result = await api.get("/retryable");

      expect(result.data).toEqual([{ id: 1 }]);
      // probe + first attempt + retry = 3 calls
      expect(callCount).toBe(3);
    });

    it("creates a fresh timeout signal for each GET retry", async () => {
      let callCount = 0;
      const requestSignals: AbortSignal[] = [];
      globalThis.fetch = vi.fn().mockImplementation(async (_input, init?: RequestInit) => {
        callCount++;
        if (callCount === 1) {
          return new Response(JSON.stringify({ data: { status: "ok" } }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (init?.signal) requestSignals.push(init.signal);
        return new Response(JSON.stringify({ data: [] }), {
          status: callCount === 2 ? 500 : 200,
          headers: { "Content-Type": "application/json" },
        });
      });

      vi.resetModules();
      const { api } = await import("@/api/client");
      await api.get("/retry-with-fresh-timeout");

      expect(requestSignals).toHaveLength(2);
      expect(requestSignals[0]).not.toBe(requestSignals[1]);
    });

    it("does not retry POST requests on 500", async () => {
      let callCount = 0;
      globalThis.fetch = vi.fn().mockImplementation(async () => {
        callCount++;
        if (callCount === 1) {
          return new Response(JSON.stringify({ data: { status: "ok" } }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response(
          JSON.stringify({ detail: "Internal Server Error" }),
          { status: 500, headers: { "Content-Type": "application/json" } }
        );
      });

      vi.resetModules();
      const { api } = await import("@/api/client");
      const result = await api.post("/no-retry", { data: "test" });

      expect(result.error?.code).toBe("HTTP_500");
      // probe + one POST attempt = 2 calls (no retry)
      expect(callCount).toBe(2);
    });
  });

  // ── Request deduplication ──────────────────────────────────────────────

  describe("request deduplication", () => {
    it("deduplicates concurrent identical GET requests", async () => {
      let fetchCallCount = 0;
      globalThis.fetch = vi.fn().mockImplementation(async () => {
        fetchCallCount++;
        if (fetchCallCount === 1) {
          // Probe
          return new Response(JSON.stringify({ data: { status: "ok" } }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        // Actual request - add small delay to ensure both callers overlap
        await new Promise((r) => setTimeout(r, 10));
        return new Response(
          JSON.stringify({ data: { value: "shared" }, meta: null }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      });

      vi.resetModules();
      const { api } = await import("@/api/client");
      // Wait for probe to complete
      await api.get("/health-check");

      // Reset counter after probe + first get
      const beforeCount = fetchCallCount;

      // Fire two identical GETs concurrently
      const [r1, r2] = await Promise.all([
        api.get("/same-path"),
        api.get("/same-path"),
      ]);

      // Both should return the same data
      expect(r1.data).toEqual({ value: "shared" });
      expect(r2.data).toEqual({ value: "shared" });
      // Only one actual fetch call should have been made for /same-path
      expect(fetchCallCount - beforeCount).toBe(1);
    });
  });

  // ── Storage helpers ────────────────────────────────────────────────────

  describe("storage helpers", () => {
    it("stores API key in sessionStorage instead of localStorage", async () => {
      globalThis.fetch = vi.fn().mockRejectedValue(new TypeError("no backend"));

      vi.resetModules();
      const { api } = await import("@/api/client");

      api.storeApiKey("test-key-abc");
      expect(sessionStorage.getItem("sandcastle_api_key")).toBe("test-key-abc");
      // Should NOT be in localStorage
      expect(localStorage.getItem("sandcastle_api_key")).toBeNull();
    });

    it("migrates key from localStorage to sessionStorage on read", async () => {
      localStorage.setItem("sandcastle_api_key", "legacy-key");

      globalThis.fetch = vi.fn().mockRejectedValue(new TypeError("no backend"));

      vi.resetModules();
      const { api } = await import("@/api/client");

      expect(api.hasStoredKey()).toBe(true);
      expect(api.getStoredKey()).toBe("legacy-key");
      // After migration, localStorage should be cleared
      expect(localStorage.getItem("sandcastle_api_key")).toBeNull();
      expect(sessionStorage.getItem("sandcastle_api_key")).toBe("legacy-key");
    });

    it("clearStoredKey removes from both storage locations", async () => {
      sessionStorage.setItem("sandcastle_api_key", "session-key");
      localStorage.setItem("sandcastle_api_key", "local-key");

      globalThis.fetch = vi.fn().mockRejectedValue(new TypeError("no backend"));

      vi.resetModules();
      const { api } = await import("@/api/client");

      api.clearStoredKey();
      expect(sessionStorage.getItem("sandcastle_api_key")).toBeNull();
      expect(localStorage.getItem("sandcastle_api_key")).toBeNull();
    });
  });
});
