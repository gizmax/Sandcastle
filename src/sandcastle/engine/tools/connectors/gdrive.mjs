/**
 * Google Drive connector - list, read, and create files via Google Drive API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_GOOGLE_SERVICE_ACCOUNT (JSON key file content or path)
 */

import { execSync } from "node:child_process";
import { readFileSync, existsSync } from "node:fs";

const SERVICE_ACCOUNT_RAW = process.env.TOOL_GOOGLE_SERVICE_ACCOUNT || "";

let serviceAccount = null;
try {
  // Try parsing as JSON directly
  serviceAccount = JSON.parse(SERVICE_ACCOUNT_RAW);
} catch {
  // Try as file path
  if (SERVICE_ACCOUNT_RAW && existsSync(SERVICE_ACCOUNT_RAW)) {
    serviceAccount = JSON.parse(readFileSync(SERVICE_ACCOUNT_RAW, "utf-8"));
  }
}

async function getAccessToken() {
  if (!serviceAccount) throw new Error("TOOL_GOOGLE_SERVICE_ACCOUNT is not configured");

  // Build JWT for service account authentication
  const header = Buffer.from(JSON.stringify({ alg: "RS256", typ: "JWT" })).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const claim = Buffer.from(JSON.stringify({
    iss: serviceAccount.client_email,
    scope: "https://www.googleapis.com/auth/drive",
    aud: "https://oauth2.googleapis.com/token",
    exp: now + 3600,
    iat: now,
  })).toString("base64url");

  const sigInput = `${header}.${claim}`;

  // Use openssl for RSA signing (available in all sandbox environments)
  const keyFile = "/tmp/_sa_key.pem";
  execSync(`echo '${serviceAccount.private_key.replace(/'/g, "'\\''")}' > ${keyFile}`);
  const sig = execSync(
    `echo -n '${sigInput}' | openssl dgst -sha256 -sign ${keyFile} | openssl base64 -A | tr '+/' '-_' | tr -d '='`,
    { encoding: "utf-8" }
  ).trim();
  execSync(`rm -f ${keyFile}`);

  const jwt = `${sigInput}.${sig}`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const resp = await fetch("https://oauth2.googleapis.com/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion=${jwt}`,
      signal: controller.signal,
    });
    if (!resp.ok) throw new Error(`Google auth failed: ${resp.status}`);
    const data = await resp.json();
    return data.access_token;
  } finally {
    clearTimeout(timer);
  }
}

async function api(path, method = "GET", body = null) {
  const token = await getAccessToken();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const opts = {
    method,
    headers: {
      "Authorization": `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    signal: controller.signal,
  };
  if (body) opts.body = JSON.stringify(body);
  try {
    const resp = await fetch(`https://www.googleapis.com/drive/v3${path}`, opts);
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Google Drive API ${resp.status}: ${text.slice(0, 500)}`);
    }
    return resp.json();
  } finally {
    clearTimeout(timer);
  }
}

export async function list_files(query = "", limit = 20) {
  let q = query || "trashed = false";
  const data = await api(`/files?q=${encodeURIComponent(q)}&pageSize=${limit}&fields=files(id,name,mimeType,modifiedTime,size)`);
  return data.files;
}

export async function read_file(file_id) {
  const token = await getAccessToken();
  // Get file metadata first
  const meta = await api(`/files/${file_id}?fields=id,name,mimeType`);

  // For Google Docs, export as plain text
  if (meta.mimeType?.startsWith("application/vnd.google-apps.")) {
    const exportMime = "text/plain";
    const exportController = new AbortController();
    const exportTimer = setTimeout(() => exportController.abort(), 30000);
    try {
      const resp = await fetch(
        `https://www.googleapis.com/drive/v3/files/${file_id}/export?mimeType=${encodeURIComponent(exportMime)}`,
        { headers: { "Authorization": `Bearer ${token}` }, signal: exportController.signal }
      );
      return { id: file_id, name: meta.name, content: await resp.text() };
    } finally {
      clearTimeout(exportTimer);
    }
  }

  // For regular files, download content
  const dlController = new AbortController();
  const dlTimer = setTimeout(() => dlController.abort(), 30000);
  try {
    const resp = await fetch(
      `https://www.googleapis.com/drive/v3/files/${file_id}?alt=media`,
      { headers: { "Authorization": `Bearer ${token}` }, signal: dlController.signal }
    );
    return { id: file_id, name: meta.name, content: (await resp.text()).slice(0, 100000) };
  } finally {
    clearTimeout(dlTimer);
  }
}

export async function create_file(name, content, mime_type = "text/plain", parent_id = "") {
  const token = await getAccessToken();
  const metadata = { name, mimeType: mime_type };
  if (parent_id) metadata.parents = [parent_id];

  // Multipart upload
  const boundary = "sandcastle_boundary";
  const body = [
    `--${boundary}`,
    "Content-Type: application/json; charset=UTF-8",
    "",
    JSON.stringify(metadata),
    `--${boundary}`,
    `Content-Type: ${mime_type}`,
    "",
    content,
    `--${boundary}--`,
  ].join("\r\n");

  const uploadController = new AbortController();
  const uploadTimer = setTimeout(() => uploadController.abort(), 30000);
  try {
    const resp = await fetch(
      "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
      {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${token}`,
          "Content-Type": `multipart/related; boundary=${boundary}`,
        },
        body,
        signal: uploadController.signal,
      }
    );
    if (!resp.ok) throw new Error(`Upload failed: ${resp.status}`);
    const data = await resp.json();
    return { id: data.id, name: data.name };
  } finally {
    clearTimeout(uploadTimer);
  }
}

// CLI dispatch
if (process.argv[1]?.endsWith("gdrive.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { list_files, read_file, create_file };
  if (!dispatch[fn]) {
    console.error(`Usage: node gdrive.mjs <list_files|read_file|create_file> [args...]`);
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
