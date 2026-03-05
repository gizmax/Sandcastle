import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Network, Maximize2 } from "lucide-react";

interface WorkflowNode {
  name: string;
  file_name: string;
  steps_count: number;
  last_run_status?: string | null;
}

interface DependencyGraphProps {
  workflows: WorkflowNode[];
}

interface GraphNode {
  id: number;
  name: string;
  fileName: string;
  stepsCount: number;
  status: string | null;
  x: number;
  y: number;
  vx: number;
  vy: number;
  radius: number;
}

interface GraphEdge {
  source: number;
  target: number;
  strength: number;
}

function sharedPrefix(a: string, b: string): number {
  const partsA = a.toLowerCase().replace(/[_.\s]/g, "-").split("-");
  const partsB = b.toLowerCase().replace(/[_.\s]/g, "-").split("-");
  let shared = 0;
  for (let i = 0; i < Math.min(partsA.length, partsB.length); i++) {
    if (partsA[i] === partsB[i] && partsA[i].length > 1) shared++;
    else break;
  }
  return shared;
}

function simpleSimilarity(a: string, b: string): number {
  const la = a.toLowerCase();
  const lb = b.toLowerCase();
  if (la === lb) return 1;
  const maxLen = Math.max(la.length, lb.length);
  if (maxLen === 0) return 0;
  let matches = 0;
  const setA = new Set(la.replace(/[^a-z0-9]/g, "").split(""));
  const setB = new Set(lb.replace(/[^a-z0-9]/g, "").split(""));
  for (const c of setA) {
    if (setB.has(c)) matches++;
  }
  return matches / Math.max(setA.size, setB.size);
}

function buildGraph(workflows: WorkflowNode[]): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const w = 800;
  const h = 400;
  const nodes: GraphNode[] = workflows.map((wf, i) => {
    const angle = (2 * Math.PI * i) / Math.max(workflows.length, 1);
    const r = Math.min(w, h) * 0.3;
    return {
      id: i,
      name: wf.name,
      fileName: wf.file_name,
      stepsCount: wf.steps_count,
      status: wf.last_run_status ?? null,
      x: w / 2 + r * Math.cos(angle) + (Math.random() - 0.5) * 20,
      y: h / 2 + r * Math.sin(angle) + (Math.random() - 0.5) * 20,
      vx: 0,
      vy: 0,
      radius: Math.min(30, Math.max(20, 18 + wf.steps_count * 1.5)),
    };
  });

  const edges: GraphEdge[] = [];
  const edgeSet = new Set<string>();

  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const prefix = sharedPrefix(nodes[i].name, nodes[j].name);
      if (prefix > 0) {
        const key = `${i}-${j}`;
        if (!edgeSet.has(key)) {
          edgeSet.add(key);
          edges.push({ source: i, target: j, strength: Math.min(1, prefix * 0.5) });
        }
        continue;
      }
      const sim = simpleSimilarity(nodes[i].name, nodes[j].name);
      if (sim > 0.5) {
        const key = `${i}-${j}`;
        if (!edgeSet.has(key)) {
          edgeSet.add(key);
          edges.push({ source: i, target: j, strength: sim * 0.3 });
        }
        continue;
      }
      const stepDiff = Math.abs(nodes[i].stepsCount - nodes[j].stepsCount);
      if (stepDiff <= 1 && edges.filter((e) => e.source === i || e.target === i).length === 0) {
        const key = `${i}-${j}`;
        if (!edgeSet.has(key)) {
          edgeSet.add(key);
          edges.push({ source: i, target: j, strength: 0.15 });
        }
      }
    }
  }

  return { nodes, edges };
}

