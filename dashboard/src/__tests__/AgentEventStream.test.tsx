import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/api/client", () => ({
  api: {
    authHeaders: vi.fn(() => ({ "X-API-Key": "test" })),
    setApiKey: vi.fn(),
    isMockMode: false,
    onMockChange: vi.fn(() => () => {}),
  },
}));

vi.mock("@/lib/constants", () => ({
  API_BASE_URL: "http://localhost:8080/api",
  POLL_INTERVAL: 5000,
}));

import { AgentEventStream } from "@/components/agents/AgentEventStream";

interface EnqueueController {
  enqueue: (chunk: string) => void;
  close: () => void;
}

function makeSseStream(): { response: Response; controller: EnqueueController } {
  const encoder = new TextEncoder();
  let streamController: ReadableStreamDefaultController<Uint8Array> | null = null;
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      streamController = c;
    },
    cancel() {},
  });
  const controller: EnqueueController = {
    enqueue: (chunk: string) => {
      streamController?.enqueue(encoder.encode(chunk));
    },
    close: () => {
      try {
        streamController?.close();
      } catch {
        /* already closed */
      }
    },
  };
  const response = new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
  return { response, controller };
}

function sseLine(payload: Record<string, unknown>): string {
  return `data: ${JSON.stringify(payload)}\n\n`;
}

describe("AgentEventStream", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("shows empty state when no events have arrived", async () => {
    const { response } = makeSseStream();
    globalThis.fetch = vi.fn().mockResolvedValue(response);

    render(<AgentEventStream runId="run-1" />);

    await waitFor(() => {
      expect(screen.getByTestId("agent-stream-empty")).toBeInTheDocument();
    });
  });

  it("renders agent.message event with its text", async () => {
    const { response, controller } = makeSseStream();
    globalThis.fetch = vi.fn().mockResolvedValue(response);

    render(<AgentEventStream runId="run-2" />);

    await act(async () => {
      controller.enqueue(
        sseLine({
          type: "agent.message",
          text: "Hello from the agent",
          ts: 1700000000000,
        })
      );
    });

    await waitFor(() => {
      expect(screen.getByText("Hello from the agent")).toBeInTheDocument();
    });
    expect(screen.getByTestId("agent-event-message")).toBeInTheDocument();
  });

  it("renders agent.tool_use with tool name and arguments", async () => {
    const { response, controller } = makeSseStream();
    globalThis.fetch = vi.fn().mockResolvedValue(response);

    render(<AgentEventStream runId="run-3" />);

    await act(async () => {
      controller.enqueue(
        sseLine({
          type: "agent.tool_use",
          toolName: "web_search",
          input: { query: "anthropic" },
          toolUseId: "tu_1",
          ts: 1700000001000,
        })
      );
    });

    await waitFor(() => {
      expect(screen.getByTestId("agent-event-tool-use")).toBeInTheDocument();
    });
    const card = screen.getByTestId("agent-event-tool-use");
    expect(card.textContent).toContain("web_search");
    expect(card.textContent).toContain("anthropic");
  });

  it("renders agent.thinking collapsed by default", async () => {
    const { response, controller } = makeSseStream();
    globalThis.fetch = vi.fn().mockResolvedValue(response);

    render(<AgentEventStream runId="run-4" />);

    await act(async () => {
      controller.enqueue(
        sseLine({
          type: "agent.thinking",
          text: "Reasoning about next step",
          ts: 1700000002000,
        })
      );
    });

    await waitFor(() => {
      expect(screen.getByTestId("agent-event-thinking")).toBeInTheDocument();
    });
    const button = screen
      .getByTestId("agent-event-thinking")
      .querySelector("button");
    expect(button).not.toBeNull();
    expect(button!.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("Reasoning about next step")).not.toBeInTheDocument();
  });

  it("expands agent.thinking content on click", async () => {
    const user = userEvent.setup();
    const { response, controller } = makeSseStream();
    globalThis.fetch = vi.fn().mockResolvedValue(response);

    render(<AgentEventStream runId="run-5" />);

    await act(async () => {
      controller.enqueue(
        sseLine({
          type: "agent.thinking",
          text: "Detailed chain of thought",
          ts: 1700000003000,
        })
      );
    });

    const thinking = await screen.findByTestId("agent-event-thinking");
    const toggle = thinking.querySelector("button");
    expect(toggle).not.toBeNull();

    await user.click(toggle!);

    expect(toggle!.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Detailed chain of thought")).toBeInTheDocument();
  });

  it("applies error styling to agent.tool_result with isError=true", async () => {
    const { response, controller } = makeSseStream();
    globalThis.fetch = vi.fn().mockResolvedValue(response);

    render(<AgentEventStream runId="run-6" />);

    await act(async () => {
      controller.enqueue(
        sseLine({
          type: "agent.tool_result",
          toolUseId: "tu_2",
          output: "Network timeout",
          isError: true,
          ts: 1700000004000,
        })
      );
    });

    const card = await screen.findByTestId("agent-event-tool-result");
    expect(card.getAttribute("data-error")).toBe("true");
    expect(card.className).toContain("border-error");
    expect(card.textContent).toContain("Network timeout");
  });

  it("updates status chip from running to idle", async () => {
    const { response, controller } = makeSseStream();
    globalThis.fetch = vi.fn().mockResolvedValue(response);

    render(<AgentEventStream runId="run-7" />);

    await act(async () => {
      controller.enqueue(
        sseLine({ type: "session.status_running", ts: 1700000005000 })
      );
    });

    await waitFor(() => {
      const chip = screen.getByTestId("agent-stream-status");
      expect(chip.getAttribute("data-tone")).toBe("running");
    });

    await act(async () => {
      controller.enqueue(
        sseLine({
          type: "session.status_idle",
          ts: 1700000006000,
          stopReason: "end_turn",
        })
      );
    });

    await waitFor(() => {
      const chip = screen.getByTestId("agent-stream-status");
      expect(chip.getAttribute("data-tone")).toBe("idle");
      expect(chip.textContent).toContain("end_turn");
    });
  });

  it("groups events by threadId when multiple threads are present", async () => {
    const { response, controller } = makeSseStream();
    globalThis.fetch = vi.fn().mockResolvedValue(response);

    render(<AgentEventStream runId="run-8" />);

    await act(async () => {
      controller.enqueue(
        sseLine({
          type: "agent.message",
          threadId: "thread-a",
          text: "from A",
          ts: 1700000007000,
        })
      );
      controller.enqueue(
        sseLine({
          type: "agent.message",
          threadId: "thread-b",
          text: "from B",
          ts: 1700000008000,
        })
      );
    });

    await waitFor(() => {
      const threads = screen.getAllByTestId("agent-stream-thread");
      expect(threads.length).toBe(2);
    });

    const threads = screen.getAllByTestId("agent-stream-thread");
    expect(threads[0].getAttribute("data-thread-id")).toBe("thread-a");
    expect(threads[1].getAttribute("data-thread-id")).toBe("thread-b");
  });

  it("shows error banner when stream fails", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response("boom", { status: 500 })
    );

    render(<AgentEventStream runId="run-9" />);

    await waitFor(() => {
      expect(screen.getByTestId("agent-stream-error-banner")).toBeInTheDocument();
    });
    const chip = screen.getByTestId("agent-stream-status");
    expect(chip.getAttribute("data-tone")).toBe("error");
  });

  it("falls back to unavailable state on 404", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response("not found", { status: 404 })
    );

    render(<AgentEventStream runId="run-10" />);

    await waitFor(() => {
      expect(screen.getByText(/not available/i)).toBeInTheDocument();
    });
  });
});
