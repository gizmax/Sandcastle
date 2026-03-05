import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import {
  Copy,
  Check,
  Search,
  X,
  ChevronUp,
  ChevronDown,
  WrapText,
  Maximize2,
  Minimize2,
  Pin,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface TerminalLogProps {
  logs: string;
  stepName: string;
  status: string;
  isLive?: boolean;
}

const STATUS_BG: Record<string, string> = {
  running: "bg-accent/20 text-accent",
  completed: "bg-green-400/20 text-green-400",
  failed: "bg-red-400/20 text-red-400",
  queued: "bg-amber-400/20 text-amber-400",
  cancelled: "bg-gray-400/20 text-gray-400",
};

const TIMESTAMP_RE = /(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?|\[\d{2}:\d{2}:\d{2}\])/g;
const URL_RE = /(https?:\/\/[^\s"'<>]+)/g;
const JSON_BLOCK_RE = /(\{[^{}]*\})/g;

type LineType = "error" | "warn" | "info" | "debug" | "success" | "default";

function classifyLine(line: string): LineType {
  const trimmed = line.trimStart();
  if (/^ERROR\b/i.test(trimmed) || /\berror:/i.test(trimmed)) return "error";
  if (/^WARN(?:ING)?\b/i.test(trimmed)) return "warn";
  if (/^INFO\b/i.test(trimmed)) return "info";
  if (/^DEBUG\b/i.test(trimmed)) return "debug";
  if (/\bSUCCESS\b/i.test(trimmed) || trimmed.includes("\u2713")) return "success";
  return "default";
}

const LINE_TYPE_CLASSES: Record<LineType, string> = {
  error: "text-red-400",
  warn: "text-amber-400",
  info: "text-blue-400",
  debug: "text-gray-500",
  success: "text-green-400",
  default: "text-gray-300",
};

interface SegmentBase {
  text: string;
}
interface PlainSegment extends SegmentBase { kind: "plain" }
interface TimestampSegment extends SegmentBase { kind: "timestamp" }
interface UrlSegment extends SegmentBase { kind: "url" }
interface JsonSegment extends SegmentBase { kind: "json" }
interface HighlightSegment extends SegmentBase { kind: "highlight"; active: boolean }

type Segment = PlainSegment | TimestampSegment | UrlSegment | JsonSegment | HighlightSegment;

function tokenizeLine(text: string, searchTerm: string, activeMatchLine: number, lineIndex: number): Segment[] {
  if (!text) return [{ kind: "plain", text: "" }];

  // Build a list of tagged regions
  type Region = { start: number; end: number; kind: "timestamp" | "url" | "json" | "highlight"; active?: boolean };
  const regions: Region[] = [];

  // Find timestamps
  let m: RegExpExecArray | null;
  TIMESTAMP_RE.lastIndex = 0;
  while ((m = TIMESTAMP_RE.exec(text)) !== null) {
    regions.push({ start: m.index, end: m.index + m[0].length, kind: "timestamp" });
  }

  // Find URLs
  URL_RE.lastIndex = 0;
  while ((m = URL_RE.exec(text)) !== null) {
    regions.push({ start: m.index, end: m.index + m[0].length, kind: "url" });
  }

  // Find JSON blocks
  JSON_BLOCK_RE.lastIndex = 0;
  while ((m = JSON_BLOCK_RE.exec(text)) !== null) {
    regions.push({ start: m.index, end: m.index + m[0].length, kind: "json" });
  }

  // Find search highlights
  if (searchTerm) {
    const escaped = searchTerm.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const searchRe = new RegExp(escaped, "gi");
    let matchIdx = 0;
    while ((m = searchRe.exec(text)) !== null) {
      regions.push({
        start: m.index,
        end: m.index + m[0].length,
        kind: "highlight",
        active: lineIndex === activeMatchLine && matchIdx === 0,
      });
      matchIdx++;
    }
  }

  // Sort by start, then by specificity (highlight > url > timestamp > json)
  const priority: Record<string, number> = { highlight: 4, url: 3, timestamp: 2, json: 1 };
  regions.sort((a, b) => a.start - b.start || (priority[b.kind] ?? 0) - (priority[a.kind] ?? 0));

  // Remove overlapping regions (keep higher priority)
  const filtered: Region[] = [];
  let lastEnd = 0;
  for (const r of regions) {
    if (r.start >= lastEnd) {
      filtered.push(r);
      lastEnd = r.end;
    }
  }

  // Build segments
  const segments: Segment[] = [];
  let pos = 0;
  for (const r of filtered) {
    if (r.start > pos) {
      segments.push({ kind: "plain", text: text.slice(pos, r.start) });
    }
    if (r.kind === "highlight") {
      segments.push({ kind: "highlight", text: text.slice(r.start, r.end), active: r.active ?? false });
    } else {
      segments.push({ kind: r.kind, text: text.slice(r.start, r.end) });
    }
    pos = r.end;
  }
  if (pos < text.length) {
    segments.push({ kind: "plain", text: text.slice(pos) });
  }
  if (segments.length === 0) {
    segments.push({ kind: "plain", text: "" });
  }
  return segments;
}

function SegmentSpan({ segment }: { segment: Segment }) {
  switch (segment.kind) {
    case "timestamp":
      return <span className="text-gray-500">{segment.text}</span>;
    case "url":
      return (
        <a
          href={segment.text}
          target="_blank"
          rel="noopener noreferrer"
          className="text-accent underline decoration-accent/40 hover:decoration-accent"
          onClick={(e) => e.stopPropagation()}
        >
          {segment.text}
        </a>
      );
    case "json":
      return <span className="text-purple-400">{segment.text}</span>;
    case "highlight":
      return (
        <mark
          className={cn(
            "rounded-sm px-0.5",
            segment.active
              ? "bg-amber-500/50 text-white"
              : "bg-amber-500/30 text-inherit"
          )}
        >
          {segment.text}
        </mark>
      );
    default:
      return <>{segment.text}</>;
  }
}

export function TerminalLog({ logs, stepName, status, isLive = false }: TerminalLogProps) {
  const [copied, setCopied] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [currentMatch, setCurrentMatch] = useState(0);
  const [wordWrap, setWordWrap] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [pinned, setPinned] = useState(true);
  const [highlightedLine, setHighlightedLine] = useState<number | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const prevLogLength = useRef(logs.length);

  const lines = useMemo(() => logs.split("\n"), [logs]);
  const lineTypes = useMemo(() => lines.map(classifyLine), [lines]);

  // Search matches: list of line indices that contain the search term
  const matchLines = useMemo(() => {
    if (!searchTerm) return [];
    const matches: number[] = [];
    const escaped = searchTerm.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const re = new RegExp(escaped, "i");
    for (let i = 0; i < lines.length; i++) {
      if (re.test(lines[i])) matches.push(i);
    }
    return matches;
  }, [lines, searchTerm]);

  const totalMatches = matchLines.length;

  // Auto-scroll when live and pinned
  useEffect(() => {
    if (isLive && pinned && logs.length > prevLogLength.current) {
      const el = scrollRef.current;
      if (el) {
        requestAnimationFrame(() => {
          el.scrollTop = el.scrollHeight;
        });
      }
    }
    prevLogLength.current = logs.length;
  }, [logs, isLive, pinned]);

  // Scroll to current match
  useEffect(() => {
    if (totalMatches > 0 && scrollRef.current) {
      const lineIdx = matchLines[currentMatch];
      const lineEl = scrollRef.current.querySelector(`[data-line="${lineIdx}"]`);
      if (lineEl) {
        lineEl.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    }
  }, [currentMatch, matchLines, totalMatches]);

  // Focus search input when opened
  useEffect(() => {
    if (searchOpen && searchInputRef.current) {
      searchInputRef.current.focus();
    }
  }, [searchOpen]);

  // Keyboard shortcut for search
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "f") {
        const el = scrollRef.current;
        if (el && el.closest("[data-terminal-log]")) {
          e.preventDefault();
          setSearchOpen(true);
        }
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);

  const handleCopy = useCallback(async () => {
    await navigator.clipboard.writeText(logs);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [logs]);

  const handleSearchPrev = useCallback(() => {
    if (totalMatches === 0) return;
    setCurrentMatch((prev) => (prev - 1 + totalMatches) % totalMatches);
  }, [totalMatches]);

  const handleSearchNext = useCallback(() => {
    if (totalMatches === 0) return;
    setCurrentMatch((prev) => (prev + 1) % totalMatches);
  }, [totalMatches]);

  const handleSearchKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") {
        if (e.shiftKey) handleSearchPrev();
        else handleSearchNext();
      } else if (e.key === "Escape") {
        setSearchOpen(false);
        setSearchTerm("");
        setCurrentMatch(0);
      }
    },
    [handleSearchNext, handleSearchPrev]
  );

  const activeMatchLine = totalMatches > 0 ? matchLines[currentMatch] : -1;

  return (
    <div
      data-terminal-log
      className={cn(
        "rounded-lg border border-gray-800 overflow-hidden",
        expanded ? "fixed inset-4 z-50" : ""
      )}
    >
      {/* Top bar */}
      <div className="flex items-center justify-between bg-[#0d0d1a] px-3 py-2 border-b border-gray-800">
        <div className="flex items-center gap-3">
          {/* macOS dots */}
          <div className="flex items-center gap-1.5">
            <div className="h-2.5 w-2.5 rounded-full bg-red-500/80" />
            <div className="h-2.5 w-2.5 rounded-full bg-amber-400/80" />
            <div className="h-2.5 w-2.5 rounded-full bg-green-500/80" />
          </div>
          <span className="font-mono text-xs text-gray-400 truncate max-w-[200px]">{stepName}</span>
          <span
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[10px] font-medium",
              STATUS_BG[status] || "bg-gray-700 text-gray-300"
            )}
          >
            {isLive && status === "running" && (
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-400 opacity-75" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-green-400" />
              </span>
            )}
            {isLive && status === "running" ? "Live" : status}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {isLive && (
            <button
              onClick={() => setPinned(!pinned)}
              className={cn(
                "rounded p-1 transition-colors",
                pinned
                  ? "text-accent bg-accent/10"
                  : "text-gray-500 hover:text-gray-300"
              )}
              title={pinned ? "Unpin from bottom" : "Pin to bottom"}
            >
              <Pin className="h-3.5 w-3.5" />
            </button>
          )}
          <button
            onClick={() => setWordWrap(!wordWrap)}
            className={cn(
              "rounded p-1 transition-colors",
              wordWrap
                ? "text-accent bg-accent/10"
                : "text-gray-500 hover:text-gray-300"
            )}
            title={wordWrap ? "Disable word wrap" : "Enable word wrap"}
          >
            <WrapText className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => setSearchOpen(!searchOpen)}
            className={cn(
              "rounded p-1 transition-colors",
              searchOpen
                ? "text-accent bg-accent/10"
                : "text-gray-500 hover:text-gray-300"
            )}
            title="Search (Ctrl+F)"
          >
            <Search className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => void handleCopy()}
            className="rounded p-1 text-gray-500 hover:text-gray-300 transition-colors"
            title="Copy all logs"
          >
            {copied ? (
              <Check className="h-3.5 w-3.5 text-green-400" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
          </button>
          <button
            onClick={() => setExpanded(!expanded)}
            className="rounded p-1 text-gray-500 hover:text-gray-300 transition-colors"
            title={expanded ? "Exit fullscreen" : "Fullscreen"}
          >
            {expanded ? (
              <Minimize2 className="h-3.5 w-3.5" />
            ) : (
              <Maximize2 className="h-3.5 w-3.5" />
            )}
          </button>
        </div>
      </div>

      {/* Search bar */}
      {searchOpen && (
        <div className="flex items-center gap-2 bg-[#0d0d1a] px-3 py-1.5 border-b border-gray-800">
          <Search className="h-3.5 w-3.5 text-gray-500 shrink-0" />
          <input
            ref={searchInputRef}
            type="text"
            value={searchTerm}
            onChange={(e) => {
              setSearchTerm(e.target.value);
              setCurrentMatch(0);
            }}
            onKeyDown={handleSearchKeyDown}
            placeholder="Search logs..."
            className="flex-1 bg-transparent text-xs text-gray-300 placeholder:text-gray-600 outline-none"
          />
          {searchTerm && (
            <span className="text-[10px] text-gray-500 shrink-0">
              {totalMatches > 0 ? `${currentMatch + 1}/${totalMatches}` : "0/0"}
            </span>
          )}
          <div className="flex items-center gap-0.5">
            <button
              onClick={handleSearchPrev}
              disabled={totalMatches === 0}
              className="rounded p-0.5 text-gray-500 hover:text-gray-300 disabled:opacity-30 transition-colors"
            >
              <ChevronUp className="h-3.5 w-3.5" />
            </button>
            <button
              onClick={handleSearchNext}
              disabled={totalMatches === 0}
              className="rounded p-0.5 text-gray-500 hover:text-gray-300 disabled:opacity-30 transition-colors"
            >
              <ChevronDown className="h-3.5 w-3.5" />
            </button>
          </div>
          <button
            onClick={() => {
              setSearchOpen(false);
              setSearchTerm("");
              setCurrentMatch(0);
            }}
            className="rounded p-0.5 text-gray-500 hover:text-gray-300 transition-colors"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {/* Log content */}
      <div
        ref={scrollRef}
        className={cn(
          "bg-[#1a1a2e] overflow-y-auto overflow-x-auto font-mono text-xs leading-relaxed",
          expanded ? "h-full" : "max-h-[400px]"
        )}
        onScroll={() => {
          if (isLive && scrollRef.current) {
            const el = scrollRef.current;
            const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 32;
            if (pinned && !atBottom) setPinned(false);
          }
        }}
      >
        <table className="w-full border-collapse">
          <tbody>
            {lines.map((line, i) => {
              const lineType = lineTypes[i];
              const segments = tokenizeLine(line, searchTerm, activeMatchLine, i);
              return (
                <tr
                  key={i}
                  data-line={i}
                  onClick={() => setHighlightedLine(highlightedLine === i ? null : i)}
                  className={cn(
                    "group cursor-pointer",
                    highlightedLine === i && "bg-accent/10",
                    i === activeMatchLine && "bg-amber-500/10"
                  )}
                >
                  <td className="select-none text-right align-top text-gray-600 w-8 pr-2 pl-2 border-r border-gray-800 sticky left-0 bg-[#1a1a2e]">
                    {i + 1}
                  </td>
                  <td
                    className={cn(
                      "pl-3 pr-4 py-px",
                      LINE_TYPE_CLASSES[lineType],
                      wordWrap ? "whitespace-pre-wrap break-all" : "whitespace-pre"
                    )}
                  >
                    {segments.map((seg, j) => (
                      <SegmentSpan key={j} segment={seg} />
                    ))}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Fullscreen backdrop */}
      {expanded && (
        <div
          className="fixed inset-0 bg-black/60 -z-10"
          onClick={() => setExpanded(false)}
        />
      )}
    </div>
  );
}
