import { useCallback, useEffect, useState } from "react";
import { Cpu, Network, RefreshCw, Server } from "lucide-react";
import { api } from "@/api/client";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { ErrorState } from "@/components/shared/ErrorState";
import { cn, formatHeartbeatAge } from "@/lib/utils";

// -- Types --------------------------------------------------------------

export interface MeshNode {
  id: string;
  name: string;
  base_url: string;
  capabilities: string[];
  last_heartbeat: string | null;
  heartbeat_age_seconds: number | null;
  status: "alive" | "dead";
  registered_at: string | null;
}

interface MeshNodesData {
  enabled: boolean;
  heartbeat_seconds: number;
  local_capabilities: string[];
  nodes: MeshNode[];
}

// -- Helpers ------------------------------------------------------------

const CAPABILITY_STYLES: Record<string, string> = {
  gpu: "bg-accent/10 text-accent",
  spark: "bg-success/10 text-success",
  browser: "bg-info/10 text-info",
  docker: "bg-warning/10 text-warning",
};

function CapabilityChip({ capability }: { capability: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        CAPABILITY_STYLES[capability] ?? "bg-secondary text-muted"
      )}
    >
      {capability}
    </span>
  );
}

function NodeCard({ node }: { node: MeshNode }) {
  const alive = node.status === "alive";
  return (
    <div
      data-testid="mesh-node-card"
      className="rounded-xl border border-border bg-card p-5 shadow-sm"
    >
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Server className="h-4 w-4 text-muted" />
          <span className="font-semibold text-foreground">{node.name}</span>
        </div>
        <div className="flex items-center gap-2">
          <span
            data-testid="mesh-status-dot"
            className={cn(
              "h-2.5 w-2.5 rounded-full",
              alive ? "bg-success" : "bg-error animate-pulse"
            )}
          />
          <span className={cn("text-xs font-medium", alive ? "text-success" : "text-error")}>
            {alive ? "Alive" : "Dead"}
          </span>
        </div>
      </div>
      <p className="mb-3 truncate font-mono text-xs text-muted">{node.base_url}</p>
      <div className="mb-3 flex flex-wrap gap-1.5">
        {node.capabilities.length > 0 ? (
          node.capabilities.map((cap) => <CapabilityChip key={cap} capability={cap} />)
        ) : (
          <span className="text-xs text-muted">no capabilities</span>
        )}
      </div>
      <p className="text-xs text-muted">
        Heartbeat: <span className="text-foreground">{formatHeartbeatAge(node.heartbeat_age_seconds)}</span>
      </p>
    </div>
  );
}

// -- Page ---------------------------------------------------------------

export default function FleetPage() {
  const [data, setData] = useState<MeshNodesData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const fetchNodes = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    setError(null);
    try {
      const res = await api.get<MeshNodesData>("/mesh/nodes");
      if (res.error) {
        setError(res.error.message);
      } else {
        setData(res.data);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load mesh nodes");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchNodes();
    const interval = setInterval(() => fetchNodes(true), 15_000);
    return () => clearInterval(interval);
  }, [fetchNodes]);

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner />
      </div>
    );
  }

  if (error) {
    return <ErrorState message={error} onRetry={() => fetchNodes(true)} />;
  }

  const nodes = data?.nodes ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-foreground">
            <Network className="h-6 w-6 text-accent" />
            Fleet
          </h1>
          <p className="mt-1 text-sm text-muted">
            Sandcastle Mesh nodes - steps with <code className="rounded bg-secondary px-1">requires</code> are
            routed to the machine with the right capabilities.
          </p>
        </div>
        <button
          onClick={() => fetchNodes(true)}
          disabled={refreshing}
          className={cn(
            "flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2",
            "text-sm font-medium text-foreground hover:bg-secondary transition-colors",
            refreshing && "opacity-60"
          )}
        >
          <RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} />
          Refresh
        </button>
      </div>

      {data && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-card px-4 py-3 text-sm">
          <Cpu className="h-4 w-4 text-muted" />
          <span className="text-muted">This machine:</span>
          <div className="flex flex-wrap gap-1.5">
            {data.local_capabilities.map((cap) => (
              <CapabilityChip key={cap} capability={cap} />
            ))}
          </div>
          <span className="ml-auto text-xs text-muted">
            Mesh {data.enabled ? "enabled" : "disabled"} · heartbeat every {data.heartbeat_seconds}s
          </span>
        </div>
      )}

      {nodes.length === 0 ? (
        <EmptyState
          icon={Network}
          title="No mesh nodes yet"
          description={
            "Join a machine to the mesh and its capabilities (gpu, browser, docker) become " +
            "available to every workflow: sandcastle node join <coordinator-url> --token <mesh-token>"
          }
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {nodes.map((node) => (
            <NodeCard key={node.id} node={node} />
          ))}
        </div>
      )}
    </div>
  );
}
