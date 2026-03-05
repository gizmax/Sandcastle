import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeftRight, ChevronDown, Loader2, X } from "lucide-react";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface DiffData {
  version_a: number;
  version_b: number;
  yaml_a: string;
  yaml_b: string;
  steps_added: string[];
  steps_removed: string[];
  steps_changed: string[];
}

export interface VersionDiffModalProps {
  open: boolean;
  onClose: () => void;
  workflowName: string;
  versionA: number;
  versionB: number;
  /** All available version numbers for the dropdown selectors */
  availableVersions?: number[];
}

// ---------------------------------------------------------------------------
// LCS-based diff algorithm (no external deps)
// ---------------------------------------------------------------------------

type DiffKind = "equal" | "added" | "removed";

interface DiffLine {
  kind: DiffKind;
  lineA?: number; // 1-based line number in A
  lineB?: number; // 1-based line number in B
  text: string;
}

/**
 * Compute the Longest Common Subsequence table for two arrays of strings.
 * Returns a 2D table where lcs[i][j] = length of LCS of a[0..i-1] and b[0..j-1].
 */
function lcsTable(a: string[], b: string[]): number[][] {
  const m = a.length;
  const n = b.length;
  const table: number[][] = Array.from({ length: m + 1 }, () =>
    new Array<number>(n + 1).fill(0),
  );
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (a[i - 1] === b[j - 1]) {
        table[i][j] = table[i - 1][j - 1] + 1;
      } else {
        table[i][j] = Math.max(table[i - 1][j], table[i][j - 1]);
      }
    }
  }
  return table;
}

/**
 * Backtrack through the LCS table to produce a list of diff lines.
 * Returns a unified diff: equal, added (+), removed (-).
 */
function computeDiff(textA: string, textB: string): DiffLine[] {
  const linesA = textA.split("\n");
  const linesB = textB.split("\n");
  const table = lcsTable(linesA, linesB);
  const result: DiffLine[] = [];

  let i = linesA.length;
  let j = linesB.length;

  // Backtrack from bottom-right to produce diff in reverse
  const stack: DiffLine[] = [];
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && linesA[i - 1] === linesB[j - 1]) {
      stack.push({ kind: "equal", lineA: i, lineB: j, text: linesA[i - 1] });
      i--;
      j--;
    } else if (j > 0 && (i === 0 || table[i][j - 1] >= table[i - 1][j])) {
      stack.push({ kind: "added", lineB: j, text: linesB[j - 1] });
      j--;
    } else {
      stack.push({ kind: "removed", lineA: i, text: linesA[i - 1] });
      i--;
    }
  }

  // Reverse to get natural order
  for (let k = stack.length - 1; k >= 0; k--) {
    result.push(stack[k]);
  }

  return result;
}

/**
 * Build aligned left/right panel lines from unified diff output.
 * Returns two arrays of the same length for side-by-side display.
 */
interface PanelLine {
  lineNum: number | null;
  text: string;
  kind: "equal" | "added" | "removed";
}

function buildSideBySide(diff: DiffLine[]): { left: PanelLine[]; right: PanelLine[] } {
  const left: PanelLine[] = [];
  const right: PanelLine[] = [];

  let idx = 0;
  while (idx < diff.length) {
    const line = diff[idx];

    if (line.kind === "equal") {
      left.push({ lineNum: line.lineA ?? null, text: line.text, kind: "equal" });
      right.push({ lineNum: line.lineB ?? null, text: line.text, kind: "equal" });
      idx++;
    } else if (line.kind === "removed") {
      // Collect consecutive removed lines
      const removedBatch: DiffLine[] = [];
      while (idx < diff.length && diff[idx].kind === "removed") {
        removedBatch.push(diff[idx]);
        idx++;
      }
      // Collect consecutive added lines that follow
      const addedBatch: DiffLine[] = [];
      while (idx < diff.length && diff[idx].kind === "added") {
        addedBatch.push(diff[idx]);
        idx++;
      }
      // Pair them up
      const maxLen = Math.max(removedBatch.length, addedBatch.length);
      for (let k = 0; k < maxLen; k++) {
        const rem = removedBatch[k];
        const add = addedBatch[k];
        left.push(
          rem
            ? { lineNum: rem.lineA ?? null, text: rem.text, kind: "removed" }
            : { lineNum: null, text: "", kind: "equal" },
        );
        right.push(
          add
            ? { lineNum: add.lineB ?? null, text: add.text, kind: "added" }
            : { lineNum: null, text: "", kind: "equal" },
        );
      }
    } else {
      // Standalone added line (no preceding removed)
      left.push({ lineNum: null, text: "", kind: "equal" });
      right.push({ lineNum: line.lineB ?? null, text: line.text, kind: "added" });
      idx++;
    }
  }

  return { left, right };
}

// ---------------------------------------------------------------------------
// Stats helper
// ---------------------------------------------------------------------------