function simulate(nodes: GraphNode[], edges: GraphEdge[], iterations: number): void {
  const w = 800;
  const h = 400;
  const centerX = w / 2;
  const centerY = h / 2;

  for (let iter = 0; iter < iterations; iter++) {
    const alpha = 1 - iter / iterations;
    const decay = alpha * 0.8;

    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        let dx = nodes[j].x - nodes[i].x;
        let dy = nodes[j].y - nodes[i].y;
        let dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < 1) {
          dx = (Math.random() - 0.5) * 2;
          dy = (Math.random() - 0.5) * 2;
          dist = Math.sqrt(dx * dx + dy * dy);
        }
        const repulsion = (800 * decay) / (dist * dist);
        const fx = (dx / dist) * repulsion;
        const fy = (dy / dist) * repulsion;
        nodes[i].vx -= fx;
        nodes[i].vy -= fy;
        nodes[j].vx += fx;
        nodes[j].vy += fy;
      }
    }

    for (const edge of edges) {
      const s = nodes[edge.source];
      const t = nodes[edge.target];
      const dx = t.x - s.x;
      const dy = t.y - s.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < 1) continue;
      const idealDist = 120;
      const force = (dist - idealDist) * 0.05 * edge.strength * decay;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      s.vx += fx;
      s.vy += fy;
      t.vx -= fx;
      t.vy -= fy;
    }

    for (const node of nodes) {
      const dx = centerX - node.x;
      const dy = centerY - node.y;
      node.vx += dx * 0.005 * decay;
      node.vy += dy * 0.005 * decay;
    }

    for (const node of nodes) {
      node.vx *= 0.6;
      node.vy *= 0.6;
      node.x += node.vx;
      node.y += node.vy;
      node.x = Math.max(node.radius + 10, Math.min(w - node.radius - 10, node.x));
      node.y = Math.max(node.radius + 10, Math.min(h - node.radius - 10, node.y));
    }
  }
}

function statusColor(status: string | null): { fill: string; stroke: string } {
  switch (status) {
    case "completed":
      return { fill: "rgba(34,197,94,0.15)", stroke: "var(--color-success, #22c55e)" };
    case "failed":
      return { fill: "rgba(239,68,68,0.15)", stroke: "var(--color-error, #ef4444)" };
    case "running":
      return { fill: "rgba(59,130,246,0.15)", stroke: "var(--color-accent, #3b82f6)" };
    default:
      return { fill: "var(--color-surface, #1e1e2e)", stroke: "var(--color-border, #333)" };
  }
}

function truncateLabel(name: string, maxLen: number): string {
  if (name.length <= maxLen) return name;
  return name.slice(0, maxLen - 1) + "\u2026";
}

