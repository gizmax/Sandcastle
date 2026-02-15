import { Calendar } from "lucide-react";
import { EmptyState } from "@/components/shared/EmptyState";

export default function Schedules() {
  return (
    <div>
      <h1 className="mb-6 text-2xl font-semibold tracking-tight text-foreground">Schedules</h1>
      <EmptyState
        icon={Calendar}
        title="No schedules yet"
        description="Automate your first workflow!"
      />
    </div>
  );
}
