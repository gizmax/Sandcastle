/**
 * Airtable connector - CRUD operations on Airtable bases via REST API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_AIRTABLE_API_KEY, TOOL_AIRTABLE_BASE_ID
 */

const API_KEY = process.env.TOOL_AIRTABLE_API_KEY || "";
const BASE_ID = process.env.TOOL_AIRTABLE_BASE_ID || "";
const BASE = `https://api.airtable.com/v0/${BASE_ID}`;
const TIMEOUT = 30_000;

async function api(path, method = "GET", body = null) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT);
  try {
    const opts = {
      method,
      headers: {
        "Authorization": `Bearer ${API_KEY}`,
        "Content-Type": "application/json",
      },
      signal: ctrl.signal,
    };
    if (body) opts.body = JSON.stringify(body);
    const resp = await fetch(`${BASE}${path}`, opts);
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Airtable API ${resp.status}: ${text.slice(0, 500)}`);
    }
    return resp.json();
  } finally { clearTimeout(timer); }
}

export async function list_records(table, options = "{}") {
  const opts = typeof options === "string" ? JSON.parse(options) : options;
  const params = new URLSearchParams();
  if (opts.filterByFormula) params.set("filterByFormula", opts.filterByFormula);
  if (opts.maxRecords) params.set("maxRecords", String(opts.maxRecords));
  if (opts.sort) {
    opts.sort.forEach((s, i) => {
      params.set(`sort[${i}][field]`, s.field);
      if (s.direction) params.set(`sort[${i}][direction]`, s.direction);
    });
  }
  if (opts.view) params.set("view", opts.view);
  const qs = params.toString() ? `?${params.toString()}` : "";
  const data = await api(`/${encodeURIComponent(table)}${qs}`);
  return data.records.map((r) => ({ id: r.id, fields: r.fields, createdTime: r.createdTime }));
}

export async function get_record(table, recordId) {
  if (!recordId || typeof recordId !== "string") throw new Error("recordId is required");
  const data = await api(`/${encodeURIComponent(table)}/${encodeURIComponent(recordId)}`);
  return { id: data.id, fields: data.fields, createdTime: data.createdTime };
}

export async function create_records(table, records) {
  const parsed = typeof records === "string" ? JSON.parse(records) : records;
  const payload = parsed.map((r) => ({ fields: r.fields || r }));
  const data = await api(`/${encodeURIComponent(table)}`, "POST", { records: payload });
  return data.records.map((r) => ({ id: r.id, fields: r.fields }));
}

export async function update_records(table, records) {
  const parsed = typeof records === "string" ? JSON.parse(records) : records;
  const data = await api(`/${encodeURIComponent(table)}`, "PATCH", { records: parsed });
  return data.records.map((r) => ({ id: r.id, fields: r.fields }));
}

export async function delete_records(table, recordIds) {
  const ids = typeof recordIds === "string" ? JSON.parse(recordIds) : recordIds;
  const params = ids.map((id) => `records[]=${id}`).join("&");
  const data = await api(`/${encodeURIComponent(table)}?${params}`, "DELETE");
  return data.records.map((r) => ({ id: r.id, deleted: r.deleted }));
}

// CLI dispatch
if (process.argv[1]?.endsWith("airtable.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { list_records, get_record, create_records, update_records, delete_records };
  if (!dispatch[fn]) {
    console.error("Usage: node airtable.mjs <list_records|get_record|create_records|update_records|delete_records> [args...]");
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
