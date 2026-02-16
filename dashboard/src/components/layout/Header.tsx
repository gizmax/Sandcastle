import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Menu, Search, PlayCircle, GitBranch } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/api/client";
import { ThemeToggle } from "@/components/shared/ThemeToggle";
import {
  NotificationCenter,
  type Notification,
} from "@/components/layout/NotificationCenter";

interface SearchResult {
  type: "run" | "workflow";
  label: string;
  sub: string;
  link: string;
}

interface HeaderProps {
  onMenuToggle: () => void;
  notifications: Notification[];
  onMarkAllRead: () => void;
  onClickNotification: (notification: Notification) => void;
}

export function Header({
  onMenuToggle,
  notifications,
  onMarkAllRead,
  onClickNotification,
}: HeaderProps) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [open, setOpen] = useState(false);
  const [mobileSearchOpen, setMobileSearchOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);

  const search = useCallback(async (q: string) => {
    if (q.length < 2) {
      setResults([]);
      return;
    }
    const lower = q.toLowerCase();
    const items: SearchResult[] = [];

    const [runsRes, workflowsRes] = await Promise.all([
      api.get<Array<{ run_id: string; workflow_name: string; status: string }>>("/runs", { limit: "50", offset: "0" }),
      api.get<Array<{ name: string; file_name: string; steps_count: number }>>("/workflows"),
    ]);

    if (runsRes.data) {
      for (const r of runsRes.data) {
        if (
          r.workflow_name.toLowerCase().includes(lower) ||
          r.run_id.toLowerCase().includes(lower) ||
          r.status.toLowerCase().includes(lower)
        ) {
          items.push({
            type: "run",
            label: r.workflow_name,
            sub: `${r.status} - ${r.run_id.slice(0, 8)}`,
            link: `/runs/${r.run_id}`,
          });
        }
      }
    }

    if (workflowsRes.data) {
      for (const w of workflowsRes.data) {
        if (
          w.name.toLowerCase().includes(lower) ||
          w.file_name.toLowerCase().includes(lower)
        ) {
          items.push({
            type: "workflow",
            label: w.name,
            sub: `${w.steps_count} steps - ${w.file_name}`,
            link: "/workflows",
          });
        }
      }
    }

    setResults(items.slice(0, 8));
  }, []);

  useEffect(() => {
    const t = setTimeout(() => search(query), 150);
    return () => clearTimeout(t);
  }, [query, search]);

  // Close on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  function handleSelect(result: SearchResult) {
    navigate(result.link);
    setQuery("");
    setResults([]);
    setOpen(false);
  }

  return (
    <header
      className={cn(
        "sticky top-0 z-30 flex h-14 sm:h-16 items-center gap-2 sm:gap-4 border-b border-border bg-surface/80 px-3 sm:px-4 lg:px-6 backdrop-blur-sm"
      )}
    >
      <button
        onClick={onMenuToggle}
        className="flex h-9 w-9 items-center justify-center rounded-lg text-muted hover:bg-border/50 hover:text-foreground lg:hidden"
      >
        <Menu className="h-5 w-5" />
      </button>

      {/* Mobile search toggle */}
      <button
        onClick={() => setMobileSearchOpen(!mobileSearchOpen)}
        className="flex h-9 w-9 items-center justify-center rounded-lg text-muted hover:bg-border/50 hover:text-foreground sm:hidden"
      >
        <Search className="h-5 w-5" />
      </button>

      <div ref={wrapperRef} className="relative hidden flex-1 sm:block">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <input
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => { if (results.length > 0) setOpen(true); }}
          placeholder="Search runs, workflows..."
          className={cn(
            "h-9 w-full max-w-sm rounded-lg border border-border bg-background pl-9 pr-3 text-sm",
            "placeholder:text-muted-foreground/50",
            "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30",
            "transition-all duration-200"
          )}
        />
        {open && results.length > 0 && (
          <div className="absolute left-0 top-full mt-1 w-full max-w-sm rounded-lg border border-border bg-surface shadow-lg overflow-hidden z-50">
            {results.map((r, i) => (
              <button
                key={`${r.link}-${i}`}
                onClick={() => handleSelect(r)}
                className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-border/40 transition-colors"
              >
                {r.type === "run"
                  ? <PlayCircle className="h-4 w-4 shrink-0 text-muted" />
                  : <GitBranch className="h-4 w-4 shrink-0 text-muted" />
                }
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-foreground">{r.label}</p>
                  <p className="truncate text-[11px] text-muted-foreground">{r.sub}</p>
                </div>
              </button>
            ))}
          </div>
        )}
        {open && query.length >= 2 && results.length === 0 && (
          <div className="absolute left-0 top-full mt-1 w-full max-w-sm rounded-lg border border-border bg-surface shadow-lg z-50 px-3 py-3">
            <p className="text-xs text-muted-foreground">No results for "{query}"</p>
          </div>
        )}
      </div>

      <div className="ml-auto flex items-center gap-1">
        <ThemeToggle />
        <NotificationCenter
          notifications={notifications}
          onMarkAllRead={onMarkAllRead}
          onClickNotification={onClickNotification}
        />
      </div>

      {/* Mobile search bar - full width below header */}
      {mobileSearchOpen && (
        <div ref={wrapperRef} className="absolute left-0 top-full z-50 w-full border-b border-border bg-surface p-3 shadow-md sm:hidden">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <input
              type="text"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setOpen(true);
              }}
              onFocus={() => { if (results.length > 0) setOpen(true); }}
              placeholder="Search runs, workflows..."
              autoFocus
              className={cn(
                "h-9 w-full rounded-lg border border-border bg-background pl-9 pr-3 text-sm",
                "placeholder:text-muted-foreground/50",
                "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30"
              )}
            />
            {open && results.length > 0 && (
              <div className="absolute left-0 top-full mt-1 w-full rounded-lg border border-border bg-surface shadow-lg overflow-hidden z-50">
                {results.map((r, i) => (
                  <button
                    key={`${r.link}-${i}`}
                    onClick={() => { handleSelect(r); setMobileSearchOpen(false); }}
                    className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-border/40 transition-colors"
                  >
                    {r.type === "run"
                      ? <PlayCircle className="h-4 w-4 shrink-0 text-muted" />
                      : <GitBranch className="h-4 w-4 shrink-0 text-muted" />
                    }
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm text-foreground">{r.label}</p>
                      <p className="truncate text-[11px] text-muted-foreground">{r.sub}</p>
                    </div>
                  </button>
                ))}
              </div>
            )}
            {open && query.length >= 2 && results.length === 0 && (
              <div className="absolute left-0 top-full mt-1 w-full rounded-lg border border-border bg-surface shadow-lg z-50 px-3 py-3">
                <p className="text-xs text-muted-foreground">No results for "{query}"</p>
              </div>
            )}
          </div>
        </div>
      )}
    </header>
  );
}
