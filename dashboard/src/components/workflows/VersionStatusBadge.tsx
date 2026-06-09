import { StatusLed } from "@/components/ui/StatusLed";

/* Version lifecycle as indicator lights:
   draft = dim, staging = amber on, production = green on, archived = hollow. */
export function VersionStatusBadge({ status, className }: { status: string; className?: string }) {
  return <StatusLed status={status} className={className} />;
}
