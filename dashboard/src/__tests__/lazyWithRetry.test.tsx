import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { Suspense } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { lazyWithRetry, isChunkLoadError } from "@/lib/lazyWithRetry";
import { ErrorBoundary } from "@/components/shared/ErrorBoundary";

function chunkError(): TypeError {
  return new TypeError(
    "Failed to fetch dynamically imported module: https://x/assets/Foo-abc123.js"
  );
}

describe("isChunkLoadError", () => {
  it("matches the dynamic-import failure shapes across engines", () => {
    expect(isChunkLoadError(chunkError())).toBe(true);
    expect(
      isChunkLoadError(new Error("error loading dynamically imported module"))
    ).toBe(true);
    expect(
      isChunkLoadError(new Error("Importing a module script failed."))
    ).toBe(true);
    const named = new Error("whatever");
    named.name = "ChunkLoadError";
    expect(isChunkLoadError(named)).toBe(true);
    // Vite's per-chunk CSS preload 404 rejects the same import.
    expect(
      isChunkLoadError(new Error("Unable to preload CSS for /assets/Foo-abc.css"))
    ).toBe(true);
  });

  it("does NOT match a plain network fetch failure or generic errors", () => {
    // The crucial distinction: a real api/network failure stays a network error.
    expect(isChunkLoadError(new TypeError("Failed to fetch"))).toBe(false);
    expect(isChunkLoadError(new Error("boom"))).toBe(false);
    expect(isChunkLoadError("not an error")).toBe(false);
    expect(isChunkLoadError(null)).toBe(false);
    expect(isChunkLoadError(undefined)).toBe(false);
  });
});

describe("lazyWithRetry", () => {
  let reload: ReturnType<typeof vi.fn>;
  let consoleError: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    reload = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, reload },
    });
    window.sessionStorage.clear();
    consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    consoleError?.mockRestore();
    window.sessionStorage.clear();
    // Restore online state in case a test stubbed it.
    Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
  });

  it("renders the component when the import succeeds and leaves no guard behind", async () => {
    const Lazy = lazyWithRetry(
      () => Promise.resolve({ default: () => <div>loaded ok</div> }),
      "ok"
    );
    render(
      <Suspense fallback={<div>loading</div>}>
        <Lazy />
      </Suspense>
    );
    await screen.findByText("loaded ok");
    expect(reload).not.toHaveBeenCalled();
    expect(window.sessionStorage.getItem("sc:chunk-reload:ok")).toBeNull();
  });

  it("reloads exactly once on a chunk miss and keeps the Suspense fallback up", async () => {
    const Lazy = lazyWithRetry(() => Promise.reject(chunkError()), "miss");
    render(
      <ErrorBoundary>
        <Suspense fallback={<div>loading</div>}>
          <Lazy />
        </Suspense>
      </ErrorBoundary>
    );
    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    // Guard set so a second failure won't loop.
    expect(window.sessionStorage.getItem("sc:chunk-reload:miss")).toBe("1");
    // The load-bearing line: the never-settling promise must keep the fallback
    // up and NEVER flash the boundary. Give React real time to retry the thunk
    // and propagate any throw - a single microtask flush would be too short to
    // catch a regression where line returns `throw err` instead of the promise.
    await new Promise((r) => setTimeout(r, 200));
    expect(screen.queryByText("Update available")).toBeNull();
    expect(screen.queryByText("Something went wrong")).toBeNull();
    expect(screen.getByText("loading")).toBeInTheDocument();
  });

  it("clears the guard after a reload-recovered import finally succeeds", async () => {
    // Simulate the recovery path: guard was set by the pre-reload attempt; on
    // the fresh document the import now succeeds, so the guard must be cleared.
    window.sessionStorage.setItem("sc:chunk-reload:recovered", "1");
    const Lazy = lazyWithRetry(
      () => Promise.resolve({ default: () => <div>recovered</div> }),
      "recovered"
    );
    render(
      <Suspense fallback={<div>loading</div>}>
        <Lazy />
      </Suspense>
    );
    await screen.findByText("recovered");
    expect(window.sessionStorage.getItem("sc:chunk-reload:recovered")).toBeNull();
    expect(reload).not.toHaveBeenCalled();
  });

  it("does NOT auto-reload when sessionStorage cannot persist the guard (would loop)", async () => {
    // Private mode / blocked storage: setItem throws so the guard is not durable.
    // Auto-reloading here would loop forever, so we must surface to the boundary.
    const setItem = vi
      .spyOn(window.sessionStorage, "setItem")
      .mockImplementation(() => {
        throw new DOMException("QuotaExceededError");
      });
    const Lazy = lazyWithRetry(() => Promise.reject(chunkError()), "nostore");
    render(
      <ErrorBoundary>
        <Suspense fallback={<div>loading</div>}>
          <Lazy />
        </Suspense>
      </ErrorBoundary>
    );
    await screen.findByText("Update available");
    expect(reload).not.toHaveBeenCalled();
    setItem.mockRestore();
  });

  it("does NOT auto-reload when offline; it surfaces an offline-aware boundary", async () => {
    Object.defineProperty(navigator, "onLine", { configurable: true, value: false });
    const Lazy = lazyWithRetry(() => Promise.reject(chunkError()), "offline");
    render(
      <ErrorBoundary>
        <Suspense fallback={<div>loading</div>}>
          <Lazy />
        </Suspense>
      </ErrorBoundary>
    );
    // Offline: a reload would not bring the chunk back, so no auto-reload, and
    // the message must not claim a deploy happened.
    await screen.findByText("Couldn't load this page");
    expect(reload).not.toHaveBeenCalled();
    expect(screen.queryByText("Update available")).toBeNull();
  });

  it("does not reload twice: after the guard is set it rethrows to the boundary", async () => {
    window.sessionStorage.setItem("sc:chunk-reload:already", "1");
    const Lazy = lazyWithRetry(() => Promise.reject(chunkError()), "already");
    render(
      <ErrorBoundary>
        <Suspense fallback={<div>loading</div>}>
          <Lazy />
        </Suspense>
      </ErrorBoundary>
    );
    // Boundary classifies it as a chunk error and offers a manual reload.
    await screen.findByText("Update available");
    expect(reload).not.toHaveBeenCalled();
  });

  it("never auto-reloads on a non-chunk error; it surfaces to the boundary", async () => {
    const Lazy = lazyWithRetry(() => Promise.reject(new Error("real bug")), "bug");
    render(
      <ErrorBoundary>
        <Suspense fallback={<div>loading</div>}>
          <Lazy />
        </Suspense>
      </ErrorBoundary>
    );
    await screen.findByText("Something went wrong");
    expect(reload).not.toHaveBeenCalled();
  });
});
