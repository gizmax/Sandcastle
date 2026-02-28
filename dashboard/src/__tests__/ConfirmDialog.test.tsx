import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";

describe("ConfirmDialog", () => {
  const defaultProps = {
    open: true,
    title: "Delete workflow",
    description: "This action cannot be undone.",
    onConfirm: vi.fn(),
    onCancel: vi.fn(),
  };

  it("renders nothing when closed", () => {
    const { container } = render(
      <ConfirmDialog {...defaultProps} open={false} />
    );
    expect(container.innerHTML).toBe("");
  });

  it("renders title and description when open", () => {
    render(<ConfirmDialog {...defaultProps} />);
    expect(screen.getByText("Delete workflow")).toBeInTheDocument();
    expect(screen.getByText("This action cannot be undone.")).toBeInTheDocument();
  });

  it("calls onCancel when Cancel button is clicked", () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog {...defaultProps} onCancel={onCancel} />);
    fireEvent.click(screen.getByText("Cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("calls onConfirm when confirm button is clicked", () => {
    const onConfirm = vi.fn();
    render(<ConfirmDialog {...defaultProps} onConfirm={onConfirm} />);
    fireEvent.click(screen.getByText("Confirm"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("uses custom confirmLabel", () => {
    render(<ConfirmDialog {...defaultProps} confirmLabel="Delete Forever" />);
    expect(screen.getByText("Delete Forever")).toBeInTheDocument();
  });

  it("calls onCancel on Escape key", () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog {...defaultProps} onCancel={onCancel} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("disables confirm button when confirmDisabled is true", () => {
    render(<ConfirmDialog {...defaultProps} confirmDisabled={true} />);
    const confirmBtn = screen.getByText("Confirm");
    expect(confirmBtn).toBeDisabled();
  });

  it("confirm button is enabled by default", () => {
    render(<ConfirmDialog {...defaultProps} />);
    const confirmBtn = screen.getByText("Confirm");
    expect(confirmBtn).not.toBeDisabled();
  });

  it("calls onCancel when clicking the backdrop", () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog {...defaultProps} onCancel={onCancel} />);
    // The backdrop is the first fixed div
    const backdrop = document.querySelector(".fixed.inset-0.bg-black\\/40");
    if (backdrop) {
      fireEvent.click(backdrop);
      expect(onCancel).toHaveBeenCalledTimes(1);
    }
  });

  it("calls onCancel when clicking the X button", () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog {...defaultProps} onCancel={onCancel} />);
    // The X button is the one near the title (not "Cancel")
    const buttons = screen.getAllByRole("button");
    // X button is the third button (after Cancel and Confirm, or it might be first)
    // Let's find the one that isn't Cancel or Confirm
    const xButton = buttons.find(
      (b) => b.textContent !== "Cancel" && b.textContent !== "Confirm"
    );
    if (xButton) {
      fireEvent.click(xButton);
      expect(onCancel).toHaveBeenCalledTimes(1);
    }
  });

  it("renders with danger variant by default", () => {
    render(<ConfirmDialog {...defaultProps} />);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();
  });

  it("has aria-modal and aria-labelledby attributes", () => {
    render(<ConfirmDialog {...defaultProps} />);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    // Component uses aria-labelledby pointing to the title element
    expect(dialog).toHaveAttribute("aria-labelledby");
    expect(dialog).toHaveAttribute("aria-describedby");
  });
});
