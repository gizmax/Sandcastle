/**
 * Langfuse connector - LLM observability via Langfuse API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_LANGFUSE_SECRET_KEY, TOOL_LANGFUSE_PUBLIC_KEY, TOOL_LANGFUSE_HOST
 */

const SECRET_KEY = process.env.TOOL_LANGFUSE_SECRET_KEY || "";
const PUBLIC_KEY = process.env.TOOL_LANGFUSE_PUBLIC_KEY || "";
const HOST = (process.env.TOOL_LANGFUSE_HOST || "https://cloud.langfuse.com").replace(/\/$/, "");
const TIMEOUT = 30_000;

function makeSignal() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT);
  return { signal: controller.signal, clear: () => clearTimeout(timer) };
}

function authHeader() {
  // Basic auth: public_key:secret_key
  return "Basic " + Buffer.from(`${PUBLIC_KEY}:${SECRET_KEY}`).toString("base64");
}

async function api(path, method = "GET", body = null) {
  const { signal, clear } = makeSignal();
  try {
    const opts = {
      method,
      signal,
      headers: {
        "Content-Type": "application/json",
        "Authorization": authHeader(),
      },
    };
    if (body) opts.body = JSON.stringify(body);
    const resp = await fetch(`${HOST}${path}`, opts);
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new Error(`Langfuse API ${resp.status}: ${text.slice(0, 500)}`);
    }
    const ct = resp.headers.get("content-type") || "";
    if (ct.includes("application/json")) return resp.json();
    return { ok: true };
  } finally {
    clear();
  }
}

export async function create_trace(name, metadata = "{}") {
  const meta = typeof metadata === "string" ? JSON.parse(metadata) : metadata;
  const data = await api("/api/public/traces", "POST", { name, metadata: meta });
  return { id: data.id, name: data.name, metadata: data.metadata };
}

export async function create_generation(trace_id, name, model, input, output, usage = "{}") {
  const usageObj = typeof usage === "string" ? JSON.parse(usage) : usage;
  const inputObj = typeof input === "string" ? JSON.parse(input) : input;
  const outputObj = typeof output === "string" ? JSON.parse(output) : output;
  const data = await api("/api/public/generations", "POST", {
    traceId: trace_id,
    name,
    model,
    input: inputObj,
    output: outputObj,
    usage: usageObj,
  });
  return { id: data.id, traceId: data.traceId, name: data.name };
}

export async function create_score(trace_id, name, value, comment = "") {
  const scoreValue = typeof value === "string" ? parseFloat(value) : value;
  const data = await api("/api/public/scores", "POST", {
    traceId: trace_id,
    name,
    value: scoreValue,
    comment: comment || undefined,
  });
  return { id: data.id, traceId: data.traceId, name: data.name, value: data.value };
}

export async function get_trace(trace_id) {
  const data = await api(`/api/public/traces/${encodeURIComponent(trace_id)}`);
  return {
    id: data.id,
    name: data.name,
    metadata: data.metadata,
    observations: data.observations || [],
    scores: data.scores || [],
    createdAt: data.createdAt,
  };
}

// CLI dispatch
if (process.argv[1]?.endsWith("langfuse.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { create_trace, create_generation, create_score, get_trace };
  if (!dispatch[fn]) {
    console.error(`Usage: node langfuse.mjs <${Object.keys(dispatch).join("|")}> [args...]`);
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
