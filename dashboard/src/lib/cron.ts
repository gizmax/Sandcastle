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

  if (hr === "*" && dom === "*" && dow === "*") {
    return { ...defaults, frequency: "hourly", minute: parseInt(min) || 0 };
  }

  if (hr.startsWith("*/") && dom === "*" && dow === "*") {
    return {
      ...defaults,
      frequency: "every_x_hours",
      minute: parseInt(min) || 0,
      interval: parseInt(hr.slice(2)) || 6,
    };
  }

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

  if (dom !== "*" && !dom.includes("*") && dow === "*" && !hr.includes("*")) {
    return {
      ...defaults,
      frequency: "monthly",
      minute: parseInt(min) || 0,
      hour: parseInt(hr) || 9,
      monthDay: parseInt(dom) || 1,
    };
  }

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
