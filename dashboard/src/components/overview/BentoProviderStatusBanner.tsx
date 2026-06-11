import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Key, Wifi, X } from "lucide-react";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";
import { StatusLed } from "@/components/ui/StatusLed";
import type { ProviderInfo } from "./bentoTypes";

const PROVIDER_BANNER_DISMISSED_KEY = "sandcastle_provider_banner_dismissed";

export function BentoProviderStatusBanner() {
  const navigate = useNavigate();
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [dismissed, setDismissed] = useState(() => {
    try {
      return localStorage.getItem(PROVIDER_BANNER_DISMISSED_KEY) === "true";
    } catch {
      return false;
    }
  });
  const [apiKey, setApiKey] = useState("");

  useEffect(() => {
    let cancelled = false;
    const fetchProviders = async () => {
      try {
        const res = await api.get<{ providers: ProviderInfo[] }>("/health/providers");
        if (cancelled) return;
        if (res.data?.providers) {
          setProviders(res.data.providers);
        }
      } catch {
        // Non-critical - banner simply won't show
      } finally {
        if (!cancelled) setLoaded(true);
      }
    };
    void fetchProviders();
    return () => { cancelled = true; };
  }, []);

  if (dismissed || !loaded || providers.length === 0) return null;

  const handleDismiss = () => {
    setDismissed(true);
    try {
      localStorage.setItem(PROVIDER_BANNER_DISMISSED_KEY, "true");
    } catch { /* ignore */ }
  };

  const ollamaRunning = providers.find((p) => p.id === "ollama" && p.status === "running");
  const hasAnyActive = providers.some((p) => p.status === "running" || p.status === "configured");

  const handleApiKeySubmit = () => {
    if (apiKey.trim()) {
      navigate("/settings");
    }
  };

  return (
    <div className="bg-surface rounded-2xl shadow-sm border border-border px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0 flex-1">
          <Wifi className="h-4 w-4 text-accent shrink-0" />

          <div className="flex items-center gap-2 flex-wrap">
            {providers.map((p) => {
              const isActive = p.status === "running" || p.status === "configured";
              return (
                <span
                  key={p.id}
                  className={cn(
                    "inline-flex items-center gap-1.5 px-1 text-xs font-medium",
                    isActive ? "text-success" : "text-muted-foreground",
                  )}
                >
                  <StatusLed
                    status={isActive ? "healthy" : "unconfigured"}
                    showLabel={false}
                  />
                  {p.name}
                  {p.status === "running" && p.latency_ms != null && (
                    <span className="font-mono text-[10px] text-success/70">{p.latency_ms}ms</span>
                  )}
                </span>
              );
            })}
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {ollamaRunning && (
            <span className="text-xs font-medium text-success whitespace-nowrap">
              Ollama detected - ready to use!
            </span>
          )}

          {!hasAnyActive && (
            <div className="flex items-center gap-1.5">
              <div className="relative">
                <Key className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-muted-foreground" />
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") handleApiKeySubmit(); }}
                  placeholder="Paste any API key to start"
                  className="h-7 w-48 rounded-lg border border-border bg-background pl-7 pr-2 text-xs text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:border-accent"
                />
              </div>
              <button
                onClick={handleApiKeySubmit}
                disabled={!apiKey.trim()}
                className={cn(
                  "h-7 px-3 rounded-lg text-xs font-medium transition-colors",
                  apiKey.trim()
                    ? "bg-accent text-accent-foreground hover:bg-accent/90"
                    : "bg-muted text-muted-foreground cursor-not-allowed",
                )}
              >
                Go
              </button>
            </div>
          )}

          <button
            onClick={handleDismiss}
            className="text-muted-foreground hover:text-foreground transition-colors"
            aria-label="Dismiss provider banner"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}
