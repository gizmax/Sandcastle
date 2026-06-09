/**
 * Command palette tests: fuzzy page matching, grouped results, keyboard
 * navigation, theme toggle action, debounced API search and API-down behavior.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// jsdom does not implement scrollIntoView (used to keep the active item visible)
Element.prototype.scrollIntoView = vi.fn();

const mockNavigate = vi.fn();

vi.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
}));

vi.mock("@/api/client", () => ({
  api: {
    get: vi.fn(),
  },
}));

import { api } from "@/api/client";
import { CommandPalette } from "@/components/layout/CommandPalette";
import { fuzzyScore } from "@/lib/fuzzy";
import { THEME_TOGGLE_EVENT } from "@/hooks/useTheme";

const mockedGet = vi.mocked(api.get);

function renderPalette(props: Partial<React.ComponentProps<typeof CommandPalette>> = {}) {
  return render(
    <CommandPalette open onClose={vi.fn()} recentItems={[]} {...props} />
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedGet.mockResolvedValue({ data: [], error: null });
});

describe("fuzzyScore", () => {
  it("scores substrings highest", () => {
    expect(fuzzyScore("temp", "Templates")).toBeGreaterThan(2);
  });

  it("matches subsequences in order", () => {
    expect(fuzzyScore("wfb", "Workflow Builder")).toBeGreaterThan(0);
    expect(fuzzyScore("schmon", "Schedule Monitor")).toBeGreaterThan(0);
  });

  it("rejects out-of-order or missing characters", () => {
    expect(fuzzyScore("xyz", "Templates")).toBe(0);
    expect(fuzzyScore("setalpmet", "Templates")).toBe(0);
  });
});

describe("CommandPalette", () => {
  it("shows all pages grouped under Pages when empty", () => {
    renderPalette();
    expect(screen.getByText("Pages")).toBeInTheDocument();
    expect(screen.getByText("Templates")).toBeInTheDocument();
    expect(screen.getByText("Memory")).toBeInTheDocument();
    expect(screen.getByText("Compliance")).toBeInTheDocument();
  });

  it("fuzzy matches pages by name", async () => {
    renderPalette();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "tmplts" } });
    await waitFor(() => {
      expect(screen.getByText("Templates")).toBeInTheDocument();
    });
    expect(screen.queryByText("Memory")).not.toBeInTheDocument();
  });

  it("navigates to the active item on Enter", () => {
    const onClose = vi.fn();
    renderPalette({ onClose });
    const input = screen.getByRole("combobox");
    fireEvent.change(input, { target: { value: "templates" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(mockNavigate).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("moves the selection with arrow keys", () => {
    renderPalette();
    const input = screen.getByRole("combobox");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    const active = document.getElementById("cmd-item-1");
    expect(active).toHaveAttribute("aria-selected", "true");
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    renderPalette({ onClose });
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("dispatches the theme toggle event from the /theme action", () => {
    const onClose = vi.fn();
    const listener = vi.fn();
    window.addEventListener(THEME_TOGGLE_EVENT, listener);
    renderPalette({ onClose });
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "/theme" } });
    fireEvent.click(screen.getByText("/theme"));
    expect(listener).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalled();
    expect(mockNavigate).not.toHaveBeenCalled();
    window.removeEventListener(THEME_TOGGLE_EVENT, listener);
  });

  it("searches runs and workflows via the API, with a Run entry per workflow", async () => {
    mockedGet.mockImplementation((path: string) => {
      if (path === "/runs") {
        return Promise.resolve({
          data: [
            { run_id: "abc12345-0000", workflow_name: "daily-digest", status: "completed" },
          ],
          error: null,
        });
      }
      return Promise.resolve({
        data: [{ name: "daily-digest", file_name: "daily_digest.yaml", steps_count: 3 }],
        error: null,
      });
    });

    renderPalette();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "daily" } });

    await waitFor(() => {
      expect(screen.getByText("Run daily-digest")).toBeInTheDocument();
    });
    expect(screen.getByText("Runs")).toBeInTheDocument();
    expect(screen.getByText("Workflows")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Run daily-digest"));
    expect(mockNavigate).toHaveBeenCalledWith("/workflows?run=daily_digest");
  });

  it("still shows page matches when the API is down", async () => {
    mockedGet.mockRejectedValue(new Error("connection refused"));
    renderPalette();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "settings" } });
    await waitFor(() => {
      expect(screen.getByText("Settings")).toBeInTheDocument();
    });
  });

  it("traps focus inside the dialog on Tab", () => {
    renderPalette();
    const dialog = screen.getByRole("dialog");
    const input = screen.getByRole("combobox");
    input.focus();
    // Shift+Tab from the first focusable wraps to the last
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(document.activeElement).not.toBe(document.body);
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it("renders nothing when closed", () => {
    render(<CommandPalette open={false} onClose={vi.fn()} recentItems={[]} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
