import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronUp, Clock, Calendar } from "lucide-react";
import { cn } from "@/lib/utils";
import { cronToHuman, getNextRuns, parseCron } from "@/lib/cron";
import type { ParsedCron } from "@/lib/cron";

type Frequency = ParsedCron["frequency"];

const FREQUENCIES: { value: Frequency; label: string }[] = [
  { value: "every_minute", label: "Every Minute" },
  { value: "hourly", label: "Hourly" },
  { value: "every_x_hours", label: "Every X Hours" },
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
  { value: "custom", label: "Custom" },
];

const WEEKDAYS = [
  { label: "Mon", value: 1 },
  { label: "Tue", value: 2 },
  { label: "Wed", value: 3 },
  { label: "Thu", value: 4 },
  { label: "Fri", value: 5 },
  { label: "Sat", value: 6 },
  { label: "Sun", value: 0 },
];

interface CronBuilderProps {
  value: string;
  onChange: (cron: string) => void;
}

function buildCron(state: {
  frequency: Frequency;
  hour: number;
  minute: number;
  interval: number;
  weekdays: number[];
  monthDays: number[];
  custom: string;
}): string {
  const m = state.minute;
  const h = state.hour;

  switch (state.frequency) {
    case "every_minute":
      return "* * * * *";
    case "hourly":
      return `${m} * * * *`;
    case "every_x_hours":
      return `${m} */${state.interval} * * *`;
    case "daily":
      return `${m} ${h} * * *`;
    case "weekly":
      return `${m} ${h} * * ${state.weekdays.sort((a, b) => a - b).join(",")}`;
    case "monthly":
      return `${m} ${h} ${[...state.monthDays].sort((a, b) => a - b).join(",")} * *`;
    case "custom":
      return state.custom;
  }
}

