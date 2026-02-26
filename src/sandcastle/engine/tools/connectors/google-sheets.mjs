/**
 * Google Sheets connector - read/write spreadsheet data via Sheets API v4.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_GOOGLE_SHEETS_SERVICE_ACCOUNT (JSON string),
 *              TOOL_GOOGLE_SHEETS_SPREADSHEET_ID
 */

import crypto from "node:crypto";

const SERVICE_ACCOUNT = process.env.TOOL_GOOGLE_SHEETS_SERVICE_ACCOUNT || "{}";
const SPREADSHEET_ID = process.env.TOOL_GOOGLE_SHEETS_SPREADSHEET_ID || "";
const BASE = "https://sheets.googleapis.com/v4/spreadsheets";
const SCOPES = "https://www.googleapis.com/auth/spreadsheets";
const TIMEOUT = 30_000;

let cachedToken = null;
let tokenExpiry = 0;

function base64url(data) {
  return Buffer.from(data).toString("base64url");
}

async function getAccessToken() {
  if (cachedToken && Date.now() < tokenExpiry) return cachedToken;
  const sa = JSON.parse(SERVICE_ACCOUNT);
  const now = Math.floor(Date.now() / 1000);
  const header = base64url(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  const payload = base64url(JSON.stringify({
    iss: sa.client_email,
    scope: SCOPES,
    aud: "https://oauth2.googleapis.com/token",
    iat: now,
    exp: now + 3600,
  }));
  const signature = crypto.sign("RSA-SHA256", Buffer.from(`${header}.${payload}`),
    { key: sa.private_key, padding: crypto.constants.RSA_PKCS1_PADDING });
  const jwt = `${header}.${payload}.${signature.toString("base64url")}`;

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT);
  try {
    const resp = await fetch("https://oauth2.googleapis.com/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion=${jwt}`,
      signal: ctrl.signal,
    });
    if (!resp.ok) throw new Error(`Token exchange ${resp.status}: ${await resp.text()}`);
    const data = await resp.json();
    cachedToken = data.access_token;
    tokenExpiry = Date.now() + (data.expires_in - 60) * 1000;
    return cachedToken;
  } finally { clearTimeout(timer); }
}

async function api(path, method = "GET", body = null) {
  const token = await getAccessToken();
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT);
  try {
    const opts = {
      method,
      headers: { "Authorization": `Bearer ${token}`, "Content-Type": "application/json" },
      signal: ctrl.signal,
    };
    if (body) opts.body = JSON.stringify(body);
    const resp = await fetch(`${BASE}/${SPREADSHEET_ID}${path}`, opts);
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Sheets API ${resp.status}: ${text.slice(0, 500)}`);
    }
    return resp.json();
  } finally { clearTimeout(timer); }
}

export async function read_range(range) {
  const data = await api(`/values/${encodeURIComponent(range)}`);
  return { range: data.range, values: data.values || [] };
}

export async function write_range(range, values) {
  const parsed = typeof values === "string" ? JSON.parse(values) : values;
  const data = await api(`/values/${encodeURIComponent(range)}?valueInputOption=USER_ENTERED`,
    "PUT", { range, values: parsed, majorDimension: "ROWS" });
  return { updatedRange: data.updatedRange, updatedRows: data.updatedRows, updatedCells: data.updatedCells };
}

export async function append_rows(range, values) {
  const parsed = typeof values === "string" ? JSON.parse(values) : values;
  const data = await api(`/values/${encodeURIComponent(range)}:append?valueInputOption=USER_ENTERED`,
    "POST", { range, values: parsed, majorDimension: "ROWS" });
  return { updatedRange: data.updates?.updatedRange, updatedRows: data.updates?.updatedRows };
}

export async function get_sheets() {
  const data = await api("");
  return data.sheets.map((s) => ({
    sheetId: s.properties.sheetId,
    title: s.properties.title,
    rowCount: s.properties.gridProperties?.rowCount,
    columnCount: s.properties.gridProperties?.columnCount,
  }));
}

// CLI dispatch
if (process.argv[1]?.endsWith("google-sheets.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { read_range, write_range, append_rows, get_sheets };
  if (!dispatch[fn]) {
    console.error("Usage: node google-sheets.mjs <read_range|write_range|append_rows|get_sheets> [args...]");
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
