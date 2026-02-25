/**
 * Cloudflare Workers connector - deploy workers, manage KV via Cloudflare API v4.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_CLOUDFLARE_API_TOKEN, TOOL_CLOUDFLARE_ACCOUNT_ID
 */

const TOKEN = process.env.TOOL_CLOUDFLARE_API_TOKEN || "";
const ACCOUNT = process.env.TOOL_CLOUDFLARE_ACCOUNT_ID || "";
const BASE = "https://api.cloudflare.com/client/v4";
const TIMEOUT = 30_000;

function makeSignal() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT);
  return { signal: controller.signal, clear: () => clearTimeout(timer) };
}

async function api(path, method = "GET", body = null, contentType = "application/json") {
  const { signal, clear } = makeSignal();
  try {
    const headers = {
      "Authorization": `Bearer ${TOKEN}`,
    };
    if (contentType) headers["Content-Type"] = contentType;
    const opts = { method, signal, headers };
    if (body !== null) {
      opts.body = contentType === "application/json"
        ? (typeof body === "string" ? body : JSON.stringify(body))
        : body;
    }
    const resp = await fetch(`${BASE}${path}`, opts);
    const data = await resp.json();
    if (!data.success) {
      const errs = (data.errors || []).map((e) => e.message).join("; ");
      throw new Error(`Cloudflare API ${resp.status}: ${errs || "Unknown error"}`);
    }
    return data.result;
  } finally {
    clear();
  }
}

export async function list_workers() {
  const result = await api(`/accounts/${ACCOUNT}/workers/scripts`);
  return (result || []).map((w) => ({
    id: w.id,
    etag: w.etag,
    created_on: w.created_on,
    modified_on: w.modified_on,
  }));
}

export async function get_worker(scriptName) {
  const { signal, clear } = makeSignal();
  try {
    const resp = await fetch(
      `${BASE}/accounts/${ACCOUNT}/workers/scripts/${scriptName}`,
      {
        signal,
        headers: {
          "Authorization": `Bearer ${TOKEN}`,
          "Accept": "application/javascript",
        },
      },
    );
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Cloudflare API ${resp.status}: ${text.slice(0, 500)}`);
    }
    const script = await resp.text();
    return { name: scriptName, script, size: script.length };
  } finally {
    clear();
  }
}

export async function deploy_worker(scriptName, script, options = "{}") {
  const opts = typeof options === "string" ? JSON.parse(options) : options;
  const metadata = {
    main_module: "worker.mjs",
    compatibility_date: opts.compatibility_date || "2024-01-01",
  };
  if (opts.bindings) metadata.bindings = opts.bindings;

  const form = new FormData();
  form.append("worker.mjs", new Blob([script], { type: "application/javascript+module" }), "worker.mjs");
  form.append("metadata", new Blob([JSON.stringify(metadata)], { type: "application/json" }));

  const { signal, clear } = makeSignal();
  try {
    const resp = await fetch(
      `${BASE}/accounts/${ACCOUNT}/workers/scripts/${scriptName}`,
      {
        method: "PUT",
        signal,
        headers: { "Authorization": `Bearer ${TOKEN}` },
        body: form,
      },
    );
    const data = await resp.json();
    if (!data.success) {
      const errs = (data.errors || []).map((e) => e.message).join("; ");
      throw new Error(`Cloudflare deploy ${resp.status}: ${errs}`);
    }
    return { id: data.result.id, etag: data.result.etag, deployed: true };
  } finally {
    clear();
  }
}

export async function delete_worker(scriptName) {
  await api(`/accounts/${ACCOUNT}/workers/scripts/${scriptName}`, "DELETE");
  return { deleted: true, name: scriptName };
}

export async function list_kv_namespaces() {
  const result = await api(`/accounts/${ACCOUNT}/storage/kv/namespaces`);
  return (result || []).map((ns) => ({
    id: ns.id,
    title: ns.title,
  }));
}

export async function kv_get(namespaceId, key) {
  const { signal, clear } = makeSignal();
  try {
    const resp = await fetch(
      `${BASE}/accounts/${ACCOUNT}/storage/kv/namespaces/${namespaceId}/values/${key}`,
      {
        signal,
        headers: { "Authorization": `Bearer ${TOKEN}` },
      },
    );
    if (!resp.ok) {
      if (resp.status === 404) return { key, value: null };
      const text = await resp.text();
      throw new Error(`Cloudflare KV ${resp.status}: ${text.slice(0, 500)}`);
    }
    const value = await resp.text();
    return { key, value };
  } finally {
    clear();
  }
}

export async function kv_put(namespaceId, key, value) {
  const { signal, clear } = makeSignal();
  try {
    const resp = await fetch(
      `${BASE}/accounts/${ACCOUNT}/storage/kv/namespaces/${namespaceId}/values/${key}`,
      {
        method: "PUT",
        signal,
        headers: {
          "Authorization": `Bearer ${TOKEN}`,
          "Content-Type": "text/plain",
        },
        body: value,
      },
    );
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Cloudflare KV ${resp.status}: ${text.slice(0, 500)}`);
    }
    return { key, written: true };
  } finally {
    clear();
  }
}

// CLI dispatch
if (process.argv[1]?.endsWith("cloudflare-workers.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { list_workers, get_worker, deploy_worker, delete_worker, list_kv_namespaces, kv_get, kv_put };
  if (!dispatch[fn]) {
    console.error(`Usage: node cloudflare-workers.mjs <${Object.keys(dispatch).join("|")}> [args...]`);
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
