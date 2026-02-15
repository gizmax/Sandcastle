import { Castle } from "lucide-react";
import { EmptyState } from "@/components/shared/EmptyState";

export default function Overview() {
  return (
    <div>
      <h1 className="mb-6 text-2xl font-semibold tracking-tight text-foreground">Overview</h1>
      <EmptyState
        icon={Castle}
        title="No runs yet"
        description="Create your first workflow to get started."
        action={{ label: "Create Workflow", onClick: () => window.location.assign("/workflows/builder") }}
      />
    </div>
  );
}
