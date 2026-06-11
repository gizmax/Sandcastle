import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { TelemetryRail, Odometer } from "@/components/runs/mission/TelemetryRail";
import { ThoughtStream } from "@/components/runs/mission/ThoughtStream";
import { formatClock, type FeedEntry, type Telemetry } from "@/lib/missionControl";

const telemetry: Telemetry = {
  costUsd: 0.0421,
  tokensEst: 1234,
  stepsTotal: 7,
  stepsDone: 3,
  stepsFailed: 0,
  stepsRunning: 1,
  models: ["mistral/large", "claude/sonnet"],
  activeStepId: "analyze",
};

describe("TelemetryRail", () => {
  it("renders cost, tokens, elapsed, step counter and models", () => {
    render(
      <TelemetryRail telemetry={telemetry} throughput={42} elapsedSeconds={83} isLive />
    );
    expect(screen.getByText("$0.0421")).toBeInTheDocument();
    expect(screen.getByText("1,234")).toBeInTheDocument();
    expect(screen.getByText("42 tok/s")).toBeInTheDocument();
    expect(screen.getByText("01:23")).toBeInTheDocument();
    expect(screen.getByText("3/7")).toBeInTheDocument();
    expect(screen.getByText("mistral/large")).toBeInTheDocument();
    expect(screen.getByText("claude/sonnet")).toBeInTheDocument();
  });

  it("shows $0.00 proudly with a free badge for local runs", () => {
    render(
      <TelemetryRail
        telemetry={{ ...telemetry, costUsd: 0 }}
        throughput={0}
        elapsedSeconds={5}
        isLive
      />
    );
    expect(screen.getByText("$0.00")).toBeInTheDocument();
    expect(screen.getByText("Free")).toBeInTheDocument();
    expect(screen.getByText("No API spend on this run.")).toBeInTheDocument();
  });

  it("flags failed steps in the progress card", () => {
    render(
      <TelemetryRail
        telemetry={{ ...telemetry, stepsFailed: 2 }}
        throughput={0}
        elapsedSeconds={5}
        isLive={false}
      />
    );
    expect(screen.getByText("2 steps failed")).toBeInTheDocument();
  });
});

describe("Odometer", () => {
  it("renders the target value immediately on mount (no fake count-up)", () => {
    render(<Odometer value={12.5} decimals={2} prefix="$" />);
    expect(screen.getByText("$12.50")).toBeInTheDocument();
  });
});

describe("formatClock", () => {
  it("formats mm:ss and hh:mm:ss", () => {
    expect(formatClock(0)).toBe("00:00");
    expect(formatClock(83)).toBe("01:23");
    expect(formatClock(3723)).toBe("01:02:03");
    expect(formatClock(-5)).toBe("00:00");
  });
});

describe("ThoughtStream", () => {
  const entries: FeedEntry[] = [
    { id: "1", ts: new Date(), kind: "run", title: "Run started" },
    { id: "2", ts: new Date(), kind: "step-start", stepId: "research", title: "research started" },
    {
      id: "3",
      ts: new Date(),
      kind: "output",
      stepId: "research",
      title: "research output",
      detail: "Found 3 sources.",
    },
    { id: "4", ts: new Date(), kind: "step-fail", stepId: "report", title: "report failed", detail: "timeout" },
  ];

  it("renders feed entries with details", () => {
    render(<ThoughtStream entries={entries} isLive />);
    expect(screen.getByText("Run started")).toBeInTheDocument();
    expect(screen.getByText("research started")).toBeInTheDocument();
    expect(screen.getByText("Found 3 sources.")).toBeInTheDocument();
    expect(screen.getByText("report failed")).toBeInTheDocument();
    expect(screen.getByText("timeout")).toBeInTheDocument();
    expect(screen.getByText("streaming")).toBeInTheDocument();
  });

  it("shows a waiting message when there are no events", () => {
    render(<ThoughtStream entries={[]} isLive={false} />);
    expect(screen.getByText("Waiting for events…")).toBeInTheDocument();
    expect(screen.queryByText("streaming")).not.toBeInTheDocument();
  });
});
