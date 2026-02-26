/**
 * Slack connector - send/read messages via Slack Web API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_SLACK_BOT_TOKEN
 */

const TOKEN = process.env.TOOL_SLACK_BOT_TOKEN || "";
const BASE = "https://slack.com/api";

async function api(method, body = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const resp = await fetch(`${BASE}/${method}`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${TOKEN}`,
      "Content-Type": "application/json; charset=utf-8",
    },
    body: JSON.stringify(body),
    signal: controller.signal,
  });
  clearTimeout(timer);
  const data = await resp.json();
  if (!data.ok) throw new Error(`Slack API error: ${data.error}`);
  return data;
}

export async function send_message(channel, text) {
  const data = await api("chat.postMessage", { channel, text });
  return { ok: true, ts: data.ts, channel: data.channel };
}

export async function list_channels(limit = 100) {
  const data = await api("conversations.list", {
    types: "public_channel,private_channel",
    limit,
  });
  return data.channels.map((c) => ({ id: c.id, name: c.name, topic: c.topic?.value }));
}

export async function get_messages(channel, limit = 20) {
  // Resolve channel name to ID if needed
  let channelId = channel;
  if (channel.startsWith("#")) {
    const channels = await list_channels(200);
    const found = channels.find((c) => c.name === channel.slice(1));
    if (!found) throw new Error(`Channel not found: ${channel}`);
    channelId = found.id;
  }
  const data = await api("conversations.history", { channel: channelId, limit });
  return data.messages.map((m) => ({ user: m.user, text: m.text, ts: m.ts }));
}

// CLI dispatch
if (process.argv[1]?.endsWith("slack.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { send_message, list_channels, get_messages };
  if (!dispatch[fn]) {
    console.error(`Usage: node slack.mjs <send_message|list_channels|get_messages> [args...]`);
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
