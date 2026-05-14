import { Skeleton } from "@/components/ui/Skeleton";

/** Initial loading skeleton for the Overview page. */
export function BentoLoadingSkeleton() {
  return (
    <div className="space-y-4 sm:space-y-5">
      <div className="flex items-center justify-between">
        <Skeleton className="h-8 w-36 rounded-xl" />
        <Skeleton className="h-8 w-28 rounded-xl" />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 sm:gap-5">
        <Skeleton className="h-56 rounded-2xl lg:col-span-2" />
        <Skeleton className="h-56 rounded-2xl" />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 sm:gap-5">
        {[0, 1, 2].map((i) => <Skeleton key={i} className="h-28 rounded-2xl" />)}
      </div>
      <Skeleton className="h-36 rounded-2xl" />
    </div>
  );
}

/** Page-level error state shown when the above-fold fetch fails. */
export function BentoErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold tracking-tight text-foreground">Overview</h1>
      <div className="rounded-2xl border border-error/30 bg-error/5 p-5">
        <p className="text-sm text-error">{message}</p>
        <button
          onClick={onRetry}
          className="mt-2 text-xs font-semibold text-accent hover:text-accent-hover transition-colors"
        >
          Retry
        </button>
      </div>
    </div>
  );
}
