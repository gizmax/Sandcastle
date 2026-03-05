import { useCallback, useEffect, useRef, useState } from "react";

export type ActivityEventType =
  | "run_started"
  | "run_completed"
  | "run_failed"
  | "approval_needed"
  | "schedule_fired"
  | "key_created"
  | "workflow_updated";

export type ActivityFilter = "all" | "runs" | "system";

export interface ActivityEvent {
  id: string;
  type: ActivityEventType;
  title: string;
  description: string;
  timestamp: Date;
  read: boolean;
  link?: string;
}

const STORAGE_KEY = "sandcastle-activity-read";
const POLL_INTERVAL = 30_000;

function loadReadIds(): Set<string> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return new Set();
    return new Set(JSON.parse(raw) as string[]);
  } catch {
    return new Set();
  }
}

function saveReadIds(ids: Set<string>): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...ids]));
  } catch {
    // localStorage unavailable
  }
}

function seededRandom(seed: number): () => number {
  let s = seed;
  return () => {
    s = (s * 16807 + 0) % 2147483647;
    return (s - 1) / 2147483646;
  };
}

function pickRandom<T>(arr: T[], rng: () => number): T {
  return arr[Math.floor(rng() * arr.length)];
}

const WORKFLOW_NAMES = [
  "data-pipeline", "image-gen-v2", "code-review", "deploy-staging",
  "nightly-report", "user-onboarding", "etl-sync", "model-finetune",
];

const SCHEDULE_NAMES = [
  "Nightly cleanup", "Hourly sync", "Daily digest", "Weekly report",
];

const ERROR_MESSAGES = [
  "timeout after 30s", "sandbox OOM", "API rate limit", "invalid input schema",
];

function generateInitialEvents(now: Date): ActivityEvent[] {
  const dayStart = new Date(now);
  dayStart.setHours(0, 0, 0, 0);
  const seed = dayStart.getFullYear() * 10000 + (dayStart.getMonth() + 1) * 100 + dayStart.getDate();
  const rng = seededRandom(seed);

  const count = 15 + Math.floor(rng() * 6);
  const events: ActivityEvent[] = [];

  for (let i = 0; i < count; i++) {
    const minutesAgo = Math.floor(rng() * 1440);
    const ts = new Date(now.getTime() - minutesAgo * 60_000);
    const id = `evt-${seed}-${i}`;

    const typeRoll = rng();
    let type: ActivityEventType;
    let title: string;
    let description: string;
    let link: string | undefined;

    if (typeRoll < 0.2) {
      type = "run_started";
      const wf = pickRandom(WORKFLOW_NAMES, rng);
      title = "Workflow started";
      description = `'${wf}' started`;
      link = "/runs";
    } else if (typeRoll < 0.45) {
      type = "run_completed";
      const wf = pickRandom(WORKFLOW_NAMES, rng);
      const dur = (1 + Math.floor(rng() * 120)).toString();
      title = "Run completed";
      description = `'${wf}' completed in ${dur}s`;
      link = `/runs/${id.slice(0, 8)}`;
    } else if (typeRoll < 0.6) {
      type = "run_failed";
      const wf = pickRandom(WORKFLOW_NAMES, rng);
      const err = pickRandom(ERROR_MESSAGES, rng);
      title = "Run failed";
      description = `'${wf}' failed: ${err}`;
      link = `/runs/${id.slice(0, 8)}`;
    } else if (typeRoll < 0.72) {
      type = "approval_needed";
      const wf = pickRandom(WORKFLOW_NAMES, rng);
      title = "Approval needed";
      description = `Approval needed for '${wf}'`;
      link = "/approvals";
    } else if (typeRoll < 0.82) {
      type = "schedule_fired";
      const sched = pickRandom(SCHEDULE_NAMES, rng);
      title = "Schedule triggered";
      description = `'${sched}' triggered`;
      link = "/schedules";
    } else if (typeRoll < 0.9) {
      type = "key_created";
      title = "API key created";
      description = "New API key created";
      link = "/api-keys";
    } else {
      type = "workflow_updated";
      const wf = pickRandom(WORKFLOW_NAMES, rng);
      const ver = Math.floor(rng() * 10) + 1;
      title = "Workflow updated";
      description = `'${wf}' updated to v${ver}`;
      link = "/workflows";
    }

    events.push({ id, type, title, description, timestamp: ts, read: false, link });
  }

  events.sort((a, b) => b.timestamp.getTime() - a.timestamp.getTime());
  return events;
}

