import { cn } from "@/lib/utils";
import { useEventStreamContext } from "@/hooks/useEventStreamContext";

/**
 * Small indicator showing the SSE connection status.
 * Green pulsing dot + "Live" when connected.
 * Yellow dot + "Connecting" when reconnecting.
 * Red dot + "Offline" when disconnected.
 */
export function LiveIndicator() {
  const { connectionStatus } = useEventStreamContext();

  return (
    <div className="flex items-center gap-1.5 px-2 py-1 rounded-md" title={`Stream: ${connectionStatus}`}>
      <span
        className={cn(
          "inline-block h-2 w-2 rounded-full",
          connectionStatus === "connected" && "bg-success animate-pulse",
          connectionStatus === "connecting" && "bg-warning animate-pulse",
          connectionStatus === "disconnected" && "bg-error"
        )}
      />
      <span
        className={cn(
          "text-[11px] font-medium",
          connectionStatus === "connected" && "text-success",
          connectionStatus === "connecting" && "text-warning",
          connectionStatus === "disconnected" && "text-error"
        )}
      >
        {connectionStatus === "connected" && "Live"}
        {connectionStatus === "connecting" && "Connecting"}
        {connectionStatus === "disconnected" && "Offline"}
      </span>
    </div>
  );
}
