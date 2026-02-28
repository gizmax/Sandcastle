import { useCallback, useEffect, useMemo, useRef } from "react";
import type { ReactNode } from "react";
import { useEventStream } from "@/hooks/useEventStream";
import { EventStreamContext } from "@/hooks/useEventStreamContext";
import type { StreamEvent } from "@/hooks/useEventStream";

type EventCallback = (event: StreamEvent) => void;

interface EventStreamProviderProps {
  children: ReactNode;
}

export function EventStreamProvider({ children }: EventStreamProviderProps) {
  const { events, status, clearEvents } = useEventStream();
  const subscribersRef = useRef<Map<string, Set<EventCallback>>>(new Map());

  // Dispatch events to subscribers via effect (not during render).
  // Track the last dispatched event ID so we process all new events,
  // not just the latest, avoiding missed events when multiple arrive between renders.
  const lastDispatchedIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (events.length === 0) return;

    // events are newest-first; find index of the last dispatched event
    const lastIdx = lastDispatchedIdRef.current
      ? events.findIndex((e) => e.id === lastDispatchedIdRef.current)
      : -1;

    // All events before that index are new (or all events if last ID not found)
    const newCount = lastIdx === -1 ? events.length : lastIdx;
    if (newCount === 0) return;

    lastDispatchedIdRef.current = events[0].id;

    // Dispatch new events in chronological order (oldest first)
    const newEvents = events.slice(0, newCount).reverse();
    for (const event of newEvents) {
      const subs = subscribersRef.current.get(event.type);
      if (subs) {
        for (const cb of subs) cb(event);
      }
      const wildcardSubs = subscribersRef.current.get("*");
      if (wildcardSubs) {
        for (const cb of wildcardSubs) cb(event);
      }
    }
  }, [events]);

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

  const contextValue = useMemo(
    () => ({
      events,
      connectionStatus: status,
      subscribe,
      clearEvents,
    }),
    [events, status, subscribe, clearEvents]
  );

  return (
    <EventStreamContext.Provider value={contextValue}>
      {children}
    </EventStreamContext.Provider>
  );
}