function generateNewEvent(now: Date, index: number): ActivityEvent {
  const seed = now.getTime() + index;
  const rng = seededRandom(seed);

  const types: ActivityEventType[] = [
    "run_started", "run_completed", "run_failed", "schedule_fired",
  ];
  const type = pickRandom(types, rng);
  const wf = pickRandom(WORKFLOW_NAMES, rng);
  const id = `evt-live-${now.getTime()}-${index}`;

  let title: string;
  let description: string;
  let link: string | undefined;

  switch (type) {
    case "run_started":
      title = "Workflow started";
      description = `'${wf}' started`;
      link = "/runs";
      break;
    case "run_completed": {
      const dur = (1 + Math.floor(rng() * 60)).toString();
      title = "Run completed";
      description = `'${wf}' completed in ${dur}s`;
      link = `/runs/${id.slice(0, 8)}`;
      break;
    }
    case "run_failed": {
      const err = pickRandom(ERROR_MESSAGES, rng);
      title = "Run failed";
      description = `'${wf}' failed: ${err}`;
      link = `/runs/${id.slice(0, 8)}`;
      break;
    }
    default:
      title = "Schedule triggered";
      description = `'${pickRandom(SCHEDULE_NAMES, rng)}' triggered`;
      link = "/schedules";
  }

  return { id, type, title, description, timestamp: now, read: false, link };
}

export function useActivityFeed() {
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [readIds, setReadIds] = useState<Set<string>>(loadReadIds);
  const pollCounter = useRef(0);
  const [newEventFlag, setNewEventFlag] = useState(0);

  useEffect(() => {
    const now = new Date();
    const initial = generateInitialEvents(now);
    const storedRead = loadReadIds();
    const withReadState = initial.map((e) => ({
      ...e,
      read: storedRead.has(e.id),
    }));
    setEvents(withReadState);
  }, []);

  useEffect(() => {
    const interval = setInterval(() => {
      const roll = Math.random();
      if (roll < 0.4) {
        pollCounter.current += 1;
        const newEvent = generateNewEvent(new Date(), pollCounter.current);
        setEvents((prev) => [newEvent, ...prev].slice(0, 50));
        setNewEventFlag((f) => f + 1);
      }
    }, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, []);

  const markAsRead = useCallback((id: string) => {
    setReadIds((prev) => {
      const next = new Set(prev);
      next.add(id);
      saveReadIds(next);
      return next;
    });
    setEvents((prev) => prev.map((e) => (e.id === id ? { ...e, read: true } : e)));
  }, []);

  const markAllRead = useCallback(() => {
    setEvents((prev) => {
      const allIds = new Set(readIds);
      prev.forEach((e) => allIds.add(e.id));
      saveReadIds(allIds);
      setReadIds(allIds);
      return prev.map((e) => ({ ...e, read: true }));
    });
  }, [readIds]);

  const unreadCount = events.filter((e) => !e.read).length;

  const filterEvents = useCallback(
    (filter: ActivityFilter): ActivityEvent[] => {
      if (filter === "all") return events;
      if (filter === "runs") {
        return events.filter((e) =>
          e.type === "run_started" || e.type === "run_completed" || e.type === "run_failed"
        );
      }
      return events.filter((e) =>
        e.type !== "run_started" && e.type !== "run_completed" && e.type !== "run_failed"
      );
    },
    [events],
  );

  return { events, unreadCount, markAsRead, markAllRead, filterEvents, newEventFlag };
}
