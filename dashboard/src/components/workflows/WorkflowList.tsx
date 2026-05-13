import { useEffect, useMemo, useState } from "react";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { WorkflowCard } from "@/components/workflows/WorkflowCard";
import type { WorkflowStats } from "@/components/workflows/WorkflowCard";
import { usePinnedWorkflows } from "@/hooks/usePinnedWorkflows";
import { api } from "@/api/client";

interface WorkflowStatsApiEntry {
  name: string;
  total_runs: number;
  success_rate: number;
  avg_cost_usd: number;
  last_run_status: string | null;
  last_run_ago: string | null;
}

function mapStatus(
  raw: string | null
): WorkflowStats["lastRunStatus"] {
  if (raw === "completed") return "completed";
  if (raw === "failed") return "failed";
  if (raw === "running" || raw === "queued") return "running";
  return null;
}

interface WorkflowInfo {
  name: string;
  description: string;
  steps_count: number;
  file_name: string;
  version?: number | null;
  version_status?: string | null;
  total_versions?: number | null;
  steps?: Array<{ id: string; owner?: string; [key: string]: unknown }>;
  doctor_status?: "ok" | "warning" | "blocked" | null;
  doctor_risk?: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL" | null;
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

  const [statsByName, setStatsByName] = useState<Map<string, WorkflowStats>>(
    () => new Map()
  );

  useEffect(() => {
    let cancelled = false;
    api
      .get<WorkflowStatsApiEntry[]>("/workflows/stats")
      .then((res) => {
        if (cancelled || !res.data) return;
        const next = new Map<string, WorkflowStats>();
        for (const row of res.data) {
          next.set(row.name, {
            totalRuns: row.total_runs ?? 0,
            successRate: row.success_rate ?? 0,
            avgCost: row.avg_cost_usd ?? 0,
            lastRunStatus: mapStatus(row.last_run_status),
            lastRunAgo: row.last_run_ago,
          });
        }
        setStatsByName(next);
      })
      .catch(() => {
        // Stats are optional — render cards without metrics on failure.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const statsMap = useMemo(() => statsByName, [statsByName]);

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
          // Derive unique owners from workflow step definitions
          const owners = wf.steps
            ? [...new Set(wf.steps.map((s) => s.owner).filter((o): o is string => !!o))]
            : undefined;
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
              stats={statsMap.get(wf.name)}
              owners={owners}
              doctorStatus={wf.doctor_status}
              doctorRisk={wf.doctor_risk}
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
