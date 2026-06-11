import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { TryModelModal } from "@/components/runs/TryModelModal";

afterEach(() => cleanup());

const steps = [
  { step_id: "draft", model: "sonnet" },
  { step_id: "review", model: "haiku" },
];

describe("TryModelModal", () => {
  it("does not render when closed", () => {
    render(
      <TryModelModal open={false} onClose={vi.fn()} runId="abc12345" steps={steps} onSubmit={vi.fn()} />
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens the model picker and a step picker for multi-step runs", () => {
    render(
      <TryModelModal open onClose={vi.fn()} runId="abc12345" steps={steps} onSubmit={vi.fn()} />
    );
    expect(screen.getByRole("dialog", { name: "Try another model" })).toBeInTheDocument();
    // Model picker present with grouped provider options.
    const modelSelect = screen.getByLabelText("New model") as HTMLSelectElement;
    expect(modelSelect).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Opus" })).toBeInTheDocument();
    // Step picker present because there are 2 steps.
    expect(screen.getByLabelText("Swap model from step")).toBeInTheDocument();
  });

  it("shows a cost hint from the original run cost", () => {
    render(
      <TryModelModal open onClose={vi.fn()} runId="abc12345" steps={steps} originalCostUsd={0.42} onSubmit={vi.fn()} />
    );
    expect(screen.getByText(/new paid run/i)).toBeInTheDocument();
    expect(screen.getByText(/\$0\.42/)).toBeInTheDocument();
  });

  it("submits from_step + chosen model when a model is selected", () => {
    const onSubmit = vi.fn();
    render(
      <TryModelModal open onClose={vi.fn()} runId="abc12345" steps={steps} onSubmit={onSubmit} />
    );
    fireEvent.change(screen.getByLabelText("New model"), { target: { value: "opus" } });
    fireEvent.click(screen.getByRole("button", { name: /Fork & run/i }));
    expect(onSubmit).toHaveBeenCalledWith({ from_step: "draft", model: "opus" });
  });

  it("keeps the submit disabled until a model is chosen", () => {
    const onSubmit = vi.fn();
    render(
      <TryModelModal open onClose={vi.fn()} runId="abc12345" steps={steps} onSubmit={onSubmit} />
    );
    fireEvent.click(screen.getByRole("button", { name: /Fork & run/i }));
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
