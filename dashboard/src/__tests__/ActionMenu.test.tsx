import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { ActionMenu, type ActionMenuItem } from "@/components/shared/ActionMenu";

afterEach(() => cleanup());

function items(onSelect: () => void): ActionMenuItem[] {
  return [
    { id: "rerun", label: "Re-run", onSelect },
    { id: "compare", label: "Compare with another run...", onSelect },
    { id: "delete", label: "Delete", danger: true, onSelect },
  ];
}

describe("ActionMenu", () => {
  it("renders the prominent primary action as a button", () => {
    const onSelect = vi.fn();
    render(
      <ActionMenu
        primary={{ id: "p", label: "Replay failed step", onSelect }}
        items={[]}
      />
    );
    const btn = screen.getByRole("button", { name: "Replay failed step" });
    expect(btn).toBeInTheDocument();
    fireEvent.click(btn);
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it("opens the overflow menu and lists secondary actions", () => {
    const onSelect = vi.fn();
    render(<ActionMenu items={items(onSelect)} menuLabel="Run actions" />);
    // Menu is closed initially.
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Run actions" }));
    const menu = screen.getByRole("menu", { name: "Run actions" });
    expect(menu).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Re-run/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Compare/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Delete/ })).toBeInTheDocument();
  });

  it("fires the selected action and closes the menu", () => {
    const onSelect = vi.fn();
    render(<ActionMenu items={items(onSelect)} menuLabel="Run actions" />);
    fireEvent.click(screen.getByRole("button", { name: "Run actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Re-run/ }));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("does not render an overflow trigger when there are no items", () => {
    render(<ActionMenu primary={{ id: "p", label: "Run", onSelect: vi.fn() }} items={[]} />);
    expect(screen.queryByRole("button", { name: /More actions/ })).not.toBeInTheDocument();
  });

  it("exposes aria-haspopup/expanded on the trigger for accessibility", () => {
    render(<ActionMenu items={items(vi.fn())} />);
    const trigger = screen.getByRole("button", { name: "More actions" });
    expect(trigger).toHaveAttribute("aria-haspopup", "menu");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
  });
});
