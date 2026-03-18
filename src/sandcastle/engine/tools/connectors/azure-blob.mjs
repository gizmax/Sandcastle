/**
 * Azure Blob Storage connector - object storage via Azure REST API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_AZURE_STORAGE_CONNECTION_STRING
 *           or TOOL_AZURE_STORAGE_ACCOUNT + TOOL_AZURE_STORAGE_KEY
 */

const CONNECTION_STRING = process.env.TOOL_AZURE_STORAGE_CONNECTION_STRING || "";
const ACCOUNT_NAME = process.env.TOOL_AZURE_STORAGE_ACCOUNT || "";
const ACCOUNT_KEY = process.env.TOOL_AZURE_STORAGE_KEY || "";
const TIMEOUT = 60_000;
const API_VERSION = "2023-01-03";

function makeSignal() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT);
  return { signal: controller.signal, clear: () => clearTimeout(timer) };
}

function parseConnectionString(cs) {
  const parts = {};
  for (const part of cs.split(";")) {
    const idx = part.indexOf("=");
    if (idx > 0) {
      parts[part.slice(0, idx)] = part.slice(idx + 1);
    }
  }
  return {
    account: parts.AccountName || "",
    key: parts.AccountKey || "",
    endpoint: parts.BlobEndpoint || `https://${parts.AccountName}.blob.core.windows.net`,
  };
}

function getConfig() {
  if (CONNECTION_STRING) return parseConnectionString(CONNECTION_STRING);
  if (ACCOUNT_NAME && ACCOUNT_KEY) {
    return {
      account: ACCOUNT_NAME,
      key: ACCOUNT_KEY,
      endpoint: `https://${ACCOUNT_NAME}.blob.core.windows.net`,
    };
  }
  throw new Error(
    "Azure Blob: set TOOL_AZURE_STORAGE_CONNECTION_STRING or " +
    "TOOL_AZURE_STORAGE_ACCOUNT + TOOL_AZURE_STORAGE_KEY",
  );
}

async function sign(account, key, stringToSign) {
  const { createHmac } = await import("node:crypto");
  const keyBuf = Buffer.from(key, "base64");
  return createHmac("sha256", keyBuf).update(stringToSign, "utf8").digest("base64");
}

async function sharedKeyHeader(account, key, method, url, headers, contentType = "") {
  const u = new URL(url);
  const dateStr = new Date().toUTCString();
  const canonicalHeaders = `x-ms-date:${dateStr}\nx-ms-version:${API_VERSION}`;
  const pathStr = `/${account}${u.pathname}`;
  const query = u.searchParams;
  const sortedQuery = [...query.entries()].sort(([a], [b]) => a.localeCompare(b));
  const canonicalQuery = sortedQuery.map(([k, v]) => `${k}:${v}`).join("\n");
  const stringToSign = [
    method,
    "", // Content-Encoding
    "", // Content-Language
    "", // Content-Length
    "", // Content-MD5
    contentType,
    "", // Date
    "", // If-Modified-Since
    "", // If-Match
    "", // If-None-Match
    "", // If-Unmodified-Since
    "", // Range
    canonicalHeaders,
    pathStr + (canonicalQuery ? "\n" + canonicalQuery : ""),
  ].join("\n");
  const sig = await sign(account, key, stringToSign);
  return {
    "x-ms-date": dateStr,
    "x-ms-version": API_VERSION,
    Authorization: `SharedKey ${account}:${sig}`,
    ...(contentType ? { "Content-Type": contentType } : {}),
  };
}

export async function upload(container, blob_name, content, content_type = "application/octet-stream") {
  const cfg = getConfig();
  const url = `${cfg.endpoint}/${encodeURIComponent(container)}/${encodeURIComponent(blob_name)}`;
  const body = typeof content === "string" ? content : JSON.stringify(content);
  const headers = await sharedKeyHeader(cfg.account, cfg.key, "PUT", url, {}, content_type);
  headers["x-ms-blob-type"] = "BlockBlob";
  headers["Content-Length"] = String(Buffer.byteLength(body));

  const { signal, clear } = makeSignal();
  try {
    const resp = await fetch(url, { method: "PUT", signal, headers, body });
    if (!resp.ok && resp.status !== 201) {
      const text = await resp.text().catch(() => "");
      throw new Error(`Azure Blob upload ${resp.status}: ${text.slice(0, 500)}`);
    }
    return { uploaded: true, container, blob: blob_name, contentType: content_type };
  } finally {
    clear();
  }
}

