import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

type Frequency = "hourly" | "every_x_hours" | "daily" | "weekly" | "monthly" | "custom";

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

function parseCron(cron: string): {
  frequency: Frequency;
  hour: number;
  minute: number;
  interval: number;
  weekdays: number[];
  monthDay: number;
} {
  const defaults = {
    frequency: "daily" as Frequency,
    hour: 9,
    minute: 0,
    interval: 6,
    weekdays: [1, 2, 3, 4, 5],
    monthDay: 1,
  };

  if (!cron) return defaults;

  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return { ...defaults, frequency: "custom" };

  const [min, hr, dom, , dow] = parts;

  // Every hour: 0 * * * *
  if (hr === "*" && dom === "*" && dow === "*") {
    return { ...defaults, frequency: "hourly", minute: parseInt(min) || 0 };
  }

  // Every X hours: 0 */X * * *
  if (hr.startsWith("*/") && dom === "*" && dow === "*") {
    return {
      ...defaults,
      frequency: "every_x_hours",
      minute: parseInt(min) || 0,
      interval: parseInt(hr.slice(2)) || 6,
    };
  }

  // Weekly: M H * * 1,3,5
  if (dom === "*" && dow !== "*" && !hr.includes("*") && !hr.includes("/")) {
    const days = dow.split(",").map(Number).filter((n) => !isNaN(n));
    return {
      ...defaults,
      frequency: "weekly",
      minute: parseInt(min) || 0,
      hour: parseInt(hr) || 9,
      weekdays: days.length > 0 ? days : [1, 2, 3, 4, 5],
    };
  }

  // Monthly: M H D * *
  if (dom !== "*" && !dom.includes("*") && dow === "*" && !hr.includes("*")) {
    return {
      ...defaults,
      frequency: "monthly",
      minute: parseInt(min) || 0,
      hour: parseInt(hr) || 9,
      monthDay: parseInt(dom) || 1,
    };
  }

  // Daily: M H * * *
  if (dom === "*" && dow === "*" && !hr.includes("*") && !hr.includes("/")) {
    return {
      ...defaults,
      frequency: "daily",
      minute: parseInt(min) || 0,
      hour: parseInt(hr) || 9,
    };
  }

  return { ...defaults, frequency: "custom" };
}

function buildCron(state: {
  frequency: Frequency;
  hour: number;
  minute: number;
  interval: number;
  weekdays: number[];
  monthDay: number;
  custom: string;
}): string {
  const m = state.minute;
  const h = state.hour;

  switch (state.frequency) {
    case "hourly":
      return `${m} * * * *`;
    case "every_x_hours":
      return `${m} */${state.interval} * * *`;
    case "daily":
      return `${m} ${h} * * *`;
    case "weekly":
      return `${m} ${h} * * ${state.weekdays.sort((a, b) => a - b).join(",")}`;
    case "monthly":
      return `${m} ${h} ${state.monthDay} * *`;
    case "custom":
      return state.custom;
  }
}

export function cronToHuman(cron: string): string {
  const parsed = parseCron(cron);

  const pad = (n: number) => String(n).padStart(2, "0");
  const time = `${pad(parsed.hour)}:${pad(parsed.minute)}`;

  switch (parsed.frequency) {
    case "hourly":
      return parsed.minute === 0 ? "Every hour" : `Every hour at :${pad(parsed.minute)}`;
    case "every_x_hours":
      return `Every ${parsed.interval} hours`;
    case "daily":
      return `Daily at ${time}`;
    case "weekly": {
      const dayNames = parsed.weekdays
        .sort((a, b) => a - b)
        .map((d) => WEEKDAYS.find((w) => w.value === d)?.label || String(d));
      if (dayNames.length === 5 && !parsed.weekdays.includes(0) && !parsed.weekdays.includes(6)) {
        return `Weekdays at ${time}`;
      }
      if (dayNames.length === 7) {
        return `Daily at ${time}`;
      }
      return `${dayNames.join(", ")} at ${time}`;
    }
    case "monthly":
      return `Monthly on day ${parsed.monthDay} at ${time}`;
    case "custom":
      return cron;
  }
}

