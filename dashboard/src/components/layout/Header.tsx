import { useContext, useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Menu, Search, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "@/components/shared/ThemeToggle";
import { EventStreamContext } from "@/hooks/useEventStreamContext";
import {
  NotificationCenter,
  type Notification,
} from "@/components/layout/NotificationCenter";
import { api } from "@/api/client";
import { useUpdateCheck } from "@/hooks/useUpdateCheck";

interface HeaderProps {
  onMenuToggle: () => void;
  onOpenPalette: () => void;
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
  "/time-machine": "Time Machine",
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

const PROVIDER_BADGE_LABEL: Record<string, string> = {
  anthropic: "Claude \uD83C\uDDFA\uD83C\uDDF8",
  mistral: "Mistral \uD83C\uDDEA\uD83C\uDDFA",
  openai: "OpenAI \uD83C\uDDFA\uD83C\uDDF8",
  ollama: "Ollama \uD83C\uDFE0",
  google: "Gemini \uD83C\uDDFA\uD83C\uDDF8",
  minimax: "MiniMax \uD83C\uDDFA\uD83C\uDDF8",
};

/** Fetch current advisor provider once and cache in module scope. */
let _cachedProvider: string | null = null;

function useAdvisorProviderBadge(): string | null {
  const [provider, setProvider] = useState<string | null>(_cachedProvider);
  useEffect(() => {
    if (_cachedProvider) return;
    api.get<{ current_provider: string }>("/advisor/status").then((res) => {
      if (res.data?.current_provider) {
        _cachedProvider = res.data.current_provider;
        setProvider(res.data.current_provider);
      }
    }).catch(() => undefined);
  }, []);
  return provider;
}

/** Fetch the Spark Mode flag once and cache in module scope. */
let _cachedSparkMode: boolean | null = null;

function useSparkModeBadge(): boolean {
  const [sparkMode, setSparkMode] = useState<boolean>(_cachedSparkMode ?? false);
  useEffect(() => {
    if (_cachedSparkMode !== null) return;
    api
      .get<{ spark_mode: boolean }>("/runtime")
      .then((res) => {
        if (res.data?.spark_mode !== undefined) {
          _cachedSparkMode = res.data.spark_mode;
          setSparkMode(res.data.spark_mode);
        }
      })
      .catch(() => undefined);
  }, []);
  return sparkMode;
}

const CONNECTION_LABEL: Record<string, string> = {
  connected: "Live",
  connecting: "Connecting",
  disconnected: "Offline",
};

/**
 * Quiet system-status chip: connection dot + active model provider, in one
 * muted, clickable pill that links to provider settings. The connection state
 * is carried by the dot colour (+ tooltip) instead of an alarming "Offline"
 * word sitting bare in the bar.
 */
function StatusChip({ provider }: { provider: string | null }) {
  // Read the stream context null-safe: the header must not crash when no
  // EventStreamProvider is mounted (e.g. isolated tests, error boundaries).
  const connectionStatus = useContext(EventStreamContext)?.connectionStatus ?? "connecting";
  const label = provider
    ? (PROVIDER_BADGE_LABEL[provider] ?? provider)
    : CONNECTION_LABEL[connectionStatus] ?? "Provider";

  return (
    <Link
      to="/settings?tab=providers"
      data-testid="live-indicator"
      title={`Model provider${provider ? `: ${PROVIDER_BADGE_LABEL[provider] ?? provider}` : ""} · Stream: ${connectionStatus} — open Providers`}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border border-border/70 bg-background px-2.5 py-1",
        "text-[11px] font-medium text-muted-foreground transition-colors",
        "hover:text-foreground hover:border-accent/40",
      )}
    >
      <span
        className={cn(
          "inline-block h-2 w-2 rounded-full shrink-0",
          connectionStatus === "connected" && "bg-success animate-pulse",
          connectionStatus === "connecting" && "bg-warning animate-pulse",
          connectionStatus === "disconnected" && "bg-muted-foreground/50",
        )}
      />
      <span className="whitespace-nowrap">{label}</span>
    </Link>
  );
}

const DISMISS_KEY = "sandcastle_update_banner_dismissed";
const DISMISS_DURATION_MS = 7 * 24 * 60 * 60 * 1000; // 7 days

/** Check whether the update banner was dismissed within the last 7 days. */
function isBannerDismissed(): boolean {
  try {
    const raw = localStorage.getItem(DISMISS_KEY);
    if (!raw) return false;
    const ts = Number(raw);
    if (Number.isNaN(ts)) return false;
    return Date.now() - ts < DISMISS_DURATION_MS;
  } catch {
    return false;
  }
}

