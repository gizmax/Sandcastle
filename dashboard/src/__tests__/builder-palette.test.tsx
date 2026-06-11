/**
 * builder-palette.test.tsx — Wave 2 palette learnability.
 *
 * Covers the three new behaviours on the WorkflowBuilder left palette:
 *   1. Each palette item renders a hover trigger carrying the right summary.
 *   2. "Learn mode" toggle shows/hides the inline one-line summaries and
 *      persists the choice to localStorage.
 *   3. "Common next steps" suggestions appear for a selected step type.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";

import { PaletteItem } from "@/components/workflows/PaletteItem";
import { getNextStepSuggestions } from "@/components/workflows/nextStepSuggestions";
import { getStepMeta, getAgentTemplateMeta } from "@/lib/builder/stepMetadata";
import { Zap } from "lucide-react";

describe("PaletteItem hover + learn mode", () => {
  it("renders an accessible add button describing what the step does", () => {
    const meta = getStepMeta("race");
    render(
      <PaletteItem type="race" icon={Zap} label="Race" onAdd={() => {}} />,
    );
    // The button's aria-label carries the plain-English summary so users
    // understand "Race" before clicking it.
    const btn = screen.getByRole("button", { name: /Add Race step/i });
    expect(btn).toHaveAttribute("aria-label", expect.stringContaining(meta.summary));
    expect(btn).toHaveAttribute("data-palette-item", "race");
  });

  it("opens a hover card with summary, when-to-use and example on focus", () => {
    const meta = getStepMeta("gate");
    render(
      <PaletteItem type="gate" icon={Zap} label="Gate" onAdd={() => {}} />,
    );
    const trigger = screen.getByRole("button", { name: /Add Gate step/i })
      .parentElement as HTMLElement;
    fireEvent.focus(trigger);
    // role="tooltip" card is portalled into the body.
    const card = screen.getByRole("tooltip");
    expect(within(card).getByText("Gate")).toBeInTheDocument();
    expect(card).toHaveTextContent(meta.summary);
    expect(card).toHaveTextContent(/Use when:/);
    expect(card).toHaveTextContent(/e\.g\./);
  });

  it("uses agent-template copy for the 15 agent personas", () => {
    const meta = getAgentTemplateMeta("researcher");
    render(
      <PaletteItem
        type="agent"
        template="researcher"
        icon={Zap}
        label="Researcher"
        onAdd={() => {}}
      />,
    );
    const trigger = screen.getByRole("button", { name: /Add Researcher step/i })
      .parentElement as HTMLElement;
    fireEvent.focus(trigger);
    expect(screen.getByRole("tooltip")).toHaveTextContent(meta.summary);
  });

  it("only shows the inline summary when learn mode is on", () => {
    const meta = getStepMeta("sensor");
    const { rerender } = render(
      <PaletteItem
        type="sensor"
        icon={Zap}
        label="Sensor"
        showInlineSummary={false}
        onAdd={() => {}}
      />,
    );
    const btn = screen.getByRole("button", { name: /Add Sensor step/i });
    // Off: the visible label text does not include the summary body.
    expect(btn).not.toHaveTextContent(meta.summary);

    rerender(
      <PaletteItem
        type="sensor"
        icon={Zap}
        label="Sensor"
        showInlineSummary
        onAdd={() => {}}
      />,
    );
    // On: summary now rendered inline (always visible, not just on hover).
    expect(
      screen.getByRole("button", { name: /Add Sensor step/i }),
    ).toHaveTextContent(meta.summary);
  });

  it("fires onAdd when clicked", () => {
    let clicked = false;
    render(
      <PaletteItem
        type="llm"
        icon={Zap}
        label="LLM"
        onAdd={() => {
          clicked = true;
        }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Add LLM step/i }));
    expect(clicked).toBe(true);
  });
});

describe("getNextStepSuggestions", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("suggests parse/transform after an http step", () => {
    const s = getNextStepSuggestions("http");
    expect(s).toContain("parse");
    expect(s).toContain("transform");
  });

  it("suggests transform/notify/condition after a standard agent step", () => {
    expect(getNextStepSuggestions("standard")).toEqual([
      "transform",
      "notify",
      "condition",
    ]);
  });

  it("suggests parse after a browser step", () => {
    expect(getNextStepSuggestions("browser")).toContain("parse");
  });

  it("never returns more than 3 suggestions", () => {
    for (const t of ["standard", "http", "code", "unknown_type"]) {
      expect(getNextStepSuggestions(t).length).toBeLessThanOrEqual(3);
    }
  });

  it("falls back to a sensible default for unknown types", () => {
    expect(getNextStepSuggestions("totally-made-up")).toEqual([
      "transform",
      "notify",
    ]);
  });
});
