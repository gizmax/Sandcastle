import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ErrorBoundary } from "@/components/shared/ErrorBoundary";

// Component that throws an error
function BrokenComponent(): React.ReactElement {
  throw new Error("Test explosion");
}

function GoodComponent(): React.ReactElement {
  return <div>Everything is fine</div>;
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
    const { container } = render(
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

  // Clean up
  afterEach(() => {
    consoleError?.mockRestore();
  });
});
