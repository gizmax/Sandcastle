import { NavLink } from "react-router-dom";
import {
  Calendar,
  Castle,
  FlaskConical,
  Gauge,
  GitBranch,
  Inbox,
  Key,
  Layers,
  LayoutDashboard,
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

const navItems = [
  { to: "/", icon: LayoutDashboard, label: "Overview", end: true },
  { to: "/runs", icon: PlayCircle, label: "Runs" },
  { to: "/workflows", icon: GitBranch, label: "Workflows" },
  { to: "/templates", icon: Layers, label: "Templates" },
  { to: "/approvals", icon: ShieldCheck, label: "Approvals", badge: "approvals" as const },
  { to: "/autopilot", icon: FlaskConical, label: "AutoPilot" },
  { to: "/violations", icon: ShieldAlert, label: "Violations" },
  { to: "/optimizer", icon: Gauge, label: "Optimizer" },
  { to: "/schedules", icon: Calendar, label: "Schedules" },
  { to: "/dead-letter", icon: Inbox, label: "Dead Letter", badge: "dlq" as const },
  { to: "/api-keys", icon: Key, label: "API Keys" },
  { to: "/settings", icon: Settings, label: "Settings" },
];

export function Sidebar({ open, onClose, dlqCount = 0, approvalsCount = 0 }: SidebarProps) {
  const { info } = useRuntimeInfo();
  const version = info?.version ?? "0.9.0";

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
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent/15">
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

        <nav className="flex-1 space-y-1 px-3 py-4">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              onClick={onClose}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium",
                  "transition-all duration-200",
                  isActive
                    ? "bg-accent/10 text-accent"
                    : "text-muted hover:bg-border/40 hover:text-foreground"
                )
              }
            >
              <item.icon className="h-[18px] w-[18px] shrink-0" />
              <span className="flex-1">{item.label}</span>
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
        </nav>

        <div className="border-t border-border px-5 py-4">
          <p className="text-xs text-muted-foreground">Sandcastle v{version}</p>
        </div>
      </aside>
    </>
  );
}
