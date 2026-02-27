import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

// Mock react-router-dom
const mockNavigate = vi.fn();
vi.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
}));

import { useKeyboardShortcuts } from "@/hooks/useKeyboardShortcuts";

describe("useKeyboardShortcuts", () => {
  afterEach(() => {
    mockNavigate.mockReset();
  });

  function fireKey(key: string, opts: Partial<KeyboardEventInit> = {}) {
    const event = new KeyboardEvent("keydown", {
      key,
      bubbles: true,
      ...opts,
    });
    window.dispatchEvent(event);
  }

  it("navigates to / on Cmd+1", () => {
    renderHook(() => useKeyboardShortcuts());
    act(() => fireKey("1", { metaKey: true }));
    expect(mockNavigate).toHaveBeenCalledWith("/");
  });

  it("navigates to /runs on Cmd+2", () => {
    renderHook(() => useKeyboardShortcuts());
    act(() => fireKey("2", { metaKey: true }));
    expect(mockNavigate).toHaveBeenCalledWith("/runs");
  });

  it("navigates to /workflows on Cmd+3", () => {
    renderHook(() => useKeyboardShortcuts());
    act(() => fireKey("3", { metaKey: true }));
    expect(mockNavigate).toHaveBeenCalledWith("/workflows");
  });

  it("navigates to /schedules on Cmd+4", () => {
    renderHook(() => useKeyboardShortcuts());
    act(() => fireKey("4", { metaKey: true }));
    expect(mockNavigate).toHaveBeenCalledWith("/schedules");
  });

  it("works with Ctrl key too", () => {
    renderHook(() => useKeyboardShortcuts());
    act(() => fireKey("1", { ctrlKey: true }));
    expect(mockNavigate).toHaveBeenCalledWith("/");
  });

  it("does not navigate without meta/ctrl", () => {
    renderHook(() => useKeyboardShortcuts());
    act(() => fireKey("1"));
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it("does not navigate when target is an input", () => {
    renderHook(() => useKeyboardShortcuts());

    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();

    const event = new KeyboardEvent("keydown", {
      key: "1",
      metaKey: true,
      bubbles: true,
    });
    Object.defineProperty(event, "target", { value: input });
    window.dispatchEvent(event);

    expect(mockNavigate).not.toHaveBeenCalled();
    document.body.removeChild(input);
  });

  it("does not navigate when target is a textarea", () => {
    renderHook(() => useKeyboardShortcuts());

    const textarea = document.createElement("textarea");
    document.body.appendChild(textarea);

    const event = new KeyboardEvent("keydown", {
      key: "2",
      metaKey: true,
      bubbles: true,
    });
    Object.defineProperty(event, "target", { value: textarea });
    window.dispatchEvent(event);

    expect(mockNavigate).not.toHaveBeenCalled();
    document.body.removeChild(textarea);
  });

  it("Cmd+K focuses the search input", () => {
    renderHook(() => useKeyboardShortcuts());

    // Create a mock search input matching the selector
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "Search workflows...";
    const header = document.createElement("header");
    header.appendChild(input);
    document.body.appendChild(header);

    const focusSpy = vi.spyOn(input, "focus");

    act(() => fireKey("k", { metaKey: true }));
    expect(focusSpy).toHaveBeenCalled();

    document.body.removeChild(header);
  });

  it("Cmd+K (uppercase K) also focuses search", () => {
    renderHook(() => useKeyboardShortcuts());

    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "Search things";
    const header = document.createElement("header");
    header.appendChild(input);
    document.body.appendChild(header);

    const focusSpy = vi.spyOn(input, "focus");

    act(() => fireKey("K", { metaKey: true }));
    expect(focusSpy).toHaveBeenCalled();

    document.body.removeChild(header);
  });

  it("cleans up listener on unmount", () => {
    const removeSpy = vi.spyOn(window, "removeEventListener");
    const { unmount } = renderHook(() => useKeyboardShortcuts());
    unmount();
    expect(removeSpy).toHaveBeenCalledWith("keydown", expect.any(Function));
    removeSpy.mockRestore();
  });
});
