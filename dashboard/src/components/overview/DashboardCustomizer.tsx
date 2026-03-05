import { useRef, useState } from "react";
import { GripVertical, RotateCcw } from "lucide-react";
import { type WidgetConfig, WIDGET_LABELS } from "@/hooks/useDashboardLayout";
import { cn } from "@/lib/utils";

interface Props {
  widgets: WidgetConfig[];
  onMove: (dragId: string, dropId: string) => void;
  onToggle: (id: string) => void;
  onReset: () => void;
}

export function DashboardCustomizer({ widgets, onMove, onToggle, onReset }: Props) {
  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);
  const [overEdge, setOverEdge] = useState<"top" | "bottom" | null>(null);
  const itemRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  const sorted = [...widgets].sort((a, b) => a.order - b.order);

  function handleDragStart(e: React.DragEvent, id: string) {
    setDragId(id);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", id);
  }

  function handleDragOver(e: React.DragEvent, id: string) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (id === dragId) {
      setOverId(null);
      setOverEdge(null);
      return;
    }
    const rect = e.currentTarget.getBoundingClientRect();
    const midY = rect.top + rect.height / 2;
    setOverId(id);
    setOverEdge(e.clientY < midY ? "top" : "bottom");
  }

  function handleDrop(e: React.DragEvent, id: string) {
    e.preventDefault();
    const srcId = e.dataTransfer.getData("text/plain");
    if (srcId && srcId !== id) {
      onMove(srcId, id);
    }
    setDragId(null);
    setOverId(null);
    setOverEdge(null);
  }

  function handleDragEnd() {
    setDragId(null);
    setOverId(null);
    setOverEdge(null);
  }

  return (
    <div className="w-64 rounded-xl border border-border bg-surface p-3 shadow-lg">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">
        Widgets
      </div>
      <div className="space-y-1">
        {sorted.map((w) => (
          <div
            key={w.id}
            ref={(el) => {
              if (el) itemRefs.current.set(w.id, el);
              else itemRefs.current.delete(w.id);
            }}
            draggable
            onDragStart={(e) => handleDragStart(e, w.id)}
            onDragOver={(e) => handleDragOver(e, w.id)}
            onDrop={(e) => handleDrop(e, w.id)}
            onDragEnd={handleDragEnd}
            className={cn(
              "flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm transition-all",
              "select-none cursor-grab active:cursor-grabbing",
              dragId === w.id && "opacity-40",
              overId === w.id && overEdge === "top" && "border-t-2 border-t-accent",
              overId === w.id && overEdge === "bottom" && "border-b-2 border-b-accent",
              overId !== w.id && "border-t-2 border-t-transparent border-b-2 border-b-transparent",
            )}
          >
            <GripVertical className="h-3.5 w-3.5 shrink-0 text-muted" />
            <span
              className={cn(
                "flex-1 truncate",
                w.visible ? "text-foreground" : "text-muted line-through",
              )}
            >
              {WIDGET_LABELS[w.id] ?? w.id}
            </span>
            <button
              onClick={() => onToggle(w.id)}
              className={cn(
                "relative h-5 w-9 shrink-0 rounded-full transition-colors",
                w.visible ? "bg-accent" : "bg-border",
              )}
              aria-label={w.visible ? `Hide ${WIDGET_LABELS[w.id]}` : `Show ${WIDGET_LABELS[w.id]}`}
            >
              <span
                className={cn(
                  "absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform",
                  w.visible ? "translate-x-4" : "translate-x-0.5",
                )}
              />
            </button>
          </div>
        ))}
      </div>
      <button
        onClick={onReset}
        className="mt-3 flex w-full items-center justify-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-muted transition-colors hover:text-foreground hover:border-foreground/20"
      >
        <RotateCcw className="h-3 w-3" />
        Reset to default
      </button>
    </div>
  );
}
