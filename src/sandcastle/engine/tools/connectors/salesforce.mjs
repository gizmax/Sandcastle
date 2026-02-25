/**
 * Salesforce connector - query and manage records via REST API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_SALESFORCE_CLIENT_ID, TOOL_SALESFORCE_CLIENT_SECRET,
 *              TOOL_SALESFORCE_REFRESH_TOKEN, TOOL_SALESFORCE_INSTANCE_URL
 */

const CLIENT_ID = process.env.TOOL_SALESFORCE_CLIENT_ID || "";
const CLIENT_SECRET = process.env.TOOL_SALESFORCE_CLIENT_SECRET || "";
const REFRESH_TOKEN = process.env.TOOL_SALESFORCE_REFRESH_TOKEN || "";
const INSTANCE_URL = (process.env.TOOL_SALESFORCE_INSTANCE_URL || "").replace(/\/$/, "");

let accessToken = "";

async function getAccessToken() {
  if (accessToken) return accessToken;
  const resp = await fetch(`${INSTANCE_URL}/services/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "refresh_token",
      client_id: CLIENT_ID,
      client_secret: CLIENT_SECRET,
      refresh_token: REFRESH_TOKEN,
    }),
  });
  if (!resp.ok) throw new Error(`Salesforce auth failed: ${resp.status}`);
  const data = await resp.json();
  accessToken = data.access_token;
  return accessToken;
}

async function api(path, method = "GET", body = null) {
  const token = await getAccessToken();
  const opts = {
    method,
    headers: {
      "Authorization": `Bearer ${token}`,
      "Content-Type": "application/json",
    },
  };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(`${INSTANCE_URL}/services/data/v59.0${path}`, opts);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Salesforce API ${resp.status}: ${text.slice(0, 500)}`);
  }
  return resp.status === 204 ? {} : resp.json();
}

export async function query(soql) {
  const data = await api(`/query?q=${encodeURIComponent(soql)}`);
  return {
    totalSize: data.totalSize,
    records: data.records.map((r) => {
      const { attributes, ...fields } = r;
      return fields;
    }),
  };
}

export async function create_record(sobject, data) {
  const parsedData = typeof data === "string" ? JSON.parse(data) : data;
  const result = await api(`/sobjects/${sobject}`, "POST", parsedData);
  return { id: result.id, success: result.success };
}

export async function update_record(sobject, record_id, data) {
  const parsedData = typeof data === "string" ? JSON.parse(data) : data;
  await api(`/sobjects/${sobject}/${record_id}`, "PATCH", parsedData);
  return { id: record_id, updated: true };
}

// CLI dispatch
if (process.argv[1]?.endsWith("salesforce.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { query, create_record, update_record };
  if (!dispatch[fn]) {
    console.error(`Usage: node salesforce.mjs <query|create_record|update_record> [args...]`);
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
