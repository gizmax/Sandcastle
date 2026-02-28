import { useCallback, useEffect, useState } from "react";
import { Calendar, Plus } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { ScheduleTable } from "@/components/schedules/ScheduleTable";
import { CreateScheduleModal } from "@/components/schedules/CreateScheduleModal";
import { EditScheduleModal } from "@/components/schedules/EditScheduleModal";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
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
  const [error, setError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editSchedule, setEditSchedule] = useState<ScheduleItem | null>(null);
  const [deleteId, setDeleteId] = useState<string | null>(null);

  const fetchSchedules = useCallback(async () => {
    try {
      setError(null);
      const res = await api.get<ScheduleItem[]>("/schedules");
      if (res.data) setSchedules(res.data);
    } catch {
      setError("Could not connect to the API server");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchSchedules();
  }, [fetchSchedules]);

  const handleToggle = useCallback(async (id: string, enabled: boolean) => {
    const res = await api.patch(`/schedules/${id}`, { enabled });
    if (res.error) {
      toast.error(`Failed to toggle schedule: ${res.error.message}`);
      return;
    }
    toast.success(`Schedule ${enabled ? "enabled" : "disabled"}`);
    setSchedules((prev) =>
      prev.map((s) => (s.id === id ? { ...s, enabled } : s))
    );
  }, []);

  const handleDelete = useCallback((id: string) => {
    setDeleteId(id);
  }, []);

  const confirmDelete = useCallback(async () => {
    if (!deleteId) return;
    const res = await api.delete(`/schedules/${deleteId}`);
    if (res.error) {
      toast.error(`Failed to delete schedule: ${res.error.message}`);
      setDeleteId(null);
      return;
    }
    toast.success("Schedule deleted");
    setSchedules((prev) => prev.filter((s) => s.id !== deleteId));
    setDeleteId(null);
  }, [deleteId]);

  const handleCreate = useCallback(
    async (data: {
      workflow_name: string;
      cron_expression: string;
      input_data: Record<string, unknown>;
    }) => {
      const res = await api.post("/schedules", data);
      if (res.error) {
        toast.error(`Failed to create schedule: ${res.error.message}`);
        return;
      }
      toast.success("Schedule created");
      setModalOpen(false);
      void fetchSchedules();
    },
    [fetchSchedules]
  );

  const handleEdit = useCallback(
    async (id: string, data: { cron_expression: string; enabled: boolean }) => {
      const res = await api.patch(`/schedules/${id}`, data);
      if (res.error) {
        toast.error(`Failed to update schedule: ${res.error.message}`);
        return;
      }
      toast.success("Schedule updated");
      setSchedules((prev) =>
        prev.map((s) => (s.id === id ? { ...s, ...data } : s))
      );
      setEditSchedule(null);
    },
    []
  );

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <h1 className="mb-4 sm:mb-6 text-xl sm:text-2xl font-semibold tracking-tight text-foreground">Schedules</h1>
        <div className="rounded-xl border border-error/30 bg-error/5 p-4">
          <p className="text-sm text-error">{error}</p>
          <button
            onClick={() => { setLoading(true); void fetchSchedules(); }}
            className="mt-2 text-xs font-medium text-accent hover:text-accent/80 transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4 sm:space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">Schedules</h1>
        <button
          onClick={() => setModalOpen(true)}
          aria-label="New schedule"
          className={cn(
            "flex items-center gap-2 rounded-lg bg-accent px-3 sm:px-4 py-2 text-sm font-medium text-accent-foreground",
            "hover:bg-accent-hover transition-all duration-200",
            "shadow-sm hover:shadow-md"
          )}
        >
          <Plus className="h-4 w-4" />
          <span className="hidden sm:inline">New Schedule</span>
        </button>
      </div>

      {schedules.length === 0 ? (
        <EmptyState
          icon={Calendar}
          title="No schedules yet"
          description="No schedules configured. Create a schedule to run workflows on a cron timer."
          action={{ label: "New Schedule", onClick: () => setModalOpen(true) }}
        />
      ) : (
        <ScheduleTable
          schedules={schedules}
          onToggle={handleToggle}
          onDelete={handleDelete}
          onEdit={setEditSchedule}
        />
      )}

      <CreateScheduleModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onSubmit={handleCreate}
      />

      {editSchedule && (
        <EditScheduleModal
          open={true}
          schedule={editSchedule}
          onClose={() => setEditSchedule(null)}
          onSubmit={handleEdit}
        />
      )}

      <ConfirmDialog
        open={!!deleteId}
        title="Delete Schedule"
        description="Are you sure you want to delete this schedule? This cannot be undone."
        confirmLabel="Delete"
        onConfirm={() => void confirmDelete()}
        onCancel={() => setDeleteId(null)}
      />
    </div>
  );
}
