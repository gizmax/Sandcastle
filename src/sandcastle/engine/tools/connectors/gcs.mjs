/**
 * Google Cloud Storage connector - object storage via GCS JSON API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_GCS_SERVICE_ACCOUNT_JSON, TOOL_GCS_PROJECT_ID
 */

const SERVICE_ACCOUNT_JSON = process.env.TOOL_GCS_SERVICE_ACCOUNT_JSON || "";
const PROJECT_ID = process.env.TOOL_GCS_PROJECT_ID || "";
const TIMEOUT = 60_000;

const GCS_API = "https://storage.googleapis.com";
const GCS_UPLOAD_API = "https://storage.googleapis.com/upload/storage/v1";
const GCS_JSON_API = "https://storage.googleapis.com/storage/v1";

let _tokenCache = null;
let _tokenExpiry = 0;

function makeSignal() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT);
  return { signal: controller.signal, clear: () => clearTimeout(timer) };
}

async function getAccessToken() {
  if (_tokenCache && Date.now() < _tokenExpiry) return _tokenCache;
  if (!SERVICE_ACCOUNT_JSON) throw new Error("TOOL_GCS_SERVICE_ACCOUNT_JSON is not set");

  const sa = JSON.parse(SERVICE_ACCOUNT_JSON);
  const now = Math.floor(Date.now() / 1000);
  const header = { alg: "RS256", typ: "JWT" };
  const claim = {
    iss: sa.client_email,
    scope: "https://www.googleapis.com/auth/devstorage.full_control",
    aud: "https://oauth2.googleapis.com/token",
    exp: now + 3600,
    iat: now,
  };

  // Build unsigned JWT
  const b64 = (obj) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  const unsigned = `${b64(header)}.${b64(claim)}`;

  // Sign using crypto (Node.js built-in)
  const { createSign } = await import("node:crypto");
  const signer = createSign("RSA-SHA256");
  signer.update(unsigned);
  const signature = signer.sign(sa.private_key, "base64url");
  const jwt = `${unsigned}.${signature}`;

  const { signal, clear } = makeSignal();
  try {
    const resp = await fetch("https://oauth2.googleapis.com/token", {
      method: "POST",
      signal,
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
        assertion: jwt,
      }),
    });
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new Error(`GCS token error ${resp.status}: ${text.slice(0, 500)}`);
    }
    const data = await resp.json();
    _tokenCache = data.access_token;
    _tokenExpiry = Date.now() + (data.expires_in - 60) * 1000;
    return _tokenCache;
  } finally {
    clear();
  }
}

async function authHeaders() {
  const token = await getAccessToken();
  return { Authorization: `Bearer ${token}` };
}

export async function upload(bucket, path, content, content_type = "application/octet-stream") {
  const token = await getAccessToken();
  const { signal, clear } = makeSignal();
  try {
    const url = `${GCS_UPLOAD_API}/b/${encodeURIComponent(bucket)}/o?uploadType=media&name=${encodeURIComponent(path)}`;
    const resp = await fetch(url, {
      method: "POST",
      signal,
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": content_type,
      },
      body: typeof content === "string" ? content : JSON.stringify(content),
    });
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new Error(`GCS upload ${resp.status}: ${text.slice(0, 500)}`);
    }
    const data = await resp.json();
    return { name: data.name, bucket: data.bucket, size: data.size, contentType: data.contentType };
  } finally {
    clear();
  }
}

export async function download(bucket, path) {
  const token = await getAccessToken();
  const { signal, clear } = makeSignal();
  try {
    const url = `${GCS_JSON_API}/b/${encodeURIComponent(bucket)}/o/${encodeURIComponent(path)}?alt=media`;
    const resp = await fetch(url, { signal, headers: { Authorization: `Bearer ${token}` } });
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new Error(`GCS download ${resp.status}: ${text.slice(0, 500)}`);
    }
    const ct = resp.headers.get("content-type") || "";
    if (ct.includes("application/json")) return { content: await resp.json() };
    return { content: await resp.text() };
  } finally {
    clear();
  }
}

export async function list_objects(bucket, prefix = "", max_results = "100") {
  const maxRes = typeof max_results === "string" ? parseInt(max_results, 10) : max_results;
  const headers = await authHeaders();
  const { signal, clear } = makeSignal();
  try {
    const params = new URLSearchParams({ maxResults: String(maxRes) });
    if (prefix) params.set("prefix", prefix);
    const url = `${GCS_JSON_API}/b/${encodeURIComponent(bucket)}/o?${params}`;
    const resp = await fetch(url, { signal, headers });
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new Error(`GCS list ${resp.status}: ${text.slice(0, 500)}`);
    }
    const data = await resp.json();
    return {
      objects: (data.items || []).map((o) => ({
        name: o.name,
        size: o.size,
        contentType: o.contentType,
        updated: o.updated,
      })),
      nextPageToken: data.nextPageToken || null,
    };
  } finally {
    clear();
  }
}

export async function delete_object(bucket, path) {
  const headers = await authHeaders();
  const { signal, clear } = makeSignal();
  try {
    const url = `${GCS_JSON_API}/b/${encodeURIComponent(bucket)}/o/${encodeURIComponent(path)}`;
    const resp = await fetch(url, { method: "DELETE", signal, headers });
    if (!resp.ok && resp.status !== 204) {
      const text = await resp.text().catch(() => "");
      throw new Error(`GCS delete ${resp.status}: ${text.slice(0, 500)}`);
    }
    return { deleted: true, bucket, path };
  } finally {
    clear();
  }
}

// CLI dispatch
if (process.argv[1]?.endsWith("gcs.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { upload, download, list_objects, delete_object };
  if (!dispatch[fn]) {
    console.error(`Usage: node gcs.mjs <${Object.keys(dispatch).join("|")}> [args...]`);
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
