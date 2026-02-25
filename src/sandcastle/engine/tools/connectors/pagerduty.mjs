/**
 * PagerDuty API connector (REST API + Events API v2).
 * Env: TOOL_PAGERDUTY_API_KEY
 */

const API_KEY = process.env.TOOL_PAGERDUTY_API_KEY;
const BASE = "https://api.pagerduty.com";

async function api(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const resp = await fetch(`${BASE}${path}`, {
    ...options,
    headers: {
      Authorization: `Token token=${API_KEY}`,
      "Content-Type": "application/json",
      Accept: "application/vnd.pagerduty+json;version=2",
      ...options.headers,
    },
    signal: controller.signal,
  });
  clearTimeout(timer);
  if (!resp.ok) {
    const t = await resp.text().catch(() => "");
    throw new Error(`PagerDuty ${resp.status}: ${t.slice(0, 500)}`);
  }
  return resp.json();
}

export async function create_incident(title, serviceId, urgency = "high", body = "") {
  const data = await api("/incidents", {
    method: "POST",
    body: JSON.stringify({
      incident: {
        type: "incident",
        title,
        service: { id: serviceId, type: "service_reference" },
        urgency,
        body: body ? { type: "incident_body", details: body } : undefined,
      },
    }),
  });
  const i = data.incident;
  return { id: i.id, incident_number: i.incident_number, title: i.title, status: i.status, urgency: i.urgency, html_url: i.html_url };
}

export async function list_incidents(options = {}) {
  const o = typeof options === "string" ? JSON.parse(options) : options;
  const params = new URLSearchParams();
  if (o.statuses) (typeof o.statuses === "string" ? o.statuses.split(",") : o.statuses).forEach((s) => params.append("statuses[]", s.trim()));
  if (o.since) params.set("since", o.since);
  if (o.until) params.set("until", o.until);
  if (o.limit) params.set("limit", o.limit);
  const qs = params.toString();
  const data = await api(`/incidents${qs ? "?" + qs : ""}`);
  return (data.incidents || []).map((i) => ({
    id: i.id, incident_number: i.incident_number, title: i.title,
    status: i.status, urgency: i.urgency, created_at: i.created_at,
    service: i.service?.summary, html_url: i.html_url,
  }));
}

export async function get_incident(incidentId) {
  const data = await api(`/incidents/${incidentId}`);
  const i = data.incident;
  return {
    id: i.id, incident_number: i.incident_number, title: i.title,
    status: i.status, urgency: i.urgency, created_at: i.created_at,
    last_status_change_at: i.last_status_change_at,
    service: i.service?.summary, escalation_policy: i.escalation_policy?.summary,
    assignments: (i.assignments || []).map((a) => a.assignee?.summary),
    html_url: i.html_url,
  };
}

export async function acknowledge_incident(incidentId) {
  const data = await api(`/incidents/${incidentId}`, {
    method: "PUT",
    body: JSON.stringify({ incident: { type: "incident_reference", status: "acknowledged" } }),
  });
  return { id: data.incident.id, status: data.incident.status };
}

export async function resolve_incident(incidentId) {
  const data = await api(`/incidents/${incidentId}`, {
    method: "PUT",
    body: JSON.stringify({ incident: { type: "incident_reference", status: "resolved" } }),
  });
  return { id: data.incident.id, status: data.incident.status };
}

const funcs = { create_incident, list_incidents, get_incident, acknowledge_incident, resolve_incident };
const [,, fn, ...args] = process.argv;
if (fn) {
  const f = funcs[fn];
  if (!f) { console.error(`Unknown: ${fn}. Available: ${Object.keys(funcs).join(", ")}`); process.exit(1); }
  const parsed = args.map((a) => { try { return JSON.parse(a); } catch { return a; } });
  f(...parsed).then((r) => console.log(JSON.stringify(r, null, 2))).catch((e) => { console.error(e.message); process.exit(1); });
}