export function Header({
  onMenuToggle,
  onOpenPalette,
  notifications,
  onMarkAllRead,
  onClickNotification,
}: HeaderProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const advisorProvider = useAdvisorProviderBadge();
  const sparkMode = useSparkModeBadge();
  const update = useUpdateCheck();
  const [bannerDismissed, setBannerDismissed] = useState(isBannerDismissed);

  const pageTitle = getPageTitle(location.pathname);

  const showBanner = update.updateAvailable && !update.loading && !bannerDismissed;

  const handleDismiss = () => {
    try {
      localStorage.setItem(DISMISS_KEY, String(Date.now()));
    } catch {
      // localStorage unavailable
    }
    setBannerDismissed(true);
  };

  const handleUpdateNow = () => {
    navigate("/settings");
    // Scroll to the update section after navigation
    setTimeout(() => {
      const el = document.getElementById("software-update-section");
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 100);
  };

  return (
    <>
      {/* Update available banner */}
      {showBanner && (
        <div className="sticky top-0 z-40 flex items-center justify-center gap-2 sm:gap-3 bg-warning/15 border-b border-warning/30 px-3 py-1.5 text-xs sm:text-sm text-warning">
          <span className="shrink-0 font-medium">
            Sandcastle v{update.latestVersion} is available
          </span>
          {update.highlights.length > 0 && (
            <span className="hidden md:inline text-warning/70 truncate max-w-md" title={update.highlights.join(", ")}>
              - {update.highlights.join(", ")}
            </span>
          )}
          <div className="flex items-center gap-2 shrink-0">
            {update.changelogUrl && (
              <a
                href={update.changelogUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="underline underline-offset-2 hover:text-warning/80 transition-colors"
              >
                What's new
              </a>
            )}
            <button
              onClick={handleUpdateNow}
              className="rounded-md bg-warning/20 hover:bg-warning/30 border border-warning/40 px-2 py-0.5 font-medium transition-colors cursor-pointer"
            >
              Update now
            </button>
            <button
              onClick={handleDismiss}
              aria-label="Dismiss update banner"
              className="rounded p-0.5 hover:bg-warning/20 text-warning/60 hover:text-warning transition-colors cursor-pointer"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      )}

      <header
        className={cn(
          "sticky z-30 flex h-14 sm:h-16 items-center gap-2 sm:gap-4 border-b border-border bg-surface/80 px-3 sm:px-4 lg:px-6 backdrop-blur-sm",
          showBanner ? "top-[33px]" : "top-0"
        )}
      >
        <button
          onClick={onMenuToggle}
          aria-label="Toggle navigation menu"
          className="flex h-9 w-9 items-center justify-center rounded-lg text-muted hover:bg-border/50 hover:text-foreground lg:hidden"
        >
          <Menu className="h-5 w-5" />
        </button>

        {/* Page title / breadcrumb */}
        <h2 className="hidden text-sm font-semibold text-foreground lg:block">
          {pageTitle}
        </h2>

        {/* Command palette trigger - mobile */}
        <button
          onClick={onOpenPalette}
          aria-label="Open search"
          className="flex h-9 w-9 items-center justify-center rounded-lg text-muted hover:bg-border/50 hover:text-foreground sm:hidden"
        >
          <Search className="h-5 w-5" />
        </button>

        {/* Command palette trigger - desktop */}
        <button
          onClick={onOpenPalette}
          className={cn(
            "ml-auto hidden h-9 w-full max-w-sm items-center gap-2 rounded-lg border border-border bg-background px-3 text-sm sm:flex lg:ml-8",
            "text-muted-foreground/50 hover:border-accent/30 hover:text-muted-foreground",
            "transition-settle"
          )}
        >
          <Search className="h-4 w-4 shrink-0" />
          <span className="flex-1 text-left">Search or type / for commands...</span>
          <kbd className="pointer-events-none rounded border border-border bg-background px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
            {"\u2318"}K
          </kbd>
        </button>

        <div className={cn("flex items-center gap-1 sm:gap-2", "sm:ml-0 ml-auto")}>
          {/* STATUS — passive system state, quiet and clickable */}
          <div className="hidden sm:flex items-center gap-1.5">
            <StatusChip provider={advisorProvider} />
            {sparkMode && (
              <span
                className="inline-flex items-center gap-1 rounded-full border border-purple-500/30 bg-purple-500/15 px-2 py-1 text-[10px] font-medium text-purple-600 dark:text-purple-400"
                title="Running on a DGX Spark — local models, $0/run, data stays on-box"
              >
                ⚡ Spark
              </span>
            )}
          </div>

          {/* divider between status and controls */}
          <div className="hidden sm:block h-5 w-px bg-border mx-1" aria-hidden="true" />

          {/* CONTROLS — interactive actions, pinned right */}
          <ThemeToggle />
          <NotificationCenter
            notifications={notifications}
            onMarkAllRead={onMarkAllRead}
            onClickNotification={onClickNotification}
          />
        </div>
      </header>
    </>
  );
}