export function CronBuilder({ value, onChange }: CronBuilderProps) {
  const initial = parseCron(value);
  const [frequency, setFrequency] = useState<Frequency>(initial.frequency);
  const [hour, setHour] = useState(initial.hour);
  const [minute, setMinute] = useState(initial.minute);
  const [interval, setInterval] = useState(initial.interval);
  const [weekdays, setWeekdays] = useState<number[]>(initial.weekdays);
  const [monthDays, setMonthDays] = useState<number[]>(initial.monthDays);
  const [custom, setCustom] = useState(value || "0 9 * * *");
  const [showRawCron, setShowRawCron] = useState(false);

  // Re-sync internal state when parent changes the value prop
  useEffect(() => {
    const parsed = parseCron(value);
    setFrequency(parsed.frequency);
    setHour(parsed.hour);
    setMinute(parsed.minute);
    setInterval(parsed.interval);
    setWeekdays(parsed.weekdays);
    setMonthDays(parsed.monthDays);
    if (parsed.frequency === "custom") {
      setCustom(value);
    }
  }, [value]);

  useEffect(() => {
    const cron = buildCron({ frequency, hour, minute, interval, weekdays, monthDays, custom });
    onChange(cron);
  }, [frequency, hour, minute, interval, weekdays, monthDays, custom, onChange]);

  const currentCron = buildCron({ frequency, hour, minute, interval, weekdays, monthDays, custom });

  const nextRuns = useMemo(() => {
    return getNextRuns(currentCron, 3);
  }, [currentCron]);

  const humanDescription = useMemo(() => {
    return cronToHuman(currentCron);
  }, [currentCron]);

  const selectClass = cn(
    "h-9 rounded-lg border border-border bg-background px-3 text-sm text-foreground",
    "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30",
    "transition-colors"
  );

  return (
    <div className="space-y-3">
      {/* Label */}
      <label className="block text-xs font-medium text-muted">Schedule</label>

      {/* Frequency tabs */}
      <div className="flex flex-wrap gap-1.5">
        {FREQUENCIES.map((f) => (
          <button
            key={f.value}
            type="button"
            onClick={() => setFrequency(f.value)}
            className={cn(
              "rounded-lg px-2.5 py-1.5 text-xs font-medium transition-all duration-200",
              frequency === f.value
                ? "bg-accent text-accent-foreground shadow-sm"
                : "bg-surface border border-border text-muted hover:text-foreground hover:border-accent/30"
            )}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Hourly: minute picker */}
      {frequency === "hourly" && (
        <div>
          <label className="mb-1.5 block text-xs font-medium text-muted">At minute</label>
          <select
            value={minute}
            onChange={(e) => setMinute(Number(e.target.value))}
            className={cn(selectClass, "w-24")}
          >
            {Array.from({ length: 60 }, (_, i) => (
              <option key={i} value={i}>
                :{String(i).padStart(2, "0")}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Every X hours: interval + minute */}
      {frequency === "every_x_hours" && (
        <div className="space-y-2.5">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-muted">Every</label>
            <div className="flex items-center gap-2">
              <select
                value={interval}
                onChange={(e) => setInterval(Number(e.target.value))}
                className={cn(selectClass, "w-24")}
              >
                {[2, 3, 4, 6, 8, 12].map((h) => (
                  <option key={h} value={h}>
                    {h}
                  </option>
                ))}
              </select>
              <span className="text-sm text-muted">hours</span>
            </div>
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-muted">At minute</label>
            <select
              value={minute}
              onChange={(e) => setMinute(Number(e.target.value))}
              className={cn(selectClass, "w-24")}
            >
              {[0, 5, 10, 15, 20, 30, 45].map((m) => (
                <option key={m} value={m}>
                  :{String(m).padStart(2, "0")}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}

      {/* Time picker for daily/weekly/monthly */}
      {(frequency === "daily" || frequency === "weekly" || frequency === "monthly") && (
        <div>
          <label className="mb-1.5 block text-xs font-medium text-muted">Time (UTC)</label>
          <div className="flex items-center gap-2">
            <select
              value={hour}
              onChange={(e) => setHour(Number(e.target.value))}
              className={cn(selectClass, "w-20")}
            >
              {Array.from({ length: 24 }, (_, i) => (
                <option key={i} value={i}>
                  {String(i).padStart(2, "0")}
                </option>
              ))}
            </select>
            <span className="text-sm font-bold text-muted">:</span>
            <select
              value={minute}
              onChange={(e) => setMinute(Number(e.target.value))}
              className={cn(selectClass, "w-20")}
            >
              {[0, 5, 10, 15, 20, 30, 45].map((m) => (
                <option key={m} value={m}>
                  {String(m).padStart(2, "0")}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}

      {/* Weekday picker */}
      {frequency === "weekly" && (
        <div>
          <label className="mb-1.5 block text-xs font-medium text-muted">Days of week</label>
          <div className="flex flex-wrap gap-1.5">
            {WEEKDAYS.map((day) => {
              const active = weekdays.includes(day.value);
              return (
                <button
                  key={day.value}
                  type="button"
                  onClick={() => {
                    if (active) {
                      if (weekdays.length > 1) {
                        setWeekdays(weekdays.filter((d) => d !== day.value));
                      }
                    } else {
                      setWeekdays([...weekdays, day.value]);
                    }
                  }}
                  className={cn(
                    "rounded-lg px-3 py-1.5 text-xs font-medium transition-all duration-200",
                    active
                      ? "bg-accent text-accent-foreground shadow-sm"
                      : "bg-surface border border-border text-muted hover:text-foreground hover:border-accent/30"
                  )}
                >
                  {day.label}
                </button>
              );
            })}
          </div>
          <div className="flex gap-3 mt-2">
            <button
              type="button"
              onClick={() => setWeekdays([1, 2, 3, 4, 5])}
              className="text-[11px] text-accent/70 hover:text-accent transition-colors"
            >
              Weekdays
            </button>
            <button
              type="button"
              onClick={() => setWeekdays([0, 6])}
              className="text-[11px] text-accent/70 hover:text-accent transition-colors"
            >
              Weekends
            </button>
            <button
              type="button"
              onClick={() => setWeekdays([0, 1, 2, 3, 4, 5, 6])}
              className="text-[11px] text-accent/70 hover:text-accent transition-colors"
            >
              Every day
            </button>
          </div>
        </div>
      )}

      {/* Month day grid picker */}
      {frequency === "monthly" && (
        <div>
          <label className="mb-1.5 block text-xs font-medium text-muted">Days of month</label>
          <div className="grid grid-cols-7 gap-1">
            {Array.from({ length: 31 }, (_, i) => {
              const day = i + 1;
              const active = monthDays.includes(day);
              return (
                <button
                  key={day}
                  type="button"
                  onClick={() => {
                    if (active) {
                      if (monthDays.length > 1) {
                        setMonthDays(monthDays.filter((d) => d !== day));
                      }
                    } else {
                      setMonthDays([...monthDays, day]);
                    }
                  }}
                  className={cn(
                    "rounded-lg py-1.5 text-xs font-medium transition-all duration-200",
                    active
                      ? "bg-accent text-accent-foreground shadow-sm"
                      : "bg-surface border border-border text-muted hover:text-foreground hover:border-accent/30"
                  )}
                >
                  {day}
                </button>
              );
            })}
          </div>
          <div className="flex gap-3 mt-2">
            <button
              type="button"
              onClick={() => setMonthDays([1])}
              className="text-[11px] text-accent/70 hover:text-accent transition-colors"
            >
              1st only
            </button>
            <button
              type="button"
              onClick={() => setMonthDays([1, 15])}
              className="text-[11px] text-accent/70 hover:text-accent transition-colors"
            >
              1st &amp; 15th
            </button>
          </div>
        </div>
      )}

      {/* Custom cron input */}
      {frequency === "custom" && (
        <div>
          <label className="mb-1.5 block text-xs font-medium text-muted">Cron Expression</label>
          <input
            type="text"
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
            placeholder="0 */6 * * *"
            className={cn(selectClass, "w-full font-mono")}
          />
          <p className="text-[11px] text-muted-foreground mt-1">
            Format: minute hour day-of-month month day-of-week
          </p>
        </div>
      )}

      {/* Preview card */}
      <div className="rounded-lg bg-background border border-border overflow-hidden">
        {/* Human-readable description */}
        <div className="flex items-center gap-2 px-3 py-2.5">
          <Clock className="h-3.5 w-3.5 text-accent shrink-0" />
          <span className="text-sm font-medium text-foreground">{humanDescription}</span>
        </div>

        {/* Next 3 runs */}
        {nextRuns.length > 0 && (
          <div className="border-t border-border px-3 py-2">
            <div className="flex items-center gap-1.5 mb-1.5">
              <Calendar className="h-3 w-3 text-muted" />
              <span className="text-[11px] font-medium text-muted">Next runs</span>
            </div>
            <div className="space-y-0.5">
              {nextRuns.map((run, i) => (
                <div key={i} className="text-xs text-muted-foreground font-mono">
                  {formatRunDate(run)}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Expandable raw cron (for non-custom modes) */}
        {frequency !== "custom" && (
          <div className="border-t border-border">
            <button
              type="button"
              onClick={() => setShowRawCron(!showRawCron)}
              className="flex w-full items-center justify-between px-3 py-1.5 text-[11px] text-muted-foreground hover:text-muted transition-colors"
            >
              <code className="font-mono">{currentCron}</code>
              {showRawCron ? (
                <ChevronUp className="h-3 w-3" />
              ) : (
                <ChevronDown className="h-3 w-3" />
              )}
            </button>
            {showRawCron && (
              <div className="px-3 pb-2">
                <input
                  type="text"
                  value={currentCron}
                  readOnly
                  className={cn(
                    selectClass,
                    "w-full font-mono text-xs bg-surface cursor-default"
                  )}
                />
                <p className="text-[10px] text-muted-foreground mt-0.5">
                  Read-only - switch to Custom mode to edit directly
                </p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function formatRunDate(date: Date): string {
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const dow = days[date.getUTCDay()];
  const mon = months[date.getUTCMonth()];
  const d = date.getUTCDate();
  const h = String(date.getUTCHours()).padStart(2, "0");
  const m = String(date.getUTCMinutes()).padStart(2, "0");
  return `${dow}, ${mon} ${d} at ${h}:${m} UTC`;
}
