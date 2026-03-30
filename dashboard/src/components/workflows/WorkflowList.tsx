import { useMemo } from "react";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { WorkflowCard } from "@/components/workflows/WorkflowCard";
import type { WorkflowStats } from "@/components/workflows/WorkflowCard";
import { usePinnedWorkflows } from "@/hooks/usePinnedWorkflows";

interface WorkflowInfo {
  name: string;
  description: string;
  steps_count: number;
  file_name: string;
  version?: number | null;
  version_status?: string | null;
  total_versions?: number | null;
}

interface WorkflowListProps {
  workflows: WorkflowInfo[];
  selectedNames?: Set<string>;
  onSelectionChange?: (names: Set<string>) => void;
  onRun: (workflow: WorkflowInfo) => void;
  onBatch?: (workflow: WorkflowInfo) => void;
  onEdit: (workflow: WorkflowInfo) => void;
  onViewDag: (workflow: WorkflowInfo) => void;
  onViewVersions?: (workflow: WorkflowInfo) => void;
}

export function WorkflowList({ workflows, selectedNames, onSelectionChange, onRun, onBatch, onEdit, onViewDag, onViewVersions }: WorkflowListProps) {
  const allSelected = selectedNames != null && workflows.length > 0 && workflows.every((wf) => selectedNames.has(wf.file_name.replace(".yaml", "")));
  const { isPinned, togglePin } = usePinnedWorkflows();

  // Stats are only shown when a real per-workflow stats API endpoint
  // is available. Until then, cards render without the metrics row.
  const statsMap = useMemo(
    () => new Map<string, WorkflowStats>(),
    [],
  );

  const handleSelectAll = () => {
    if (!onSelectionChange) return;
    if (allSelected) {
      onSelectionChange(new Set());
    } else {
      onSelectionChange(new Set(workflows.map((wf) => wf.file_name.replace(".yaml", ""))));
    }
  };

  const handleToggle = (wfName: string) => {
    if (!onSelectionChange || !selectedNames) return;
    const next = new Set(selectedNames);
    if (next.has(wfName)) {
      next.delete(wfName);
    } else {
      next.add(wfName);
    }
    onSelectionChange(next);
  };

  return (
    <div className="space-y-3" data-testid="workflow-list">
      {onSelectionChange && (
        <button
          type="button"
          onClick={handleSelectAll}
          className="flex items-center gap-2 text-xs text-muted hover:text-foreground transition-colors"
        >
          <span
            className={cn(
              "flex h-4 w-4 items-center justify-center rounded border transition-colors",
              allSelected
                ? "border-accent bg-accent text-accent-foreground"
                : "border-border bg-background text-transparent"
            )}
          >
            <Check className="h-2.5 w-2.5" />
          </span>
          Select all ({workflows.length})
        </button>
      )}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {workflows.map((wf) => {
          const wfKey = wf.file_name.replace(".yaml", "");
          return (
            <WorkflowCard
              key={wf.file_name}
              name={wf.name}
              description={wf.description}
              stepsCount={wf.steps_count}
              fileName={wf.file_name}
              version={wf.version}
              versionStatus={wf.version_status}
              totalVersions={wf.total_versions}
              selected={selectedNames?.has(wfKey)}
              pinned={isPinned(wf.name)}
              stats={statsMap.get(wf.file_name)}
              onSelect={onSelectionChange ? () => handleToggle(wfKey) : undefined}
              onTogglePin={() => togglePin(wf.name)}
              onRun={() => onRun(wf)}
              onBatch={onBatch ? () => onBatch(wf) : undefined}
              onEdit={() => onEdit(wf)}
              onViewDag={() => onViewDag(wf)}
              onViewVersions={onViewVersions ? () => onViewVersions(wf) : undefined}
            />
          );
        })}
      </div>
    </div>
  );
}
