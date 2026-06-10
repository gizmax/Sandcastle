import { describe, it, expect } from "vitest";
import { humanizeStep } from "@/lib/builder/humanizeStep";

describe("humanizeStep", () => {
  it("describes a standard agent step with prompt and id", () => {
    const s = humanizeStep({
      id: "summary",
      stepType: "standard",
      prompt: "summarize {input.text}",
    });
    expect(s).toBe(
      'Asks the agent to "summarize {input.text}" and saves the result as "summary".',
    );
  });

  it("describes an llm step", () => {
    const s = humanizeStep({
      id: "draft",
      stepType: "llm",
      prompt: "write a haiku",
    });
    expect(s).toBe(
      'Sends the prompt "write a haiku" to the model and saves the result as "draft".',
    );
  });

  it("describes an http step with url", () => {
    expect(
      humanizeStep({
        id: "post",
        stepType: "http",
        httpConfig: { url: "https://api.dev/hook" },
      }),
    ).toBe('Calls https://api.dev/hook and saves the result as "post".');
  });

  it("describes a condition step", () => {
    expect(
      humanizeStep({
        stepType: "condition",
        conditionConfig: { expression: "{x} > 1" },
      }),
    ).toBe("Branches on whether {x} > 1.");
  });

  it("describes a classify step with categories", () => {
    expect(
      humanizeStep({
        stepType: "classify",
        classifyConfig: { categories: ["spam", "ham"] },
      }),
    ).toContain('"spam", "ham"');
  });

  it("describes a loop step", () => {
    expect(
      humanizeStep({ stepType: "loop", loopConfig: { over: "{input.items}" } }),
    ).toContain("Loops over {input.items}");
  });

  it("handles a step with no prompt gracefully", () => {
    expect(humanizeStep({ id: "x", stepType: "standard" })).toBe(
      'Runs an agent step and saves the result as "x".',
    );
  });

  it("falls back for unknown types", () => {
    expect(humanizeStep({ id: "weird", stepType: "quantum" })).toBe(
      'Runs a quantum step called "weird".',
    );
  });

  it("falls back for empty step", () => {
    expect(humanizeStep({})).toContain("Runs an agent step");
  });

  it("collapses and truncates long prompts", () => {
    const long = "x".repeat(300);
    const s = humanizeStep({ stepType: "llm", prompt: long });
    expect(s.length).toBeLessThan(200);
    expect(s).toContain("…");
  });
});
