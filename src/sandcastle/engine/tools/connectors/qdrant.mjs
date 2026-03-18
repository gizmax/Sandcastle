/**
 * Qdrant connector - vector database operations via Qdrant REST API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_QDRANT_URL, TOOL_QDRANT_API_KEY (optional)
 */

const BASE = (process.env.TOOL_QDRANT_URL || "http://localhost:6333").replace(/\/$/, "");
const API_KEY = process.env.TOOL_QDRANT_API_KEY || "";
const TIMEOUT = 30_000;

function makeSignal() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT);
  return { signal: controller.signal, clear: () => clearTimeout(timer) };
}

async function api(path, method = "GET", body = null) {
  const { signal, clear } = makeSignal();
  try {
    const headers = { "Content-Type": "application/json" };
    if (API_KEY) headers["api-key"] = API_KEY;
    const opts = { method, signal, headers };
    if (body) opts.body = JSON.stringify(body);
    const resp = await fetch(`${BASE}${path}`, opts);
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new Error(`Qdrant API ${resp.status}: ${text.slice(0, 500)}`);
    }
    return resp.json();
  } finally {
    clear();
  }
}

export async function search(collection, vector, limit = "10", filter = "{}") {
  const vec = typeof vector === "string" ? JSON.parse(vector) : vector;
  const lim = typeof limit === "string" ? parseInt(limit, 10) : limit;
  const fil = typeof filter === "string" ? JSON.parse(filter) : filter;
  const payload = { vector: vec, limit: lim, with_payload: true };
  if (fil && Object.keys(fil).length > 0) payload.filter = fil;
  const data = await api(`/collections/${encodeURIComponent(collection)}/points/search`, "POST", payload);
  return {
    results: (data.result || []).map((r) => ({
      id: r.id,
      score: r.score,
      payload: r.payload,
      vector: r.vector,
    })),
  };
}

export async function upsert(collection, points) {
  const pts = typeof points === "string" ? JSON.parse(points) : points;
  const data = await api(
    `/collections/${encodeURIComponent(collection)}/points`,
    "PUT",
    { points: pts },
  );
  return { status: data.result?.status || "ok", operationId: data.result?.operation_id };
}

export async function get_collections() {
  const data = await api("/collections");
  return {
    collections: (data.result?.collections || []).map((c) => ({
      name: c.name,
    })),
  };
}

export async function delete_points(collection, filter) {
  const fil = typeof filter === "string" ? JSON.parse(filter) : filter;
  const data = await api(
    `/collections/${encodeURIComponent(collection)}/points/delete`,
    "POST",
    { filter: fil },
  );
  return { status: data.result?.status || "ok", operationId: data.result?.operation_id };
}

// CLI dispatch
if (process.argv[1]?.endsWith("qdrant.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { search, upsert, get_collections, delete_points };
  if (!dispatch[fn]) {
    console.error(`Usage: node qdrant.mjs <${Object.keys(dispatch).join("|")}> [args...]`);
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