export function DependencyGraph({ workflows }: DependencyGraphProps) {
  const navigate = useNavigate();
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const [visible, setVisible] = useState(true);
  const [hoveredNode, setHoveredNode] = useState<number | null>(null);
  const [dragNode, setDragNode] = useState<number | null>(null);
  const [viewBox, setViewBox] = useState({ x: 0, y: 0, w: 800, h: 400 });
  const [isPanning, setIsPanning] = useState(false);
  const panStart = useRef({ x: 0, y: 0, vx: 0, vy: 0 });

  const { nodes, edges } = useMemo(() => {
    const graph = buildGraph(workflows);
    simulate(graph.nodes, graph.edges, 120);
    return graph;
  }, [workflows]);

  const [positions, setPositions] = useState<{ x: number; y: number }[]>(() =>
    nodes.map((n) => ({ x: n.x, y: n.y }))
  );

  useEffect(() => {
    setPositions(nodes.map((n) => ({ x: n.x, y: n.y })));
  }, [nodes]);

  const connectedEdges = useMemo(() => {
    if (hoveredNode === null) return new Set<number>();
    const s = new Set<number>();
    edges.forEach((e, i) => {
      if (e.source === hoveredNode || e.target === hoveredNode) s.add(i);
    });
    return s;
  }, [hoveredNode, edges]);

  const connectedNodes = useMemo(() => {
    if (hoveredNode === null) return new Set<number>();
    const s = new Set<number>([hoveredNode]);
    edges.forEach((e) => {
      if (e.source === hoveredNode) s.add(e.target);
      if (e.target === hoveredNode) s.add(e.source);
    });
    return s;
  }, [hoveredNode, edges]);

  const svgPoint = useCallback(
    (clientX: number, clientY: number) => {
      const svg = svgRef.current;
      if (!svg) return { x: 0, y: 0 };
      const rect = svg.getBoundingClientRect();
      return {
        x: viewBox.x + ((clientX - rect.left) / rect.width) * viewBox.w,
        y: viewBox.y + ((clientY - rect.top) / rect.height) * viewBox.h,
      };
    },
    [viewBox]
  );

  const handleMouseDown = useCallback(
    (e: React.MouseEvent, nodeId?: number) => {
      if (nodeId !== undefined) {
        e.stopPropagation();
        setDragNode(nodeId);
        return;
      }
      setIsPanning(true);
      panStart.current = { x: e.clientX, y: e.clientY, vx: viewBox.x, vy: viewBox.y };
    },
    [viewBox]
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (dragNode !== null) {
        const pt = svgPoint(e.clientX, e.clientY);
        setPositions((prev) => {
          const next = [...prev];
          next[dragNode] = { x: pt.x, y: pt.y };
          return next;
        });
        return;
      }
      if (isPanning) {
        const svg = svgRef.current;
        if (!svg) return;
        const rect = svg.getBoundingClientRect();
        const dx = ((e.clientX - panStart.current.x) / rect.width) * viewBox.w;
        const dy = ((e.clientY - panStart.current.y) / rect.height) * viewBox.h;
        setViewBox((vb) => ({ ...vb, x: panStart.current.vx - dx, y: panStart.current.vy - dy }));
      }
    },
    [dragNode, isPanning, svgPoint, viewBox.w, viewBox.h]
  );

  const handleMouseUp = useCallback(() => {
    setDragNode(null);
    setIsPanning(false);
  }, []);

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    const scaleFactor = e.deltaY > 0 ? 1.1 : 0.9;
    setViewBox((vb) => {
      const svg = svgRef.current;
      if (!svg) return vb;
      const rect = svg.getBoundingClientRect();
      const mx = vb.x + ((e.clientX - rect.left) / rect.width) * vb.w;
      const my = vb.y + ((e.clientY - rect.top) / rect.height) * vb.h;
      const nw = vb.w * scaleFactor;
      const nh = vb.h * scaleFactor;
      return {
        x: mx - (mx - vb.x) * (nw / vb.w),
        y: my - (my - vb.y) * (nh / vb.h),
        w: nw,
        h: nh,
      };
    });
  }, []);

  const resetView = useCallback(() => {
    setViewBox({ x: 0, y: 0, w: 800, h: 400 });
  }, []);

  if (!visible) {
    return (
      <div className="rounded-xl border border-border bg-surface overflow-hidden">
        <div className="flex items-center justify-between px-5 py-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <Network className="h-4 w-4 text-accent" />
            Workflow Graph
          </div>
          <button
            onClick={() => setVisible(true)}
            className="text-xs text-muted hover:text-foreground transition-colors"
          >
            Show
          </button>
        </div>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="rounded-xl border border-border bg-surface overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-5 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Network className="h-4 w-4 text-accent" />
          Workflow Graph
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={resetView}
            title="Reset view"
            className="flex items-center justify-center rounded-lg border border-border px-2 py-1.5 text-xs text-muted hover:text-foreground hover:bg-border/40 transition-colors"
          >
            <Maximize2 className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => setVisible(false)}
            className="text-xs text-muted hover:text-foreground transition-colors"
          >
            Hide
          </button>
        </div>
      </div>
      <svg
        ref={svgRef}
        viewBox={`${viewBox.x} ${viewBox.y} ${viewBox.w} ${viewBox.h}`}
        className="w-full select-none"
        style={{ height: 400, cursor: isPanning ? "grabbing" : dragNode !== null ? "grabbing" : "grab" }}
        onMouseDown={(e) => handleMouseDown(e)}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onWheel={handleWheel}
      >
        <defs>
          <marker
            id="dep-arrow"
            viewBox="0 0 10 6"
            refX="10"
            refY="3"
            markerWidth="8"
            markerHeight="6"
            orient="auto"
          >
            <path d="M0,0 L10,3 L0,6" fill="var(--color-border, #555)" opacity="0.4" />
          </marker>
        </defs>

        {edges.map((edge, i) => {
          const s = positions[edge.source];
          const t = positions[edge.target];
          if (!s || !t) return null;
          const mx = (s.x + t.x) / 2;
          const my = (s.y + t.y) / 2;
          const dx = t.x - s.x;
          const dy = t.y - s.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          const offset = dist * 0.15;
          const nx = -dy / (dist || 1);
          const ny = dx / (dist || 1);
          const cx = mx + nx * offset;
          const cy = my + ny * offset;

          const highlighted = connectedEdges.has(i);
          const dimmed = hoveredNode !== null && !highlighted;

          return (
            <path
              key={`edge-${i}`}
              d={`M${s.x},${s.y} Q${cx},${cy} ${t.x},${t.y}`}
              fill="none"
              stroke={highlighted ? "var(--color-accent, #3b82f6)" : "var(--color-border, #555)"}
              strokeWidth={highlighted ? 2 : 1.5}
              strokeOpacity={dimmed ? 0.15 : highlighted ? 0.8 : 0.4}
              markerEnd="url(#dep-arrow)"
              className="transition-all duration-200"
            />
          );
        })}

        {nodes.map((node, i) => {
          const pos = positions[i];
          if (!pos) return null;
          const { fill, stroke } = statusColor(node.status);
          const dimmed = hoveredNode !== null && !connectedNodes.has(i);
          const isHovered = hoveredNode === i;
          const label = truncateLabel(node.name, 16);

          return (
            <g
              key={`node-${i}`}
              className="transition-opacity duration-200"
              style={{ opacity: dimmed ? 0.25 : 1, cursor: "pointer" }}
              onMouseDown={(e) => handleMouseDown(e, i)}
              onMouseEnter={() => setHoveredNode(i)}
              onMouseLeave={() => setHoveredNode(null)}
              onClick={(e) => {
                if (dragNode !== null) return;
                e.stopPropagation();
                const slug = node.fileName.replace(".yaml", "");
                navigate(`/workflows/${slug}`);
              }}
            >
              {node.status === "running" && (
                <circle cx={pos.x} cy={pos.y} r={node.radius + 4} fill="none" stroke={stroke} strokeWidth={2}>
                  <animate attributeName="r" from={node.radius + 2} to={node.radius + 10} dur="1.5s" repeatCount="indefinite" />
                  <animate attributeName="stroke-opacity" from="0.6" to="0" dur="1.5s" repeatCount="indefinite" />
                </circle>
              )}
              <circle
                cx={pos.x}
                cy={pos.y}
                r={isHovered ? node.radius + 3 : node.radius}
                fill={fill}
                stroke={stroke}
                strokeWidth={isHovered ? 2.5 : 1.5}
                className="transition-all duration-150"
              />
              <text
                x={pos.x}
                y={pos.y + 3}
                textAnchor="middle"
                fill="var(--color-foreground, #e0e0e0)"
                fontSize={10}
                fontWeight={500}
                style={{ pointerEvents: "none" }}
              >
                {node.stepsCount}
              </text>
              <text
                x={pos.x}
                y={pos.y + node.radius + 14}
                textAnchor="middle"
                fill="var(--color-muted, #888)"
                fontSize={9}
                style={{ pointerEvents: "none" }}
              >
                {label}
              </text>

              {isHovered && (
                <g style={{ pointerEvents: "none" }}>
                  <rect
                    x={pos.x - 80}
                    y={pos.y - node.radius - 52}
                    width={160}
                    height={40}
                    rx={6}
                    fill="var(--color-background, #111)"
                    stroke="var(--color-border, #333)"
                    strokeWidth={1}
                    opacity={0.95}
                  />
                  <text x={pos.x} y={pos.y - node.radius - 35} textAnchor="middle" fill="var(--color-foreground, #e0e0e0)" fontSize={10} fontWeight={600}>
                    {truncateLabel(node.name, 24)}
                  </text>
                  <text x={pos.x} y={pos.y - node.radius - 21} textAnchor="middle" fill="var(--color-muted, #888)" fontSize={9}>
                    {node.stepsCount} steps - {node.status || "idle"}
                  </text>
                </g>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
