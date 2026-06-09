import { useCallback, useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import {
  Brain,
  Calendar,
  CalendarClock,
  Castle,
  ChevronRight,
  ClipboardCheck,
  FlaskConical,
  Gauge,
  GitBranch,
  HeartPulse,
  Inbox,
  Key,
  Layers,
  LayoutDashboard,
  MoonStar,
  Plug,
  PlayCircle,
  Settings,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Star,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useRuntimeInfo } from "@/hooks/useRuntimeInfo";
import { useUpdateCheck } from "@/hooks/useUpdateCheck";
import { usePinnedWorkflows } from "@/hooks/usePinnedWorkflows";
import { useUiMode } from "@/contexts/UiModeContext";

interface SidebarProps {
  open: boolean;
  onClose: () => void;
  dlqCount?: number;
  approvalsCount?: number;
}

interface NavItem {
  to: string;
  icon: typeof LayoutDashboard;
  label: string;
  end?: boolean;
  badge?: "dlq" | "approvals";
  shortcut?: string;
}

interface NavSection {
  label: string;
  items: NavItem[];
  collapsible?: boolean;
}

const STORAGE_KEY = "sandcastle-ops-expanded";

const navSections: NavSection[] = [
  {
    label: "MAIN",
    items: [
      { to: "/", icon: LayoutDashboard, label: "Overview", end: true, shortcut: "\u2318 1" },
      { to: "/runs", icon: PlayCircle, label: "Runs", shortcut: "\u2318 2" },
      { to: "/workflows", icon: GitBranch, label: "Workflows", shortcut: "\u2318 3" },
    ],
  },
  {
    label: "DISCOVER",
    items: [
      { to: "/templates", icon: Layers, label: "Template Hub" },
      { to: "/integrations", icon: Plug, label: "Integrations" },
    ],
  },
  {
    label: "OPERATIONS",
    collapsible: true,
    items: [
      { to: "/approvals", icon: ShieldCheck, label: "Approvals", badge: "approvals" },
      { to: "/evaluations", icon: ClipboardCheck, label: "Evaluations" },
      { to: "/autopilot", icon: FlaskConical, label: "AutoPilot" },
      { to: "/evolution", icon: Sparkles, label: "Evolution" },
      { to: "/night-shift", icon: MoonStar, label: "Night Shift" },
      { to: "/violations", icon: ShieldAlert, label: "Violations" },
      { to: "/compliance", icon: Shield, label: "Compliance" },
      { to: "/optimizer", icon: Gauge, label: "Optimizer" },
      { to: "/schedules", icon: Calendar, label: "Schedules" },
      { to: "/schedule-monitor", icon: CalendarClock, label: "Schedule Monitor" },
      { to: "/dead-letter", icon: Inbox, label: "Dead Letter", badge: "dlq" },
      { to: "/memory", icon: Brain, label: "Agent Memory" },
    ],
  },
  {
    label: "SYSTEM",
    items: [
      { to: "/system-health", icon: HeartPulse, label: "System Health" },
      { to: "/api-keys", icon: Key, label: "API Keys" },
      { to: "/settings", icon: Settings, label: "Settings" },
    ],
  },
];

export function Sidebar({ open, onClose, dlqCount = 0, approvalsCount = 0 }: SidebarProps) {
  const { info } = useRuntimeInfo();
  const { updateAvailable } = useUpdateCheck();
  const { pinnedWorkflows } = usePinnedWorkflows();
  const { isLite, setMode } = useUiMode();
  const version = info?.version ?? "-";

  // In Lite mode, show only the beginner-relevant navigation: drop the whole
  // OPERATIONS section and API Keys; advanced pages are route-guarded too.
  const sections = isLite
    ? navSections
        .filter((s) => s.label !== "OPERATIONS")
        .map((s) =>
          s.label === "SYSTEM"
            ? { ...s, items: s.items.filter((i) => i.to !== "/api-keys") }
            : s
        )
    : navSections;

  const [opsExpanded, setOpsExpanded] = useState(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === "true";
    } catch {
      return false;
    }
  });

  const opsContentRef = useRef<HTMLDivElement>(null);

  const toggleOps = useCallback(() => {
    setOpsExpanded((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(STORAGE_KEY, String(next));
      } catch {
        // storage unavailable
      }
      return next;
    });
  }, []);

  // Keep max-height in sync with content for smooth animation
  useEffect(() => {
    const el = opsContentRef.current;
    if (!el) return;
    if (opsExpanded) {
      el.style.maxHeight = el.scrollHeight + "px";
      const onEnd = () => {
        el.style.maxHeight = "none";
      };
      el.addEventListener("transitionend", onEnd, { once: true });
      return () => el.removeEventListener("transitionend", onEnd);
    } else {
      // Collapse: set explicit height first so transition works
      el.style.maxHeight = el.scrollHeight + "px";
      // Force reflow
      void el.offsetHeight;
      el.style.maxHeight = "0px";
    }
  }, [opsExpanded]);

  const opsBadgeCount = dlqCount + approvalsCount;

  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/40 lg:hidden"
          aria-hidden="true"
          onClick={onClose}
        />
      )}

      <aside
        aria-label="Main navigation"
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-64 flex-col border-r border-border bg-surface",
          "lg:static lg:z-auto",
          "transition-transform duration-300 ease-in-out",
          open ? "translate-x-0" : "-translate-x-full lg:translate-x-0"
        )}
      >
        <div className="flex h-16 items-center gap-3 border-b border-border px-5">
          <div className="glow-accent flex h-8 w-8 items-center justify-center rounded-lg bg-accent/15">
            <Castle className="h-5 w-5 text-accent" />
          </div>
          <span className="text-lg font-semibold tracking-tight text-foreground">
            Sandcastle
          </span>
          <button
            onClick={onClose}
            aria-label="Close navigation"
            className="ml-auto rounded-lg p-1 text-muted hover:text-foreground lg:hidden"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <nav className="flex-1 overflow-y-auto px-3 py-3" aria-label="Sidebar">
          {sections.map((section, sectionIdx) => {
            const renderItems = (items: NavItem[]) => (
              <div className="space-y-0.5">
                {items.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.end}
                    onClick={onClose}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium",
                        "transition-all duration-200",
                        isActive
                          ? "bg-accent/10 text-accent"
                          : "text-muted hover:bg-border/40 hover:text-foreground"
                      )
                    }
                  >
                    <item.icon className="h-[18px] w-[18px] shrink-0" />
                    <span className="flex-1">{item.label}</span>
                    {item.shortcut && (
                      <span className="hidden text-[10px] font-normal text-muted-foreground/50 lg:inline">
                        {item.shortcut}
                      </span>
                    )}
                    {item.badge === "dlq" && dlqCount > 0 && (
                      <span className="flex h-5 min-w-5 items-center justify-center rounded-full bg-error px-1.5 text-[11px] font-semibold text-white">
                        {dlqCount}
                      </span>
                    )}
                    {item.badge === "approvals" && approvalsCount > 0 && (
                      <span className="flex h-5 min-w-5 items-center justify-center rounded-full bg-warning px-1.5 text-[11px] font-semibold text-white">
                        {approvalsCount}
                      </span>
                    )}
                  </NavLink>
                ))}
              </div>
            );

            return (
              <div key={section.label}>
                <div className={cn(sectionIdx > 0 && "mt-5")}>
                  {section.collapsible ? (
                    <>
                      <button
                        type="button"
                        onClick={toggleOps}
                        className="mb-1.5 flex w-full items-center gap-1 px-3 group"
                        aria-expanded={opsExpanded}
                        aria-controls="sidebar-ops-section"
                      >
                        <ChevronRight
                          aria-hidden="true"
                          className={cn(
                            "h-3 w-3 text-muted-foreground transition-transform duration-200",
                            opsExpanded && "rotate-90"
                          )}
                        />
                        <span className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground group-hover:text-foreground transition-colors">
                          {section.label}
                        </span>
                        {!opsExpanded && opsBadgeCount > 0 && (
                          <span
                            aria-label={`${opsBadgeCount} items need attention`}
                            className="ml-auto flex h-4 min-w-4 items-center justify-center rounded-full bg-warning/80 px-1 text-[10px] font-semibold text-white"
                          >
                            {opsBadgeCount}
                          </span>
                        )}
                      </button>
                      <div
                        id="sidebar-ops-section"
                        ref={opsContentRef}
                        className="overflow-hidden transition-[max-height] duration-300 ease-in-out"
                        style={{ maxHeight: opsExpanded ? "none" : "0px" }}
                      >
                        {renderItems(section.items)}
                      </div>
                    </>
                  ) : (
                    <>
                      <p className="mb-1.5 px-3 text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
                        {section.label}
                      </p>
                      {renderItems(section.items)}
                    </>
                  )}
                </div>

                {/* Pinned workflows section - rendered after MAIN (full mode only) */}
                {section.label === "MAIN" && !isLite && pinnedWorkflows.length > 0 && (
                  <div className="mt-5">
                    <p className="mb-1.5 px-3 text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
                      Pinned
                    </p>
                    <div className="space-y-0.5">
                      {pinnedWorkflows.map((wfName) => (
                        <NavLink
                          key={`pinned-${wfName}`}
                          to={`/workflows/${encodeURIComponent(wfName)}`}
                          onClick={onClose}
                          className={({ isActive }) =>
                            cn(
                              "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium",
                              "transition-all duration-200",
                              isActive
                                ? "bg-accent/10 text-accent"
                                : "text-muted hover:bg-border/40 hover:text-foreground"
                            )
                          }
                        >
                          <Star className="h-[14px] w-[14px] shrink-0 fill-current text-accent/60" />
                          <span className="flex-1 truncate">{wfName}</span>
                        </NavLink>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </nav>

        <div className="border-t border-border px-5 py-4 space-y-3">
          <button
            type="button"
            onClick={() => setMode(isLite ? "full" : "lite")}
            title={isLite ? "Show all advanced features" : "Switch to the simplified beginner view"}
            className="flex w-full items-center justify-between rounded-lg border border-border px-3 py-2 text-xs font-medium text-muted hover:text-foreground hover:border-accent/40 transition-colors"
          >
            <span>{isLite ? "Lite mode" : "Full mode"}</span>
            <span className="text-accent">{isLite ? "Switch to Full" : "Switch to Lite"}</span>
          </button>
          <NavLink
            to="/settings"
            onClick={onClose}
            className="flex items-center gap-2 group"
          >
            <p className="text-xs text-muted-foreground group-hover:text-foreground transition-colors">
              Sandcastle v{version}
            </p>
            {updateAvailable && (
              <span className="relative flex h-2 w-2" title="Update available">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-warning opacity-75" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-warning" />
              </span>
            )}
            {info?.license && info.license.status === "valid" && (
              <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-success/15 border border-success/30 text-success">
                {info.license.tier === "enterprise" ? "Enterprise" : "Pro"}
              </span>
            )}
            {info?.license && info.license.status === "expired" && (
              <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-warning/15 border border-warning/30 text-warning">
                Expired
              </span>
            )}
          </NavLink>
        </div>
      </aside>
    </>
  );
}
