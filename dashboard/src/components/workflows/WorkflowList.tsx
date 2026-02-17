import { WorkflowCard } from "@/components/workflows/WorkflowCard";

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
  onRun: (workflow: WorkflowInfo) => void;
  onEdit: (workflow: WorkflowInfo) => void;
  onViewDag: (workflow: WorkflowInfo) => void;
  onViewVersions?: (workflow: WorkflowInfo) => void;
}

export function WorkflowList({ workflows, onRun, onEdit, onViewDag, onViewVersions }: WorkflowListProps) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {workflows.map((wf) => (
        <WorkflowCard
          key={wf.file_name}
          name={wf.name}
          description={wf.description}
          stepsCount={wf.steps_count}
          fileName={wf.file_name}
          version={wf.version}
          versionStatus={wf.version_status}
          totalVersions={wf.total_versions}
          onRun={() => onRun(wf)}
          onEdit={() => onEdit(wf)}
          onViewDag={() => onViewDag(wf)}
          onViewVersions={onViewVersions ? () => onViewVersions(wf) : undefined}
        />
      ))}
    </div>
  );
}
