import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { Menu, Search, PlayCircle, GitBranch, Plug } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/api/client";
import { ThemeToggle } from "@/components/shared/ThemeToggle";
import { LiveIndicator } from "@/components/shared/LiveIndicator";
import {
  NotificationCenter,
  type Notification,
} from "@/components/layout/NotificationCenter";

interface SearchResult {
  type: "run" | "workflow" | "tool";
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

// Map pathname to a readable page title
const PAGE_TITLES: Record<string, string> = {
  "/": "Overview",
  "/runs": "Runs",
  "/workflows": "Workflows",
  "/workflows/builder": "Workflow Builder",
  "/templates": "Template Hub",
  "/integrations": "Integrations",
  "/approvals": "Approvals",
  "/evaluations": "Evaluations",
  "/autopilot": "AutoPilot",
  "/violations": "Violations",
  "/optimizer": "Optimizer",
  "/schedules": "Schedules",
  "/dead-letter": "Dead Letter",
  "/api-keys": "API Keys",
  "/system-health": "System Health",
  "/settings": "Settings",
};

function getPageTitle(pathname: string): string {
  // Direct match first
  if (PAGE_TITLES[pathname]) return PAGE_TITLES[pathname];
  // Handle dynamic segments like /runs/:id
  if (pathname.startsWith("/runs/")) return "Run Detail";
  if (pathname.startsWith("/workflows/")) return "Workflow Detail";
  return "Sandcastle";
}

export function Header({
  onMenuToggle,
  notifications,
  onMarkAllRead,
  onClickNotification,
}: HeaderProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [open, setOpen] = useState(false);
  const [mobileSearchOpen, setMobileSearchOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const mobileWrapperRef = useRef<HTMLDivElement>(null);
  const searchVersionRef = useRef(0);

  const pageTitle = getPageTitle(location.pathname);

  const search = useCallback(async (q: string) => {
    if (q.length < 2) {
      setResults([]);
      return;
    }
    const version = ++searchVersionRef.current;
    const lower = q.toLowerCase();
    const items: SearchResult[] = [];

    const [runsRes, workflowsRes, toolsRes] = await Promise.all([
      api.get<Array<{ run_id: string; workflow_name: string; status: string }>>("/runs", { limit: "50", offset: "0" }),
      api.get<Array<{ name: string; file_name: string; steps_count: number }>>("/workflows"),
      api.get<{ tools: Array<{ name: string; description: string; category: string; configured: boolean }> }>("/tools"),
    ]);

    // Discard results from stale searches
    if (version !== searchVersionRef.current) return;

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

    if (toolsRes.data?.tools) {
      for (const t of toolsRes.data.tools) {
        if (
          t.name.toLowerCase().includes(lower) ||
          t.description.toLowerCase().includes(lower) ||
          t.category.toLowerCase().includes(lower)
        ) {
          items.push({
            type: "tool",
            label: t.name,
            sub: `${t.category} - ${t.configured ? "configured" : "not configured"}`,
            link: "/integrations",
          });
        }
      }
    }

    setResults(items.slice(0, 8));
  }, []);

  useEffect(() => {
    const t = setTimeout(() => search(query), 300);
    return () => clearTimeout(t);
  }, [query, search]);

  // Close on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      const target = e.target as Node;
      if (
        wrapperRef.current && !wrapperRef.current.contains(target) &&
        (!mobileWrapperRef.current || !mobileWrapperRef.current.contains(target))
      ) {
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

      {/* Page title / breadcrumb */}
      <h2 className="hidden text-sm font-semibold text-foreground lg:block">
        {pageTitle}
      </h2>

      {/* Mobile search toggle */}
      <button
        onClick={() => setMobileSearchOpen(!mobileSearchOpen)}
        className="flex h-9 w-9 items-center justify-center rounded-lg text-muted hover:bg-border/50 hover:text-foreground sm:hidden"
      >
        <Search className="h-5 w-5" />
      </button>

      {/* Command palette trigger - desktop */}
      <div ref={wrapperRef} className="relative ml-auto hidden flex-1 sm:block sm:max-w-sm lg:ml-8">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <input
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => { if (results.length > 0) setOpen(true); }}
          placeholder="Search..."
          className={cn(
            "h-9 w-full rounded-lg border border-border bg-background pl-9 pr-16 text-sm",
            "placeholder:text-muted-foreground/50",
            "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30",
            "transition-all duration-200"
          )}
        />
        <kbd className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 rounded border border-border bg-background px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
          {"\u2318"}K
        </kbd>
        {open && results.length > 0 && (
          <div className="absolute left-0 top-full mt-1 w-full rounded-lg border border-border bg-surface shadow-lg overflow-hidden z-50">
            {results.map((r, i) => (
              <button
                key={`${r.link}-${i}`}
                onClick={() => handleSelect(r)}
                className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-border/40 transition-colors"
              >
                {r.type === "run"
                  ? <PlayCircle className="h-4 w-4 shrink-0 text-muted" />
                  : r.type === "tool"
                  ? <Plug className="h-4 w-4 shrink-0 text-muted" />
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

      <div className={cn("flex items-center gap-1", "sm:ml-0 ml-auto")}>
        <LiveIndicator />
        <ThemeToggle />
        <NotificationCenter
          notifications={notifications}
          onMarkAllRead={onMarkAllRead}
          onClickNotification={onClickNotification}
        />
      </div>

      {/* Mobile search bar - full width below header */}
      {mobileSearchOpen && (
        <div ref={mobileWrapperRef} className="absolute left-0 top-full z-50 w-full border-b border-border bg-surface p-3 shadow-md sm:hidden">
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
              placeholder="Search..."
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
                      : r.type === "tool"
                      ? <Plug className="h-4 w-4 shrink-0 text-muted" />
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
