import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Menu, Search, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "@/components/shared/ThemeToggle";
import { LiveIndicator } from "@/components/shared/LiveIndicator";
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
            "transition-all duration-200"
          )}
        >
          <Search className="h-4 w-4 shrink-0" />
          <span className="flex-1 text-left">Search or type / for commands...</span>
          <kbd className="pointer-events-none rounded border border-border bg-background px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
            {"\u2318"}K
          </kbd>
        </button>

        <div className={cn("flex items-center gap-1 sm:gap-2", "sm:ml-0 ml-auto")}>
          {advisorProvider && (
            <span className="hidden sm:inline-flex rounded-full bg-accent/10 px-2 py-0.5 text-[10px] font-medium text-accent">
              {PROVIDER_BADGE_LABEL[advisorProvider] ?? advisorProvider}
            </span>
          )}
          <LiveIndicator />
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
