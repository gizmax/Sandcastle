import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  GitBranch,
  Key,
  LayoutDashboard,
  PlayCircle,
  Plug,
  Search,
  Settings,
  ShieldCheck,
  AlertTriangle,
  HeartPulse,
  Inbox,
  Calendar,
  Clock,
  Sparkles,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/api/client";
import { OMNIBOX_FOCUS_EVENT } from "@/components/overview/Omnibox";
import type { RecentItem } from "@/hooks/useRecentItems";

/* ------------------------------------------------------------------ */
/*  Types                                                             */
/* ------------------------------------------------------------------ */

interface SearchResult {
  type: "run" | "workflow" | "tool";
  label: string;
  sub: string;
  link: string;
}

interface CommandItem {
  /** Category used for grouping */
  category: "action" | "recent" | "run" | "workflow" | "page";
  label: string;
  description: string;
  link: string;
  /** Extra query params to append */
  search?: string;
  icon: React.ElementType;
  /** Special non-navigation action. "omnibox" focuses the Overview omnibox. */
  action?: "omnibox";
}

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  recentItems: RecentItem[];
}

/* ------------------------------------------------------------------ */
/*  Static data                                                       */
/* ------------------------------------------------------------------ */

const ACTION_COMMANDS: CommandItem[] = [
  { category: "action", label: "✨ Describe a workflow…", description: "Build an agent from a sentence", link: "/", action: "omnibox", icon: Sparkles },
  { category: "action", label: "/run", description: "Quick run a workflow", link: "/workflows", icon: PlayCircle },
  { category: "action", label: "/approve", description: "Go to pending approvals", link: "/approvals", icon: ShieldCheck },
  { category: "action", label: "/failures", description: "Go to failed runs", link: "/runs", search: "?status=failed", icon: AlertTriangle },
  { category: "action", label: "/keys", description: "Go to API keys page", link: "/api-keys", icon: Key },
  { category: "action", label: "/settings", description: "Go to settings", link: "/settings", icon: Settings },
  { category: "action", label: "/health", description: "Go to system health", link: "/system-health", icon: HeartPulse },
  { category: "action", label: "/dlq", description: "Go to dead letter queue", link: "/dead-letter", icon: Inbox },
  { category: "action", label: "/schedules", description: "Go to schedules", link: "/schedules", icon: Calendar },
];

const PAGE_ITEMS: CommandItem[] = [
  { category: "page", label: "Overview", description: "Dashboard overview", link: "/", icon: LayoutDashboard },
  { category: "page", label: "Runs", description: "All workflow runs", link: "/runs", icon: PlayCircle },
  { category: "page", label: "Workflows", description: "Manage workflows", link: "/workflows", icon: GitBranch },
  { category: "page", label: "Approvals", description: "Pending approvals", link: "/approvals", icon: ShieldCheck },
  { category: "page", label: "Integrations", description: "Connected tools", link: "/integrations", icon: Plug },
  { category: "page", label: "API Keys", description: "Manage API keys", link: "/api-keys", icon: Key },
  { category: "page", label: "Settings", description: "Application settings", link: "/settings", icon: Settings },
  { category: "page", label: "System Health", description: "Health overview", link: "/system-health", icon: HeartPulse },
  { category: "page", label: "Dead Letter", description: "Dead letter queue", link: "/dead-letter", icon: Inbox },
  { category: "page", label: "Schedules", description: "Scheduled workflows", link: "/schedules", icon: Calendar },
];

const CATEGORY_ORDER = ["action", "recent", "run", "workflow", "page"] as const;

const CATEGORY_LABELS: Record<string, string> = {
  action: "Actions",
  recent: "Recent",
  run: "Runs",
  workflow: "Workflows",
  page: "Pages",
};

/* ------------------------------------------------------------------ */
/*  Icon helper                                                       */
/* ------------------------------------------------------------------ */

function iconForRecent(item: RecentItem): React.ElementType {
  if (item.type === "run") return PlayCircle;
  if (item.type === "workflow") return GitBranch;
  return LayoutDashboard;
}

/* ------------------------------------------------------------------ */
/*  Component                                                         */
/* ------------------------------------------------------------------ */

