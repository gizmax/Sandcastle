/**
 * Discord connector - send messages, webhooks, threads via Discord API v10.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_DISCORD_BOT_TOKEN, TOOL_DISCORD_WEBHOOK_URL
 */

const BOT_TOKEN = process.env.TOOL_DISCORD_BOT_TOKEN || "";
const WEBHOOK_URL = process.env.TOOL_DISCORD_WEBHOOK_URL || "";
const BASE = "https://discord.com/api/v10";
const TIMEOUT = 30_000;

async function api(path, method = "GET", body = null) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT);
  try {
    const opts = {
      method,
      headers: {
        "Authorization": `Bot ${BOT_TOKEN}`,
        "Content-Type": "application/json",
        "User-Agent": "Sandcastle-Tool (https://github.com/gizmax/Sandcastle, 1.0)",
      },
      signal: ctrl.signal,
    };
    if (body) opts.body = JSON.stringify(body);
    const resp = await fetch(`${BASE}${path}`, opts);
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Discord API ${resp.status}: ${text.slice(0, 500)}`);
    }
    return resp.status === 204 ? {} : resp.json();
  } finally { clearTimeout(timer); }
}

export async function send_message(channelId, content, options = "{}") {
  const opts = typeof options === "string" ? JSON.parse(options) : options;
  const payload = { content, ...opts };
  const data = await api(`/channels/${channelId}/messages`, "POST", payload);
  return { id: data.id, channel_id: data.channel_id, content: data.content, timestamp: data.timestamp };
}

export async function send_webhook(content, options = "{}") {
  if (!WEBHOOK_URL) throw new Error("TOOL_DISCORD_WEBHOOK_URL not set");
  const opts = typeof options === "string" ? JSON.parse(options) : options;
  const payload = { content, ...opts };
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT);
  try {
    const url = WEBHOOK_URL.includes("?") ? `${WEBHOOK_URL}&wait=true` : `${WEBHOOK_URL}?wait=true`;
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: ctrl.signal,
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Discord Webhook ${resp.status}: ${text.slice(0, 500)}`);
    }
    const data = await resp.json();
    return { id: data.id, content: data.content, timestamp: data.timestamp };
  } finally { clearTimeout(timer); }
}

export async function get_messages(channelId, limit = 20) {
  const capped = Math.min(Math.max(1, Number(limit)), 100);
  const data = await api(`/channels/${channelId}/messages?limit=${capped}`);
  return data.map((m) => ({
    id: m.id,
    author: m.author?.username,
    content: m.content,
    timestamp: m.timestamp,
    embeds: m.embeds?.length || 0,
  }));
}

export async function create_thread(channelId, name, message = "") {
  const payload = {
    name,
    type: 11, // PUBLIC_THREAD
    auto_archive_duration: 1440,
  };
  if (message) payload.message = { content: message };
  const data = await api(`/channels/${channelId}/threads`, "POST", payload);
  return { id: data.id, name: data.name, type: data.type, parent_id: data.parent_id };
}

// CLI dispatch
if (process.argv[1]?.endsWith("discord.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { send_message, send_webhook, get_messages, create_thread };
  if (!dispatch[fn]) {
    console.error("Usage: node discord.mjs <send_message|send_webhook|get_messages|create_thread> [args...]");
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
