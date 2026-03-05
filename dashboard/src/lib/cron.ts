type Frequency = "every_minute" | "hourly" | "every_x_hours" | "daily" | "weekly" | "monthly" | "custom";

const WEEKDAYS = [
  { label: "Mon", value: 1 },
  { label: "Tue", value: 2 },
  { label: "Wed", value: 3 },
  { label: "Thu", value: 4 },
  { label: "Fri", value: 5 },
  { label: "Sat", value: 6 },
  { label: "Sun", value: 0 },
];

export interface ParsedCron {
  frequency: Frequency;
  hour: number;
  minute: number;
  interval: number;
  weekdays: number[];
  monthDays: number[];
}

export function parseCron(cron: string): ParsedCron {
  const defaults: ParsedCron = {
    frequency: "daily",
    hour: 9,
    minute: 0,
    interval: 6,
    weekdays: [1, 2, 3, 4, 5],
    monthDays: [1],
  };

  if (!cron) return defaults;

  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return { ...defaults, frequency: "custom" };

  const [min, hr, dom, , dow] = parts;

  // Every minute: * * * * *
  if (min === "*" && hr === "*" && dom === "*" && dow === "*") {
    return { ...defaults, frequency: "every_minute" };
  }

  // Every hour: N * * * *
  if (hr === "*" && dom === "*" && dow === "*") {
    return { ...defaults, frequency: "hourly", minute: parseInt(min) || 0 };
  }

  // Every X hours: N */X * * *
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

  // Monthly: M H D1,D2 * *
  if (dom !== "*" && !dom.includes("*") && dow === "*" && !hr.includes("*")) {
    const days = dom.split(",").map(Number).filter((n) => !isNaN(n) && n >= 1 && n <= 31);
    return {
      ...defaults,
      frequency: "monthly",
      minute: parseInt(min) || 0,
      hour: parseInt(hr) || 9,
      monthDays: days.length > 0 ? days : [1],
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

export function cronToHuman(cron: string): string {
  const parsed = parseCron(cron);

  const pad = (n: number) => String(n).padStart(2, "0");
  const time = `${pad(parsed.hour)}:${pad(parsed.minute)} UTC`;

  switch (parsed.frequency) {
    case "every_minute":
      return "Every minute";
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
    case "monthly": {
      const sorted = [...parsed.monthDays].sort((a, b) => a - b);
      const dayStr = sorted.length === 1
        ? `day ${sorted[0]}`
        : `days ${sorted.join(", ")}`;
      return `Monthly on ${dayStr} at ${time}`;
    }
    case "custom":
      return cron;
  }
}

/**
 * Calculate the next N run times for a cron expression from a given start date.
 * Supports a subset of cron: the frequencies we parse (not arbitrary custom cron).
 */
export function getNextRuns(cron: string, count: number, from?: Date): Date[] {
  const parsed = parseCron(cron);
  const runs: Date[] = [];
  const start = from ? new Date(from) : new Date();

  // Use a cursor that starts from `start` and advances
  const cursor = new Date(start);
  // Round up to the next minute boundary
  cursor.setUTCSeconds(0, 0);
  cursor.setUTCMinutes(cursor.getUTCMinutes() + 1);

  // Safety: max iterations to prevent infinite loops
  const MAX_ITER = 525_960; // ~1 year of minutes

  for (let i = 0; i < MAX_ITER && runs.length < count; i++) {
    if (matchesCron(parsed, cursor, cron)) {
      runs.push(new Date(cursor));
    }
    cursor.setUTCMinutes(cursor.getUTCMinutes() + 1);
  }

  return runs;
}

function matchesCron(parsed: ParsedCron, date: Date, raw: string): boolean {
  const m = date.getUTCMinutes();
  const h = date.getUTCHours();
  const dom = date.getUTCDate();
  const dow = date.getUTCDay(); // 0=Sun

  switch (parsed.frequency) {
    case "every_minute":
      return true;
    case "hourly":
      return m === parsed.minute;
    case "every_x_hours":
      return m === parsed.minute && h % parsed.interval === 0;
    case "daily":
      return m === parsed.minute && h === parsed.hour;
    case "weekly":
      return m === parsed.minute && h === parsed.hour && parsed.weekdays.includes(dow);
    case "monthly":
      return m === parsed.minute && h === parsed.hour && parsed.monthDays.includes(dom);
    case "custom":
      return matchesRawCron(raw, date);
  }
}

/**
 * Best-effort matcher for 5-field cron. Handles *, N, N,N, and star/N patterns.
 */
function matchesRawCron(cron: string, date: Date): boolean {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return false;

  const [minF, hrF, domF, monF, dowF] = parts;
  const m = date.getUTCMinutes();
  const h = date.getUTCHours();
  const dom = date.getUTCDate();
  const mon = date.getUTCMonth() + 1;
  const dow = date.getUTCDay();

  return (
    fieldMatches(minF, m) &&
    fieldMatches(hrF, h) &&
    fieldMatches(domF, dom) &&
    fieldMatches(monF, mon) &&
    fieldMatches(dowF, dow)
  );
}

function fieldMatches(field: string, value: number): boolean {
  if (field === "*") return true;

  // */N step
  if (field.startsWith("*/")) {
    const step = parseInt(field.slice(2));
    return !isNaN(step) && step > 0 && value % step === 0;
  }

  // Comma-separated list
  const nums = field.split(",").map(Number);
  return nums.some((n) => n === value);
}
