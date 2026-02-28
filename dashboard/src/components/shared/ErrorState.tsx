/**
 * Shared error state component for pages that fail to load data.
 * Displays a red alert box with an error message and optional retry button.
 */

interface ErrorStateProps {
  /** The page title shown above the error box. */
  title?: string;
  /** The error message to display. */
  message: string;
  /** Called when the user clicks the Retry button. Omit to hide the button. */
  onRetry?: () => void;
}

export function ErrorState({ title, message, onRetry }: ErrorStateProps) {
  return (
    <div>
      {title && (
        <h1 className="mb-4 sm:mb-6 text-xl sm:text-2xl font-semibold tracking-tight text-foreground">
          {title}
        </h1>
      )}
      <div className="rounded-xl border border-error/30 bg-error/5 p-4">
        <p className="text-sm text-error">{message}</p>
        {onRetry && (
          <button
            onClick={onRetry}
            className="mt-2 text-xs font-medium text-accent hover:text-accent/80 transition-colors"
          >
            Retry
          </button>
        )}
      </div>
    </div>
  );
}