export async function download(container, blob_name) {
  const cfg = getConfig();
  const url = `${cfg.endpoint}/${encodeURIComponent(container)}/${encodeURIComponent(blob_name)}`;
  const headers = await sharedKeyHeader(cfg.account, cfg.key, "GET", url, {});

  const { signal, clear } = makeSignal();
  try {
    const resp = await fetch(url, { signal, headers });
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new Error(`Azure Blob download ${resp.status}: ${text.slice(0, 500)}`);
    }
    const ct = resp.headers.get("content-type") || "";
    if (ct.includes("application/json")) return { content: await resp.json() };
    return { content: await resp.text() };
  } finally {
    clear();
  }
}

export async function list_blobs(container, prefix = "", max_results = "100") {
  const cfg = getConfig();
  const maxRes = typeof max_results === "string" ? parseInt(max_results, 10) : max_results;
  const params = new URLSearchParams({
    restype: "container",
    comp: "list",
    maxresults: String(maxRes),
  });
  if (prefix) params.set("prefix", prefix);
  const url = `${cfg.endpoint}/${encodeURIComponent(container)}?${params}`;
  const headers = await sharedKeyHeader(cfg.account, cfg.key, "GET", url, {});

  const { signal, clear } = makeSignal();
  try {
    const resp = await fetch(url, { signal, headers });
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new Error(`Azure Blob list ${resp.status}: ${text.slice(0, 500)}`);
    }
    const xml = await resp.text();
    // Simple XML parsing without external dependencies
    const blobs = [];
    const blobRegex = /<Blob>[\s\S]*?<\/Blob>/g;
    for (const match of xml.matchAll(blobRegex)) {
      const nameMatch = match[0].match(/<Name>([^<]+)<\/Name>/);
      const sizeMatch = match[0].match(/<Content-Length>([^<]+)<\/Content-Length>/);
      const ctMatch = match[0].match(/<Content-Type>([^<]+)<\/Content-Type>/);
      const lastModMatch = match[0].match(/<Last-Modified>([^<]+)<\/Last-Modified>/);
      blobs.push({
        name: nameMatch?.[1] || "",
        size: sizeMatch?.[1] || "0",
        contentType: ctMatch?.[1] || "",
        lastModified: lastModMatch?.[1] || "",
      });
    }
    const nextMarkerMatch = xml.match(/<NextMarker>([^<]*)<\/NextMarker>/);
    return { blobs, nextMarker: nextMarkerMatch?.[1] || null };
  } finally {
    clear();
  }
}

export async function delete_blob(container, blob_name) {
  const cfg = getConfig();
  const url = `${cfg.endpoint}/${encodeURIComponent(container)}/${encodeURIComponent(blob_name)}`;
  const headers = await sharedKeyHeader(cfg.account, cfg.key, "DELETE", url, {});

  const { signal, clear } = makeSignal();
  try {
    const resp = await fetch(url, { method: "DELETE", signal, headers });
    if (!resp.ok && resp.status !== 202) {
      const text = await resp.text().catch(() => "");
      throw new Error(`Azure Blob delete ${resp.status}: ${text.slice(0, 500)}`);
    }
    return { deleted: true, container, blob: blob_name };
  } finally {
    clear();
  }
}

// CLI dispatch
if (process.argv[1]?.endsWith("azure-blob.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { upload, download, list_blobs, delete_blob };
  if (!dispatch[fn]) {
    console.error(`Usage: node azure-blob.mjs <${Object.keys(dispatch).join("|")}> [args...]`);
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
