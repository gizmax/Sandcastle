/**
 * Proven badge + proof modal tests: badge rendering and click isolation,
 * modal manifest/checksum display, replay PASS/FAIL flow, and error states.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("@/api/client", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

import { api } from "@/api/client";
import { ProvenBadge } from "@/components/templates/ProvenBadge";
import { ProvenModal } from "@/components/templates/ProvenModal";
import { TemplateCard } from "@/components/templates/TemplateCard";

const mockedGet = vi.mocked(api.get);
const mockedPost = vi.mocked(api.post);

const VERIFICATION = {
  proven: true,
  manifest: {
    name: "bundle-test",
    version: "1.0.0",
    description: "A proven workflow",
    author: "tester",
    license: "MIT",
    sandcastle_version: "0.33.0",
    created_at: "2026-06-09T00:00:00+00:00",
  },
  workflow: { file: "workflow.yaml", sha256: "a".repeat(64), valid: true },
  cassettes: [
    {
      file: "cassettes/proof.cassette.json",
      sha256: "b".repeat(64),
      valid: true,
      step_count: 1,
      recorded_cost_usd: 0.02,
    },
  ],
  checksums_valid: true,
  installed_workflow_matches: true,
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ProvenBadge", () => {
  it("renders the bold Proven label", () => {
    render(<ProvenBadge />);
    expect(screen.getByTestId("proven-badge")).toHaveTextContent("Proven");
  });

  it("fires onClick without triggering the parent", () => {
    const onBadge = vi.fn();
    const onParent = vi.fn();
    render(
      <button onClick={onParent}>
        <ProvenBadge onClick={onBadge} />
      </button>
    );
    fireEvent.click(screen.getByTestId("proven-badge"));
    expect(onBadge).toHaveBeenCalledTimes(1);
    expect(onParent).not.toHaveBeenCalled();
  });
});

describe("TemplateCard proven badge", () => {
  const baseTemplate = {
    name: "bundle-test",
    description: "desc",
    tags: [],
    step_count: 1,
    source: "community" as const,
  };

  it("shows the badge for proven templates", () => {
    render(
      <TemplateCard
        template={{ ...baseTemplate, proven: true }}
        isSelected={false}
        onClick={vi.fn()}
        onProvenClick={vi.fn()}
      />
    );
    expect(screen.getByTestId("proven-badge")).toBeInTheDocument();
  });

  it("shows nothing for non-bundle templates", () => {
    render(
      <TemplateCard template={baseTemplate} isSelected={false} onClick={vi.fn()} />
    );
    expect(screen.queryByTestId("proven-badge")).not.toBeInTheDocument();
  });

  it("opens the proof instead of the card detail when the badge is clicked", () => {
    const onCard = vi.fn();
    const onProven = vi.fn();
    render(
      <TemplateCard
        template={{ ...baseTemplate, proven: true }}
        isSelected={false}
        onClick={onCard}
        onProvenClick={onProven}
      />
    );
    fireEvent.click(screen.getByTestId("proven-badge"));
    expect(onProven).toHaveBeenCalledTimes(1);
    expect(onCard).not.toHaveBeenCalled();
  });
});

describe("ProvenModal", () => {
  it("loads and shows manifest details with checksum validity", async () => {
    mockedGet.mockResolvedValue({ data: VERIFICATION, error: null });
    render(<ProvenModal templateName="bundle-test" onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText("tester")).toBeInTheDocument();
    });
    expect(mockedGet).toHaveBeenCalledWith("/templates/bundle-test/verification");
    expect(screen.getByText("1.0.0")).toBeInTheDocument();
    expect(screen.getByText("MIT")).toBeInTheDocument();
    expect(screen.getByText("workflow.yaml")).toBeInTheDocument();
    expect(screen.getByText("proof.cassette.json")).toBeInTheDocument();
    expect(screen.getByText(/sha256: aaaaaaaaaaaa/)).toBeInTheDocument();
  });

  it("replays the proof and shows PASS per cassette", async () => {
    mockedGet.mockResolvedValue({ data: VERIFICATION, error: null });
    mockedPost.mockResolvedValue({
      data: {
        ok: true,
        errors: [],
        cassettes: [
          {
            file: "cassettes/proof.cassette.json",
            passed: true,
            detail: "1 step(s) replayed at $0",
            replay_hits: 1,
            replay_misses: 0,
          },
        ],
      },
      error: null,
    });

    render(<ProvenModal templateName="bundle-test" onClose={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText("Replay proof locally")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Replay proof locally"));
    await waitFor(() => {
      expect(screen.getByText("PASS")).toBeInTheDocument();
    });
    expect(mockedPost).toHaveBeenCalledWith("/templates/bundle-test/verify");
    expect(
      screen.getByText("Proof verified - every cassette replayed at $0.")
    ).toBeInTheDocument();
  });

  it("shows FAIL and errors when the replay breaks", async () => {
    mockedGet.mockResolvedValue({ data: VERIFICATION, error: null });
    mockedPost.mockResolvedValue({
      data: {
        ok: false,
        errors: ["cassette 'cassettes/proof.cassette.json' checksum mismatch"],
        cassettes: [
          {
            file: "cassettes/proof.cassette.json",
            passed: false,
            detail: "checksum mismatch",
            replay_hits: 0,
            replay_misses: 0,
          },
        ],
      },
      error: null,
    });

    render(<ProvenModal templateName="bundle-test" onClose={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText("Replay proof locally")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Replay proof locally"));
    await waitFor(() => {
      expect(screen.getByText("FAIL")).toBeInTheDocument();
    });
    expect(screen.getByText("Verification failed.")).toBeInTheDocument();
    expect(screen.getAllByText(/checksum mismatch/).length).toBeGreaterThan(0);
  });

  it("warns when the installed workflow was edited after install", async () => {
    mockedGet.mockResolvedValue({
      data: { ...VERIFICATION, installed_workflow_matches: false },
      error: null,
    });
    render(<ProvenModal templateName="bundle-test" onClose={vi.fn()} />);
    await waitFor(() => {
      expect(
        screen.getByText(/edited after install/)
      ).toBeInTheDocument();
    });
  });

  it("handles an unreachable API gracefully", async () => {
    mockedGet.mockRejectedValue(new Error("connection refused"));
    render(<ProvenModal templateName="bundle-test" onClose={vi.fn()} />);
    await waitFor(() => {
      expect(
        screen.getByText(/Could not load the verification status/)
      ).toBeInTheDocument();
    });
  });

  it("closes from the header button", async () => {
    mockedGet.mockResolvedValue({ data: VERIFICATION, error: null });
    const onClose = vi.fn();
    render(<ProvenModal templateName="bundle-test" onClose={onClose} />);
    fireEvent.click(screen.getByLabelText("Close proof dialog"));
    expect(onClose).toHaveBeenCalled();
  });
});
