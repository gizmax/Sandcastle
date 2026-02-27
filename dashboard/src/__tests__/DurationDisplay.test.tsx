import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DurationDisplay } from "@/components/shared/DurationDisplay";

describe("DurationDisplay", () => {
  it("renders seconds", () => {
    render(<DurationDisplay seconds={45} />);
    expect(screen.getByText("45s")).toBeInTheDocument();
  });

  it("renders minutes and seconds", () => {
    render(<DurationDisplay seconds={125} />);
    expect(screen.getByText("2m 5s")).toBeInTheDocument();
  });

  it("renders just minutes when seconds are 0", () => {
    render(<DurationDisplay seconds={120} />);
    expect(screen.getByText("2m")).toBeInTheDocument();
  });

  it("renders 0 seconds", () => {
    render(<DurationDisplay seconds={0} />);
    expect(screen.getByText("0s")).toBeInTheDocument();
  });

  it("applies custom className", () => {
    const { container } = render(
      <DurationDisplay seconds={10} className="text-muted" />
    );
    expect(container.querySelector("span")).toHaveClass("text-muted");
  });
});
