/**
 * builder-palette-integration.test.tsx — Wave 2 palette learnability, wired
 * through the real WorkflowBuilder chrome.
 *
 * Verifies, against the actual component:
 *   - The Learn toggle in the palette header flips inline summaries on/off and
 *     persists the choice to localStorage["sandcastle-builder-learn"].
 *   - "Common next steps" suggestions render once a step is selected and the
 *     suggestion buttons match the step type.
 */
import React from "react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";

// ReactFlow is heavy/canvas-driven — stub it and expose onNodeClick so the test
// can select a node and exercise the suggestions bar.
let capturedOnNodeClick:
  | ((e: unknown, node: { id: string }) => void)
  | undefined;
vi.mock("@xyflow/react", () => ({
  ReactFlow: ({
    children,
    onNodeClick,
  }: {
    children?: React.ReactNode;
    onNodeClick?: (e: unknown, node: { id: string }) => void;
  }) => {
    capturedOnNodeClick = onNodeClick;
    return <div data-testid="react-flow">{children}</div>;
  },
  Background: () => <div />,
  Controls: () => <div />,
  addEdge: vi.fn((_e, eds) => eds),
  useNodesState: () => {
    const [n, setN] = React.useState<unknown[]>([]);
    return [n, setN, vi.fn()];
  },
  useEdgesState: () => {
    const [e, setE] = React.useState<unknown[]>([]);
    return [e, setE, vi.fn()];
  },
}));

vi.mock("@/components/workflows/StepNode", () => ({
  StepNode: () => <div data-testid="step-node" />,
}));
vi.mock("@/components/workflows/StepConfigPanel", async () => {
  const actual = await vi.importActual<
    typeof import("@/components/workflows/StepConfigPanel")
  >("@/components/workflows/StepConfigPanel");
  return {
    ...actual,
    StepConfigPanel: () => <div data-testid="step-config-panel" />,
  };
});
vi.mock("@/components/workflows/YamlPreview", () => ({
  YamlPreview: () => <div data-testid="yaml-preview" />,
}));
vi.mock("@/components/workflows/TemplateBrowser", () => ({
  TemplateBrowser: () => <div data-testid="template-browser" />,
}));
vi.mock("@/components/workflows/GenerateChatPanel", () => ({
  GenerateChatPanel: () => <div data-testid="generate-chat-panel" />,
}));
vi.mock("@/components/workflows/ToolSelector", () => ({
  ToolSelector: () => <div data-testid="tool-selector" />,
}));

import { WorkflowBuilder } from "@/components/workflows/WorkflowBuilder";
import { getStepMeta } from "@/lib/builder/stepMetadata";

const LEARN_KEY = "sandcastle-builder-learn";

describe("WorkflowBuilder palette — Learn mode toggle", () => {
  beforeEach(() => {
    window.localStorage.clear();
    capturedOnNodeClick = undefined;
  });

  it("defaults Learn mode ON for a first-timer (no existing workflow) and shows inline summaries", () => {
    render(<WorkflowBuilder />);
    const httpBtn = screen.getByRole("button", { name: /Add HTTP step/i });
    expect(httpBtn).toHaveTextContent(getStepMeta("http").summary);
  });

  it("toggling Learn off hides inline summaries and persists the choice", () => {
    render(<WorkflowBuilder />);
    const toggle = screen.getByRole("switch", { name: /Learn/i });
    expect(toggle).toHaveAttribute("aria-checked", "true");

    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(
      screen.getByRole("button", { name: /Add HTTP step/i }),
    ).not.toHaveTextContent(getStepMeta("http").summary);
    expect(window.localStorage.getItem(LEARN_KEY)).toBe("false");
  });

  it("restores the persisted Learn preference on mount", () => {
    window.localStorage.setItem(LEARN_KEY, "false");
    render(<WorkflowBuilder />);
    expect(screen.getByRole("switch", { name: /Learn/i })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });
});

describe("WorkflowBuilder — common next steps suggestions", () => {
  beforeEach(() => {
    window.localStorage.clear();
    capturedOnNodeClick = undefined;
  });

  it("shows an 'Add next:' bar with relevant suggestions once a step is selected", () => {
    render(<WorkflowBuilder />);

    // Add an HTTP step, then select it via the stubbed canvas node click.
    fireEvent.click(screen.getByRole("button", { name: /Add HTTP step/i }));
    expect(capturedOnNodeClick).toBeTypeOf("function");
    // The new HTTP step id is "http_1".
    capturedOnNodeClick!(null, { id: "http_1" });

    const bar = screen.getByRole("group", { name: /Suggested next steps/i });
    expect(within(bar).getByText(/Add next:/i)).toBeInTheDocument();
    // http -> parse / transform / code
    expect(
      within(bar).getByRole("button", { name: /Add Parse step/i }),
    ).toBeInTheDocument();
    expect(
      within(bar).getByRole("button", { name: /Add Transform step/i }),
    ).toBeInTheDocument();
  });

  it("dismissing the suggestions bar hides it", () => {
    render(<WorkflowBuilder />);
    fireEvent.click(screen.getByRole("button", { name: /Add HTTP step/i }));
    capturedOnNodeClick!(null, { id: "http_1" });

    fireEvent.click(
      screen.getByRole("button", { name: /Dismiss suggestions/i }),
    );
    expect(
      screen.queryByRole("group", { name: /Suggested next steps/i }),
    ).not.toBeInTheDocument();
  });
});
