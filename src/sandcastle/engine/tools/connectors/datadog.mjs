/**
 * Datadog API connector (v1/v2).
 * Env: TOOL_DATADOG_API_KEY, TOOL_DATADOG_APP_KEY, TOOL_DATADOG_SITE
 */

const API_KEY = process.env.TOOL_DATADOG_API_KEY;
const APP_KEY = process.env.TOOL_DATADOG_APP_KEY;
const SITE = process.env.TOOL_DATADOG_SITE || "datadoghq.com";

async function api(version, path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const resp = await fetch(`https://api.${SITE}/api/${version}${path}`, {
    ...options,
    headers: {
      "DD-API-KEY": API_KEY,
      "DD-APPLICATION-KEY": APP_KEY,
      "Content-Type": "application/json",
      ...options.headers,
    },
    signal: controller.signal,
  });
  clearTimeout(timer);
  if (!resp.ok) {
    const t = await resp.text().catch(() => "");
    throw new Error(`Datadog ${resp.status}: ${t.slice(0, 500)}`);
  }
  return resp.json();
}

export async function query_metrics(query, from, to) {
  const f = typeof from === "number" ? from : Math.floor(new Date(from).getTime() / 1000);
  const t = typeof to === "number" ? to : Math.floor(new Date(to).getTime() / 1000);
  const data = await api("v1", `/query?query=${encodeURIComponent(query)}&from=${f}&to=${t}`);
  return {
    status: data.status,
    series: (data.series || []).map((s) => ({
      metric: s.metric, scope: s.scope, pointlist: s.pointlist?.slice(0, 100),
    })),
  };
}

export async function create_event(title, text, tags = [], alertType = "info") {
  const t = typeof tags === "string" ? JSON.parse(tags) : tags;
  const data = await api("v1", "/events", {
    method: "POST",
    body: JSON.stringify({ title, text, tags: t, alert_type: alertType }),
  });
  return { id: data.event?.id, status: data.status };
}

export async function search_logs(query, from, to, limit = 50) {
  const data = await api("v2", "/logs/events/search", {
    method: "POST",
    body: JSON.stringify({
      filter: { query, from: String(from), to: String(to) },
      sort: "timestamp",
      page: { limit: Number(limit) },
    }),
  });
  return (data.data || []).map((l) => ({
    id: l.id,
    message: l.attributes?.message?.slice(0, 500),
    status: l.attributes?.status,
    service: l.attributes?.service,
    timestamp: l.attributes?.timestamp,
    tags: l.attributes?.tags,
  }));
}

export async function list_monitors(options = {}) {
  const o = typeof options === "string" ? JSON.parse(options) : options;
  const params = new URLSearchParams();
  if (o.tags) params.set("monitor_tags", o.tags);
  if (o.name) params.set("name", o.name);
  const qs = params.toString();
  const data = await api("v1", `/monitor${qs ? "?" + qs : ""}`);
  return (Array.isArray(data) ? data : []).map((m) => ({
    id: m.id, name: m.name, type: m.type, query: m.query,
    overall_state: m.overall_state, message: m.message?.slice(0, 200),
  }));
}

export async function create_incident(title, severity, options = {}) {
  const o = typeof options === "string" ? JSON.parse(options) : options;
  const data = await api("v2", "/incidents", {
    method: "POST",
    body: JSON.stringify({
      data: {
        type: "incidents",
        attributes: {
          title,
          severity,
          customer_impact_scope: o.impact_scope || "",
          fields: { summary: { type: "textbox", value: o.summary || title } },
        },
      },
    }),
  });
  return { id: data.data?.id, title: data.data?.attributes?.title, severity: data.data?.attributes?.severity };
}

const funcs = { query_metrics, create_event, search_logs, list_monitors, create_incident };
const [,, fn, ...args] = process.argv;
if (fn) {
  const f = funcs[fn];
  if (!f) { console.error(`Unknown: ${fn}. Available: ${Object.keys(funcs).join(", ")}`); process.exit(1); }
  const parsed = args.map((a) => { try { return JSON.parse(a); } catch { return a; } });
  f(...parsed).then((r) => console.log(JSON.stringify(r, null, 2))).catch((e) => { console.error(e.message); process.exit(1); });
}
