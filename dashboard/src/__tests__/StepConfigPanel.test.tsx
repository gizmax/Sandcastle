import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import {
  StepConfigPanel,
  type StepConfig,
  type StepType,
} from "@/components/workflows/StepConfigPanel";

/**
 * Minimal-but-complete StepConfig factory so each test can override just the
 * fields it cares about. Mirrors the defaults the builder seeds new steps with.
 */
function makeStep(overrides: Partial<StepConfig> = {}): StepConfig {
  const base: StepConfig = {
    id: "step-1",
    stepType: "standard" as StepType,
    prompt: "",
    model: "sonnet",
    maxTurns: 10,
    timeout: 300,
    parallelOver: "",
    dependsOn: [],
    tools: [],
    directoryInput: { enabled: false, defaultPath: "" },
    csvOutput: { enabled: false, directory: "", mode: "new_file", filename: "" },
    pdfReport: { enabled: false, directory: "", language: "en", filename: "" },
    autopilot: {
      enabled: false,
      optimizeFor: "quality",
      evaluation: "llm_judge",
      sampleRate: 1,
      minSamples: 1,
      qualityThreshold: 0.8,
      autoDeploy: false,
      variants: [],
    },
    retry: {
      enabled: false,
      maxAttempts: 3,
      backoff: "exponential",
      onFailure: "abort",
    },
    approval: {
      enabled: false,
      message: "",
      timeoutHours: 24,
      onTimeout: "abort",
      allowEdit: false,
    },
    policies: [],
    slo: {
      enabled: false,
      qualityMin: 0.7,
      costMaxUsd: 1,
      latencyMaxSeconds: 60,
      optimizeFor: "balanced",
    },
    llmSystemPrompt: "",
    httpConfig: { url: "", method: "GET", headers: {}, body: "", auth: "" },
    codeConfig: { code: "", language: "python" },
    conditionConfig: { expression: "", thenSteps: [], elseSteps: [] },
    classifyConfig: { categories: [], input: "", model: "haiku", branches: {} },
    loopConfig: { over: "", stepIds: [], maxIterations: 100 },
    raceConfig: { branches: "", validator: "" },
    sensorConfig: {
      url: "",
      method: "GET",
      headers: "",
      checkInterval: 10,
      timeout: 300,
      condition: "",
    },
    gateConfig: { strategies: [] },
    transformConfig: { template: "" },
    notifyConfig: { service: "", channel: "", message: "" },
    delegateConfig: { workflow: "", taskDescription: "", timeout: 600 },
    browserConfig: {
      mode: "playwright",
      startUrl: "",
      viewportWidth: 1280,
      viewportHeight: 720,
      timeout: 120,
      waitAfterAction: 0.5,
      headless: true,
      credentials_env: "",
      screenshotOnError: true,
      max_actions: 100,
      capture_screenshots: false,
      output_schema: null,
      captcha_strategy: "pause",
    },
  };
  return { ...base, ...overrides };
}

function renderPanel(step: StepConfig, allStepIds: string[] = [step.id]) {
  const onChange = vi.fn();
  const onDelete = vi.fn();
  const utils = render(
    <StepConfigPanel
      step={step}
      allStepIds={allStepIds}
      onChange={onChange}
      onDelete={onDelete}
    />,
  );
  return { onChange, onDelete, ...utils };
}

describe("StepConfigPanel — plain-English summary (④)", () => {
  it("renders a humanized summary that reflects the configured step", () => {
    renderPanel(
      makeStep({ id: "summary", stepType: "llm", prompt: "summarize {input.text}" }),
    );
    // humanizeStep(llm + prompt) → 'Sends the prompt "…" to the model and saves the result as "summary".'
    expect(
      screen.getByText(/Sends the prompt "summarize \{input\.text\}" to the model/),
    ).toBeInTheDocument();
    expect(screen.getByText(/saves the result as "summary"/)).toBeInTheDocument();
  });

  it("updates the summary for a different step type", () => {
    renderPanel(makeStep({ id: "poll", stepType: "sensor", sensorConfig: {
      url: "https://api/status",
      method: "GET",
      headers: "",
      checkInterval: 10,
      timeout: 300,
      condition: "response.get('done')",
    } }));
    expect(
      screen.getByText(/Polls https:\/\/api\/status until its condition is met/),
    ).toBeInTheDocument();
  });
});

describe("StepConfigPanel — inline validation (③)", () => {
  it("renders a validation issue for a step missing a required field", () => {
    renderPanel(makeStep({ stepType: "standard", prompt: "" }));
    const list = screen.getByRole("list", { name: /validation issues/i });
    expect(within(list).getByText(/Agent step has no prompt/)).toBeInTheDocument();
    // The actionable hint is shown alongside the message.
    expect(within(list).getByText(/Tell the agent what to do/)).toBeInTheDocument();
  });

  it("shows no validation list when the step is valid", () => {
    renderPanel(makeStep({ stepType: "standard", prompt: "do the thing" }));
    expect(
      screen.queryByRole("list", { name: /validation issues/i }),
    ).not.toBeInTheDocument();
  });

  it("flags a dependsOn that points at a non-existent step", () => {
    renderPanel(
      makeStep({ id: "a", stepType: "standard", prompt: "go", dependsOn: ["ghost"] }),
      ["a"],
    );
    const list = screen.getByRole("list", { name: /validation issues/i });
    expect(within(list).getByText(/which is not a step in this workflow/)).toBeInTheDocument();
  });
});

describe("StepConfigPanel — field-level help (②)", () => {
  it("exposes help content for the race Validator Expression field", () => {
    renderPanel(makeStep({ stepType: "race" }));
    // The help affordance is keyboard-focusable and accessibly labelled.
    const help = screen.getByLabelText("Validator Expression help");
    expect(help).toBeInTheDocument();
    // Focusing the HoverCard trigger reveals the card with the example.
    const trigger = help.closest("span[tabindex]");
    expect(trigger).not.toBeNull();
    fireEvent.focus(trigger as HTMLElement);
    expect(screen.getByText(/the finished branch's output value/)).toBeInTheDocument();
    expect(screen.getByText(/len\(output\) > 0/)).toBeInTheDocument();
  });

  it("exposes help for the condition Expression field", () => {
    renderPanel(makeStep({ stepType: "condition" }));
    const help = screen.getByLabelText("Expression help");
    const trigger = help.closest("span[tabindex]");
    fireEvent.focus(trigger as HTMLElement);
    expect(
      screen.getByText(/must evaluate to true or false/),
    ).toBeInTheDocument();
  });
});
