import { GitBranch } from "lucide-react";
import { EmptyState } from "@/components/shared/EmptyState";

export default function Workflows() {
  return (
    <div>
      <h1 className="mb-6 text-2xl font-semibold tracking-tight text-foreground">Workflows</h1>
      <EmptyState
        icon={GitBranch}
        title="No workflows found"
        description="Build your first workflow!"
        action={{ label: "Create Workflow", onClick: () => window.location.assign("/workflows/builder") }}
      />
    </div>
  );
}
