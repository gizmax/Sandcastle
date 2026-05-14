import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { cn } from "@/lib/utils";
import type { HeatmapCell } from "./bentoTypes";

const DAY_LABELS = ["M", "", "W", "", "F", "", ""];

function getHeatmapIntensityClass(count: number, maxCount: number): string {
  if (count === 0) return "bg-border";
  const ratio = count / maxCount;
  if (ratio < 0.25) return "bg-accent/20";
  if (ratio < 0.5) return "bg-accent/40";
  if (ratio < 0.75) return "bg-accent/70";
  return "bg-accent";
}

export function BentoHeatmap({ cells }: { cells: HeatmapCell[] }) {
  const navigate = useNavigate();
  const [tooltip, setTooltip] = useState<{ x: number; y: number; text: string } | null>(null);

  const weeks: (HeatmapCell | null)[][] = [];
  let currentWeek: (HeatmapCell | null)[] = Array(7).fill(null);

  for (const cell of cells) {
    const row = cell.dayOfWeek;
    if (row === 0 && currentWeek.some((c) => c !== null)) {
      weeks.push(currentWeek);
      currentWeek = Array(7).fill(null);
    }
    currentWeek[row] = cell;
  }
  if (currentWeek.some((c) => c !== null)) weeks.push(currentWeek);

  const maxCount = Math.max(1, ...cells.map((c) => c.count));

  const monthLabels: { col: number; label: string }[] = [];
  const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  let prevMonth = -1;
  for (let col = 0; col < weeks.length; col++) {
    const firstCell = weeks[col].find((c) => c !== null);
    if (firstCell) {
      const month = parseInt(firstCell.date.slice(5, 7), 10) - 1;
      if (month !== prevMonth) {
        monthLabels.push({ col, label: monthNames[month] });
        prevMonth = month;
      }
    }
  }

  return (
    <div className={cn(
      "bg-surface rounded-2xl shadow-sm border border-border",
      "hover:border-accent/30 transition-all duration-300",
      "p-6",
    )}>
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-foreground">Activity</h3>
        <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
          <span>Less</span>
          <div className="h-[9px] w-[9px] rounded-[2px] bg-border" />
          <div className="h-[9px] w-[9px] rounded-[2px] bg-accent/20" />
          <div className="h-[9px] w-[9px] rounded-[2px] bg-accent/40" />
          <div className="h-[9px] w-[9px] rounded-[2px] bg-accent/70" />
          <div className="h-[9px] w-[9px] rounded-[2px] bg-accent" />
          <span>More</span>
        </div>
      </div>

      <div className="relative overflow-x-auto">
        <div className="flex gap-[3px]" style={{ paddingLeft: "28px" }}>
          {weeks.map((_, col) => {
            const ml = monthLabels.find((m) => m.col === col);
            return (
              <div key={col} className="flex-1 text-[10px] text-muted-foreground min-w-0">
                {ml ? ml.label : ""}
              </div>
            );
          })}
        </div>

        <div className="flex gap-0">
          <div className="flex flex-col gap-[3px] pr-1.5 pt-[3px]" style={{ width: "28px", minWidth: "28px" }}>
            {DAY_LABELS.map((label, i) => (
              <div key={i} className="flex h-[14px] items-center text-[10px] leading-none text-muted-foreground">
                {label}
              </div>
            ))}
          </div>

          <div className="flex gap-[3px] flex-1">
            {weeks.map((week, col) => (
              <div key={col} className="flex flex-col gap-[3px] flex-1">
                {week.map((cell, row) => (
                  <div
                    key={row}
                    className={cn(
                      "h-[14px] rounded-[3px] transition-colors duration-100",
                      cell ? "cursor-pointer" : "cursor-default",
                      cell ? getHeatmapIntensityClass(cell.count, maxCount) : "bg-transparent",
                    )}
                    onClick={() => {
                      if (cell) navigate(`/runs?date=${cell.date}`);
                    }}
                    onMouseEnter={(e) => {
                      if (!cell) return;
                      const rect = e.currentTarget.getBoundingClientRect();
                      setTooltip({
                        x: rect.left + rect.width / 2,
                        y: rect.top - 8,
                        text: `${cell.date}: ${cell.count} run${cell.count !== 1 ? "s" : ""}`,
                      });
                    }}
                    onMouseLeave={() => setTooltip(null)}
                  />
                ))}
              </div>
            ))}
          </div>
        </div>
      </div>

      {tooltip && (
        <div
          className="pointer-events-none fixed z-50 rounded-lg bg-foreground px-2.5 py-1.5 text-[11px] font-medium text-background shadow-xl"
          style={{
            left: tooltip.x, top: tooltip.y,
            transform: "translate(-50%, -100%)",
          }}
        >
          {tooltip.text}
        </div>
      )}
    </div>
  );
}
