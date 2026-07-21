import { afterEach, describe, expect, it, vi } from "vitest";
import { exportToCsv } from "@/lib/exportCsv";

describe("exportToCsv", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("prefixes formula-like fields so spreadsheet apps treat them as text", async () => {
    const captured: { csv: Blob | MediaSource | null } = { csv: null };
    vi.spyOn(URL, "createObjectURL").mockImplementation((blob) => {
      captured.csv = blob;
      return "blob:csv";
    });
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);

    exportToCsv("runs.csv", ["=header"], [["+sum", "-1", "@name", "\tvalue", "\rvalue"]]);

    expect(captured.csv).not.toBeNull();
    expect(captured.csv).toBeInstanceOf(Blob);
    if (!(captured.csv instanceof Blob)) throw new Error("CSV export did not create a Blob");
    // Blob.text() decodes the UTF-8 BOM, so assert the CSV fields after it.
    await expect(captured.csv.text()).resolves.toBe(
      "'=header\n'+sum,'-1,'@name,'\tvalue,\"'\rvalue\""
    );
  });
});
