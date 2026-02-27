import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useToasts } from "@/hooks/useToasts";

describe("useToasts", () => {
  it("starts with empty toasts", () => {
    const { result } = renderHook(() => useToasts());
    expect(result.current.toasts).toHaveLength(0);
  });

  it("adds a toast", () => {
    const { result } = renderHook(() => useToasts());
    act(() => {
      result.current.addToast("success", "Workflow started");
    });
    expect(result.current.toasts).toHaveLength(1);
    expect(result.current.toasts[0].message).toBe("Workflow started");
    expect(result.current.toasts[0].type).toBe("success");
  });

  it("adds multiple toasts", () => {
    const { result } = renderHook(() => useToasts());
    act(() => {
      result.current.addToast("success", "First");
      result.current.addToast("error", "Second");
      result.current.addToast("warning", "Third");
    });
    expect(result.current.toasts).toHaveLength(3);
  });

  it("caps at 5 visible toasts (keeps newest)", () => {
    const { result } = renderHook(() => useToasts());
    act(() => {
      for (let i = 0; i < 7; i++) {
        result.current.addToast("warning", `Toast ${i}`);
      }
    });
    expect(result.current.toasts).toHaveLength(5);
    // Should keep the last 5 (Toast 2 through Toast 6)
    expect(result.current.toasts[0].message).toBe("Toast 2");
    expect(result.current.toasts[4].message).toBe("Toast 6");
  });

  it("dismisses a toast by id", () => {
    const { result } = renderHook(() => useToasts());
    act(() => {
      result.current.addToast("warning", "Dismissable");
    });
    const id = result.current.toasts[0].id;
    act(() => {
      result.current.dismissToast(id);
    });
    expect(result.current.toasts).toHaveLength(0);
  });

  it("dismissing non-existent id does nothing", () => {
    const { result } = renderHook(() => useToasts());
    act(() => {
      result.current.addToast("warning", "Keep me");
    });
    act(() => {
      result.current.dismissToast("nonexistent-id");
    });
    expect(result.current.toasts).toHaveLength(1);
  });

  it("assigns unique IDs to each toast", () => {
    const { result } = renderHook(() => useToasts());
    act(() => {
      result.current.addToast("warning", "A");
      result.current.addToast("warning", "B");
    });
    const ids = result.current.toasts.map((t) => t.id);
    expect(new Set(ids).size).toBe(2);
  });
});
