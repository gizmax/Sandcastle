/**
 * Intercom connector - manage contacts and conversations via Intercom REST API v2.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_INTERCOM_ACCESS_TOKEN
 */

const ACCESS_TOKEN = process.env.TOOL_INTERCOM_ACCESS_TOKEN || "";
const BASE = "https://api.intercom.io";

async function api(path, method = "GET", body = null) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const opts = {
    method,
    headers: {
      "Authorization": `Bearer ${ACCESS_TOKEN}`,
      "Content-Type": "application/json",
      "Intercom-Version": "2.10",
    },
    signal: controller.signal,
  };
  if (body) opts.body = JSON.stringify(body);
  try {
    const resp = await fetch(`${BASE}${path}`, opts);
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Intercom API ${resp.status}: ${text.slice(0, 500)}`);
    }
    return resp.json();
  } finally {
    clearTimeout(timer);
  }
}

export async function search_contacts(query = "", limit = 20) {
  const data = await api("/contacts/search", "POST", {
    query: {
      operator: "OR",
      value: [
        { field: "email", operator: "~", value: query },
        { field: "name", operator: "~", value: query },
      ],
    },
    pagination: { per_page: limit },
  });
  return data.data.map((c) => ({
    id: c.id,
    name: c.name,
    email: c.email,
    role: c.role,
    created_at: c.created_at,
  }));
}

export async function create_conversation(contact_id, body) {
  const data = await api("/conversations", "POST", {
    from: { type: "contact", id: contact_id },
    body,
  });
  return { id: data.id, created_at: data.created_at };
}

export async function reply_conversation(conversation_id, body, type = "admin") {
  const data = await api(`/conversations/${conversation_id}/reply`, "POST", {
    message_type: "comment",
    type,
    body,
  });
  return { id: data.id, conversation_id };
}

export async function list_conversations(state = "", limit = 20) {
  let path = `/conversations?per_page=${limit}`;
  if (state) path += `&state=${state}`;
  const data = await api(path);
  return data.conversations.map((c) => ({
    id: c.id,
    title: c.title || c.source?.subject,
    state: c.state,
    created_at: c.created_at,
    waiting_since: c.waiting_since,
  }));
}

// CLI dispatch
if (process.argv[1]?.endsWith("intercom.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { search_contacts, create_conversation, reply_conversation, list_conversations };
  if (!dispatch[fn]) {
    console.error(`Usage: node intercom.mjs <search_contacts|create_conversation|reply_conversation|list_conversations> [args...]`);
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
