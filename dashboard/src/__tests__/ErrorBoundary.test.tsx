import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ErrorBoundary } from "@/components/shared/ErrorBoundary";

// Component that throws an error
function BrokenComponent(): React.ReactElement {
  throw new Error("Test explosion");
}

function GoodComponent(): React.ReactElement {
  return <div>Everything is fine</div>;
}

// Throws the exact shape a stale-deploy chunk miss produces.
function ChunkBroken(): React.ReactElement {
  throw new TypeError(
    "Failed to fetch dynamically imported module: https://x/assets/TemplatesPage-abc.js"
  );
}

// Throws a genuine network error (api call), not a chunk miss.
function NetworkBroken(): React.ReactElement {
  throw new TypeError("Failed to fetch");
}

describe("ErrorBoundary", () => {
  // Suppress React's error boundary console.error in tests
  let consoleError: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  it("renders children when no error occurs", () => {
    render(
      <ErrorBoundary>
        <GoodComponent />
      </ErrorBoundary>
    );
    expect(screen.getByText("Everything is fine")).toBeInTheDocument();
  });

  it("renders error fallback when child throws", () => {
    render(
      <ErrorBoundary>
        <BrokenComponent />
      </ErrorBoundary>
    );
    expect(screen.getByText("Something went wrong")).toBeInTheDocument();
    expect(screen.getByText("Test explosion")).toBeInTheDocument();
  });

  it("renders custom fallback when provided", () => {
    render(
      <ErrorBoundary fallback={<div>Custom fallback</div>}>
        <BrokenComponent />
      </ErrorBoundary>
    );
    expect(screen.getByText("Custom fallback")).toBeInTheDocument();
  });

  it("has a Try Again button that resets the boundary", () => {
    const { container: _container } = render(
      <ErrorBoundary>
        <BrokenComponent />
      </ErrorBoundary>
    );
    expect(screen.getByText("Something went wrong")).toBeInTheDocument();
    expect(screen.getByText("Try Again")).toBeInTheDocument();

    // Click Try Again - will re-render and re-throw since BrokenComponent always throws
    fireEvent.click(screen.getByText("Try Again"));
    // Should still show error since the component still throws
    expect(screen.getByText("Something went wrong")).toBeInTheDocument();
  });

  it("shows an 'Update available' reload prompt for a stale-deploy chunk miss", () => {
    render(
      <ErrorBoundary>
        <ChunkBroken />
      </ErrorBoundary>
    );
    // Chunk misses must NOT read as a network/connection problem.
    expect(screen.getByText("Update available")).toBeInTheDocument();
    expect(screen.getByText("Reload")).toBeInTheDocument();
    expect(screen.queryByText("Connection error")).not.toBeInTheDocument();
    expect(screen.queryByText("Something went wrong")).not.toBeInTheDocument();
  });

  it("Reload button hard-reloads and clears the chunk-retry guards", () => {
    const reload = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, reload },
    });
    window.sessionStorage.setItem("sc:chunk-reload:templates", "1");
    window.sessionStorage.setItem("unrelated", "keep");

    render(
      <ErrorBoundary>
        <ChunkBroken />
      </ErrorBoundary>
    );
    fireEvent.click(screen.getByText("Reload"));

    expect(reload).toHaveBeenCalledTimes(1);
    expect(window.sessionStorage.getItem("sc:chunk-reload:templates")).toBeNull();
    expect(window.sessionStorage.getItem("unrelated")).toBe("keep");
  });

  it("still shows 'Connection error' for a real network failure", () => {
    render(
      <ErrorBoundary>
        <NetworkBroken />
      </ErrorBoundary>
    );
    expect(screen.getByText("Connection error")).toBeInTheDocument();
    expect(
      screen.getByText(/Could not reach the server/)
    ).toBeInTheDocument();
    expect(screen.queryByText("Update available")).not.toBeInTheDocument();
  });

  // Clean up
  afterEach(() => {
    consoleError?.mockRestore();
    window.sessionStorage.clear();
  });
});
