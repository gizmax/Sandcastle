import { useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  type Node,
  type Edge,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { StepNode } from "@/components/workflows/StepNode";

interface DagStep {
  id: string;
  model?: string;
  depends_on?: string[];
  status?: string;
}

interface DagGraphProps {
  steps: DagStep[];
  className?: string;
}

const nodeTypes: NodeTypes = {
  step: StepNode,
};

export function DagGraph({ steps, className }: DagGraphProps) {
  const { nodes, edges } = useMemo(() => {
    // Build adjacency and compute layers via topological sort
    const depMap = new Map<string, string[]>();
    const stepMap = new Map<string, DagStep>();
    steps.forEach((s) => {
      depMap.set(s.id, s.depends_on || []);
      stepMap.set(s.id, s);
    });

    // Assign layers
    const layers = new Map<string, number>();
    const visited = new Set<string>();

    function getLayer(id: string): number {
      if (layers.has(id)) return layers.get(id)!;
      if (visited.has(id)) return 0; // cycle guard
      visited.add(id);

      const deps = depMap.get(id) || [];
      if (deps.length === 0) {
        layers.set(id, 0);
        return 0;
      }
      const maxDep = Math.max(...deps.map(getLayer));
      const layer = maxDep + 1;
      layers.set(id, layer);
      return layer;
    }

    steps.forEach((s) => getLayer(s.id));

    // Group by layer for horizontal positioning
    const layerGroups: Map<number, string[]> = new Map();
    steps.forEach((s) => {
      const layer = layers.get(s.id) || 0;
      if (!layerGroups.has(layer)) layerGroups.set(layer, []);
      layerGroups.get(layer)!.push(s.id);
    });

    const xSpacing = 200;
    const ySpacing = 120;

    const nodes: Node[] = steps.map((s) => {
      const layer = layers.get(s.id) || 0;
      const group = layerGroups.get(layer) || [s.id];
      const indexInLayer = group.indexOf(s.id);
      const totalInLayer = group.length;
      const xOffset = (indexInLayer - (totalInLayer - 1) / 2) * xSpacing;

      return {
        id: s.id,
        type: "step",
        position: { x: 300 + xOffset, y: layer * ySpacing + 50 },
        data: {
          label: s.id,
          model: s.model,
          status: s.status,
        },
      };
    });

    const edges: Edge[] = [];
    steps.forEach((s) => {
      (s.depends_on || []).forEach((dep) => {
        edges.push({
          id: `${dep}-${s.id}`,
          source: dep,
          target: s.id,
          animated: s.status === "running",
          style: { stroke: "var(--color-accent)", strokeWidth: 2 },
        });
      });
    });

    return { nodes, edges };
  }, [steps]);

  return (
    <div className={className} style={{ height: "400px" }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        proOptions={{ hideAttribution: true }}
        nodesDraggable={false}
        nodesConnectable={false}
      >
        <Background gap={16} size={1} color="var(--color-border)" />
        <Controls
          showInteractive={false}
          className="!bg-surface !border-border !shadow-sm"
        />
      </ReactFlow>
    </div>
  );
}
