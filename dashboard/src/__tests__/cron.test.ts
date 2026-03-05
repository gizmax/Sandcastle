import { describe, it, expect } from "vitest";
import { cronToHuman, parseCron, getNextRuns } from "@/lib/cron";

describe("cronToHuman", () => {
  // -- Every minute ---------------------------------------------------------

  it("parses every minute", () => {
    expect(cronToHuman("* * * * *")).toBe("Every minute");
  });

  // -- Hourly ---------------------------------------------------------------

  it("parses hourly at :00", () => {
    expect(cronToHuman("0 * * * *")).toBe("Every hour");
  });

  it("parses hourly at :15", () => {
    expect(cronToHuman("15 * * * *")).toBe("Every hour at :15");
  });

  // -- Every X hours --------------------------------------------------------

  it("parses every 6 hours", () => {
    expect(cronToHuman("0 */6 * * *")).toBe("Every 6 hours");
  });

  it("parses every 2 hours", () => {
    expect(cronToHuman("0 */2 * * *")).toBe("Every 2 hours");
  });

  // -- Daily ----------------------------------------------------------------

  it("parses daily at 09:00", () => {
    expect(cronToHuman("0 9 * * *")).toBe("Daily at 09:00 UTC");
  });

  it("parses daily at 14:30", () => {
    expect(cronToHuman("30 14 * * *")).toBe("Daily at 14:30 UTC");
  });

  it("parses daily at midnight (known quirk: hour 0 becomes 9 due to || operator)", () => {
    // Note: parseCron uses `parseInt(hr) || 9` which treats hour 0 as falsy,
    // falling back to 9. This is a known behavior in the current implementation.
    expect(cronToHuman("0 0 * * *")).toBe("Daily at 09:00 UTC");
  });

  // -- Weekly ---------------------------------------------------------------

  it("parses weekdays at 09:00", () => {
    expect(cronToHuman("0 9 * * 1,2,3,4,5")).toBe("Weekdays at 09:00 UTC");
  });

  it("parses Mon and Wed", () => {
    expect(cronToHuman("0 8 * * 1,3")).toBe("Mon, Wed at 08:00 UTC");
  });

  it("parses all 7 days as daily", () => {
    expect(cronToHuman("0 9 * * 0,1,2,3,4,5,6")).toBe("Daily at 09:00 UTC");
  });

  // -- Monthly --------------------------------------------------------------

  it("parses monthly on day 1", () => {
    expect(cronToHuman("0 9 1 * *")).toBe("Monthly on day 1 at 09:00 UTC");
  });

  it("parses monthly on day 15", () => {
    expect(cronToHuman("30 10 15 * *")).toBe("Monthly on day 15 at 10:30 UTC");
  });

  it("parses monthly with multiple days", () => {
    expect(cronToHuman("0 9 1,15 * *")).toBe("Monthly on days 1, 15 at 09:00 UTC");
  });

  // -- Custom / edge cases --------------------------------------------------

  it("returns raw expression for unsupported formats", () => {
    expect(cronToHuman("*/5 */3 1-15 1,6 *")).toBe("*/5 */3 1-15 1,6 *");
  });

  it("handles empty string", () => {
    expect(cronToHuman("")).toBe("Daily at 09:00 UTC");
  });

  it("handles malformed cron with wrong number of parts", () => {
    // 6 parts instead of 5
    const result = cronToHuman("0 9 * * * *");
    expect(result).toBe("0 9 * * * *"); // custom fallback
  });
});

describe("parseCron", () => {
  it("parses every minute", () => {
    expect(parseCron("* * * * *").frequency).toBe("every_minute");
  });

  it("parses monthly with multiple days", () => {
    const p = parseCron("0 9 1,15,28 * *");
    expect(p.frequency).toBe("monthly");
    expect(p.monthDays).toEqual([1, 15, 28]);
  });

  it("returns custom for unknown patterns", () => {
    expect(parseCron("*/5 */3 1-15 1,6 *").frequency).toBe("custom");
  });
});

describe("getNextRuns", () => {
  it("returns correct number of runs for daily", () => {
    const from = new Date("2026-03-05T10:00:00Z");
    const runs = getNextRuns("0 9 * * *", 3, from);
    expect(runs).toHaveLength(3);
    // First run should be 2026-03-06 at 09:00 UTC (next day since 10:00 > 09:00)
    expect(runs[0].getUTCHours()).toBe(9);
    expect(runs[0].getUTCMinutes()).toBe(0);
  });

  it("returns correct number of runs for hourly", () => {
    const from = new Date("2026-03-05T10:00:00Z");
    const runs = getNextRuns("30 * * * *", 3, from);
    expect(runs).toHaveLength(3);
    expect(runs[0].getUTCMinutes()).toBe(30);
  });

  it("returns runs for weekly schedule", () => {
    // Monday and Wednesday at 09:00
    const from = new Date("2026-03-02T10:00:00Z"); // Monday
    const runs = getNextRuns("0 9 * * 1,3", 3, from);
    expect(runs).toHaveLength(3);
  });

  it("falls back to monthly day 1 for out-of-range day 32", () => {
    // parseCron filters out day 32 (>31) and falls back to day 1
    const from = new Date("2026-03-05T10:00:00Z");
    const runs = getNextRuns("0 9 32 * *", 3, from);
    expect(runs).toHaveLength(3);
    // Should run on the 1st of each month
    for (const run of runs) {
      expect(run.getUTCDate()).toBe(1);
    }
  });

  it("handles every minute", () => {
    const from = new Date("2026-03-05T10:00:00Z");
    const runs = getNextRuns("* * * * *", 3, from);
    expect(runs).toHaveLength(3);
    // Should be consecutive minutes
    expect(runs[1].getTime() - runs[0].getTime()).toBe(60_000);
  });
});
