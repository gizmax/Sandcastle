import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { CostDisplay } from "@/components/shared/CostDisplay";

describe("CostDisplay", () => {
  it("renders formatted cost", () => {
    render(<CostDisplay cost={1.84} />);
    expect(screen.getByText("$1.84")).toBeInTheDocument();
  });

  it("renders zero cost", () => {
    render(<CostDisplay cost={0} />);
    expect(screen.getByText("$0.00")).toBeInTheDocument();
  });

  it("applies custom className", () => {
    const { container } = render(
      <CostDisplay cost={5} className="text-green-500" />
    );
    expect(container.querySelector("span")).toHaveClass("text-green-500");
  });
});
