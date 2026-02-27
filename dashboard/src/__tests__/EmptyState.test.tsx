import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { EmptyState } from "@/components/shared/EmptyState";
import { Inbox } from "lucide-react";

describe("EmptyState", () => {
  it("renders title and description", () => {
    render(
      <EmptyState
        title="No workflows yet"
        description="Create your first workflow to get started."
      />
    );
    expect(screen.getByText("No workflows yet")).toBeInTheDocument();
    expect(screen.getByText("Create your first workflow to get started.")).toBeInTheDocument();
  });

  it("renders without icon", () => {
    const { container } = render(
      <EmptyState title="Empty" description="Nothing here" />
    );
    // No icon container should be present
    expect(container.querySelector(".rounded-2xl")).toBeNull();
  });

  it("renders with icon", () => {
    const { container } = render(
      <EmptyState icon={Inbox} title="Empty" description="Nothing here" />
    );
    // Icon container should be present
    expect(container.querySelector(".rounded-2xl")).not.toBeNull();
  });

  it("renders action button when provided", () => {
    const onClick = vi.fn();
    render(
      <EmptyState
        title="No runs"
        description="Start a run"
        action={{ label: "Create Run", onClick }}
      />
    );
    const button = screen.getByText("Create Run");
    expect(button).toBeInTheDocument();
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("does not render action button when not provided", () => {
    render(<EmptyState title="Empty" description="Nothing" />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("applies custom className", () => {
    const { container } = render(
      <EmptyState title="Test" description="Test" className="mt-8" />
    );
    expect(container.firstChild).toHaveClass("mt-8");
  });
});
