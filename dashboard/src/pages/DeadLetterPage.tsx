import { Inbox } from "lucide-react";
import { EmptyState } from "@/components/shared/EmptyState";

export default function DeadLetterPage() {
  return (
    <div>
      <h1 className="mb-6 text-2xl font-semibold tracking-tight text-foreground">Dead Letter Queue</h1>
      <EmptyState
        icon={Inbox}
        title="No dead letters"
        description="Failed steps that exceed retry limits will appear here."
      />
    </div>
  );
}
