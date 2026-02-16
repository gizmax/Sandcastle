import { createContext, useCallback, useContext, useRef } from "react";
import type { ReactNode } from "react";
import { useEventStream } from "@/hooks/useEventStream";
import type { ConnectionStatus, StreamEvent } from "@/hooks/useEventStream";

type EventCallback = (event: StreamEvent) => void;

interface EventStreamContextValue {
  /** Latest events (most recent first, max 50) */
  events: StreamEvent[];
  /** Connection status */
  connectionStatus: ConnectionStatus;
  /** Subscribe to a specific event type. Returns unsubscribe function. */
  subscribe: (eventType: string, callback: EventCallback) => () => void;
  /** Clear stored events */
  clearEvents: () => void;
}

const EventStreamContext = createContext<EventStreamContextValue | null>(null);

interface EventStreamProviderProps {
  children: ReactNode;
}

export function EventStreamProvider({ children }: EventStreamProviderProps) {
  const { events, status, clearEvents } = useEventStream();
  const subscribersRef = useRef<Map<string, Set<EventCallback>>>(new Map());

  // Dispatch events to subscribers whenever new events arrive
  const lastDispatchedRef = useRef<string | null>(null);
  const latestEvent = events[0] ?? null;

  if (latestEvent && latestEvent.id !== lastDispatchedRef.current) {
    lastDispatchedRef.current = latestEvent.id;
    const subs = subscribersRef.current.get(latestEvent.type);
    if (subs) {
      for (const cb of subs) {
        cb(latestEvent);
      }
    }
    // Also dispatch to wildcard subscribers
    const wildcardSubs = subscribersRef.current.get("*");
    if (wildcardSubs) {
      for (const cb of wildcardSubs) {
        cb(latestEvent);
      }
    }
  }

  const subscribe = useCallback((eventType: string, callback: EventCallback) => {
    if (!subscribersRef.current.has(eventType)) {
      subscribersRef.current.set(eventType, new Set());
    }
    subscribersRef.current.get(eventType)!.add(callback);

    return () => {
      const subs = subscribersRef.current.get(eventType);
      if (subs) {
        subs.delete(callback);
        if (subs.size === 0) {
          subscribersRef.current.delete(eventType);
        }
      }
    };
  }, []);

  return (
    <EventStreamContext.Provider
      value={{
        events,
        connectionStatus: status,
        subscribe,
        clearEvents,
      }}
    >
      {children}
    </EventStreamContext.Provider>
  );
}

/**
 * Access the event stream context.
 * Must be used within an EventStreamProvider.
 */
export function useEventStreamContext(): EventStreamContextValue {
  const ctx = useContext(EventStreamContext);
  if (!ctx) {
    throw new Error("useEventStreamContext must be used within EventStreamProvider");
  }
  return ctx;
}
