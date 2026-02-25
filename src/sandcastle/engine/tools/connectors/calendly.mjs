/**
 * Calendly API v2 connector.
 * Env: TOOL_CALENDLY_API_KEY
 */

const API_KEY = process.env.TOOL_CALENDLY_API_KEY;
const BASE = "https://api.calendly.com";

async function api(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const resp = await fetch(`${BASE}${path}`, {
    ...options,
    headers: { Authorization: `Bearer ${API_KEY}`, "Content-Type": "application/json", ...options.headers },
    signal: controller.signal,
  });
  clearTimeout(timer);
  if (!resp.ok) {
    const t = await resp.text().catch(() => "");
    throw new Error(`Calendly ${resp.status}: ${t.slice(0, 500)}`);
  }
  return resp.json();
}

export async function get_user() {
  const data = await api("/users/me");
  const u = data.resource;
  return { uri: u.uri, name: u.name, email: u.email, timezone: u.timezone, slug: u.slug };
}

export async function list_event_types() {
  const user = await get_user();
  const data = await api(`/event_types?user=${encodeURIComponent(user.uri)}&count=50`);
  return data.collection.map((e) => ({
    uri: e.uri, name: e.name, slug: e.slug, duration: e.duration, active: e.active, kind: e.kind,
  }));
}

export async function list_events(options = {}) {
  const o = typeof options === "string" ? JSON.parse(options) : options;
  const user = await get_user();
  const params = new URLSearchParams({ user: user.uri, count: "25" });
  if (o.min_start_time) params.set("min_start_time", o.min_start_time);
  if (o.max_start_time) params.set("max_start_time", o.max_start_time);
  if (o.status) params.set("status", o.status);
  const data = await api(`/scheduled_events?${params}`);
  return data.collection.map((e) => ({
    uri: e.uri, name: e.name, status: e.status,
    start_time: e.start_time, end_time: e.end_time,
    event_type: e.event_type, location: e.location,
  }));
}

export async function get_event(eventUuid) {
  const data = await api(`/scheduled_events/${eventUuid}`);
  const e = data.resource;
  const invitees = await api(`/scheduled_events/${eventUuid}/invitees`);
  return {
    uri: e.uri, name: e.name, status: e.status,
    start_time: e.start_time, end_time: e.end_time,
    invitees: invitees.collection.map((i) => ({ email: i.email, name: i.name, status: i.status })),
  };
}

export async function cancel_event(eventUuid, reason = "") {
  await api(`/scheduled_events/${eventUuid}/cancellation`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
  return { cancelled: true, eventUuid };
}

const funcs = { get_user, list_event_types, list_events, get_event, cancel_event };
const [,, fn, ...args] = process.argv;
if (fn) {
  const f = funcs[fn];
  if (!f) { console.error(`Unknown: ${fn}. Available: ${Object.keys(funcs).join(", ")}`); process.exit(1); }
  const parsed = args.map((a) => { try { return JSON.parse(a); } catch { return a; } });
  f(...parsed).then((r) => console.log(JSON.stringify(r, null, 2))).catch((e) => { console.error(e.message); process.exit(1); });
}
