import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { RotateCcw, GitCompareArrows, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { ActionMenu, type ActionMenuItem } from "@/components/shared/ActionMenu";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";

interface RunRowActionsProps {
  runId: string;
  status: string;
  onChanged?: () => void;
}

interface RunStepsLite {
  steps: { step_id: string; status: string }[] | null;
}

/**
 * Compact contextual action menu for a Runs list row — mirrors the run detail
 * page actions (re-run / replay failed / compare / delete) so the pattern is
 * consistent across objects. The list rows don't carry step data, so re-run and
 * replay resolve the target step by fetching the run on demand.
 *
 * Extension point: add future row actions (e.g. "Heal failure") to `items`
 * below — one line each, exactly like the detail page. See ActionMenu.
 */
export function RunRowActions({ runId, status, onChanged }: RunRowActionsProps) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const isRunning = ["running", "queued"].includes(status);
  const isFailed = status === "failed" || status === "partial";

  // Resolve the right step then replay. `failedOnly` targets the first failed
  // step; otherwise we re-run from the first step.
  const replay = useCallback(
    async (failedOnly: boolean) => {
      if (busy) return;
      setBusy(true);
      const detail = await api.get<RunStepsLite>(`/runs/${runId}`);
      const steps = detail.data?.steps ?? [];
      const target = failedOnly
        ? steps.find((s) => s.status === "failed")?.step_id
        : steps[0]?.step_id;
      if (!target) {
        toast.error("No replayable step found for this run");
        setBusy(false);
        return;
      }
      const res = await api.post<{ new_run_id: string }>(`/runs/${runId}/replay`, {
        from_step: target,
      });
      setBusy(false);
      if (res.error) {
        toast.error(`Replay failed: ${res.error.message}`);
      } else if (res.data?.new_run_id) {
        toast.success(failedOnly ? "Replaying failed step" : "Re-run started");
        navigate(`/runs/${res.data.new_run_id}`);
      }
    },
    [busy, runId, navigate]
  );

  const handleDelete = useCallback(async () => {
    const res = await api.delete(`/runs/${runId}`);
    setConfirmDelete(false);
    if (res.error) {
      toast.error(`Delete failed: ${res.error.message}`);
    } else {
      toast.success("Run deleted");
      onChanged?.();
    }
  }, [runId, onChanged]);

  const items: ActionMenuItem[] = [];
  if (!isRunning) {
    if (isFailed) {
      items.push({
        id: "replay-failed",
        label: "Replay failed step",
        icon: RotateCcw,
        disabled: busy,
        onSelect: () => void replay(true),
      });
    }
    items.push({
      id: "rerun",
      label: "Re-run",
      icon: RotateCcw,
      disabled: busy,
      onSelect: () => void replay(false),
    });
  }
  items.push({
    id: "compare",
    label: "Compare with another run...",
    icon: GitCompareArrows,
    onSelect: () => navigate(`/runs/compare?run_a=${runId}`),
  });
  if (!isRunning) {
    items.push({
      id: "delete",
      label: "Delete",
      icon: Trash2,
      danger: true,
      onSelect: () => setConfirmDelete(true),
    });
  }
  // ── Extension point: future per-row actions slot in here, one line each. ──

  return (
    <>
      <ActionMenu items={items} menuLabel={`Actions for run ${runId.slice(0, 8)}`} size="sm" />
      <ConfirmDialog
        open={confirmDelete}
        title="Delete Run"
        description={`Delete run ${runId.slice(0, 8)}...? This cannot be undone.`}
        confirmLabel="Delete"
        variant="danger"
        onConfirm={handleDelete}
        onCancel={() => setConfirmDelete(false)}
      />
    </>
  );
}
