import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RotateCcw, Download } from "lucide-react";
import { isChunkLoadError } from "@/lib/lazyWithRetry";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  /** Optional label for identifying which boundary caught the error in logs. */
  name?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
  isNetworkError: boolean;
  isChunkError: boolean;
  isOffline: boolean;
}

/**
 * Error boundary that catches render errors in its subtree.
 * Distinguishes between a stale-deploy chunk miss, a network error, and a
 * generic application error so each gets an accurate, actionable message.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null, isNetworkError: false, isChunkError: false, isOffline: false };

  static getDerivedStateFromError(error: Error): State {
    // A failed chunk import reads as a network error to the naive matcher, but
    // the cure is "reload for the new build", not "check your connection".
    // Classify it first so the user gets the right message.
    const isChunkError = isChunkLoadError(error);
    const isNetworkError =
      !isChunkError &&
      error.name === "TypeError" &&
      /fetch|network|load/i.test(error.message);
    // A chunk miss while offline is not a deploy - don't claim one. Compute it
    // here so render() stays a pure function of state.
    const isOffline = typeof navigator !== "undefined" && !navigator.onLine;
    return { hasError: true, error, isNetworkError, isChunkError, isOffline };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    const label = this.props.name ?? "unknown";
    console.error(
      `[Sandcastle] ErrorBoundary(${label}) caught error:`,
      error,
      info.componentStack
    );
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null, isNetworkError: false, isChunkError: false, isOffline: false });
  };

  handleHardReload = () => {
    // Clear the one-shot reload guards so the fresh build is allowed to
    // auto-retry its chunks again after this manual reload.
    try {
      const s = window.sessionStorage;
      for (let i = s.length - 1; i >= 0; i--) {
        const k = s.key(i);
        if (k && k.startsWith("sc:chunk-reload:")) s.removeItem(k);
      }
    } catch {
      /* storage unavailable - reload still works */
    }
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      // A stale-deploy chunk miss is recoverable by reloading the app; show
      // that directly rather than the page-level fallback or a network error.
      // When offline the same failure shape means "you're disconnected", not
      // "a new version deployed" - don't assert a deploy that didn't happen.
      if (this.state.isChunkError) {
        const offline = this.state.isOffline;
        return (
          <div className="flex flex-col items-center justify-center py-24 gap-6">
            <div className="flex items-center justify-center w-16 h-16 rounded-2xl bg-accent/10">
              <Download className="w-8 h-8 text-accent" />
            </div>
            <div className="text-center space-y-2">
              <h2 className="text-lg font-semibold text-foreground">
                {offline ? "Couldn't load this page" : "Update available"}
              </h2>
              <p className="text-sm text-muted max-w-md">
                {offline
                  ? "You appear to be offline. Reconnect, then reload to try again."
                  : "A newer version of the dashboard was just deployed. Reload to load the latest build."}
              </p>
            </div>
            <button
              onClick={this.handleHardReload}
              className="flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground hover:bg-accent-hover transition-settle"
            >
              <RotateCcw className="w-4 h-4" />
              Reload
            </button>
          </div>
        );
      }

      if (this.props.fallback) return this.props.fallback;

      return (
        <div className="flex flex-col items-center justify-center py-24 gap-6">
          <div className="flex items-center justify-center w-16 h-16 rounded-2xl bg-error/10">
            <AlertTriangle className="w-8 h-8 text-error" />
          </div>
          <div className="text-center space-y-2">
            <h2 className="text-lg font-semibold text-foreground">
              {this.state.isNetworkError
                ? "Connection error"
                : "Something went wrong"}
            </h2>
            <p className="text-sm text-muted max-w-md">
              {this.state.isNetworkError
                ? "Could not reach the server. Check your network connection and try again."
                : (this.state.error?.message || "An unexpected error occurred")}
            </p>
            {!this.state.isNetworkError && this.state.error && (
              <details className="mt-3 text-left max-w-lg">
                <summary className="text-xs text-muted cursor-pointer hover:text-foreground">Technical details</summary>
                <pre className="mt-2 max-h-40 overflow-auto rounded bg-background p-2 text-[10px] text-muted font-mono whitespace-pre-wrap">
                  {this.state.error.stack || this.state.error.message}
                </pre>
              </details>
            )}
          </div>
          <button
            onClick={this.handleReset}
            className="flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground hover:bg-accent-hover transition-settle"
          >
            <RotateCcw className="w-4 h-4" />
            Try Again
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

interface SectionErrorBoundaryProps {
  children: ReactNode;
  /** Short label for the failing section, shown inline (e.g. "charts", "heatmap"). */
  section: string;
}

/**
 * Compact per-section error boundary. Renders an inline retry card instead of
 * the full page-level fallback, so a single failing API does not crash the
 * whole page.
 */
export function SectionErrorBoundary({ children, section }: SectionErrorBoundaryProps) {
  return (
    <ErrorBoundary
      name={`section:${section}`}
      fallback={<SectionErrorFallback section={section} />}
    >
      {children}
    </ErrorBoundary>
  );
}

function SectionErrorFallback({ section }: { section: string }) {
  return (
    <div
      role="alert"
      className="flex items-center justify-between gap-3 rounded-2xl border border-error/30 bg-error/5 px-4 py-3"
    >
      <div className="flex items-center gap-2.5 min-w-0">
        <AlertTriangle className="h-4 w-4 text-error shrink-0" />
        <p className="text-sm text-foreground truncate">
          The <span className="font-semibold">{section}</span> section failed to load.
        </p>
      </div>
      <button
        onClick={() => window.location.reload()}
        className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-foreground hover:border-accent/40 transition-colors shrink-0"
      >
        <RotateCcw className="h-3 w-3" />
        Retry
      </button>
    </div>
  );
}
