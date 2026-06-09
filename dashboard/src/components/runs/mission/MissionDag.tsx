import { useEffect, useMemo } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  useReactFlow,
  type Node,
  type Edge,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { StepNode } from "@/components/workflows/StepNode";
import type { MissionStep } from "@/lib/missionControl";

interface MissionDagProps {
  steps: MissionStep[];
  activeStepId: string | null;
  follow: boolean;
}

const nodeTypes: NodeTypes = {
  step: StepNode,
};

const DONE = new Set(["completed", "skipped"]);

/** Collapse parallel fan-out rows into one node per step_id. */
function collapseSteps(steps: MissionStep[]): MissionStep[] {
  const byId = new Map<string, MissionStep>();
  for (const step of steps) {
    const existing = byId.get(step.step_id);
    if (!existing) {
      byId.set(step.step_id, { ...step });
      continue;
    }
    // Aggregate: failed > running > queued > pending; completed only when all done
    const merged = { ...existing };
    merged.cost_usd = (existing.cost_usd ?? 0) + (step.cost_usd ?? 0);
    if (step.status === "failed" || existing.status === "failed") {
      merged.status = "failed";
    } else if (step.status === "running" || existing.status === "running") {
      merged.status = "running";
    } else if (!DONE.has(step.status) || !DONE.has(existing.status)) {
      merged.status = DONE.has(step.status) ? existing.status : step.status;
    }
    byId.set(step.step_id, merged);
  }
  return [...byId.values()];
}

function nodeOpacity(status: string): number {
  if (status === "pending") return 0.45;
  if (status === "queued") return 0.7;
  return 1;
}

function edgeStyle(sourceStatus: string, targetStatus: string): Partial<Edge> {
  const sourceDone = DONE.has(sourceStatus);
  // Data is flowing: upstream finished, downstream consuming it
  if (sourceDone && targetStatus === "running") {
    return {
      animated: true,
      style: { stroke: "var(--color-success)", strokeWidth: 2 },
    };
  }
  if (sourceStatus === "failed") {
    return {
      animated: false,
      style: { stroke: "var(--color-error)", strokeWidth: 2, opacity: 0.9 },
    };
  }
  if (sourceDone) {
    return {
      animated: false,
      style: { stroke: "var(--color-success)", strokeWidth: 2, opacity: 0.75 },
    };
  }
  if (sourceStatus === "running") {
    return {
      animated: true,
      style: { stroke: "var(--color-accent)", strokeWidth: 2, opacity: 0.9 },
    };
  }
  return {
    animated: false,
    style: { stroke: "var(--color-border)", strokeWidth: 1.5, opacity: 0.7 },
  };
}

function MissionDagInner({ steps, activeStepId, follow }: MissionDagProps) {
  const reactFlow = useReactFlow();
  const collapsed = useMemo(() => collapseSteps(steps), [steps]);

  const { nodes, edges } = useMemo(() => {
    // Layered layout via topological depth (same approach as DagGraph)
    const depMap = new Map<string, string[]>();
    const statusMap = new Map<string, string>();
    collapsed.forEach((s) => {
      depMap.set(s.step_id, s.depends_on || []);
      statusMap.set(s.step_id, s.status);
    });

    const layers = new Map<string, number>();
    const visited = new Set<string>();

    function getLayer(id: string): number {
      if (layers.has(id)) return layers.get(id)!;
      if (visited.has(id)) return 0; // cycle guard
      visited.add(id);

      const deps = (depMap.get(id) || []).filter((d) => depMap.has(d));
      if (deps.length === 0) {
        layers.set(id, 0);
        return 0;
      }
      const maxDep = Math.max(...deps.map(getLayer));
      const layer = maxDep + 1;
      layers.set(id, layer);
      return layer;
    }

    collapsed.forEach((s) => getLayer(s.step_id));

    const layerGroups: Map<number, string[]> = new Map();
    collapsed.forEach((s) => {
      const layer = layers.get(s.step_id) || 0;
      if (!layerGroups.has(layer)) layerGroups.set(layer, []);
      layerGroups.get(layer)!.push(s.step_id);
    });

    const xSpacing = 220;
    const ySpacing = 140;

    const nodes: Node[] = collapsed.map((s) => {
      const layer = layers.get(s.step_id) || 0;
      const group = layerGroups.get(layer) || [s.step_id];
      const indexInLayer = group.indexOf(s.step_id);
      const totalInLayer = group.length;
      const xOffset = (indexInLayer - (totalInLayer - 1) / 2) * xSpacing;
      const isActive = s.step_id === activeStepId;

      return {
        id: s.step_id,
        type: "step",
        position: { x: 400 + xOffset, y: layer * ySpacing + 60 },
        className:
          s.status === "completed"
            ? "mission-node-flash"
            : s.status === "failed"
              ? "mission-node-flash-error"
              : undefined,
        style: {
          opacity: nodeOpacity(s.status),
          transition: "opacity 300ms ease",
        },
        selected: isActive,
        data: {
          label: s.step_id,
          model: s.model ?? undefined,
          status: s.status,
        },
      };
    });

    const edges: Edge[] = [];
    collapsed.forEach((s) => {
      (s.depends_on || []).forEach((dep) => {
        if (!statusMap.has(dep)) return;
        edges.push({
          id: `${dep}-${s.step_id}`,
          source: dep,
          target: s.step_id,
          ...edgeStyle(statusMap.get(dep) ?? "pending", s.status),
        });
      });
    });

    return { nodes, edges };
  }, [collapsed, activeStepId]);

  // Auto-pan/zoom to the active node while following the run
  useEffect(() => {
    if (!follow || !activeStepId) return;
    // Defer one frame so the node exists in the flow before fitting
    const raf = requestAnimationFrame(() => {
      void reactFlow.fitView({
        nodes: [{ id: activeStepId }],
        duration: 600,
        padding: 2.5,
        maxZoom: 1.1,
      });
    });
    return () => cancelAnimationFrame(raf);
  }, [follow, activeStepId, reactFlow]);

  // Re-frame the whole graph when follow is switched off
  useEffect(() => {
    if (follow) return;
    void reactFlow.fitView({ duration: 600, padding: 0.2 });
  }, [follow, reactFlow]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      fitView
      minZoom={0.2}
      proOptions={{ hideAttribution: true }}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
      panOnScroll
      zoomOnScroll
    >
      <Background gap={24} size={1.5} color="var(--color-border)" />
    </ReactFlow>
  );
}

export function MissionDag(props: MissionDagProps) {
  return (
    <ReactFlowProvider>
      <MissionDagInner {...props} />
    </ReactFlowProvider>
  );
}
