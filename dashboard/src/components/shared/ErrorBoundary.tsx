import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";

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
}

/**
 * Error boundary that catches render errors in its subtree.
 * Distinguishes between network errors and application errors
 * to provide more helpful messages.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null, isNetworkError: false };

  static getDerivedStateFromError(error: Error): State {
    const isNetworkError =
      error.name === "TypeError" && /fetch|network|load/i.test(error.message);
    return { hasError: true, error, isNetworkError };
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
    this.setState({ hasError: false, error: null, isNetworkError: false });
  };

  render() {
    if (this.state.hasError) {
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
            className="flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground hover:bg-accent-hover transition-all duration-200"
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