export function CronBuilder({ value, onChange }: CronBuilderProps) {
  const initial = parseCron(value);
  const [frequency, setFrequency] = useState<Frequency>(initial.frequency);
  const [hour, setHour] = useState(initial.hour);
  const [minute, setMinute] = useState(initial.minute);
  const [interval, setInterval] = useState(initial.interval);
  const [weekdays, setWeekdays] = useState<number[]>(initial.weekdays);
  const [monthDay, setMonthDay] = useState(initial.monthDay);
  const [custom, setCustom] = useState(value || "0 9 * * *");

  useEffect(() => {
    const cron = buildCron({ frequency, hour, minute, interval, weekdays, monthDay, custom });
    onChange(cron);
  }, [frequency, hour, minute, interval, weekdays, monthDay, custom, onChange]);

  const inputClass = cn(
    "h-9 rounded-lg border border-border bg-background px-3 text-sm",
    "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30"
  );

  const currentCron = buildCron({ frequency, hour, minute, interval, weekdays, monthDay, custom });

  return (
    <div className="space-y-3">
      {/* Frequency selector */}
      <div>
        <label className="mb-1 block text-xs font-medium text-muted">Frequency</label>
        <select
          value={frequency}
          onChange={(e) => setFrequency(e.target.value as Frequency)}
          className={cn(inputClass, "w-full")}
        >
          <option value="hourly">Every hour</option>
          <option value="every_x_hours">Every X hours</option>
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
          <option value="monthly">Monthly</option>
          <option value="custom">Custom (cron)</option>
        </select>
      </div>

      {/* Every X hours */}
      {frequency === "every_x_hours" && (
        <div>
          <label className="mb-1 block text-xs font-medium text-muted">Every</label>
          <div className="flex items-center gap-2">
            <select
              value={interval}
              onChange={(e) => setInterval(Number(e.target.value))}
              className={cn(inputClass, "w-24")}
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
      )}

      {/* Time picker for daily/weekly/monthly */}
      {(frequency === "daily" || frequency === "weekly" || frequency === "monthly") && (
        <div>
          <label className="mb-1 block text-xs font-medium text-muted">Time</label>
          <div className="flex items-center gap-2">
            <select
              value={hour}
              onChange={(e) => setHour(Number(e.target.value))}
              className={cn(inputClass, "w-20")}
            >
              {Array.from({ length: 24 }, (_, i) => (
                <option key={i} value={i}>
                  {String(i).padStart(2, "0")}
                </option>
              ))}
            </select>
            <span className="text-sm font-medium text-muted">:</span>
            <select
              value={minute}
              onChange={(e) => setMinute(Number(e.target.value))}
              className={cn(inputClass, "w-20")}
            >
              {[0, 15, 30, 45].map((m) => (
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
          <label className="mb-1 block text-xs font-medium text-muted">Days</label>
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
                    "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                    active
                      ? "bg-accent text-accent-foreground"
                      : "bg-background border border-border text-muted hover:text-foreground"
                  )}
                >
                  {day.label}
                </button>
              );
            })}
          </div>
          <div className="flex gap-2 mt-1.5">
            <button
              type="button"
              onClick={() => setWeekdays([1, 2, 3, 4, 5])}
              className="text-[11px] text-accent/70 hover:text-accent transition-colors"
            >
              Weekdays
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

      {/* Month day picker */}
      {frequency === "monthly" && (
        <div>
          <label className="mb-1 block text-xs font-medium text-muted">Day of month</label>
          <select
            value={monthDay}
            onChange={(e) => setMonthDay(Number(e.target.value))}
            className={cn(inputClass, "w-24")}
          >
            {Array.from({ length: 28 }, (_, i) => (
              <option key={i + 1} value={i + 1}>
                {i + 1}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Custom cron */}
      {frequency === "custom" && (
        <div>
          <label className="mb-1 block text-xs font-medium text-muted">Cron Expression</label>
          <input
            type="text"
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
            placeholder="0 */6 * * *"
            className={cn(inputClass, "w-full font-mono")}
          />
          <p className="text-[11px] text-muted-foreground mt-0.5">
            Format: minute hour day month weekday
          </p>
        </div>
      )}

      {/* Preview */}
      <div className="rounded-md bg-background border border-border px-3 py-2">
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted">Schedule:</span>
          <span className="text-xs font-medium text-foreground">{cronToHuman(currentCron)}</span>
        </div>
        <div className="flex items-center justify-between mt-0.5">
          <span className="text-[11px] text-muted-foreground">Cron:</span>
          <code className="text-[11px] font-mono text-muted-foreground">{currentCron}</code>
        </div>
      </div>
    </div>
  );
}
