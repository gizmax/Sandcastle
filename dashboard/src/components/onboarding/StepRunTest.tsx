import { CheckCircle, Rocket } from "lucide-react";
import { cn } from "@/lib/utils";

interface StepRunTestProps {
  onComplete: () => void;
}

export function StepRunTest({ onComplete }: StepRunTestProps) {
  return (
    <div className="space-y-6 text-center">
      <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-success/10">
        <CheckCircle className="h-8 w-8 text-success" />
      </div>
      <div>
        <h2 className="text-lg font-semibold text-foreground">You're all set!</h2>
        <p className="mt-1 text-sm text-muted">
          Sandcastle is ready. Start running workflows from the dashboard.
        </p>
      </div>

      <button
        onClick={onComplete}
        className={cn(
          "inline-flex items-center gap-2 rounded-lg bg-accent px-6 py-2.5 text-sm font-medium text-accent-foreground",
          "hover:bg-accent-hover transition-all duration-200 shadow-sm hover:shadow-md"
        )}
      >
        <Rocket className="h-4 w-4" />
        Go to Dashboard
      </button>
    </div>
  );
}
