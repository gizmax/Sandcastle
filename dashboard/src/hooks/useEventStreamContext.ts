import { createContext, useContext } from "react";
import type { ConnectionStatus, StreamEvent } from "@/hooks/useEventStream";

type EventCallback = (event: StreamEvent) => void;

export interface EventStreamContextValue {
  events: StreamEvent[];
  connectionStatus: ConnectionStatus;
  subscribe: (eventType: string, callback: EventCallback) => () => void;
  clearEvents: () => void;
}

export const EventStreamContext = createContext<EventStreamContextValue | null>(null);

/**
 * Access the event stream context.
 * Must be used within an EventStreamProvider.
 */
export function useEventStreamContext() {
  const ctx = useContext(EventStreamContext);
  if (!ctx) {
    throw new Error("useEventStreamContext must be used within EventStreamProvider");
  }
  return ctx;
}
