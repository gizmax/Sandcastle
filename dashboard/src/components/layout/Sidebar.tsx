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
  Layers,
  LayoutDashboard,
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
import { useDensity, type Density, type NavGroupId } from "@/contexts/UiModeContext";

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
  /** Stable group id used for density visibility decisions. */
  id: NavGroupId;
  label: string;
  items: NavItem[];
  /** OPERATE renders as a collapsible disclosure. */
  collapsible?: boolean;
}

const OPS_STORAGE_KEY = "sandcastle-ops-expanded";

// -----------------------------------------------------------------------------
// 3-verb IA: Home (pinned) + BUILD / RUN / IMPROVE / OPERATE.
// Visibility per density is resolved at render time via groupVisible().
// -----------------------------------------------------------------------------
const HOME_ITEM: NavItem = {
  to: "/",
  icon: LayoutDashboard,
  label: "Home",
  end: true,
  shortcut: "⌘ 1",
};

const navSections: NavSection[] = [
  {
    id: "BUILD",
    label: "BUILD",
    items: [
      { to: "/workflows", icon: GitBranch, label: "Workflows", shortcut: "⌘ 3" },
      { to: "/templates", icon: Layers, label: "Template Hub" },
    ],
  },
  {
    id: "RUN",
    label: "RUN",
    items: [
      { to: "/runs", icon: PlayCircle, label: "Runs", shortcut: "⌘ 2" },
      { to: "/approvals", icon: ShieldCheck, label: "Approvals", badge: "approvals" },
      { to: "/schedules", icon: Calendar, label: "Schedules" },
    ],
  },
  {
    id: "IMPROVE",
    label: "IMPROVE",
    items: [
      { to: "/evolution", icon: Sparkles, label: "Evolution" },
      { to: "/autopilot", icon: FlaskConical, label: "AutoPilot" },
      { to: "/optimizer", icon: Gauge, label: "Optimizer" },
      { to: "/evaluations", icon: ClipboardCheck, label: "Evaluations" },
      { to: "/memory", icon: Brain, label: "Agent Memory" },
    ],
  },
  {
    id: "OPERATE",
    label: "OPERATE",
    collapsible: true,
    items: [
      { to: "/system-health", icon: HeartPulse, label: "System Health" },
      { to: "/dead-letter", icon: Inbox, label: "Dead Letter", badge: "dlq" },
      { to: "/violations", icon: ShieldAlert, label: "Violations" },
      { to: "/compliance", icon: Shield, label: "Compliance" },
      { to: "/integrations", icon: Plug, label: "Integrations" },
      { to: "/schedule-monitor", icon: CalendarClock, label: "Schedule Monitor" },
    ],
  },
];

export function Sidebar({ open, onClose, dlqCount = 0, approvalsCount = 0 }: SidebarProps) {
  const { info } = useRuntimeInfo();
  const { updateAvailable } = useUpdateCheck();
  const { pinnedWorkflows } = usePinnedWorkflows();
  const { effectiveDensity, setDensity, groupVisible, operateOpenByDefault } = useDensity();
  const version = info?.version ?? "-";

  // Density gates which groups are visible. HOME/BUILD/RUN are always shown;
  // IMPROVE + OPERATE appear at Standard+.
  const sections = navSections.filter((s) => groupVisible(s.id));

  const [opsExpanded, setOpsExpanded] = useState(() => {
    try {
      const stored = localStorage.getItem(OPS_STORAGE_KEY);
      if (stored === "true") return true;
      if (stored === "false") return false;
    } catch {
      // storage unavailable
    }
    // No explicit choice yet: open by default only on "Everything".
    return operateOpenByDefault;
  });

  const opsContentRef = useRef<HTMLDivElement>(null);

  const toggleOps = useCallback(() => {
    setOpsExpanded((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(OPS_STORAGE_KEY, String(next));
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
          {/* Home is pinned at the very top, outside any group. */}
          {renderItems([HOME_ITEM])}

          {/* Pinned workflows live under Home / BUILD. */}
          {pinnedWorkflows.length > 0 && (
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

          {sections.map((section) => (
            <div key={section.id} className="mt-5">
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
          ))}
        </nav>

        <div className="border-t border-border px-5 py-4 space-y-3">
          {/* Density control: 3-tier segmented selector. */}
          <DensityControl value={effectiveDensity} onChange={setDensity} />

          <NavLink
            to="/settings"
            onClick={onClose}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-all duration-200",
                isActive
                  ? "bg-accent/10 text-accent"
                  : "text-muted hover:bg-border/40 hover:text-foreground"
              )
            }
          >
            <Settings className="h-[18px] w-[18px] shrink-0" />
            <span className="flex-1">Settings</span>
          </NavLink>

          <NavLink
            to="/settings"
            onClick={onClose}
            className="flex items-center gap-2 px-3 group"
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

const DENSITY_OPTIONS: { value: Density; label: string; hint: string }[] = [
  { value: "Essentials", label: "Essentials", hint: "Just the basics: build & run" },
  { value: "Standard", label: "Standard", hint: "Adds the intelligence tools" },
  { value: "Everything", label: "Everything", hint: "Every page, operations open" },
];

function DensityControl({
  value,
  onChange,
}: {
  value: Density;
  onChange: (d: Density) => void;
}) {
  return (
    <div>
      <p className="mb-1.5 px-1 text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
        Density
      </p>
      <div
        role="radiogroup"
        aria-label="Interface density"
        className="flex items-center gap-0.5 rounded-lg border border-border p-0.5"
      >
        {DENSITY_OPTIONS.map((opt) => {
          const active = opt.value === value;
          return (
            <button
              key={opt.value}
              type="button"
              role="radio"
              aria-checked={active}
              title={opt.hint}
              onClick={() => onChange(opt.value)}
              className={cn(
                "flex-1 rounded-md px-1.5 py-1 text-[11px] font-medium transition-colors",
                active
                  ? "bg-accent/15 text-accent"
                  : "text-muted hover:text-foreground"
              )}
            >
              {opt.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
