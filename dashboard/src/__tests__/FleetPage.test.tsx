import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

const mockGet = vi.fn();

vi.mock("@/api/client", () => ({
  api: {
    get: (...args: unknown[]) => mockGet(...args),
  },
}));

import FleetPage from "@/pages/FleetPage";
import { formatHeartbeatAge } from "@/lib/utils";

const NODES_RESPONSE = {
  data: {
    enabled: true,
    heartbeat_seconds: 15,
    local_capabilities: ["code"],
    nodes: [
      {
        id: "n1",
        name: "dgx-spark",
        base_url: "http://spark.local:8080",
        capabilities: ["code", "gpu", "spark"],
        last_heartbeat: new Date().toISOString(),
        heartbeat_age_seconds: 4,
        status: "alive",
        registered_at: new Date().toISOString(),
      },
      {
        id: "n2",
        name: "mac-mini",
        base_url: "http://mac-mini.local:8080",
        capabilities: ["code", "browser"],
        last_heartbeat: new Date().toISOString(),
        heartbeat_age_seconds: 312,
        status: "dead",
        registered_at: new Date().toISOString(),
      },
    ],
  },
  error: null,
};

describe("FleetPage", () => {
  beforeEach(() => {
    mockGet.mockReset();
  });

  it("renders node cards with name, capabilities and status", async () => {
    mockGet.mockResolvedValue(NODES_RESPONSE);
    render(<FleetPage />);

    await waitFor(() => {
      expect(screen.getByText("dgx-spark")).toBeInTheDocument();
    });
    expect(mockGet).toHaveBeenCalledWith("/mesh/nodes");
    expect(screen.getByText("mac-mini")).toBeInTheDocument();
    expect(screen.getAllByTestId("mesh-node-card")).toHaveLength(2);
    // Capability chips
    expect(screen.getByText("gpu")).toBeInTheDocument();
    expect(screen.getByText("browser")).toBeInTheDocument();
    // Status labels (one alive, one dead)
    expect(screen.getByText("Alive")).toBeInTheDocument();
    expect(screen.getByText("Dead")).toBeInTheDocument();
    // Heartbeat ages
    expect(screen.getByText("4s ago")).toBeInTheDocument();
    expect(screen.getByText("5m ago")).toBeInTheDocument();
  });

  it("shows the local capability summary bar", async () => {
    mockGet.mockResolvedValue(NODES_RESPONSE);
    render(<FleetPage />);
    await waitFor(() => {
      expect(screen.getByText("This machine:")).toBeInTheDocument();
    });
    expect(screen.getByText(/heartbeat every 15s/)).toBeInTheDocument();
  });

  it("shows empty state with the join command when no nodes", async () => {
    mockGet.mockResolvedValue({
      data: {
        enabled: false,
        heartbeat_seconds: 15,
        local_capabilities: ["code"],
        nodes: [],
      },
      error: null,
    });
    render(<FleetPage />);
    await waitFor(() => {
      expect(screen.getByText("No mesh nodes yet")).toBeInTheDocument();
    });
    expect(screen.getByText(/sandcastle node join/)).toBeInTheDocument();
    expect(screen.queryByTestId("mesh-node-card")).toBeNull();
  });

  it("shows an error state when the API fails", async () => {
    mockGet.mockResolvedValue({
      data: null,
      error: { code: "FORBIDDEN", message: "Admin access required" },
    });
    render(<FleetPage />);
    await waitFor(() => {
      expect(screen.getByText("Admin access required")).toBeInTheDocument();
    });
  });
});

describe("formatHeartbeatAge", () => {
  it("formats seconds, minutes, hours, days and never", () => {
    expect(formatHeartbeatAge(null)).toBe("never");
    expect(formatHeartbeatAge(12)).toBe("12s ago");
    expect(formatHeartbeatAge(90)).toBe("1m ago");
    expect(formatHeartbeatAge(7200)).toBe("2h ago");
    expect(formatHeartbeatAge(200_000)).toBe("2d ago");
  });
});
