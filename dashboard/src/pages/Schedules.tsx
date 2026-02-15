import { useCallback, useEffect, useState } from "react";
import { Calendar, Plus } from "lucide-react";
import { api } from "@/api/client";
import { ScheduleTable } from "@/components/schedules/ScheduleTable";
import { CreateScheduleModal } from "@/components/schedules/CreateScheduleModal";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { cn } from "@/lib/utils";

interface ScheduleItem {
  id: string;
  workflow_name: string;
  cron_expression: string;
  enabled: boolean;
  last_run_id: string | null;
  created_at: string | null;
}

export default function Schedules() {
  const [schedules, setSchedules] = useState<ScheduleItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);

  const fetchSchedules = useCallback(async () => {
    const res = await api.get<ScheduleItem[]>("/schedules");
    if (res.data) setSchedules(res.data);
    setLoading(false);
  }, []);

  useEffect(() => {
    void fetchSchedules();
  }, [fetchSchedules]);

  const handleToggle = useCallback(async (id: string, enabled: boolean) => {
    await api.patch(`/schedules/${id}`, { enabled });
    setSchedules((prev) =>
      prev.map((s) => (s.id === id ? { ...s, enabled } : s))
    );
  }, []);

  const handleDelete = useCallback(async (id: string) => {
    await api.delete(`/schedules/${id}`);
    setSchedules((prev) => prev.filter((s) => s.id !== id));
  }, []);

  const handleCreate = useCallback(
    async (data: {
      workflow_name: string;
      cron_expression: string;
      input_data: Record<string, unknown>;
    }) => {
      await api.post("/schedules", data);
      setModalOpen(false);
      fetchSchedules();
    },
    [fetchSchedules]
  );

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">Schedules</h1>
        <button
          onClick={() => setModalOpen(true)}
          className={cn(
            "flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground",
            "hover:bg-accent-hover transition-all duration-200",
            "shadow-sm hover:shadow-md"
          )}
        >
          <Plus className="h-4 w-4" />
          New Schedule
        </button>
      </div>

      {schedules.length === 0 ? (
        <EmptyState
          icon={Calendar}
          title="No schedules yet"
          description="Automate your first workflow!"
          action={{ label: "New Schedule", onClick: () => setModalOpen(true) }}
        />
      ) : (
        <ScheduleTable
          schedules={schedules}
          onToggle={handleToggle}
          onDelete={handleDelete}
        />
      )}

      <CreateScheduleModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onSubmit={handleCreate}
      />
    </div>
  );
}