export function CommandPalette({ open, onClose, recentItems }: CommandPaletteProps) {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [apiResults, setApiResults] = useState<SearchResult[]>([]);
  const searchVersionRef = useRef(0);

  // Focus input when palette opens
  useEffect(() => {
    if (open) {
      setQuery("");
      setApiResults([]);
      setActiveIndex(0);
      // Small delay so the DOM is ready
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  // API search with debounce
  useEffect(() => {
    if (!open) return;
    // Skip API search for slash commands or short queries
    if (query.startsWith("/") || query.length < 2) {
      setApiResults([]);
      return;
    }
    const timer = setTimeout(() => {
      void fetchResults(query);
    }, 250);
    return () => clearTimeout(timer);
  }, [query, open]); // eslint-disable-line react-hooks/exhaustive-deps

  const fetchResults = useCallback(async (q: string) => {
    const version = ++searchVersionRef.current;
    const lower = q.toLowerCase();
    const items: SearchResult[] = [];

    let runsRes, workflowsRes;
    try {
      [runsRes, workflowsRes] = await Promise.all([
        api.get<Array<{ run_id: string; workflow_name: string; status: string }>>("/runs", { limit: "50", offset: "0" }),
        api.get<Array<{ name: string; file_name: string; steps_count: number }>>("/workflows"),
      ]);
    } catch {
      return;
    }
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

    setApiResults(items.slice(0, 10));
  }, []);

  // Build the flat item list based on query
  const flatItems: CommandItem[] = useMemo(() => {
    const lower = query.toLowerCase().trim();

    // Slash commands mode
    if (lower.startsWith("/")) {
      const slashQuery = lower.slice(1);
      const filtered = ACTION_COMMANDS.filter(
        (c) =>
          c.label.toLowerCase().includes(lower) ||
          c.description.toLowerCase().includes(slashQuery)
      );
      return filtered;
    }

    // Empty query: lead with the omnibox action, then recent + pages
    if (lower.length === 0) {
      const omniboxAction = ACTION_COMMANDS.find((c) => c.action === "omnibox");
      const recent: CommandItem[] = recentItems.map((ri) => ({
        category: "recent" as const,
        label: ri.label,
        description: ri.type === "run" ? "Recent run" : ri.type === "workflow" ? "Recent workflow" : "Recent page",
        link: ri.id,
        icon: iconForRecent(ri),
      }));
      return [...(omniboxAction ? [omniboxAction] : []), ...recent, ...PAGE_ITEMS];
    }

    // Search mode: combine matching actions + API results + page matches
    const matchedActions = ACTION_COMMANDS.filter(
      (c) =>
        c.action === "omnibox" &&
        (c.label.toLowerCase().includes(lower) ||
          c.description.toLowerCase().includes(lower) ||
          "describe workflow agent".includes(lower)),
    );

    const matchedPages = PAGE_ITEMS.filter(
      (p) =>
        p.label.toLowerCase().includes(lower) ||
        p.description.toLowerCase().includes(lower)
    );

    const runItems: CommandItem[] = apiResults
      .filter((r) => r.type === "run")
      .map((r) => ({
        category: "run" as const,
        label: r.label,
        description: r.sub,
        link: r.link,
        icon: PlayCircle,
      }));

    const workflowItems: CommandItem[] = apiResults
      .filter((r) => r.type === "workflow")
      .map((r) => ({
        category: "workflow" as const,
        label: r.label,
        description: r.sub,
        link: r.link,
        icon: GitBranch,
      }));

    return [...matchedActions, ...runItems, ...workflowItems, ...matchedPages];
  }, [query, recentItems, apiResults]);

  // Group items by category, preserving order
  const grouped = useMemo(() => {
    const map = new Map<string, CommandItem[]>();
    for (const item of flatItems) {
      const cat = item.category;
      if (!map.has(cat)) map.set(cat, []);
      map.get(cat)!.push(item);
    }
    const result: { category: string; items: CommandItem[] }[] = [];
    for (const cat of CATEGORY_ORDER) {
      const items = map.get(cat);
      if (items && items.length > 0) {
        result.push({ category: cat, items });
      }
    }
    return result;
  }, [flatItems]);

  // Reset active index when items change
  useEffect(() => {
    setActiveIndex(0);
  }, [flatItems.length, query]);

  // Scroll active item into view
  useEffect(() => {
    if (!listRef.current) return;
    const el = listRef.current.querySelector(`[data-index="${activeIndex}"]`);
    if (el) {
      el.scrollIntoView({ block: "nearest" });
    }
  }, [activeIndex]);

  function handleSelect(item: CommandItem) {
    if (item.action === "omnibox") {
      // Go to the Overview, then focus the omnibox once it's mounted.
      navigate("/");
      onClose();
      // Defer so the Overview (and its omnibox listener) has mounted.
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          window.dispatchEvent(new CustomEvent(OMNIBOX_FOCUS_EVENT));
        });
      });
      return;
    }
    const url = item.link + (item.search ?? "");
    navigate(url);
    onClose();
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((prev) => (prev + 1 >= flatItems.length ? 0 : prev + 1));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((prev) => (prev <= 0 ? flatItems.length - 1 : prev - 1));
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      if (flatItems[activeIndex]) {
        handleSelect(flatItems[activeIndex]);
      }
      return;
    }
  }

  if (!open) return null;

  let globalIdx = -1;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-[60] bg-black/50 backdrop-blur-sm transition-opacity"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Palette */}
      <div className="fixed inset-0 z-[61] flex items-start justify-center pt-[15vh] px-4">
        <div
          role="dialog"
          aria-label="Command palette"
          className={cn(
            "w-full max-w-lg overflow-hidden rounded-xl border border-border bg-surface shadow-2xl",
            "animate-in fade-in slide-in-from-top-2 duration-150"
          )}
        >
          {/* Search input */}
          <div className="flex items-center gap-3 border-b border-border px-4">
            <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder='Search or type "/" for commands...'
              className={cn(
                "h-12 flex-1 bg-transparent text-sm text-foreground outline-none",
                "placeholder:text-muted-foreground/50"
              )}
              role="combobox"
              aria-expanded={flatItems.length > 0}
              aria-haspopup="listbox"
              aria-autocomplete="list"
              aria-activedescendant={
                flatItems.length > 0 ? `cmd-item-${activeIndex}` : undefined
              }
            />
            <kbd className="hidden sm:inline-flex items-center rounded border border-border bg-background px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
              ESC
            </kbd>
          </div>

          {/* Results */}
          <div
            ref={listRef}
            role="listbox"
            className="max-h-80 overflow-y-auto overscroll-contain py-1"
          >
            {flatItems.length === 0 && query.length >= 2 && (
              <div className="px-4 py-6 text-center">
                <p className="text-sm text-muted-foreground">
                  No results for &quot;{query}&quot;
                </p>
              </div>
            )}

            {grouped.map((group) => (
              <div key={group.category}>
                {/* Category header */}
                <div className="px-4 pt-2 pb-1">
                  <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                    {CATEGORY_LABELS[group.category] ?? group.category}
                  </span>
                </div>

                {group.items.map((item) => {
                  globalIdx++;
                  const idx = globalIdx;
                  const isActive = idx === activeIndex;
                  const Icon = item.icon;

                  return (
                    <button
                      key={`${item.category}-${item.link}-${item.label}-${idx}`}
                      id={`cmd-item-${idx}`}
                      data-index={idx}
                      role="option"
                      aria-selected={isActive}
                      onClick={() => handleSelect(item)}
                      onMouseEnter={() => setActiveIndex(idx)}
                      className={cn(
                        "flex w-full items-center gap-3 px-4 py-2 text-left transition-colors",
                        isActive
                          ? "bg-accent/10 border-l-2 border-accent"
                          : "border-l-2 border-transparent hover:bg-border/30"
                      )}
                    >
                      <Icon
                        className={cn(
                          "h-4 w-4 shrink-0",
                          isActive ? "text-accent" : "text-muted-foreground"
                        )}
                      />
                      <div className="min-w-0 flex-1">
                        <span
                          className={cn(
                            "text-sm",
                            isActive ? "text-foreground font-medium" : "text-foreground"
                          )}
                        >
                          {item.label}
                        </span>
                      </div>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {item.description}
                      </span>
                      {item.category === "recent" && (
                        <Clock className="h-3 w-3 shrink-0 text-muted-foreground/50" />
                      )}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>

          {/* Footer hint */}
          <div className="flex items-center gap-4 border-t border-border px-4 py-2">
            <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
              <kbd className="rounded border border-border bg-background px-1 py-0.5 font-mono text-[10px]">&uarr;&darr;</kbd>
              navigate
            </span>
            <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
              <kbd className="rounded border border-border bg-background px-1 py-0.5 font-mono text-[10px]">&crarr;</kbd>
              select
            </span>
            <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
              <kbd className="rounded border border-border bg-background px-1 py-0.5 font-mono text-[10px]">/</kbd>
              commands
            </span>
          </div>
        </div>
      </div>
    </>
  );
}
