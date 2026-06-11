import { memo, useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  type Node,
  type Edge,
  type NodeProps,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Layers, Radio } from "lucide-react";
import { cn } from "@/lib/utils";
import type { AdapterInfo } from "@/pages/NightShiftPage";

interface AdapterLineageGraphProps {
  adapters: AdapterInfo[];
  onSelect: (adapter: AdapterInfo) => void;
  className?: string;
}

type AdapterNodeData = {
  adapter: AdapterInfo;
  onSelect: (adapter: AdapterInfo) => void;
};

type AdapterNodeType = Node<AdapterNodeData, "adapter">;

function AdapterNodeComponent({ data }: NodeProps<AdapterNodeType>) {
  const { adapter, onSelect } = data;
  const score = adapter.metrics?.eval_score;

  return (
    <button
      type="button"
      onClick={() => onSelect(adapter)}
      aria-label={`Adapter ${adapter.adapter_id}`}
      className={cn(
        "rounded-lg border bg-surface px-3.5 py-2.5 text-left shadow-sm min-w-[170px]",
        "transition-all duration-200 cursor-pointer hover:shadow-md",
        adapter.served
          ? "border-accent ring-2 ring-accent/40 glow-accent"
          : "border-border hover:border-accent/40"
      )}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!bg-accent !border-surface !w-2 !h-2"
      />

      <div className="flex items-center gap-1.5">
        <Layers className={cn("h-3.5 w-3.5 shrink-0", adapter.served ? "text-accent" : "text-muted")} />
        <span className="font-mono text-xs font-medium text-foreground truncate max-w-[150px]">
          {adapter.adapter_id}
        </span>
      </div>

      <div className="mt-1.5 flex items-center gap-1.5">
        <span className="rounded-md bg-success/10 px-1.5 py-0.5 text-[10px] font-semibold text-success font-data">
          {score != null ? `eval ${score.toFixed(2)}` : "no eval"}
        </span>
        {adapter.served && (
          <span className="inline-flex items-center gap-1 rounded-md bg-accent/15 border border-accent/30 px-1.5 py-0.5 text-[10px] font-semibold text-accent">
            <Radio className="h-2.5 w-2.5 animate-pulse" />
            SERVING
          </span>
        )}
      </div>

      <Handle
        type="source"
        position={Position.Bottom}
        className="!bg-accent !border-surface !w-2 !h-2"
      />
    </button>
  );
}

const AdapterNode = memo(AdapterNodeComponent);

const nodeTypes: NodeTypes = {
  adapter: AdapterNode,
};

/** Parent -> child lineage tree of trained adapters, served adapter highlighted. */
export function AdapterLineageGraph({ adapters, onSelect, className }: AdapterLineageGraphProps) {
  const { nodes, edges } = useMemo(() => {
    const byId = new Map(adapters.map((a) => [a.adapter_id, a]));

    // Depth = generation: 0 for roots, parent depth + 1 for children.
    const depths = new Map<string, number>();
    function getDepth(id: string, seen: Set<string>): number {
      const cached = depths.get(id);
      if (cached !== undefined) return cached;
      if (seen.has(id)) return 0; // cycle guard
      seen.add(id);
      const parentId = byId.get(id)?.parent_adapter_id;
      const depth = parentId && byId.has(parentId) ? getDepth(parentId, seen) + 1 : 0;
      depths.set(id, depth);
      return depth;
    }
    adapters.forEach((a) => getDepth(a.adapter_id, new Set()));

    // Group generations for horizontal centering (oldest first inside a layer).
    const layerGroups = new Map<number, AdapterInfo[]>();
    [...adapters]
      .sort((a, b) => a.created_at - b.created_at)
      .forEach((a) => {
        const depth = depths.get(a.adapter_id) ?? 0;
        if (!layerGroups.has(depth)) layerGroups.set(depth, []);
        layerGroups.get(depth)!.push(a);
      });

    const xSpacing = 230;
    const ySpacing = 120;

    const nodes: Node[] = adapters.map((a) => {
      const depth = depths.get(a.adapter_id) ?? 0;
      const group = layerGroups.get(depth) ?? [a];
      const indexInLayer = group.findIndex((g) => g.adapter_id === a.adapter_id);
      const xOffset = (indexInLayer - (group.length - 1) / 2) * xSpacing;
      return {
        id: a.adapter_id,
        type: "adapter",
        position: { x: 300 + xOffset, y: depth * ySpacing + 40 },
        data: { adapter: a, onSelect },
      };
    });

    const edges: Edge[] = adapters
      .filter((a) => a.parent_adapter_id && byId.has(a.parent_adapter_id))
      .map((a) => ({
        id: `${a.parent_adapter_id}-${a.adapter_id}`,
        source: a.parent_adapter_id!,
        target: a.adapter_id,
        animated: a.served,
        style: { stroke: "var(--color-accent)", strokeWidth: 2 },
      }));

    return { nodes, edges };
  }, [adapters, onSelect]);

  return (
    <div className={className} style={{ height: "340px" }}>
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
