import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, act, within } from "@testing-library/react";
import { ReactFlowProvider } from "@xyflow/react";
import { StepNode } from "@/components/workflows/StepNode";
import { getStepMeta } from "@/lib/builder/stepMetadata";

/**
 * StepNode renders an xyflow node, so its `Handle`s need ReactFlow context.
 * We render the memoized component directly with a minimal NodeProps-ish shape;
 * only the `data` and `selected` props are read by StepNode.
 */
afterEach(() => {
  vi.useRealTimers();
});

/** Hover the trigger and flush the HoverCard's open delay timer. */
function openOnHover(trigger: HTMLElement) {
  vi.useFakeTimers();
  fireEvent.mouseEnter(trigger);
  act(() => {
    vi.runAllTimers();
  });
  vi.useRealTimers();
}

function renderNode(data: Record<string, unknown>, selected = false) {
  // The component only consumes `data` and `selected`; cast through unknown to
  // satisfy the full NodeProps type without constructing every xyflow field.
  const props = { data, selected } as unknown as Parameters<typeof StepNode>[0];
  return render(
    <ReactFlowProvider>
      <StepNode {...props} />
    </ReactFlowProvider>,
  );
}

describe("StepNode hover mini-card", () => {
  it("exposes the step type metadata summary on hover", () => {
    renderNode({ label: "summarize", stepType: "llm", model: "sonnet" });

    // HoverCard opens on hover of the focusable trigger wrapping the node.
    const node = screen.getByTestId("step-node");
    // The trigger is the HoverCard span wrapping the node div.
    const trigger = node.parentElement as HTMLElement;
    openOnHover(trigger);

    const card = screen.getByRole("tooltip");
    expect(card).toHaveTextContent(getStepMeta("llm").summary);
    // Title combines the step id and the human label.
    expect(card).toHaveTextContent("summarize");
    expect(card).toHaveTextContent(getStepMeta("llm").label);
  });

  it("opens the hover card on keyboard focus (a11y)", () => {
    renderNode({ label: "fetch", stepType: "http" });

    const node = screen.getByTestId("step-node");
    const trigger = node.parentElement as HTMLElement;
    fireEvent.focus(trigger);

    expect(screen.getByRole("tooltip")).toHaveTextContent(
      getStepMeta("http").summary,
    );
    // The trigger advertises the open card via aria-describedby.
    expect(trigger).toHaveAttribute("aria-describedby");
  });
});

describe("StepNode validation badge", () => {
  it("shows a validation badge with the issue when a required field is missing", () => {
    // A browser step is the type whose required field (startUrl) the node
    // `data` actually carries — missing it is a real, node-visible problem.
    renderNode({ label: "login", stepType: "browser", browserUrl: "" });

    const badge = screen.getByRole("img", { name: /error/i });
    expect(badge).toBeInTheDocument();

    // Hovering the badge reveals the issue message + fix hint.
    const badgeTrigger = badge.parentElement as HTMLElement;
    openOnHover(badgeTrigger);

    const cards = screen.getAllByRole("tooltip");
    const issueCard = cards.find((c) =>
      /start url/i.test(c.textContent ?? ""),
    );
    expect(issueCard).toBeTruthy();
    expect(issueCard).toHaveTextContent(/Set the page the browser should open/i);
  });

  it("shows no validation badge for a valid node", () => {
    renderNode({
      label: "login",
      stepType: "browser",
      browserUrl: "https://example.com",
    });

    expect(screen.queryByRole("img", { name: /error|warning/i })).toBeNull();
  });

  it("does not false-positive on types whose required field the node cannot see", () => {
    // An LLM step's required field is `prompt`, which is NOT carried on the
    // node `data` — so the canvas must not flag it as missing.
    renderNode({ label: "summarize", stepType: "llm", model: "sonnet" });

    expect(screen.queryByRole("img", { name: /error|warning/i })).toBeNull();
  });

  it("renders the humanized line for the node in the hover card", () => {
    renderNode({
      label: "login",
      stepType: "browser",
      browserUrl: "https://example.com",
    });

    const node = screen.getByTestId("step-node");
    const trigger = node.parentElement as HTMLElement;
    openOnHover(trigger);

    const card = screen.getByRole("tooltip");
    expect(
      within(card).getByText(/Automates a browser starting at/i),
    ).toBeInTheDocument();
  });
});
