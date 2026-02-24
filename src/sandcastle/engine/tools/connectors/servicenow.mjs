/**
 * ServiceNow connector - incidents and change requests.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_SERVICENOW_INSTANCE, TOOL_SERVICENOW_USERNAME, TOOL_SERVICENOW_PASSWORD
 */

const INSTANCE = process.env.TOOL_SERVICENOW_INSTANCE || "";
const USERNAME = process.env.TOOL_SERVICENOW_USERNAME || "";
const PASSWORD = process.env.TOOL_SERVICENOW_PASSWORD || "";
const BASE = `https://${INSTANCE}.service-now.com/api/now`;

async function api(path, method = "GET", body = null) {
  const opts = {
    method,
    headers: {
      "Authorization": `Basic ${Buffer.from(`${USERNAME}:${PASSWORD}`).toString("base64")}`,
      "Content-Type": "application/json",
      "Accept": "application/json",
    },
  };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(`${BASE}${path}`, opts);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`ServiceNow API ${resp.status}: ${text.slice(0, 500)}`);
  }
  return resp.json();
}

export async function get_incidents(query = "", state = "", limit = 20) {
  let path = `/table/incident?sysparm_limit=${limit}&sysparm_display_value=true`;
  const qparts = [];
  if (query) qparts.push(`short_descriptionLIKE${query}`);
  if (state) qparts.push(`state=${state}`);
  if (qparts.length) path += `&sysparm_query=${qparts.join("^")}`;
  const data = await api(path);
  return (data.result || []).map(r => ({
    sys_id: r.sys_id,
    number: r.number,
    short_description: r.short_description,
    state: r.state,
    priority: r.priority,
    assigned_to: r.assigned_to,
    created_on: r.created_on,
  }));
}

export async function create_incident(short_description, description = "", urgency = "2", category = "") {
  const body = { short_description, description, urgency };
  if (category) body.category = category;
  const data = await api("/table/incident", "POST", body);
  const r = data.result;
  return { sys_id: r.sys_id, number: r.number, state: r.state };
}

export async function update_incident(sys_id, state = "", work_notes = "") {
  const body = {};
  if (state) body.state = state;
  if (work_notes) body.work_notes = work_notes;
  const data = await api(`/table/incident/${sys_id}`, "PATCH", body);
  const r = data.result;
  return { sys_id: r.sys_id, number: r.number, state: r.state, updated: true };
}

export async function get_change_requests(state = "", limit = 20) {
  let path = `/table/change_request?sysparm_limit=${limit}&sysparm_display_value=true`;
  if (state) path += `&sysparm_query=state=${state}`;
  const data = await api(path);
  return (data.result || []).map(r => ({
    sys_id: r.sys_id,
    number: r.number,
    short_description: r.short_description,
    state: r.state,
    priority: r.priority,
    start_date: r.start_date,
    end_date: r.end_date,
  }));
}

// CLI dispatch
if (process.argv[1]?.endsWith("servicenow.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { get_incidents, create_incident, update_incident, get_change_requests };
  if (!dispatch[fn]) {
    console.error(`Usage: node servicenow.mjs <get_incidents|create_incident|update_incident|get_change_requests> [args...]`);
    process.exit(1);
  }
  try {
    const result = await dispatch[fn](...args);
    console.log(JSON.stringify(result, null, 2));
  } catch (err) {
    console.error(`Error: ${err.message}`);
    process.exit(1);
  }
}
