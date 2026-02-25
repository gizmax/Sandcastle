/**
 * Pinecone connector - vector upsert, query, and index management via REST API v1.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_PINECONE_API_KEY, TOOL_PINECONE_INDEX_HOST
 */

const API_KEY = process.env.TOOL_PINECONE_API_KEY || "";
const HOST = (process.env.TOOL_PINECONE_INDEX_HOST || "").replace(/\/+$/, "");
const TIMEOUT = 30_000;

function makeSignal() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT);
  return { signal: controller.signal, clear: () => clearTimeout(timer) };
}

async function api(path, method = "GET", body = null) {
  const { signal, clear } = makeSignal();
  try {
    const opts = {
      method,
      signal,
      headers: {
        "Api-Key": API_KEY,
        "Content-Type": "application/json",
        "X-Pinecone-API-Version": "2024-07",
      },
    };
    if (body !== null) opts.body = typeof body === "string" ? body : JSON.stringify(body);
    const resp = await fetch(`${HOST}${path}`, opts);
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Pinecone API ${resp.status}: ${text.slice(0, 500)}`);
    }
    return resp.json();
  } finally {
    clear();
  }
}

export async function upsert(vectors) {
  const vecs = typeof vectors === "string" ? JSON.parse(vectors) : vectors;
  return api("/vectors/upsert", "POST", { vectors: vecs });
}

export async function query(vector, topK = "10", filter = "{}") {
  const vec = typeof vector === "string" ? JSON.parse(vector) : vector;
  const k = typeof topK === "string" ? parseInt(topK, 10) : topK;
  const f = typeof filter === "string" ? JSON.parse(filter) : filter;
  const body = { vector: vec, topK: k, includeMetadata: true };
  if (Object.keys(f).length > 0) body.filter = f;
  const data = await api("/query", "POST", body);
  return {
    matches: (data.matches || []).map((m) => ({
      id: m.id,
      score: m.score,
      metadata: m.metadata,
    })),
  };
}

export async function delete_vectors(ids) {
  const idList = typeof ids === "string" ? JSON.parse(ids) : ids;
  return api("/vectors/delete", "POST", { ids: idList });
}

export async function describe_index() {
  return api("/describe_index_stats", "POST", {});
}

// CLI dispatch
if (process.argv[1]?.endsWith("pinecone.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { upsert, query, delete_vectors, describe_index };
  if (!dispatch[fn]) {
    console.error(`Usage: node pinecone.mjs <${Object.keys(dispatch).join("|")}> [args...]`);
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
