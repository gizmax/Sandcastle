/**
 * Supabase connector - database queries, RPC, and storage via PostgREST + Storage API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_SUPABASE_URL, TOOL_SUPABASE_SERVICE_KEY
 */

const URL = process.env.TOOL_SUPABASE_URL || "";
const KEY = process.env.TOOL_SUPABASE_SERVICE_KEY || "";
const TIMEOUT = 30_000;

function makeSignal() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT);
  return { signal: controller.signal, clear: () => clearTimeout(timer) };
}

async function rest(path, method = "GET", headers = {}, body = null) {
  const { signal, clear } = makeSignal();
  try {
    const opts = {
      method,
      signal,
      headers: {
        "apikey": KEY,
        "Authorization": `Bearer ${KEY}`,
        "Content-Type": "application/json",
        ...headers,
      },
    };
    if (body !== null) opts.body = typeof body === "string" ? body : JSON.stringify(body);
    const resp = await fetch(`${URL}/rest/v1/${path}`, opts);
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Supabase REST ${resp.status}: ${text.slice(0, 500)}`);
    }
    const ct = resp.headers.get("content-type") || "";
    return ct.includes("json") ? resp.json() : resp.text();
  } finally {
    clear();
  }
}

async function storageApi(path, method = "POST", body = null, extraHeaders = {}) {
  const { signal, clear } = makeSignal();
  try {
    const opts = {
      method,
      signal,
      headers: {
        "apikey": KEY,
        "Authorization": `Bearer ${KEY}`,
        ...extraHeaders,
      },
    };
    if (body !== null) opts.body = body;
    const resp = await fetch(`${URL}/storage/v1/${path}`, opts);
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Supabase Storage ${resp.status}: ${text.slice(0, 500)}`);
    }
    return resp.json();
  } finally {
    clear();
  }
}

export async function query(table, options = "{}") {
  const opts = typeof options === "string" ? JSON.parse(options) : options;
  const params = new URLSearchParams();
  if (opts.select) params.set("select", opts.select);
  if (opts.eq) Object.entries(opts.eq).forEach(([k, v]) => params.set(`${k}`, `eq.${v}`));
  if (opts.gt) Object.entries(opts.gt).forEach(([k, v]) => params.set(`${k}`, `gt.${v}`));
  if (opts.lt) Object.entries(opts.lt).forEach(([k, v]) => params.set(`${k}`, `lt.${v}`));
  if (opts.like) Object.entries(opts.like).forEach(([k, v]) => params.set(`${k}`, `like.${v}`));
  if (opts.order) params.set("order", opts.order);
  if (opts.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  return rest(`${table}${qs ? "?" + qs : ""}`, "GET", { "Prefer": "return=representation" });
}

export async function insert(table, records) {
  const rows = typeof records === "string" ? JSON.parse(records) : records;
  return rest(table, "POST", { "Prefer": "return=representation" }, rows);
}

export async function update(table, match, data) {
  const matchObj = typeof match === "string" ? JSON.parse(match) : match;
  const dataObj = typeof data === "string" ? JSON.parse(data) : data;
  const params = new URLSearchParams();
  Object.entries(matchObj).forEach(([k, v]) => params.set(k, `eq.${v}`));
  return rest(`${table}?${params}`, "PATCH", { "Prefer": "return=representation" }, dataObj);
}

export async function delete_rows(table, match) {
  const matchObj = typeof match === "string" ? JSON.parse(match) : match;
  const params = new URLSearchParams();
  Object.entries(matchObj).forEach(([k, v]) => params.set(k, `eq.${v}`));
  return rest(`${table}?${params}`, "DELETE", { "Prefer": "return=representation" });
}

export async function rpc(functionName, params = "{}") {
  const body = typeof params === "string" ? JSON.parse(params) : params;
  return rest(`rpc/${functionName}`, "POST", {}, body);
}

export async function upload_file(bucket, path, base64Content, contentType = "application/octet-stream") {
  const buffer = Buffer.from(base64Content, "base64");
  return storageApi(
    `object/${bucket}/${path}`,
    "POST",
    buffer,
    { "Content-Type": contentType },
  );
}

// CLI dispatch
if (process.argv[1]?.endsWith("supabase.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { query, insert, update, delete_rows, rpc, upload_file };
  if (!dispatch[fn]) {
    console.error(`Usage: node supabase.mjs <${Object.keys(dispatch).join("|")}> [args...]`);
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
