import { PlayCircle } from "lucide-react";
import { EmptyState } from "@/components/shared/EmptyState";

export default function Runs() {
  return (
    <div>
      <h1 className="mb-6 text-2xl font-semibold tracking-tight text-foreground">Runs</h1>
      <EmptyState
        icon={PlayCircle}
        title="No runs yet"
        description="Run a workflow to see execution history here."
      />
    </div>
  );
}
