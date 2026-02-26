/**
 * Anthropic connector - Claude chat completions via Messages API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_ANTHROPIC_API_KEY
 */

const API_KEY = process.env.TOOL_ANTHROPIC_API_KEY || "";
const BASE = "https://api.anthropic.com/v1";
const API_VERSION = "2023-06-01";
const TIMEOUT = 30_000;

async function api(path, body) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT);
  try {
    const resp = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: {
        "x-api-key": API_KEY,
        "anthropic-version": API_VERSION,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Anthropic API ${resp.status}: ${text.slice(0, 500)}`);
    }
    return resp.json();
  } finally { clearTimeout(timer); }
}

export async function chat_completion(messages, model = "claude-sonnet-4-20250514", options = "{}") {
  const parsed = typeof messages === "string" ? JSON.parse(messages) : messages;
  const opts = typeof options === "string" ? JSON.parse(options) : options;
  const payload = {
    model,
    max_tokens: opts.max_tokens || 4096,
    messages: parsed,
  };
  if (opts.system) payload.system = opts.system;
  if (opts.temperature !== undefined) payload.temperature = opts.temperature;
  if (opts.top_p !== undefined) payload.top_p = opts.top_p;
  if (opts.stop_sequences) payload.stop_sequences = opts.stop_sequences;

  const data = await api("/messages", payload);
  const textBlocks = data.content.filter((b) => b.type === "text");
  return {
    id: data.id,
    model: data.model,
    content: textBlocks.map((b) => b.text).join(""),
    stop_reason: data.stop_reason,
    usage: data.usage,
  };
}

export async function create_message(system = "", userMessage = "", options = "{}") {
  const opts = typeof options === "string" ? JSON.parse(options) : options;
  const payload = {
    model: opts.model || "claude-sonnet-4-20250514",
    max_tokens: opts.max_tokens || 4096,
    messages: [{ role: "user", content: userMessage }],
  };
  if (system) payload.system = system;
  if (opts.temperature !== undefined) payload.temperature = opts.temperature;

  const data = await api("/messages", payload);
  const textBlocks = data.content.filter((b) => b.type === "text");
  return {
    id: data.id,
    model: data.model,
    content: textBlocks.map((b) => b.text).join(""),
    stop_reason: data.stop_reason,
    usage: data.usage,
  };
}

// CLI dispatch
if (process.argv[1]?.endsWith("anthropic.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { chat_completion, create_message };
  if (!dispatch[fn]) {
    console.error("Usage: node anthropic.mjs <chat_completion|create_message> [args...]");
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
