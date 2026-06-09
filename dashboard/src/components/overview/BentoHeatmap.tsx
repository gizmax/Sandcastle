import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { cn } from "@/lib/utils";
import type { HeatmapCell } from "./bentoTypes";

// dayOfWeek: 0=Monday ... 6=Sunday - label Mon/Wed/Fri on their rows.
const DAY_LABELS = ["Mon", "", "Wed", "", "Fri", "", ""];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const GAP = 3;
const DAY_GUTTER = 34; // room for the Mon/Wed/Fri column

/** 0..4 intensity bucket for a count. */
function level(count: number, max: number): number {
  if (count <= 0) return 0;
  const r = count / max;
  if (r < 0.25) return 1;
  if (r < 0.5) return 2;
  if (r < 0.75) return 3;
  return 4;
}

const LEVEL_CLASS = [
  "bg-foreground/[0.06]", // 0 - empty, subtle
  "bg-accent/30",
  "bg-accent/55",
  "bg-accent/80",
  "bg-accent shadow-[0_0_8px_-1px] shadow-accent/70", // 4 - hottest, glows
];

export function BentoHeatmap({ cells }: { cells: HeatmapCell[] }) {
  const navigate = useNavigate();
  const [tooltip, setTooltip] = useState<{ x: number; y: number; text: string } | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);

  // Measure the container so cells fill the full card width (crisp squares,
  // sized to the data) instead of leaving the right side empty.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    setWidth(el.clientWidth);
    if (typeof ResizeObserver === "undefined") return; // jsdom / SSR safety
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setWidth(e.contentRect.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const { weeks, maxCount, monthLabels, total, busiest } = useMemo(() => {
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
    const total = cells.reduce((s, c) => s + c.count, 0);
    const busiest = cells.reduce<HeatmapCell | null>(
      (best, c) => (c.count > (best?.count ?? 0) ? c : best),
      null,
    );

    const monthLabels: { col: number; label: string }[] = [];
    let prevMonth = -1;
    for (let col = 0; col < weeks.length; col++) {
      const firstCell = weeks[col].find((c) => c !== null);
      if (firstCell) {
        const month = parseInt(firstCell.date.slice(5, 7), 10) - 1;
        if (month !== prevMonth) {
          monthLabels.push({ col, label: MONTHS[month] });
          prevMonth = month;
        }
      }
    }
    return { weeks, maxCount, monthLabels, total, busiest };
  }, [cells]);

  // Size each cell so the grid fills the available width.
  const cell =
    width <= 0 || weeks.length === 0
      ? 15
      : Math.max(9, Math.min(22, Math.floor((width - DAY_GUTTER - (weeks.length - 1) * GAP) / weeks.length)));
  const step = cell + GAP;

  return (
    <div
      className={cn(
        "bg-surface rounded-2xl shadow-sm border border-border",
        "hover:border-accent/30 transition-settle",
        "p-6",
      )}
    >
      <div className="mb-4 flex items-end justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-foreground">Run activity</h3>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            {total.toLocaleString()} runs · last 52 weeks
            {busiest && busiest.count > 0 && (
              <> · busiest {busiest.date.slice(5)} ({busiest.count})</>
            )}
          </p>
        </div>
        <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
          <span>Less</span>
          {LEVEL_CLASS.map((c, i) => (
            <div key={i} className={cn("h-[10px] w-[10px] rounded-[3px]", c)} />
          ))}
          <span>More</span>
        </div>
      </div>

      <div ref={wrapRef} className="flex gap-2">
        {/* Day-of-week labels */}
        <div className="flex flex-col" style={{ gap: GAP, paddingTop: 18, width: DAY_GUTTER - 8 }}>
          {DAY_LABELS.map((label, i) => (
            <div
              key={i}
              className="flex items-center text-[10px] leading-none text-muted-foreground"
              style={{ height: cell }}
            >
              {label}
            </div>
          ))}
        </div>

        {/* Month labels + grid */}
        <div className="relative flex-1 min-w-0">
          <div className="relative" style={{ height: 18 }}>
            {monthLabels.map((m) => (
              <span
                key={`${m.col}-${m.label}`}
                className="absolute top-0 text-[10px] font-medium text-muted-foreground"
                style={{ left: m.col * step }}
              >
                {m.label}
              </span>
            ))}
          </div>

          <div className="flex" style={{ gap: GAP }}>
            {weeks.map((week, col) => (
              <div key={col} className="flex flex-col" style={{ gap: GAP }}>
                {week.map((c, row) => {
                  if (!c) {
                    return <div key={row} style={{ width: cell, height: cell }} />;
                  }
                  const lvl = level(c.count, maxCount);
                  return (
                    <div
                      key={row}
                      className={cn(
                        "rounded-[3px] cursor-pointer outline-none",
                        "transition-[transform,box-shadow] duration-150 ease-out",
                        "hover:scale-[1.35] hover:ring-2 hover:ring-accent/60 hover:z-10",
                        LEVEL_CLASS[lvl],
                      )}
                      style={{ width: cell, height: cell }}
                      onClick={() => navigate(`/runs?date=${c.date}`)}
                      onMouseEnter={(e) => {
                        const rect = e.currentTarget.getBoundingClientRect();
                        setTooltip({
                          x: rect.left + rect.width / 2,
                          y: rect.top - 8,
                          text: `${c.date}: ${c.count} run${c.count !== 1 ? "s" : ""}`,
                        });
                      }}
                      onMouseLeave={() => setTooltip(null)}
                    />
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      </div>

      {tooltip && (
        <div
          className="pointer-events-none fixed z-50 rounded-lg bg-foreground px-2.5 py-1.5 text-[11px] font-medium text-background shadow-xl"
          style={{ left: tooltip.x, top: tooltip.y, transform: "translate(-50%, -100%)" }}
        >
          {tooltip.text}
        </div>
      )}
    </div>
  );
}