function computeStats(diff: DiffLine[]): { added: number; removed: number; changed: number } {
  let added = 0;
  let removed = 0;

  // Walk through to count changed pairs (adjacent removed+added)
  let idx = 0;
  let changedPairs = 0;
  while (idx < diff.length) {
    if (diff[idx].kind === "removed") {
      const remStart = idx;
      while (idx < diff.length && diff[idx].kind === "removed") idx++;
      const remCount = idx - remStart;
      const addStart = idx;
      while (idx < diff.length && diff[idx].kind === "added") idx++;
      const addCount = idx - addStart;
      // Paired lines count as "changed"
      const paired = Math.min(remCount, addCount);
      changedPairs += paired;
      removed += remCount - paired;
      added += addCount - paired;
    } else if (diff[idx].kind === "added") {
      added++;
      idx++;
    } else {
      idx++;
    }
  }

  return { added, removed, changed: changedPairs };
}

// ---------------------------------------------------------------------------
// Version selector dropdown
// ---------------------------------------------------------------------------

function VersionSelector({
  value,
  versions,
  onChange,
  label,
}: {
  value: number;
  versions: number[];
  onChange: (v: number) => void;
  label: string;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-muted">{label}</span>
      <div className="relative">
        <select
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className={cn(
            "appearance-none rounded-lg border border-border bg-background",
            "pl-3 pr-7 py-1.5 text-sm font-mono text-foreground",
            "focus:outline-none focus:ring-2 focus:ring-ring/30",
            "cursor-pointer transition-colors hover:border-accent/50",
          )}
        >
          {versions.map((v) => (
            <option key={v} value={v}>
              v{v}
            </option>
          ))}
        </select>
        <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3 w-3 -translate-y-1/2 text-muted" />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Diff line component
// ---------------------------------------------------------------------------

const LINE_KIND_STYLES: Record<string, string> = {
  equal: "",
  added:
    "bg-success/10 border-l-2 border-success dark:bg-success/[0.07]",
  removed:
    "bg-error/10 border-l-2 border-error dark:bg-error/[0.07]",
};

function DiffLineRow({ line }: { line: PanelLine }) {
  // Empty spacer line for alignment
  const isEmpty = line.lineNum === null && line.text === "" && line.kind === "equal";

  return (
    <div
      className={cn(
        "flex min-h-[1.375rem] text-sm font-mono leading-relaxed",
        LINE_KIND_STYLES[line.kind],
        isEmpty && "opacity-30",
      )}
    >
      <span className="w-10 shrink-0 select-none pr-2 text-right text-muted/50 text-xs leading-relaxed">
        {line.lineNum ?? ""}
      </span>
      <span className="whitespace-pre overflow-x-auto px-2">
        {line.text || "\u00A0"}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main modal component
// ---------------------------------------------------------------------------

export function VersionDiffModal({
  open,
  onClose,
  workflowName,
  versionA: initialA,
  versionB: initialB,
  availableVersions,
}: VersionDiffModalProps) {
  const [verA, setVerA] = useState(initialA);
  const [verB, setVerB] = useState(initialB);
  const [diff, setDiff] = useState<DiffData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const leftPanelRef = useRef<HTMLDivElement>(null);
  const rightPanelRef = useRef<HTMLDivElement>(null);
  const isSyncing = useRef(false);

  // Fetch diff data
  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(null);
    setDiff(null);

    api
      .get<DiffData>(`/workflows/${workflowName}/versions/diff`, {
        version_a: String(verA),
        version_b: String(verB),
      })
      .then((res) => {
        if (res.data) {
          setDiff(res.data);
        } else if (res.error) {
          setError(res.error.message);
        }
      })
      .finally(() => setLoading(false));
  }, [open, workflowName, verA, verB]);

  // Keyboard shortcuts
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  // Synchronized scroll
  const syncScroll = useCallback(
    (source: "left" | "right") => {
      if (isSyncing.current) return;
      isSyncing.current = true;

      const src = source === "left" ? leftPanelRef.current : rightPanelRef.current;
      const dst = source === "left" ? rightPanelRef.current : leftPanelRef.current;

      if (src && dst) {
        dst.scrollTop = src.scrollTop;
      }

      requestAnimationFrame(() => {
        isSyncing.current = false;
      });
    },
    [],
  );

  useEffect(() => {
    const leftEl = leftPanelRef.current;
    const rightEl = rightPanelRef.current;
    if (!leftEl || !rightEl) return;

    const handleLeftScroll = () => syncScroll("left");
    const handleRightScroll = () => syncScroll("right");

    leftEl.addEventListener("scroll", handleLeftScroll, { passive: true });
    rightEl.addEventListener("scroll", handleRightScroll, { passive: true });

    return () => {
      leftEl.removeEventListener("scroll", handleLeftScroll);
      rightEl.removeEventListener("scroll", handleRightScroll);
    };
  }, [syncScroll, diff]); // re-attach when diff changes (panels re-render)

  // Swap versions
  const handleSwap = useCallback(() => {
    setVerA((a) => {
      setVerB(a);
      return verB;
    });
  }, [verB]);

  // Build version options
  const versionOptions = availableVersions
    ? [...availableVersions].sort((a, b) => b - a)
    : [...new Set([verA, verB])].sort((a, b) => b - a);

  if (!open) return null;

  // Compute diff lines for display
  const diffLines = diff ? computeDiff(diff.yaml_a, diff.yaml_b) : [];
  const sideBySide = diff ? buildSideBySide(diffLines) : { left: [], right: [] };
  const stats = diff ? computeStats(diffLines) : { added: 0, removed: 0, changed: 0 };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Version diff: v${verA} vs v${verB}`}
        className={cn(
          "relative mx-4 flex max-h-[90vh] w-full max-w-6xl flex-col overflow-hidden",
          "rounded-xl border border-border bg-surface shadow-2xl",
        )}
        onClick={(e) => e.stopPropagation()}
      >
        {/* ── Header ── */}
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <div className="flex items-center gap-4">
            <h3 className="text-sm font-semibold text-foreground">Compare Versions</h3>

            <div className="flex items-center gap-2">
              <VersionSelector
                label="From"
                value={verA}
                versions={versionOptions}
                onChange={setVerA}
              />

              <button
                onClick={handleSwap}
                className={cn(
                  "rounded-md p-1.5 text-muted transition-colors",
                  "hover:text-accent hover:bg-accent/10",
                )}
                title="Swap versions"
              >
                <ArrowLeftRight className="h-3.5 w-3.5" />
              </button>

              <VersionSelector
                label="To"
                value={verB}
                versions={versionOptions}
                onChange={setVerB}
              />
            </div>

            {/* Stats */}
            {diff && (
              <div className="flex items-center gap-3 text-xs">
                {stats.added > 0 && (
                  <span className="text-success font-medium">+{stats.added}</span>
                )}
                {stats.removed > 0 && (
                  <span className="text-error font-medium">-{stats.removed}</span>
                )}
                {stats.changed > 0 && (
                  <span className="text-warning font-medium">~{stats.changed}</span>
                )}
                {stats.added === 0 && stats.removed === 0 && stats.changed === 0 && (
                  <span className="text-muted">No changes</span>
                )}
              </div>
            )}
          </div>

          <button
            onClick={onClose}
            className="rounded-md p-1.5 text-muted hover:text-foreground hover:bg-border/40 transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* ── Step-level summary ── */}
        {diff &&
          (diff.steps_added.length > 0 ||
            diff.steps_removed.length > 0 ||
            diff.steps_changed.length > 0) && (
            <div className="flex flex-wrap gap-4 border-b border-border px-5 py-2.5 text-xs">
              {diff.steps_added.length > 0 && (
                <span className="text-success">
                  +{diff.steps_added.length} step{diff.steps_added.length > 1 ? "s" : ""}:{" "}
                  {diff.steps_added.join(", ")}
                </span>
              )}
              {diff.steps_removed.length > 0 && (
                <span className="text-error">
                  -{diff.steps_removed.length} step{diff.steps_removed.length > 1 ? "s" : ""}:{" "}
                  {diff.steps_removed.join(", ")}
                </span>
              )}
              {diff.steps_changed.length > 0 && (
                <span className="text-warning">
                  ~{diff.steps_changed.length} step{diff.steps_changed.length > 1 ? "s" : ""}{" "}
                  changed: {diff.steps_changed.join(", ")}
                </span>
              )}
            </div>
          )}

        {/* ── Content area ── */}
        {loading && (
          <div className="flex flex-1 items-center justify-center py-16">
            <Loader2 className="h-6 w-6 animate-spin text-muted" />
          </div>
        )}

        {error && (
          <div className="flex flex-1 items-center justify-center py-16">
            <p className="text-sm text-error">{error}</p>
          </div>
        )}

        {diff && !loading && (
          <div className="flex flex-1 overflow-hidden">
            {/* ── Column headers ── */}
            <div className="flex w-full flex-col overflow-hidden">
              <div className="grid grid-cols-2 divide-x divide-border border-b border-border">
                <div className="px-4 py-2">
                  <span className="text-xs font-medium text-muted">
                    v{diff.version_a} (older)
                  </span>
                </div>
                <div className="px-4 py-2">
                  <span className="text-xs font-medium text-muted">
                    v{diff.version_b} (newer)
                  </span>
                </div>
              </div>

              {/* ── Side-by-side panels ── */}
              <div className="grid flex-1 grid-cols-2 divide-x divide-border overflow-hidden">
                {/* Left panel */}
                <div
                  ref={leftPanelRef}
                  className="overflow-auto py-2"
                >
                  {sideBySide.left.map((line, i) => (
                    <DiffLineRow key={i} line={line} />
                  ))}
                  {sideBySide.left.length === 0 && (
                    <div className="px-4 py-8 text-center text-xs text-muted">
                      Empty content
                    </div>
                  )}
                </div>

                {/* Right panel */}
                <div
                  ref={rightPanelRef}
                  className="overflow-auto py-2"
                >
                  {sideBySide.right.map((line, i) => (
                    <DiffLineRow key={i} line={line} />
                  ))}
                  {sideBySide.right.length === 0 && (
                    <div className="px-4 py-8 text-center text-xs text-muted">
                      Empty content
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
