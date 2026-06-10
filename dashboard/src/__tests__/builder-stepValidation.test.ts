import { describe, it, expect } from "vitest";
import {
  validateStep,
  isStepValid,
  type StepLike,
} from "@/lib/builder/stepValidation";

describe("validateStep — required fields per type", () => {
  it("flags a standard step with no prompt", () => {
    const r = validateStep({ id: "a", stepType: "standard", prompt: "" });
    expect(r.level).toBe("error");
    expect(r.issues[0].field).toBe("prompt");
  });

  it("passes a standard step with a prompt", () => {
    const r = validateStep({ id: "a", stepType: "standard", prompt: "do it" });
    expect(r.level).toBe("ok");
    expect(r.issues).toHaveLength(0);
  });

  it("flags llm with no prompt", () => {
    expect(validateStep({ stepType: "llm" }).level).toBe("error");
  });

  it("flags http with no url", () => {
    const r = validateStep({ stepType: "http", httpConfig: { url: "" } });
    expect(r.level).toBe("error");
    expect(r.issues[0].field).toBe("httpConfig.url");
  });

  it("passes http with a url", () => {
    const r = validateStep({
      stepType: "http",
      httpConfig: { url: "https://x.dev" },
    });
    expect(r.level).toBe("ok");
  });

  it("flags code with no code", () => {
    expect(validateStep({ stepType: "code", codeConfig: { code: "" } }).level).toBe(
      "error",
    );
  });

  it("flags condition with no expression", () => {
    expect(
      validateStep({ stepType: "condition", conditionConfig: { expression: "" } })
        .level,
    ).toBe("error");
  });

  it("flags classify with no categories", () => {
    expect(
      validateStep({ stepType: "classify", classifyConfig: { categories: [] } })
        .level,
    ).toBe("error");
  });

  it("flags loop with nothing to iterate over", () => {
    expect(validateStep({ stepType: "loop", loopConfig: { over: "" } }).level).toBe(
      "error",
    );
  });

  it("flags race with no branches", () => {
    expect(
      validateStep({ stepType: "race", raceConfig: { branches: "" } }).level,
    ).toBe("error");
  });

  it("flags gate with no strategy", () => {
    expect(validateStep({ stepType: "gate", gateConfig: { strategies: [] } }).level).toBe(
      "error",
    );
  });

  it("passes gate with a strategy type", () => {
    const r = validateStep({
      stepType: "gate",
      gateConfig: { strategies: [{ type: "human" }] },
    });
    expect(r.level).toBe("ok");
  });

  it("flags sub_workflow with no target", () => {
    expect(validateStep({ stepType: "sub_workflow" }).level).toBe("error");
  });

  it("passes sub_workflow with a delegateConfig.workflow target", () => {
    const r = validateStep({
      stepType: "sub_workflow",
      delegateConfig: { workflow: "enrich" },
    });
    expect(r.level).toBe("ok");
  });

  it("returns ok for a type with no rules", () => {
    expect(validateStep({ stepType: "parse" }).level).toBe("ok");
  });
});

describe("validateStep — dependsOn cross-checks", () => {
  const all: StepLike[] = [
    { id: "first", stepType: "llm", prompt: "x" },
    { id: "second", stepType: "llm", prompt: "y" },
  ];

  it("flags a dependsOn pointing at a missing step", () => {
    const r = validateStep(
      { id: "second", stepType: "llm", prompt: "y", dependsOn: ["ghost"] },
      all,
    );
    expect(r.level).toBe("error");
    expect(r.issues.some((i) => i.field === "dependsOn")).toBe(true);
  });

  it("passes a dependsOn pointing at an existing step", () => {
    const r = validateStep(
      { id: "second", stepType: "llm", prompt: "y", dependsOn: ["first"] },
      all,
    );
    expect(r.level).toBe("ok");
  });

  it("flags a self-dependency", () => {
    const r = validateStep(
      { id: "first", stepType: "llm", prompt: "x", dependsOn: ["first"] },
      all,
    );
    expect(r.issues.some((i) => i.message.includes("itself"))).toBe(true);
  });

  it("skips cross-checks when allSteps is omitted", () => {
    const r = validateStep({
      id: "x",
      stepType: "llm",
      prompt: "x",
      dependsOn: ["ghost"],
    });
    expect(r.level).toBe("ok");
  });
});

describe("isStepValid", () => {
  it("mirrors validateStep level", () => {
    expect(isStepValid({ stepType: "llm", prompt: "hi" })).toBe(true);
    expect(isStepValid({ stepType: "llm", prompt: "" })).toBe(false);
  });
});
