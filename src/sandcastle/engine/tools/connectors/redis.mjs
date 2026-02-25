/**
 * Redis connector - key/value ops and pub/sub.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Upstash (HTTP): TOOL_REDIS_URL + TOOL_REDIS_TOKEN
 * Local Redis: falls back to redis-cli subprocess.
 *
 * Credentials: TOOL_REDIS_URL, TOOL_REDIS_TOKEN
 */

import { execSync } from "node:child_process";

const REDIS_URL = process.env.TOOL_REDIS_URL || "";
const REDIS_TOKEN = process.env.TOOL_REDIS_TOKEN || "";
const TIMEOUT = 30_000;

const isUpstash = REDIS_URL.includes("upstash") || REDIS_TOKEN;

async function upstashCmd(...args) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT);
  try {
    const resp = await fetch(`${REDIS_URL}`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${REDIS_TOKEN}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(args),
      signal: ctrl.signal,
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Upstash Redis ${resp.status}: ${text.slice(0, 500)}`);
    }
    const data = await resp.json();
    if (data.error) throw new Error(`Redis error: ${data.error}`);
    return data.result;
  } finally { clearTimeout(timer); }
}

function localCmd(...args) {
  const cliUrl = REDIS_URL || "redis://localhost:6379";
  const escaped = args.map((a) => `'${String(a).replace(/'/g, "'\\''")}'`).join(" ");
  try {
    const result = execSync(`redis-cli -u "${cliUrl}" ${escaped}`, {
      timeout: TIMEOUT,
      encoding: "utf-8",
    });
    return result.trim();
  } catch (err) {
    throw new Error(`redis-cli error: ${err.message.slice(0, 500)}`);
  }
}

async function cmd(...args) {
  return isUpstash ? upstashCmd(...args) : localCmd(...args);
}

export async function get(key) {
  const value = await cmd("GET", key);
  // Try to parse JSON, otherwise return as string
  if (value === null || value === "(nil)") return { key, value: null };
  try {
    return { key, value: JSON.parse(value) };
  } catch {
    return { key, value };
  }
}

export async function set(key, value, ttl = "") {
  const args = ["SET", key, typeof value === "object" ? JSON.stringify(value) : String(value)];
  if (ttl) args.push("EX", String(ttl));
  const result = await cmd(...args);
  return { ok: result === "OK" || result === true, key };
}

// Named delete_ to avoid reserved word issues, exported as 'delete'
async function delete_(key) {
  const result = await cmd("DEL", key);
  return { ok: true, key, deleted: Number(result) || (result === true ? 1 : 0) };
}
export { delete_ as delete };

export async function list_keys(pattern = "*") {
  const result = await cmd("KEYS", pattern);
  const keys = Array.isArray(result) ? result : String(result).split("\n").filter(Boolean);
  return { pattern, count: keys.length, keys };
}

export async function publish(channel, message) {
  const payload = typeof message === "object" ? JSON.stringify(message) : String(message);
  const result = await cmd("PUBLISH", channel, payload);
  return { channel, receivers: Number(result) || 0 };
}

// CLI dispatch
if (process.argv[1]?.endsWith("redis.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { get, set, delete: delete_, list_keys, publish };
  if (!dispatch[fn]) {
    console.error("Usage: node redis.mjs <get|set|delete|list_keys|publish> [args...]");
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
