import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { RunRowActions } from "@/components/runs/RunRowActions";

vi.mock("@/api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

afterEach(() => cleanup());

function renderRow(status: string) {
  return render(
    <MemoryRouter>
      <RunRowActions runId="run-abcdef01" status={status} />
    </MemoryRouter>
  );
}

describe("RunRowActions", () => {
  it("offers Replay failed step + Re-run for a failed run", () => {
    renderRow("failed");
    fireEvent.click(screen.getByRole("button", { name: /Actions for run/ }));
    expect(screen.getByRole("menuitem", { name: /Replay failed step/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /^Re-run$/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Compare/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Delete/ })).toBeInTheDocument();
  });

  it("omits Replay failed step for a completed run", () => {
    renderRow("completed");
    fireEvent.click(screen.getByRole("button", { name: /Actions for run/ }));
    expect(screen.queryByRole("menuitem", { name: /Replay failed step/ })).not.toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /^Re-run$/ })).toBeInTheDocument();
  });

  it("only allows Compare for an in-flight run (no re-run/replay/delete)", () => {
    renderRow("running");
    fireEvent.click(screen.getByRole("button", { name: /Actions for run/ }));
    expect(screen.getByRole("menuitem", { name: /Compare/ })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /^Re-run$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /Replay failed step/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /Delete/ })).not.toBeInTheDocument();
  });
});
