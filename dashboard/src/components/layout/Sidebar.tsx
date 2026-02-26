import { NavLink } from "react-router-dom";
import {
  Calendar,
  Castle,
  ClipboardCheck,
  FlaskConical,
  Gauge,
  GitBranch,
  HeartPulse,
  Inbox,
  Key,
  Layers,
  LayoutDashboard,
  Plug,
  PlayCircle,
  Settings,
  ShieldAlert,
  ShieldCheck,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useRuntimeInfo } from "@/hooks/useRuntimeInfo";

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
}

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
    items: [
      { to: "/approvals", icon: ShieldCheck, label: "Approvals", badge: "approvals" },
      { to: "/evaluations", icon: ClipboardCheck, label: "Evaluations" },
      { to: "/autopilot", icon: FlaskConical, label: "AutoPilot" },
      { to: "/violations", icon: ShieldAlert, label: "Violations" },
      { to: "/optimizer", icon: Gauge, label: "Optimizer" },
      { to: "/schedules", icon: Calendar, label: "Schedules" },
      { to: "/dead-letter", icon: Inbox, label: "Dead Letter", badge: "dlq" },
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
  const version = info?.version ?? "0.15.0";

  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/40 lg:hidden"
          onClick={onClose}
        />
      )}

      <aside
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
            className="ml-auto rounded-lg p-1 text-muted hover:text-foreground lg:hidden"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <nav className="flex-1 overflow-y-auto px-3 py-3">
          {navSections.map((section, sectionIdx) => (
            <div key={section.label} className={cn(sectionIdx > 0 && "mt-5")}>
              <p className="mb-1.5 px-3 text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
                {section.label}
              </p>
              <div className="space-y-0.5">
                {section.items.map((item) => (
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
            </div>
          ))}
        </nav>

        <div className="border-t border-border px-5 py-4">
          <div className="flex items-center gap-2">
            <p className="text-xs text-muted-foreground">Sandcastle v{version}</p>
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
          </div>
        </div>
      </aside>
    </>
  );
}
